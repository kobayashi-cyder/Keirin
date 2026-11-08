"""Offline, zero-API-cost tests of GPT-4o local-frame keirin vision pipeline."""
import csv
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import gpt4o_race_vision as m
from build_multiview_features import validate_history
from video_history import register

RACE = "7520261020010001"
REG_NOW = datetime.fromisoformat("2026-10-21T12:00:00+09:00")
ANALYZE_NOW = datetime.fromisoformat("2026-10-22T09:00:00+09:00")
COMMIT_NOW = datetime.fromisoformat("2026-10-22T10:00:00+09:00")
VIDEO = Path("local_data/videos/official.mp4")
JPG = b"\xff\xd8" + b"0" * 1400 + b"\xff\xd9"


def result_rows(cars=7):
    return [{
        "race_id": RACE, "race_at_jst": "2026-10-20T20:45:00+09:00",
        "venue": "matsuyama", "grade": "F2", "car_no": str(car),
        "rider_id": f"R{car}", "finish_position": str(car),
        "source_url": "https://example.org/license-approved-official-results",
        "rights_status": "permitted",
    } for car in range(1, cars+1)]


def sample_prediction(timestamps, cars=7, *, complete=True):
    return {
        "race_identity_visible": True,
        "full_race_visibly_covered": complete,
        "race_note": "synthetic test fixture only; no race was actually analyzed",
        "riders": [{
            "car_no": car, "attack_count": 1 if car == 1 else 0,
            "front_seconds": 20.0 if car == 1 else 0.0,
            "late_gain_places": 0, "line_cohesion": 0.8,
            "confidence": 0.96,
            "evidence_seconds": [timestamps[0], timestamps[-1]],
            "limitations": "synthetic test fixture",
        } for car in range(1, cars+1)]
    }


def fake_output(payload):
    return {"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(payload)}]}
    ]}


class FakeHTTP:
    status = 200
    def __init__(self, result):
        self.raw = json.dumps(result).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def read(self, n):
        return self.raw[:n]


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.temp.name)
        VIDEO.parent.mkdir(parents=True, exist_ok=True)
        VIDEO.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 4096)
        register(VIDEO, RACE, "https://example.org/my-licensed-keirin-video",
                 "permitted", now=REG_NOW)

    def tearDown(self):
        os.chdir(self.cwd)
        self.temp.cleanup()

    def test_manifest_and_local_only_video_not_a_youtube_url(self):
        entry = m.registration(VIDEO, RACE)
        self.assertEqual(entry["rights_status"], "permitted")
        self.assertEqual(len(entry["sha256"]), 64)
        for val in ("https://youtube.com/watch?v=BhwmwNby6Ow",
                    "../download.mp4", "/tmp/file.mp4",
                    "local_data/history/official.mp4"):
            with self.assertRaises(ValueError):
                m._video_file(val)
        with self.assertRaisesRegex(ValueError, "exactly one matching"):
            m.registration(VIDEO, "7520261020010002")
        VIDEO.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"X" * 4096)
        with self.assertRaisesRegex(ValueError, "matching"):
            m.registration(VIDEO, RACE)

    def test_sample_window_starts_and_ends_at_video_bounds(self):
        times = m.sample_times(120, 12)
        self.assertEqual((len(times), times[0], times[-1]), (12, 0.5, 119.0))
        self.assertEqual(times, sorted(times))
        for val in (1, 3, 25, 60):
            with self.assertRaises(ValueError):
                m.sample_times(120, val)
        with self.assertRaises(ValueError):
            m.sample_times(901, 12)

    def test_frame_extraction_uses_local_ffmpeg_no_file_or_shell(self):
        class Result:
            returncode = 0
            stdout = JPG
        calls = []
        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return Result()
        self.assertEqual(m.frame_at(VIDEO, 8.0, runner=runner), JPG)
        self.assertEqual(calls[0][0][0], "ffmpeg")
        self.assertEqual(calls[0][0][-1], "-")
        self.assertNotIn("https://", " ".join(calls[0][0]))
        self.assertEqual(calls[0][0].count("image2pipe"), 1)
        self.assertEqual(calls[0][1]["check"], False)
        self.assertNotIn("shell", calls[0][1])
        with self.assertRaises(ValueError):
            m.frame_at(VIDEO, 2, runner=lambda *a, **k: ResultMalformed())

    def test_responses_payload_is_gpt4o_structured_and_store_false(self):
        body = m.build_api_request([(0.5, JPG), (8.5, JPG)])
        self.assertEqual(body["model"], "gpt-4o")
        self.assertFalse(body["store"])
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual(body["input"][0]["role"], "developer")
        images = [c for c in body["input"][1]["content"] if c["type"]=="input_image"]
        self.assertEqual(len(images), 2)
        self.assertTrue(images[0]["image_url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(images[0]["detail"], "high")
        self.assertEqual(len([c for c in body["input"][1]["content"]
                              if c["type"] == "input_text"]), 3)
        self.assertNotIn("youtube.com/watch", json.dumps(body))

    def test_api_response_and_http_error_never_leak_credentials(self):
        payload = sample_prediction([0.5,8.5])
        calls = []
        def opener(req, timeout):
            calls.append((req, timeout))
            return FakeHTTP(fake_output(payload))
        result = m.openai_responses({"model":"gpt-4o"}, "sk-secret-not-for-logs",
                                    opener=opener)
        self.assertEqual(result, payload)
        self.assertEqual(calls[0][0].get_method(), "POST")
        self.assertEqual(urlsplit(calls[0][0].full_url).hostname, "api.openai.com")
        self.assertEqual(calls[0][1], 120)
        def error(req, timeout):
            raise HTTPError(req.full_url + "?token=secret", 403, "forbidden", {}, None)
        with self.assertRaisesRegex(ValueError, "HTTP 403") as ctx:
            m.openai_responses({}, "sk-secret-not-for-logs", opener=error)
        self.assertNotIn("secret", str(ctx.exception))
        with self.assertRaisesRegex(ValueError, "complete structured output"):
            m.openai_responses({}, "key",
                opener=lambda *a,**kw: FakeHTTP({"status":"incomplete","output":[]}))

    def test_model_does_not_get_credit_for_unsampled_fake_timestamps(self):
        model = sample_prediction([0.5, 8.5])
        model["riders"][0]["evidence_seconds"] = [1.5]
        with self.assertRaisesRegex(ValueError, "timestamp not included"):
            m.validate_prediction(model, [0.5,8.5])
        model = sample_prediction([0.5, 8.5])
        model["riders"][0]["front_seconds"] = float("nan")
        with self.assertRaises(ValueError):
            m.validate_prediction(model, [0.5,8.5])
        model = sample_prediction([0.5, 8.5])
        model["riders"][1]["car_no"] = 1
        with self.assertRaisesRegex(ValueError, "duplicate"):
            m.validate_prediction(model, [0.5,8.5])

    def run_fake(self, *, complete=True):
        def api(body):
            captions = body["input"][1]["content"]
            stamps = [float(c["text"].split("=")[1][:-1])
                      for c in captions
                      if c["type"] == "input_text" and c["text"].startswith("LOCAL_FRAME_T=")]
            return sample_prediction(stamps, complete=complete)
        return m.analyze(
            VIDEO, RACE, n=8, api=api,
            probe=lambda path: 100.0, decode=lambda path, t: JPG,
            now=ANALYZE_NOW,
        )

    def test_full_mocked_pipeline_produces_provisional_not_verified(self):
        report = self.run_fake()
        self.assertEqual(report["frame_count"], 8)
        self.assertEqual(report["model"], "gpt-4o")
        self.assertFalse(report["source_video_uploaded"])
        self.assertTrue(report["ai_suggested_training_candidate"])
        self.assertFalse(report["training_allowed"])
        self.assertFalse(report["model_verified"])
        self.assertEqual(report["status"], "AI_CANDIDATE_REQUIRES_VERIFICATION")
        result_path = Path("local_data/history/verified_race_results.csv")
        result_path.parent.mkdir(parents=True, exist_ok=True)
        with result_path.open("w", encoding="utf-8", newline="") as f:
            w=csv.DictWriter(f, fieldnames=m.OFFICIAL_FIELDS);w.writeheader()
            w.writerows(result_rows())
        official = m.official_results(result_path, RACE, 7)
        rows = m.convert_report(report, official, now=COMMIT_NOW)
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["rider_id"], "R1")
        self.assertEqual(rows[0]["finish_position"], "1")
        self.assertEqual(rows[0]["front_seconds"], "20.0")
        self.assertEqual(rows[0]["available_at_jst"], COMMIT_NOW.isoformat())
        self.assertEqual(rows[0]["video_sha256"], report["video_sha256"])
        self.assertFalse(rows[0]["youtube_video_id"])
        validate_history(rows[0])
        target = Path("local_data/history/gpt4o_rider_events.csv")
        self.assertEqual(m.append_events(target, rows), 7)
        self.assertEqual(len(target.read_text(encoding="utf-8").splitlines()), 8)
        with self.assertRaisesRegex(ValueError, "already saved"):
            m.append_events(target, rows)

    def test_incomplete_frames_or_unknown_metrics_cannot_be_committed(self):
        report = self.run_fake(complete=False)
        self.assertFalse(report["ai_suggested_training_candidate"])
        with self.assertRaisesRegex(ValueError, "complete"):
            m.convert_report(report, result_rows(), now=COMMIT_NOW)
        report = self.run_fake()
        report["prediction"]["riders"][0]["front_seconds"] = None
        with self.assertRaisesRegex(ValueError, "every necessary"):
            m.convert_report(report, result_rows(), now=COMMIT_NOW)
        report = self.run_fake()
        report["prediction"]["riders"][0]["confidence"] = .75
        with self.assertRaisesRegex(ValueError, "every necessary"):
            m.convert_report(report, result_rows(), now=COMMIT_NOW)
        report = self.run_fake()
        with self.assertRaisesRegex(ValueError, "future"):
            m.convert_report(report, result_rows(),
                             now=datetime.fromisoformat("2026-10-21T23:00:00+09:00"))

    def test_official_results_reject_ties_mismatched_cars(self):
        target = Path("local_data/history/official.csv")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as f:
            w=csv.DictWriter(f, fieldnames=m.OFFICIAL_FIELDS);w.writeheader()
            rows=result_rows();rows[1]["finish_position"]="1"
            w.writerows(rows)
        with self.assertRaisesRegex(ValueError, "nonunique finishing"):
            m.official_results(target, RACE, 7)
        with target.open("w", encoding="utf-8", newline="") as f:
            w=csv.DictWriter(f, fieldnames=m.OFFICIAL_FIELDS);w.writeheader()
            w.writerows(result_rows()[:6])
        with self.assertRaisesRegex(ValueError, "missing or not aligned"):
            m.official_results(target, RACE, 7)

    def test_default_cli_never_calls_openai_and_requires_explicit_upload_flags(self):
        argv = ["x.py", "--video", str(VIDEO), "--race-id", RACE]
        with patch.object(sys, "argv", argv):
            with patch.object(m, "probe_seconds", return_value=120.0):
                with patch.object(m, "openai_responses", side_effect=AssertionError("must not call")):
                    with patch("sys.stdout", new_callable=io.StringIO) as out:
                        self.assertEqual(m.main(), 0)
        preview=json.loads(out.getvalue())
        self.assertEqual(preview["status"], "DRY_RUN_NO_API_CALL")
        self.assertEqual(preview["api_calls"], 0)
        self.assertEqual(preview["frames_uploaded"], 0)
        with patch.object(sys, "argv", argv+["--analyze"]):
            with patch.object(m, "probe_seconds", return_value=120.0):
                with patch("sys.stderr", new_callable=io.StringIO):
                    self.assertEqual(m.main(), 2)

    def test_invalid_response_structure_is_rejected(self):
        payload = sample_prediction([0.5,8.5])
        payload["extra"] = "not in schema"
        with self.assertRaisesRegex(ValueError, "schema-compliant"):
            m.validate_prediction(payload, [0.5,8.5])
        payload = sample_prediction([0.5,8.5])
        payload["riders"][0]["confidence"] = True
        with self.assertRaises(ValueError):
            m.validate_prediction(payload, [0.5,8.5])

    def test_env_key_loaded_from_untracked_local_file(self):
        with patch.dict(os.environ, {}, clear=True):
            Path(".env").write_text("OPENAI_API_KEY=sk-test-never-share\n", encoding="utf-8")
            self.assertEqual(m.load_openai_key(), "sk-test-never-share")


class ResultMalformed:
    returncode = 0
    stdout = b"not a jpeg"


if __name__ == "__main__":
    unittest.main()

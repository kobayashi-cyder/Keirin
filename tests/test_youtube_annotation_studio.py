"""Offline tests for YouTube embedded human annotation and model integration."""
import csv
import http.client
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import youtube_annotation_studio as studio
import build_multiview_features as multiview
from youtube_race_search import HEADER

RACE = "7520261108010001"
VID = "AbCd1234_-X"
RACE_AT = "2026-11-08T20:40:00+09:00"
REVIEW_AT = "2026-11-08T20:55:00+09:00"
NOW = datetime.fromisoformat("2026-11-08T21:20:00+09:00")
LINK = Path("local_data/history/youtube_race_links.csv")
OUT = Path("local_data/history/youtube_rider_events.csv")


def csv_write(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def reviewed_link():
    csv_write(LINK, HEADER, [{
        "race_id": RACE, "race_date": "2026-11-08", "venue": "松山",
        "race_number": "1", "video_id": VID,
        "reviewed_at_jst": REVIEW_AT,
        "review_status": "MANUALLY_CONFIRMED_LINK_ONLY",
        "download_permission": "NOT_ESTABLISHED",
    }])


def session():
    return studio.prepare_session(
        RACE, RACE_AT, "matsuyama", "F2", 7, now=NOW
    )


def complete_payload():
    return {
        "review_complete": True,
        "riders": [
            {"car_no": car, "rider_id": f"S{car}",
             "finish_position": car, "line_cohesion": 0.75}
            for car in range(1, 8)
        ],
        "markers": [
            {"car_no": 1, "kind": "attack", "second": 31.2},
            {"car_no": 1, "kind": "front_start", "second": 32.0},
            {"car_no": 1, "kind": "front_end", "second": 42.3},
            {"car_no": 1, "kind": "late_gain", "second": 56.5, "places": 1},
        ],
    }


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.tmp.name)
        reviewed_link()
        self.s = session()

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def test_server_session_uses_only_manual_reviewed_video(self):
        self.assertEqual(self.s["video_id"], VID)
        self.assertEqual(self.s["display_venue"], "松山")
        self.assertEqual(self.s["venue_key"], "matsuyama")
        self.assertEqual(self.s["cars"], 7)

    def test_event_markers_are_aggregated_not_inferred(self):
        rows = studio.build_annotated_rows(complete_payload(), self.s, now=NOW)
        self.assertEqual(len(rows), 7)
        first = rows[0]
        self.assertEqual(first["attack_count"], "1")
        self.assertEqual(first["front_seconds"], "10.30")
        self.assertEqual(first["late_gain_places"], "1")
        self.assertEqual(first["video_sha256"], "")
        self.assertEqual(first["youtube_video_id"], VID)
        self.assertEqual(first["rights_status"], "self_recorded")
        self.assertEqual(first["available_at_jst"], NOW.isoformat())
        self.assertEqual(first["annotation_markers_json"].count('"kind"'), 4)
        self.assertEqual(rows[1]["attack_count"], "0")
        self.assertEqual(rows[1]["annotation_markers_json"], "[]")
        self.assertEqual(first["source_url"],
                         "https://www.youtube.com/watch?v="+VID)
        self.assertEqual(set(first), set(studio.ANNOTATION_FIELDS))

    def test_atomic_csv_save_and_duplicate_race_rejection(self):
        result = studio.save_annotations(complete_payload(), self.s,
                                         output=OUT, now=NOW)
        self.assertEqual(result["status"], "ANNOTATED_HISTORICAL_RACE_SAVED_NOT_TRAINED")
        self.assertEqual(result["rider_rows_added"], 7)
        self.assertFalse(result["video_downloaded"])
        with OUT.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["youtube_video_id"], VID)
        self.assertNotIn("video_blob", rows[0])
        with self.assertRaisesRegex(ValueError, "already been reviewed"):
            studio.save_annotations(complete_payload(), self.s,
                                    output=OUT, now=NOW)
        with OUT.open(encoding="utf-8", newline="") as f:
            self.assertEqual(sum(1 for _ in f), 8)

    def test_requires_full_review_and_rider_identity(self):
        d = complete_payload()
        d["review_complete"] = False
        with self.assertRaisesRegex(ValueError, "confirmation"):
            studio.build_annotated_rows(d, self.s, now=NOW)
        d = complete_payload()
        d["riders"][0]["rider_id"] = ""
        with self.assertRaisesRegex(ValueError, "stable"):
            studio.build_annotated_rows(d, self.s, now=NOW)
        d = complete_payload()
        d["riders"][2]["rider_id"] = "S1"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            studio.build_annotated_rows(d, self.s, now=NOW)
        d = complete_payload()
        d["riders"][2]["finish_position"] = 1
        with self.assertRaisesRegex(ValueError, "duplicate"):
            studio.build_annotated_rows(d, self.s, now=NOW)

    def test_marker_pairing_and_maximums(self):
        d = complete_payload()
        d["markers"] = [{"car_no": 1, "kind": "front_start", "second": 33.0}]
        with self.assertRaisesRegex(ValueError, "unclosed"):
            studio.build_annotated_rows(d, self.s, now=NOW)
        d["markers"] = [{"car_no": 1, "kind": "front_end", "second": 33.0}]
        with self.assertRaisesRegex(ValueError, "must follow"):
            studio.build_annotated_rows(d, self.s, now=NOW)
        d["markers"] = [{"car_no": 9, "kind": "attack", "second": 33.0}]
        with self.assertRaisesRegex(ValueError, "marker car"):
            studio.build_annotated_rows(d, self.s, now=NOW)
        d["markers"] = [{"car_no": 1, "kind": "attack", "second": float("nan")}]
        with self.assertRaisesRegex(ValueError, "outside"):
            studio.build_annotated_rows(d, self.s, now=NOW)
        d["markers"] = [{"car_no": 1, "kind": "late_gain", "second": 3.0, "places": 9}]
        with self.assertRaisesRegex(ValueError, "late_gain"):
            studio.build_annotated_rows(d, self.s, now=NOW)

    def test_line_cohesion_missing_and_nonfinite_rejected(self):
        for value in (None, float("inf"), float("nan"), -0.1, 1.1, "0.5"):
            d = complete_payload()
            d["riders"][0]["line_cohesion"] = value
            with self.assertRaisesRegex(ValueError, "line_cohesion"):
                studio.build_annotated_rows(d, self.s, now=NOW)

    def test_race_date_future_review_and_wrong_link_refused(self):
        with self.assertRaisesRegex(ValueError, "start date"):
            studio.prepare_session(RACE, "2026-11-09T20:40:00+09:00",
                                   "matsuyama", "F2", 7,
                                   now=datetime.fromisoformat("2026-11-10T21:20:00+09:00"))
        with self.assertRaisesRegex(ValueError, "not occurred"):
            studio.prepare_session(RACE, RACE_AT,
                                   "matsuyama", "F2", 7,
                                   now=datetime.fromisoformat("2026-11-08T20:00:00+09:00"))
        with self.assertRaisesRegex(ValueError, "cannot predate"):
            studio.build_annotated_rows(
                complete_payload(), self.s,
                now=datetime.fromisoformat("2026-11-08T20:20:00+09:00"))
        d = dict(self.s)
        d["video_id"] = "AaBbCcDDee0"
        with self.assertRaisesRegex(ValueError, "association changed"):
            studio.build_annotated_rows(complete_payload(), d, now=NOW)

    def test_old_csv_without_youtube_id_still_supported(self):
        row = studio.build_annotated_rows(complete_payload(), self.s, now=NOW)[0]
        del row["youtube_video_id"]
        row["video_sha256"] = "a"*64
        for name in ("attack_count", "front_seconds", "late_gain_places",
                     "line_cohesion"):
            self.assertTrue(row[name])
        # A local-video provenance row must use its own legitimate URL.
        row["source_url"] = "https://example.org/legally-held-video"
        multiview.validate_history(row)

    def test_multiview_builder_reads_reviewed_youtube_notes(self):
        studio.save_annotations(complete_payload(), self.s, now=NOW)
        with OUT.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 7)
        # 20-dimensional features can aggregate these human observations
        # only after the annotations were actually saved.
        context = {
            "rider_id": "S1", "venue": "matsuyama", "grade": "F2",
            "bank_length_m": "400", "wind_mps": "1.8",
            "rights_status": "permitted",
            "source_url": "https://example.org/preclose-racecard"
        }
        after = datetime.fromisoformat("2026-11-15T18:00:00+09:00")
        observed = multiview.dt(after.isoformat())
        for row in rows:
            multiview.validate_history(row)
        tagged = [{**row,
                   "_race_at": multiview.dt(row["race_at_jst"]),
                   "_available_at": multiview.dt(row["available_at_jst"])}
                  for row in rows]
        feats = multiview.features_for_target(
            context, observed, [r for r in tagged if r["rider_id"] == "S1"])
        self.assertEqual(feats["hist_video_races_180d"], "1")
        self.assertEqual(feats["hist_attack_rate_180d"], "1")
        self.assertEqual(feats["hist_front_seconds_180d"], "10.3")
        self.assertEqual(feats["hist_same_venue_races_365d"], "1")
        with self.assertRaisesRegex(ValueError, "no eligible earlier history"):
            multiview.features_for_target(
                context, multiview.dt("2026-11-08T18:00:00+09:00"),
                [r for r in tagged if r["rider_id"] == "S1"])

    def test_review_link_not_a_licensed_video_file(self):
        with OUT.open("w", encoding="utf-8", newline="") as fh:
            fh.write("")
        row = studio.build_annotated_rows(complete_payload(), self.s, now=NOW)[0]
        self.assertEqual(row["rights_status"], "self_recorded")
        self.assertEqual(row["video_sha256"], "")
        self.assertEqual(row["youtube_video_id"], VID)
        self.assertEqual(studio.UI_FILE.name, "youtube_annotation_studio.html")

    def test_HTTP_local_ui_and_csrf_protection(self):
        with studio.AnnotatorServer(("127.0.0.1", 0), studio.AnnotationHandler,
                                    self.s, output=OUT) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=4)
            try:
                conn.request("GET", "/")
                response = conn.getresponse()
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("youtube.com/iframe_api", body)
                self.assertIn(VID, body)
                self.assertNotIn("__RACE_CONFIGURATION_JSON__", body)
                self.assertNotIn("yt-dlp", body)

                data = json.dumps(complete_payload()).encode("utf-8")
                origin = server.expected_origin
                conn.request("POST", "/api/save", body=data, headers={
                    "Content-Type": "application/json",
                    "Origin": "http://attacker.example",
                    "X-Annotation-Token": server.secret
                })
                resp = conn.getresponse()
                resp.read()
                self.assertEqual(resp.status, 403)
                self.assertFalse(OUT.exists())

                conn.request("POST", "/api/save", body=data, headers={
                    "Content-Type": "application/json",
                    "Origin": origin,
                    "X-Annotation-Token": server.secret,
                })
                resp = conn.getresponse()
                parsed = json.loads(resp.read())
                self.assertEqual(resp.status, 200)
                self.assertEqual(parsed["rider_rows_added"], 7)
                self.assertTrue(OUT.exists())
            finally:
                conn.close()
                server.shutdown()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()

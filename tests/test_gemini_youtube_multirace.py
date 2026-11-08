"""Zero-network regression tests for arbitrary-race Gemini YouTube batch research."""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import gemini_youtube_multirace as m
import build_multiview_features as mv
from pre_race_readiness import COLUMNS

RACE1 = "7520261108010001"
RACE2 = "7520261108020001"
VIDEO1 = "BhwmwNby6Ow"
VIDEO2 = "AbCd1234_-X"
URL1 = "https://www.youtube.com/watch?v=" + VIDEO1
URL2 = "https://www.youtube.com/watch?v=" + VIDEO2
JST = "+09:00"


def job(race_id=RACE1, url=URL1):
    return m.parse_race({
        "race_id": race_id, "race_date": "2026-11-08",
        "venue": "松山", "venue_key": "matsuyama",
        "race_number": "1" if race_id == RACE1 else "2",
        "cars": "7", "youtube_url": url
    })


def model_answer(*, difference=0, identity=True, missing=False, start=100):
    return {
        "target_race_visible": identity,
        "full_race_covered": identity,
        "race_start_seconds": start if identity else None,
        "race_end_seconds": start+250 if identity else None,
        "identity_evidence": "on-screen local raceboard: 松山1R" if identity else "",
        "riders": [
            {
                "car_no": car,
                "attack_count": (1 + difference) if car == 1 else 0,
                "front_seconds": (25.0 + difference) if car == 1 else 0.0,
                "late_gain_places": 1 if car == 1 else 0,
                "line_cohesion": .8 if not missing else None,
                "confidence": .96,
                "evidence_seconds": [start+20, start+220],
                "limitations": "synthetic fixture",
            } for car in range(1, 8)
        ] if identity else [],
    }


def fake_call(race, url, pass_index, model, key):
    return model_answer()


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def actual_result_rows(race_id=RACE1):
    return [{
        "race_id": race_id,
        "race_at_jst": "2026-11-08T20:45:00+09:00",
        "venue": "matsuyama", "grade": "F2",
        "car_no": str(i), "rider_id": f"R{i}",
        "finish_position": str(i),
        "source_url": "https://example.org/provider-licensed-results",
        "rights_status": "permitted",
    } for i in range(1, 8)]


class MultiGeminiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.tmp.cleanup()

    def test_any_race_mapping_and_strict_canonical_youtube_urls(self):
        target = job("7520261209100001", "https://youtu.be/" + VIDEO2)
        self.assertEqual(target.race_id, "7520261209100001")
        self.assertEqual(target.youtube_url, URL2)
        self.assertEqual(m.canonical_video(URL1)[0], VIDEO1)
        self.assertEqual(m.canonical_video(
            "https://youtube.com/watch?v="+VIDEO1+"&t=15")[1], URL1)
        for bad in (
            "http://youtube.com/watch?v="+VIDEO1,
            "https://evil.example/watch?v="+VIDEO1,
            "https://youtube.com.evil.example/watch?v="+VIDEO1,
            "https://youtube.com/playlist?list=123",
            "file:///watch?v="+VIDEO1,
            "https://youtube.com/watch?v=bad",
        ):
            with self.assertRaises(ValueError):
                m.canonical_video(bad)
        with self.assertRaises(ValueError):
            m.parse_race({**m.asdict(job()), "cars": "6"})

    def test_queue_template_and_multiple_valid_races(self):
        path = Path("local_data/history/gemini_queue.csv")
        m.create_template(path)
        with self.assertRaisesRegex(ValueError, "already exists"):
            m.create_template(path)
        with path.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=m.QUEUE_FIELDS)
            w.writerow({**m.asdict(job()), "race_number": "1", "cars": "7"})
            w.writerow({**m.asdict(job(RACE2, URL2)), "race_number": "2", "cars": "7"})
        jobs = m.load_jobs(path)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[1].youtube_url, URL2)
        with path.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=m.QUEUE_FIELDS)
            w.writerow({**m.asdict(job()), "race_number": "1", "cars": "7"})
        with self.assertRaisesRegex(ValueError, "duplicated"):
            m.load_jobs(path)

    def test_dry_run_references_races_without_google_requests(self):
        args = ["app", "--race-id", RACE1, "--race-date", "2026-11-08",
                "--venue", "松山", "--venue-key", "matsuyama", "--race-number", "1",
                "--cars", "7", "--video-url", URL1]
        with patch.object(sys, "argv", args):
            with patch.object(m, "call_gemini", side_effect=AssertionError("no calls")):
                with patch("sys.stdout", new_callable=io.StringIO) as out:
                    self.assertEqual(m.main(), 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["status"], "DRY_RUN_NO_API_CALL")
        self.assertEqual(report["race_jobs"][0]["video_url"], URL1)

    def test_two_independent_passes_and_parallel_races(self):
        lock = threading.Lock()
        active = [0, 0]
        def fake(job, url, idx, model, key):
            with lock:
                active[0] += 1
                active[1] = max(active[0], active[1])
            time.sleep(.03)
            with lock:
                active[0] -= 1
            return model_answer()
        two = [job(), job(RACE2, URL2)]
        result = m.analyze_batch(two, model="gemini-2.5-flash",
                                 workers=2, passes=2, max_api_calls=4,
                                 key="fake", caller=fake)
        self.assertEqual(result["tasks_planned"], 4)
        self.assertEqual(result["completed_passes"], 4)
        self.assertEqual(len(result["reports"]), 2)
        self.assertGreaterEqual(active[1], 2)
        self.assertLessEqual(active[1], 2)
        for doc in result["reports"]:
            self.assertEqual(doc["status"], "AUTOMATED_RESEARCH_CANDIDATE")
            self.assertTrue(doc["identity_consensus"])
            self.assertFalse(doc["training_validated"])
        calls = [0]
        def must_not_replay(*a):
            calls[0] += 1
            raise AssertionError("saved calls must never repeat")
        resumed = m.analyze_batch(two, model="gemini-2.5-flash",
                                  workers=2, passes=2, max_api_calls=4,
                                  key="fake", caller=must_not_replay)
        self.assertEqual(calls[0], 0)
        self.assertEqual(len(resumed["reports"]), 2)

    def test_api_budget_and_missing_url_never_call_google_automatically(self):
        with self.assertRaisesRegex(ValueError, "exceed"):
            m.analyze_batch([job(),job(RACE2,URL2)],
                            model="gemini-2.5-flash", max_api_calls=3,
                            key="fake", caller=fake_call)
        missing = job(url="")
        with self.assertRaisesRegex(ValueError, "no video URL"):
            m.analyze_batch([missing], model="gemini-2.5-flash",
                            key="fake", caller=fake_call)
        with self.assertRaisesRegex(ValueError, "max_searches"):
            m.analyze_batch([missing], model="gemini-2.5-flash",
                            auto_discover=True,max_searches=0,
                            key="fake", caller=fake_call,
                            discoverer=lambda j: [])

    def test_auto_discovery_uses_unverified_candidate_and_still_gates_identity(self):
        target = job(url="")
        def discover(_):
            return [{"url": URL1, "status": "UNVERIFIED_SEARCH_CANDIDATE"}]
        r = m.analyze_batch(
            [target], model="gemini-2.5-flash",
            auto_discover=True, max_searches=1, candidates_per_race=1,
            key="fake", caller=fake_call, discoverer=discover
        )
        self.assertEqual(len(r["reports"]), 1)
        self.assertEqual(r["reports"][0]["youtube_url"], URL1)
        self.assertEqual(r["reports"][0]["race"]["youtube_url"], "")
        self.assertTrue(r["reports"][0]["all_metrics_consensus"])

    def test_disagreement_missing_and_identity_failure_are_quarantined(self):
        j=job()
        docs = []
        for i, pred in enumerate((model_answer(), model_answer(difference=1)), 1):
            docs.append({"schema": m.PASS_SCHEMA, "race": m.asdict(j),
                         "youtube_url": URL1, "pass_index": i,
                         "model": "gemini-2.5-flash", "prediction": pred})
        result = m.agree(j, URL1, docs)
        self.assertIsNone(result["riders"][0]["attack_count"])
        self.assertFalse(result["all_metrics_consensus"])
        docs[1]["prediction"] = model_answer(missing=True)
        result = m.agree(j, URL1, docs)
        self.assertIsNone(result["riders"][0]["line_cohesion"])
        docs[1]["prediction"] = model_answer(identity=False)
        result = m.agree(j, URL1, docs)
        self.assertFalse(result["identity_consensus"])
        self.assertEqual(result["status"], "INCOMPLETE_OR_DISAGREED_NOT_TRAINING_READY")

    def test_timestamp_and_invalid_numeric_is_rejected(self):
        j = job()
        wrong = model_answer()
        wrong["riders"][0]["evidence_seconds"] = [999999]
        with self.assertRaisesRegex(ValueError, "range"):
            m.validate_response(wrong, j)
        wrong = model_answer()
        wrong["riders"][0]["attack_count"] = float("nan")
        with self.assertRaises(ValueError):
            m.validate_response(wrong, j)
        wrong = model_answer()
        wrong["riders"][0]["car_no"] = 2
        with self.assertRaisesRegex(ValueError, "duplicate"):
            m.validate_response(wrong, j)
        wrong = model_answer()
        wrong["target_race_visible"] = False
        wrong["full_race_covered"] = True
        # A claim of full video but not matching target is conservatively
        # handled at the consensus gate, not by one pass alone.
        m.validate_response(wrong, j)

    def test_export_requires_independent_official_results_and_time(self):
        result = m.analyze_batch([job()], model="gemini-2.5-flash",
                                 key="fake", caller=fake_call)
        reports = result["reports"]
        path = Path("local_data/history/verified_race_results.csv")
        write_csv(path, m.OFFICIAL_FIELDS, actual_result_rows())
        later = datetime.now(m.JST) + timedelta(minutes=5)
        success = m.export_research(reports, path, now=later)
        self.assertEqual(success["exported_races"], 1)
        self.assertEqual(success["rows_added"], 7)
        data = m._read_csv(m.EVENTS_FILE, m.HISTORY_FIELDS)
        self.assertEqual(len(data), 7)
        self.assertTrue(all(row["youtube_video_id"] == VIDEO1 for row in data))
        self.assertEqual(data[0]["finish_position"], "1")
        self.assertEqual(data[0]["rights_status"], "self_recorded")
        links = m._read_csv(m.LINKS_FILE, m.HEADER)
        self.assertEqual(links[0]["review_status"], m.AUTO_STATUS)
        self.assertEqual(links[0]["video_id"], VIDEO1)
        again = m.export_research(reports, path, now=later)
        self.assertEqual(again["exported_races"], 0)
        self.assertEqual(again["blocked"][0]["reason"], "already_exported_no_overwrite")

    def test_wrong_or_missing_official_results_blocks_auto_export(self):
        reports = m.analyze_batch([job()], model="gemini-2.5-flash",
                                  key="fake", caller=fake_call)["reports"]
        result_path = Path("local_data/history/verified.csv")
        off = actual_result_rows()
        off[0]["rider_id"] = off[1]["rider_id"]
        write_csv(result_path, m.OFFICIAL_FIELDS, off)
        blocked = m.export_research(reports, result_path,
                                    now=datetime.now(m.JST)+timedelta(minutes=5))
        self.assertEqual(blocked["exported_races"], 0)
        self.assertFalse(m.EVENTS_FILE.exists())
        off = actual_result_rows()
        off[0]["venue"] = "other"
        write_csv(result_path, m.OFFICIAL_FIELDS, off)
        blocked = m.export_research(reports, result_path,
                                    now=datetime.now(m.JST)+timedelta(minutes=5))
        self.assertEqual(blocked["exported_races"], 0)

    def test_full_existing_20_feature_builder_explicit_opt_in(self):
        # Use an earlier race and later future pre-race snapshot to ensure no leakage.
        reports = m.analyze_batch([job()], model="gemini-2.5-flash",
                                  key="fake", caller=fake_call)["reports"]
        results_path = Path("local_data/history/official.csv")
        write_csv(results_path, m.OFFICIAL_FIELDS, actual_result_rows())
        t = datetime.fromisoformat("2026-11-08T23:00:00+09:00")
        e = m.export_research(reports, results_path, now=t)
        self.assertEqual(e["exported_races"], 1)
        base = Path("local_data/pre_race_features.csv")
        context = Path("local_data/history/target_context.csv")
        future_race = "7520261115010001"
        base_rows = [{
            "race_id": future_race, "race_date": "2026-11-15",
            "car_no": str(car), "observed_at_jst": "2026-11-15T18:00:00+09:00",
            "ingested_at_jst": "2026-11-15T18:01:00+09:00",
            "deadline_at_jst": "2026-11-15T20:35:00+09:00",
            "start_at_jst": "2026-11-15T20:40:00+09:00",
            "source_url": "https://example.org/authorized-preclose",
            "rights_status": "permitted", "line_id": str(car),
            "rating": str(78+car), "recent_win_rate": ".1",
            "recent_top2_rate": ".25", "recent_top3_rate": ".5",
            "line_position": "1", "age": "30",
        } for car in range(1,8)]
        contexts = [{
            "race_id": future_race, "car_no": str(car), "rider_id": f"R{car}",
            "venue": "matsuyama", "grade": "F2", "bank_length_m": "400",
            "wind_mps": "3.1", "observed_at_jst": "2026-11-15T17:58:00+09:00",
            "source_url": "https://example.org/authorized-preclose",
            "rights_status": "permitted",
        } for car in range(1,8)]
        write_csv(base, COLUMNS+("ingested_at_jst",), base_rows)
        write_csv(context, mv.CONTEXT_FIELDS, contexts)
        with self.assertRaisesRegex(ValueError, "matching manually reviewed"):
            mv.build(base, context, m.EVENTS_FILE,
                     youtube_index=m.LINKS_FILE,
                     now=datetime.fromisoformat("2026-11-16T00:00:00+09:00"))
        rows, races = mv.build(
            base, context, m.EVENTS_FILE,
            youtube_index=m.LINKS_FILE, allow_ai_research=True,
            now=datetime.fromisoformat("2026-11-16T00:00:00+09:00")
        )
        self.assertEqual((len(rows),races),(7,1))
        self.assertEqual(rows[0]["hist_video_races_180d"], "1")
        self.assertEqual(rows[0]["hist_attack_rate_180d"], "1")
        self.assertEqual(rows[0]["hist_front_seconds_180d"], "25")

    def test_future_info_prevented_in_prereace_multiview(self):
        reports = m.analyze_batch([job()], model="gemini-2.5-flash",
                                  key="fake", caller=fake_call)["reports"]
        official = Path("local_data/history/official.csv")
        write_csv(official, m.OFFICIAL_FIELDS, actual_result_rows())
        m.export_research(reports, official,
                          now=datetime.fromisoformat("2026-11-16T00:00:00+09:00"))
        records = m._read_csv(m.EVENTS_FILE,m.HISTORY_FIELDS)
        tagged = [
            {**r, "_race_at": mv.dt(r["race_at_jst"]),
             "_available_at": mv.dt(r["available_at_jst"])}
            for r in records if r["rider_id"] == "R1"
        ]
        context = {
            "venue": "matsuyama", "grade": "F2",
            "bank_length_m": "400", "wind_mps": "1",
            "source_url": "https://example.org/preclose",
            "rights_status": "permitted",
        }
        with self.assertRaisesRegex(ValueError, "no eligible earlier history"):
            mv.features_for_target(
                context, mv.dt("2026-11-15T18:00:00+09:00"), tagged
            )

    def test_cli_requires_explicit_cost_acknowledgment(self):
        args=["test","--race-id",RACE1,"--race-date","2026-11-08",
              "--venue","松山","--venue-key","matsuyama",
              "--race-number","1","--cars","7",
              "--video-url",URL1,"--analyze"]
        with patch.object(sys,"argv",args):
            with patch("sys.stderr",new_callable=io.StringIO) as err:
                self.assertEqual(m.main(),2)
                self.assertIn("--confirm-api-cost",err.getvalue())


if __name__=="__main__":
    unittest.main()

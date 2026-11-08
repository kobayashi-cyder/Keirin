"""Offline tests for local automatic research-data Discord notifications."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from audit_odds_snapshot import expected_keys
import auto_discord_research as mod

NOW = datetime(2026, 11, 8, 9, 35, tzinfo=timezone.utc)


def record(*, full=True, observed="2026-11-08T18:31:00+09:00",
           updated="2026-11-08T18:30:00+09:00"):
    data = {
        "source_url": "https://keirin.kdreams.jp/matsuyama/racedetail/7520261108010001/?pageType=odds",
        "observed_at_jst": observed,
        "deadline_at_jst": "2026-11-08T20:35:00+09:00",
        "page_displayed_times_jst": [updated],
        "cars": 7, "markets": {},
    }
    for name in ("3rentan", "3renpuku", "2shatan", "2shafuku", "wide"):
        keys = sorted(expected_keys(name, 7))
        used = keys if full or name != "3rentan" else keys[:50]
        data["markets"][name] = {
            "quotes": {key: 2.0 for key in used},
            "count": len(used), "expected_count": len(keys),
            "complete": len(used) == len(keys),
        }
    return data


class AutoResearchTests(unittest.TestCase):
    def test_all_markets_complete_and_fresh_is_eligible(self):
        report, state = mod.inspect_record(record(), now=NOW)
        self.assertEqual(state, "READY_RESEARCH_COUNTS_ONLY")
        self.assertEqual(len(report["markets_structurally_complete"]), 5)
        self.assertEqual(report["qualifying_count"], 0)
        self.assertFalse(report["alert_allowed"])

    def test_partial_market_and_missing_market_are_not_alertable(self):
        report, why = mod.inspect_record(record(full=False), now=NOW)
        self.assertIsNone(report)
        self.assertEqual(why, "INCOMPLETE_MARKET")
        rec = record()
        del rec["markets"]["wide"]
        report, why = mod.inspect_record(rec, now=NOW)
        self.assertIsNone(report)
        self.assertEqual(why, "MISSING_MARKETS")

    def test_stale_and_deadline_are_not_alertable(self):
        report, why = mod.inspect_record(record(),
                                         now=datetime(2026, 11, 8, 10, 0,
                                                      tzinfo=timezone.utc))
        self.assertIsNone(report)
        self.assertEqual(why, "STALE_LOCAL_SNAPSHOT")
        report, why = mod.inspect_record(
            record(observed="2026-11-08T20:29:00+09:00",
                   updated="2026-11-08T20:29:00+09:00"),
            now=datetime(2026, 11, 8, 11, 30, tzinfo=timezone.utc),
        )
        self.assertIsNone(report)
        self.assertEqual(why, "PAST_OR_NEAR_DEADLINE")

    def test_persistent_dedup_after_send_no_leaking_odds(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            old = os.getcwd()
            os.chdir(tmp)
            try:
                folder = Path("local_data/odds_snapshots")
                folder.mkdir(parents=True)
                p = folder / "7520261108010001.jsonl"
                p.write_text(json.dumps(record()) + "\n", encoding="utf-8")
                sent = []
                def fake_send(text):
                    sent.append(text)
                    return "SENT"
                outcome = mod.run_scan(folder, mod.STATE_PATH, now=NOW,
                                       send=True, poster=fake_send)
                self.assertEqual(outcome["sent"], 1)
                self.assertEqual(len(sent), 1)
                self.assertIn("3連単：210/210", sent[0])
                self.assertNotIn("2.0", sent[0])
                self.assertNotIn("期待回収率130", sent[0])
                self.assertEqual(len(mod.read_state()["notified"]), 1)
                new_result = mod.run_scan(folder, mod.STATE_PATH, now=NOW,
                                          send=True, poster=fake_send)
                self.assertEqual(new_result["sent"], 0)
                self.assertEqual(len(sent), 1)
            finally:
                os.chdir(old)

    def test_dry_run_never_persists_as_notified(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                folder = Path("local_data/odds_snapshots")
                folder.mkdir(parents=True)
                (folder / "race.jsonl").write_text(json.dumps(record())+"\n",
                                                    encoding="utf-8")
                res = mod.run_scan(folder, mod.STATE_PATH, now=NOW, send=False,
                                   poster=lambda x: self.fail("poster called"))
                self.assertEqual(res["counts"]["DRY_RUN_READY"], 1)
                self.assertFalse(mod.STATE_PATH.exists())
            finally:
                os.chdir(old)

    def test_openai_failure_does_not_send_or_mark_notified(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                folder = Path("local_data/odds_snapshots")
                folder.mkdir(parents=True)
                (folder / "race.jsonl").write_text(json.dumps(record())+"\n",
                                                    encoding="utf-8")
                suppressed = set()
                result = mod.run_scan(
                    folder, mod.STATE_PATH, now=NOW, send=True, use_openai=True,
                    poster=lambda x: self.fail("must not post"),
                    note_generator=lambda kind, model: (None, "HTTP_429"),
                    suppress_failed=suppressed,
                )
                self.assertEqual(result["counts"]["OPENAI_HTTP_429"], 1)
                self.assertEqual(suppressed, {"race.jsonl"})
                self.assertFalse(mod.STATE_PATH.exists())
                again = mod.run_scan(
                    folder, mod.STATE_PATH, now=NOW, send=True, use_openai=True,
                    poster=lambda x: self.fail("must not post"),
                    note_generator=lambda kind, model: self.fail("retry"),
                    suppress_failed=suppressed,
                )
                self.assertEqual(again["counts"]["SUPPRESSED_FAILED_THIS_RUN"], 1)
            finally:
                os.chdir(old)

    def test_invalid_file_does_not_crash_scanning(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                folder = Path("local_data/odds_snapshots")
                folder.mkdir(parents=True)
                (folder / "wrong.jsonl").write_text("{not json", encoding="utf-8")
                outcome = mod.run_scan(folder, mod.STATE_PATH, now=NOW, send=True,
                                       poster=lambda x: self.fail("must not send"))
                self.assertEqual(outcome["counts"]["INVALID_SNAPSHOT"], 1)
            finally:
                os.chdir(old)

    def test_paths_cannot_serve_as_arbitrary_file_access(self):
        for p in (Path("/tmp/odds.jsonl"), Path("../.env"),
                  Path("local_data/../.env"), Path(".env")):
            with self.assertRaises(ValueError):
                mod.safe_path(p)

    def test_state_corruption_rejected_not_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                path = Path("local_data/discord_research_notified.json")
                path.parent.mkdir()
                path.write_text("not-json", encoding="utf-8")
                with self.assertRaises(json.JSONDecodeError):
                    mod.read_state()
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()

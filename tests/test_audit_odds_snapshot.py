"""Read-only odds archive audit, zero HTTP calls and zero real bets."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from audit_odds_snapshot import audit_jsonl, audit_record, expected_keys


def seven_car_record():
    markets = {}
    for market in ("3rentan", "3renpuku", "2shatan", "2shafuku", "wide"):
        keys = sorted(expected_keys(market, 7))
        observed = keys[:50] if market == "3rentan" else keys
        markets[market] = {
            "quotes": {k: 3.5 for k in observed},
            "count": len(observed),
            "expected_count": len(keys),
            "complete": len(observed) == len(keys),
        }
    return {
        "source_url": "https://keirin.kdreams.jp/matsuyama/racedetail/7520261108010001/?pageType=odds",
        "cars": 7,
        "observed_at_jst": "2026-11-08T17:48:16+09:00",
        "deadline_at_jst": "2026-11-08T20:35:00+09:00",
        "page_displayed_times_jst": ["2026-11-08T17:24:00+09:00"],
        "markets": markets,
        "parser_method": "html_structure",
    }


class SnapshotAuditTests(unittest.TestCase):
    def test_screenshot_pattern_four_complete_and_top50_partial(self):
        d = audit_record(seven_car_record(), now=datetime(2026,11,8,9,0,tzinfo=timezone.utc))
        self.assertEqual(d["status"], "STRUCTURE_CHECK_PASSED_NOT_SOURCE_VERIFIED")
        self.assertEqual(d["markets"]["3rentan"]["missing"], 160)
        self.assertEqual(d["markets"]["3rentan"]["observed"], 50)
        for other in ("3renpuku", "2shatan", "2shafuku", "wide"):
            self.assertTrue(d["markets"][other]["structural_coverage_complete"])
        self.assertEqual(d["source_freshness"], "STALE_OR_MIXED_PAGE_UPDATE_TIME")
        self.assertFalse(d["alert_allowed"])

    def test_historical_snapshot_cannot_be_live(self):
        d = audit_record(seven_car_record(), now=datetime(2026,11,9,tzinfo=timezone.utc))
        self.assertEqual(d["current_usability"], "HISTORICAL_DO_NOT_TREAT_AS_LIVE")
        self.assertFalse(d["bet_allowed"])

    def test_wrong_market_ticket_rejected(self):
        rec = seven_car_record()
        rec["markets"]["2shatan"]["quotes"]["1-1"] = 20.0
        rec["markets"]["2shatan"]["count"] += 1
        rec["markets"]["2shatan"]["complete"] = False
        d = audit_record(rec)
        self.assertEqual(d["status"], "REJECTED")
        self.assertTrue(any("invalid ticket" in issue for issue in d["issues"]))

    def test_mismatch_count_rejected(self):
        rec = seven_car_record()
        rec["markets"]["3rentan"]["complete"] = True
        self.assertEqual(audit_record(rec)["status"], "REJECTED")

    def test_missing_page_update_time_never_implies_freshness(self):
        rec = seven_car_record()
        rec["page_displayed_times_jst"] = []
        d = audit_record(rec, now=datetime(2026,11,8,9,0,tzinfo=timezone.utc))
        self.assertEqual(d["source_freshness"], "UNKNOWN_PAGE_UPDATE_TIME")
        self.assertEqual(d["current_usability"], "UNKNOWN_SOURCE_FRESHNESS")

    def test_eight_nine_frame_wagers_have_expected_counts(self):
        self.assertEqual(len(expected_keys("2wakutan", 8)), 32)
        self.assertEqual(len(expected_keys("2wakufuku", 8)), 17)
        self.assertEqual(len(expected_keys("2wakutan", 9)), 33)
        self.assertEqual(len(expected_keys("2wakufuku", 9)), 18)

    def test_last_jsonl_record_only(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "sample.jsonl"
            with p.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(seven_car_record()) + "\n")
                fh.write(json.dumps(seven_car_record()) + "\n")
            d = audit_jsonl(p)
            self.assertEqual(d["record_count"], 2)
            self.assertEqual(d["markets"]["3rentan"]["missing"], 160)

    def test_odds_value_range_and_predeadline_are_checked(self):
        rec = seven_car_record()
        rec["markets"]["wide"]["quotes"]["1-2"] = {"min": 4.5, "max": 3.0}
        self.assertEqual(audit_record(rec)["status"], "REJECTED")
        rec = seven_car_record()
        rec["observed_at_jst"] = "2026-11-08T20:35:00+09:00"
        with self.assertRaisesRegex(ValueError, "not before deadline"):
            audit_record(rec)


if __name__ == "__main__":
    unittest.main()

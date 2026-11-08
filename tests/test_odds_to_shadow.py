"""Offline tests: real-shape snapshot to research-only seven-market EV report."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from audit_odds_snapshot import expected_keys
from odds_to_shadow import (last_record, research_report,
                            validate_probability_input)

SOURCE = "https://keirin.kdreams.jp/matsuyama/racedetail/7520261108010001/?pageType=odds"
NOW = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)


def snapshot(*, cars=7, full_3rentan=True, stale=False):
    markets = {}
    for name in (("3rentan", "2shatan", "3renpuku", "2shafuku", "wide")
                 if cars == 7 else
                 ("3rentan", "2shatan", "3renpuku", "2shafuku",
                  "2wakutan", "2wakufuku", "wide")):
        possible = sorted(expected_keys(name, cars))
        used = possible if name != "3rentan" or full_3rentan else possible[:50]
        price = {"min": 2.5, "max": 3.5} if name == "wide" else 600.0
        markets[name] = {
            "quotes": {combo: price for combo in used},
            "count": len(used),
            "expected_count": len(possible),
            "complete": len(used) == len(possible)
        }
    return {
        "source_url": SOURCE,
        "cars": cars,
        "observed_at_jst": "2026-11-08T17:48:16+09:00",
        "deadline_at_jst": "2026-11-08T20:35:00+09:00",
        "page_displayed_times_jst": [
            "2026-11-08T17:10:00+09:00" if stale else
            "2026-11-08T17:45:00+09:00"
        ],
        "markets": markets,
    }


def distribution(cars=7):
    combos = sorted(expected_keys("3rentan", cars))
    probability = 1 / len(combos)
    return {
        "schema": "keirin-top3-probabilities-v1",
        "race_id": "7520261108010001",
        "cars": cars,
        "model_id": "synthetic-regression-test-not-real-AI",
        "generated_at": "2026-11-08T17:40:00+09:00",
        "values": {combo: probability for combo in combos},
    }


class OddsToShadowTests(unittest.TestCase):
    def test_no_probabilities_reports_coverage_without_predictions(self):
        d = research_report(snapshot(), now=NOW)
        self.assertEqual(d["status"], "NOT_READY_NO_PROBABILITIES")
        self.assertEqual(len(d["markets_structurally_complete"]), 5)
        self.assertFalse(d["markets_excluded_incomplete"])
        self.assertEqual(d["qualifying_count"], 0)
        self.assertEqual(d["candidates"], [])
        self.assertFalse(d["alert_allowed"])
        self.assertFalse(d["model_verified"])

    def test_full_probs_generate_math_only_shadow_candidates(self):
        d = research_report(snapshot(), probabilities=distribution(), now=NOW)
        self.assertEqual(d["status"], "SHADOW_ONLY_UNVALIDATED")
        self.assertEqual(d["probability_model_id"], "synthetic-regression-test-not-real-AI")
        self.assertGreater(d["qualifying_count"], 0)
        self.assertFalse(d["alert_allowed"])
        self.assertFalse(d["bet_allowed"])
        self.assertTrue(all(row["stressed_return"] >= 1.3 for row in d["candidates"]))

    def test_partial_three_rentan_market_is_excluded(self):
        d = research_report(snapshot(full_3rentan=False),
                            probabilities=distribution(), now=NOW)
        self.assertEqual(d["status"], "SHADOW_ONLY_UNVALIDATED")
        self.assertIn("3rentan", d["markets_excluded_incomplete"])
        self.assertNotIn("3rentan", d["markets_structurally_complete"])
        self.assertNotIn("3rentan", {row["market"] for row in d["candidates"]})

    def test_stale_source_prevents_expected_return_rankings(self):
        d = research_report(snapshot(stale=True), probabilities=distribution(), now=NOW)
        self.assertEqual(d["status"], "RESEARCH_BLOCKED_STALE_OR_UNKNOWN_SITE_UPDATE")
        self.assertEqual(d["candidates"], [])
        self.assertFalse(d["alert_allowed"])

    def test_wrong_race_and_wrong_car_count_refused(self):
        probs = distribution()
        probs["race_id"] = "unrelated"
        with self.assertRaisesRegex(ValueError, "another race"):
            validate_probability_input(probs, snapshot())
        probs = distribution()
        probs["cars"] = 9
        with self.assertRaisesRegex(ValueError, "number of cars"):
            validate_probability_input(probs, snapshot())

    def test_missing_outcomes_and_late_generated_model_refused(self):
        probs = distribution()
        probs["values"].pop(next(iter(probs["values"])))
        with self.assertRaises(ValueError):
            validate_probability_input(probs, snapshot())
        probs = distribution()
        probs["generated_at"] = "2026-11-08T17:49:00+09:00"
        with self.assertRaisesRegex(ValueError, "before odds"):
            validate_probability_input(probs, snapshot())

    def test_fake_complete_flag_or_bad_quote_is_rejected(self):
        record = snapshot(full_3rentan=False)
        record["markets"]["3rentan"]["complete"] = True
        with self.assertRaisesRegex(ValueError, "integrity audit"):
            research_report(record, now=NOW)
        record = snapshot()
        record["markets"]["2shafuku"]["quotes"]["1-2"] = 0.2
        with self.assertRaisesRegex(ValueError, "integrity audit"):
            research_report(record, now=NOW)

    def test_historical_does_not_become_live(self):
        historical = datetime(2026, 11, 9, tzinfo=timezone.utc)
        d = research_report(snapshot(), probabilities=distribution(), now=historical)
        self.assertEqual(d["current_usability"], "HISTORICAL_DO_NOT_TREAT_AS_LIVE")
        self.assertFalse(d["alert_allowed"])
        self.assertEqual(d["status"], "SHADOW_ONLY_UNVALIDATED")

    def test_nine_car_7_markets_complete_and_priceable(self):
        record = snapshot(cars=9)
        # This is a synthetic fixture, so reuse the correct 7-car source URL as
        # a basic provenance string and same synthetic race ID.
        d = research_report(record, probabilities=distribution(cars=9), now=NOW)
        self.assertEqual(len(d["markets_structurally_complete"]), 7)
        self.assertIn("2wakufuku", d["markets_structurally_complete"])
        self.assertEqual(d["status"], "SHADOW_ONLY_UNVALIDATED")

    def test_reads_last_snapshot_in_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snap.jsonl"
            first = snapshot(full_3rentan=False)
            second = snapshot(full_3rentan=True)
            path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n",
                            encoding="utf-8")
            loaded = last_record(path)
            self.assertEqual(loaded["markets"]["3rentan"]["count"], 210)
            self.assertEqual(research_report(loaded, now=NOW)["status"],
                             "NOT_READY_NO_PROBABILITIES")


if __name__ == "__main__":
    unittest.main()

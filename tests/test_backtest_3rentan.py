"""Synthetic offline regression for point-in-time 3rentan settlement only."""
import csv
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import backtest_3rentan as m
from audit_odds_snapshot import expected_keys

RACE = "7520261108010001"
SOURCE = f"https://keirin.kdreams.jp/matsuyama/racedetail/{RACE}/?pageType=odds"
ODDS_T0 = "2026-11-08T17:31:00+09:00"
ODDS_T1 = "2026-11-08T18:01:00+09:00"
DEADLINE = "2026-11-08T20:35:00+09:00"


def snapshot(when=ODDS_T0, *, price=1000.0, page_update=None,
             include3=True, cars=7):
    names = ("3rentan", "2shatan", "3renpuku", "2shafuku", "wide")
    items = {}
    for name in names:
        if name == "3rentan" and not include3:
            continue
        keys = expected_keys(name, cars)
        items[name] = {
            "quotes": {key: price for key in keys},
            "count": len(keys), "expected_count": len(keys), "complete": True
        }
    return {
        "cars": cars, "source_url": SOURCE,
        "observed_at_jst": when,
        "deadline_at_jst": DEADLINE,
        "page_displayed_times_jst": [
            page_update or when
        ],
        "markets": items,
    }


def probabilities(*, generated="2026-11-08T17:25:00+09:00",
                  dominant="1-2-3", race=RACE):
    keys = expected_keys("3rentan", 7)
    prob = {key: 0.001 for key in keys}
    prob[dominant] = 1 - .001 * (len(keys) - 1)
    return {
        "schema": "keirin-top3-probabilities-v1", "race_id": race,
        "cars": 7, "model_id": "synthetic-test-only",
        "generated_at": generated, "values": prob,
    }


def settled_result(*, winner="1-2-3", payout="8000",
                   result_type="", matching_source=True):
    return {
        "race_id": "20261108_matsuyama_01",
        "date": "2026-11-08", "bet_type": "3rentan",
        "winning_combination": winner, "payout_per_100yen": payout,
        "winning_final_odds": str(int(payout)/100) if payout else "",
        "result_type": result_type,
        "source_urls": SOURCE if matching_source else "https://unmatched.example.com/",
    }


class BacktestTests(unittest.TestCase):
    def test_one_real_payout_not_quoted_odds(self):
        one = snapshot()
        result = m.historical_choice(one, probabilities())
        self.assertEqual(result["combo"], "1-2-3")
        self.assertAlmostEqual(result["quoted_odds"], 1000.0)

    def test_earliest_eligible_snapshot_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                Path("local_data/odds_snapshots").mkdir(parents=True)
                Path("local_data/models").mkdir(parents=True)
                odds = Path(f"local_data/odds_snapshots/{RACE}.jsonl")
                first, second = snapshot(price=500), snapshot(ODDS_T1, price=1000)
                odds.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n",
                                encoding="utf-8")
                Path(f"local_data/models/{RACE}_top3.json").write_text(
                    json.dumps(probabilities()), encoding="utf-8")
                results = Path("local_data/results.csv")
                with results.open("w", encoding="utf-8", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=list(settled_result()))
                    w.writeheader()
                    w.writerow(settled_result(payout="8000"))
                report = m.backtest(Path("local_data/odds_snapshots"),
                                    Path("local_data/models"), results)
                self.assertEqual(report["eligible_bet_count"], 1)
                self.assertEqual(report["hits"], 1)
                self.assertEqual(report["realized_return_ratio"], 80.0)
                self.assertEqual(report["net_profit_yen"], 7900)
                self.assertEqual(report["selections"][0]["quoted_preclose_odds"], 500)
                self.assertEqual(report["selections"][0]["settled_payout_yen"], 8000)
                self.assertFalse(report["alert_allowed"])
                self.assertFalse(report["bet_allowed"])
            finally:
                os.chdir(old_cwd)

    def test_prediction_generated_after_earlier_odds_cannot_select_it(self):
        start = snapshot()
        with self.assertRaisesRegex(ValueError, "before odds"):
            m.historical_choice(start, probabilities(
                generated="2026-11-08T17:40:00+09:00"))

    def test_cannot_settle_on_quoted_price(self):
        with self.assertRaisesRegex(ValueError, "payout"):
            m.settlement(settled_result(payout=""), 7)
        with self.assertRaisesRegex(ValueError, "disagree"):
            row = settled_result(payout="8000")
            row["winning_final_odds"] = "600"
            m.settlement(row, 7)

    def test_ties_refunds_and_cancellations_excluded(self):
        for kind in ("tie", "refund", "cancelled", "disqualified"):
            with self.assertRaisesRegex(ValueError, "unsettleable"):
                m.settlement(settled_result(result_type=kind), 7)

    def test_only_kdreams_exact_race_join(self):
        self.assertEqual(m.result_id(settled_result()), RACE)
        self.assertIsNone(m.result_id(settled_result(matching_source=False)))
        row = settled_result()
        row["race_id"] = "7520261108019999"
        self.assertIsNone(m.result_id(row))

    def test_stale_site_update_and_missing_market_rejected(self):
        with self.assertRaisesRegex(ValueError, "stale or unknown"):
            m.eligible_snapshot(snapshot(page_update="2026-11-08T17:00:00+09:00"),
                                probabilities())
        with self.assertRaisesRegex(ValueError, "complete 3rentan"):
            m.eligible_snapshot(snapshot(include3=False), probabilities())

    def test_missing_probability_file_reports_no_bets(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                Path("local_data/odds_snapshots").mkdir(parents=True)
                Path("local_data/models").mkdir(parents=True)
                Path(f"local_data/odds_snapshots/{RACE}.jsonl").write_text(
                    json.dumps(snapshot()), encoding="utf-8")
                results = Path("local_data/results.csv")
                with results.open("w", encoding="utf-8", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=list(settled_result()))
                    w.writeheader()
                    w.writerow(settled_result())
                report = m.backtest(Path("local_data/odds_snapshots"),
                                    Path("local_data/models"), results)
                self.assertEqual(report["eligible_bet_count"], 0)
                self.assertEqual(report["rejected_counts"]["NO_POINT_IN_TIME_MODEL_PROBABILITIES"], 1)
                self.assertEqual(report["status"], "NOT_READY_NO_SETTLEABLE_POINT_IN_TIME_BETS")
            finally:
                os.chdir(old_cwd)

    def test_threshold_can_reject_all(self):
        self.assertIsNone(m.historical_choice(snapshot(price=1),
                                               probabilities(), threshold=1.3))

    def test_output_paths_are_private(self):
        for value in (".env", "../file", "/tmp/m.txt", "local_data/../.env"):
            with self.assertRaises(ValueError):
                m._local(value)

    def test_win_and_loss_payout_units_correct(self):
        result = settled_result(winner="2-1-3")
        winner, payout, day = m.settlement(result, 7)
        self.assertEqual((winner, payout, day.isoformat()), ("2-1-3", 8000, "2026-11-08"))
        self.assertNotEqual(m.historical_choice(snapshot(), probabilities())["combo"], winner)


if __name__ == "__main__":
    unittest.main()

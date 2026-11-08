"""Offline validation of seven bet types and input guards; no API calls."""
import math
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "paper_trading"))
from all_markets import (
    MARKETS,
    demo,
    evaluate_offered_odds,
    from_joint_finish_time_samples,
    plackett_luce_demo,
    project_all_markets,
    shadow_report,
    validate_top3,
)

class AllMarketsTests(unittest.TestCase):
    def test_nine_car_uniform_probabilities(self):
        cars = list(range(1, 10))
        dist = plackett_luce_demo(cars, {c: 1 for c in cars})
        self.assertEqual(len(dist), 504)
        p = project_all_markets(cars, dist)
        self.assertEqual(set(p), set(MARKETS))
        for market in MARKETS:
            self.assertAlmostEqual(sum(p[market].values()), 3 if market == "wide" else 1, places=10)
        self.assertAlmostEqual(p["3rentan"]["1-2-3"], 1 / 504)
        self.assertAlmostEqual(p["3renpuku"]["1-2-3"], 1 / 84)
        self.assertAlmostEqual(p["2shatan"]["1-2"], 1 / 72)
        self.assertAlmostEqual(p["2shafuku"]["1-2"], 1 / 36)
        self.assertAlmostEqual(p["wide"]["1-2"], 3 / 36)
        # Frame 4 contains cars 4 and 5: a same-frame wager is possible.
        self.assertAlmostEqual(p["2wakutan"]["4-4"], 2 / 72)
        self.assertAlmostEqual(p["2wakufuku"]["4-4"], 2 / 72)
        self.assertEqual(len(p["2wakutan"]), 33)
        self.assertEqual(len(p["2wakufuku"]), 18)

    def test_seven_car_size_and_wide(self):
        cars = list(range(1, 8))
        dist = plackett_luce_demo(cars, {c: 1 for c in cars})
        markets = project_all_markets(cars, dist)
        self.assertEqual(len(dist), 210)
        self.assertEqual(len(markets["3renpuku"]), 35)
        self.assertEqual(len(markets["2shatan"]), 42)
        self.assertEqual(len(markets["2shafuku"]), 21)
        self.assertAlmostEqual(markets["wide"]["1-2"], 1 / 7)
        self.assertIn("6-6", markets["2wakutan"])

    def test_joint_finish_time_samples(self):
        cars = list(range(1, 8))
        rows = [[1, 2, 3, 4, 5, 6, 7], [2, 1, 3, 4, 5, 6, 7]]
        projected = project_all_markets(cars, from_joint_finish_time_samples(cars, rows))
        self.assertAlmostEqual(projected["3rentan"]["1-2-3"], 0.5)
        self.assertAlmostEqual(projected["3rentan"]["2-1-3"], 0.5)
        self.assertAlmostEqual(projected["3renpuku"]["1-2-3"], 1.0)
        self.assertAlmostEqual(projected["wide"]["1-2"], 1.0)

    def test_range_uses_lower_odds_and_stress(self):
        p = project_all_markets(
            list(range(1, 8)),
            {"1-2-3": 1.0},
        )
        priced = {"wide": {"1-2": {"min": 1.2, "max": 8.0}}}
        self.assertEqual(evaluate_offered_odds(p, priced), [])
        priced["wide"]["1-2"]["min"] = 2.0
        selected = evaluate_offered_odds(p, priced, threshold=1.30, stress=0.75)
        self.assertEqual(selected[0]["odds_used"], 2.0)
        self.assertAlmostEqual(selected[0]["stressed_return"], 1.5)
        self.assertAlmostEqual(selected[0]["stressed_profit_percent"], 50.0)

    def test_bad_probability_inputs_fail_closed(self):
        cars = list(range(1, 8))
        for bad in [
            {"1-2-3": 0.7},
            {"1-2-3": -1.0, "2-1-3": 2.0},
            {"1-1-3": 1.0},
            {"1-2-8": 1.0},
            {"01-2-3": 1.0},
            {"1-2-3": math.nan},
        ]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_top3(cars, bad)
        with self.assertRaises(ValueError):
            from_joint_finish_time_samples(cars, [[1, 1, 3, 4, 5, 6, 7]])

    def test_bad_odds_and_unknown_ticket_rejected(self):
        probs = project_all_markets(list(range(1, 8)), {"1-2-3": 1.0})
        for odds in [
            {"2shafuku": {"2-1": 10.0}},
            {"3rentan": {"1-2-3": float("nan")}},
            {"wide": {"1-2": {"min": 5, "max": 4}}},
            {"unlisted_market": {"1-2": 20}},
            {"2shatan": {"1-2": 0.5}},
        ]:
            with self.subTest(odds=odds), self.assertRaises(ValueError):
                evaluate_offered_odds(probs, odds)

    def test_shadow_report_never_authorizes_alert(self):
        now = datetime(2026, 11, 8, 13, tzinfo=timezone.utc)
        payload = {
            "race_id": "synthetic_001",
            "cars": list(range(1, 8)),
            "probability_input": {
                "type": "joint_finish_time_samples",
                "samples": [[1, 2, 3, 4, 5, 6, 7]],
            },
            "offered_odds": {"3rentan": {"1-2-3": 10}},
            "odds_source_url": "https://example.org/not-real",
            "odds_observed_at": (now - timedelta(minutes=1)).isoformat(),
            "deadline_at": (now + timedelta(minutes=40)).isoformat(),
        }
        out = shadow_report(payload, now=now)
        self.assertFalse(out["alert_allowed"])
        self.assertEqual(out["freshness"], "recent_preclose_observation")
        self.assertEqual(out["qualifying_count"], 1)
        self.assertEqual(out["candidates"][0]["market"], "3rentan")
        self.assertEqual(out["status"], "SHADOW_ONLY_UNVALIDATED")

    def test_demo_explicitly_synthetic(self):
        result = demo()
        self.assertFalse(result["alert_allowed"])
        self.assertEqual(result["status"], "SYNTHETIC_DEMO_ONLY")
        self.assertAlmostEqual(result["projection_probability_sums"]["wide"], 3)


if __name__ == "__main__":
    unittest.main()

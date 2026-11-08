"""Deterministic, offline tests for the research-only top-three model."""
import csv
import itertools
import json
import math
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import train_baseline as m
from pre_race_readiness import COLUMNS


def synthetic_dataset(days=20, per_day=6, cars=7):
    start = date(2026, 9, 1)
    races = []
    for d in range(days):
        for index in range(per_day):
            # A rotating car order prevents simple car-number memorization.
            shifted = list(range(1, cars + 1))
            offset = (d * per_day + index) % cars
            rotated = shifted[offset:] + shifted[:offset]
            ratings = {car: 80 + (cars - rank) * 2 for rank, car in enumerate(rotated)}
            features = {car: (
                ratings[car], (cars - rank) / (2 * cars),
                (cars - rank) / cars, (cars - rank + .1) / cars,
                float(rank + 1), float(24 + car)
            ) for rank, car in enumerate(rotated)}
            races.append({"race_id": f"SYNTH_{d}_{index}",
                          "date": (start + timedelta(days=d)).isoformat(),
                          "features": features,
                          "top3": tuple(rotated[:3])})
    return races


class TestBaseline(unittest.TestCase):
    def test_temporal_split_by_complete_day(self):
        train, val, test = m.time_split(synthetic_dataset())
        all_sets = [{r["date"] for r in group} for group in (train, val, test)]
        self.assertFalse(all_sets[0] & all_sets[1])
        self.assertFalse(all_sets[0] & all_sets[2])
        self.assertFalse(all_sets[1] & all_sets[2])
        self.assertTrue(max(all_sets[0]) < min(all_sets[1]))
        self.assertTrue(max(all_sets[1]) < min(all_sets[2]))

    def test_analytic_gradient_matches_numeric(self):
        race = synthetic_dataset(days=1, per_day=1)[0]
        mean, scale = m.fit_scaler([race])
        weights = [0.05, -.03, 0.02, -.01, .04, 0.01]
        _, grad = m.race_nll_gradient(race, weights, mean, scale)
        eps = 1e-6
        for j in range(len(weights)):
            plus, minus = weights.copy(), weights.copy()
            plus[j] += eps
            minus[j] -= eps
            fp = m.race_nll_gradient(race, plus, mean, scale)[0]
            fm = m.race_nll_gradient(race, minus, mean, scale)[0]
            self.assertAlmostEqual(grad[j], (fp - fm) / (2 * eps), places=5)

    def test_data_gate_prevents_model_output(self):
        races = synthetic_dataset(days=10, per_day=2)
        result = m.research_model(races)
        self.assertEqual(result["status"], "NOT_READY")
        self.assertFalse(result["training_allowed"])

    def test_train_evaluate_predict_no_alert(self):
        races = synthetic_dataset()
        report = m.research_model(races, min_races=100, min_days=20)
        self.assertEqual(report["status"], "RESEARCH_BASELINE_EVALUATED")
        self.assertFalse(report["model_verified"])
        self.assertFalse(report["alert_allowed"])
        self.assertGreater(report["test_uniform_top3_nll"],
                           report["test_top3_nll"])
        prediction = m.predict_joint_top3(report, races[-1]["features"])
        self.assertFalse(prediction["alert_allowed"])
        probs = prediction["market_probabilities"]
        self.assertAlmostEqual(sum(probs["3rentan"].values()), 1.0)
        self.assertAlmostEqual(sum(probs["wide"].values()), 3.0)
        self.assertEqual(len(prediction["joint_top3"]), 210)

    def test_rider_model_supports_nine(self):
        races = synthetic_dataset(days=4, per_day=2, cars=9)
        train = m.fit_plackett_luce(races[:6], iterations=25)
        constructed = dict(status="RESEARCH_BASELINE_EVALUATED",
                           weights=train["weights"], scaler_means=train["mean"],
                           scaler_scales=train["scale"],
                           calibration_temperature=1.0)
        prediction = m.predict_joint_top3(constructed, races[-1]["features"])
        self.assertEqual(len(prediction["joint_top3"]), 504)
        self.assertAlmostEqual(sum(prediction["market_probabilities"]["wide"].values()), 3.0)

    def test_real_csv_join_requires_provenance_and_before_close_ingestion(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            feature_file = root / "features.csv"
            result_file = root / "result.csv"
            rows = []
            for car in range(1, 8):
                rows.append({
                    "race_id": "20261108_TEST_01", "race_date": "2026-11-08",
                    "car_no": str(car),
                    "observed_at_jst": "2026-11-08T12:00:00+09:00",
                    "deadline_at_jst": "2026-11-08T12:20:00+09:00",
                    "start_at_jst": "2026-11-08T12:25:00+09:00",
                    "source_url": "https://example.org/data", "rights_status": "permitted",
                    "line_id": str(car), "rating": "90",
                    "recent_win_rate": ".2", "recent_top2_rate": ".4",
                    "recent_top3_rate": ".6", "line_position": "1", "age": "25",
                    "ingested_at_jst": "2026-11-08T12:05:00+09:00",
                })
            with feature_file.open("w", encoding="utf-8", newline="") as out:
                writer = csv.DictWriter(out, fieldnames=tuple(COLUMNS) + ("ingested_at_jst",))
                writer.writeheader()
                writer.writerows(rows)
            with result_file.open("w", encoding="utf-8", newline="") as out:
                writer = csv.DictWriter(out, fieldnames=("race_id", "winning_combination"))
                writer.writeheader()
                writer.writerow({"race_id": "20261108_TEST_01", "winning_combination": "1-2-3"})
            accepted, problems = m.read_training_rows(feature_file, result_file)
            self.assertEqual(len(accepted), 1)
            self.assertFalse(problems)
            rows[0]["ingested_at_jst"] = "2026-11-08T12:21:00+09:00"
            with feature_file.open("w", encoding="utf-8", newline="") as out:
                writer = csv.DictWriter(out, fieldnames=tuple(COLUMNS) + ("ingested_at_jst",))
                writer.writeheader()
                writer.writerows(rows)
            rejected, problems = m.read_training_rows(feature_file, result_file)
            self.assertFalse(rejected)
            self.assertTrue(problems)

    def test_missing_files_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            races, reasons = m.read_training_rows(root/"missing.csv", root/"results.csv")
            self.assertEqual(races, [])
            self.assertEqual(reasons, {"missing_inputs": 1})


if __name__ == "__main__":
    unittest.main()

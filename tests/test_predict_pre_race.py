"""Offline no-fake-feature regression checks for forward top-three model export."""
import csv
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import predict_pre_race as m
from pre_race_readiness import COLUMNS

RACE_ID = "7520261108010001"
NOW = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)


def evaluated_model():
    return {
        "status": "RESEARCH_BASELINE_EVALUATED",
        "model_verified": False, "alert_allowed": False,
        "features": list(m.FEATURES),
        "weights": [0.01, .02, .02, -.04, -.05, .01],
        "scaler_means": [90, .1, .3, .4, 1.5, 32],
        "scaler_scales": [5, .1, .2, .3, .7, 9],
        "calibration_temperature": 1.5,
        "data_ranges": {
            "train": {"races": 400, "days": 22,
                      "from": "2026-09-01", "to": "2026-09-22"},
            "validation": {"races": 50, "days": 4,
                           "from": "2026-09-23", "to": "2026-09-26"},
            "test": {"races": 50, "days": 4,
                     "from": "2026-09-27", "to": "2026-09-30"},
        },
        "beats_uniform_baseline": True,
        "test_top3_nll": 4.9, "test_uniform_top3_nll": 5.347,
    }


def rows(cars=7):
    data = []
    for car in range(1, cars+1):
        data.append({
            "race_id": RACE_ID, "race_date": "2026-11-08",
            "car_no": str(car),
            "observed_at_jst": "2026-11-08T18:00:00+09:00",
            "deadline_at_jst": "2026-11-08T20:35:00+09:00",
            "start_at_jst": "2026-11-08T20:40:00+09:00",
            "ingested_at_jst": "2026-11-08T18:01:00+09:00",
            "source_url": "https://example.org/licensed-data",
            "rights_status": "self_recorded",
            "line_id": str(car), "line_position": "1",
            "rating": str(80 + car),
            "recent_win_rate": ".1", "recent_top2_rate": ".3",
            "recent_top3_rate": ".5", "age": "32",
        })
    return data


def save_csv(path, entries):
    with Path(path).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS) + ["ingested_at_jst"])
        writer.writeheader()
        writer.writerows(entries)


class ForwardPredictionTests(unittest.TestCase):
    def test_full_research_distribution_and_schema_without_external_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "features.csv"
            save_csv(csv_path, rows())
            features = m.forward_rows(csv_path, RACE_ID, now=NOW)
            d = m.predict_forward(evaluated_model(), features, now=NOW)
            self.assertEqual(d["schema"], "keirin-top3-probabilities-v1")
            self.assertEqual(len(d["values"]), 210)
            self.assertAlmostEqual(sum(d["values"].values()), 1.0)
            self.assertEqual(d["race_id"], RACE_ID)
            self.assertFalse(d["model_verified"])
            self.assertFalse(d["alert_allowed"])
            self.assertFalse(d["bet_allowed"])
            self.assertIn("unvalidated", d["model_id"])

    def test_missing_real_pre_race_features_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "not found"):
                m.forward_rows(Path(tmp) / "missing.csv", RACE_ID, now=NOW)

    def test_untested_model_or_insufficient_history_is_rejected(self):
        features = {"race_date": "2026-11-08"}
        model = evaluated_model()
        model["status"] = "NOT_READY"
        with self.assertRaises(ValueError):
            m.checked_model(model, race_date=features["race_date"])
        model = evaluated_model()
        model["data_ranges"]["train"]["races"] = 100
        with self.assertRaisesRegex(ValueError, "insufficient"):
            m.checked_model(model, race_date=features["race_date"])

    def test_no_training_on_same_day_or_future_results(self):
        model = evaluated_model()
        model["data_ranges"]["test"]["to"] = "2026-11-08"
        with self.assertRaisesRegex(ValueError, "overlaps target"):
            m.checked_model(model, race_date="2026-11-08")

    def test_model_must_beat_uniform_baseline(self):
        model = evaluated_model()
        model["beats_uniform_baseline"] = False
        with self.assertRaisesRegex(ValueError, "did not beat"):
            m.checked_model(model, race_date="2026-11-08")
        model = evaluated_model()
        model["weights"] = [float("nan")] + model["weights"][1:]
        with self.assertRaisesRegex(ValueError, "invalid numeric"):
            m.checked_model(model, race_date="2026-11-08")

    def test_cannot_use_late_ingested_features(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "features.csv"
            entries = rows()
            entries[0]["ingested_at_jst"] = "2026-11-08T20:36:00+09:00"
            save_csv(csv_path, entries)
            with self.assertRaisesRegex(ValueError, "mixed race metadata"):
                m.forward_rows(csv_path, RACE_ID, now=NOW)
            entries = rows()
            for row in entries:
                row["ingested_at_jst"] = "2026-11-08T20:36:00+09:00"
            save_csv(csv_path, entries)
            with self.assertRaisesRegex(ValueError, "late or unprovable"):
                m.forward_rows(csv_path, RACE_ID, now=NOW)

    def test_missing_cars_or_unlicensed_rows_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "features.csv"
            save_csv(csv_path, rows()[:-1])
            with self.assertRaises(ValueError):
                m.forward_rows(csv_path, RACE_ID, now=NOW)
            entries = rows()
            entries[0]["rights_status"] = "unknown"
            save_csv(csv_path, entries)
            with self.assertRaisesRegex(ValueError, "rights status"):
                m.forward_rows(csv_path, RACE_ID, now=NOW)

    def test_cannot_generate_inside_last_ten_minutes(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "features.csv"
            save_csv(csv_path, rows())
            with self.assertRaisesRegex(ValueError, "late or unprovable"):
                m.forward_rows(
                    csv_path, RACE_ID,
                    now=datetime(2026, 11, 8, 11, 28, tzinfo=timezone.utc)
                )

    def test_prediction_output_path_must_be_local(self):
        for path in ("/tmp/model.json", ".env", "../model.json",
                     "local_data/../model.json"):
            with self.assertRaises(ValueError):
                m.safe_output(path)

    def test_nine_car_distribution_full_504(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "features.csv"
            save_csv(csv_path, rows(cars=9))
            feats = m.forward_rows(csv_path, RACE_ID, now=NOW)
            d = m.predict_forward(evaluated_model(), feats, now=NOW)
            self.assertEqual(len(d["values"]), 504)
            self.assertAlmostEqual(sum(d["values"].values()), 1.0)


if __name__ == "__main__":
    unittest.main()

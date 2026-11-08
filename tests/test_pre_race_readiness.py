"""Synthetic offline tests for training data integrity guards."""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import pre_race_readiness as a


def entries(race_id="20261108_TEST_01"):
    return [{
        "race_id": race_id,
        "race_date": "2026-11-08",
        "car_no": str(c),
        "observed_at_jst": "2026-11-08T12:00:00+09:00",
        "deadline_at_jst": "2026-11-08T12:20:00+09:00",
        "start_at_jst": "2026-11-08T12:25:00+09:00",
        "source_url": "https://example.org/permissioned",
        "rights_status": "permitted",
        "line_id": str(c),
        "rating": "90.0",
        "recent_win_rate": "0.2",
        "recent_top2_rate": "0.4",
        "recent_top3_rate": "0.6",
        "line_position": "1",
        "age": "25",
    } for c in range(1, 8)]


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class TrainingReadinessTests(unittest.TestCase):
    def test_valid_race_join_with_result(self):
        date = a.validate_race(entries(), {"20261108_TEST_01": {(1, 2, 3)}})
        self.assertEqual(date, "2026-11-08")

    def test_future_observation_rejected(self):
        rows = entries()
        rows[0]["observed_at_jst"] = "2026-11-08T12:21:00+09:00"
        with self.assertRaisesRegex(ValueError, "leaked"):
            a.validate_race(rows, {"20261108_TEST_01": {(1, 2, 3)}})

    def test_missing_and_duplicate_car_rejected(self):
        rows = entries()
        rows[-1]["car_no"] = "1"
        with self.assertRaisesRegex(ValueError, "duplicate car"):
            a.validate_race(rows, {"20261108_TEST_01": {(1, 2, 3)}})

    def test_refuse_result_tie(self):
        with self.assertRaisesRegex(ValueError, "multiple winning outcomes"):
            a.validate_race(entries(), {"20261108_TEST_01": {(1, 2, 3), (2, 1, 3)}})

    def test_rights_status_required(self):
        rows = entries()
        rows[0]["rights_status"] = "permission_unverified"
        with self.assertRaisesRegex(ValueError, "rights status"):
            a.validate_race(rows, {"20261108_TEST_01": {(1, 2, 3)}})

    def test_missing_csv_returns_research_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            report = a.audit(base / "missing.csv", base / "results.csv")
        self.assertFalse(report["training_allowed"])
        self.assertEqual(report["status"], "NOT_READY")

    def test_realistic_csv_rejects_below_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            feature_file = base / "features.csv"
            result_file = base / "results.csv"
            write_csv(feature_file, entries(), a.COLUMNS)
            write_csv(result_file, [{
                "race_id": "20261108_TEST_01",
                "winning_combination": "1-2-3",
            }], ("race_id", "winning_combination"))
            report = a.audit(feature_file, result_file)
        self.assertEqual(report["eligible_races"], 1)
        self.assertEqual(report["distinct_days"], 1)
        self.assertEqual(report["rejected_races"], 0)
        self.assertFalse(report["training_allowed"])
        self.assertEqual(report["status"], "NOT_READY")

    def test_row_with_unusable_result_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ff = base / "features.csv"
            rf = base / "results.csv"
            write_csv(ff, entries(), a.COLUMNS)
            write_csv(rf, [{
                "race_id": "20261108_TEST_01",
                "winning_combination": "1-2-3",
            }, {
                "race_id": "20261108_TEST_01",
                "winning_combination": "2-1-3",
            }], ("race_id", "winning_combination"))
            report = a.audit(ff, rf)
        self.assertEqual(report["eligible_races"], 0)
        self.assertEqual(report["rejected_races"], 1)

    def test_result_string_parser(self):
        self.assertEqual(a.ordered_finish("1-2-3"), (1, 2, 3))
        for item in ("1-1-2", "01-2-3-4", "a-1-2", ""):
            self.assertIsNone(a.ordered_finish(item) if item != "01-2-3-4" else a.ordered_finish(item))


if __name__ == "__main__":
    unittest.main()

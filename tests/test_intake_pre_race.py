"""Offline tests: user-supplied pre-close features, never a prediction."""
import csv
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import intake_pre_race as m
from pre_race_readiness import COLUMNS

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 8, 12, 5, tzinfo=JST)


def riders(race="20261108_TEST_01", size=7):
    out = []
    for car in range(1, size+1):
        out.append({
            "race_id": race,
            "race_date": "2026-11-08",
            "car_no": str(car),
            "observed_at_jst": "2026-11-08T12:00:00+09:00",
            "deadline_at_jst": "2026-11-08T12:25:00+09:00",
            "start_at_jst": "2026-11-08T12:30:00+09:00",
            "source_url": "https://example.org/permissioned",
            "rights_status": "permitted",
            "line_id": f"L{car}",
            "rating": "90",
            "recent_win_rate": "0.2",
            "recent_top2_rate": "0.4",
            "recent_top3_rate": "0.6",
            "line_position": "1",
            "age": "26",
        })
    return out


def write(path, data):
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(data)


class IntakeTests(unittest.TestCase):
    def test_seven_rider_archive_and_idempotent_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, archive = root/"input.csv", root/"out"/"features.csv"
            write(source, riders())
            result = m.intake(source, archive, now=NOW)
            self.assertEqual(result["races_added"], 1)
            self.assertEqual(result["rider_rows_added"], 7)
            self.assertFalse(result["prediction_allowed"])
            self.assertFalse(result["alert_allowed"])
            text = archive.read_text(encoding="utf-8")
            self.assertIn("ingested_at_jst", text)
            self.assertIn(NOW.isoformat(), text)
            before = archive.read_bytes()
            with self.assertRaisesRegex(ValueError, "already archived"):
                m.intake(source, archive, now=NOW)
            self.assertEqual(before, archive.read_bytes())

    def test_multiple_races_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, archive = root/"input.csv", root/"archive.csv"
            write(source, riders() + riders("20261108_TEST_02", 9))
            result = m.intake(source, archive, now=NOW)
            self.assertEqual(result["races_added"], 2)
            self.assertEqual(result["rider_rows_added"], 16)

    def test_reject_after_deadline_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, archive = root/"input.csv", root/"archive.csv"
            write(source, riders())
            with self.assertRaisesRegex(ValueError, "already closed"):
                m.intake(source, archive, now=NOW+timedelta(hours=1))
            self.assertFalse(archive.exists())

    def test_reject_future_observation(self):
        rows = riders()
        rows[0]["observed_at_jst"] = "2026-11-08T12:09:00+09:00"
        with self.assertRaisesRegex(ValueError, "in the future"):
            m.validate_rows(rows, now=NOW)

    def test_reject_missing_car(self):
        with self.assertRaisesRegex(ValueError, "exactly 7, 8 or 9"):
            m.validate_rows(riders()[:-1], now=NOW)

    def test_reject_duplicate_car(self):
        rows = riders()
        rows[-1]["car_no"] = "1"
        with self.assertRaisesRegex(ValueError, "duplicate rider"):
            m.validate_rows(rows, now=NOW)

    def test_reject_unverified_rights(self):
        rows = riders()
        rows[0]["rights_status"] = "permission_unverified"
        with self.assertRaisesRegex(ValueError, "rights status"):
            m.validate_rows(rows, now=NOW)

    def test_reject_inconsistent_rates(self):
        rows = riders()
        rows[0]["recent_win_rate"] = "0.9"
        with self.assertRaisesRegex(ValueError, "rates must satisfy"):
            m.validate_rows(rows, now=NOW)

    def test_template_has_no_fake_data_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"nested"/"template.csv"
            self.assertEqual(m.create_template(path), path)
            with path.open(encoding="utf-8", newline="") as fh:
                self.assertEqual(list(csv.DictReader(fh)), [])
            with self.assertRaisesRegex(ValueError, "exists"):
                m.create_template(path)


if __name__ == "__main__":
    unittest.main()

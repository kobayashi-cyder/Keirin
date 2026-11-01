import csv
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import coverage_report as coverage


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


class CoverageTests(unittest.TestCase):
    def test_contiguous_100_with_unique_combinations(self):
        combinations = list(itertools.permutations("1234567", 3))[:100]
        rows = {i + 1: ("-".join(x), 10.0 + i) for i, x in enumerate(combinations)}
        self.assertTrue(coverage.ranks_complete(rows, 50))
        self.assertTrue(coverage.ranks_complete(rows, 100))
        del rows[73]
        self.assertTrue(coverage.ranks_complete(rows, 50))
        self.assertFalse(coverage.ranks_complete(rows, 100))

    def test_dead_heat_counts_once_and_unknown_schedule_stays_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            write_rows(data / "test_results.csv", [
                {"race_id": "20261031_test_01", "winning_combination": "1-2-3",
                 "payout_per_100yen": "570", "winning_final_odds": "5.7",
                 "result_type": "dead_heat_first_place"},
                {"race_id": "20261031_test_01", "winning_combination": "2-1-3",
                 "payout_per_100yen": "880", "winning_final_odds": "8.8",
                 "result_type": "dead_heat_first_place"},
            ])
            write_rows(data / "venue_day_coverage.csv", [
                {"date": "2026-10-31", "venue_slug": "test", "listed_races": "2"}
            ])
            write_rows(data / "top50.csv", [
                {"race_id": "20261031_test_01", "rank": "1", "combination": "1-2-3",
                 "final_odds": "5.7"},
            ])
            with patch.object(coverage, "ROOT", root), patch.object(coverage, "DATA", data):
                coverage.report()
            stats = json.loads((data / "coverage_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(stats["all_result_races"], 1)
            self.assertEqual(stats["all_winning_payout_rows"], 2)
            self.assertEqual(stats["known_missing_result_races"], 1)
            self.assertEqual(stats["top50_races"], 0)
            date_index = list(csv.DictReader((data / "date_index.csv").open(encoding="utf-8")))
            self.assertEqual(date_index[0]["schedule_status"], "not_indexed")
            oct31 = [x for x in date_index if x["date"] == "2026-10-31"][0]
            self.assertEqual(oct31["listed_races"], "2")

    def test_conflicting_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            write_rows(data / "a_results.csv", [
                {"race_id": "20261031_test_01", "winning_combination": "1-2-3",
                 "payout_per_100yen": "570", "winning_final_odds": "5.7"}
            ])
            write_rows(data / "b_results.csv", [
                {"race_id": "20261031_test_01", "winning_combination": "1-2-3",
                 "payout_per_100yen": "590", "winning_final_odds": "5.9"}
            ])
            with patch.object(coverage, "DATA", data):
                with self.assertRaises(ValueError):
                    coverage.load_results()


if __name__ == "__main__":
    unittest.main()

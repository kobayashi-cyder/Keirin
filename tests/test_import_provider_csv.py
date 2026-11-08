"""Offline tests for licensed local CSV adapter; no web access or credentials."""
import csv
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import import_provider_csv as adapter
from pre_race_readiness import audit

NOW = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
URL = "https://licensed-provider.example.com/export/racecards.csv"
RACE = "7520261108010001"
DEADLINE = "2026-11-08T20:35:00+09:00"
START = "2026-11-08T20:40:00+09:00"


def provider_rows(cars=7, *, include_line=True, race=RACE):
    rows = []
    for car in range(1, cars + 1):
        values = {
            "raceId": race, "date": "2026-11-08", "banum": str(car),
            "score": str(69.5 + car), "age": str(25 + car),
            "win_rate": "20.0" if car == 1 else "10.0%",
            "place2_rate": "0.4",
            "place3_rate": "60.0%",
            "deadline": DEADLINE, "start_time": START,
        }
        if include_line:
            values["line"] = f"line_{car}"
            values["line_order"] = "1"
        rows.append(values)
    return rows


def write_csv(path, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class ProviderAdapterTests(unittest.TestCase):
    def test_aliases_lineup_and_mixed_percent_formats(self):
        rows = provider_rows(include_line=False)
        result = adapter.convert(
            list(rows[0]), rows, source_url=URL, rights_status="permitted",
            lineup="1-2-6/7/3-4-5", now=NOW,
        )
        self.assertEqual(len(result), 7)
        first = result[0]
        self.assertEqual(first["race_id"], RACE)
        self.assertEqual(first["rating"], "70.5")
        self.assertEqual(first["recent_win_rate"], "0.2")
        self.assertEqual(first["recent_top2_rate"], "0.4")
        self.assertEqual(first["recent_top3_rate"], "0.6")
        self.assertEqual(first["line_id"], "L1")
        self.assertEqual(first["line_position"], "1")
        self.assertEqual(result[5]["line_id"], "L1")
        self.assertEqual(result[5]["line_position"], "3")
        self.assertEqual(result[6]["line_id"], "L2")
        self.assertEqual(first["observed_at_jst"], "2026-11-08T19:00:00+09:00")

    def test_dry_run_does_not_write_and_commit_does(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = os.getcwd()
            os.chdir(tmp)
            try:
                folder = Path("local_data")
                folder.mkdir()
                source = folder / "provider.csv"
                archive = folder / "pre_race_features.csv"
                write_csv(source, provider_rows())
                preview = adapter.do_import(source, archive, now=NOW,
                                            source_url=URL, rights_status="permitted")
                self.assertEqual(preview["status"], "DRY_RUN_VALIDATED_NOT_SAVED")
                self.assertFalse(archive.exists())
                committed = adapter.do_import(
                    source, archive, now=NOW, commit=True,
                    source_url=URL, rights_status="permitted"
                )
                self.assertEqual(committed["races_added"], 1)
                self.assertTrue(archive.exists())
                with archive.open(encoding="utf-8", newline="") as fh:
                    saved = list(csv.DictReader(fh))
                self.assertEqual(len(saved), 7)
                self.assertEqual(saved[0]["ingested_at_jst"],
                                 "2026-11-08T19:00:00+09:00")
                self.assertEqual(
                    audit(archive, Path("local_data/not-present.csv"))["feature_rows"], 7)
                with self.assertRaisesRegex(ValueError, "already archived"):
                    adapter.do_import(source, archive, now=NOW, commit=True,
                                      source_url=URL, rights_status="permitted")
                with archive.open(encoding="utf-8") as fh:
                    self.assertEqual(sum(1 for _ in fh), 8)
            finally:
                os.chdir(before)

    def test_late_import_cannot_forge_past_observation(self):
        rows = provider_rows()
        converted = adapter.convert(
            list(rows[0]), rows, source_url=URL,
            rights_status="permitted",
            now=datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(ValueError, "already closed"):
            adapter.validate_rows(
                converted, now=datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
            )

    def test_missing_line_information_is_not_imputed(self):
        rows = provider_rows(include_line=False)
        normalized = adapter.convert(
            list(rows[0]), rows, source_url=URL,
            rights_status="permitted", now=NOW,
            lineup="1-2-6/7/3-4-5"
        )
        self.assertEqual(len(normalized), 7)
        with self.assertRaisesRegex(ValueError, "missing required provider field line_id"):
            adapter.convert(list(rows[0]), rows, source_url=URL,
                            rights_status="permitted", now=NOW)

    def test_lineup_rejects_missing_repeated_and_incorrect_cars(self):
        for value in ("1-2-6/7/3-4", "1-2-2/7/3-4-5-6",
                      "1-2-6/7/3-4-8", "not-a-lineup"):
            with self.assertRaises(ValueError):
                adapter.parse_lineup(value, set(range(1, 8)))

    def test_missing_rights_or_source_is_refused(self):
        rows = provider_rows()
        for options in ({"source_url": URL},
                        {"rights_status": "permitted"},
                        {}):
            with self.assertRaisesRegex(ValueError, "missing required provider field"):
                adapter.convert(list(rows[0]), rows, now=NOW, **options)

    def test_rate_conventions_and_monotonic_check(self):
        self.assertEqual(adapter.normalize_rate("45％", "win"), "0.45")
        self.assertEqual(adapter.normalize_rate("0.45", "win"), "0.45")
        self.assertEqual(adapter.normalize_rate("45", "win"), "0.45")
        for bad in ("101%", "-1", "nan", "hello", "", "inf"):
            with self.assertRaises(ValueError):
                adapter.normalize_rate(bad, "win")
        rows = provider_rows()
        rows[0]["place2_rate"] = "0.05"
        prepared = adapter.convert(
            list(rows[0]), rows, now=NOW, source_url=URL,
            rights_status="permitted"
        )
        with self.assertRaisesRegex(ValueError, "rates must satisfy"):
            adapter.validate_rows(prepared, now=NOW)

    def test_custom_mapping_for_nonstandard_provider_columns(self):
        rows = provider_rows()
        renamed = []
        for r in rows:
            r = dict(r)
            r["power_metric"] = r.pop("score")
            renamed.append(r)
        with self.assertRaisesRegex(ValueError, "missing required provider field rating"):
            adapter.convert(list(renamed[0]), renamed, now=NOW,
                            source_url=URL, rights_status="permitted")
        prepared = adapter.convert(
            list(renamed[0]), renamed, mapping={"rating": "power_metric"},
            now=NOW, source_url=URL, rights_status="permitted"
        )
        self.assertEqual(prepared[0]["rating"], "70.5")

    def test_multiple_races_with_line_columns_are_supported(self):
        rows = provider_rows() + provider_rows(race="7520261108010002")
        prepared = adapter.convert(
            list(rows[0]), rows, now=NOW,
            source_url=URL, rights_status="permitted"
        )
        self.assertEqual(len(prepared), 14)
        grouped = adapter.validate_rows(prepared, now=NOW)
        self.assertEqual(len(grouped), 2)
        with self.assertRaisesRegex(ValueError, "exactly one race"):
            adapter.convert(list(rows[0]), rows, now=NOW,
                            source_url=URL, rights_status="permitted",
                            lineup="1-2-6/7/3-4-5")

    def test_empty_csv_header_only_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = os.getcwd()
            os.chdir(tmp)
            try:
                p = Path("local_data/empty.csv")
                p.parent.mkdir()
                p.write_text("race_id,car_no,score\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "empty"):
                    adapter.csv_input(p)
            finally:
                os.chdir(before)

    def test_ambiguous_aliases_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            adapter.mapping_for_headers(
                ["race_id", "car_no", "banum"],
                {}
            )

    def test_paths_cannot_escape_local_data(self):
        for path in (".env", "../.env", "local_data/../.env", "/tmp/secret.csv"):
            with self.assertRaises(ValueError):
                adapter.safe_local_path(path)

    def test_existing_fields_disagree_with_overrides_rejected(self):
        rows = provider_rows()
        with self.assertRaisesRegex(ValueError, "metadata mismatch"):
            adapter.convert(
                list(rows[0]), rows, now=NOW, source_url=URL,
                rights_status="permitted", race_id="7520261108010099"
            )

    def test_malformed_provider_row_is_rejected(self):
        rows = provider_rows()
        rows[0][None] = ["extra_cell"]
        with self.assertRaisesRegex(ValueError, "malformed CSV"):
            adapter.convert(
                list(rows[0]), rows, now=NOW, source_url=URL,
                rights_status="permitted"
            )


if __name__ == "__main__":
    unittest.main()

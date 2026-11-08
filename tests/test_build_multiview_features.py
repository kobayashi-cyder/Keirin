"""Leakage and provenance tests for expanded historic/video tactical features."""
import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_multiview_features as m
from pre_race_readiness import COLUMNS, FEATURES
import train_baseline
import predict_pre_race

NOW = datetime.fromisoformat("2026-11-08T18:01:00+09:00")
RACE = "7520261108010001"
HTTPS = "https://example.org/authorized-private-racecards"


def base_rows():
    return [{
        "race_id": RACE, "race_date": "2026-11-08", "car_no": str(car),
        "observed_at_jst": "2026-11-08T18:00:00+09:00",
        "ingested_at_jst": "2026-11-08T18:01:00+09:00",
        "deadline_at_jst": "2026-11-08T20:35:00+09:00",
        "start_at_jst": "2026-11-08T20:40:00+09:00",
        "source_url": HTTPS, "rights_status": "permitted", "line_id": str(car),
        "rating": str(78+car), "recent_win_rate": ".1",
        "recent_top2_rate": ".25", "recent_top3_rate": ".5",
        "line_position": "1", "age": "30",
    } for car in range(1, 8)]


def context_rows():
    return [{
        "race_id": RACE, "car_no": str(car), "rider_id": f"R{car}",
        "venue": "matsuyama", "grade": "F2", "bank_length_m": "400",
        "wind_mps": "3.1", "observed_at_jst": "2026-11-08T17:58:00+09:00",
        "source_url": HTTPS, "rights_status": "permitted",
    } for car in range(1, 8)]


def event_rows(*, video=True):
    return [{
        "race_id": "earlier_race_1", "rider_id": f"R{car}",
        "race_at_jst": "2026-11-01T15:00:00+09:00",
        "available_at_jst": "2026-11-02T10:00:00+09:00",
        "venue": "matsuyama", "grade": "F2",
        "finish_position": str(car), "attack_count": "2" if video else "",
        "front_seconds": "40" if video else "",
        "late_gain_places": "1" if video else "",
        "line_cohesion": ".8" if video else "",
        "video_sha256": "a"*64 if video else "",
        "source_url": HTTPS, "rights_status": "permitted",
    } for car in range(1, 8)]


def write(path, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


class MultiViewTests(unittest.TestCase):
    def make_dataset(self, directory, *, event_override=None, context_override=None):
        folder = Path(directory) / "local_data"
        folder.mkdir(exist_ok=True)
        paths = [folder / "base.csv", folder / "context.csv", folder / "events.csv"]
        write(paths[0], base_rows())
        write(paths[1], context_override if context_override is not None else context_rows())
        write(paths[2], event_override if event_override is not None else event_rows())
        return [Path("local_data") / p.name for p in paths]

    def test_history_venue_video_tactics_contribute_fourteen_features(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                input_paths = self.make_dataset(tmp)
                rows, races = m.build(*input_paths, now=NOW)
                self.assertEqual((len(rows), races), (7, 1))
                self.assertEqual(len(m.EXTRA_FEATURE_NAMES), 14)
                first = rows[0]
                self.assertEqual(first["hist_races_180d"], "1")
                self.assertEqual(first["hist_video_races_180d"], "1")
                self.assertEqual(first["hist_attack_rate_180d"], "2")
                self.assertEqual(first["hist_front_seconds_180d"], "40")
                self.assertEqual(first["hist_same_venue_races_365d"], "1")
                self.assertEqual(first["context_bank_length_m"], "400")
                self.assertEqual(first["context_wind_mps"], "3.1")
                self.assertEqual(set(first), set(m.EXPORT_FIELDS))
            finally:
                os.chdir(old)

    def test_future_annotation_not_used_in_prior_race(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                events = event_rows()
                for row in events:
                    row["available_at_jst"] = "2026-11-09T10:00:00+09:00"
                files = self.make_dataset(tmp, event_override=events)
                with self.assertRaisesRegex(ValueError, "no eligible earlier history"):
                    m.build(*files, now=NOW)
            finally:
                os.chdir(old)

    def test_zero_video_coverage_does_not_mean_zero_attack(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                files = self.make_dataset(tmp, event_override=event_rows(video=False))
                rows, _ = m.build(*files, now=NOW)
                self.assertEqual(rows[0]["hist_video_races_180d"], "0")
                self.assertEqual(rows[0]["hist_races_180d"], "1")
                self.assertEqual(rows[0]["hist_attack_rate_180d"], "0")
            finally:
                os.chdir(old)

    def test_no_video_hash_with_fabricated_tactics_is_refused(self):
        event = event_rows(video=False)[0]
        event["attack_count"] = "0"
        with self.assertRaisesRegex(ValueError, "need a video hash"):
            m.validate_history(event)

    def test_invalid_context_fails_instead_of_guessing_wind(self):
        c = context_rows()[0]
        c["wind_mps"] = ""
        with self.assertRaisesRegex(ValueError, "missing numeric"):
            m.validate_context(c)

    def test_duplicate_historical_events_and_context_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                files = self.make_dataset(tmp, event_override=event_rows()+event_rows())
                with self.assertRaisesRegex(ValueError, "duplicate rider historical"):
                    m.build(*files, now=NOW)
                files = self.make_dataset(tmp, context_override=context_rows()+context_rows())
                with self.assertRaisesRegex(ValueError, "duplicate target"):
                    m.build(*files, now=NOW)
            finally:
                os.chdir(old)

    def test_context_observed_after_base_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                contexts = context_rows()
                contexts[0]["observed_at_jst"] = "2026-11-08T19:00:00+09:00"
                files = self.make_dataset(tmp, context_override=contexts)
                with self.assertRaisesRegex(ValueError, "target context was not known"):
                    m.build(*files, now=NOW)
            finally:
                os.chdir(old)

    def test_expanded_features_are_usable_by_training_and_inference(self):
        sample = [80.0, .1, .2, .3, 1., 32.] + list(range(14))
        dataset = [
            {"features": {
                car: tuple(v + (car*.1 if i == 0 else 0)
                           for i, v in enumerate(sample))
                for car in range(1, 8)
            }, "top3": (1, 2, 3)}
        ]
        fitted = train_baseline.fit_plackett_luce(dataset, iterations=2)
        self.assertEqual(len(fitted["weights"]), 20)
        self.assertEqual(len(fitted["mean"]), 20)
        self.assertEqual(len(fitted["scale"]), 20)
        self.assertIn(tuple(FEATURES) + m.EXTRA_FEATURE_NAMES,
                      predict_pre_race.SUPPORTED_FEATURE_SETS)

    def test_cli_templates_have_only_headers_and_never_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                from unittest.mock import patch
                with patch.object(sys, "argv", ["script", "--init-templates"]):
                    self.assertEqual(m.main(), 0)
                target = Path("local_data/history/target_context.csv")
                event = Path("local_data/history/rider_events.csv")
                self.assertEqual(len(target.read_text(encoding="utf-8").splitlines()), 1)
                self.assertEqual(len(event.read_text(encoding="utf-8").splitlines()), 1)
                with patch.object(sys, "argv", ["script", "--init-templates"]):
                    self.assertEqual(m.main(), 2)
            finally:
                os.chdir(old)

    def test_only_private_local_paths(self):
        for bad in (".env", "../outside.csv", "/tmp/c.csv", "local_data/../secret"):
            with self.assertRaises(ValueError):
                m.local(bad)


if __name__ == "__main__":
    unittest.main()

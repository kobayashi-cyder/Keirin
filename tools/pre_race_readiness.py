#!/usr/bin/env python3
"""Audit *pre-race* per-rider features before considering model training.

Pure standard library. Read-only, no scraping, no model claims, no betting.
Returns exit status 2 when pre-race inputs are missing/inadequate, not because
a software or HTTP error occurred. Never infer missing future features.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

FEATURES = (
    "rating", "recent_win_rate", "recent_top2_rate",
    "recent_top3_rate", "line_position", "age",
)
COLUMNS = (
    "race_id", "race_date", "car_no", "observed_at_jst",
    "deadline_at_jst", "start_at_jst", "source_url",
    "rights_status", "line_id",
) + FEATURES
ALLOWED_RIGHTS = {"permitted", "self_recorded"}
MIN_TRAIN_RACES = 500
MIN_DISTINCT_DAYS = 30


def time_with_zone(raw):
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp lacks timezone offset")
    return value


def https_source(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.hostname)


def ordered_finish(raw):
    """Return unambiguous official result label, or None for ties/missing."""
    if not isinstance(raw, str):
        return None
    parts = raw.split("-")
    if (len(parts) != 3 or
            any(not p.isascii() or not p.isdigit() for p in parts)):
        return None
    result = tuple(map(int, parts))
    return result if len(set(result)) == 3 else None


def index_results(path):
    outcomes = defaultdict(set)
    if not path.is_file():
        return outcomes
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not {"race_id", "winning_combination"} <= set(reader.fieldnames):
            raise ValueError("result file must have race_id and winning_combination")
        for row in reader:
            winner = ordered_finish(row.get("winning_combination", ""))
            if winner is not None:
                outcomes[row["race_id"]].add(winner)
    return outcomes


def _valid_row(row):
    if not row.get("race_id") or not row.get("race_date"):
        raise ValueError("missing race identity")
    car_no = int(row["car_no"])
    if not 1 <= car_no <= 9:
        raise ValueError("invalid car number")
    observed = time_with_zone(row["observed_at_jst"])
    deadline = time_with_zone(row["deadline_at_jst"])
    start = time_with_zone(row["start_at_jst"])
    if not observed < deadline <= start:
        raise ValueError("leaked or inconsistent observation timestamp")
    if observed.date().isoformat() > row["race_date"]:
        raise ValueError("observation after race date")
    if start.date().isoformat() != row["race_date"]:
        raise ValueError("race start date mismatch")
    if row["rights_status"] not in ALLOWED_RIGHTS:
        raise ValueError("rights status not eligible for research")
    if not https_source(row["source_url"]):
        raise ValueError("missing HTTPS provenance")
    if not row["line_id"]:
        raise ValueError("missing line")
    for field in FEATURES:
        val = float(row[field])
        if not math.isfinite(val):
            raise ValueError("nonfinite feature")
        if field.endswith("_rate") and not 0 <= val <= 1:
            raise ValueError("rate outside [0,1]")
        if field == "line_position" and (not val.is_integer() or val < 1 or val > 9):
            raise ValueError("invalid line position")
        if field == "age" and not 16 <= val <= 100:
            raise ValueError("invalid age")
    return car_no, observed, deadline, start


def validate_race(rows, outcomes):
    if len(rows) not in (7, 8, 9):
        raise ValueError("race must contain 7, 8 or 9 rider rows")
    seen = set()
    times = set()
    dates = set()
    lines = defaultdict(set)
    for row in rows:
        car, observed, deadline, start = _valid_row(row)
        if car in seen:
            raise ValueError("duplicate car number")
        seen.add(car)
        times.add((deadline, start))
        dates.add(row["race_date"])
        line = row["line_id"]
        position = int(float(row["line_position"]))
        if position in lines[line]:
            raise ValueError("duplicate position within line")
        lines[line].add(position)
    if seen != set(range(1, len(rows) + 1)):
        raise ValueError("missing or nonconsecutive cars")
    if len(times) != 1 or len(dates) != 1:
        raise ValueError("mixed race metadata")
    winners = outcomes.get(rows[0]["race_id"], set())
    if len(winners) != 1:
        raise ValueError("unavailable or multiple winning outcomes, e.g. tie")
    if not set(next(iter(winners))) <= seen:
        raise ValueError("result includes rider absent from entries")
    return rows[0]["race_date"]


def audit(feature_path, result_path, *, min_races=MIN_TRAIN_RACES, min_days=MIN_DISTINCT_DAYS):
    report = {
        "status": "NOT_READY", "training_allowed": False,
        "feature_path": str(feature_path),
        "result_path": str(result_path),
        "source": "local_files_only",
        "min_eligible_races": min_races,
        "min_distinct_days": min_days,
        "feature_rows": 0,
        "eligible_races": 0,
        "distinct_days": 0,
        "rejected_races": 0,
        "reasons": {},
        "note": "Coverage is a data gate, NOT predictive validity or reuse permission verification.",
    }
    if not feature_path.is_file():
        report["reasons"] = {"missing_pre_race_features_csv": 1}
        return report
    groups = defaultdict(list)
    errors = Counter()
    with feature_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not set(COLUMNS) <= set(reader.fieldnames):
            missing = sorted(set(COLUMNS) - set(reader.fieldnames or []))
            report["reasons"] = {"missing_feature_columns: " + ",".join(missing): 1}
            return report
        for row in reader:
            report["feature_rows"] += 1
            if not row.get("race_id"):
                errors["missing_race_id_row"] += 1
            else:
                groups[row["race_id"]].append(row)
    outcomes = index_results(result_path)
    eligible_dates = set()
    for race_id, rows in groups.items():
        try:
            date = validate_race(rows, outcomes)
        except (KeyError, ValueError, TypeError, OverflowError) as e:
            errors[str(e)] += 1
        else:
            report["eligible_races"] += 1
            eligible_dates.add(date)
    report["rejected_races"] = len(groups) - report["eligible_races"]
    report["distinct_days"] = len(eligible_dates)
    report["reasons"] = dict(sorted(errors.items()))
    if (report["eligible_races"] >= min_races and
            report["distinct_days"] >= min_days):
        report["status"] = "DATA_COVERAGE_GATE_PASSED"
        # Passing coverage alone must never allow real predictions or alerts.
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--features", type=Path, default=Path("data/pre_race_features.csv"))
    ap.add_argument("--results", type=Path, default=Path("exports/results_3rentan.csv"))
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    report = audit(args.features, args.results)
    result = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")
    print(result, end="")
    return 0 if report["status"] == "DATA_COVERAGE_GATE_PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

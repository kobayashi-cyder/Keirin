#!/usr/bin/env python3
"""Forward-only, research-grade joint top-three predictions from a trained model.

Requires a local historical research baseline trained on *actual licensed
pre-race features*, plus a separately ingested current race feature snapshot.
This program does not scrape, call OpenAI, post to Discord, claim profitability
or issue wagers. It refuses absent/unvalidated/late data instead of inventing
probabilities. A model that beats uniform still is NOT a validated betting AI.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pre_race_readiness import COLUMNS, FEATURES, _valid_row, time_with_zone
from train_baseline import predict_joint_top3

REQUIRED_RACES = 500
REQUIRED_DAYS = 30


def _at(raw):
    try:
        val = datetime.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid timestamp") from exc
    if val.tzinfo is None or val.utcoffset() is None:
        raise ValueError("timestamp must include offset")
    return val.astimezone(timezone.utc)


def _number_array(value):
    if not isinstance(value, list) or len(value) != len(FEATURES):
        raise ValueError("model parameter dimensions do not match features")
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError("model contains invalid numeric parameters")
    return value


def checked_model(model, *, race_date):
    """Fail closed on synthetic/minimal/incomplete/retroactive model artifacts."""
    if not isinstance(model, dict) or model.get("status") != "RESEARCH_BASELINE_EVALUATED":
        raise ValueError("no evaluated historical model available")
    if model.get("model_verified") is not False or model.get("alert_allowed") is not False:
        raise ValueError("model must explicitly remain unverified")
    if tuple(model.get("features", ())) != tuple(FEATURES):
        raise ValueError("model feature order mismatch")
    _number_array(model.get("weights"))
    _number_array(model.get("scaler_means"))
    scales = _number_array(model.get("scaler_scales"))
    if any(x <= 0 for x in scales):
        raise ValueError("model has invalid scaler")
    temp = model.get("calibration_temperature")
    if type(temp) not in (float, int) or not math.isfinite(temp) or temp <= 0:
        raise ValueError("invalid probability calibration temperature")
    ranges = model.get("data_ranges")
    if not isinstance(ranges, dict) or set(ranges) != {"train", "validation", "test"}:
        raise ValueError("missing chronological model report")
    total_races = 0
    days = 0
    previous_end = None
    for name in ("train", "validation", "test"):
        part = ranges[name]
        if (not isinstance(part, dict) or type(part.get("races")) is not int
                or type(part.get("days")) is not int
                or part["races"] < 1 or part["days"] < 1):
            raise ValueError("invalid training data partitions")
        first, last = part.get("from"), part.get("to")
        try:
            start_dt = datetime.strptime(first, "%Y-%m-%d").date()
            end_dt = datetime.strptime(last, "%Y-%m-%d").date()
            target_dt = datetime.strptime(race_date, "%Y-%m-%d").date()
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid model partition dates") from exc
        if start_dt > end_dt or (previous_end is not None and start_dt <= previous_end):
            raise ValueError("nonchronological model data partitions")
        previous_end = end_dt
        total_races += part["races"]
        days += part["days"]
        if name == "test" and end_dt >= target_dt:
            raise ValueError("historical model test set overlaps target race date")
    if total_races < REQUIRED_RACES or days < REQUIRED_DAYS:
        raise ValueError("historical model has insufficient real training coverage")
    nll = model.get("test_top3_nll")
    baseline = model.get("test_uniform_top3_nll")
    if (model.get("beats_uniform_baseline") is not True
            or type(nll) not in (float, int)
            or type(baseline) not in (float, int)
            or not math.isfinite(nll) or not math.isfinite(baseline)
            or not 0 <= nll < baseline):
        raise ValueError("historical model did not beat uniform test baseline")
    # A passing research model is NOT an independently validated edge.
    return model


def forward_rows(path, race_id, *, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware time required")
    now = now.astimezone(timezone.utc)
    if not re.fullmatch(r"\d{10,24}", race_id):
        raise ValueError("race ID must be a canonical numeric Kdreams ID")
    if not Path(path).is_file():
        raise ValueError("real pre-race feature CSV not found")
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or not (set(COLUMNS) | {"ingested_at_jst"}) <= set(reader.fieldnames):
            raise ValueError("pre-race features CSV lacks mandatory columns")
        rows = [row for row in reader if row.get("race_id") == race_id]
    if len(rows) not in (7, 8, 9):
        raise ValueError("target race lacks a complete 7/8/9 car pre-race snapshot")
    seen, times, ingested, observation_times, race_dates = set(), set(), set(), set(), set()
    lines = {}
    vectors = {}
    for row in rows:
        car, observed_at, deadline, start = _valid_row(row)
        if car in seen:
            raise ValueError("duplicated car")
        seen.add(car)
        times.add((_at(row["deadline_at_jst"]), _at(row["start_at_jst"])))
        ingested_at = _at(row["ingested_at_jst"])
        ingested.add(ingested_at)
        observation_times.add(_at(row["observed_at_jst"]))
        race_dates.add(row["race_date"])
        position = int(float(row["line_position"]))
        line = row["line_id"]
        lines.setdefault(line, set())
        if position in lines[line]:
            raise ValueError("duplicated line position")
        lines[line].add(position)
        vectors[car] = tuple(float(row[col]) for col in FEATURES)
    if seen != set(range(1, len(rows) + 1)):
        raise ValueError("nonconsecutive or missing car numbers")
    if len(times) != 1 or len(ingested) != 1 or len(race_dates) != 1:
        raise ValueError("mixed race metadata or feature snapshots")
    deadline, start = next(iter(times))
    ingested_at = next(iter(ingested))
    if (ingested_at >= deadline or ingested_at > now
            or any(observed > ingested_at for observed in observation_times)
            or now >= deadline - timedelta(minutes=10)):
        raise ValueError("late or unprovable pre-race feature observation")
    return {
        "race_id": race_id, "race_date": next(iter(race_dates)),
        "cars": len(rows), "deadline": deadline,
        "ingested_at": ingested_at, "vectors": vectors,
    }


def predict_forward(model, features, *, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("current time needs timezone offset")
    now = now.astimezone(timezone.utc)
    model = checked_model(model, race_date=features["race_date"])
    if now >= features["deadline"] - timedelta(minutes=10):
        raise ValueError("race deadline too close or passed")
    if features["ingested_at"] > now:
        raise ValueError("future-dated pre-race features")
    predicted = predict_joint_top3(model, features["vectors"])
    values = predicted["joint_top3"]
    if len(values) != features["cars"]*(features["cars"]-1)*(features["cars"]-2):
        raise ValueError("incomplete predicted rank distribution")
    fingerprint = hashlib.sha256(
        json.dumps({k: model[k] for k in (
            "weights", "scaler_means", "scaler_scales",
            "calibration_temperature", "data_ranges"
        )}, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()[:12]
    return {
        "schema": "keirin-top3-probabilities-v1",
        "race_id": features["race_id"],
        "cars": features["cars"],
        "model_id": "unvalidated-plackett-luce-" + fingerprint,
        "generated_at": now.isoformat(),
        "values": values,
        "research_only": True,
        "model_verified": False,
        "alert_allowed": False,
        "bet_allowed": False,
        "warning": "Historical baseline beats uniform NLL but is NOT independently "
                   "validated for profitable predictions. No Discord or wagers.",
    }


def safe_output(path):
    path = Path(path)
    if (path.is_absolute() or ".." in path.parts or not path.parts
            or path.parts[0] != "local_data" or path.suffix.lower() != ".json"):
        raise ValueError("output must be a local_data/*.json file")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--race-id", required=True)
    ap.add_argument("--features", type=Path, default=Path("local_data/pre_race_features.csv"))
    ap.add_argument("--model", type=Path, default=Path("local_data/models/research_baseline.json"))
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    try:
        inputs = forward_rows(args.features, args.race_id)
        model_file = safe_output(args.model)
        if not model_file.is_file():
            raise ValueError("trained real-data model is missing; run train_baseline.py after data readiness")
        model = json.loads(model_file.read_text(encoding="utf-8"))
        prediction = predict_forward(model, inputs)
        target = safe_output(args.output or Path("local_data/models") /
                             (args.race_id + "_top3.json"))
        if target.exists():
            raise ValueError("prediction already exists; do not overwrite a prior forecast")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(prediction, ensure_ascii=False, indent=2,
                                     allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps({
            "status": "RESEARCH_PROBABILITY_FILE_CREATED_UNVALIDATED",
            "race_id": args.race_id,
            "ordered_top3_outcomes": len(prediction["values"]),
            "model_id": prediction["model_id"],
            "output": str(target), "alert_allowed": False, "bet_allowed": False,
        }, ensure_ascii=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError) as exc:
        # Do not expose third-party race features or untrusted file contents.
        print("FORWARD_MODEL_NOT_READY:", type(exc).__name__ + ": " + str(exc)[:120],
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

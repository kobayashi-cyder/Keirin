#!/usr/bin/env python3
"""Point-in-time rider, track and video-tactics feature builder.

No scraping, guessing, image recognition, video upload or wagers. Combines
already captured genuine target racecards with earlier, rights-cleared race
event annotations. Historical video labels are ONLY used if their creation
time was earlier than the prediction's observed_at time. The code never
claims an unlabeled video has been "learned".
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pre_race_readiness import COLUMNS, ALLOWED_RIGHTS, _valid_row

EXTRA_FEATURE_NAMES = (
    "hist_races_180d", "hist_avg_finish_180d", "hist_top3_rate_180d",
    "hist_video_races_180d", "hist_attack_rate_180d",
    "hist_front_seconds_180d", "hist_late_gain_places_180d",
    "hist_line_cohesion_180d", "hist_same_venue_races_365d",
    "hist_same_grade_races_365d", "hist_same_grade_top3_rate_365d",
    "days_since_last_race", "context_bank_length_m", "context_wind_mps",
)
CONTEXT_FIELDS = (
    "race_id", "car_no", "rider_id", "venue", "grade",
    "bank_length_m", "wind_mps", "observed_at_jst",
    "source_url", "rights_status",
)
HISTORY_FIELDS = (
    "race_id", "rider_id", "race_at_jst", "available_at_jst",
    "venue", "grade", "finish_position", "attack_count",
    "front_seconds", "late_gain_places", "line_cohesion",
    "video_sha256", "source_url", "rights_status",
)
EXPORT_FIELDS = COLUMNS + ("ingested_at_jst",) + EXTRA_FEATURE_NAMES
WINDOW_LONG = timedelta(days=365)
WINDOW_SHORT = timedelta(days=180)


def dt(raw):
    try:
        date = datetime.fromisoformat(raw)
    except (ValueError, TypeError) as e:
        raise ValueError("invalid timestamp") from e
    if date.tzinfo is None or date.utcoffset() is None:
        raise ValueError("missing timezone offset")
    return date.astimezone(timezone.utc)


def number(raw, *, minimum=None, maximum=None, integer=False):
    try:
        val = float(raw)
    except (TypeError, ValueError) as e:
        raise ValueError("missing numeric historical feature") from e
    if (not math.isfinite(val) or (minimum is not None and val < minimum)
            or (maximum is not None and val > maximum)
            or (integer and not val.is_integer())):
        raise ValueError("invalid historical feature value")
    return int(val) if integer else val


def local(path):
    path = Path(path)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "local_data":
        raise ValueError("historical feature files must stay under local_data/")
    return path


def csv_records(path, required):
    path = local(path)
    if not path.is_file() or path.stat().st_size > 20_000_000:
        raise ValueError("required local CSV missing or too large")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if (not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames))
                or not set(required) <= set(reader.fieldnames)):
            raise ValueError("required CSV columns missing or duplicated")
        data = list(reader)
    if len(data) > 60000:
        raise ValueError("input too large; split into smaller batches")
    if any(None in r for r in data):
        raise ValueError("malformed CSV row")
    return data


def validate_context(context):
    if not context["rider_id"] or not context["venue"] or not context["grade"]:
        raise ValueError("missing rider identity, venue or grade")
    if context["rights_status"] not in ALLOWED_RIGHTS:
        raise ValueError("target context has no eligible rights status")
    if not context["source_url"].startswith("https://"):
        raise ValueError("target context requires HTTPS source")
    number(context["bank_length_m"], minimum=200, maximum=1000)
    number(context["wind_mps"], minimum=0, maximum=35)
    return context


def validate_history(event):
    if not event["rider_id"] or not event["race_id"] or not event["venue"] or not event["grade"]:
        raise ValueError("historical event identity incomplete")
    if event["rights_status"] not in ALLOWED_RIGHTS:
        raise ValueError("historical event has no eligible rights status")
    if not event["source_url"].startswith("https://"):
        raise ValueError("historical event source must be HTTPS")
    played = dt(event["race_at_jst"])
    available = dt(event["available_at_jst"])
    if available < played:
        raise ValueError("historical annotation available before event happened")
    number(event["finish_position"], minimum=1, maximum=9, integer=True)
    tag = event["video_sha256"].strip()
    if tag and not re.fullmatch(r"[a-f0-9]{64}", tag):
        raise ValueError("video_sha256 must be a lowercase 64-digit sha256")
    if not tag:
        # No video => video-derived tactical quantities must be absent, rather
        # than fabricated 0. The aggregate uses only video-tagged events.
        if any(event[name].strip() for name in ("attack_count", "front_seconds",
                                                "late_gain_places", "line_cohesion")):
            raise ValueError("video tactical annotations need a video hash")
    else:
        for name, max_value in (("attack_count", 30), ("front_seconds", 3000),
                                ("late_gain_places", 8), ("line_cohesion", 1)):
            number(event[name], minimum=0, maximum=max_value)
    return played, available


def average(items):
    return math.fsum(items) / len(items) if items else 0.0


def features_for_target(context, observed, events):
    current = validate_context(context)
    venue, grade = current["venue"], current["grade"]
    prior = [e for e in events
             if e["_race_at"] < observed and e["_available_at"] <= observed
             and observed - e["_race_at"] <= WINDOW_LONG]
    short = [e for e in prior if observed - e["_race_at"] <= WINDOW_SHORT]
    # Never invent an "average zero finishing position" for newcomers. Require
    # at least one earlier finished race; provide counts for tiny samples.
    if not short:
        raise ValueError("no eligible earlier history inside 180 days")
    video = [e for e in short if e["video_sha256"].strip()]
    same_grade = [e for e in prior if e["grade"] == grade]
    fields = {
        "hist_races_180d": len(short),
        "hist_avg_finish_180d": average([int(float(e["finish_position"])) for e in short]),
        "hist_top3_rate_180d": average([int(float(e["finish_position"]) <= 3) for e in short]),
        "hist_video_races_180d": len(video),
        "hist_attack_rate_180d": average([number(e["attack_count"]) for e in video]),
        "hist_front_seconds_180d": average([number(e["front_seconds"]) for e in video]),
        "hist_late_gain_places_180d": average([number(e["late_gain_places"]) for e in video]),
        "hist_line_cohesion_180d": average([number(e["line_cohesion"]) for e in video]),
        "hist_same_venue_races_365d": sum(e["venue"] == venue for e in prior),
        "hist_same_grade_races_365d": len(same_grade),
        "hist_same_grade_top3_rate_365d": average([
            int(float(e["finish_position"]) <= 3) for e in same_grade]),
        "days_since_last_race": (observed-max(e["_race_at"] for e in prior)).total_seconds()/86400,
        "context_bank_length_m": number(current["bank_length_m"]),
        "context_wind_mps": number(current["wind_mps"]),
    }
    return {key: f"{fields[key]:.9g}" for key in EXTRA_FEATURE_NAMES}


def build(base, context, historical, *, now=None):
    now = now or datetime.now(timezone.utc)
    now = dt(now.isoformat())
    current_rows = csv_records(base, COLUMNS + ("ingested_at_jst",))
    contexts = csv_records(context, CONTEXT_FIELDS)
    old_rows = csv_records(historical, HISTORY_FIELDS)
    if not current_rows or not contexts or not old_rows:
        raise ValueError("cannot create features from empty source files")
    context_lookup = {}
    for c in contexts:
        validate_context(c)
        key = (c["race_id"], c["car_no"])
        if key in context_lookup:
            raise ValueError("duplicate target race/car context")
        context_lookup[key] = c
    history_lookup = defaultdict(list)
    ids = set()
    for e in old_rows:
        played, available = validate_history(e)
        key = (e["race_id"], e["rider_id"])
        if key in ids:
            raise ValueError("duplicate rider historical race event")
        ids.add(key)
        history_lookup[e["rider_id"]].append({
            **e, "_race_at": played, "_available_at": available
        })
    outgoing = []
    by_race = defaultdict(list)
    for row in current_rows:
        by_race[row["race_id"]].append(row)
    for race_id, race_rows in sorted(by_race.items()):
        if len(race_rows) not in (7,8,9):
            raise ValueError("incomplete target race")
        cars = set()
        observed_set = set()
        for row in race_rows:
            car, observed, deadline, _ = _valid_row(row)
            if car in cars:
                raise ValueError("duplicate target car")
            cars.add(car)
            ingested = dt(row["ingested_at_jst"])
            observed = dt(row["observed_at_jst"])
            if ingested > now or observed > ingested or ingested >= dt(row["deadline_at_jst"]):
                raise ValueError("invalid or future target pre-race ingestion time")
            observed_set.add(observed)
            c = context_lookup.get((race_id, str(car)))
            if c is None:
                raise ValueError("missing target rider/context row")
            if dt(c["observed_at_jst"]) > observed:
                raise ValueError("target context was not known at racecard observation")
            extras = features_for_target(c, observed, history_lookup[c["rider_id"]])
            outgoing.append({key: row[key] for key in COLUMNS + ("ingested_at_jst",)} | extras)
        if cars != set(range(1, len(race_rows)+1)) or len(observed_set) != 1:
            raise ValueError("target race has nonconsecutive cars or mixed snapshot times")
    if set(context_lookup) != {(r["race_id"], r["car_no"]) for r in current_rows}:
        raise ValueError("target context includes races/cars absent from base features")
    return outgoing, len(by_race)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path, default=Path("local_data/pre_race_features.csv"))
    ap.add_argument("--context", type=Path, default=Path("local_data/history/target_context.csv"))
    ap.add_argument("--events", type=Path, default=Path("local_data/history/rider_events.csv"))
    ap.add_argument("--output", type=Path, default=Path("local_data/pre_race_multiview.csv"))
    ap.add_argument("--commit", action="store_true", help="default DRY RUN")
    ap.add_argument("--init-templates", action="store_true",
                    help="create blank context/event CSV headers without overwriting")
    args = ap.parse_args()
    try:
        if args.init_templates:
            targets = [(local(args.context), CONTEXT_FIELDS),
                       (local(args.events), HISTORY_FIELDS)]
            if any(path.exists() for path, _ in targets):
                raise ValueError("target context or historical event template already exists")
            for path, header in targets:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding="utf-8", newline="") as fh:
                    csv.DictWriter(fh, fieldnames=header).writeheader()
            print(json.dumps({"status": "BLANK_HISTORY_CSV_TEMPLATES_CREATED",
                              "files": [str(p) for p, _ in targets],
                              "training_ready": False}, ensure_ascii=False))
            return 0
        output = local(args.output)
        rows, races = build(args.base, args.context, args.events)
        if args.commit:
            if output.exists():
                raise ValueError("output exists; refusing to overwrite feature history")
            output.parent.mkdir(parents=True, exist_ok=True)
            temp = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", newline="", encoding="utf-8",
                    prefix="multiview_", suffix=".tmp",
                    dir=output.parent, delete=False,
                ) as fh:
                    temp = Path(fh.name)
                    writer = csv.DictWriter(fh, fieldnames=EXPORT_FIELDS)
                    writer.writeheader()
                    writer.writerows(rows)
                os.replace(temp, output)
            finally:
                if temp and temp.exists():
                    temp.unlink()
        print(json.dumps({
            "status": "MULTIVIEW_FEATURES_CREATED" if args.commit
                      else "MULTIVIEW_DRY_RUN_VALID",
            "races": races, "rows": len(rows),
            "extra_features": EXTRA_FEATURE_NAMES,
            "video_feature_note": "0 video-race count is NOT evidence of zero attacks.",
            "output": str(output) if args.commit else None,
            "model_verified": False, "alert_allowed": False,
        }, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, csv.Error, TypeError, KeyError) as exc:
        print("MULTIVIEW_NOT_READY:", type(exc).__name__ + ": " + str(exc)[:160],
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

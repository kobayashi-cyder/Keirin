#!/usr/bin/env python3
"""Convert licensed pre-race racecard CSV exports to the strict Keirin intake.

No HTTP, scraping, OpenAI calls or betting. Default is DRY RUN; --commit
runs the existing append-only pre-race importer. This does not grant or
verify data rights. A historical result CSV cannot be made pre-race by
changing timestamps: observation time is always the local import time.
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
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pre_race_readiness import COLUMNS
from intake_pre_race import intake, validate_rows

JST = ZoneInfo("Asia/Tokyo")
UNMAPPED = ("observed_at_jst", "ingested_at_jst")
ALIASES = {
    "race_id": ("race_id", "raceId", "レースID", "レースＩＤ"),
    "race_date": ("race_date", "date", "開催日", "日付"),
    "car_no": ("car_no", "banum", "car", "車番"),
    "deadline_at_jst": ("deadline_at_jst", "deadline", "投票締切日時", "締切日時"),
    "start_at_jst": ("start_at_jst", "start_time", "発走予定日時", "発走日時"),
    "source_url": ("source_url", "source", "取得元URL"),
    "rights_status": ("rights_status", "利用許諾状態"),
    "line_id": ("line_id", "line", "ラインID", "ライン"),
    "line_position": ("line_position", "line_order", "ライン位置", "番手"),
    "rating": ("rating", "race_score", "score", "競走得点"),
    "recent_win_rate": ("recent_win_rate", "win_rate", "勝率", "1着率"),
    "recent_top2_rate": ("recent_top2_rate", "place2_rate", "top2_rate", "2連対率"),
    "recent_top3_rate": ("recent_top3_rate", "place3_rate", "top3_rate", "3連対率"),
    "age": ("age", "年齢"),
}


def safe_local_path(path):
    value = Path(path)
    if (value.is_absolute() or ".." in value.parts or not value.parts
            or value.parts[0] != "local_data"):
        raise ValueError("only relative paths under local_data/ are allowed")
    return value


def csv_input(path):
    path = safe_local_path(path)
    if not path.is_file():
        raise ValueError("provider CSV file not found")
    if path.stat().st_size > 20_000_000:
        raise ValueError("provider CSV exceeds 20MB, split the export")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("invalid or duplicate provider column names")
        if len(reader.fieldnames) > 150:
            raise ValueError("provider has too many columns")
        rows = list(reader)
    if not rows:
        raise ValueError("provider CSV is empty; no race data to import")
    if len(rows) > 20_000:
        raise ValueError("provider CSV too large; split into smaller exports")
    return reader.fieldnames, rows


def mapping_for_headers(headers, explicit):
    if not isinstance(explicit, dict):
        raise ValueError("mapping JSON must be an object")
    if any(key not in ALIASES or not isinstance(value, str) for key, value in explicit.items()):
        raise ValueError("mapping contains unknown target or nonstring source column")
    result = {}
    for field, aliases in ALIASES.items():
        if field in explicit:
            if explicit[field] not in headers:
                raise ValueError(f"{field}: configured provider column not found")
            result[field] = explicit[field]
            continue
        found = [label for label in aliases if label in headers]
        if len(found) > 1:
            raise ValueError(f"{field}: ambiguous source columns; use --mapping")
        if found:
            result[field] = found[0]
    if len(set(result.values())) != len(result):
        raise ValueError("one provider column maps to multiple target fields")
    return result


def parse_lineup(lineup, cars):
    """Line groups, e.g. '1-2-6/7/3-4-5'; reject duplicates/missing cars."""
    if not isinstance(lineup, str) or not re.fullmatch(r"[1-9]+(?:-[1-9]+)*(?:/[1-9]+(?:-[1-9]+)*)*", lineup):
        raise ValueError("lineup must look like 1-2-6/7/3-4-5")
    groups = [tuple(map(int, part.split("-"))) for part in lineup.split("/")]
    listed = [car for group in groups for car in group]
    if len(set(listed)) != len(listed) or set(listed) != cars:
        raise ValueError("lineup must contain every car exactly once")
    return {car: (f"L{index}", position)
            for index, group in enumerate(groups, 1)
            for position, car in enumerate(group, 1)}


def normalize_rate(raw, column):
    value = str(raw or "").strip().replace("％", "%").replace(",", "")
    percent = value.endswith("%")
    if percent:
        value = value[:-1].strip()
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{column}: missing or invalid rate") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{column}: rate must be finite and nonnegative")
    if percent or 1 < number <= 100:
        number /= 100
    if not 0 <= number <= 1:
        raise ValueError(f"{column}: rate exceeds 100 percent")
    return f"{number:.9g}"


def convert(headers, raw_rows, *, mapping=None, race_id=None,
            deadline=None, start=None, source_url=None, rights_status=None,
            lineup=None, now=None):
    """Convert to canonical rows. Fail closed on ambiguous data.

    The current import time is written as observed_at_jst; this avoids
    false claims about when a file obtained from elsewhere was first seen.
    """
    now = now or datetime.now(JST)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("time must include zone offset")
    observed = now.astimezone(JST).isoformat()
    maps = mapping_for_headers(headers, mapping or {})
    overrides = {
        "race_id": race_id, "deadline_at_jst": deadline,
        "start_at_jst": start, "source_url": source_url,
        "rights_status": rights_status,
    }
    def get(raw, key):
        original = raw.get(maps[key], "") if key in maps else ""
        original = str(original or "").strip()
        override = overrides.get(key)
        if override is not None:
            override = str(override).strip()
            if original and original != override:
                raise ValueError(f"{key}: provider/command metadata mismatch")
            return override
        return original

    by_race = defaultdict(list)
    for index, raw in enumerate(raw_rows, 1):
        if not isinstance(raw, dict) or None in raw:
            raise ValueError(f"provider row {index}: malformed CSV")
        rid = get(raw, "race_id")
        if not rid:
            raise ValueError(f"provider row {index}: race_id missing")
        by_race[rid].append(raw)
    if lineup is not None and len(by_race) != 1:
        raise ValueError("--lineup can only be used with exactly one race per file")
    all_rows = []
    for rid, provider_race in by_race.items():
        numbers = set()
        for raw in provider_race:
            car_val = get(raw, "car_no")
            try:
                car = int(car_val)
            except ValueError as exc:
                raise ValueError(f"{rid}: invalid car number") from exc
            if car in numbers:
                raise ValueError(f"{rid}: duplicate car number")
            numbers.add(car)
        provided_lineup = parse_lineup(lineup, numbers) if lineup else None
        for raw in provider_race:
            row = {key: get(raw, key) for key in COLUMNS if key in ALIASES}
            row["race_id"] = rid
            row["observed_at_jst"] = observed
            start_time = row.get("start_at_jst", "")
            if not row.get("race_date") and start_time:
                try:
                    row["race_date"] = datetime.fromisoformat(start_time).date().isoformat()
                except ValueError as exc:
                    raise ValueError(f"{rid}: invalid start time") from exc
            for key in ("recent_win_rate", "recent_top2_rate", "recent_top3_rate"):
                row[key] = normalize_rate(row.get(key), key)
            if provided_lineup is not None:
                car = int(row["car_no"])
                group, position = provided_lineup[car]
                if row.get("line_id") and row["line_id"] != group:
                    raise ValueError(f"{rid}: lineup disagrees with existing line_id")
                if row.get("line_position") and row["line_position"] != str(position):
                    raise ValueError(f"{rid}: lineup disagrees with existing line_position")
                row["line_id"], row["line_position"] = group, str(position)
            for name in COLUMNS:
                if not row.get(name):
                    raise ValueError(f"{rid}: missing required provider field {name}")
            all_rows.append({key: row[key] for key in COLUMNS})
    return all_rows


def do_import(input_path, output_path, *, mapping_path=None, commit=False,
              race_id=None, deadline=None, start=None, source_url=None,
              rights_status=None, lineup=None, now=None):
    input_path, output_path = safe_local_path(input_path), safe_local_path(output_path)
    if input_path == output_path:
        raise ValueError("source CSV and archive must differ")
    mapping = {}
    if mapping_path is not None:
        map_path = safe_local_path(mapping_path)
        if not map_path.is_file() or map_path.stat().st_size > 20_000:
            raise ValueError("mapping file missing or too large")
        mapping = json.loads(map_path.read_text(encoding="utf-8-sig"))
    headers, source_rows = csv_input(input_path)
    now = now or datetime.now(JST)
    rows = convert(headers, source_rows, mapping=mapping, race_id=race_id,
                   deadline=deadline, start=start, source_url=source_url,
                   rights_status=rights_status, lineup=lineup, now=now)
    groups = validate_rows(rows, now=now)
    if not commit:
        return {
            "status": "DRY_RUN_VALIDATED_NOT_SAVED",
            "races": len(groups), "rider_rows": len(rows),
            "archive_path": str(output_path), "training_allowed": False,
            "note": "No data was saved. Rights status is asserted, not independently verified.",
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", suffix=".csv",
            prefix="provider_normalized_", dir=output_path.parent, delete=False,
        ) as fh:
            temp_path = Path(fh.name)
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        result = intake(temp_path, output_path, now=now)
        result["import_source"] = "locally_supplied_provider_csv"
        return result
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("local_data/pre_race_features.csv"))
    parser.add_argument("--mapping", type=Path, help="optional JSON {canonical_column: provider_column}")
    parser.add_argument("--race-id", help="single-race ID if the CSV lacks one")
    parser.add_argument("--deadline-jst", help="e.g. 2026-11-09T20:35:00+09:00")
    parser.add_argument("--start-jst", help="e.g. 2026-11-09T20:40:00+09:00")
    parser.add_argument("--source-url", help="actual provider HTTPS URL")
    parser.add_argument("--rights-status", choices=["permitted", "self_recorded"],
                        help="actual permission or genuinely self-owned measurements")
    parser.add_argument("--lineup", help="single race only, e.g. 1-2-6/7/3-4-5")
    parser.add_argument("--commit", action="store_true",
                        help="append validated rows to the local archive, default DRY RUN")
    args = parser.parse_args()
    try:
        result = do_import(
            args.input, args.output, mapping_path=args.mapping,
            commit=args.commit, race_id=args.race_id,
            deadline=args.deadline_jst, start=args.start_jst,
            source_url=args.source_url, rights_status=args.rights_status,
            lineup=args.lineup,
        )
    except (ValueError, OSError, TypeError, KeyError, csv.Error, json.JSONDecodeError) as exc:
        print("PROVIDER_IMPORT_REJECTED:", type(exc).__name__ + ": " + str(exc)[:180],
              file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

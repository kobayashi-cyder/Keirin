#!/usr/bin/env python3
"""Read-only audit of a locally saved Keirin odds JSONL snapshot.

Checks whether the program identified every *possible* ticket, timestamps,
numeric bounds and canonical ticket keys. This is NOT independently verified
site data and does not train, infer an edge, alert or make a bet.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "paper_trading"))
from all_markets import FRAME_BY_SIZE, MARKETS


def _at(value):
    try:
        d = datetime.fromisoformat(value)
    except (ValueError, TypeError) as e:
        raise ValueError("invalid ISO timestamp") from e
    if d.tzinfo is None or d.utcoffset() is None:
        raise ValueError("timestamp without UTC offset")
    return d.astimezone(timezone.utc)


def expected_keys(market, n):
    cars = tuple(range(1, n + 1))
    if market == "3rentan":
        return {"-".join(map(str, p)) for p in itertools.permutations(cars, 3)}
    if market == "2shatan":
        return {"-".join(map(str, p)) for p in itertools.permutations(cars, 2)}
    if market == "3renpuku":
        return {"-".join(map(str, p)) for p in itertools.combinations(cars, 3)}
    if market in ("2shafuku", "wide"):
        return {"-".join(map(str, p)) for p in itertools.combinations(cars, 2)}
    if market in ("2wakutan", "2wakufuku"):
        if n < 8:
            return set()  # not offered for normal seven-car race
        frames = FRAME_BY_SIZE[n]
        pairs = set()
        for a, b in itertools.permutations(cars, 2):
            f1, f2 = frames[a - 1], frames[b - 1]
            p = (f1, f2)
            if market == "2wakufuku":
                p = tuple(sorted(p))
            pairs.add("-".join(map(str, p)))
        return pairs
    raise ValueError("unrecognised market")


def _check_quote(value):
    if isinstance(value, dict):
        if set(value) != {"min", "max"}:
            return False
        lo, hi = value["min"], value["max"]
    else:
        lo = hi = value
    return (type(lo) in (int, float) and type(hi) in (int, float)
            and math.isfinite(lo) and math.isfinite(hi)
            and 1 <= lo <= hi)


def audit_record(record, *, now=None, max_page_age_minutes=10):
    if not isinstance(record, dict):
        raise ValueError("record is not a mapping")
    cars = record.get("cars")
    if type(cars) is not int or cars not in FRAME_BY_SIZE:
        raise ValueError("invalid number of cars")
    observed = _at(record.get("observed_at_jst"))
    deadline = _at(record.get("deadline_at_jst"))
    if observed >= deadline:
        raise ValueError("odds observation not before deadline")
    now = (now or datetime.now(timezone.utc))
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must have timezone")
    now = now.astimezone(timezone.utc)
    source = record.get("source_url")
    if not isinstance(source, str):
        raise ValueError("missing provenance")
    url = urlsplit(source)
    if url.scheme != "https" or url.hostname != "keirin.kdreams.jp":
        raise ValueError("unexpected provenance source")
    markets = record.get("markets")
    if not isinstance(markets, dict) or not markets:
        raise ValueError("missing markets")

    displayed_times = record.get("page_displayed_times_jst", [])
    if not isinstance(displayed_times, list):
        raise ValueError("page display timestamps should be a list")
    displayed = [_at(item) for item in displayed_times]
    if any(t > observed for t in displayed):
        raise ValueError("page update time is after observation")
    latest_displayed = max(displayed) if displayed else None
    oldest_displayed = min(displayed) if displayed else None
    if latest_displayed is None:
        source_freshness = "UNKNOWN_PAGE_UPDATE_TIME"
    elif (observed - oldest_displayed).total_seconds() > max_page_age_minutes * 60:
        source_freshness = "STALE_OR_MIXED_PAGE_UPDATE_TIME"
    else:
        source_freshness = "PAGE_UPDATE_TIMES_WITHIN_WINDOW"
    if now >= deadline:
        usability = "HISTORICAL_DO_NOT_TREAT_AS_LIVE"
    elif latest_displayed is None:
        usability = "UNKNOWN_SOURCE_FRESHNESS"
    elif source_freshness != "PAGE_UPDATE_TIMES_WITHIN_WINDOW":
        usability = "SOURCE_ODDS_STALE"
    else:
        usability = "SOURCE_UPDATE_RECENT_NOT_VERIFIED"

    breakdown, issues = {}, []
    for market, value in markets.items():
        if market not in MARKETS:
            issues.append("unknown market: " + str(market))
            continue
        if not isinstance(value, dict) or not isinstance(value.get("quotes"), dict):
            issues.append(f"{market}: missing quotes mapping")
            continue
        quotes = value["quotes"]
        possible = expected_keys(market, cars)
        keys = set(quotes)
        bad_keys = sorted(keys - possible)
        bad_prices = sorted(k for k, v in quotes.items() if not _check_quote(v))
        count = len(quotes)
        reported = value.get("count")
        reported_total = value.get("expected_count")
        complete = count == len(possible)
        if reported != count or reported_total != len(possible) or value.get("complete") is not complete:
            issues.append(f"{market}: reported count/complete inconsistent")
        if bad_keys:
            issues.append(f"{market}: invalid ticket keys, examples {bad_keys[:3]}")
        if bad_prices:
            issues.append(f"{market}: bad odds values, examples {bad_prices[:3]}")
        breakdown[market] = {
            "observed": count, "possible": len(possible),
            "missing": len(possible) - len(keys & possible),
            "claimed_complete": value.get("complete"),
            "structural_coverage_complete": complete and not bad_keys and not bad_prices,
        }

    return {
        "status": "REJECTED" if issues else "STRUCTURE_CHECK_PASSED_NOT_SOURCE_VERIFIED",
        "alert_allowed": False, "bet_allowed": False, "model_verified": False,
        "source_url": source, "cars": cars,
        "odds_observed_at": record["observed_at_jst"],
        "odds_deadline_at": record["deadline_at_jst"],
        "page_update_times": displayed_times,
        "latest_page_update_age_minutes_at_capture": (
            round((observed - latest_displayed).total_seconds() / 60, 2)
            if latest_displayed else None),
        "oldest_page_update_age_minutes_at_capture": (
            round((observed - oldest_displayed).total_seconds() / 60, 2)
            if oldest_displayed else None),
        "source_freshness": source_freshness,
        "current_usability": usability,
        "markets": breakdown, "issues": issues,
        "note": "Completeness is based on counts and valid ticket sets, not independent odds verification. "
                "A 10-second browser polling interval is NOT a promise of 10-second site odds refresh.",
    }


def audit_jsonl(file, *, now=None):
    file = Path(file)
    if not file.exists():
        raise ValueError("snapshot does not exist")
    with file.open("r", encoding="utf-8") as handle:
        records = [line for line in handle if line.strip()]
    if not records:
        raise ValueError("empty JSONL snapshot")
    data = json.loads(records[-1])
    result = audit_record(data, now=now)
    result["file"] = str(file)
    result["record_count"] = len(records)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True, help="local_data/odds_snapshots/<race_id>.jsonl")
    ap.add_argument("--output", type=Path, help="optional local report JSON")
    args = ap.parse_args()
    try:
        result = audit_jsonl(args.input)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print("AUDIT_REJECTED: " + str(e), file=sys.stderr)
        return 2
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        if args.output.is_absolute() or ".." in args.output.parts or args.output.parts[0] != "local_data":
            print("AUDIT_REJECTED: report must stay under local_data/", file=sys.stderr)
            return 2
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if result["status"].startswith("STRUCTURE_CHECK_PASSED") else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Deduplicated local Discord notices for *completed research data only*.

Scan pre-existing local_data/odds_snapshots/*.jsonl; no scraping or bets.
No notification of prices, odds value, probabilities, candidate tickets or
profit claims. Full race snapshots alone do not validate a prediction model.
Never send on stale/expired/partial or malformed odds records.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from audit_odds_snapshot import audit_record, _at
from odds_to_shadow import last_record, race_id_from_source, research_report
from discord_openai_status import (
    compose_message, load_local_env, openai_note, send_discord, status_message,
)

STATE_PATH = Path("local_data/discord_research_notified.json")
EXPECTED_MARKETS = {
    7: {"3rentan", "3renpuku", "2shatan", "2shafuku", "wide"},
    8: {"3rentan", "3renpuku", "2shatan", "2shafuku", "wide",
        "2wakutan", "2wakufuku"},
    9: {"3rentan", "3renpuku", "2shatan", "2shafuku", "wide",
        "2wakutan", "2wakufuku"},
}


def safe_path(path):
    path = Path(path)
    if (path.is_absolute() or ".." in path.parts or not path.parts
            or path.parts[0] != "local_data"):
        raise ValueError("input and state paths must be under local_data/")
    return path


def read_state(path=STATE_PATH):
    path = safe_path(path)
    if not path.exists():
        return {"schema": "keirin-discord-research-once-v1", "notified": {}}
    if not path.is_file() or path.stat().st_size > 128 * 1024:
        raise ValueError("invalid notification state file")
    obj = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(obj, dict) or obj.get("schema") != "keirin-discord-research-once-v1"
            or not isinstance(obj.get("notified"), dict)):
        raise ValueError("invalid notification state schema")
    if any(not isinstance(k, str) or not isinstance(v, str)
           for k, v in obj["notified"].items()):
        raise ValueError("invalid notification state entries")
    return obj


def save_state(state, path=STATE_PATH):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise ValueError("state path may not be a symlink")
    new_path = path.with_name(path.name + ".pending")
    if new_path.exists() and new_path.is_symlink():
        raise ValueError("state temporary path may not be a symlink")
    new_path.write_text(
        json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(new_path, path)


def inspect_record(record, *, now=None):
    """Check real pre-close freshness and complete coverage; no model needed."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware clock required")
    now = now.astimezone(timezone.utc)
    audit = audit_record(record, now=now)
    if audit["status"] != "STRUCTURE_CHECK_PASSED_NOT_SOURCE_VERIFIED":
        return None, "INVALID_ODDS"
    required = EXPECTED_MARKETS[record["cars"]]
    if set(audit["markets"]) != required:
        return None, "MISSING_MARKETS"
    if not all(m["structural_coverage_complete"] for m in audit["markets"].values()):
        return None, "INCOMPLETE_MARKET"
    if audit["source_freshness"] != "PAGE_UPDATE_TIMES_WITHIN_WINDOW":
        return None, "STALE_SITE_UPDATE"
    observed = _at(record["observed_at_jst"])
    deadline = _at(record["deadline_at_jst"])
    if observed > now + timedelta(seconds=30):
        return None, "FUTURE_ODDS_OBSERVATION"
    if now - observed > timedelta(minutes=10):
        return None, "STALE_LOCAL_SNAPSHOT"
    if deadline - now < timedelta(minutes=10):
        return None, "PAST_OR_NEAR_DEADLINE"
    # Does not calculate EV; report explicitly has no probability source.
    report = research_report(record, now=now)
    if (report["status"] != "NOT_READY_NO_PROBABILITIES" or
            report["candidates"] or report["alert_allowed"]):
        return None, "UNSAFE_REPORT"
    return report, "READY_RESEARCH_COUNTS_ONLY"


def deliver_once(file, state, *, now=None, send=False,
                 use_openai=False, model=None, poster=send_discord,
                 note_generator=None):
    if note_generator is None:
        note_generator = openai_note
    """At most one notice per race. Dedup persists only after HTTP success.

    For a failed Discord POST the state remains unchanged so an operator can
    retry deliberately. Automatic polling does not retry the same record
    repeatedly within a run (see run_scan).
    """
    file = safe_path(file)
    record = last_record(file)
    race_id = race_id_from_source(record)
    if race_id in state["notified"]:
        return "ALREADY_NOTIFIED"
    report, reason = inspect_record(record, now=now)
    if report is None:
        return "SKIPPED_" + reason
    message = status_message(report)
    if use_openai:
        note, api_status = note_generator("research_data_status", model=model)
        if api_status != "OK":
            return "OPENAI_" + api_status
        message = compose_message(message, note)
    if not send:
        return "DRY_RUN_READY"
    result = poster(message)
    if result != "SENT":
        return "DISCORD_" + result
    state["notified"][race_id] = datetime.now(timezone.utc).isoformat()
    return "SENT"


def run_scan(directory, state_path, *, send=False, use_openai=False,
             model=None, now=None, poster=None,
             note_generator=None, suppress_failed=None):
    directory, state_path = safe_path(directory), safe_path(state_path)
    if poster is None:
        poster = send_discord
    if note_generator is None:
        note_generator = openai_note
    if suppress_failed is None:
        suppress_failed = set()
    if not directory.is_dir():
        return {"status": "NO_SNAPSHOT_DIRECTORY", "checked": 0, "sent": 0}
    state = read_state(state_path)
    counts = {}
    sent = 0
    # Limit one scan to 100 distinct race files to prevent backlog or spam.
    files = sorted(directory.glob("*.jsonl"))[:100]
    for path in files:
        if path.name in suppress_failed:
            counts["SUPPRESSED_FAILED_THIS_RUN"] = counts.get("SUPPRESSED_FAILED_THIS_RUN", 0) + 1
            continue
        if sent >= 3:
            counts["MAX_POSTS_PER_SCAN"] = counts.get("MAX_POSTS_PER_SCAN", 0) + 1
            break
        try:
            result = deliver_once(path, state, now=now, send=send,
                                  use_openai=use_openai, model=model,
                                  poster=poster, note_generator=note_generator)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, OSError) as error:
            # No secrets, URLs, odds, participant details, or raw HTML in logs.
            result = "INVALID_SNAPSHOT"
        counts[result] = counts.get(result, 0) + 1
        if result.startswith(("OPENAI_", "DISCORD_")):
            suppress_failed.add(path.name)
        if result == "SENT":
            sent += 1
            save_state(state, state_path)
    return {"status": "SCAN_DONE", "checked": len(files), "sent": sent,
            "counts": dict(sorted(counts.items()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", type=Path,
                        default=Path("local_data/odds_snapshots"))
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--send", action="store_true",
                        help="opt in to sending; omitted by default")
    parser.add_argument("--use-openai", action="store_true",
                        help="optional GPT-4o note (costs API tokens)")
    parser.add_argument("--model", default=None,
                        help="OpenAI model, default from .env or GPT-4o")
    parser.add_argument("--cycles", type=int, default=1,
                        help="number of directory scans, 1-720")
    parser.add_argument("--interval", type=int, default=60,
                        help="seconds between scans, at least 30")
    args = parser.parse_args()
    if not 1 <= args.cycles <= 720 or args.interval < 30:
        parser.error("cycles 1-720 and interval >=30 seconds are required")
    try:
        print("LOCAL_ENV:", load_local_env())
        failed_this_run = set()
        for cycle in range(args.cycles):
            outcome = run_scan(
                args.snapshots, args.state, send=args.send,
                use_openai=args.use_openai, model=args.model,
                suppress_failed=failed_this_run
            )
            # Counts only, not odds or API errors containing private data.
            print(json.dumps({"cycle": cycle+1, **outcome}, ensure_ascii=False),
                  flush=True)
            if cycle + 1 < args.cycles:
                time.sleep(args.interval)
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print("AUTO_STATUS_REJECTED:", type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

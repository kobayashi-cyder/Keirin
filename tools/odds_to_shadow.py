#!/usr/bin/env python3
"""Connect audited saved odds to *supplied* pre-race joint probabilities.

Research only. No network, model invention, scraping, Discord, or betting.
Without independently generated before-race probabilities, outputs a readiness
report and NO expected-return tickets. Partial markets are excluded.
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from audit_odds_snapshot import _at, audit_record
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "paper_trading"))
from all_markets import evaluate_offered_odds, project_all_markets, validate_top3


def last_record(path):
    """Read last nonempty JSONL row; do not modify the original snapshot."""
    p = Path(path)
    if not p.is_file():
        raise ValueError("odds snapshot does not exist")
    last = None
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                last = line
    if last is None:
        raise ValueError("odds snapshot contains no data")
    return json.loads(last)


def race_id_from_source(record):
    url = urlsplit(record["source_url"])
    match = re.fullmatch(r"/[a-z0-9-]+/racedetail/(\d+)/", url.path)
    if not match:
        raise ValueError("source URL does not contain a canonical race ID")
    return match.group(1)


def validate_probability_input(document, record):
    """Require all ordered 1-3 finish outcomes with prior-generation time."""
    if not isinstance(document, dict) or document.get("schema") != "keirin-top3-probabilities-v1":
        raise ValueError("unsupported probability input schema")
    if document.get("race_id") != race_id_from_source(record):
        raise ValueError("probabilities refer to another race")
    if type(document.get("cars")) is not int or document["cars"] != record["cars"]:
        raise ValueError("probabilities refer to another number of cars")
    if not isinstance(document.get("model_id"), str) or not document["model_id"].strip():
        raise ValueError("model_id required for research provenance")
    generated = _at(document.get("generated_at"))
    observed = _at(record["observed_at_jst"])
    deadline = _at(record["deadline_at_jst"])
    if generated > observed or generated >= deadline:
        raise ValueError("probabilities were not demonstrably generated before odds observation")
    cars = list(range(1, document["cars"] + 1))
    probs = document.get("values")
    validated = validate_top3(cars, probs)
    expected = document["cars"] * (document["cars"]-1) * (document["cars"]-2)
    if len(validated) != expected:
        raise ValueError("model does not provide every ordered top-three outcome")
    return probs


def research_report(record, *, probabilities=None, now=None, threshold=1.30,
                    stress=0.75, top=20):
    """Fail closed on missing probabilities, stale or malformed odds."""
    audit = audit_record(record, now=now)
    if audit["status"] != "STRUCTURE_CHECK_PASSED_NOT_SOURCE_VERIFIED":
        raise ValueError("odds snapshot failed integrity audit: " + repr(audit["issues"]))
    market_names = sorted(audit["markets"])
    usable = sorted(name for name, item in audit["markets"].items()
                    if item["structural_coverage_complete"])
    partial = sorted(set(market_names) - set(usable))
    result = {
        "status": "NOT_READY_NO_PROBABILITIES",
        "race_id": race_id_from_source(record),
        "cars": record["cars"],
        "odds_observed_at": record["observed_at_jst"],
        "deadline_at": record["deadline_at_jst"],
        "page_update_times": audit["page_update_times"],
        "source_freshness": audit["source_freshness"],
        "current_usability": audit["current_usability"],
        "markets_observed": market_names,
        "markets_structurally_complete": usable,
        "markets_excluded_incomplete": partial,
        "market_coverage": audit["markets"],
        "threshold_return": threshold,
        "stress_factor": stress,
        "alert_allowed": False,
        "bet_allowed": False,
        "model_verified": False,
        "qualifying_count": 0,
        "candidates": [],
        "warning": "Market completeness is structural, not independent quote verification. "
                   "No verified prediction model or authorized live feed; no alerts or bets.",
    }
    if probabilities is None:
        return result
    # Validate even if the odds are not fresh; never disguise bad model inputs.
    dist = validate_probability_input(probabilities, record)
    result["probability_model_id"] = probabilities["model_id"]
    result["probability_generated_at"] = probabilities["generated_at"]
    if audit["source_freshness"] != "PAGE_UPDATE_TIMES_WITHIN_WINDOW":
        result["status"] = "RESEARCH_BLOCKED_STALE_OR_UNKNOWN_SITE_UPDATE"
        return result
    if not usable:
        result["status"] = "RESEARCH_BLOCKED_NO_COMPLETE_MARKETS"
        return result
    # All included quotes have passed the audit: keys, coverage and bounds.
    quoted = {name: record["markets"][name]["quotes"] for name in usable}
    projected = project_all_markets(list(range(1, record["cars"] + 1)), dist)
    selected = evaluate_offered_odds(projected, quoted,
                                     threshold=threshold, stress=stress)
    result.update({
        "status": "SHADOW_ONLY_UNVALIDATED",
        "qualifying_count": len(selected),
        "candidates": selected[:top],
        "note": "Candidate ranking is only a mathematical comparison to supplied "
                "pre-race probabilities. The model is NOT approved and this report "
                "is not a recommendation, live odds confirmation or ROI proof.",
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True,
                        help="saved local_data/odds_snapshots/<race>.jsonl")
    parser.add_argument("--probabilities", type=Path,
                        help="optional pre-race top3 probability JSON; if absent, readiness only")
    parser.add_argument("--output", type=Path,
                        help="optional report file, only under local_data/")
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()
    try:
        if args.top < 1:
            raise ValueError("--top must be positive")
        record = last_record(args.input)
        probs = (json.loads(args.probabilities.read_text(encoding="utf-8"))
                 if args.probabilities else None)
        report = research_report(record, probabilities=probs, top=args.top)
        serialized = json.dumps(report, indent=2, ensure_ascii=False,
                                allow_nan=False) + "\n"
        if args.output is not None:
            output = args.output
            if (output.is_absolute() or ".." in output.parts or
                    not output.parts or output.parts[0] != "local_data"):
                raise ValueError("output must be under local_data/ in the repository")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(serialized, encoding="utf-8")
        print(serialized, end="")
        return 0
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        print("RESEARCH_BRIDGE_REJECTED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

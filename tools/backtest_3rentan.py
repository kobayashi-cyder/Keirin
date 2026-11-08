#!/usr/bin/env python3
"""Historical 3rentan paper backtest with strict point-in-time checks.

Uses archived PRE-CLOSE odds JSONL, race-matched before-odds top3
probabilities, and actual settled 3rentan payout in results CSV.
No future results/payouts are inputs to the choice. No bets or alerts.
Only one hypothetical 100-yen ticket per race. Never settle using
a quoted pre-close price, or call price movements guaranteed profit.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from audit_odds_snapshot import audit_record, _at
from odds_to_shadow import race_id_from_source, validate_probability_input
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "paper_trading"))
from all_markets import project_all_markets

KDR = re.compile(r"/[a-z0-9-]+/racedetail/(\d+)/")
MAX_BYTES = 15_000_000


def _local(path, *, allow_exports=False):
    path = Path(path)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("path must be relative and cannot escape repository")
    if path.parts[0] != "local_data" and not (
            allow_exports and path == Path("exports/results_3rentan.csv")):
        raise ValueError("read data only under local_data/ (or default exports/results_3rentan.csv)")
    return path


def _json(path):
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("JSON model missing or too large")
    return json.loads(path.read_text(encoding="utf-8"))


def result_id(row):
    """Resolve a race to its Kdreams canonical ID without guessing joins."""
    raw = row.get("race_id", "")
    matches = set()
    if isinstance(raw, str) and re.fullmatch(r"\d{10,24}", raw):
        matches.add(raw)
    for candidate in str(row.get("source_urls", "")).split(";"):
        candidate = candidate.strip()
        if not candidate:
            continue
        url = urlsplit(candidate)
        if url.scheme != "https" or url.hostname != "keirin.kdreams.jp":
            continue
        matched = KDR.fullmatch(url.path)
        if matched:
            matches.add(matched.group(1))
    return next(iter(matches)) if len(matches) == 1 else None


def results_index(path):
    path = _local(path, allow_exports=True)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("result CSV missing or too large")
    out = {}
    duplicate = set()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"race_id", "date", "bet_type", "winning_combination",
                    "payout_per_100yen", "source_urls"}
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            raise ValueError("result CSV missing mandatory columns")
        for row in reader:
            if row.get("bet_type") != "3rentan":
                continue
            race_id = result_id(row)
            if race_id is None:
                continue
            if race_id in out:
                duplicate.add(race_id)
            out[race_id] = row
    for race_id in duplicate:
        out.pop(race_id, None)
    return out, len(duplicate)


def settlement(row, cars):
    """Only an unequivocal single winning combination and 100-yen payout."""
    if row.get("result_type", "").strip().lower() not in ("", "normal"):
        raise ValueError("unsettleable result type: tie, refund or cancellation")
    raw = str(row.get("winning_combination", ""))
    if not re.fullmatch(r"[1-9]-[1-9]-[1-9]", raw):
        raise ValueError("noncanonical winning combination")
    combo = tuple(map(int, raw.split("-")))
    if len(set(combo)) != 3 or any(c > cars for c in combo):
        raise ValueError("result winner incompatible with cars")
    payout_text = str(row.get("payout_per_100yen", "")).strip()
    if not re.fullmatch(r"\d+", payout_text):
        raise ValueError("missing or invalid actual payout")
    payout = int(payout_text)
    if payout < 100:
        raise ValueError("actual winning payout below stake, cannot verify")
    final_odds = str(row.get("winning_final_odds", "")).strip()
    if final_odds:
        try:
            amount = float(final_odds)
        except ValueError as exc:
            raise ValueError("invalid final winning odds") from exc
        if not math.isfinite(amount) or abs(100 * amount - payout) > 1.1:
            raise ValueError("published final odds and payout disagree")
    date = datetime.strptime(row["date"], "%Y-%m-%d").date()
    return "-".join(map(str, combo)), payout, date


def odds_observations(path, *, limit=1000):
    path = _local(path)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("odds JSONL missing or too large")
    observations = []
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if i > limit:
                raise ValueError("too many odds snapshots in one race file")
            if line.strip():
                observations.append(json.loads(line))
    if not observations:
        raise ValueError("empty odds archive")
    return observations


def eligible_snapshot(odds, probabilities):
    """Validate existing site update, complete 3rentan market and time order."""
    audit = audit_record(odds)
    if audit["status"] != "STRUCTURE_CHECK_PASSED_NOT_SOURCE_VERIFIED":
        raise ValueError("invalid odds archive structure")
    if audit["source_freshness"] != "PAGE_UPDATE_TIMES_WITHIN_WINDOW":
        raise ValueError("stale or unknown page update time at capture")
    if "3rentan" not in audit["markets"] or not audit["markets"]["3rentan"]["structural_coverage_complete"]:
        raise ValueError("no complete 3rentan ticket market")
    observed, deadline = _at(odds["observed_at_jst"]), _at(odds["deadline_at_jst"])
    if (deadline - observed).total_seconds() < 600:
        raise ValueError("odds too close to race deadline")
    validated = validate_probability_input(probabilities, odds)
    return observed, validated


def historical_choice(odds, probabilities, *, threshold=1.30, stress=0.75):
    if not (type(threshold) in (float, int) and math.isfinite(threshold) and threshold > 0):
        raise ValueError("invalid threshold")
    if not (type(stress) in (float, int) and math.isfinite(stress) and 0 < stress <= 1):
        raise ValueError("stress must be in (0,1]")
    _, distribution = eligible_snapshot(odds, probabilities)
    n = odds["cars"]
    projected = project_all_markets(list(range(1, n + 1)), distribution)
    table = odds["markets"]["3rentan"]["quotes"]
    ranked = []
    for combo, price in table.items():
        if type(price) not in (int, float) or price < 1 or not math.isfinite(price):
            raise ValueError("invalid fixed 3rentan odds quote")
        probability = projected["3rentan"].get(combo)
        if probability is None:
            raise ValueError("unrecognized ticket")
        stressed_ev = probability * price * stress
        if stressed_ev + 1e-12 >= threshold:
            ranked.append((stressed_ev, combo, price, probability))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = ranked[0]
    return {
        "combo": selected[1], "quoted_odds": selected[2],
        "estimated_probability": round(selected[3], 9),
        "stressed_expected_return": round(selected[0], 6),
    }


def backtest(odds_dir, probabilities_dir, result_csv, *,
             threshold=1.30, stress=0.75, stake=100):
    odds_dir = _local(odds_dir)
    probabilities_dir = _local(probabilities_dir)
    if type(stake) is not int or stake != 100:
        raise ValueError("only one 100-yen notional ticket supported")
    if not odds_dir.is_dir():
        return {"status": "NOT_READY_NO_ODDS_ARCHIVE", "bets": 0,
                "alert_allowed": False, "bet_allowed": False}
    actual, duplicate_results = results_index(result_csv)
    rejected = Counter()
    selected = []
    scanned = 0
    for file in sorted(odds_dir.glob("*.jsonl")):
        scanned += 1
        if scanned > 10000:
            raise ValueError("too many races")
        try:
            snapshots = odds_observations(file)
            race_id = race_id_from_source(snapshots[0])
            if file.stem != race_id or any(race_id_from_source(x) != race_id for x in snapshots):
                raise ValueError("race ID mismatch in odds file")
            result = actual.get(race_id)
            if result is None:
                rejected["NO_EXACT_RACE_MATCHED_SETTLEMENT"] += 1
                continue
            winner, payout, result_date = settlement(result, snapshots[0]["cars"])
            pfile = probabilities_dir / (race_id + "_top3.json")
            if not pfile.is_file():
                rejected["NO_POINT_IN_TIME_MODEL_PROBABILITIES"] += 1
                continue
            probabilities = _json(pfile)
            generated = _at(probabilities.get("generated_at"))
            # IMPORTANT: choose the EARLIEST eligible quote after model
            # generation. Picking the most favorable price in hindsight
            # would be a lookahead bias.
            candidates = []
            for odds in snapshots:
                try:
                    observed, _ = eligible_snapshot(odds, probabilities)
                except (ValueError, KeyError, TypeError):
                    continue
                if generated <= observed:
                    candidates.append((observed, odds))
            if not candidates:
                rejected["NO_VALID_POINT_IN_TIME_SNAPSHOT"] += 1
                continue
            candidates.sort(key=lambda x: x[0])
            observed, odds = candidates[0]
            if observed.date() != result_date and observed.astimezone(
                    timezone.utc).date() != result_date:
                # Date in results is venue-local; use captured timestamp's
                # original timezone date rather than UTC for this check.
                local_day = datetime.fromisoformat(odds["observed_at_jst"]).date()
                if local_day != result_date:
                    rejected["RESULT_DATE_MISMATCH"] += 1
                    continue
            ticket = historical_choice(odds, probabilities,
                                       threshold=threshold, stress=stress)
            if ticket is None:
                rejected["NO_THRESHOLD_CANDIDATE"] += 1
                continue
            paid = payout if ticket["combo"] == winner else 0
            selected.append({
                "race_id": race_id,
                "race_date": result_date.isoformat(),
                "odds_observed_at": odds["observed_at_jst"],
                "probability_generated_at": probabilities["generated_at"],
                "selected_ticket": ticket["combo"],
                "quoted_preclose_odds": ticket["quoted_odds"],
                "expected_probability": ticket["estimated_probability"],
                "stressed_expected_return": ticket["stressed_expected_return"],
                "hit": ticket["combo"] == winner,
                "settled_payout_yen": paid, "stake_yen": stake,
                "profit_yen": paid - stake,
            })
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, OSError):
            rejected["INVALID_OR_AMBIGUOUS_DATA"] += 1
    selected.sort(key=lambda row: (row["race_date"], row["odds_observed_at"], row["race_id"]))
    total_stake = len(selected) * stake
    total_payout = sum(row["settled_payout_yen"] for row in selected)
    equity = peak = drawdown = 0
    for row in selected:
        equity += row["profit_yen"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    count = len(selected)
    return {
        "status": ("RESEARCH_BACKTEST_UNVALIDATED" if selected
                   else "NOT_READY_NO_SETTLEABLE_POINT_IN_TIME_BETS"),
        "source_odds_files_scanned": scanned,
        "result_rows_with_ambiguous_duplicate_ids": duplicate_results,
        "eligible_bet_count": count,
        "races_with_bets": count,
        "hits": sum(int(row["hit"]) for row in selected),
        "hit_rate": round(sum(int(row["hit"]) for row in selected) / count, 6) if count else None,
        "notional_stake_yen": total_stake,
        "settled_actual_payout_yen": total_payout,
        "net_profit_yen": total_payout - total_stake,
        "realized_return_ratio": round(total_payout / total_stake, 6) if count else None,
        "maximum_drawdown_yen": drawdown,
        "threshold": threshold, "stress_factor": stress,
        "rejected_counts": dict(sorted(rejected.items())),
        "selections": selected,
        "alert_allowed": False, "bet_allowed": False, "model_verified": False,
        "caveats": [
            "This is historical simulated settlement, NOT proven future ROI.",
            "Only 3rentan supported: other market payouts/refunds are not in existing results.",
            "100 yen for one preselected ticket per race; no commissions/slippage/late odds guarantee.",
            "Quoted pre-race odds are used ONLY to select; realized payout uses final settlement CSV.",
            "Source timestamps and probabilities are self-reported and not independently authenticated.",
            "Retrospective model tuning or cherry-picked races invalidates out-of-sample inference.",
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--odds-dir", type=Path, default=Path("local_data/odds_snapshots"))
    ap.add_argument("--probabilities-dir", type=Path, default=Path("local_data/models"))
    ap.add_argument("--results", type=Path, default=Path("exports/results_3rentan.csv"))
    ap.add_argument("--output", type=Path, default=Path("local_data/backtests/3rentan.json"))
    ap.add_argument("--threshold", type=float, default=1.30)
    ap.add_argument("--stress", type=float, default=0.75)
    args = ap.parse_args()
    try:
        target = _local(args.output)
        if target.suffix != ".json":
            raise ValueError("report output must be JSON")
        summary = backtest(args.odds_dir, args.probabilities_dir, args.results,
                           threshold=args.threshold, stress=args.stress)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2,
                                     allow_nan=False) + "\n", encoding="utf-8")
        safe_view = {key: val for key, val in summary.items() if key != "selections"}
        safe_view["output"] = str(target)
        print(json.dumps(safe_view, ensure_ascii=False, indent=2))
        return 0 if summary["status"] == "RESEARCH_BACKTEST_UNVALIDATED" else 2
    except (ValueError, OSError, TypeError, json.JSONDecodeError) as exc:
        print("BACKTEST_NOT_READY: " + type(exc).__name__ + ": " + str(exc)[:140],
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate 3-rentan results and optional popularity-ordered odds.

Usage: python tools/validate.py results.csv [odds.csv]
Supports simultaneous winning tickets in the same race (dead heat).
Expects: race_id,winning_combination,payout_per_100yen,winning_final_odds
Optional odds: race_id,rank,combination,final_odds (or displayed_odds/odds),hit
"""
import csv
import math
import sys
from collections import defaultdict


def load_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fp:
        return list(csv.DictReader(fp))


def odds_number(row):
    for key in ("final_odds", "displayed_odds", "odds"):
        value = row.get(key)
        if value not in (None, ""):
            return float(value)
    raise ValueError("odds value missing")


def check_results(path):
    winners = defaultdict(dict)
    race_types = defaultdict(set)
    for row in load_csv(path):
        rid = row["race_id"]
        combo = row["winning_combination"]
        picks = combo.split("-")
        assert len(picks) == 3 and len(set(picks)) == 3, (rid, combo)
        assert all(s.isdigit() and int(s) >= 1 for s in picks), (rid, combo)
        assert combo not in winners[rid], (rid, combo, "duplicate")
        payout = int(row["payout_per_100yen"])
        settled_odds = float(row["winning_final_odds"])
        assert payout > 0 and abs(payout / 100 - settled_odds) < 0.011, (rid, combo)
        winners[rid][combo] = settled_odds
        race_types[rid].add(row.get("result_type", "normal"))
    for rid, combos in winners.items():
        if len(combos) > 1:
            assert race_types[rid] == {"dead_heat_first_place"}, (rid, "multiple winners require dead_heat_first_place")
    return winners


def check_odds(path, winners):
    groups = defaultdict(list)
    for row in load_csv(path):
        groups[row["race_id"]].append(row)
    top50 = top100 = 0
    for rid, rows in groups.items():
        assert rid in winners, (rid, "no settled result")
        ranks = [int(row["rank"]) for row in rows]
        combos = [row["combination"] for row in rows]
        assert len(set(ranks)) == len(rows) and len(set(combos)) == len(rows), rid
        assert sorted(ranks) == list(range(1, len(rows) + 1)), (rid, "rank gap")
        ordered = sorted(rows, key=lambda row: int(row["rank"]))
        last = 0.0
        for row in ordered:
            combo = row["combination"]
            assert len(combo.split("-")) == 3 and len(set(combo.split("-"))) == 3, (rid, combo)
            value = odds_number(row)
            assert math.isfinite(value) and value > 0 and value + 1e-9 >= last, (rid, combo)
            last = value
            if combo in winners[rid]:
                assert abs(value - winners[rid][combo]) < 0.11, (rid, combo, "payout mismatch")
            if row.get("hit", "") not in ("", None):
                assert int(row["hit"]) == int(combo in winners[rid]), (rid, combo, "hit flag mismatch")
        top50 += len(rows) >= 50
        top100 += len(rows) >= 100
    print(f"odds races: {len(groups)}, Top50-or-more: {top50}, Top100-or-more: {top100}")


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    winners = check_results(sys.argv[1])
    print(f"settled races: {len(winners)}, winning payouts: {sum(map(len, winners.values()))}")
    if len(sys.argv) == 3:
        check_odds(sys.argv[2], winners)
    print("Structural checks passed; original source authenticity needs separate verification.")


if __name__ == "__main__":
    main()

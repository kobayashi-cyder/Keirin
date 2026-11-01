#!/usr/bin/env python3
"""Validate settled 3連単 outcomes and optional Top100 odds CSV.
Usage: python tools/validate.py data/aomori_results_36.csv [data/top100.csv]
Odds columns: race_id, rank, combination, final_odds (or odds)
"""
import csv
import math
import sys
from collections import defaultdict

def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    results = read(sys.argv[1])
    winners = {}
    for row in results:
        key = row["race_id"]
        combo = row["winning_combination"]
        picks = combo.split("-")
        assert len(picks) == 3 and len(set(picks)) == 3 and all(x.isdigit() for x in picks), (key, combo)
        assert key not in winners, key
        payout = int(row["payout_per_100yen"])
        odds = float(row["winning_final_odds"])
        assert payout > 0 and abs(payout/100 - odds) < 0.011, key
        winners[key] = (combo, odds)
    print("settled races:", len(winners))
    if len(sys.argv) < 3:
        return
    groups = defaultdict(list)
    for row in read(sys.argv[2]):
        groups[row["race_id"]].append(row)
    complete = 0
    for key, rows in groups.items():
        assert key in winners, f"unmatched race: {key}"
        ranks = [int(x["rank"]) for x in rows]
        combos = [x["combination"] for x in rows]
        vals = [float(x.get("final_odds") or x["odds"]) for x in rows]
        assert len(ranks) == len(set(ranks)) and len(combos) == len(set(combos)), key
        assert sorted(ranks) == list(range(1, len(rows)+1)), key
        assert all(math.isfinite(v) and v > 1 for v in vals), key
        ordered = sorted(rows, key=lambda x: int(x["rank"]))
        v = [float(x.get("final_odds") or x["odds"]) for x in ordered]
        assert v == sorted(v), key
        hit = [x for x in rows if x["combination"] == winners[key][0]]
        if hit:
            assert abs(float(hit[0].get("final_odds") or hit[0]["odds"]) - winners[key][1]) < 0.11, key
        complete += len(rows) == 100
    print("odds races:", len(groups), "Top100 complete:", complete)
    print("All supplied records passed structural checks; source authenticity is not proven by this script.")

if __name__ == "__main__":
    main()

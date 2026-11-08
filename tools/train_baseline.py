#!/usr/bin/env python3
"""Train a research-only Plackett-Luce top-three ranker from real pre-race CSV.

No web access, no OpenAI calls, no placing bets, and no Discord alerts.
This is a transparent baseline to test whether *pre-race* rider features
contain predictive signal. Passing an evaluation never approves a model.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from pre_race_readiness import FEATURES, index_results, validate_race, time_with_zone
from build_multiview_features import EXTRA_FEATURE_NAMES

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "paper_trading"))
from all_markets import plackett_luce_demo, project_all_markets

MIN_RACES = 500
MIN_DAYS = 30
FEATURE_NAMES = tuple(FEATURES)


def read_training_rows(features_path, results_path, *, feature_names=FEATURE_NAMES):
    """Join before-close snapshots with unambiguous result labels by race_id."""
    if not features_path.is_file() or not results_path.is_file():
        return [], {"missing_inputs": 1}
    results = index_results(results_path)
    by_race = defaultdict(list)
    with features_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"ingested_at_jst", "race_id", "car_no", "race_date",
                    "deadline_at_jst", *feature_names}
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            return [], {"invalid_or_missing_feature_columns": 1}
        for row in reader:
            by_race[row["race_id"]].append(row)
    out, reasons = [], Counter()
    for race_id, rows in sorted(by_race.items()):
        try:
            day = validate_race(rows, results)
            deadline = time_with_zone(rows[0]["deadline_at_jst"])
            ingested = {time_with_zone(row["ingested_at_jst"]) for row in rows}
            if len(ingested) != 1 or next(iter(ingested)) >= deadline:
                raise ValueError("not demonstrably ingested before deadline")
            finish = next(iter(results[race_id]))
            riders = {}
            for row in rows:
                car = int(row["car_no"])
                riders[car] = tuple(float(row[col]) for col in feature_names)
            out.append({"race_id": race_id, "date": day, "features": riders,
                        "top3": finish})
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reasons[str(exc)] += 1
    return out, dict(sorted(reasons.items()))


def time_split(races):
    """Split by entire date, never by rows or within one race-day."""
    days = sorted({r["date"] for r in races})
    if len(days) < 3:
        raise ValueError("at least three distinct days required")
    first = max(1, int(len(days) * .7))
    second = max(first + 1, int(len(days) * .85))
    second = min(second, len(days) - 1)
    train_days, val_days, test_days = (set(days[:first]), set(days[first:second]),
                                       set(days[second:]))
    return ([r for r in races if r["date"] in train_days],
            [r for r in races if r["date"] in val_days],
            [r for r in races if r["date"] in test_days])


def fit_scaler(train):
    vectors = [row for race in train for row in race["features"].values()]
    dimensions = len(vectors[0])
    mean = [math.fsum(v[j] for v in vectors) / len(vectors)
            for j in range(dimensions)]
    scale = []
    for j in range(dimensions):
        variance = math.fsum((v[j] - mean[j]) ** 2 for v in vectors) / len(vectors)
        scale.append(max(variance ** .5, 1e-8))
    return mean, scale


def standardized(features, mean, scale):
    return {car: tuple((v[j] - mean[j]) / scale[j] for j in range(len(mean)))
            for car, v in features.items()}


def score_riders(features, weights, mean, scale, temperature=1.0):
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    x = standardized(features, mean, scale)
    return {car: sum(a * b for a, b in zip(weights, vals)) / temperature
            for car, vals in x.items()}


def race_nll_gradient(race, weights, mean, scale, temperature=1.0):
    """Negative sequential choice log likelihood and gradient d/d weights."""
    x = standardized(race["features"], mean, scale)
    scores = {car: sum(a * b for a, b in zip(weights, v)) / temperature
              for car, v in x.items()}
    remain = set(x)
    loss = 0.0
    gradient = [0.0] * len(weights)
    for actual in race["top3"]:
        maximum = max(scores[c] for c in remain)
        expv = {c: math.exp(scores[c] - maximum) for c in remain}
        denom = math.fsum(expv.values())
        probabilities = {c: v / denom for c, v in expv.items()}
        loss += maximum + math.log(denom) - scores[actual]
        for j in range(len(weights)):
            expected = math.fsum(probabilities[c] * x[c][j] for c in remain)
            gradient[j] += (expected - x[actual][j]) / temperature
        remain.remove(actual)
    return loss, gradient


def mean_nll(races, weights, mean, scale, temperature=1.0):
    if not races:
        raise ValueError("empty evaluation set")
    return math.fsum(race_nll_gradient(r, weights, mean, scale, temperature)[0]
                     for r in races) / len(races)


def uniform_nll(races):
    return math.fsum(math.log(n * (n - 1) * (n - 2))
                     for n in (len(r["features"]) for r in races)) / len(races)


def fit_plackett_luce(train, *, iterations=160, step=0.045, l2=0.007):
    """Regularised Adam optimisation, no third-party packages required."""
    if not train:
        raise ValueError("cannot fit an empty training set")
    mean, scale = fit_scaler(train)
    n_features = len(mean)
    weights = [0.0] * n_features
    m, v = [0.0] * n_features, [0.0] * n_features
    beta1, beta2 = .9, .999
    for t in range(1, iterations + 1):
        gradients = [0.0] * n_features
        for race in train:
            _, g = race_nll_gradient(race, weights, mean, scale)
            for j, item in enumerate(g):
                gradients[j] += item
        for j in range(n_features):
            g = gradients[j] / len(train) + l2 * weights[j]
            m[j] = beta1 * m[j] + (1 - beta1) * g
            v[j] = beta2 * v[j] + (1 - beta2) * g * g
            adjusted_m = m[j] / (1 - beta1 ** t)
            adjusted_v = v[j] / (1 - beta2 ** t)
            weights[j] -= step * adjusted_m / (adjusted_v ** .5 + 1e-8)
    return {"weights": weights, "mean": mean, "scale": scale}


def fit_temperature(val, model):
    options = (0.5, 0.75, 1., 1.25, 1.5, 2., 2.5, 3.)
    return min(options, key=lambda z: mean_nll(
        val, model["weights"], model["mean"], model["scale"], z
    ))


def research_model(races, *, min_races=MIN_RACES, min_days=MIN_DAYS,
                   feature_names=FEATURE_NAMES):
    """Strict data-volume gate, immutable time split, held-out evaluation."""
    unique_days = len({r["date"] for r in races})
    if len(races) < min_races or unique_days < min_days:
        return {"status": "NOT_READY", "training_allowed": False,
                "eligible_races": len(races), "distinct_days": unique_days,
                "min_races": min_races, "min_days": min_days}
    train, validation, test = time_split(races)
    if min(len(train), len(validation), len(test)) < 15:
        return {"status": "NOT_READY", "training_allowed": False,
                "reason": "chronological validation/test partitions too small"}
    model = fit_plackett_luce(train)
    temperature = fit_temperature(validation, model)
    test_nll = mean_nll(test, model["weights"], model["mean"], model["scale"], temperature)
    uniform = uniform_nll(test)
    validation_nll = mean_nll(validation, model["weights"],
                              model["mean"], model["scale"], temperature)
    return {
        "status": "RESEARCH_BASELINE_EVALUATED",
        "training_allowed": False,
        "model_verified": False,
        "alert_allowed": False,
        "model_type": "linear_plackett_luce_top3",
        "features": feature_names,
        "scaler_means": model["mean"],
        "scaler_scales": model["scale"],
        "weights": model["weights"],
        "calibration_temperature": temperature,
        "data_ranges": {
            name: {"races": len(part),
                   "days": len({r["date"] for r in part}),
                   "from": min(r["date"] for r in part),
                   "to": max(r["date"] for r in part)}
            for name, part in (("train", train), ("validation", validation), ("test", test))
        },
        "validation_top3_nll": round(validation_nll, 6),
        "test_top3_nll": round(test_nll, 6),
        "test_uniform_top3_nll": round(uniform, 6),
        "beats_uniform_baseline": test_nll < uniform,
        "note": "Historical test is not an independent betting ROI, authorization, or forward validation.",
    }


def predict_joint_top3(model, rider_features):
    """Only for offline research; NOT an approved pick/notification."""
    if model.get("status") != "RESEARCH_BASELINE_EVALUATED":
        raise ValueError("model not evaluated")
    features = {int(k): tuple(float(v) for v in row)
                for k, row in rider_features.items()}
    cars = sorted(features)
    if cars != list(range(1, len(cars) + 1)) or len(cars) not in (7, 8, 9):
        raise ValueError("rider feature vectors must cover 7, 8 or 9 cars")
    scores = score_riders(features, model["weights"], model["scaler_means"],
                          model["scaler_scales"], model["calibration_temperature"])
    strengths = {car: math.exp(max(-20., min(20., score)))
                 for car, score in scores.items()}
    top3 = plackett_luce_demo(cars, strengths)
    return {"joint_top3": {"-".join(map(str, order)): p for order, p in top3.items()},
            "market_probabilities": project_all_markets(cars, {
                "-".join(map(str, order)): p for order, p in top3.items()
            }), "alert_allowed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path,
                        default=Path("local_data/pre_race_features.csv"))
    parser.add_argument("--results", type=Path,
                        default=Path("exports/results_3rentan.csv"))
    parser.add_argument("--output", type=Path,
                        default=Path("local_data/models/research_baseline.json"))
    parser.add_argument("--multiview", action="store_true",
                        help="train on 14 extra historical/video features in an audited multiview CSV")
    args = parser.parse_args()
    selected = FEATURE_NAMES + EXTRA_FEATURE_NAMES if args.multiview else FEATURE_NAMES
    races, reasons = read_training_rows(args.features, args.results,
                                         feature_names=selected)
    result = research_model(races, feature_names=selected)
    result["excluded_reasons"] = reasons
    if result["status"] == "RESEARCH_BASELINE_EVALUATED":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        result["output"] = str(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "RESEARCH_BASELINE_EVALUATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

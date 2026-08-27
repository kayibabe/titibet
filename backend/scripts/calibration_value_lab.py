"""Walk-forward calibration and value research for TiTiBet.

This is a research-only lab. It never changes live signal gates or staking rules.
For every test block, calibrator selection is trained only on observations that
precede that block. Value is then measured from the calibrated probability and
the earliest stored execution price.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from model_quality_lab_v2 import run_lab
from app.quant.calibration import evaluate_calibrator, select_calibrator
from app.quant.probability import edge, expected_value, fair_odds

METHODS = ("identity", "platt", "beta", "isotonic")
BUCKETS = ((0.00, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, float("inf")))


def _bucket(ev: float) -> str:
    for low, high in BUCKETS:
        if low <= ev < high:
            return f"{low:.0%}-{high:.0%}" if high != float("inf") else f">={low:.0%}"
    return "below_0%"


def _run_engine_walk_forward(rows: list[dict], train_size: int, test_size: int, min_train: int) -> dict:
    rows = sorted(rows, key=lambda r: (r.get("event_date") or "", r.get("fixture_id") or 0))
    if len(rows) <= train_size:
        return {"n": 0, "folds": [], "summary": None, "value_buckets": {}}

    folds: list[dict] = []
    value_rows: list[dict] = []
    start = train_size
    while start < len(rows):
        train_start = max(0, start - train_size)
        train = rows[train_start:start]
        test = rows[start:min(len(rows), start + test_size)]
        if len(train) < min_train or not test:
            break
        probs = [r["prob"] for r in train]
        outcomes = [r["outcome"] for r in train]
        try:
            chosen = select_calibrator(probs, outcomes, methods=METHODS, min_train=min_train)
        except ValueError:
            continue

        test_probs = [r["prob"] for r in test]
        test_outcomes = [r["outcome"] for r in test]
        calibrated = chosen.calibrator.predict(test_probs)
        raw = evaluate_calibrator(
            # Identity is intentionally evaluated directly on the untouched probabilities.
            __import__("app.quant.calibration", fromlist=["IdentityCalibrator"]).IdentityCalibrator(),
            test_probs,
            test_outcomes,
        )
        calibrated_report = evaluate_calibrator(chosen.calibrator, test_probs, test_outcomes)
        fold = {
            "start": start,
            "end": start + len(test),
            "train_n": len(train),
            "test_n": len(test),
            "selected_method": chosen.method,
            "raw_brier": round(raw.brier, 8),
            "calibrated_brier": round(calibrated_report.brier, 8),
            "raw_log_loss": round(raw.log_loss, 8),
            "calibrated_log_loss": round(calibrated_report.log_loss, 8),
            "raw_calibration_error": round(raw.mean_absolute_calibration_error, 8),
            "calibrated_calibration_error": round(calibrated_report.mean_absolute_calibration_error, 8),
        }
        folds.append(fold)

        for source, calibrated_p, original in zip(test, calibrated.tolist(), test):
            odds = source.get("odds")
            if odds is None or odds <= 1:
                continue
            ev = expected_value(float(calibrated_p), float(odds))
            value_rows.append({
                "ev": ev,
                "edge": edge(float(calibrated_p), float(odds)),
                "fair_odds": fair_odds(float(calibrated_p)),
                "odds": float(odds),
                "outcome": int(original["outcome"]),
            })
        start += len(test)

    if not folds:
        return {"n": 0, "folds": [], "summary": None, "value_buckets": {}}

    n = sum(f["test_n"] for f in folds)
    summary = {
        "n": n,
        "raw_brier": sum(f["raw_brier"] * f["test_n"] for f in folds) / n,
        "calibrated_brier": sum(f["calibrated_brier"] * f["test_n"] for f in folds) / n,
        "raw_log_loss": sum(f["raw_log_loss"] * f["test_n"] for f in folds) / n,
        "calibrated_log_loss": sum(f["calibrated_log_loss"] * f["test_n"] for f in folds) / n,
        "raw_calibration_error": sum(f["raw_calibration_error"] * f["test_n"] for f in folds) / n,
        "calibrated_calibration_error": sum(f["calibrated_calibration_error"] * f["test_n"] for f in folds) / n,
        "method_counts": {
            method: sum(1 for f in folds if f["selected_method"] == method)
            for method in sorted({f["selected_method"] for f in folds})
        },
    }

    buckets: dict[str, dict] = {}
    for label in ["0%-2%", "2%-5%", "5%-10%", "10%-15%", ">=15%", "below_0%"]:
        rs = [r for r in value_rows if _bucket(r["ev"]) == label]
        if not rs:
            continue
        profit = sum((r["odds"] - 1) if r["outcome"] else -1 for r in rs)
        buckets[label] = {
            "n": len(rs),
            "wins": sum(r["outcome"] for r in rs),
            "hit_rate": sum(r["outcome"] for r in rs) / len(rs),
            "mean_ev": sum(r["ev"] for r in rs) / len(rs),
            "mean_edge": sum(r["edge"] for r in rs) / len(rs),
            "roi": profit / len(rs),
        }
    return {"n": n, "folds": folds, "summary": summary, "value_buckets": buckets}


async def run_value_lab(
    date_from: date | None,
    date_to: date | None,
    market: str | None,
    *,
    train_size: int = 100,
    test_size: int = 25,
    min_train: int = 50,
) -> dict:
    report = await run_lab(date_from, date_to, market, include_observations=True)
    raw = report.pop("observations", {})
    results: dict[str, dict] = {}
    for market_name, market_rows in raw.items():
        results[market_name] = {}
        for engine in ("bayesian", "poisson", "ensemble"):
            rows = [r for r in market_rows if r.get("engine") == engine]
            if rows:
                results[market_name][engine] = _run_engine_walk_forward(
                    rows, train_size=train_size, test_size=test_size, min_train=min_train
                )

    return {
        "scope": report["scope"],
        "fixtures_seen": report["fixtures_seen"],
        "markets": results,
        "methodology": {
            "research_only": True,
            "production_rules_changed": False,
            "adaptive_suppression_applied": False,
            "point_in_time_form": True,
            "walk_forward": True,
            "train_size": train_size,
            "test_size": test_size,
            "min_train": min_train,
            "calibration_methods": list(METHODS),
            "selection_rule": "lowest training-window Brier, then log loss, with identity baseline",
            "value_buckets": ["0%-2%", "2%-5%", "5%-10%", "10%-15%", ">=15%", "below_0%"],
            "important": "calibrated EV is research evidence only; no production bet gate or staking rule is modified",
        },
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="date_from", type=date.fromisoformat)
    p.add_argument("--to", dest="date_to", type=date.fromisoformat)
    p.add_argument("--market")
    p.add_argument("--train-size", type=int, default=100)
    p.add_argument("--test-size", type=int, default=25)
    p.add_argument("--min-train", type=int, default=50)
    p.add_argument("--output", default="calibration_value_lab.json")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    result = await run_value_lab(
        args.date_from, args.date_to, args.market,
        train_size=args.train_size,
        test_size=args.test_size,
        min_train=args.min_train,
    )
    output = Path(args.output)
    if not output.is_absolute():
        output = Path.cwd() / output
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nSaved calibration/value report to: {output.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

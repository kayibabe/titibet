"""Q2 market edge discovery and production eligibility.

Research-only consumer of model_quality_lab.json. No production configuration or
DB state is modified. EV is the primary pricing-edge measure because averaging
probabilities and averaging implied probabilities separately can be misleading
when odds vary between observations.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ENGINES = ("bayesian", "poisson", "ensemble")
DEFAULT_MIN_N = 100
DEFAULT_MIN_EDGE = 0.05
DEFAULT_MAX_CALIBRATION_ERROR = 0.10


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def num(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    return float(value) if finite(value) else None


def classify(row: dict[str, Any], *, min_n: int, min_edge: float,
             max_calibration_error: float) -> tuple[str, list[str]]:
    n = int(row.get("n") or 0)
    ev = num(row, "mean_ev")
    roi = num(row, "roi")
    cal = num(row, "calibration_error")
    pos_ev = num(row, "positive_ev_rate")
    brier = num(row, "brier")
    log_loss = num(row, "log_loss")
    reasons: list[str] = []

    if n < min_n:
        reasons.append(f"sample_n={n}<{min_n}")
    if ev is None:
        reasons.append("mean_ev_unavailable")
    elif ev < min_edge:
        reasons.append(f"mean_ev={ev:.3f}<{min_edge:.3f}")
    if roi is None:
        reasons.append("roi_unavailable")
    elif roi < 0:
        reasons.append(f"roi={roi:.3f}<0")
    if cal is not None and cal > max_calibration_error:
        reasons.append(f"calibration_error={cal:.3f}>{max_calibration_error:.3f}")
    if brier is None or log_loss is None:
        reasons.append("forecast_quality_incomplete")

    # Conditional is reserved for sufficiently large samples with positive EV
    # and ROI, but a calibration/forecast-quality weakness prevents approval.
    core_ok = n >= min_n and ev is not None and ev >= min_edge and roi is not None and roi >= 0
    quality_ok = (cal is None or cal <= max_calibration_error) and brier is not None and log_loss is not None
    if core_ok and quality_ok:
        return "APPROVED", [f"mean_ev={ev:.3f}", f"roi={roi:.3f}", f"n={n}"]
    if core_ok:
        return "CONDITIONAL", reasons
    return "REJECT", reasons


def quality_rank(row: dict[str, Any]) -> float:
    brier = num(row, "brier")
    log_loss = num(row, "log_loss")
    cal = num(row, "calibration_error")
    roi = num(row, "roi")
    ev = num(row, "mean_ev")
    n = int(row.get("n") or 0)
    score = 0.0
    if n >= DEFAULT_MIN_N:
        score += 2
    if brier is not None:
        score += max(0.0, 1.0 - brier)
    if log_loss is not None:
        score += max(0.0, 1.0 - log_loss)
    if cal is not None:
        score += max(0.0, 1.0 - cal)
    if roi is not None:
        score += max(-1.0, min(1.0, roi))
    if ev is not None:
        score += max(-1.0, min(1.0, ev))
    return round(score, 6)


def run(report: dict[str, Any], *, min_n: int = DEFAULT_MIN_N,
        min_edge: float = DEFAULT_MIN_EDGE,
        max_calibration_error: float = DEFAULT_MAX_CALIBRATION_ERROR) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for market, engines in sorted((report.get("markets") or {}).items()):
        for engine in ENGINES:
            source = engines.get(engine)
            if not isinstance(source, dict):
                continue
            status, reasons = classify(
                source, min_n=min_n, min_edge=min_edge,
                max_calibration_error=max_calibration_error,
            )
            model_edge = (
                round(float(source["mean_probability"]) - float(source["mean_implied_probability"]), 6)
                if finite(source.get("mean_probability")) and finite(source.get("mean_implied_probability"))
                else None
            )
            rows.append({
                "market": market,
                "engine": engine,
                "status": status,
                "n": source.get("n"),
                "wins": source.get("wins"),
                "hit_rate": source.get("hit_rate"),
                "brier": source.get("brier"),
                "log_loss": source.get("log_loss"),
                "calibration_error": source.get("calibration_error"),
                "mean_probability": source.get("mean_probability"),
                "mean_implied_probability": source.get("mean_implied_probability"),
                "mean_probability_gap": model_edge,
                "mean_ev": source.get("mean_ev"),
                "positive_ev_rate": source.get("positive_ev_rate"),
                "roi": source.get("roi"),
                "quality_rank": quality_rank(source),
                "reasons": reasons,
            })

    status_order = {"APPROVED": 0, "CONDITIONAL": 1, "REJECT": 2}
    rows.sort(key=lambda x: (status_order[x["status"]], -(x["quality_rank"] or 0), x["market"], x["engine"]))
    return {
        "report_type": "q2_market_edge_discovery_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_scope": report.get("scope"),
        "fixtures_seen": report.get("fixtures_seen"),
        "parameters": {
            "min_n": min_n,
            "min_probability_edge": min_edge,
            "max_calibration_error": max_calibration_error,
        },
        "production_rules_changed": False,
        "rows": rows,
        "summary": {
            "approved": sum(x["status"] == "APPROVED" for x in rows),
            "conditional": sum(x["status"] == "CONDITIONAL" for x in rows),
            "rejected": sum(x["status"] == "REJECT" for x in rows),
        },
        "methodology": {
            "pricing_edge": "mean EV is primary; probability gap is reported diagnostically only",
            "profitability_gate": "aggregate historical ROI must be non-negative",
            "sample_gate": "minimum observation count is required",
            "quality_gate": "calibration error <= configured maximum and Brier/log-loss available",
            "note": "Research-only. This report never changes production suppression, thresholds, staking, or ranking.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="model_quality_lab.json")
    parser.add_argument("--output", default="market_edge_discovery.json")
    parser.add_argument("--min-n", type=int, default=DEFAULT_MIN_N)
    parser.add_argument("--min-edge", type=float, default=DEFAULT_MIN_EDGE)
    parser.add_argument("--max-calibration-error", type=float, default=DEFAULT_MAX_CALIBRATION_ERROR)
    args = parser.parse_args()
    source = Path(args.input)
    target = Path(args.output)
    result = run(json.loads(source.read_text(encoding="utf-8")), min_n=args.min_n,
                 min_edge=args.min_edge, max_calibration_error=args.max_calibration_error)
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nSaved Q2 market-edge report to: {target.resolve()}")


if __name__ == "__main__":
    main()

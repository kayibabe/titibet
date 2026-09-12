"""Q2 market edge discovery and production eligibility lab.

Consumes the ungated model-quality report and converts model/pricing metrics into
an auditable market eligibility matrix. This is research-only: it never mutates
production rules or database state.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ENGINES = ("bayesian", "poisson", "ensemble")
EDGE_THRESHOLDS = (0.03, 0.05, 0.07, 0.10)
DEFAULT_MIN_N = 100
DEFAULT_MIN_PRICED_N = 100
DEFAULT_MAX_CAL_ERROR = 0.10


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _metric(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    return float(value) if _finite(value) else None


def _rank_score(row: dict[str, Any], min_n: int) -> float:
    """Quality score used only to rank candidates, not to declare profitability."""
    n = _metric(row, "n") or 0.0
    brier = _metric(row, "brier")
    log_loss = _metric(row, "log_loss")
    cal = _metric(row, "calibration_error")
    roi = _metric(row, "roi")
    pos_ev = _metric(row, "positive_ev_rate")

    score = 0.0
    if n >= min_n:
        score += 2.0
    if brier is not None:
        score += max(0.0, 1.0 - brier)
    if log_loss is not None:
        score += max(0.0, 1.0 - log_loss)
    if cal is not None:
        score += max(0.0, 1.0 - cal)
    if roi is not None:
        score += max(-1.0, min(1.0, roi))
    if pos_ev is not None:
        score += pos_ev
    return round(score, 6)


def classify(row: dict[str, Any], *, min_n: int, min_priced_n: int,
             min_edge: float, max_cal_error: float) -> tuple[str, list[str]]:
    n = int(row.get("n") or 0)
    cal = _metric(row, "calibration_error")
    roi = _metric(row, "roi")
    mean_ev = _metric(row, "mean_ev")
    pos_ev = _metric(row, "positive_ev_rate")
    mean_prob = _metric(row, "mean_probability")
    mean_imp = _metric(row, "mean_implied_probability")

    reasons: list[str] = []
    if n < min_n:
        reasons.append(f"sample_n={n}<{min_n}")
    if mean_imp is None:
        reasons.append("no_priced_observations")
    if mean_imp is not None and n < min_priced_n:
        reasons.append(f"priced_n={n}<{min_priced_n}")
    if cal is not None and cal > max_cal_error:
        reasons.append(f"calibration_error={cal:.3f}>{max_cal_error:.3f}")

    observed_edge = None
    if mean_prob is not None and mean_imp is not None:
        observed_edge = mean_prob - mean_imp
    elif mean_ev is not None:
        observed_edge = mean_ev

    if observed_edge is None:
        reasons.append("edge_unavailable")
    elif observed_edge < min_edge:
        reasons.append(f"edge={observed_edge:.3f}<{min_edge:.3f}")

    # Profitability is intentionally conservative: historical ROI must also be
    # non-negative before a market can be APPROVED. Positive EV rate alone is
    # insufficient because it may coexist with a negative aggregate ROI.
    if roi is None:
        reasons.append("roi_unavailable")
    elif roi < 0:
        reasons.append(f"roi={roi:.3f}<0")

    if reasons:
        # Research candidates remain visible when they have an edge but fail one
        # or more robustness gates. This prevents small-sample winners becoming
        # accidental production rules.
        if observed_edge is not None and observed_edge >= min_edge and roi is not None and roi >= 0:
            return "CONDITIONAL", reasons
        return "REJECT", reasons
    return "APPROVED", [f"edge={observed_edge:.3f}", f"roi={roi:.3f}", f"n={n}"]


def run(report: dict[str, Any], *, min_n: int, min_priced_n: int,
        min_edge: float, max_cal_error: float) -> dict[str, Any]:
    markets = report.get("markets") or {}
    rows: list[dict[str, Any]] = []

    for market, engines in sorted(markets.items()):
        for engine in ENGINES:
            row = engines.get(engine)
            if not isinstance(row, dict):
                continue
            status, reasons = classify(
                row, min_n=min_n, min_priced_n=min_priced_n,
                min_edge=min_edge, max_cal_error=max_cal_error,
            )
            rows.append({
                "market": market,
                "engine": engine,
                "status": status,
                "n": row.get("n"),
                "wins": row.get("wins"),
                "hit_rate": row.get("hit_rate"),
                "brier": row.get("brier"),
                "log_loss": row.get("log_loss"),
                "calibration_error": row.get("calibration_error"),
                "mean_probability": row.get("mean_probability"),
                "mean_implied_probability": row.get("mean_implied_probability"),
                "observed_probability_edge": (
                    round(float(row["mean_probability"]) - float(row["mean_implied_probability"]), 6)
                    if _finite(row.get("mean_probability")) and _finite(row.get("mean_implied_probability"))
                    else None
                ),
                "mean_ev": row.get("mean_ev"),
                "positive_ev_rate": row.get("positive_ev_rate"),
                "roi": row.get("roi"),
                "ranking_score": _rank_score(row, min_n),
                "reasons": reasons,
            })

    status_order = {"APPROVED": 0, "CONDITIONAL": 1, "REJECT": 2}
    rows.sort(key=lambda r: (status_order[r["status"]], -(r["ranking_score"] or 0), r["market"], r["engine"]))

    by_market: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = by_market.setdefault(row["market"], {"engines": {}, "best_engine": None})
        item["engines"][row["engine"]] = row
    for market, item in by_market.items():
        candidates = [r for r in item["engines"].values() if r["status"] != "REJECT"]
        item["best_engine"] = max(candidates, key=lambda r: r["ranking_score"], default=None)

    return {
        "report_type": "q2_market_edge_discovery",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_scope": report.get("scope"),
        "fixtures_seen": report.get("fixtures_seen"),
        "parameters": {
            "min_n": min_n,
            "min_priced_n": min_priced_n,
            "min_probability_edge": min_edge,
            "max_calibration_error": max_cal_error,
            "edge_thresholds_research": EDGE_THRESHOLDS,
        },
        "production_rules_changed": False,
        "rows": rows,
        "by_market": by_market,
        "summary": {
            "approved": sum(r["status"] == "APPROVED" for r in rows),
            "conditional": sum(r["status"] == "CONDITIONAL" for r in rows),
            "rejected": sum(r["status"] == "REJECT" for r in rows),
        },
        "methodology": {
            "purpose": "market research and eligibility screening only",
            "edge": "mean model probability minus mean bookmaker implied probability when both exist; mean EV fallback",
            "profitability_gate": "aggregate historical ROI must be non-negative",
            "small_sample_policy": "small samples cannot be approved",
            "note": "This report does not change production suppression, thresholds, staking, or signal ranking.",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="model_quality_lab.json")
    parser.add_argument("--output", default="market_edge_discovery.json")
    parser.add_argument("--min-n", type=int, default=DEFAULT_MIN_N)
    parser.add_argument("--min-priced-n", type=int, default=DEFAULT_MIN_PRICED_N)
    parser.add_argument("--min-edge", type=float, default=0.05)
    parser.add_argument("--max-calibration-error", type=float, default=DEFAULT_MAX_CAL_ERROR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = Path(args.input)
    target = Path(args.output)
    report = json.loads(source.read_text(encoding="utf-8"))
    result = run(
        report,
        min_n=args.min_n,
        min_priced_n=args.min_priced_n,
        min_edge=args.min_edge,
        max_cal_error=args.max_calibration_error,
    )
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nSaved Q2 market-edge report to: {target.resolve()}")


if __name__ == "__main__":
    main()

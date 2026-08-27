"""Run a reproducible Bayesian/Poisson/Dual backtest comparison.

The command executes each engine over the same historical scope, captures the
BacktestResult rows immediately after each run, and writes one JSON report.
It intentionally does not change production signal configuration.

Example:
    python scripts/benchmark_engines.py --from 2026-01-01 --to 2026-06-30
    python scripts/benchmark_engines.py --from 2026-01-01 --to 2026-06-30 --market "Over 2.5"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

# Make the script runnable from either backend/ or repository root.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import BacktestResult
from app.quant.backtest_report import summarize_backtest
from app.services.backtester import run_backtest


async def benchmark(
    *,
    date_from: date | None,
    date_to: date | None,
    market: str | None,
) -> dict:
    reports: dict[str, dict] = {}
    for engine in ("bayesian", "poisson", "dual"):
        async with AsyncSessionLocal() as db:
            await run_backtest(
                db=db,
                market=market,
                date_from=date_from,
                date_to=date_to,
                engine=engine,
            )

            query = select(BacktestResult).order_by(
                BacktestResult.fixture_date, BacktestResult.id
            )
            if market:
                query = query.where(BacktestResult.market == market)
            if date_from:
                query = query.where(BacktestResult.fixture_date >= date_from)
            if date_to:
                query = query.where(BacktestResult.fixture_date <= date_to)

            rows = list((await db.execute(query)).scalars().all())
            report = summarize_backtest(rows)
            reports[engine] = {
                "n": report.n,
                "wins": report.wins,
                "hit_rate": report.hit_rate,
                "hit_rate_ci": report.hit_rate_ci,
                "brier": report.brier,
                "log_loss": report.log_loss,
                "calibration_error": report.calibration_error,
                "mean_model_probability": report.mean_model_probability,
                "mean_implied_probability": report.mean_implied_probability,
                "mean_ev": report.mean_ev,
                "positive_ev_rate": report.positive_ev_rate,
                "roi": report.roi,
                "significance_vs_baseline": report.significance_vs_baseline,
            }

    return {
        "scope": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "market": market,
        },
        "engines": reports,
        "methodology": {
            "same_scope": True,
            "execution_price": "configured backtest execution price",
            "production_rules_changed": False,
            "note": "This benchmark currently reproduces the application's existing backtest gates. Use strict point-in-time policy before treating adaptive-learning comparisons as final evidence.",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat)
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat)
    parser.add_argument("--market")
    parser.add_argument(
        "--output",
        default="quant_engine_benchmark.json",
        help="Output JSON path (default: quant_engine_benchmark.json)",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    report = await benchmark(
        date_from=args.date_from,
        date_to=args.date_to,
        market=args.market,
    )
    output = Path(args.output)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nSaved benchmark report to: {output.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

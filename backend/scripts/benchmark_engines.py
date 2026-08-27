"""Run a reproducible Bayesian/Poisson/Dual backtest comparison.

Strict mode is the default: current adaptive performance weights and current
learned suppression are neutralized for the historical replay. This makes the
run suitable for model research rather than production-rule auditing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
# TiTiBet's default SQLite URL is relative (./titibet.db). Anchor benchmark
# execution to backend/ so the benchmark uses the application's local database
# regardless of whether it is launched from the repository root or backend/.
os.chdir(BACKEND_DIR)

from sqlalchemy import select

import app.services.backtester as backtester_module
from app.core.database import AsyncSessionLocal
from app.models import BacktestResult, Fixture
from app.quant.backtest_report import summarize_backtest
from app.services.performance_intelligence import PerformanceWeights


async def _neutral_weights(*_args, **_kwargs) -> PerformanceWeights:
    """Return neutral adaptive state for strict historical replay."""
    return PerformanceWeights()


async def _no_current_suppression(*_args, **_kwargs) -> frozenset[str]:
    return frozenset()


class StrictReplay:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._old_weights = backtester_module.compute_performance_weights
        self._old_suppression = backtester_module._get_underperforming_leagues

    def __enter__(self):
        if self.enabled:
            backtester_module.compute_performance_weights = _neutral_weights
            backtester_module._get_underperforming_leagues = _no_current_suppression
        return self

    def __exit__(self, *_exc):
        backtester_module.compute_performance_weights = self._old_weights
        backtester_module._get_underperforming_leagues = self._old_suppression


async def _assert_data_available() -> int:
    """Fail with a useful message when the local application DB has no fixtures."""
    async with AsyncSessionLocal() as db:
        count = int((await db.execute(select(Fixture.id).limit(1))).scalars().first() is not None)
    if not count:
        raise RuntimeError(
            f"No fixture data found in {BACKEND_DIR / 'titibet.db'}. "
            "Run TiTiBet locally with its populated database or configure DB_URL "
            "to point this benchmark at the populated database."
        )
    return count


def _report(rows: list[BacktestResult]) -> dict:
    report = summarize_backtest(rows)
    return {
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


def _group_by_market(rows: list[BacktestResult]) -> dict[str, dict]:
    """Return the same diagnostics split by market so sparse aggregates are visible."""
    grouped: dict[str, list[BacktestResult]] = {}
    for row in rows:
        grouped.setdefault(row.market, []).append(row)
    return {market: _report(group) for market, group in sorted(grouped.items())}


async def benchmark(
    *,
    date_from: date | None,
    date_to: date | None,
    market: str | None,
    strict: bool,
) -> dict:
    await _assert_data_available()
    reports: dict[str, dict] = {}
    with StrictReplay(strict):
        for engine in ("bayesian", "poisson", "dual"):
            async with AsyncSessionLocal() as db:
                await backtester_module.run_backtest(
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
                reports[engine] = {
                    "overall": _report(rows),
                    "by_market": _group_by_market(rows),
                }

    return {
        "scope": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "market": market,
        },
        "database": str(BACKEND_DIR / "titibet.db"),
        "engines": reports,
        "methodology": {
            "same_scope": True,
            "strict_point_in_time": strict,
            "current_adaptive_weights": "disabled" if strict else "enabled",
            "current_league_suppression": "disabled" if strict else "enabled",
            "execution_price": "configured backtest execution price",
            "production_rules_changed": False,
            "note": "Strict mode is for model research. It neutralizes current learned state rather than pretending that historical adaptive state has been reconstructed.",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat)
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat)
    parser.add_argument("--market")
    parser.add_argument("--legacy", action="store_true", help="Use current adaptive state instead of strict replay")
    parser.add_argument(
        "--output",
        default=str(Path.cwd() / "quant_engine_benchmark.json"),
        help="Output JSON path (default: repository root quant_engine_benchmark.json)",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    report = await benchmark(
        date_from=args.date_from,
        date_to=args.date_to,
        market=args.market,
        strict=not args.legacy,
    )
    output = Path(args.output)
    if not output.is_absolute():
        output = Path.cwd() / output
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nSaved benchmark report to: {output.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

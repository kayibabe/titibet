"""Compare persisted backtest results by source engine.

This module deliberately treats each stored engine run as an independent
experiment. It does not claim the runs are temporally identical unless their
fixture/date scope is identical. Use the strict point-in-time replay policy for
future historical experiments before promoting a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.models.backtest import BacktestResult

from .backtest_report import summarize_backtest


@dataclass(frozen=True)
class EngineComparison:
    engine: str
    report: dict


def compare_engines(rows: Iterable[BacktestResult]) -> list[EngineComparison]:
    grouped: dict[str, list[BacktestResult]] = {}
    for row in rows:
        grouped.setdefault(row.source_engine or "unknown", []).append(row)

    comparisons = []
    for engine in sorted(grouped):
        report = summarize_backtest(grouped[engine]).__dict__.copy()
        comparisons.append(EngineComparison(engine=engine, report=report))

    # Prefer statistical/model metrics over raw ROI when ranking candidate
    # engines. Lower Brier/log-loss/calibration error is better; positive ROI
    # is a secondary economic signal. Engines without enough observations remain
    # visible but are never silently promoted.
    comparisons.sort(
        key=lambda item: (
            item.report.get("brier") is None,
            item.report.get("brier") if item.report.get("brier") is not None else float("inf"),
            item.report.get("log_loss") if item.report.get("log_loss") is not None else float("inf"),
            -item.report.get("roi", 0.0),
        )
    )
    return comparisons

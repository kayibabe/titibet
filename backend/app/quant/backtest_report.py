"""Quantitative validation of persisted BacktestResult rows.

This module deliberately evaluates existing backtest output; it does not alter
signal generation. It exposes calibration, value, and significance diagnostics
that can be compared by engine/market before production rules are changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from app.models.backtest import BacktestResult

from .calibration import calibration_report, wilson_interval
from .expected_value import assess_value
from .statistical_tests import compare_hit_rate_to_baseline


@dataclass(frozen=True)
class ValidationSummary:
    n: int
    wins: int
    hit_rate: float
    hit_rate_ci: tuple[float, float]
    brier: Optional[float]
    log_loss: Optional[float]
    calibration_error: Optional[float]
    mean_model_probability: Optional[float]
    mean_implied_probability: Optional[float]
    mean_ev: Optional[float]
    positive_ev_rate: Optional[float]
    roi: float
    significance_vs_baseline: Optional[dict]


def _usable(rows: Iterable[BacktestResult]) -> list[BacktestResult]:
    return [
        row for row in rows
        if row.bet_result in (0, 1)
        and row.flat_stake is not None
        and float(row.flat_stake) > 0
        and row.actual_odd is not None
        and float(row.actual_odd) > 1.0
    ]


def summarize_backtest(
    rows: Iterable[BacktestResult],
    *,
    min_baseline_n: int = 30,
) -> ValidationSummary:
    usable = _usable(rows)
    n = len(usable)
    if not n:
        return ValidationSummary(
            n=0,
            wins=0,
            hit_rate=0.0,
            hit_rate_ci=(0.0, 0.0),
            brier=None,
            log_loss=None,
            calibration_error=None,
            mean_model_probability=None,
            mean_implied_probability=None,
            mean_ev=None,
            positive_ev_rate=None,
            roi=0.0,
            significance_vs_baseline=None,
        )

    wins = sum(int(row.bet_result) for row in usable)
    hit = wins / n
    ci = wilson_interval(wins, n)
    probs = [float(row.derived_prob) for row in usable if row.derived_prob is not None]
    outcomes_for_probs = [int(row.bet_result) for row in usable if row.derived_prob is not None]
    odds = [float(row.actual_odd) for row in usable]
    implied = [1.0 / odd for odd in odds]
    evs = [
        assess_value(float(row.derived_prob), float(row.actual_odd)).ev
        for row in usable
        if row.derived_prob is not None
    ]
    total_stake = sum(float(row.flat_stake) for row in usable)
    total_profit = sum(float(row.profit_loss or 0.0) for row in usable)
    calibration = calibration_report(probs, outcomes_for_probs) if probs else None
    sig = None
    if n >= min_baseline_n:
        baseline = sum(implied) / len(implied)
        test = compare_hit_rate_to_baseline(wins, n, baseline)
        sig = {
            "baseline": round(baseline, 6),
            "z": round(test.z, 4),
            "p_value_two_sided": round(test.p_value_two_sided, 6),
            "lower": round(test.lower, 6),
            "upper": round(test.upper, 6),
        }

    return ValidationSummary(
        n=n,
        wins=wins,
        hit_rate=round(hit, 6),
        hit_rate_ci=tuple(round(x, 6) for x in ci),
        brier=round(calibration.brier, 6) if calibration else None,
        log_loss=round(calibration.log_loss, 6) if calibration else None,
        calibration_error=round(calibration.mean_absolute_calibration_error, 6) if calibration else None,
        mean_model_probability=round(sum(probs) / len(probs), 6) if probs else None,
        mean_implied_probability=round(sum(implied) / len(implied), 6) if implied else None,
        mean_ev=round(sum(evs) / len(evs), 6) if evs else None,
        positive_ev_rate=round(sum(ev >= 0 for ev in evs) / len(evs), 6) if evs else None,
        roi=round(total_profit / total_stake, 6) if total_stake else 0.0,
        significance_vs_baseline=sig,
    )

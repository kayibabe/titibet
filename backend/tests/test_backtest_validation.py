from types import SimpleNamespace

import pytest

from app.quant.backtest_report import summarize_backtest


def _row(*, result: int, prob: float | None = 0.6, odds: float = 2.0, profit: float | None = None, stake: float = 10.0):
    if profit is None:
        profit = stake * (odds - 1.0) if result else -stake
    return SimpleNamespace(
        bet_result=result,
        derived_prob=prob,
        actual_odd=odds,
        profit_loss=profit,
        flat_stake=stake,
    )


def test_summary_reports_calibration_and_value_metrics():
    rows = [
        _row(result=1, prob=0.8, odds=2.0),
        _row(result=1, prob=0.7, odds=2.0),
        _row(result=0, prob=0.3, odds=2.0),
        _row(result=0, prob=0.2, odds=2.0),
    ]
    report = summarize_backtest(rows, min_baseline_n=100)
    assert report.n == 4
    assert report.wins == 2
    assert report.hit_rate == pytest.approx(0.5)
    assert report.brier is not None
    assert report.log_loss is not None
    assert report.mean_model_probability == pytest.approx(0.5)
    assert report.mean_implied_probability == pytest.approx(0.5)
    assert report.mean_ev == pytest.approx(0.0)
    assert report.positive_ev_rate == pytest.approx(1.0)
    assert report.roi == pytest.approx(0.0)
    assert report.significance_vs_baseline is None


def test_summary_marks_only_positive_ev_rows():
    rows = [
        _row(result=1, prob=0.60, odds=2.0),   # EV +20%
        _row(result=0, prob=0.40, odds=2.0),   # EV -20%
    ]
    report = summarize_backtest(rows, min_baseline_n=100)
    assert report.mean_ev == pytest.approx(0.0)
    assert report.positive_ev_rate == pytest.approx(0.5)


def test_summary_ignores_rows_without_valid_execution_price():
    rows = [
        _row(result=1, prob=0.6, odds=2.0),
        _row(result=1, prob=0.6, odds=None),
        _row(result=0, prob=0.6, odds=1.0),
        _row(result=0, prob=0.6, odds=0.0),
    ]
    report = summarize_backtest(rows)
    assert report.n == 1
    assert report.wins == 1


def test_significance_is_included_after_minimum_sample():
    rows = [_row(result=1, prob=0.6, odds=3.0) for _ in range(30)]
    report = summarize_backtest(rows, min_baseline_n=30)
    assert report.significance_vs_baseline is not None
    assert report.significance_vs_baseline["baseline"] == pytest.approx(1 / 3)

from app.models.backtest import BacktestResult
from app.quant.model_comparison import compare_engines


def _row(engine: str, won: int, prob: float, odds: float, pl: float) -> BacktestResult:
    return BacktestResult(
        fixture_id=1,
        fixture_date=None,
        market="Over 2.5",
        source_engine=engine,
        derived_prob=prob,
        actual_odd=odds,
        bet_result=won,
        profit_loss=pl,
        flat_stake=10.0,
    )


def test_compare_engines_groups_and_reports():
    rows = [
        _row("bayesian", 1, 0.70, 1.8, 8.0),
        _row("bayesian", 0, 0.60, 1.8, -10.0),
        _row("poisson", 1, 0.65, 2.0, 10.0),
        _row("dual", 1, 0.75, 1.7, 7.0),
    ]

    result = compare_engines(rows)

    assert {item.engine for item in result} == {"bayesian", "poisson", "dual"}
    for item in result:
        assert item.report["n"] == 1 if item.engine != "bayesian" else item.report["n"] == 2
        assert "brier" in item.report
        assert "roi" in item.report


def test_compare_engines_empty():
    assert compare_engines([]) == []

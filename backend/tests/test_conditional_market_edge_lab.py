from datetime import date

from backend.scripts.conditional_market_edge_lab import (
    Observation,
    aggregate,
    agreement_label,
    band_name,
    is_research_candidate,
)


def test_agreement_labels():
    assert agreement_label(0.74, 0.72) == "BOTH_HIGH"
    assert agreement_label(0.64, 0.60) == "BOTH"
    assert agreement_label(0.80, 0.65) == "DISAGREE"
    assert agreement_label(0.71, None) == "BAYESIAN_ONLY"
    assert agreement_label(None, 0.71) == "POISSON_ONLY"


def test_odds_and_probability_bands():
    assert band_name(1.24, (("<1.25", None, 1.25),)) == "<1.25"
    assert band_name(1.25, (("1.25-1.49", 1.25, 1.50),)) == "1.25-1.49"
    assert band_name(0.75, (("0.75-0.84", 0.75, 0.85),)) == "0.75-0.84"


def row(n=40, ev=0.05, roi=0.04, cal=0.06):
    return {
        "n": n,
        "priced_n": n,
        "wins": 28,
        "hit_rate": 0.70,
        "mean_probability": 0.74,
        "mean_implied_probability": 0.69,
        "mean_ev": ev,
        "positive_ev_rate": 0.55,
        "roi": roi,
        "brier": 0.18,
        "log_loss": 0.50,
        "calibration_error": cal,
    }


def test_research_candidate_gate():
    assert is_research_candidate(row()) is True
    assert is_research_candidate(row(n=29)) is False
    assert is_research_candidate(row(ev=0.02)) is False
    assert is_research_candidate(row(roi=-0.01)) is False
    assert is_research_candidate(row(cal=0.11)) is False


def test_aggregate_returns_expected_core_metrics():
    rows = [
        Observation(
            fixture_date=date(2026, 1, 1),
            market="Under 2.5",
            engine="poisson",
            odds=2.0,
            probability=0.60,
            outcome=1,
            ev=0.20,
            league="Premier League",
            country="England",
            tier=1,
            agreement="BOTH",
            bayesian_probability=0.58,
            poisson_probability=0.60,
        ),
        Observation(
            fixture_date=date(2026, 1, 2),
            market="Under 2.5",
            engine="poisson",
            odds=2.0,
            probability=0.50,
            outcome=0,
            ev=0.0,
            league="Premier League",
            country="England",
            tier=1,
            agreement="BOTH",
            bayesian_probability=0.48,
            poisson_probability=0.50,
        ),
    ]
    result = aggregate(rows)
    assert result["n"] == 2
    assert result["wins"] == 1
    assert result["hit_rate"] == 0.5
    assert result["roi"] == 0.0

from backend.scripts.market_edge_discovery_v2 import classify, run


def row(**overrides):
    value = {
        "n": 200,
        "wins": 150,
        "hit_rate": 0.75,
        "brier": 0.17,
        "log_loss": 0.52,
        "calibration_error": 0.04,
        "mean_probability": 0.75,
        "mean_implied_probability": 0.70,
        "mean_ev": 0.06,
        "positive_ev_rate": 0.40,
        "roi": 0.06,
    }
    value.update(overrides)
    return value


def test_approval_requires_positive_ev_roi_sample_and_quality():
    status, reasons = classify(row(), min_n=100, min_edge=0.05, max_calibration_error=0.10)
    assert status == "APPROVED"
    assert "mean_ev=0.060" in reasons


def test_negative_roi_rejects_even_when_mean_ev_is_positive():
    status, reasons = classify(row(roi=-0.08), min_n=100, min_edge=0.05, max_calibration_error=0.10)
    assert status == "REJECT"
    assert any("roi=-0.080<0" in reason for reason in reasons)


def test_small_sample_cannot_be_approved():
    status, reasons = classify(row(n=22), min_n=100, min_edge=0.05, max_calibration_error=0.10)
    assert status == "REJECT"
    assert any("sample_n=22<100" in reason for reason in reasons)


def test_high_calibration_error_becomes_conditional_when_core_edge_is_good():
    status, reasons = classify(row(calibration_error=0.14), min_n=100, min_edge=0.05, max_calibration_error=0.10)
    assert status == "CONDITIONAL"
    assert any("calibration_error=0.140>0.100" in reason for reason in reasons)


def test_probability_gap_is_diagnostic_not_primary_edge_measure():
    # A positive historical ROI with a negative average probability gap must not
    # be approved unless aggregate EV itself clears the pricing threshold.
    status, _ = classify(
        row(mean_probability=0.69, mean_implied_probability=0.72, mean_ev=0.06),
        min_n=100, min_edge=0.05, max_calibration_error=0.10,
    )
    assert status == "APPROVED"


def test_run_preserves_research_only_contract():
    report = {"scope": {"date_from": "2026-01-01", "date_to": "2026-06-30"}, "fixtures_seen": 10,
              "markets": {"Over 2.5": {"poisson": row()}}}
    result = run(report)
    assert result["production_rules_changed"] is False
    assert result["summary"]["approved"] == 1

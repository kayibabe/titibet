"""Tests for quantitative calibration utilities.

These tests focus on deterministic invariants rather than historical ROI so the
research layer cannot silently alter production betting behaviour.
"""

import pytest

from app.quant.calibration import (
    BetaCalibrator,
    IdentityCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    calibration_report,
    fit_calibrator,
    select_calibrator,
    walk_forward_calibration,
)


def test_identity_preserves_probability_order_and_values():
    p = [0.1, 0.4, 0.8]
    cal = IdentityCalibrator().fit(p, [0, 1, 1])
    assert cal.predict(p).tolist() == pytest.approx(p)


def test_platt_outputs_valid_probabilities():
    p = [0.05, 0.10, 0.20, 0.40, 0.60, 0.80, 0.90, 0.95]
    y = [0, 0, 0, 0, 1, 1, 1, 1]
    out = PlattCalibrator().fit(p, y).predict(p)
    assert all(0 < x < 1 for x in out)
    assert all(a <= b for a, b in zip(out, out[1:]))


def test_beta_outputs_valid_probabilities():
    p = [0.02, 0.05, 0.15, 0.30, 0.55, 0.75, 0.90, 0.98]
    y = [0, 0, 0, 0, 1, 1, 1, 1]
    out = BetaCalibrator().fit(p, y).predict(p)
    assert all(0 < x < 1 for x in out)


def test_isotonic_is_monotone():
    p = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    y = [1, 0, 0, 1, 0, 1]
    out = IsotonicCalibrator().fit(p, y).predict(p)
    assert all(a <= b + 1e-12 for a, b in zip(out, out[1:]))
    assert all(0 <= x <= 1 for x in out)


def test_calibration_report_retains_existing_metrics_contract():
    report = calibration_report([0.2, 0.8], [0, 1])
    assert report.n == 2
    assert report.brier == pytest.approx(0.04)
    assert report.log_loss > 0
    assert report.mean_absolute_calibration_error >= 0


def test_unknown_calibration_method_is_rejected():
    with pytest.raises(ValueError, match="unknown calibration method"):
        fit_calibrator("not-a-method", [0.2, 0.8], [0, 1])


def test_selection_requires_minimum_training_sample():
    with pytest.raises(ValueError, match="at least 50"):
        select_calibrator([0.5] * 10, [0, 1] * 5)


def test_walk_forward_returns_no_result_without_enough_history():
    result = walk_forward_calibration([0.5] * 40, [0, 1] * 20, train_size=40, min_train=50)
    assert result["n"] == 0
    assert result["folds"] == []


def test_walk_forward_uses_prior_blocks_only():
    # 120 observations: first 100 train, final 20 test.  The test block is
    # deliberately all wins, but cannot affect calibrator selection for itself.
    p = [0.5] * 100 + [0.9] * 20
    y = [0, 1] * 50 + [1] * 20
    result = walk_forward_calibration(p, y, train_size=100, test_size=20, min_train=50)
    assert result["n"] == 20
    assert len(result["folds"]) == 1
    fold = result["folds"][0]
    assert fold["start"] == 100
    assert fold["end"] == 120
    assert fold["train_n"] == 100
    assert fold["test_n"] == 20

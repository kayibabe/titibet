from datetime import date, datetime, timedelta, timezone

import math
import pytest

from app.quant.calibration import calibration_report, wilson_interval
from app.quant.ensemble import weighted_probability
from app.quant.expected_value import assess_value
from app.quant.leakage_guard import EvidenceTimestamp, assert_no_future_evidence, assert_training_before_test
from app.quant.statistical_tests import compare_hit_rate_to_baseline
from app.quant.walk_forward import evaluate_walk_forward, make_expanding_folds


def test_value_assessment_requires_both_probability_and_price_value():
    assert assess_value(0.70, 1.30, min_probability=0.60, min_ev=0.0).qualifies is False
    result = assess_value(0.70, 1.60, min_probability=0.60, min_ev=0.05, min_edge=0.05)
    assert result.qualifies is True
    assert result.ev == pytest.approx(0.12)


def test_ensemble_normalizes_weights():
    result = weighted_probability({"bayesian": 0.60, "poisson": 0.80}, {"bayesian": 3, "poisson": 1})
    assert result.probability == pytest.approx(0.65)
    assert sum(result.weights.values()) == pytest.approx(1.0)


def test_ensemble_rejects_missing_component_weight():
    with pytest.raises(ValueError):
        weighted_probability({"a": 0.5}, {"b": 1.0})


def test_calibration_report_and_bins():
    report = calibration_report([0.9, 0.9, 0.1, 0.1], [1, 0, 0, 1], n_bins=10)
    assert report.n == 4
    assert report.brier == pytest.approx(0.41)
    assert len(report.bins) == 2


def test_wilson_interval_contains_point_estimate():
    lower, upper = wilson_interval(60, 100)
    assert lower < 0.60 < upper


def test_walk_forward_folds_are_time_ordered():
    folds = make_expanding_folds(12, minimum_train=6, test_size=2)
    assert [(f.train_end, f.test_start, f.test_end) for f in folds] == [(6, 6, 8), (8, 8, 10), (10, 10, 12)]


def test_walk_forward_never_fits_on_test_rows():
    observations = list(range(8))
    seen = []

    def fit(train):
        seen.append(tuple(train))
        return max(train)

    def evaluate(model, test):
        return (model, tuple(test))

    results = evaluate_walk_forward(observations, fit, evaluate, minimum_train=4, test_size=2)
    assert seen == [(0, 1, 2, 3), (0, 1, 2, 3, 4, 5)]
    assert results[0] == (3, (4, 5))
    assert results[1] == (5, (6, 7))


def test_leakage_guard_rejects_future_evidence():
    t = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="look-ahead leakage"):
        assert_no_future_evidence(t, [EvidenceTimestamp("future_form", t + timedelta(minutes=1))])


def test_training_and_test_must_not_overlap():
    t1 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 10, tzinfo=timezone.utc)
    assert_training_before_test(t1, t2)
    with pytest.raises(ValueError):
        assert_training_before_test(t2, t1)


def test_binomial_significance_guard_reports_sample_and_rate():
    result = compare_hit_rate_to_baseline(65, 100, 0.50)
    assert result.n == 100
    assert result.rate == pytest.approx(0.65)
    assert result.z > 0
    assert 0 <= result.p_value_two_sided <= 1

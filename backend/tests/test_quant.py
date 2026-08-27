import pytest

from app.quant.metrics import brier_score, log_loss, roi
from app.quant.probability import edge, expected_value, fair_odds, implied_probability
from app.quant.validation import ValidationWindow, assert_point_in_time
from datetime import datetime, timezone


def test_probability_and_value_calculations():
    assert implied_probability(2.0) == pytest.approx(0.5)
    assert fair_odds(0.5) == pytest.approx(2.0)
    assert expected_value(0.5, 2.0) == pytest.approx(0.0)
    assert edge(0.6, 2.0) == pytest.approx(0.1)


def test_metrics():
    assert brier_score([0.9, 0.1], [1, 0]) == pytest.approx(0.01)
    assert log_loss([0.9, 0.1], [1, 0]) == pytest.approx(-__import__('math').log(0.9))
    assert roi([10.0, -5.0], [100.0, 100.0]) == pytest.approx(0.025)


def test_validation_window_rejects_overlap():
    dt = lambda day: datetime(2026, 1, day, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        ValidationWindow(dt(1), dt(5), dt(4), dt(10))


def test_point_in_time_guard_rejects_future_feature():
    prediction = datetime(2026, 1, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="look-ahead leakage"):
        assert_point_in_time(prediction, [datetime(2026, 1, 11, tzinfo=timezone.utc)])

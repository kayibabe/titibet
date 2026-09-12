from datetime import date, datetime, timezone

import pytest

from app.quant.strict_replay import (
    ReplayObservation,
    expanding_windows,
    filter_point_in_time,
    require_temporal_order,
    strictly_before,
)


def test_strictly_before_rejects_same_day_and_future():
    d = date(2026, 1, 10)
    assert strictly_before(d, date(2026, 1, 9))
    assert not strictly_before(d, date(2026, 1, 10))
    assert not strictly_before(d, datetime(2026, 1, 11, tzinfo=timezone.utc))


def test_filter_point_in_time_applies_explicit_value_gates():
    rows = [
        ReplayObservation(1, date(2026, 1, 1), "Over 2.5", 0.65, 2.0, 1, "bayesian"),
        ReplayObservation(2, date(2026, 1, 2), "Over 2.5", 0.52, 2.0, 1, "poisson"),
        ReplayObservation(3, date(2026, 1, 3), "Over 2.5", 0.70, 1.2, 1, "dual"),
    ]
    selected = filter_point_in_time(rows, min_probability=0.55, min_edge=0.05, min_ev=0.05)
    assert [row.fixture_id for row in selected] == [1]


def test_temporal_order_guard():
    require_temporal_order([date(2026, 1, 1), date(2026, 1, 2)])
    with pytest.raises(ValueError):
        require_temporal_order([date(2026, 1, 2), date(2026, 1, 1)])


def test_expanding_windows_are_non_overlapping():
    rows = [ReplayObservation(i, date(2026, 1, i), "M", 0.6, 2.0, i % 2, "bayesian") for i in range(1, 7)]
    windows = expanding_windows(rows, min_train=2, test_size=2)
    assert len(windows) == 2
    assert [o.fixture_id for o in windows[0][0]] == [1, 2]
    assert [o.fixture_id for o in windows[0][1]] == [3, 4]
    assert [o.fixture_id for o in windows[1][0]] == [1, 2, 3, 4]
    assert [o.fixture_id for o in windows[1][1]] == [5, 6]

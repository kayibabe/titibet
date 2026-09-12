"""Strict point-in-time replay helpers for quantitative experiments.

This module is intentionally independent of the production signal pipeline. It
provides deterministic filters and validation for historical observations so a
benchmark can be run without importing current adaptive performance state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable


@dataclass(frozen=True)
class ReplayObservation:
    fixture_id: int
    fixture_date: date
    market: str
    probability: float | None
    odds: float | None
    outcome: int | None
    source_engine: str

    @property
    def implied_probability(self) -> float | None:
        if self.odds is None or self.odds <= 1:
            return None
        return 1.0 / self.odds

    @property
    def edge(self) -> float | None:
        implied = self.implied_probability
        if self.probability is None or implied is None:
            return None
        return self.probability - implied

    @property
    def ev(self) -> float | None:
        if self.probability is None or self.odds is None or self.odds <= 1:
            return None
        return self.probability * self.odds - 1.0


def strictly_before(prediction_date: date, information_date: date | datetime | None) -> bool:
    """Return True only when information predates the prediction date."""
    if information_date is None:
        return False
    candidate = information_date.date() if isinstance(information_date, datetime) else information_date
    return candidate < prediction_date


def filter_point_in_time(
    observations: Iterable[ReplayObservation],
    *,
    min_probability: float = 0.0,
    min_edge: float | None = None,
    min_ev: float | None = None,
) -> list[ReplayObservation]:
    """Apply explicit, reproducible betting gates to already point-in-time data."""
    result: list[ReplayObservation] = []
    for obs in observations:
        if obs.probability is None or obs.odds is None or obs.odds <= 1:
            continue
        if obs.probability < min_probability:
            continue
        if min_edge is not None and (obs.edge is None or obs.edge < min_edge):
            continue
        if min_ev is not None and (obs.ev is None or obs.ev < min_ev):
            continue
        result.append(obs)
    return result


def require_temporal_order(dates: Iterable[date]) -> None:
    """Reject a sequence that is not chronological."""
    values = list(dates)
    if values != sorted(values):
        raise ValueError("historical replay observations must be chronological")


def expanding_windows(
    observations: list[ReplayObservation],
    *,
    min_train: int,
    test_size: int,
) -> list[tuple[list[ReplayObservation], list[ReplayObservation]]]:
    """Build non-overlapping expanding train/test windows from chronological data."""
    if min_train < 1 or test_size < 1:
        raise ValueError("min_train and test_size must be positive")
    require_temporal_order([o.fixture_date for o in observations])
    windows = []
    cursor = min_train
    while cursor < len(observations):
        test_end = min(cursor + test_size, len(observations))
        windows.append((observations[:cursor], observations[cursor:test_end]))
        cursor = test_end
    return windows

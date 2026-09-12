"""Guardrails for point-in-time model evaluation.

The validator intentionally does not implement data access. Callers must pass
only features and model parameters that were available at the prediction time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ValidationWindow:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime

    def __post_init__(self) -> None:
        if not self.train_start < self.train_end:
            raise ValueError("training window must have positive duration")
        if not self.train_end <= self.test_start:
            raise ValueError("training and test windows must not overlap")
        if not self.test_start < self.test_end:
            raise ValueError("test window must have positive duration")


def assert_point_in_time(
    prediction_time: datetime,
    feature_times: Sequence[datetime],
) -> None:
    """Reject any feature whose timestamp is after the prediction timestamp."""
    future = [t for t in feature_times if t > prediction_time]
    if future:
        raise ValueError(
            "look-ahead leakage detected: feature timestamp is after prediction time"
        )


def validate_binary_labels(labels: Sequence[int]) -> None:
    if not labels:
        raise ValueError("labels must not be empty")
    if any(int(label) not in (0, 1) for label in labels):
        raise ValueError("labels must be binary (0 or 1)")


def require_sample_size(n: int, minimum: int, purpose: str = "evaluation") -> None:
    if minimum < 1:
        raise ValueError("minimum sample size must be positive")
    if n < minimum:
        raise ValueError(f"{purpose} requires at least {minimum} observations; got {n}")

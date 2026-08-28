"""Explicit data-leakage checks for historical evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True)
class EvidenceTimestamp:
    name: str
    observed_at: datetime


def assert_no_future_evidence(
    prediction_time: datetime,
    evidence: Iterable[EvidenceTimestamp],
) -> None:
    """Raise when any evidence was observed after the prediction timestamp."""
    for item in evidence:
        if item.observed_at.tzinfo is None or prediction_time.tzinfo is None:
            raise ValueError("prediction_time and evidence timestamps must be timezone-aware")
        if item.observed_at > prediction_time:
            raise ValueError(
                f"look-ahead leakage: evidence '{item.name}' observed after prediction time"
            )


def assert_training_before_test(train_end: datetime, test_start: datetime) -> None:
    if train_end.tzinfo is None or test_start.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    if train_end > test_start:
        raise ValueError("training data overlaps the test period")

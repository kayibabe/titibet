"""Immutable point-in-time feature container used by validation and replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Mapping


@dataclass(frozen=True)
class FeatureSnapshot:
    fixture_id: int
    as_of: datetime
    event_date: date
    features: Mapping[str, float | int | str | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        if self.as_of.tzinfo.utcoffset(self.as_of) is None:
            raise ValueError("as_of must be timezone-aware")
        if self.as_of.tzinfo != timezone.utc:
            object.__setattr__(self, "as_of", self.as_of.astimezone(timezone.utc))

    def contains_future_information(self, observed_at: datetime) -> bool:
        if observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return observed_at > self.as_of

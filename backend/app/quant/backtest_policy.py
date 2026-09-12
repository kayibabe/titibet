"""Policies controlling historical backtest leakage risk."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestPolicy:
    """Explicit policy for historical replay.

    ``point_in_time=True`` disables adaptive overlays whose current database state
    cannot be reconstructed from the historical timestamp. This is intentionally
    conservative: a strict baseline is preferable to accidentally using future
    information. A future iteration can replace the disabled overlays with
    date-scoped point-in-time estimates.
    """

    point_in_time: bool = False
    allow_adaptive_performance: bool = True
    allow_current_suppression: bool = True

    @classmethod
    def strict(cls) -> "BacktestPolicy":
        return cls(
            point_in_time=True,
            allow_adaptive_performance=False,
            allow_current_suppression=False,
        )

    @classmethod
    def legacy(cls) -> "BacktestPolicy":
        return cls(
            point_in_time=False,
            allow_adaptive_performance=True,
            allow_current_suppression=True,
        )

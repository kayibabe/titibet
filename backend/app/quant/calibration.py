"""Calibration utilities for binary prediction probabilities."""

from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt
from typing import Iterable


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    n: int
    mean_predicted: float
    observed_rate: float
    absolute_error: float


@dataclass(frozen=True)
class CalibrationReport:
    n: int
    brier: float
    log_loss: float
    mean_absolute_calibration_error: float
    bins: tuple[CalibrationBin, ...]


def _validate(probabilities: Iterable[float], outcomes: Iterable[int]) -> list[tuple[float, int]]:
    pairs = [(float(p), int(y)) for p, y in zip(probabilities, outcomes)]
    if not pairs:
        raise ValueError("at least one observation is required")
    for p, y in pairs:
        if not 0.0 <= p <= 1.0:
            raise ValueError("probabilities must be between 0 and 1")
        if y not in (0, 1):
            raise ValueError("outcomes must be binary")
    return pairs


def calibration_bins(
    probabilities: Iterable[float], outcomes: Iterable[int], *, n_bins: int = 10
) -> tuple[CalibrationBin, ...]:
    if n_bins < 2:
        raise ValueError("n_bins must be at least 2")
    pairs = _validate(probabilities, outcomes)
    bins: list[CalibrationBin] = []
    width = 1.0 / n_bins
    for i in range(n_bins):
        lower = i * width
        upper = 1.0 if i == n_bins - 1 else (i + 1) * width
        members = [
            (p, y)
            for p, y in pairs
            if (lower <= p < upper) or (i == n_bins - 1 and p == 1.0)
        ]
        if not members:
            continue
        mean_p = sum(p for p, _ in members) / len(members)
        observed = sum(y for _, y in members) / len(members)
        bins.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                n=len(members),
                mean_predicted=mean_p,
                observed_rate=observed,
                absolute_error=abs(mean_p - observed),
            )
        )
    return tuple(bins)


def calibration_report(
    probabilities: Iterable[float], outcomes: Iterable[int], *, n_bins: int = 10
) -> CalibrationReport:
    pairs = _validate(probabilities, outcomes)
    ps = [p for p, _ in pairs]
    ys = [y for _, y in pairs]
    epsilon = 1e-15
    brier = sum((p - y) ** 2 for p, y in pairs) / len(pairs)
    ll = sum(
        -(y * log(max(epsilon, min(1.0 - epsilon, p)))
        + (1 - y) * log(max(epsilon, min(1.0 - epsilon, 1.0 - p))))
        for p, y in pairs
    ) / len(pairs)
    bins = calibration_bins(ps, ys, n_bins=n_bins)
    total = sum(b.n for b in bins)
    mace = sum(b.absolute_error * b.n for b in bins) / total
    return CalibrationReport(
        n=len(pairs),
        brier=brier,
        log_loss=ll,
        mean_absolute_calibration_error=mace,
        bins=bins,
    )


def wilson_interval(wins: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0 or not 0 <= wins <= n:
        raise ValueError("wins must be between 0 and n, with n positive")
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * sqrt((p * (1 - p) / n) + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)

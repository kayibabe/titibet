"""Out-of-sample probability and betting-performance metrics."""

from __future__ import annotations

import math
from collections.abc import Iterable


def brier_score(probabilities: Iterable[float], outcomes: Iterable[int]) -> float:
    pairs = list(zip(probabilities, outcomes))
    if not pairs:
        raise ValueError("at least one observation is required")
    return sum((float(p) - int(y)) ** 2 for p, y in pairs) / len(pairs)


def log_loss(probabilities: Iterable[float], outcomes: Iterable[int], epsilon: float = 1e-15) -> float:
    pairs = list(zip(probabilities, outcomes))
    if not pairs:
        raise ValueError("at least one observation is required")
    if epsilon <= 0 or epsilon >= 0.5:
        raise ValueError("epsilon must be between 0 and 0.5")
    total = 0.0
    for p, y in pairs:
        p = min(1.0 - epsilon, max(epsilon, float(p)))
        y = int(y)
        if y not in (0, 1):
            raise ValueError("binary outcomes must be 0 or 1")
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(pairs)


def roi(profits: Iterable[float], stakes: Iterable[float]) -> float:
    pairs = list(zip(profits, stakes))
    total_staked = sum(float(s) for _, s in pairs)
    if total_staked <= 0:
        raise ValueError("total stake must be positive")
    return sum(float(p) for p, _ in pairs) / total_staked

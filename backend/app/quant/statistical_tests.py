"""Conservative statistical tests for betting-model monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, sqrt

from .calibration import wilson_interval


@dataclass(frozen=True)
class BinomialTest:
    n: int
    successes: int
    rate: float
    lower: float
    upper: float
    baseline: float
    z: float
    p_value_two_sided: float


def _normal_two_sided_p(z: float) -> float:
    return 1.0 - erf(abs(z) / sqrt(2.0))


def compare_hit_rate_to_baseline(successes: int, n: int, baseline: float) -> BinomialTest:
    if not 0 <= successes <= n or n <= 0:
        raise ValueError("successes must be between 0 and n, with n positive")
    if not 0.0 <= baseline <= 1.0:
        raise ValueError("baseline must be between 0 and 1")
    se = sqrt(baseline * (1.0 - baseline) / n)
    z = 0.0 if se == 0 else ((successes / n) - baseline) / se
    lower, upper = wilson_interval(successes, n)
    return BinomialTest(
        n=n,
        successes=successes,
        rate=successes / n,
        lower=lower,
        upper=upper,
        baseline=baseline,
        z=z,
        p_value_two_sided=_normal_two_sided_p(z),
    )

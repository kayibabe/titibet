"""Value-betting qualification primitives.

A signal is not a value bet merely because its model probability is high.
Qualification is expressed as independent gates so callers can compare
probability quality and price quality separately.
"""

from __future__ import annotations

from dataclasses import dataclass

from .probability import expected_value, fair_odds, implied_probability


@dataclass(frozen=True)
class ValueAssessment:
    probability: float
    odds: float
    fair_odds: float
    implied_probability: float
    edge: float
    ev: float
    qualifies: bool


def assess_value(
    probability: float,
    odds: float,
    *,
    min_probability: float = 0.0,
    min_ev: float = 0.0,
    min_edge: float = 0.0,
) -> ValueAssessment:
    p = float(probability)
    o = float(odds)
    fair = fair_odds(p)
    implied = implied_probability(o)
    prob_edge = p - implied
    ev = expected_value(p, o)
    qualifies = (
        p >= min_probability
        and prob_edge >= min_edge
        and ev >= min_ev
    )
    return ValueAssessment(
        probability=p,
        odds=o,
        fair_odds=fair,
        implied_probability=implied,
        edge=prob_edge,
        ev=ev,
        qualifies=qualifies,
    )

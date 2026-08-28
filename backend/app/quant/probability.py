"""Probability and value calculations.

These functions are deliberately pure and side-effect free so they can be used
by live signals, backtests, and validation tests without changing behaviour.
"""

from __future__ import annotations


def _validate_probability(probability: float) -> float:
    p = float(probability)
    if not 0.0 <= p <= 1.0:
        raise ValueError("probability must be between 0 and 1")
    return p


def _validate_decimal_odds(odds: float) -> float:
    value = float(odds)
    if value <= 1.0:
        raise ValueError("decimal odds must be greater than 1.0")
    return value


def implied_probability(odds: float) -> float:
    """Return raw implied probability from decimal odds, without margin removal."""
    return 1.0 / _validate_decimal_odds(odds)


def fair_odds(probability: float) -> float:
    """Return model fair decimal odds for a probability."""
    p = _validate_probability(probability)
    if p <= 0.0:
        return float("inf")
    return 1.0 / p


def expected_value(probability: float, odds: float) -> float:
    """Return expected profit per unit stake.

    EV = p * (odds - 1) - (1 - p)
       = p * odds - 1
    """
    p = _validate_probability(probability)
    o = _validate_decimal_odds(odds)
    return p * o - 1.0


def edge(probability: float, odds: float) -> float:
    """Return probability edge versus the raw bookmaker implied probability."""
    return _validate_probability(probability) - implied_probability(odds)


def overround(probabilities: list[float]) -> float:
    """Return bookmaker overround from a mutually exclusive market."""
    if not probabilities:
        raise ValueError("probabilities must not be empty")
    return sum(_validate_probability(p) for p in probabilities) - 1.0

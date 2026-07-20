"""
staking.py -- Kelly + unit-based staking recommendations.
Ported from FootBet odds_engine.py (Kelly) + TiTiBet tracker (unit staking).
"""
from __future__ import annotations
import math
from app.core.config import get_settings, BAYESIAN_KELLY_P_VARIANCE, BAYESIAN_KELLY_PRIOR_VARIANCE

settings = get_settings()


def kelly_stake_pct(prob: float, odds: float, fraction: float | None = None, cap: float | None = None) -> float:
    """Quarter-Kelly fraction. Returns fraction of bankroll to stake (0-cap)."""
    if not (0 < prob < 1):
        return 0.0
    frac = fraction if fraction is not None else settings.kelly_fraction
    hard_cap = cap if cap is not None else settings.max_kelly_pct
    b = odds - 1.0
    if b <= 0:
        return 0.0
    full = (b * prob - (1.0 - prob)) / b
    return min(max(0.0, full * frac), hard_cap)


def unit_stake_pct(confidence: str, unit_pct: float | None = None) -> float:
    """Unit-based stake as fraction of bankroll."""
    u = unit_pct if unit_pct is not None else settings.unit_pct
    multipliers = {"High": 3, "Medium": 2, "Low": 1}
    return u * multipliers.get(confidence, 0)


# -- New utilities -------------------------------------------------------------

def full_kelly(prob: float, odds: float) -> float:
    """Raw (unscaled) Kelly fraction. Returns 0 when edge is negative."""
    if not (0 < prob < 1):
        return 0.0
    b = odds - 1.0
    if b <= 0:
        return 0.0
    f = (b * prob - (1.0 - prob)) / b
    return max(0.0, f)


def bayesian_kelly(
    prob: float,
    odds: float,
    var_model: float | None = None,
    var_prior: float | None = None,
    fraction: float | None = None,
    cap: float | None = None,
) -> float:
    """
    Bayesian Kelly with shrinkage for estimation uncertainty (from qsbip).
    f* = standard_kelly x (var_model / (var_model + var_prior)) x fraction

    Shrinkage dampens over-confident stakes when the model probability is
    estimated with high uncertainty. Defaults come from config.
    """
    vm = var_model if var_model is not None else BAYESIAN_KELLY_P_VARIANCE
    vp = var_prior if var_prior is not None else BAYESIAN_KELLY_PRIOR_VARIANCE
    frac = fraction if fraction is not None else settings.kelly_fraction
    hard_cap = cap if cap is not None else settings.max_kelly_pct

    f_raw = full_kelly(prob, odds)
    if f_raw <= 0 or (vm + vp) == 0:
        return 0.0

    shrinkage = vm / (vm + vp)
    return min(f_raw * shrinkage * frac, hard_cap)


def devig(odds_list: list[float]) -> list[float]:
    """
    Multiplicative devigging -- returns true probabilities summing to 1.0.
    Removes the bookmaker's overround from a complete market.
    """
    if not odds_list:
        return []
    raw = [1.0 / o for o in odds_list if o > 1.0]
    k = sum(raw)
    return [p / k for p in raw] if k > 0 else raw


# NOTE: recommended_stake_pct was removed -- it duplicated dual_engine._recommended_stake
# and was never called by any engine or router (confirmed: no imports in the codebase).
# The authoritative staking logic lives in app/engines/dual_engine.py::_recommended_stake.

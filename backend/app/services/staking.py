"""
staking.py -- Kelly + unit-based staking recommendations.
Ported from FootBet odds_engine.py (Kelly) + TiTiBet tracker (unit staking).

Extended with:
  - full_kelly()          -- raw (unscaled) Kelly fraction
  - bayesian_kelly()      -- shrinkage-adjusted Kelly (qsbip)
  - devig()               -- multiplicative overround removal
  - expected_value()      -- explicit EV = p * odds - 1
  - dynamic_ev_threshold() -- noise-adaptive EV gate
"""
from __future__ import annotations
import math
import statistics
from typing import Sequence
from app.core.config import get_settings, BAYESIAN_KELLY_P_VARIANCE, BAYESIAN_KELLY_PRIOR_VARIANCE, EV_BASE_THRESHOLD, EV_NOISE_MULTIPLIER, EV_DYNAMIC_WINDOW

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


def expected_value(prob: float, decimal_odds: float) -> float:
    """EV = prob x decimal_odds - 1. Positive = expected profit per unit staked."""
    return prob * decimal_odds - 1.0


def dynamic_ev_threshold(
    historical_evs: Sequence[float] | None = None,
    base: float | None = None,
    noise_multiplier: float | None = None,
) -> float:
    """
    theta = base_threshold + noise_multiplier x std(recent EVs).
    Automatically raises the bar when the model's EV outputs are noisy.
    Falls back to base_threshold when insufficient history.
    """
    base_val = base if base is not None else EV_BASE_THRESHOLD
    multiplier = noise_multiplier if noise_multiplier is not None else EV_NOISE_MULTIPLIER

    if not historical_evs or len(historical_evs) < 5:
        return base_val

    window = list(historical_evs)[-EV_DYNAMIC_WINDOW:]
    try:
        noise = statistics.stdev(window)
    except statistics.StatisticsError:
        noise = 0.0
    return base_val + multiplier * noise


# NOTE: recommended_stake_pct was removed -- it duplicated dual_engine._recommended_stake
# and was never called by any engine or router (confirmed: no imports in the codebase).
# The authoritative staking logic lives in app/engines/dual_engine.py::_recommended_stake.

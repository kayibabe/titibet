"""
Tests for app/services/staking.py — Kelly criterion staking math.

Covers:
  * full_kelly: textbook formula f* = (bp − q) / b, floored at 0
  * kelly_stake_pct: fractional Kelly with hard cap, config defaults
  * bayesian_kelly: shrinkage f* = kelly × var_m/(var_m+var_p) × fraction, capped
  * unit_stake_pct: confidence-tier multipliers
  * devig: multiplicative overround removal, probabilities sum to 1
"""
import math

import pytest

from app.core.config import (
    BAYESIAN_KELLY_P_VARIANCE,
    BAYESIAN_KELLY_PRIOR_VARIANCE,
    get_settings,
)
from app.services.staking import (
    bayesian_kelly,
    devig,
    full_kelly,
    kelly_stake_pct,
    unit_stake_pct,
)

settings = get_settings()


# ---------------------------------------------------------------------------
# full_kelly — raw, unscaled
# ---------------------------------------------------------------------------

def test_full_kelly_textbook_case():
    # p=0.6, odds=2.0 → b=1 → f* = (1×0.6 − 0.4)/1 = 0.2
    assert full_kelly(0.6, 2.0) == pytest.approx(0.2)


def test_full_kelly_general_formula():
    p, odds = 0.55, 2.2
    b = odds - 1.0
    expected = (b * p - (1 - p)) / b
    assert full_kelly(p, odds) == pytest.approx(expected)


def test_full_kelly_negative_edge_returns_zero():
    # p=0.4 at evens → edge is negative → never bet
    assert full_kelly(0.4, 2.0) == 0.0
    # Fair price exactly (p = 1/odds) → zero edge → zero stake
    assert full_kelly(0.5, 2.0) == 0.0


@pytest.mark.parametrize("prob", [0.0, 1.0, -0.1, 1.5])
def test_full_kelly_invalid_prob_returns_zero(prob):
    assert full_kelly(prob, 2.0) == 0.0


@pytest.mark.parametrize("odds", [1.0, 0.5, 0.0, -2.0])
def test_full_kelly_odds_at_or_below_evens_stake_returns_zero(odds):
    # b = odds − 1 ≤ 0 → no positive-edge bet possible
    assert full_kelly(0.9, odds) == 0.0


# ---------------------------------------------------------------------------
# kelly_stake_pct — fractional Kelly with cap
# ---------------------------------------------------------------------------

def test_kelly_stake_pct_applies_fraction():
    # full kelly 0.2 × explicit quarter fraction = 0.05, cap lifted to not bind
    assert kelly_stake_pct(0.6, 2.0, fraction=0.25, cap=1.0) == pytest.approx(0.05)


def test_kelly_stake_pct_uses_config_defaults():
    # Default fraction/cap come from settings (kelly_fraction, max_kelly_pct).
    expected = min(0.2 * settings.kelly_fraction, settings.max_kelly_pct)
    assert kelly_stake_pct(0.6, 2.0) == pytest.approx(expected)


def test_kelly_stake_pct_hard_cap_binds():
    # Huge edge: p=0.9 at odds 3.0 → full kelly = (2×0.9 − 0.1)/2 = 0.85
    # Half-kelly 0.425 must be clamped to the 2% cap.
    assert kelly_stake_pct(0.9, 3.0, fraction=0.5, cap=0.02) == pytest.approx(0.02)


def test_kelly_stake_pct_negative_edge_returns_zero():
    assert kelly_stake_pct(0.3, 2.0, fraction=0.25, cap=1.0) == 0.0


def test_kelly_stake_pct_never_negative_or_above_cap():
    for p in (0.05, 0.35, 0.5, 0.65, 0.95):
        for odds in (1.2, 1.8, 2.5, 5.0, 10.0):
            f = kelly_stake_pct(p, odds)
            assert 0.0 <= f <= settings.max_kelly_pct


# ---------------------------------------------------------------------------
# bayesian_kelly — shrinkage for estimation uncertainty
# ---------------------------------------------------------------------------

def test_bayesian_kelly_shrinkage_formula():
    p, odds = 0.6, 2.0
    vm, vp, frac = 0.05, 0.10, 0.25
    expected = full_kelly(p, odds) * (vm / (vm + vp)) * frac  # 0.2 × 1/3 × 0.25
    assert bayesian_kelly(p, odds, var_model=vm, var_prior=vp,
                          fraction=frac, cap=1.0) == pytest.approx(expected)


def test_bayesian_kelly_defaults_from_config():
    p, odds = 0.6, 2.0
    shrink = BAYESIAN_KELLY_P_VARIANCE / (BAYESIAN_KELLY_P_VARIANCE + BAYESIAN_KELLY_PRIOR_VARIANCE)
    expected = min(full_kelly(p, odds) * shrink * settings.kelly_fraction, settings.max_kelly_pct)
    assert bayesian_kelly(p, odds) == pytest.approx(expected)


def test_bayesian_kelly_always_leq_plain_fractional_kelly():
    """Shrinkage < 1 whenever prior variance > 0 — Bayesian stake must be smaller."""
    for p, odds in [(0.55, 2.1), (0.65, 1.9), (0.7, 1.6)]:
        bayes = bayesian_kelly(p, odds, fraction=0.25, cap=1.0)
        plain = kelly_stake_pct(p, odds, fraction=0.25, cap=1.0)
        assert bayes <= plain
        if plain > 0:
            assert bayes < plain


def test_bayesian_kelly_zero_variances_returns_zero():
    assert bayesian_kelly(0.6, 2.0, var_model=0.0, var_prior=0.0) == 0.0


def test_bayesian_kelly_negative_edge_returns_zero():
    assert bayesian_kelly(0.3, 2.0) == 0.0


def test_bayesian_kelly_respects_cap():
    assert bayesian_kelly(0.95, 5.0, var_model=1.0, var_prior=0.0,
                          fraction=1.0, cap=0.02) == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# unit_stake_pct — confidence-tier unit staking
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("confidence,multiplier", [
    ("High", 3), ("Medium", 2), ("Low", 1),
])
def test_unit_stake_pct_multipliers(confidence, multiplier):
    assert unit_stake_pct(confidence, unit_pct=0.01) == pytest.approx(0.01 * multiplier)


def test_unit_stake_pct_unknown_confidence_is_zero():
    assert unit_stake_pct("Bananas", unit_pct=0.01) == 0.0
    assert unit_stake_pct("", unit_pct=0.01) == 0.0


def test_unit_stake_pct_uses_config_default_unit():
    assert unit_stake_pct("Medium") == pytest.approx(settings.unit_pct * 2)


# ---------------------------------------------------------------------------
# devig — multiplicative overround removal
# ---------------------------------------------------------------------------

def test_devig_probabilities_sum_to_one():
    probs = devig([1.9, 1.9])  # typical two-way market with ~5% vig
    assert sum(probs) == pytest.approx(1.0)
    assert probs[0] == pytest.approx(0.5)
    assert probs[1] == pytest.approx(0.5)


def test_devig_three_way_market():
    odds = [2.5, 3.2, 2.9]  # 1X2-style market
    probs = devig(odds)
    assert sum(probs) == pytest.approx(1.0)
    # Relative ordering preserved: shortest price → highest probability.
    assert probs[0] > probs[2] > probs[1]
    # Each devigged prob is below its raw implied prob (overround removed).
    for p, o in zip(probs, odds):
        assert p < 1.0 / o + 1e-9
        assert p == pytest.approx((1 / o) / sum(1 / x for x in odds))


def test_devig_filters_invalid_odds():
    # Odds ≤ 1.0 are dropped before devigging.
    probs = devig([1.9, 1.0, 0.5, 1.9])
    assert len(probs) == 2
    assert sum(probs) == pytest.approx(1.0)


def test_devig_empty_and_all_invalid():
    assert devig([]) == []
    assert devig([1.0, 0.9]) == []


# ---------------------------------------------------------------------------
# Cross-checks: Kelly expected-log-growth sanity
# ---------------------------------------------------------------------------

def test_full_kelly_maximizes_log_growth():
    """f* from full_kelly must beat nearby fractions on expected log growth."""
    p, odds = 0.6, 2.0
    b = odds - 1.0
    f_star = full_kelly(p, odds)

    def growth(f: float) -> float:
        return p * math.log(1 + f * b) + (1 - p) * math.log(1 - f)

    g_star = growth(f_star)
    for delta in (-0.05, -0.01, 0.01, 0.05):
        f = f_star + delta
        if 0 < f < 1:
            assert g_star >= growth(f)

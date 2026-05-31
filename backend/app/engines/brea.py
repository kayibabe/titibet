"""
BREA — BTTS Risk-Elimination Algorithm.
Ported from betapp/backend/models/brea.py.

Three Dixon-Coles-corrected Poisson models targeting BTTS combination markets.
Used in titibet primarily as BTTS signal enrichment (risk index + quality score),
and optionally to generate standalone BTTS combination market signals.

Models:
  2a — "BTTS + Under 2.5 Goals" NO bet: fails only on 1:1 scoreline. RI_1 < 10%.
  2b — "BTTS + Over 3.5 Goals"  NO bet: fails when both score AND total ≥ 4.  RI_2 < 25%.
  2c — "BTTS + Over 4.5 Goals"  NO bet: fails when both score AND total ≥ 5.  RI_3 < 15%.

Requires: scipy (optional — degrades gracefully if unavailable)
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Try importing scipy ───────────────────────────────────────────────────────
try:
    from scipy.stats import poisson as _scipy_poisson
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False
    logger.warning("scipy not installed — BREA will use Poisson PMF fallback")


# ── Default thresholds (overridden by config.BREA_* constants) ───────────────
_RI1_MAX: float = 0.10
_RI2_MAX: float = 0.25
_RI3_MAX: float = 0.15
_RHO: float = -0.10  # Dixon-Coles correlation


# ── Pure-Python Poisson PMF fallback ─────────────────────────────────────────
def _poisson_pmf_py(k: int, lam: float) -> float:
    if lam <= 0 or k < 0:
        return 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(min(k, 20))


def _pmf(k: int, lam: float) -> float:
    if _SCIPY_OK:
        return float(_scipy_poisson.pmf(k, lam))
    return _poisson_pmf_py(k, lam)


# ── Dixon-Coles correction ────────────────────────────────────────────────────
def _tau(h: int, a: int, lh: float, la: float, rho: float) -> float:
    if h == 0 and a == 0:
        return 1.0 - lh * la * rho
    elif h == 1 and a == 0:
        return 1.0 + la * rho
    elif h == 0 and a == 1:
        return 1.0 + lh * rho
    elif h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def joint_prob(h: int, a: int, lh: float, la: float, rho: float = _RHO) -> float:
    """P(H=h, A=a) with Dixon-Coles low-score correction."""
    p_h = _pmf(h, lh)
    p_a = _pmf(a, la)
    return p_h * p_a * _tau(h, a, lh, la, rho)


# ── Kelly / EV helpers ────────────────────────────────────────────────────────
def _ev(p: float, odds: float) -> float:
    return p * odds - 1.0


def _fractional_kelly(p: float, odds: float, fraction: float = 0.25, cap: float = 0.02) -> float:
    b = odds - 1.0
    if b <= 0 or p <= 0 or p >= 1:
        return 0.0
    f = (b * p - (1.0 - p)) / b
    return min(max(0.0, f) * fraction, cap)


# ── Result dataclass ──────────────────────────────────────────────────────────
@dataclass
class BREAResult:
    model: str          # "2a", "2b", "2c"
    passed: bool
    ri: float = 0.0
    p_win: float = 0.0
    ev: float = 0.0
    kelly_stake_pct: float = 0.0
    reject_reason: Optional[str] = None
    details: dict = field(default_factory=dict)


# ── Model 2a: BTTS + Under 2.5 = NO ─────────────────────────────────────────
def model_2a(
    lambda_h: float,
    lambda_a: float,
    odds_btts_no_under25: float = 0.0,
    rho: float = _RHO,
    ri1_max: float = _RI1_MAX,
) -> BREAResult:
    """
    BTTS + Under 2.5 Goals = NO.
    Bet loses only on scoreline 1:1 (the one BTTS+U2.5 combination).
    RI_1 = P(H=1, A=1). Accept when RI_1 < ri1_max AND EV > 0 (if odds given).
    """
    ri1 = joint_prob(1, 1, lambda_h, lambda_a, rho)
    p_win = 1.0 - ri1

    ev = _ev(p_win, odds_btts_no_under25) if odds_btts_no_under25 > 1.0 else float("nan")
    ev_ok = math.isnan(ev) or ev > 0

    passed = ri1 < ri1_max and ev_ok
    reason = None
    if ri1 >= ri1_max:
        reason = f"RI_1={ri1:.4f} >= {ri1_max}"
    elif not ev_ok:
        reason = f"EV={ev:.4f} <= 0"

    return BREAResult(
        model="2a", passed=passed, ri=round(ri1, 4), p_win=round(p_win, 4),
        ev=round(ev, 4) if not math.isnan(ev) else 0.0,
        kelly_stake_pct=(
            round(_fractional_kelly(p_win, odds_btts_no_under25), 4)
            if passed and odds_btts_no_under25 > 1.0 else 0.0
        ),
        reject_reason=reason,
        details={"lambda_h": lambda_h, "lambda_a": lambda_a, "p_1_1": round(ri1, 6), "rho": rho},
    )


# ── Model 2b: BTTS + Over 3.5 = NO ──────────────────────────────────────────
def model_2b(
    lambda_h: float,
    lambda_a: float,
    odds_btts_no_over35: float = 0.0,
    rho: float = _RHO,
    ri2_max: float = _RI2_MAX,
    max_goals: int = 12,
) -> BREAResult:
    """
    BTTS + Over 3.5 Goals = NO.
    Fails when both teams score AND total ≥ 4.
    RI_2 = SUM_{h≥1, a≥max(1, 4-h)} P(H=h, A=a).
    """
    ri2 = 0.0
    for h in range(1, max_goals + 1):
        for a in range(max(1, 4 - h), max_goals + 1):
            ri2 += joint_prob(h, a, lambda_h, lambda_a, rho)

    p_win = 1.0 - ri2
    ev = _ev(p_win, odds_btts_no_over35) if odds_btts_no_over35 > 1.0 else float("nan")
    ev_ok = math.isnan(ev) or ev > 0
    passed = ri2 < ri2_max and ev_ok
    reason = None
    if ri2 >= ri2_max:
        reason = f"RI_2={ri2:.4f} >= {ri2_max}"
    elif not ev_ok:
        reason = f"EV={ev:.4f} <= 0"

    return BREAResult(
        model="2b", passed=passed, ri=round(ri2, 4), p_win=round(p_win, 4),
        ev=round(ev, 4) if not math.isnan(ev) else 0.0,
        kelly_stake_pct=(
            round(_fractional_kelly(p_win, odds_btts_no_over35), 4)
            if passed and odds_btts_no_over35 > 1.0 else 0.0
        ),
        reject_reason=reason,
        details={"lambda_h": lambda_h, "lambda_a": lambda_a, "rho": rho},
    )


# ── Model 2c: BTTS + Over 4.5 = NO ──────────────────────────────────────────
def model_2c(
    lambda_h: float,
    lambda_a: float,
    odds_btts_no_over45: float = 0.0,
    rho: float = _RHO,
    ri3_max: float = _RI3_MAX,
    max_goals: int = 12,
) -> BREAResult:
    """
    BTTS + Over 4.5 Goals = NO.
    Fails when both teams score AND total ≥ 5.
    RI_3 = SUM_{h≥1, a≥max(1, 5-h)} P(H=h, A=a).
    """
    ri3 = 0.0
    for h in range(1, max_goals + 1):
        for a in range(max(1, 5 - h), max_goals + 1):
            ri3 += joint_prob(h, a, lambda_h, lambda_a, rho)

    p_win = 1.0 - ri3
    ev = _ev(p_win, odds_btts_no_over45) if odds_btts_no_over45 > 1.0 else float("nan")
    ev_ok = math.isnan(ev) or ev > 0
    passed = ri3 < ri3_max and ev_ok
    reason = None
    if ri3 >= ri3_max:
        reason = f"RI_3={ri3:.4f} >= {ri3_max}"
    elif not ev_ok:
        reason = f"EV={ev:.4f} <= 0"

    return BREAResult(
        model="2c", passed=passed, ri=round(ri3, 4), p_win=round(p_win, 4),
        ev=round(ev, 4) if not math.isnan(ev) else 0.0,
        kelly_stake_pct=(
            round(_fractional_kelly(p_win, odds_btts_no_over45), 4)
            if passed and odds_btts_no_over45 > 1.0 else 0.0
        ),
        reject_reason=reason,
        details={"lambda_h": lambda_h, "lambda_a": lambda_a, "rho": rho},
    )


# ── Composite FSS ─────────────────────────────────────────────────────────────
def composite_fss(
    xg_h: float,
    xg_a: float,
    over25_rate_home: float = 0.45,
    over25_rate_away: float = 0.45,
    ri1: float = 0.08,
) -> float:
    """
    BREA Final Selection Score (FSS). Range [0, 1].
    FSS = 0.40 * DS_norm + 0.35 * GM + 0.25 * (1 - RI_1)
    where DS_norm normalises Dominance Score from [-1, 1] to [0, 1].
    """
    total_xg = xg_h + xg_a
    ds = (xg_h - xg_a) / total_xg if total_xg > 0 else 0.0
    ds_norm = (ds + 1.0) / 2.0
    gm = (over25_rate_home + over25_rate_away) / 2.0
    fss = 0.40 * ds_norm + 0.35 * gm + 0.25 * (1.0 - ri1)
    return round(max(0.0, min(1.0, fss)), 4)

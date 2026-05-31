"""
WTCPM — Weak Team Corner Persistence Model.
Ported from betapp/backend/models/wtcpm.py.

Target market: Underdog Over 1.5 Corners.
Core idea: when a weaker team faces a strong favourite, the underdog is
pressed deep and consistently earns corners defending. The Poisson-based
Corner Confidence Score (CCS) quantifies this edge.

P(corners ≥ 2) = 1 − e^{−λ}(1 + λ)   [Poisson CDF complement]

When full H2H corner data is unavailable (common in early integration),
the model falls back to league-average defaults — still gates on the
odds structure and DI so results stay credible.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# ── Default thresholds (overridden by config.WTCPM_* constants) ──────────────
_CCS_MIN: float = 65.0
_DI_MIN: float = 5.0
_STD_F_MAX: float = 1.40
_STD_U_MIN: float = 7.00
_STRONG_F_MAX: float = 1.30
_STRONG_U_MIN: float = 10.00

# League-average defaults used when H2H corner data is unavailable.
# Derived from top-5 European league analysis: underdogs vs top-half sides
# average ~2.8 corners per game; vs title contenders ~2.3 corners.
_DEFAULT_H2H_CORNERS: list[int] = [3, 2, 3, 2, 3]   # conservative centre estimate
_DEFAULT_SOA_CORNERS: list[int] = [2, 3, 2, 2, 3]


@dataclass
class WTCPMResult:
    passed: bool
    di: float = 0.0
    h2h_avg: float = 0.0
    soa: float = 0.0
    pc: float = 0.0
    hsr: float = 0.0
    ssr: float = 0.0
    ccs: float = 0.0
    safety_margin: float = 0.0
    p_corners_ge2: float = 0.0
    ev: float = 0.0
    kelly_stake_pct: float = 0.0
    qualifier_tier: str = "none"   # "standard" | "strong"
    reject_reason: Optional[str] = None
    details: dict = field(default_factory=dict)
    used_defaults: bool = False    # True when H2H data was approximated


def run(
    f_odds: float,
    u_odds: float,
    odds_ud_over15_corners: float,
    h2h_corners: Optional[list[int]] = None,
    soa_corners: Optional[list[int]] = None,
    ccs_min: float = _CCS_MIN,
    di_min: float = _DI_MIN,
    std_f_max: float = _STD_F_MAX,
    std_u_min: float = _STD_U_MIN,
    strong_f_max: float = _STRONG_F_MAX,
    strong_u_min: float = _STRONG_U_MIN,
) -> WTCPMResult:
    """
    Full WTCPM evaluation for one fixture.

    Parameters
    ----------
    f_odds                : Favourite's match odds (lower decimal)
    u_odds                : Underdog's match odds (higher decimal)
    odds_ud_over15_corners: Bookmaker odds for underdog Over 1.5 corners
    h2h_corners           : Underdog corner counts in last N H2H matches
                            (None → use league-average defaults)
    soa_corners           : Underdog corner counts vs strong opponents
                            (None → use league-average defaults)
    """
    used_defaults = False

    # ── Step 1: Qualifier gate ────────────────────────────────────────────
    tier = _qualify(f_odds, u_odds, std_f_max, std_u_min, strong_f_max, strong_u_min)
    if tier == "none":
        return WTCPMResult(
            passed=False,
            reject_reason=f"Odds don't meet qualifier: F={f_odds:.2f}, U={u_odds:.2f}",
        )

    # ── Step 2: Dominance Index ───────────────────────────────────────────
    di = u_odds / f_odds if f_odds > 0 else 0.0
    if di < di_min:
        return WTCPMResult(
            passed=False, di=di, qualifier_tier=tier,
            reject_reason=f"DI={di:.2f} < {di_min}",
        )

    # ── Steps 3-6: Corner analysis ────────────────────────────────────────
    if not h2h_corners:
        h2h_corners = list(_DEFAULT_H2H_CORNERS)
        used_defaults = True
    if not soa_corners:
        soa_corners = list(_DEFAULT_SOA_CORNERS)
        used_defaults = True

    n_h2h = len(h2h_corners) or 1
    h2h_avg = sum(h2h_corners) / n_h2h
    hsr = sum(1 for c in h2h_corners if c >= 2) / n_h2h

    n_soa = len(soa_corners) or 1
    soa = sum(soa_corners) / n_soa
    ssr = sum(1 for c in soa_corners if c >= 2) / n_soa

    # ── Step 7: Weighted corner projection ───────────────────────────────
    pc = 0.6 * h2h_avg + 0.4 * soa
    safety_margin = pc - 1.5

    # ── Step 8: Corner Confidence Score (0–100) ───────────────────────────
    ccs = min(100.0,
              30.0 * hsr +
              30.0 * ssr +
              20.0 * (pc / 5.0) +
              20.0 * (di / 12.0))

    if ccs < ccs_min:
        return WTCPMResult(
            passed=False, di=di, h2h_avg=h2h_avg, soa=soa, pc=pc,
            hsr=hsr, ssr=ssr, ccs=ccs, safety_margin=safety_margin,
            qualifier_tier=tier, used_defaults=used_defaults,
            reject_reason=f"CCS={ccs:.1f} < {ccs_min}",
        )

    # ── Poisson P(corners ≥ 2) ────────────────────────────────────────────
    lam = pc
    p_ge2 = 1.0 - math.exp(-lam) * (1.0 + lam)

    # ── EV gate ───────────────────────────────────────────────────────────
    if odds_ud_over15_corners <= 1.0:
        return WTCPMResult(
            passed=False, di=di, h2h_avg=h2h_avg, soa=soa, pc=pc,
            hsr=hsr, ssr=ssr, ccs=ccs, safety_margin=safety_margin,
            p_corners_ge2=p_ge2, qualifier_tier=tier, used_defaults=used_defaults,
            reject_reason="No valid corner odds",
        )
    ev = p_ge2 * odds_ud_over15_corners - 1.0
    if ev <= 0:
        return WTCPMResult(
            passed=False, di=di, h2h_avg=h2h_avg, soa=soa, pc=pc,
            hsr=hsr, ssr=ssr, ccs=ccs, safety_margin=safety_margin,
            p_corners_ge2=p_ge2, ev=ev, qualifier_tier=tier, used_defaults=used_defaults,
            reject_reason=f"EV={ev:.4f} <= 0",
        )

    b = odds_ud_over15_corners - 1.0
    f_full = (b * p_ge2 - (1.0 - p_ge2)) / b if b > 0 else 0.0
    kelly_pct = round(min(max(0.0, f_full) * 0.25, 0.02), 4)

    return WTCPMResult(
        passed=True,
        di=round(di, 3),
        h2h_avg=round(h2h_avg, 3),
        soa=round(soa, 3),
        pc=round(pc, 3),
        hsr=round(hsr, 3),
        ssr=round(ssr, 3),
        ccs=round(ccs, 2),
        safety_margin=round(safety_margin, 3),
        p_corners_ge2=round(p_ge2, 4),
        ev=round(ev, 4),
        kelly_stake_pct=kelly_pct,
        qualifier_tier=tier,
        used_defaults=used_defaults,
        details={
            "f_odds": f_odds, "u_odds": u_odds,
            "h2h_corners": h2h_corners, "soa_corners": soa_corners,
            "lambda_corners": round(lam, 3),
            "used_defaults": used_defaults,
        },
    )


def _qualify(f_odds, u_odds, std_f_max, std_u_min, strong_f_max, strong_u_min) -> str:
    if f_odds <= strong_f_max and u_odds >= strong_u_min:
        return "strong"
    if f_odds <= std_f_max and u_odds >= std_u_min:
        return "standard"
    return "none"

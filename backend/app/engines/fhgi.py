"""
FHGI — First-Half Goal Intensity Model.
Ported from betapp/backend/models/fhgi.py.

Provides a rigorous probabilistic assessment of "Over 0.5 Goals in the First
Half" using HT exact-score market odds. Replaces titibet's simple threshold
check with a multi-stage signal chain:

  1. Window gate      — O_11 (HT 1:1) in [2.0, 6.0]
  2. Devigging        — normalise 4-way HT CS market to true probabilities
  3. GPI              — Goal Probability Index = devigged P(HT 1:1)
  4. FHGMI            — First-Half Goal Market Intensity = (P_10+P_01+2P_11)/P_00
  5. P_Model          — Logistic: σ(β_0 + β_1 * GPI)
  6. EV gate          — P_Model * O(FH Over 0.5) − 1 > 0

Default logistic coefficients (β_0=0.20, β_1=2.00) are calibrated placeholders.
After 50+ observed matches, refit via logistic regression on (GPI, FH_O05_outcome).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ── Defaults ──────────────────────────────────────────────────────────────────
_O11_MIN: float = 2.0
_O11_MAX: float = 6.0
_FHGMI_MIN: float = 1.50


@dataclass
class FHGIResult:
    passed: bool
    gpi: float = 0.0
    fhgmi: float = 0.0
    p_model: float = 0.0
    p_market: float = 0.0
    ev: float = 0.0
    kelly_stake_pct: float = 0.0
    overround_ht: float = 1.0
    reject_reason: Optional[str] = None
    details: dict = field(default_factory=dict)


def run(
    o_11: float,
    o_10: float,
    o_01: float,
    o_00: float,
    o_fh_over05: float,
    beta_0: float = 0.20,
    beta_1: float = 2.00,
    o11_min: float = _O11_MIN,
    o11_max: float = _O11_MAX,
    fhgmi_min: float = _FHGMI_MIN,
) -> FHGIResult:
    """
    Full FHGI evaluation for one fixture.

    Parameters
    ----------
    o_11, o_10, o_01, o_00 : HT exact-score odds for 1:1, 1:0, 0:1, 0:0
    o_fh_over05            : Bookmaker odds for FH Over 0.5 goals
    beta_0, beta_1         : Logistic regression coefficients (placeholder defaults)
    """
    # ── Step 1: Window gate ───────────────────────────────────────────────
    if not (o11_min <= o_11 <= o11_max):
        return FHGIResult(
            passed=False,
            reject_reason=f"O_11={o_11:.2f} outside [{o11_min}, {o11_max}]",
        )

    # ── Step 2: Devigging (4-way HT exact-score market) ──────────────────
    raw = [1.0 / o for o in (o_00, o_10, o_01, o_11) if o > 0]
    if len(raw) < 4:
        return FHGIResult(passed=False, reject_reason="Invalid HT CS odds")
    k_ht = sum(raw)
    p_00_t, p_10_t, p_01_t, p_11_t = [r / k_ht for r in raw]

    # ── Step 3: GPI = devigged P(HT 1:1) ─────────────────────────────────
    gpi = p_11_t

    # ── Step 4: FHGMI ────────────────────────────────────────────────────
    if p_00_t <= 0:
        return FHGIResult(passed=False, reject_reason="p_00 = 0; invalid market")
    fhgmi = (p_10_t + p_01_t + 2.0 * p_11_t) / p_00_t

    if fhgmi < fhgmi_min:
        return FHGIResult(
            passed=False, gpi=round(gpi, 4), fhgmi=round(fhgmi, 4),
            overround_ht=round(k_ht, 4),
            reject_reason=f"FHGMI={fhgmi:.3f} < {fhgmi_min}",
        )

    # ── Step 5: P_Model (logistic) ───────────────────────────────────────
    log_odds = beta_0 + beta_1 * gpi
    p_model = 1.0 / (1.0 + math.exp(-log_odds))

    # ── Step 6: Devigged market probability ──────────────────────────────
    p_market_raw = 1.0 / o_fh_over05 if o_fh_over05 > 1.0 else 0.0
    p_market = p_market_raw / 1.05  # approximate binary devig

    # ── Step 7: EV gate ──────────────────────────────────────────────────
    ev = p_model * o_fh_over05 - 1.0
    if ev <= 0:
        return FHGIResult(
            passed=False, gpi=round(gpi, 4), fhgmi=round(fhgmi, 4),
            p_model=round(p_model, 4), p_market=round(p_market, 4),
            ev=round(ev, 4), overround_ht=round(k_ht, 4),
            reject_reason=f"EV={ev:.4f} <= 0",
        )

    # ── Kelly ─────────────────────────────────────────────────────────────
    b = o_fh_over05 - 1.0
    f_full = (b * p_model - (1.0 - p_model)) / b if b > 0 else 0.0
    kelly_pct = round(min(max(0.0, f_full) * 0.25, 0.02), 4)

    return FHGIResult(
        passed=True,
        gpi=round(gpi, 4),
        fhgmi=round(fhgmi, 4),
        p_model=round(p_model, 4),
        p_market=round(p_market, 4),
        ev=round(ev, 4),
        kelly_stake_pct=kelly_pct,
        overround_ht=round(k_ht, 4),
        details={
            "o_11": o_11, "o_10": o_10, "o_01": o_01, "o_00": o_00,
            "o_fh_over05": o_fh_over05,
            "p_00_devigged": round(p_00_t, 4),
            "p_10_devigged": round(p_10_t, 4),
            "p_01_devigged": round(p_01_t, 4),
            "p_11_devigged": round(p_11_t, 4),
            "beta_0": beta_0, "beta_1": beta_1,
        },
    )

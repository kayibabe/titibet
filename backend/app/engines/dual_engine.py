"""
dual_engine.py -- Fuses Bayesian and Poisson engine outputs into DualSignal.

Fusion rules:
- Both agree, no contradiction -> High/Medium/Low based on combined confidence
- Contradiction detected -> Low confidence, zero stake
- Bayesian only -> downgrade confidence by one tier
- Poisson only -> downgrade confidence by one tier

Quality score weights Bayesian more heavily (60%) when both agree, since it uses
a broader bookmaker consensus vs the Poisson model which has fewer CS data points.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.config import get_settings
from app.engines.bayesian import BayesianResult
from app.engines.poisson import PoissonResult

settings = get_settings()


@dataclass
class DualSignal:
    fixture_id: int
    market: str

    bayesian: Optional[BayesianResult]
    poisson: Optional[PoissonResult]

    agreement: str          # Both / Bayesian Only / Poisson Only / Contradiction / None
    confidence: str         # High / Medium / Low / None
    quality_score: float
    recommended_stake_pct: float
    contradiction: bool
    mixed_signals: list[str]


def _unit_stake(confidence: str) -> float:
    return {"High": 3, "Medium": 2, "Low": 1}.get(confidence, 0) * settings.unit_pct


def _recommended_stake(kelly_pct: float, confidence: str, agreement: str) -> float:
    # Zero-stake anything with no genuine conviction: contradictions, no-confidence,
    # and Low confidence (single-engine or mild disagreement) all carry no edge.
    if agreement == "Contradiction" or confidence in ("None", "Low"):
        return 0.0
    unit = _unit_stake(confidence)
    # kelly_pct is already the fractional Kelly (fraction applied in bayesian._kelly).
    # Do NOT multiply by settings.kelly_fraction again -- that would apply the
    # fraction twice (e.g. 0.25 x 0.25 = 6.25% instead of the intended 25%).
    base = min(kelly_pct, unit)
    # No agreement/confidence stake multiplier — empirical data (n=208 settled,
    # 2026-06-23) shows Both+High picks hit 65.3% vs Poisson Only 85.0%.
    # The higher odds on Both+High do not compensate; Kelly fraction is lower
    # (10% vs 17.4%). Adding a boost here would over-stake the weaker signal.
    return round(base, 4)


def fuse(
    fixture_id: int,
    market: str,
    bayesian: Optional[BayesianResult],
    poisson: Optional[PoissonResult],
    mixed_signals: list[str],
) -> DualSignal:
    b_ok = bayesian is not None and bayesian.is_value
    p_ok = poisson is not None and poisson.rule_pass

    contradiction = bool(mixed_signals)

    # Poisson quality contribution: model probability (edge-vs-market retired
    # 2026-07-02). Both this and bayesian.quality_score live in [0, 1].
    p_prob = (poisson.poisson_prob or 0.0) if poisson else 0.0

    if b_ok and p_ok:
        if contradiction:
            # Both engines fired but in opposing directions -- no actionable signal.
            # Zero the quality score so contradictory signals rank last.
            agreement = "Contradiction"
            confidence = "Low"
            qs = 0.0
        elif not contradiction and bayesian.confidence == "High" and poisson.grade == "A":
            agreement = "Both"
            confidence = "High"
            qs = bayesian.quality_score * 0.6 + p_prob * 0.4
        elif bayesian.confidence in ("High", "Medium") or poisson.grade in ("A", "B"):
            agreement = "Both"
            confidence = "Medium"
            qs = bayesian.quality_score * 0.6 + p_prob * 0.3
        else:
            agreement = "Both"
            confidence = "Low"
            qs = bayesian.quality_score * 0.5
    elif b_ok and not p_ok:
        # Downgrade Bayesian confidence by one tier
        tier_down = {"High": "Medium", "Medium": "Low", "Low": "None"}
        agreement = "Bayesian Only"
        confidence = tier_down.get(bayesian.confidence, "None")
        qs = bayesian.quality_score * 0.6
    elif not b_ok and p_ok:
        # Downgrade Poisson grade by one tier
        tier_down = {"A": "Medium", "B": "Low", "C": "None"}
        agreement = "Poisson Only"
        confidence = tier_down.get(poisson.grade, "None")
        qs = p_prob * 0.4

    else:
        agreement = "None"
        confidence = "None"
        qs = 0.0

    # (Edge-based demotion and the negative-edge Poisson-only hard block were
    # removed 2026-07-02: signals are no longer accepted or rejected on
    # model-vs-market edge.)

    # Market+agreement suppression -- audit-validated (2026-06-03, n=924 settled).
    # Home Over 1.5 Poisson Only: Low+Poisson=-35.7% ROI (n=30), Medium+Poisson=-10.5% (n=41).
    # Only Both+High has genuine edge in this market (+61.7% ROI, n=14).
    # Poisson fires because lambda looks high; Bayesian disagrees because the bookmaker
    # has already priced the move and the implied edge is zero.
    if market == "Home Over 1.5" and agreement == "Poisson Only":
        confidence = "None"

    # Away Over 0.5 in the 80-90% probability band: audit 2026-06-03 showed -20pp
    # calibration error (17 bets, 64.7% actual hit vs ~85% model probability).
    # The bookmaker has priced these as near-certainties; our model independently
    # agrees but is overconfident -- the market is right more often than we are.
    # Block signals where bayesian derived_prob falls in [0.80, 0.90).
    if market == "Away Over 0.5" and bayesian is not None:
        _ao05_prob = bayesian.derived_prob or 0.0
        if 0.80 <= _ao05_prob < 0.90:
            confidence = "None"

    kelly_pct = (bayesian.kelly_pct if bayesian else 0.0) or 0.0
    stake = _recommended_stake(kelly_pct, confidence, agreement)

    return DualSignal(
        fixture_id=fixture_id, market=market,
        bayesian=bayesian, poisson=poisson,
        agreement=agreement, confidence=confidence,
        quality_score=round(qs, 4),
        recommended_stake_pct=stake,
        contradiction=contradiction,
        mixed_signals=mixed_signals,
    )

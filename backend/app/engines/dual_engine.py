"""
dual_engine.py — Fuses Bayesian and Poisson engine outputs into DualSignal.

Fusion rules:
- Both agree, no contradiction → High/Medium/Low based on combined confidence
- Contradiction detected → Low confidence, zero stake
- Bayesian only → downgrade confidence by one tier
- Poisson only → downgrade confidence by one tier

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
    # Apply fractional Kelly — scale down raw Kelly by the configured fraction (default 0.25)
    # so we bet a conservative quarter of what full Kelly would suggest.
    fractional_kelly = kelly_pct * settings.kelly_fraction
    base = min(fractional_kelly, unit)
    if agreement == "Both" and confidence == "High":
        base = min(base * 1.5, settings.max_kelly_pct)
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

    # Normalise Poisson edge_pct (percentage → fraction) so quality scores stay in [0, 1].
    # bayesian.quality_score is already in [0, 1] after the Bayesian engine fix.
    p_edge = ((poisson.edge_pct or 0) / 100.0) if poisson else 0.0

    if b_ok and p_ok:
        if contradiction:
            # Both engines fired but in opposing directions — no actionable signal.
            # Zero the quality score so the signal never reaches accumulator candidacy.
            agreement = "Contradiction"
            confidence = "Low"
            qs = 0.0
        elif not contradiction and bayesian.confidence == "High" and poisson.grade == "A":
            agreement = "Both"
            confidence = "High"
            qs = bayesian.quality_score * 0.6 + p_edge * 0.4
        elif bayesian.confidence in ("High", "Medium") or poisson.grade in ("A", "B"):
            agreement = "Both"
            confidence = "Medium"
            qs = bayesian.quality_score * 0.6 + p_edge * 0.3
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
        qs = p_edge * 0.4
    else:
        agreement = "None"
        confidence = "None"
        qs = 0.0

    # Demote confidence one tier when Bayesian edge falls below the minimum threshold.
    # Prevents high-agreement-but-low-edge signals from receiving full staking.
    # Guard: only demote High or Medium — a signal already at Low should NOT be pushed
    # to None here, because the Bayesian engine already assigned Low precisely because
    # the edge was thin.  Double-demoting it silently drops valid Both/Low agreements.
    if bayesian is not None and (bayesian.edge or 0.0) < settings.min_value_edge and confidence in ("High", "Medium"):
        demotion = {"High": "Medium", "Medium": "Low"}
        confidence = demotion.get(confidence, confidence)

    # Hard block: when Bayesian edge is genuinely negative (bookmaker's implied
    # probability exceeds our model's probability), Poisson-only signals have no
    # business in the feed — we're firing on pattern alone against the market.
    # Audit evidence: 24 Home Over 1.5 + 18 Away Over 1.5 negative-edge signals
    # showed -12% ROI at ~50% hit rate; the market was right, our model was wrong.
    if (bayesian is not None
            and (bayesian.edge or 0.0) < 0.0
            and agreement == "Poisson Only"):
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

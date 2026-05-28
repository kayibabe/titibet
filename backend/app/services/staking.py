"""
staking.py — Kelly + unit-based staking recommendations.
Ported from FootBet odds_engine.py (Kelly) + TiTiBet tracker (unit staking).
"""
from __future__ import annotations
from app.core.config import get_settings

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


def recommended_stake_pct(
    kelly_pct: float,
    confidence: str,
    agreement: str,
    unit_pct: float | None = None,
) -> float:
    """
    Recommended stake = min(kelly, unit_stake), adjusted by dual confidence.
    Zeroed out for contradictions and 'None' confidence.
    Boosted ×1.5 (capped at 5%) for dual High signals.
    """
    if agreement == "Contradiction" or confidence == "None":
        return 0.0
    unit = unit_stake_pct(confidence, unit_pct)
    base = min(kelly_pct, unit) if unit > 0 else kelly_pct
    if agreement == "Both" and confidence == "High":
        base = min(base * 1.5, settings.max_kelly_pct)
    return round(base, 4)

from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class BookmakerOdds(BaseModel):
    bookmaker: str
    selection: str
    odds: float


class BayesianOut(BaseModel):
    prob: Optional[float] = None
    edge: Optional[float] = None
    best_odd: Optional[float] = None
    bookmaker: Optional[str] = None
    overround: Optional[float] = None
    coverage: Optional[float] = None
    bookmaker_count: Optional[int] = None
    is_value: Optional[bool] = None
    confidence: Optional[str] = None
    quality_score: Optional[float] = None
    kelly_pct: Optional[float] = None


class PoissonOut(BaseModel):
    lambda_h: Optional[float] = None
    lambda_a: Optional[float] = None
    lambda_total: Optional[float] = None
    prob: Optional[float] = None
    rule_key: Optional[str] = None
    rule_pass: Optional[bool] = None
    rule_strong: Optional[bool] = None
    edge_pct: Optional[float] = None
    grade: Optional[str] = None
    # Fixture-level contradiction descriptions emitted by the Poisson engine.
    # The list is denormalised onto every Signal row for the fixture, so callers
    # can render alongside the per-row `contradiction` flag.
    mixed_signals: Optional[list[str]] = None


class AdvancedModelsOut(BaseModel):
    """
    Advanced model outputs attached to each signal.
    All fields are optional — present only when the relevant engine fired.
    """
    # BOS 2.0 — Match Stability Index
    bos_si: Optional[float] = None
    bos_passed: Optional[bool] = None

    # ZINB — expected goals from Zero-Inflated Negative Binomial model
    zinb_lambda_h: Optional[float] = None
    zinb_lambda_a: Optional[float] = None

    # Glicko-2 rating differential (home_r − away_r)
    glicko_r_diff: Optional[float] = None
    # Days since the staler team's last match (>14 = stale; certainty is zeroed in ranking)
    glicko_rating_age_days: Optional[int] = None



class AlternativeSignal(BaseModel):
    market: str
    dual_confidence: Optional[str] = None
    primary_prob: Optional[float] = None
    best_odd: Optional[float] = None


class SignalOut(BaseModel):
    id: int
    fixture_id: int
    market: str
    bayesian: Optional[BayesianOut] = None
    poisson: Optional[PoissonOut] = None
    dual_confidence: str
    dual_agreement: str
    dual_quality_score: Optional[float] = None
    dual_recommended_stake_pct: Optional[float] = None
    contradiction: bool
    computed_at: Optional[datetime] = None

    # Selection label for tracker/UI (independent of bayesian.* — kept top-level)
    selection_name: Optional[str] = None

    # Displayable market odds, always populated when the signal has a price —
    # including Poisson-only signals, whose `bayesian` block is None even though
    # the row carries bookmaker odds (best_odd falls back to poi_signal_odds).
    best_odd: Optional[float] = None
    best_bookmaker: Optional[str] = None

    # All bookmaker prices for this market (populated in deep-dive endpoint)
    bookmaker_odds: Optional[list[BookmakerOdds]] = None

    # Line movement — negative means odds shortened (steam move confirmed our edge)
    odds_drift_pct: Optional[float] = None

    # Advanced model enrichment (BOS, ZINB, Glicko-2, BREA, FHGI)
    advanced: Optional[AdvancedModelsOut] = None

    # Banker designation — top-ranked High-confidence picks for the day
    is_banker: Optional[bool] = None

    # Alternative markets from the same fixture (up to 2, populated in router)
    alternatives: list[AlternativeSignal] = []

    # Fatigue flags — True when the team played 2+ matches in the prior 7 days
    fatigue_home: Optional[bool] = None
    fatigue_away: Optional[bool] = None

    # Denormalised fixture fields (populated in router)
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    league: Optional[str] = None
    league_tier: Optional[int] = None
    country: Optional[str] = None
    kickoff_at: Optional[datetime] = None
    status: Optional[str] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None

    model_config = {"from_attributes": True}

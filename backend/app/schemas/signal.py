from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


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
    ev_pct: Optional[float] = None


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

    # Explicit Expected Value (p_model × best_odd − 1)
    ev_score: Optional[float] = None

    # Glicko-2 rating differential (home_r − away_r)
    glicko_r_diff: Optional[float] = None

    # BREA — BTTS risk enrichment (BTTS Yes signals only)
    brea_ri1: Optional[float] = None     # P(1:1) — only losing case for BTTS+U2.5 NO
    brea_fss: Optional[float] = None     # Final Selection Score [0-1]

    # FHGI — First-Half Goal Intensity (Over 0.5 1H signals only)
    fhgi_gpi: Optional[float] = None     # Goal Probability Index = devigged P(HT 1:1)
    fhgi_fhgmi: Optional[float] = None   # FHGMI ratio
    fhgi_p_model: Optional[float] = None  # Logistic model P(FH Over 0.5)


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

    # All bookmaker prices for this market (populated in deep-dive endpoint)
    bookmaker_odds: Optional[list[BookmakerOdds]] = None

    # Line movement — negative means odds shortened (steam move confirmed our edge)
    odds_drift_pct: Optional[float] = None

    # Advanced model enrichment (BOS, ZINB, Glicko-2, BREA, FHGI)
    advanced: Optional[AdvancedModelsOut] = None

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


class RecommendedTicketLegOut(BaseModel):
    signal_id: int
    fixture_id: int
    match_name: str
    home_team: str
    away_team: str
    league: Optional[str] = None
    league_tier: Optional[int] = None
    kickoff_at: Optional[datetime] = None
    event_date: Optional[str] = None
    market: str
    selection_name: str
    bookmaker: str
    odds: float
    probability: Optional[float] = None
    ev_pct: Optional[float] = None
    confidence: Optional[str] = None
    agreement: Optional[str] = None
    recommended_stake_pct: Optional[float] = None
    source_rule_key: Optional[str] = None
    signal_grade: Optional[str] = None
    odds_drift_pct: Optional[float] = None
    why_tags: list[str] = Field(default_factory=list)


class RecommendedTicketCardOut(BaseModel):
    key: str
    label: str
    combined_odds: Optional[float] = None
    win_probability_estimate: Optional[float] = None
    low_win_prob_warning: Optional[bool] = None
    very_low_win_prob: Optional[bool] = None
    summary_tags: list[str] = Field(default_factory=list)
    legs: list[RecommendedTicketLegOut] = Field(default_factory=list)
    empty_reason: Optional[str] = None


class RecommendedTicketsResponse(BaseModel):
    date: str
    generation_mode: str
    cards: list[RecommendedTicketCardOut]


# ── TiTiBet Named Ticket system ───────────────────────────────────────────────

class TitibetSubTicketOut(BaseModel):
    """One sub-ticket within the Pro bundle (High Conf ACCA, Goals ACCA, etc.)"""
    key: str
    label: str
    description: str = ""
    legs: list[RecommendedTicketLegOut] = Field(default_factory=list)
    combined_odds: Optional[float] = None
    win_probability_estimate: Optional[float] = None
    low_win_prob_warning: Optional[bool] = None
    very_low_win_prob: Optional[bool] = None
    summary_tags: list[str] = Field(default_factory=list)
    empty_reason: Optional[str] = None
    # singles = True means these are tracked individually, not as one acca
    is_singles: bool = False


class TitibetGeneralTicketOut(BaseModel):
    key: str = "general"
    label: str = "TiTiBet General"
    description: str = "All signal matches for today"
    legs: list[RecommendedTicketLegOut] = Field(default_factory=list)
    combined_odds: Optional[float] = None
    win_probability_estimate: Optional[float] = None
    low_win_prob_warning: Optional[bool] = None
    very_low_win_prob: Optional[bool] = None
    empty_reason: Optional[str] = None


class TitibetFreeTicketOut(BaseModel):
    key: str = "free"
    label: str = "TiTiBet Free"
    description: str = "3 selected picks for today"
    # The 3 highlighted picks
    selected_legs: list[RecommendedTicketLegOut] = Field(default_factory=list)
    # All other legs shown greyed (full details retained, just styled differently)
    other_legs: list[RecommendedTicketLegOut] = Field(default_factory=list)
    combined_odds: Optional[float] = None
    win_probability_estimate: Optional[float] = None
    empty_reason: Optional[str] = None


class TitibetProTicketOut(BaseModel):
    key: str = "pro"
    label: str = "TiTiBet Pro"
    description: str = "Premium ticket bundle"
    sub_tickets: list[TitibetSubTicketOut] = Field(default_factory=list)


class TitibetTicketsResponse(BaseModel):
    date: str
    generation_mode: str = "titibet_tickets"
    general: TitibetGeneralTicketOut
    free: TitibetFreeTicketOut
    pro: TitibetProTicketOut

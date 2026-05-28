from __future__ import annotations
from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, Field


class TrackPickRequest(BaseModel):
    fixture_id: Optional[int] = None
    bookmaker: str
    event_date: Optional[date] = None
    match_name: str
    league: Optional[str] = None
    market_type: str
    selection_name: str
    odds: float
    stake: float = 1.0
    recommended_stake_pct: Optional[float] = None
    source_rule_key: Optional[str] = None
    source_rule_label: Optional[str] = None
    signal_grade: Optional[str] = None
    dual_confidence: Optional[str] = None
    dual_agreement: Optional[str] = None
    notes: Optional[str] = None


class BetUpdate(BaseModel):
    stake: Optional[float] = None
    result_status: Optional[str] = None
    notes: Optional[str] = None


class BetOut(BaseModel):
    id: int
    fixture_id: Optional[int] = None
    bookmaker: str
    event_date: Optional[date] = None
    match_name: str
    home_team: Optional[str] = None   # resolved from fixture join in list_bets
    away_team: Optional[str] = None   # resolved from fixture join in list_bets
    league: Optional[str] = None
    market_type: str
    selection_name: str
    odds: float
    stake: float
    recommended_stake_pct: Optional[float] = None
    source_rule_key: Optional[str] = None
    source_rule_label: Optional[str] = None
    signal_grade: Optional[str] = None
    dual_confidence: Optional[str] = None
    dual_agreement: Optional[str] = None
    result_status: str
    profit_loss: float
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    settled_at: Optional[datetime] = None
    closing_odds: Optional[float] = None
    clv_pct: Optional[float] = None

    # ── Match result (resolved from the fixture join in list_bets) ─────────────
    # Both scores will be None until the match completes and ingestion picks up
    # the final result. Frontend treats `home_score != null && away_score != null`
    # as the "match has finished" gate — fixture_status is informational only.
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    fixture_status: Optional[str] = None
    kickoff_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AccumulatorLegRequest(BaseModel):
    tracked_bet_id: int
    leg_order: int = 0


class AccumulatorCreate(BaseModel):
    name: Optional[str] = None
    ticket_date: Optional[date] = None
    stake: float = 1.0
    legs: list[AccumulatorLegRequest]


class AccumulatorOut(BaseModel):
    id: int
    ticket_date: Optional[date] = None
    name: Optional[str] = None
    stake: float
    combined_odds: Optional[float] = None
    result_status: str
    profit_loss: float
    created_at: Optional[datetime] = None
    legs: list[dict] = Field(default_factory=list)
    # card_key (e.g. "titibet_free") | "goals_acca" | "manual"
    ticket_source: Optional[str] = "manual"

    model_config = {"from_attributes": True}


class ConfirmRecommendedTicketLegIn(BaseModel):
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


class ConfirmRecommendedTicketIn(BaseModel):
    card_key: str
    stake: float
    ticket_date: Optional[date] = None
    ticket_name: Optional[str] = None
    legs: list[ConfirmRecommendedTicketLegIn]


class ConfirmRecommendedTicketOut(BaseModel):
    card_key: str
    accumulator_ticket_id: int
    combined_odds: float
    tracked_bets: list[BetOut]
    message: str

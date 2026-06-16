from __future__ import annotations
from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel


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



"""
loss_analysis.py — DB model for AI-generated loss analysis records.

Each settled "Lost" tracked_bet gets one LossAnalysis row per run of the
loss_analysis_agent. The agent tags the failure with structured categories and
a natural-language narrative, which the system aggregates into threshold
recommendations fed back into performance_intelligence.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from sqlalchemy import Integer, String, Float, Date, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class LossAnalysis(Base):
    __tablename__ = "loss_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # The tracked bet this analysis refers to
    tracked_bet_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tracked_bets.id"), index=True, nullable=False
    )

    # Snapshot fields (denormalised for fast reads without joins)
    event_date: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)
    match_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    league: Mapped[Optional[str]] = mapped_column(String(120), index=True, nullable=True)
    league_tier: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    market_type: Mapped[Optional[str]] = mapped_column(String(120), index=True, nullable=True)
    odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dual_confidence: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    source_rule_key: Mapped[Optional[str]] = mapped_column(String(40), index=True, nullable=True)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # AI agent output
    agent_id: Mapped[str] = mapped_column(String(40), default="loss_analyst", nullable=False)

    # Comma-separated failure category tags, e.g. "high_odds_risk,tier3_exposure"
    # Categories: high_odds_risk | tier3_exposure | zero_zero | end_of_season |
    #             defensive_game | market_mispricing | weak_home_team |
    #             away_team_blank | model_overconfidence | data_gap
    failure_categories: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Natural-language explanation of why the bet lost
    narrative: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Concrete recommendation for the system (e.g. "Cap home_o05 at 2.00 odds")
    recommendation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Severity score 1-10: how avoidable was this loss given available data?
    # 10 = completely avoidable with better filters; 1 = genuine bad luck
    avoidability_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    tracked_bet: Mapped[Optional["TrackedBet"]] = relationship(  # noqa: F821
        "TrackedBet", foreign_keys=[tracked_bet_id]
    )

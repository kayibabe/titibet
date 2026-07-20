"""
learning_proposal.py — Persisted output of the Threshold Tuner + Backtester pipeline.

Each row represents a concrete, backtester-validated threshold change.
Lifecycle:
  1. loss_analysis_agent / strategy_pipeline write accepted proposals here
     (deactivating any prior active row for the same target first).
  2. Signal scoring reads active proposals to apply threshold overrides.
  3. Rows are never hard-deleted — set is_active = False to retire them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.database import Base


class LearningProposal(Base):
    __tablename__ = "learning_proposals"
    __table_args__ = (
        Index("ix_lp_change_type_target", "change_type", "target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # What kind of threshold this modifies.
    # Currently used values: "market_odds_ceiling"
    # Reserved for future use: "tier_suppression", "min_confidence",
    #                          "rule_disable", "quality_threshold"
    change_type: Mapped[str] = mapped_column(String(60), nullable=False)

    # The specific target (market name, rule key, tier label, etc.)
    # e.g. "Home Over 0.5", "poisson_over_1_5", "Tier3"
    target: Mapped[str] = mapped_column(String(120), nullable=False)

    # The new threshold value proposed by the Tuner and validated by the Backtester.
    proposed_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Human-readable explanation produced by the Threshold Tuner LLM.
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Backtester confidence in the proposal: "High" / "Medium" / "Low"
    confidence: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Backtester summary note (wins avoided vs wins sacrificed)
    backtest_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Only one active proposal per (change_type, target) should be True at once.
    # When a new proposal supersedes an older one, the old row is set False.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
        onupdate=func.now(),
    )

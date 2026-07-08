from datetime import datetime, date
from typing import Optional
from sqlalchemy import Integer, String, Float, Boolean, Date, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class TrackedBet(Base):
    __tablename__ = "tracked_bets"
    __table_args__ = (
        UniqueConstraint("user_id", "fixture_id", "bookmaker", "market_type", "selection_name", name="uq_bet"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    fixture_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("fixtures.id"), index=True, nullable=True)
    bookmaker: Mapped[str] = mapped_column(String(80), index=True)
    event_date: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)
    match_name: Mapped[str] = mapped_column(String(255), index=True)
    league: Mapped[Optional[str]] = mapped_column(String(120), index=True, nullable=True)
    market_type: Mapped[str] = mapped_column(String(120), index=True)
    selection_name: Mapped[str] = mapped_column(String(120), index=True)
    odds: Mapped[float] = mapped_column(Float)
    stake: Mapped[float] = mapped_column(Float, default=1.0)
    recommended_stake_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_rule_key: Mapped[Optional[str]] = mapped_column(String(40), index=True, nullable=True)
    source_rule_label: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    signal_grade: Mapped[Optional[str]] = mapped_column(String(4), index=True, nullable=True)
    dual_confidence: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    dual_agreement: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    data_completeness: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    result_status: Mapped[str] = mapped_column(String(16), default="Pending", index=True)
    profit_loss: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    acca_ticket_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)

    # Closing Line Value
    # closing_odds: best market price for this market just before kickoff
    # clv_pct:      (closing_odds / bet_odds - 1) * 100  -- positive = beat the close
    closing_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    clv_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=func.now(), nullable=True)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    fixture: Mapped[Optional["Fixture"]] = relationship("Fixture", back_populates="tracked_bets")  # noqa: F821

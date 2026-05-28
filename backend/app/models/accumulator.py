from datetime import datetime, date
from typing import Optional
from sqlalchemy import Integer, String, Float, Date, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class AccumulatorTicket(Base):
    __tablename__ = "accumulator_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    ticket_date: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    stake: Mapped[float] = mapped_column(Float, default=1.0)
    combined_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    result_status: Mapped[str] = mapped_column(String(16), default="Pending", index=True)
    profit_loss: Mapped[float] = mapped_column(Float, default=0.0)
    # Source of the ticket: card_key (e.g. "titibet_free") | "goals_acca" | "manual"
    ticket_source: Mapped[Optional[str]] = mapped_column(String(60), nullable=True, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=func.now(), nullable=True)

    legs: Mapped[list["AccumulatorLeg"]] = relationship("AccumulatorLeg", back_populates="ticket", cascade="all, delete-orphan")


class AccumulatorLeg(Base):
    __tablename__ = "accumulator_legs"
    __table_args__ = (
        UniqueConstraint("ticket_id", "tracked_bet_id", name="uq_acca_leg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(Integer, ForeignKey("accumulator_tickets.id"), index=True)
    tracked_bet_id: Mapped[int] = mapped_column(Integer, ForeignKey("tracked_bets.id"), index=True)
    leg_order: Mapped[int] = mapped_column(Integer, default=0)

    ticket: Mapped["AccumulatorTicket"] = relationship("AccumulatorTicket", back_populates="legs")
    tracked_bet: Mapped["TrackedBet"] = relationship("TrackedBet", back_populates="accumulator_legs")  # noqa: F821

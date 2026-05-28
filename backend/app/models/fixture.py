from datetime import datetime, date
from typing import Optional
from sqlalchemy import Integer, String, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Fixture(Base):
    __tablename__ = "fixtures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_fixture_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    event_date: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)
    kickoff_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True, nullable=True)
    home_team: Mapped[str] = mapped_column(String(120))
    away_team: Mapped[str] = mapped_column(String(120))
    country: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    league: Mapped[Optional[str]] = mapped_column(String(120), index=True, nullable=True)
    league_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    league_tier: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    season: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(60), index=True, nullable=True)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=func.now(), nullable=True)

    market_snapshots: Mapped[list["MarketSnapshot"]] = relationship("MarketSnapshot", back_populates="fixture", lazy="selectin")  # noqa: F821
    signals: Mapped[list["Signal"]] = relationship("Signal", back_populates="fixture", lazy="selectin")  # noqa: F821
    tracked_bets: Mapped[list["TrackedBet"]] = relationship("TrackedBet", back_populates="fixture", lazy="selectin")  # noqa: F821

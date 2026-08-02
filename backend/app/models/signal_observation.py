from datetime import datetime, date
from typing import Optional
from sqlalchemy import Integer, String, Float, Date, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class SignalObservation(Base):
    __tablename__ = "signal_observations"

    id:               Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    observation_type: Mapped[str]            = mapped_column(String(40), index=True)
    fixture_id:       Mapped[Optional[int]]  = mapped_column(Integer, ForeignKey("fixtures.id"), index=True, nullable=True)
    event_date:       Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)
    match_name:       Mapped[str]            = mapped_column(String(255))
    league:           Mapped[Optional[str]]  = mapped_column(String(120), nullable=True)
    country:          Mapped[Optional[str]]  = mapped_column(String(80), nullable=True)
    league_tier:      Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    market_type:      Mapped[str]            = mapped_column(String(120))
    dual_agreement:   Mapped[Optional[str]]  = mapped_column(String(40), nullable=True)
    dual_confidence:  Mapped[Optional[str]]  = mapped_column(String(10), nullable=True)
    signal_grade:     Mapped[Optional[str]]  = mapped_column(String(4), nullable=True)
    odds:             Mapped[float]          = mapped_column(Float)
    bookmaker:        Mapped[Optional[str]]  = mapped_column(String(80), nullable=True)
    result_status:    Mapped[str]            = mapped_column(String(16), default="Pending", index=True)
    profit_loss:      Mapped[float]          = mapped_column(Float, default=0.0)
    created_at:       Mapped[datetime]       = mapped_column(DateTime, server_default=func.now())
    settled_at:       Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

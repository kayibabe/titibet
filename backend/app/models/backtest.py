from datetime import datetime, date
from typing import Optional
from sqlalchemy import Integer, String, Float, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    fixture_date: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)
    league_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    league_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    league_tier: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_team: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    away_team: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    market: Mapped[str] = mapped_column(String(80), index=True)
    source_engine: Mapped[str] = mapped_column(String(20), default="dual")
    derived_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_odd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dual_confidence: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    bet_result: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1=won, 0=lost, -1=unknown
    profit_loss: Mapped[float] = mapped_column(Float, default=0.0)
    flat_stake: Mapped[float] = mapped_column(Float, default=1.0)
    kelly_stake: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

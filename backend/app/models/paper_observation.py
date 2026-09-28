"""Zero-stake prospective observations backed by immutable signal decisions."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PaperObservation(Base):
    __tablename__ = "paper_observations"
    __table_args__ = (
        UniqueConstraint(
            "fixture_id", "market_type", "strategy_version_id",
            name="uq_paper_observation_fixture_market_strategy",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(Integer, ForeignKey("signal_decisions.id"), nullable=False, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    kickoff_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    odds: Mapped[float] = mapped_column(Float, nullable=False)
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    quote_id: Mapped[int] = mapped_column(Integer, ForeignKey("odds_quotes.id"), nullable=False)
    feature_snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("feature_snapshots.id"), nullable=False)
    model_version_id: Mapped[int] = mapped_column(Integer, ForeignKey("model_versions.id"), nullable=False)
    strategy_version_id: Mapped[int] = mapped_column(Integer, ForeignKey("strategy_versions.id"), nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(40), nullable=False, default="prospective")
    result_status: Mapped[str] = mapped_column(String(20), nullable=False, default="Pending", index=True)
    profit_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

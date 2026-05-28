from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, String, Float, DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (
        Index("ix_ms_fixture_bookmaker", "fixture_id", "bookmaker"),
        Index("ix_ms_fixture_market", "fixture_id", "market_type"),
        Index("ix_ms_fixture_pulledat", "fixture_id", "pulled_at"),
        # Prevents duplicate rows when the same date is synced multiple times.
        # The ingestion service also enforces this via cache-aware upsert logic,
        # but this constraint is a hard DB-level guarantee for new databases.
        UniqueConstraint(
            "fixture_id", "bookmaker", "market_type", "selection_name",
            name="uq_ms_fixture_bookie_market_sel",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(Integer, ForeignKey("fixtures.id"), index=True)
    bookmaker: Mapped[str] = mapped_column(String(80), index=True)
    market_type: Mapped[str] = mapped_column(String(120), index=True)
    selection_name: Mapped[str] = mapped_column(String(120))
    odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pulled_at: Mapped[datetime] = mapped_column(DateTime, index=True, server_default=func.now())

    fixture: Mapped["Fixture"] = relationship("Fixture", back_populates="market_snapshots")

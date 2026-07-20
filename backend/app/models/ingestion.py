from datetime import datetime, date
from typing import Optional
from sqlalchemy import Integer, String, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_date: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    fixtures_pulled: Mapped[int] = mapped_column(Integer, default=0)
    markets_pulled: Mapped[int] = mapped_column(Integer, default=0)
    signals_computed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

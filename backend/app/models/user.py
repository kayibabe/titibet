from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, String, DateTime, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # Tier: free / pro / elite
    tier: Mapped[str] = mapped_column(String(20), default="free", index=True)
    # Subscription: inactive / active / cancelled / past_due
    subscription_status: Mapped[str] = mapped_column(String(20), default="inactive")
    subscription_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Payment provider reference (Paystack customer code or Stripe customer id)
    payment_customer_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), default="Africa/Blantyre")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=func.now(), nullable=True)

    @property
    def is_pro(self) -> bool:
        return self.tier in ("pro", "elite") and self.subscription_status == "active"

    @property
    def is_elite(self) -> bool:
        return self.tier == "elite" and self.subscription_status == "active"

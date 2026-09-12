"""Stage 0 tracking guards and conservative, non-mutating legacy classification.

Current snapshots remain mutable. A successful guard is NOT complete prospective
lineage; immutable quotes and model artifacts are introduced in Stage 1.
"""
from datetime import datetime, timedelta, timezone
from math import isfinite

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Fixture, MarketSnapshot, Signal
from app.services.clv import _BET_TO_SELECTION, _MARKET_TYPE_SCOPE


def utc(value: datetime) -> datetime:
    # Existing SQLite timestamps are UTC without timezone information.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def classify_entry(created_at: datetime | None, kickoff_at: datetime | None) -> str:
    """Describe recorded timing, without asserting historical selection or execution."""
    if created_at is None or kickoff_at is None:
        return "timing_unknown"
    if utc(created_at) >= utc(kickoff_at):
        return "late_entry_unverified"
    return "pre_kickoff_incomplete_evidence"


async def tracking_rejection(
    db: AsyncSession, signal: Signal, fixture: Fixture, *, now: datetime | None = None,
) -> str | None:
    """Require an unstarted fixture and a matching, locally received real quote."""
    supplied_now = now
    now = utc(now or datetime.now(timezone.utc))
    if fixture.kickoff_at is None:
        return "MISSING_KICKOFF"
    if now >= utc(fixture.kickoff_at):
        return "POST_KICKOFF"
    if (fixture.status or "").strip().upper() != "NS":
        return "FIXTURE_NOT_SCHEDULED"
    if signal.computed_at is None or utc(signal.computed_at) > now:
        return "INVALID_COMPUTATION_TIME"
    odds = signal.bayesian_best_odd
    if odds is None or not isfinite(odds) or odds <= 1.0 or not signal.bayesian_bookmaker:
        return "MISSING_QUOTE"
    scope = _MARKET_TYPE_SCOPE.get(signal.market)
    if not scope:
        return "UNSUPPORTED_MARKET_SCOPE"
    latest = await db.scalar(
        select(MarketSnapshot)
        .where(
            MarketSnapshot.fixture_id == fixture.id,
            MarketSnapshot.bookmaker == signal.bayesian_bookmaker,
            MarketSnapshot.market_type.in_(scope),
            MarketSnapshot.selection_name == _BET_TO_SELECTION.get(signal.market, signal.market),
            MarketSnapshot.pulled_at <= now.replace(tzinfo=None),
        )
        .order_by(MarketSnapshot.pulled_at.desc(), MarketSnapshot.id.desc())
        .limit(1)
    )
    if latest is None or latest.odds is None or not isfinite(latest.odds) or abs(latest.odds - odds) > 1e-9:
        return "MISSING_MATCHING_QUOTE"
    if utc(latest.pulled_at) > utc(signal.computed_at):
        return "QUOTE_AFTER_PREDICTION"
    checked_at = utc(supplied_now or datetime.now(timezone.utc))
    if checked_at >= utc(fixture.kickoff_at):
        return "POST_KICKOFF"
    if utc(latest.pulled_at) < checked_at - timedelta(minutes=get_settings().tracking_quote_max_age_minutes):
        return "STALE_QUOTE"
    return None

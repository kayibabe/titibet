"""
clv.py — Closing Line Value computation.

CLV = (closing_odds / bet_odds - 1) × 100

closing_odds is taken as the best odds available in the 4-hour window before
kickoff (the canonical closing line). Falls back to the best pre-kickoff odds
within 24 hours if the narrow window has no data.

A positive CLV means you got better than the closing market price —
the canonical evidence of genuine model edge, independent of outcome variance.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    BTTS_MARKET_NAMES, GOALS_MARKET_NAMES,
    HOME_GOALS_MARKET_NAMES, AWAY_GOALS_MARKET_NAMES,
    WIN_TO_NIL_COMBINED_MARKET_NAMES,
    EXACT_GOALS_MARKET_NAMES,
)
from app.models.bet import TrackedBet
from app.models.fixture import Fixture
from app.models.odds import MarketSnapshot

log = logging.getLogger(__name__)

# Maps TrackedBet.market_type (standardized) → MarketSnapshot.selection_name.
# Over/Under markets match directly; special markets need remapping.
_BET_TO_SELECTION: dict[str, str] = {
    "BTTS Yes": "Yes",
    "BTTS No":  "No",
    # Win to Nil is delivered as one combined market ("Win To Nil") with
    # selections "Home"/"Away" — not per-side Yes/No markets (DB audit 2026-07-02).
    "Home Win to Nil": "Home",
    "Away Win to Nil": "Away",
    "Exactly 1 Goal":  "1",
    "Exactly 2 Goals": "2",
    "Exactly 3 Goals": "3",
    # Team totals — selection_name matches the Over/Under label directly
}

# Maps standardized market_type → the set of raw market_type names in MarketSnapshot.
# Scopes CLV queries so "Over 1.5" from "Goals Over/Under" is not confused with
# "Over 1.5" from "Total - Home" or "Away Team Total Goals (1st Half)".
_MARKET_TYPE_SCOPE: dict[str, frozenset] = {
    # Full-game totals
    "Over 0.5":  GOALS_MARKET_NAMES,
    "Over 1.5":  GOALS_MARKET_NAMES,
    "Over 2.5":  GOALS_MARKET_NAMES,
    "Over 3.5":  GOALS_MARKET_NAMES,
    "Under 1.5": GOALS_MARKET_NAMES,
    "Under 2.5": GOALS_MARKET_NAMES,
    "Under 3.5": GOALS_MARKET_NAMES,
    # BTTS
    "BTTS Yes":  BTTS_MARKET_NAMES,
    "BTTS No":   BTTS_MARKET_NAMES,
    # Team totals
    "Home Over 0.5": HOME_GOALS_MARKET_NAMES,
    "Home Over 1.5": HOME_GOALS_MARKET_NAMES,
    "Away Over 0.5": AWAY_GOALS_MARKET_NAMES,
    "Away Over 1.5": AWAY_GOALS_MARKET_NAMES,
    # Win to nil — combined market, disambiguated by selection ("Home"/"Away")
    "Home Win to Nil": WIN_TO_NIL_COMBINED_MARKET_NAMES,
    "Away Win to Nil": WIN_TO_NIL_COMBINED_MARKET_NAMES,
    # Exact goals
    "Exactly 1 Goal":  EXACT_GOALS_MARKET_NAMES,
    "Exactly 2 Goals": EXACT_GOALS_MARKET_NAMES,
    "Exactly 3 Goals": EXACT_GOALS_MARKET_NAMES,
}


async def compute_clv_for_bet(bet: TrackedBet, db: AsyncSession) -> tuple[float | None, float | None]:
    """
    Returns (closing_odds, clv_pct) for a single bet.
    Uses the best odds in market_snapshots within the 4-hour pre-kickoff window.
    Falls back to best pre-kickoff odds within 24 hours if the narrow window is empty.
    Returns (None, None) if no snapshot data exists.
    """
    if not bet.fixture_id or not bet.market_type or not bet.odds or bet.odds <= 0:
        return None, None

    selection_name = _BET_TO_SELECTION.get(bet.market_type, bet.market_type)
    market_scope = _MARKET_TYPE_SCOPE.get(bet.market_type)
    fixture_row = await db.get(Fixture, bet.fixture_id)
    closing = None

    def _base_conditions() -> list:
        conds = [
            MarketSnapshot.fixture_id == bet.fixture_id,
            MarketSnapshot.selection_name == selection_name,
            MarketSnapshot.odds.is_not(None),
        ]
        if market_scope:
            conds.append(MarketSnapshot.market_type.in_(list(market_scope)))
        return conds

    # Primary pass: snapshots within 4 h before kickoff (true closing line)
    if fixture_row and fixture_row.kickoff_at:
        window_start = fixture_row.kickoff_at - timedelta(hours=4)
        result = await db.execute(
            select(func.max(MarketSnapshot.odds))
            .where(
                *_base_conditions(),
                MarketSnapshot.pulled_at >= window_start,
                MarketSnapshot.pulled_at <= fixture_row.kickoff_at,
            )
        )
        closing = result.scalar_one_or_none()

    # Fallback: best odds at any point before kickoff
    if not closing or closing <= 0:
        fallback = _base_conditions()
        if fixture_row and fixture_row.kickoff_at:
            fallback.append(MarketSnapshot.pulled_at <= fixture_row.kickoff_at)
        result = await db.execute(
            select(func.max(MarketSnapshot.odds)).where(*fallback)
        )
        closing = result.scalar_one_or_none()

    if closing is None or closing <= 0:
        return None, None

    clv_pct = round((closing / bet.odds - 1.0) * 100, 2)
    return round(closing, 3), clv_pct


async def compute_clv_all(db: AsyncSession, force: bool = False, user_id: int | None = None) -> dict:
    """
    Compute CLV for all tracked bets that have a fixture_id.
    By default, skips bets that already have closing_odds set (unless force=True).
    Returns a summary dict.
    """
    query = select(TrackedBet).where(TrackedBet.fixture_id.is_not(None))
    if user_id is not None:
        query = query.where(TrackedBet.user_id == user_id)
    if not force:
        query = query.where(TrackedBet.closing_odds.is_(None))

    rows = await db.execute(query)
    bets = list(rows.scalars().all())

    updated = 0
    skipped = 0

    for bet in bets:
        closing, clv_pct = await compute_clv_for_bet(bet, db)
        if closing is not None:
            bet.closing_odds = closing
            bet.clv_pct = clv_pct
            updated += 1
        else:
            skipped += 1

    if updated:
        await db.commit()

    log.info("CLV computation done: %d updated, %d skipped (no snapshot)", updated, skipped)
    return {"updated": updated, "skipped_no_data": skipped}

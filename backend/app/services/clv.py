"""
clv.py — Closing Line Value computation.

CLV = (closing_odds / bet_odds - 1) × 100

closing_odds is taken from two sources, in order of preference:

1. capture_closing_odds_for_pending() — an hourly scheduler job that pulls
   fresh odds from API-Football for fixtures with pending bets kicking off
   within the next ~80 minutes and writes the true closing line onto the bet
   row itself. The bet row is the durable store: market_snapshots is a
   current-state cache that ingestion deletes and rewrites every sync, so it
   cannot serve as a price history.
2. compute_clv_for_bet() — snapshot-based fallback, only trusted when a
   snapshot exists inside the 4-hour pre-kickoff window. There is deliberately
   NO wider fallback: comparing the bet price to the very snapshot it was
   placed from yields a meaningless CLV of 0, which is worse than no data
   (audit 2026-07-26: 6 of 9 recorded CLVs were such tautologies).

A positive CLV means you got better than the closing market price —
the canonical evidence of genuine model edge, independent of outcome variance.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

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
    # Team totals: MarketSnapshot stores market_type="Total - Home"/"Total - Away"
    # with selection_name="Over 0.5", "Under 0.5", etc. — strip the side prefix.
    "Home Over 0.5":  "Over 0.5",
    "Home Over 1.5":  "Over 1.5",
    "Home Under 0.5": "Under 0.5",
    "Home Under 1.5": "Under 1.5",
    "Away Over 0.5":  "Over 0.5",
    "Away Over 1.5":  "Over 1.5",
    "Away Under 0.5": "Under 0.5",
    "Away Under 1.5": "Under 1.5",
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
    Returns (None, None) when no snapshot lands in that window — a missing CLV
    is honest; a CLV computed against the bet's own opening price is not.
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


def _best_odds_from_api_rows(rows: list[dict], market_type: str) -> float | None:
    """Best (max) odds for a bet's market from parsed /odds API rows."""
    selection_name = _BET_TO_SELECTION.get(market_type, market_type)
    market_scope = _MARKET_TYPE_SCOPE.get(market_type)
    best = None
    for row in rows:
        if row.get("selection_name") != selection_name:
            continue
        if market_scope and row.get("market_type") not in market_scope:
            continue
        odds = row.get("odds")
        if odds and odds > 1.0 and (best is None or odds > best):
            best = odds
    return best


async def capture_closing_odds_for_pending(
    db: AsyncSession, lookahead_minutes: int = 80,
) -> dict:
    """
    Capture true closing lines for pending bets kicking off soon.

    For every pending bet without closing_odds whose fixture kicks off within
    the next `lookahead_minutes`, pull fresh odds from API-Football (one call
    per fixture, force-bypassing the file cache) and write closing_odds +
    clv_pct directly onto the bet row. Run hourly with an 80-minute lookahead
    so every kickoff is captured 20-80 minutes out.
    """
    from app.services import api_client

    # kickoff_at is stored naive-UTC; compare with a naive-UTC now.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window_end = now + timedelta(minutes=lookahead_minutes)

    rows = await db.execute(
        select(TrackedBet, Fixture)
        .join(Fixture, TrackedBet.fixture_id == Fixture.id)
        .where(
            TrackedBet.result_status == "Pending",
            TrackedBet.closing_odds.is_(None),
            TrackedBet.odds.is_not(None),
            Fixture.kickoff_at.is_not(None),
            Fixture.kickoff_at > now,
            Fixture.kickoff_at <= window_end,
        )
    )
    pairs = list(rows.all())
    if not pairs:
        return {"captured": 0, "no_line": 0, "fixtures": 0}

    # One API call per distinct fixture, shared across its bets.
    odds_by_fixture: dict[int, list[dict]] = {}
    for _, fixture in pairs:
        if fixture.id not in odds_by_fixture:
            odds_by_fixture[fixture.id] = await api_client.fetch_fixture_odds(
                fixture.external_fixture_id, force=True,
            )

    captured = 0
    no_line = 0
    for bet, fixture in pairs:
        closing = _best_odds_from_api_rows(odds_by_fixture[fixture.id], bet.market_type)
        if closing is None:
            no_line += 1
            continue
        bet.closing_odds = round(closing, 3)
        bet.clv_pct = round((closing / bet.odds - 1.0) * 100, 2)
        captured += 1

    if captured:
        await db.commit()
    log.info(
        "Closing-odds capture: %d bet(s) captured, %d without a live line, %d fixture pull(s)",
        captured, no_line, len(odds_by_fixture),
    )
    return {"captured": captured, "no_line": no_line, "fixtures": len(odds_by_fixture)}

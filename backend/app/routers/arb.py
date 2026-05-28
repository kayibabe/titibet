"""
arb.py — Arbitrage opportunity detector.

Scans today's market_snapshots and surfaces two-way markets where the combined
implied probability across two bookmakers is < 100%, guaranteeing a risk-free
profit regardless of outcome when both sides are backed simultaneously.

Only pre-match fixtures (status NS/TBD) are considered — live arb requires
real-time feeds this system doesn't have.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_optional
from app.core.database import get_db
from app.models import Fixture
from app.models.odds import MarketSnapshot
from app.models.user import User

router = APIRouter(prefix="/api/arb", tags=["arb"])

# Two-way selection pairs to check within the same market_type.
# ONLY include pairs that are genuinely mutually exclusive and exhaustive —
# i.e. exactly one of the two outcomes MUST happen.
#
# Over/Under totals: if not Over then Under (and vice versa) — true 2-way.
# BTTS Yes/No: either both teams score or they don't — true 2-way.
#
# Home/Away is intentionally excluded: in 1X2 markets a Draw is a third outcome,
# and in special markets ("To Win From Behind", "To Miss A Penalty", etc.) both
# or neither team can satisfy the condition — making them non-exhaustive and
# therefore unsuitable for arb detection.
_OPPOSITE_PAIRS: list[tuple[str, str]] = [
    # ── Goals / totals over/under ────────────────────────────────────────────
    # Half-ball (.5) lines have no push: exactly one side must win.
    # These pairs match ANY market that uses this selection format:
    # goals totals, home/away team totals, cards totals, corners totals.
    ("Over 0.5",  "Under 0.5"),
    ("Over 1.5",  "Under 1.5"),
    ("Over 2.5",  "Under 2.5"),
    ("Over 3.5",  "Under 3.5"),
    ("Over 4.5",  "Under 4.5"),
    ("Over 5.5",  "Under 5.5"),

    # ── Odd / Even ───────────────────────────────────────────────────────────
    # Any integer count (goals, corners, cards, fouls) must be odd or even.
    # 0 is even. No third outcome is possible — one pair covers every
    # Odd/Even market in the DB (full-match, first half, second half,
    # home goals, away goals, corners, yellow cards, fouls).
    ("Even",      "Odd"),

    # ── Both Teams To Score ──────────────────────────────────────────────────
    # Either both teams score or they don't — exhaustive by definition.
    # "Yes"/"No" also covers: Clean Sheet (Home/Away), Both Teams Score
    # First Half / Second Half, Game Decided in Extra Time / After Penalties,
    # Own Goal.
    ("Yes",       "No"),
    ("GG",        "NG"),      # BTTS alternate labels used by some bookmakers
]

# Minimum arb margin to surface (filters noise from rounding / stale quotes)
_MIN_ARB_PCT = 0.3


@router.get("/opportunities")
async def arb_opportunities(
    date_str: Optional[str] = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Return all arbitrage opportunities for a given date (default: today).

    Each opportunity includes:
      - arb_pct   — guaranteed profit % on total stake (e.g. 2.5 = 2.5%)
      - stake_a/b — how many units to stake on each side per 100 total units
      - profit    — guaranteed profit per 100 units staked
    """
    target_date = date.fromisoformat(date_str) if date_str else date.today()

    # Load fixtures for the date
    fix_rows = (await db.execute(
        select(Fixture).where(Fixture.event_date == target_date)
    )).scalars().all()

    if not fix_rows:
        return []

    fixture_map = {f.id: f for f in fix_rows}
    fixture_ids = list(fixture_map.keys())

    # Load all market snapshots for these fixtures
    snap_rows = (await db.execute(
        select(MarketSnapshot).where(MarketSnapshot.fixture_id.in_(fixture_ids))
    )).scalars().all()

    # Build: fixture_id → market_type → selection_name → {bookmaker: best_odds}
    data: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    for snap in snap_rows:
        if not snap.odds or snap.odds <= 1.0:
            continue
        existing = data[snap.fixture_id][snap.market_type][snap.selection_name].get(snap.bookmaker, 0.0)
        if snap.odds > existing:
            data[snap.fixture_id][snap.market_type][snap.selection_name][snap.bookmaker] = snap.odds

    opportunities: list[dict] = []

    for fixture_id, markets in data.items():
        fixture = fixture_map[fixture_id]
        # Only pre-match — skip live / finished games
        if fixture.status not in (None, "NS", "TBD", ""):
            continue

        for market_type, selections in markets.items():
            for side_a, side_b in _OPPOSITE_PAIRS:
                if side_a not in selections or side_b not in selections:
                    continue

                # Best odds for each side across all bookmakers
                bk_a = max(selections[side_a], key=selections[side_a].get)
                bk_b = max(selections[side_b], key=selections[side_b].get)
                best_a = selections[side_a][bk_a]
                best_b = selections[side_b][bk_b]

                implied_a = 1.0 / best_a
                implied_b = 1.0 / best_b
                total_implied = implied_a + implied_b

                if total_implied >= 1.0:
                    continue

                arb_pct = round((1.0 - total_implied) * 100, 2)
                if arb_pct < _MIN_ARB_PCT:
                    continue

                # Optimal stakes per 100 units total
                stake_a = round(implied_a / total_implied * 100, 2)
                stake_b = round(implied_b / total_implied * 100, 2)
                profit  = round(arb_pct, 2)

                opportunities.append({
                    "fixture_id":  fixture_id,
                    "home_team":   fixture.home_team,
                    "away_team":   fixture.away_team,
                    "league":      fixture.league,
                    "country":     fixture.country,
                    "kickoff_at":  fixture.kickoff_at.isoformat() if fixture.kickoff_at else None,
                    "market_type": market_type,
                    "side_a":      side_a,
                    "side_b":      side_b,
                    "odds_a":      round(best_a, 3),
                    "odds_b":      round(best_b, 3),
                    "bookie_a":    bk_a,
                    "bookie_b":    bk_b,
                    "arb_pct":     arb_pct,
                    "stake_a":     stake_a,
                    "stake_b":     stake_b,
                    "profit_per_100": profit,
                })

    # Best arb first
    opportunities.sort(key=lambda x: -x["arb_pct"])
    return opportunities

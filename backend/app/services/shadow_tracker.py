"""
shadow_tracker.py — Silent observation log for signals blocked by conservative gates.

Records HO0.5 @ 2.50-2.99 Both+High signals (blocked by DUAL_HIGH_ODDS_CEILING=1.95)
without placing real stakes.  After 3-4 weeks of forward data we can decide whether
to relax the ceiling for that sub-band.

Observation type: 'ho05_high_odds'
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Signal, Fixture
from app.models.signal_observation import SignalObservation
from app.core.config import (
    COPA_HO05_SUPPRESSED_LEAGUES,
    HO05_DATA_POOR_COUNTRIES,
    OVER_GOALS_SUPPRESSED_LEAGUES,
    DISABLED_LEAGUES,
    WOMEN_OVER_SUPPRESSED_MARKETS,
    is_womens_fixture,
)
from app.services.settlement import SCORE_SETTLEABLE_MARKETS

logger = logging.getLogger("titibet.shadow_tracker")

OBS_TYPE = "ho05_high_odds"
ODDS_LO   = 2.50
ODDS_HI   = 3.00


def _grade(q: float | None) -> str | None:
    if q is None:
        return None
    if q >= 0.60: return "A"
    if q >= 0.45: return "B"
    if q >= 0.30: return "C"
    return "D"


async def shadow_track_date(db: AsyncSession, run_date: date) -> int:
    """
    Record HO0.5 @ 2.50-2.99 Both+High signals for run_date that pass all
    standard gates except the DUAL_HIGH_ODDS_CEILING.  Skips fixtures already
    observed for this date.  Returns count of new rows inserted.
    """
    rows = list(
        (await db.execute(
            select(Signal, Fixture)
            .join(Fixture, Signal.fixture_id == Fixture.id)
            .where(Fixture.event_date == run_date)
            .where(Signal.is_candidate == False)  # noqa: E712
            .where(Signal.dual_agreement == "Both")
            .where(Signal.dual_confidence == "High")
            .where(Signal.market == "Home Over 0.5")
        )).all()
    )

    if not rows:
        return 0

    # Existing observations for this date — skip already-recorded fixtures.
    existing = set(
        (await db.execute(
            select(SignalObservation.fixture_id)
            .where(SignalObservation.event_date == run_date)
            .where(SignalObservation.observation_type == OBS_TYPE)
        )).scalars().all()
    )

    inserted = 0
    for signal, fixture in rows:
        odds = signal.bayesian_best_odd or 0.0
        if not (ODDS_LO <= odds < ODDS_HI):
            continue

        if signal.fixture_id in existing:
            continue

        # Skip World fixtures — friendlies / tournaments where home-scoring edge
        # doesn't hold (33% WR observed; the edge is in domestic leagues only).
        if (fixture.country or "").lower() == "world":
            continue

        # Mirror all other standard suppression gates so this is a clean signal.
        league_lower = (fixture.league or "").lower().strip()
        if league_lower in DISABLED_LEAGUES or "friendlies" in league_lower:
            continue
        if any(k in league_lower for k in OVER_GOALS_SUPPRESSED_LEAGUES):
            continue
        if COPA_HO05_SUPPRESSED_LEAGUES and any(kw in league_lower for kw in COPA_HO05_SUPPRESSED_LEAGUES):
            continue
        if (fixture.league_tier or 3) >= 3 and (fixture.country or "").lower() in HO05_DATA_POOR_COUNTRIES:
            continue
        if is_womens_fixture(fixture.league, fixture.home_team, fixture.away_team):
            continue

        db.add(SignalObservation(
            observation_type=OBS_TYPE,
            fixture_id=signal.fixture_id,
            event_date=fixture.event_date,
            match_name=f"{fixture.home_team} vs {fixture.away_team}",
            league=fixture.league,
            country=fixture.country,
            league_tier=fixture.league_tier,
            market_type="Home Over 0.5",
            dual_agreement=signal.dual_agreement,
            dual_confidence=signal.dual_confidence,
            signal_grade=_grade(signal.dual_quality_score),
            odds=odds,
            bookmaker=signal.bayesian_bookmaker,
            result_status="Pending",
        ))
        existing.add(signal.fixture_id)
        inserted += 1

    if inserted:
        await db.commit()
        logger.info("Shadow tracker: %d HO0.5 high-odds observation(s) for %s", inserted, run_date)

    return inserted


async def settle_shadow_observations(db: AsyncSession) -> int:
    """
    Settle all Pending signal_observations whose fixture has a final score.
    Returns count of newly settled rows.
    """
    _settle_condition = SCORE_SETTLEABLE_MARKETS["Home Over 0.5"]

    pending = list(
        (await db.execute(
            select(SignalObservation, Fixture)
            .join(Fixture, SignalObservation.fixture_id == Fixture.id)
            .where(SignalObservation.result_status == "Pending")
            .where(SignalObservation.observation_type == OBS_TYPE)
        )).all()
    )

    settled = 0
    for obs, fixture in pending:
        status = (fixture.status or "").strip().upper()
        if status not in {"FT", "AET", "PEN"}:
            continue
        if fixture.home_score is None or fixture.away_score is None:
            continue

        won = _settle_condition(fixture.home_score, fixture.away_score)
        obs.result_status = "Won" if won else "Lost"
        obs.profit_loss   = round(obs.odds - 1.0, 4) if won else -1.0
        obs.settled_at    = datetime.now(timezone.utc)
        settled += 1

    if settled:
        await db.commit()
        logger.info("Shadow tracker: settled %d observation(s)", settled)

    return settled

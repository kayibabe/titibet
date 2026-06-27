"""
auto_tracker.py — Backend auto-tracking of system signals.

Creates TrackedBet rows (user_id=None) for every qualifying signal on a date.
Idempotent: existing rows are skipped.  Called from sync_and_compute() so
auto-tracking runs every sync cycle regardless of whether anyone visits the
Signals page.

Qualifying signals:
  - High confidence + Both agreement  (dual signal)
  - Home Over 0.5 + Poisson Only + rule_strong  (Poisson signal)
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Signal, Fixture, TrackedBet
from app.models.user import User as _User  # noqa: F401 — registers users table in SA metadata
from app.core.config import (
    DUAL_HIGH_ODDS_CEILING, WOMEN_LEAGUE_KEYWORDS,
    WOMEN_OVER_SUPPRESSED_MARKETS, HO05_DATA_POOR_COUNTRIES,
)

logger = logging.getLogger("titibet.auto_tracker")

FLAT_STAKE = 50_000.0


def _grade(q: float | None) -> str | None:
    if q is None:
        return None
    if q >= 0.08:  return "A"
    if q >= 0.055: return "B"
    if q >= 0.035: return "C"
    return "D"


async def auto_track_date(db: AsyncSession, run_date: date) -> int:
    """
    Create system TrackedBet rows for all qualifying signals on run_date.
    Returns count of newly inserted bets.
    """
    from sqlalchemy import or_, and_

    # Load qualifying signals for this date
    rows = list(
        (await db.execute(
            select(Signal, Fixture)
            .join(Fixture, Signal.fixture_id == Fixture.id)
            .where(Fixture.event_date == run_date)
            .where(Signal.is_candidate == False)  # noqa: E712
            .where(
                or_(
                    and_(
                        Signal.dual_confidence == "High",
                        Signal.dual_agreement == "Both",
                    ),
                    and_(
                        Signal.market == "Home Over 0.5",
                        Signal.dual_agreement == "Poisson Only",
                        Signal.poisson_rule_strong == True,  # noqa: E712
                    ),
                )
            )
        )).all()
    )

    if not rows:
        return 0

    # Load existing bets for this date to avoid duplicates.
    # Check on (fixture_id, market_type) only — bookmaker varies between the
    # old per-user strategy-tracker rows and the new system_auto rows, so
    # using bookmaker in the key would miss those collisions.
    existing_rows = list(
        (await db.execute(
            select(TrackedBet.fixture_id, TrackedBet.market_type)
            .where(TrackedBet.event_date == run_date)
        )).all()
    )
    existing_keys: set[tuple] = {
        (r.fixture_id, r.market_type) for r in existing_rows
    }

    inserted = 0
    for signal, fixture in rows:
        bookmaker = signal.bayesian_bookmaker or "Best Available"
        key = (signal.fixture_id, signal.market)
        if key in existing_keys:
            continue

        odds = signal.bayesian_best_odd
        if not odds or odds <= 1.01:
            prob = signal.poisson_prob or signal.bayesian_prob
            if prob and 0.0 < prob < 1.0:
                odds = round(1.0 / prob, 3)
            else:
                continue

        # Skip Both+High picks whose odds exceed the serving-time ceiling —
        # consistent with what the router shows subscribers.
        ceiling = DUAL_HIGH_ODDS_CEILING.get(signal.market)
        if (
            ceiling is not None
            and signal.dual_confidence == "High"
            and signal.dual_agreement == "Both"
            and odds >= ceiling
        ):
            continue

        # Skip women's league over-goals picks — models calibrated on men's
        # football systematically overestimate scoring in women's fixtures.
        if (
            signal.market in WOMEN_OVER_SUPPRESSED_MARKETS
            and any(kw in (fixture.league or "").lower() for kw in WOMEN_LEAGUE_KEYWORDS)
        ):
            continue

        # Skip Both+High Home Over 0.5 from data-poor countries at Tier 3.
        # Both engines can agree with high confidence on insufficient historical
        # data — the agreement reflects noise, not genuine edge.
        if (
            signal.market == "Home Over 0.5"
            and signal.dual_confidence == "High"
            and signal.dual_agreement == "Both"
            and (fixture.league_tier or 3) >= 3
            and (fixture.country or "").lower() in HO05_DATA_POOR_COUNTRIES
        ):
            continue

        is_dual = signal.dual_confidence == "High" and signal.dual_agreement == "Both"
        match_name = f"{fixture.home_team} vs {fixture.away_team}"

        bet = TrackedBet(
            user_id=None,
            fixture_id=signal.fixture_id,
            bookmaker=bookmaker,
            event_date=fixture.event_date,
            match_name=match_name,
            league=fixture.league,
            market_type=signal.market,
            selection_name=signal.market,
            odds=odds,
            stake=FLAT_STAKE,
            recommended_stake_pct=signal.dual_recommended_stake_pct,
            source_rule_key="system_dual" if is_dual else "system_auto",
            source_rule_label="Dual Signal (High+Both)" if is_dual else "System Auto-Pick",
            signal_grade=_grade(signal.dual_quality_score),
            dual_confidence=signal.dual_confidence,
            dual_agreement=signal.dual_agreement,
            result_status="Pending",
        )
        db.add(bet)
        existing_keys.add(key)
        inserted += 1

    if inserted:
        await db.commit()
        logger.info("Auto-tracker: inserted %d system bets for %s", inserted, run_date)

    return inserted

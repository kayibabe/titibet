"""
cleanup_suppressed_bets.py

Retroactively deletes system auto-tracked bets (user_id IS NULL) that violate
current suppression rules. Safe to re-run — idempotent.

Run locally:  python cleanup_suppressed_bets.py
Run on Fly:   fly ssh console -C "python /app/cleanup_suppressed_bets.py"
"""
import asyncio
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

async def main():
    from sqlalchemy import select, delete
    from app.core.database import AsyncSessionLocal
    from app.models.bet import TrackedBet
    from app.models.fixture import Fixture
    from app.core.config import (
        DISABLED_LEAGUES, DISABLED_MARKETS, OVER_GOALS_SUPPRESSED_LEAGUES,
        AWAY_GOALS_SUPPRESSED_LEAGUES, WOMEN_OVER_SUPPRESSED_MARKETS,
        HO05_DATA_POOR_COUNTRIES, DUAL_HIGH_ODDS_CEILING,
        is_womens_fixture,
    )

    OVER_MKT = {"Home Over 0.5", "Away Over 0.5", "Over 1.5", "Over 2.5", "Home Over 1.5", "Away Over 1.5"}
    AWAY_MKT = {"Away Over 0.5", "Away Over 1.5"}

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(TrackedBet, Fixture)
            .outerjoin(Fixture, TrackedBet.fixture_id == Fixture.id)
            .where(TrackedBet.user_id == None)  # noqa: E711
        )).all()

        ids_to_delete = []
        status_counts = {}
        reason_counts = {}

        for bet, fix in rows:
            league_lower = (bet.league or "").lower().strip()
            market = bet.market_type or ""
            country = (fix.country or "").lower() if fix else ""
            tier = (fix.league_tier or 3) if fix else 3
            odds = bet.odds or 0.0
            confidence = bet.dual_confidence or ""
            agreement = bet.dual_agreement or ""
            home_team = (fix.home_team if fix else None)
            away_team = (fix.away_team if fix else None)

            reason = None

            if league_lower in DISABLED_LEAGUES:
                reason = f"DISABLED_LEAGUES ({bet.league})"
            elif "friendlies" in league_lower:
                reason = f"friendlies ({bet.league})"
            elif market in DISABLED_MARKETS:
                reason = f"DISABLED_MARKETS ({market})"
            elif market in OVER_MKT and any(k in league_lower for k in OVER_GOALS_SUPPRESSED_LEAGUES):
                matched = next(k for k in OVER_GOALS_SUPPRESSED_LEAGUES if k in league_lower)
                reason = f"OVER_GOALS_SUPPRESSED ({matched})"
            elif market in AWAY_MKT and any(k in league_lower for k in AWAY_GOALS_SUPPRESSED_LEAGUES):
                reason = f"AWAY_GOALS_SUPPRESSED ({bet.league})"
            elif market in WOMEN_OVER_SUPPRESSED_MARKETS and is_womens_fixture(bet.league, home_team, away_team):
                reason = f"WOMEN_OVER_SUPPRESSED ({bet.league})"
            elif (
                market == "Home Over 0.5"
                and confidence == "High"
                and agreement == "Both"
                and tier >= 3
                and country in HO05_DATA_POOR_COUNTRIES
            ):
                reason = f"HO05_DATA_POOR ({country})"
            elif (
                confidence == "High"
                and agreement == "Both"
                and market in DUAL_HIGH_ODDS_CEILING
                and odds >= DUAL_HIGH_ODDS_CEILING[market]
            ):
                reason = f"DUAL_HIGH_ODDS_CEILING ({market} @{odds})"

            if reason:
                ids_to_delete.append(bet.id)
                status_counts[bet.result_status] = status_counts.get(bet.result_status, 0) + 1
                reason_counts[reason] = reason_counts.get(reason, 0) + 1

        if not ids_to_delete:
            print("Nothing to delete — database is already clean.")
            return

        result = await db.execute(
            delete(TrackedBet).where(TrackedBet.id.in_(ids_to_delete))
        )
        await db.commit()

        print(f"Deleted {result.rowcount} system auto-pick rows.")
        print("\nBy result_status:")
        for s, n in sorted(status_counts.items(), key=lambda x: -x[1]):
            print(f"  {n:3d}  {s}")
        print("\nBy suppression rule:")
        for r, n in sorted(reason_counts.items(), key=lambda x: -x[1]):
            print(f"  {n:3d}  {r}")

        print("\nDone.")


asyncio.run(main())

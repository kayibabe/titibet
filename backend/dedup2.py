"""
Delete user_id=3 system_auto/system_dual bets that are duplicated by
user_id=None system picks for the same fixture+market.
"""
import asyncio
from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.models import TrackedBet
from app.services.settlement import settle_bets_for_date
from sqlalchemy import select, delete, and_

async def run():
    async with AsyncSessionLocal() as db:
        # Find user_id=3 system bets where a user_id=None system bet exists
        # for the same fixture+market
        user_rows = (await db.execute(
            select(TrackedBet)
            .where(
                TrackedBet.user_id == 3,
                TrackedBet.source_rule_key.in_(["system_auto", "system_dual", "home_o05"]),
            )
        )).scalars().all()

        to_delete = []
        for bet in user_rows:
            # Check if a system (user_id=None) row exists for same fixture+market
            existing_system = await db.scalar(
                select(TrackedBet).where(
                    TrackedBet.fixture_id == bet.fixture_id,
                    TrackedBet.market_type == bet.market_type,
                    TrackedBet.user_id.is_(None),
                )
            )
            if existing_system:
                to_delete.append(bet.id)
                print(f"  Delete ID {bet.id}: {bet.match_name} odds={bet.odds} (dup of ID {existing_system.id} odds={existing_system.odds})")

        if to_delete:
            await db.execute(
                delete(TrackedBet).where(TrackedBet.id.in_(to_delete))
            )
            await db.commit()
            print(f"\nDeleted {len(to_delete)} duplicate rows")
        else:
            print("No duplicates found")

        # Settle pending system bets
        n = await settle_bets_for_date(db, None)
        print(f"Settlement: {n} bets settled")
        await db.commit()

asyncio.run(run())

"""
Sync fixture statuses + scores for all dates that have pending bets,
then run settlement across all dates.
"""
import asyncio, os, sys
from pathlib import Path
from datetime import date, timedelta

BACKEND = Path(r'D:\WebApps\titibet\backend')
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select, distinct
from app.core.database import AsyncSessionLocal
from app.models import TrackedBet, Fixture
from app.services import ingestion
from app.services.settlement import settle_bets_for_date

async def main():
    async with AsyncSessionLocal() as db:
        # Find all dates that have pending bets
        result = await db.execute(
            select(distinct(Fixture.event_date))
            .join(TrackedBet, TrackedBet.fixture_id == Fixture.id)
            .where(TrackedBet.result_status == 'Pending')
            .where(Fixture.event_date.isnot(None))
            .order_by(Fixture.event_date)
        )
        pending_dates = [r[0] for r in result.all()]
        print(f"Dates with pending bets: {pending_dates}")

        # Sync each date to refresh fixture statuses and scores
        for d in pending_dates:
            if isinstance(d, str):
                from datetime import date as date_cls
                d = date_cls.fromisoformat(d)
            print(f"  Syncing {d}...", end=" ")
            try:
                run = await ingestion.sync_date(db, d)
                print(f"{run.status} — {run.fixtures_pulled} fixtures")
            except Exception as e:
                print(f"ERROR: {e}")

        # Now settle all pending bets
        print("\nSettling all pending bets...")
        result = await settle_bets_for_date(db, None)
        print(f"Result: {result}")
        print(f"\nDone. {result.get('settled', 0)} bet(s) settled.")
        if result.get('skip_not_final', 0):
            print(f"  {result['skip_not_final']} skipped — fixtures not yet final (still in play or tonight)")
        if result.get('skip_no_score', 0):
            print(f"  {result['skip_no_score']} skipped — no score data yet")

asyncio.run(main())

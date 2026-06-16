"""
backfill_signals.py
-------------------
Re-runs compute_signals_for_date for every historical date that has both
completed fixture scores AND stored market_snapshots.

Run from D:\WebApps\titibet\backend\ :
    python backfill_signals.py

The signal engine now includes the flip signals (u35_flip, hu15_flip,
au15_flip, hwtn_flip, awtn_flip). Re-running over historical data
retroactively generates those signals so we can evaluate performance.

After this completes, run backfill_performance.py to see the report.
"""
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

# Must run from backend/ so the relative DB path resolves correctly
BACKEND = Path(r'D:\WebApps\titibet\backend')
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models import Fixture
from app.services.signal_engine import compute_signals_for_date


def p(s):
    sys.stdout.buffer.write((str(s) + '\n').encode('utf-8'))
    sys.stdout.buffer.flush()


async def main():
    async with AsyncSessionLocal() as db:
        # All dates that have completed fixtures AND market_snapshots
        result = await db.execute(text("""
            SELECT DISTINCT f.event_date
            FROM fixtures f
            JOIN market_snapshots ms ON ms.fixture_id = f.id
            WHERE f.home_score IS NOT NULL
            ORDER BY f.event_date
        """))
        dates = [row[0] for row in result.all()]

        p(f"Dates to backfill: {len(dates)}")
        p("=" * 50)

        total_signals = 0
        for d in dates:
            if isinstance(d, str):
                run_date = date.fromisoformat(d)
            else:
                run_date = d
            try:
                count = await compute_signals_for_date(db, run_date)
                total_signals += count
                p(f"  {run_date}  →  {count} signals written")
            except Exception as e:
                p(f"  {run_date}  ERROR: {e}")

        p("=" * 50)
        p(f"Done. Total signals written: {total_signals}")


if __name__ == "__main__":
    asyncio.run(main())

"""
recompute_7day_candidates.py

Recomputes signals for the past 7 days (today inclusive) using the current
signal_engine logic, which includes:
  - is_candidate collection for Poisson-strong Over 1.5 / Over 2.5 signals
  - Seasonal suppression bypass for candidates (lines 882-884 of signal_engine.py)

Run from the backend/ directory:
    python recompute_7day_candidates.py

No API calls are made — signals are recomputed from MarketSnapshot rows already
in the DB. Existing signal rows for each date are deleted and replaced.
"""
import asyncio
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")

from app.core.database import AsyncSessionLocal, init_db
from app.services.signal_engine import compute_signals_for_date


async def main() -> None:
    await init_db()

    today = date.today()
    dates = [today - timedelta(days=i) for i in range(7)]

    async with AsyncSessionLocal() as db:
        for d in dates:
            count = await compute_signals_for_date(db, d)
            print(f"{d}  ->  {count} signal(s) written")

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())

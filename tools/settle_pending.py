"""
settle_pending.py — Force-refresh stale fixtures and settle all Pending bets.

Run from the backend/ directory with the virtual environment active:
  cd D:\WebApps\titibet\backend
  python ..\tools\settle_pending.py

The script calls API-Football to get the actual match results for every
fixture linked to a Pending bet that still shows NS (Not Started) in the
DB, then runs the standard settlement logic.

Free API plan uses one call per unique event_date, so 2 dates with Pending
bets = 2 calls.
"""

import asyncio
import sys
import os

# Allow imports from backend/app/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.chdir(os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.database import AsyncSessionLocal, init_db
from app.services.settlement import refresh_stale_fixtures_and_settle


async def main() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        print("Running refresh_stale_fixtures_and_settle...")
        result = await refresh_stale_fixtures_and_settle(db)
        print("\n=== Settlement result ===")
        for k, v in result.items():
            print(f"  {k}: {v}")
        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())

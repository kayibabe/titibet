"""Run settlement for all pending bets across all dates."""
import asyncio, os, sys
from pathlib import Path

BACKEND = Path(r'D:\WebApps\titibet\backend')
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

from app.core.database import AsyncSessionLocal
from app.services.settlement import settle_bets_for_date

async def main():
    async with AsyncSessionLocal() as db:
        print("Settling all pending bets...")
        n = await settle_bets_for_date(db, None)  # None = all dates
        print(f"Done. {n} bet(s) settled.")

asyncio.run(main())

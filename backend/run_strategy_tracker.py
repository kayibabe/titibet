"""Run strategy_tracker manually for today and past dates."""
import asyncio, os, sys
from pathlib import Path
from datetime import date

BACKEND = Path(r'D:\WebApps\titibet\backend')
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

from app.core.database import AsyncSessionLocal
from app.services.strategy_tracker import auto_track_home_over_strategy

async def main():
    async with AsyncSessionLocal() as db:
        for d in [date(2026,5,31), date(2026,6,1), date(2026,6,2)]:
            result = await auto_track_home_over_strategy(db, d)
            print(f'{d}: {result}')

asyncio.run(main())

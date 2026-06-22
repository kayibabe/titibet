import asyncio, sys, os
sys.path.insert(0, '/app')
os.chdir('/app')
from dotenv import load_dotenv
load_dotenv('/app/.env')
from datetime import date
from app.services.ingestion import sync_date
from app.services.signal_engine import compute_signals_for_date
from app.core.database import init_db, engine, AsyncSessionLocal
from app.core.migrations import run_migrations

async def main():
    await init_db()
    await run_migrations(engine)
    today = date.today()
    async with AsyncSessionLocal() as db:
        print(f"Syncing {today}...")
        await sync_date(db, today)
        print("Computing signals...")
        n = await compute_signals_for_date(db, today)
        print(f"Done — {n} signals computed.")

asyncio.run(main())

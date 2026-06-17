import asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from app.core.database import AsyncSessionLocal
from app.services.signal_engine import compute_signals_for_date
from datetime import date

async def main():
    today = date.today()
    print(f"Computing signals for {today} ...")
    async with AsyncSessionLocal() as db:
        result = await compute_signals_for_date(db, today)
        print(f"Result: {result}")

        # Check WC signals now
        from sqlalchemy import text
        r = await db.execute(text("""
            SELECT f.home_team, f.away_team, f.status,
                   s.market, s.dual_confidence, s.dual_agreement, s.dual_quality_score
            FROM fixtures f
            JOIN signals s ON s.fixture_id = f.id
            WHERE lower(f.league) LIKE '%world cup%'
              AND f.event_date = :today
        """), {"today": today.isoformat()})
        rows = r.all()
        print(f"\nWorld Cup signals for {today.isoformat()}: {len(rows)}")
        for row in rows:
            print(f"  [{row[2]}] {row[0]} vs {row[1]} | {row[3]} | {row[4]} | {row[5]} | qs={row[6]:.3f}")

asyncio.run(main())

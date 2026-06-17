import asyncio, sys
sys.path.insert(0, ".")

async def main():
    from datetime import date, timedelta
    from app.core.database import AsyncSessionLocal
    from app.services import signal_engine

    test_dates = [date.today() - timedelta(days=d) for d in range(7)]

    async with AsyncSessionLocal() as db:
        for d in test_dates:
            n = await signal_engine.compute_signals_for_date(db, d)
            print(f"{d}: computed {n} signals")

    import aiosqlite
    async with aiosqlite.connect("titibet.db") as db:
        db.row_factory = aiosqlite.Row
        c = await db.execute("""
            SELECT s.market, s.dual_confidence, s.dual_agreement, s.is_candidate,
                   COUNT(*) as n
            FROM signals s JOIN fixtures f ON s.fixture_id=f.id
            WHERE f.event_date >= date('now','-7 days')
            GROUP BY s.market, s.dual_confidence, s.dual_agreement, s.is_candidate
            ORDER BY n DESC
        """)
        print("\nSignal distribution after key fix (7d):")
        for r in await c.fetchall(): print(f"  {dict(r)}")

asyncio.run(main())

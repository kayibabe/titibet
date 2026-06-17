import asyncio, sys
sys.path.insert(0, ".")

async def test():
    from datetime import date, timedelta
    from app.core.database import AsyncSessionLocal
    from app.services import signal_engine
    import aiosqlite

    async with AsyncSessionLocal() as db:
        for d in [date.today() - timedelta(days=i) for i in range(7)]:
            n = await signal_engine.compute_signals_for_date(db, d)

    async with aiosqlite.connect("titibet.db") as db:
        db.row_factory = aiosqlite.Row
        c = await db.execute("""
            SELECT s.market, s.dual_agreement, s.is_candidate, COUNT(*) as n
            FROM signals s JOIN fixtures f ON s.fixture_id=f.id
            WHERE f.event_date >= date('now','-7 days')
            GROUP BY s.market, s.dual_agreement, s.is_candidate ORDER BY n DESC
        """)
        for r in await c.fetchall(): print(dict(r))

asyncio.run(test())

import asyncio, sys
sys.path.insert(0, ".")

async def main():
    import aiosqlite
    async with aiosqlite.connect("titibet.db") as db:
        db.row_factory = aiosqlite.Row

        # How many fixtures in last 7d have Over 1.5 Goals bookmaker odds at all?
        c = await db.execute("""
            SELECT COUNT(DISTINCT ms.fixture_id) as fixtures_with_o15
            FROM market_snapshots ms
            WHERE ms.selection_name = 'Over 1.5'
            AND ms.market_type IN ('Goals Over/Under','Total Goals','Goals Over Under')
            AND EXISTS (SELECT 1 FROM fixtures f WHERE f.id=ms.fixture_id AND f.event_date >= date('now','-7 days'))
        """)
        r = await c.fetchone()
        print(f"Fixtures with 'Over 1.5' bookmaker odds (last 7d): {r['fixtures_with_o15']}")

        # What are the actual market_type names for over/under goals?
        c = await db.execute("""
            SELECT ms.market_type, ms.selection_name, COUNT(*) as n
            FROM market_snapshots ms
            JOIN fixtures f ON ms.fixture_id=f.id
            WHERE f.event_date >= date('now','-7 days')
            AND (ms.selection_name LIKE 'Over%' OR ms.selection_name LIKE 'Under%')
            GROUP BY ms.market_type, ms.selection_name ORDER BY n DESC LIMIT 20
        """)
        print("\nOver/Under selections in snapshots (7d):")
        for r in await c.fetchall(): print(f"  {dict(r)}")

        # CS odds availability for recent fixtures
        c = await db.execute("""
            SELECT COUNT(DISTINCT ms.fixture_id) as n
            FROM market_snapshots ms
            JOIN fixtures f ON ms.fixture_id=f.id
            WHERE f.event_date >= date('now','-7 days')
            AND ms.selection_name = '0:0'
        """)
        r = await c.fetchone()
        print(f"\nFixtures with CS 0:0 odds (last 7d): {r['n']}")

asyncio.run(main())

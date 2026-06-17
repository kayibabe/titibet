import asyncio, sys
sys.path.insert(0, ".")

async def audit():
    import aiosqlite
    async with aiosqlite.connect("titibet.db") as db:
        db.row_factory = aiosqlite.Row

        c = await db.execute("""
            SELECT league,
                   COUNT(*) as bets,
                   SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN result_status='Lost' THEN 1 ELSE 0 END) as losses,
                   ROUND(SUM(profit_loss),2) as pl
            FROM tracked_bets
            WHERE source_rule_key='system_auto' AND result_status IN ('Won','Lost')
            GROUP BY league HAVING bets >= 3
            ORDER BY pl ASC LIMIT 20
        """)
        rows = await c.fetchall()
        print("=== System auto - league performance (worst first, min 3 bets) ===")
        for r in rows:
            d = dict(r)
            settled = d['wins'] + d['losses']
            wr = round(d['wins'] / settled * 100, 1) if settled else 0
            print(f"  {d['league']:<40} {d['bets']:>3}b  {wr:>5}%WR  {d['pl']:>+10}")

        c = await db.execute("""
            SELECT COUNT(*) as n FROM tracked_bets
            WHERE result_status='Pending' AND event_date < date('now','-2 days')
        """)
        print(f"\nStuck pending (>2 days old): {(await c.fetchone())['n']}")

        c = await db.execute("""
            SELECT run_date, started_at, signals_computed, fixtures_pulled
            FROM ingestion_runs
            WHERE run_date >= date('now','-7 days') AND signals_computed=0
            ORDER BY started_at
        """)
        rows = await c.fetchall()
        print(f"\n=== Zero-signal runs last 7d ({len(rows)} total) ===")
        for r in rows:
            d = dict(r)
            print(f"  {d['run_date']}  {d['started_at'][11:16]}  fixtures:{d['fixtures_pulled']}")

        c = await db.execute("""
            SELECT DISTINCT lower(trim(f.league)) as lc, f.league, f.country
            FROM signals s JOIN fixtures f ON s.fixture_id=f.id
            WHERE f.event_date >= date('now','-7 days')
        """)
        rows = await c.fetchall()
        BANNED = {
            "pro league","reserve league","segunda division","persha liga",
            "premiere division","serie c - promotion - play-offs","serie d",
            "usl championship","primera division femenina","regionalliga - mitte",
            "regionalliga - ost","regionalliga - west",
        }
        leaking = [dict(r) for r in rows if r['lc'] in BANNED]
        print(f"\n=== Banned leagues still in signals ===")
        if leaking:
            for r in leaking: print(f"  LEAK: {r['league']} ({r['country']})")
        else:
            print("  None")

        c = await db.execute("""SELECT COUNT(*) as n FROM signals s
            JOIN fixtures f ON s.fixture_id=f.id WHERE f.event_date=date('now')""")
        print(f"\nSignals today: {(await c.fetchone())['n']}")

        c = await db.execute("""
            SELECT dual_confidence, COUNT(*) as n FROM signals s
            JOIN fixtures f ON s.fixture_id=f.id
            WHERE f.event_date >= date('now','-30 days')
            GROUP BY dual_confidence
        """)
        print("\n=== Signal confidence distribution (30d) ===")
        for r in await c.fetchall(): print(f"  {dict(r)}")

asyncio.run(audit())

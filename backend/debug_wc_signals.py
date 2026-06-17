import asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from app.core.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        # Check what odds snapshots exist for WC upcoming games
        r = await db.execute(text("""
            SELECT mo.market_type, mo.bookmaker, mo.home_odd, mo.draw_odd, mo.away_odd,
                   mo.over_1_5, mo.over_2_5, mo.over_3_5,
                   mo.home_cs_0_0, mo.home_cs_1_0, mo.home_cs_2_0, mo.home_cs_1_1
            FROM market_snapshots mo
            WHERE mo.fixture_id = 9626
            ORDER BY mo.bookmaker
            LIMIT 20
        """))
        rows = r.all()
        print(f"France vs Senegal (id=9626) — odds snapshot sample ({len(rows)} rows shown):")
        for row in rows:
            print(f"  {row[1]} | {row[0]} | H={row[2]} D={row[3]} A={row[4]} | O1.5={row[5]} O2.5={row[6]}")

        # Check what poisson data exists for these teams
        r2 = await db.execute(text("""
            SELECT f.home_team, f.away_team, f.event_date, s.poisson_lambda_home, s.poisson_lambda_away,
                   s.poisson_prob, s.bayesian_prob, s.bayesian_best_odd, s.bayesian_bookmaker,
                   s.dual_confidence, s.dual_agreement, s.market
            FROM signals s
            JOIN fixtures f ON f.id = s.fixture_id
            WHERE f.event_date = '2026-06-16'
            ORDER BY s.dual_quality_score DESC NULLS LAST
        """))
        sigs = r2.all()
        print(f"\nAll signals for today ({len(sigs)}):")
        for row in sigs:
            print(f"  {row[0]} vs {row[1]} | {row[11]} | lam={row[3]:.2f}/{row[4]:.2f} | "
                  f"pois_p={row[5]:.3f} bay_p={row[6]:.3f} | {row[9]} | {row[10]}")

        # Check total distinct bookmakers with H2H CS odds for WC games
        r3 = await db.execute(text("""
            SELECT COUNT(DISTINCT bookmaker) as n_books,
                   SUM(CASE WHEN home_cs_1_0 IS NOT NULL THEN 1 ELSE 0 END) as has_cs
            FROM market_snapshots
            WHERE fixture_id = 9626
        """))
        row = r3.one()
        print(f"\nFrance vs Senegal: {row[0]} bookmakers, {row[1]} rows with CS odds")

        r4 = await db.execute(text("""
            SELECT COUNT(DISTINCT bookmaker) as n_books,
                   SUM(CASE WHEN home_cs_1_0 IS NOT NULL THEN 1 ELSE 0 END) as has_cs
            FROM market_snapshots
            WHERE fixture_id = 9635
        """))
        row = r4.one()
        print(f"Iraq vs Norway: {row[0]} bookmakers, {row[1]} rows with CS odds")

asyncio.run(main())

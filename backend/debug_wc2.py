import asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from app.core.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text(
            "SELECT bookmaker, selection_name, odds FROM market_snapshots "
            "WHERE fixture_id=9626 AND market_type='Total - Home' "
            "ORDER BY bookmaker, selection_name"
        ))
        rows = r.all()
        print(f"Total - Home for France vs Senegal ({len(rows)} rows):")
        for row in rows:
            print(f"  {row[0]} | {row[1]} | {row[2]}")

        # Also check Correct Score for Bayesian engine
        r2 = await db.execute(text(
            "SELECT bookmaker, selection_name, odds FROM market_snapshots "
            "WHERE fixture_id=9626 AND market_type='Correct Score' "
            "ORDER BY bookmaker, selection_name LIMIT 20"
        ))
        rows2 = r2.all()
        print(f"\nCorrect Score for France vs Senegal ({len(rows2)} rows):")
        for row in rows2:
            print(f"  {row[0]} | {row[1]} | {row[2]}")

        # Check what the poisson engine needs — historical goals for these teams
        # Look at how many WC fixtures France/Senegal have in DB
        r3 = await db.execute(text(
            "SELECT home_team, away_team, home_score, away_score, event_date "
            "FROM fixtures "
            "WHERE (home_team='France' OR away_team='France') "
            "AND home_score IS NOT NULL "
            "ORDER BY event_date DESC LIMIT 10"
        ))
        rows3 = r3.all()
        print(f"\nFrance recent matches in DB (for Poisson):")
        for row in rows3:
            print(f"  {row[0]} vs {row[1]} | {row[2]}-{row[3]} | {row[4]}")

asyncio.run(main())

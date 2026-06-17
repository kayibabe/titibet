import asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from app.core.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        # Get all WC fixtures with their signals
        r = await db.execute(text("""
            SELECT f.id, f.home_team, f.away_team, f.status, f.event_date,
                   s.market, s.dual_confidence, s.dual_agreement, s.dual_quality_score,
                   s.poisson_rule_strong
            FROM fixtures f
            LEFT JOIN signals s ON s.fixture_id = f.id
            WHERE lower(f.league) LIKE '%world cup%'
            ORDER BY f.event_date DESC, f.id
        """))
        rows = r.all()

        print("World Cup fixtures & signals:")
        for row in rows:
            fid, home, away, status, date, mkt, conf, agree, qs, strong = row
            sig_str = f"  -> {mkt} | {conf} | {agree} | qs={qs:.3f} | strong={strong}" if mkt else "  -> NO SIGNAL"
            print(f"[{status}] {date} | {home} vs {away} (id={fid})")
            print(sig_str)

        # Check today's upcoming WC games and why they might not show
        print("\n\nUpcoming (NS) WC games specifically:")
        r2 = await db.execute(text("""
            SELECT f.id, f.home_team, f.away_team, f.event_date,
                   COUNT(s.id) as signal_count,
                   COUNT(mo.id) as odds_count
            FROM fixtures f
            LEFT JOIN signals s ON s.fixture_id = f.id
            LEFT JOIN market_snapshots mo ON mo.fixture_id = f.id
            WHERE lower(f.league) LIKE '%world cup%'
              AND f.status = 'NS'
            GROUP BY f.id
        """))
        for row in r2.all():
            print(f"  id={row[0]}: {row[1]} vs {row[2]} | date={row[3]} | signals={row[4]} | odds_snapshots={row[5]}")

asyncio.run(main())

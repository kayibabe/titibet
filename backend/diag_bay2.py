import asyncio, sys
sys.path.insert(0, ".")

async def main():
    from datetime import date, timedelta
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models import Fixture, MarketSnapshot
    from app.services.signal_engine import _build_poisson_odds
    from app.engines import bayesian as bay_engine

    # Find a recent fixture that has Over 1.5 bookmaker odds
    import aiosqlite
    async with aiosqlite.connect("titibet.db") as raw:
        raw.row_factory = aiosqlite.Row
        c = await raw.execute("""
            SELECT f.id, f.home_team, f.away_team, f.event_date, f.league, f.status,
                   COUNT(ms.id) as snap_count
            FROM fixtures f JOIN market_snapshots ms ON ms.fixture_id=f.id
            WHERE f.event_date >= date('now','-3 days')
            AND EXISTS (
                SELECT 1 FROM market_snapshots ms2
                WHERE ms2.fixture_id=f.id
                AND ms2.selection_name='Over 1.5'
                AND ms2.market_type='Goals Over/Under'
            )
            GROUP BY f.id ORDER BY f.event_date DESC LIMIT 3
        """)
        fixtures_raw = await c.fetchall()

    if not fixtures_raw:
        print("No fixtures with Over 1.5 odds found in last 3 days")
        return

    async with AsyncSessionLocal() as db:
        for fr in fixtures_raw:
            fid = fr['id']
            print(f"\n--- {fr['home_team']} vs {fr['away_team']} ({fr['event_date']}, {fr['league']}) ---")

            result = await db.execute(
                select(MarketSnapshot).where(MarketSnapshot.fixture_id == fid)
            )
            snaps = list(result.scalars().all())

            poi_odds, poi_signal_odds = _build_poisson_odds(snaps)
            print(f"  signal_odds: {poi_signal_odds}")
            print(f"  over1_5 price: {poi_signal_odds.get('over1_5')}")
            print(f"  CS s00: {poi_odds.get('s00')}")

            bay_result = bay_engine.analyse_fixture(
                fixture_id=fid,
                snapshots=snaps,
                home_totals=None, away_totals=None, win_to_nil_home=None, win_to_nil_away=None,
                exact_goals=None, all_markets=True,
            )

            for mr in bay_result.market_results:
                if mr.market in ("Over 1.5", "Over 2.5", "Home Over 0.5"):
                    print(f"  Bayesian {mr.market}: prob={mr.derived_prob:.3f} is_value={mr.is_value} "
                          f"confidence={mr.confidence} edge={mr.edge:.3f} best_odd={mr.best_actual_odd}")

asyncio.run(main())

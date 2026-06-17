import asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from app.core.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text("""
            SELECT
                result_status,
                COUNT(*) as n,
                AVG(stake) as avg_stake,
                AVG(odds) as avg_odds,
                SUM(profit_loss) as total_pl,
                SUM(stake) as total_staked,
                MIN(odds) as min_odds,
                MAX(odds) as max_odds
            FROM tracked_bets
            WHERE result_status IN ('Won', 'Lost')
            GROUP BY result_status
        """))
        print("By result:")
        for row in r.all():
            print(f"  {row[0]}: n={row[1]}, avg_stake={row[2]:.2f}, avg_odds={row[3]:.3f}, "
                  f"min_odds={row[6]:.3f}, max_odds={row[7]:.3f}, "
                  f"total_pl={row[4]:.2f}, total_staked={row[5]:.2f}")

        # Sample of won bets with their profit_loss
        r2 = await db.execute(text("""
            SELECT stake, odds, profit_loss, market_type, source_rule_key
            FROM tracked_bets WHERE result_status = 'Won' LIMIT 10
        """))
        print("\nSample Won bets:")
        for row in r2.all():
            expected_pl = round(row[0] * (row[1] - 1), 2)
            print(f"  stake={row[0]:.2f}, odds={row[1]:.3f}, profit_loss={row[2]:.2f}, "
                  f"expected={expected_pl:.2f}, src={row[4]}")

        r3 = await db.execute(text("""
            SELECT stake, odds, profit_loss, market_type, source_rule_key
            FROM tracked_bets WHERE result_status = 'Lost' LIMIT 10
        """))
        print("\nSample Lost bets:")
        for row in r3.all():
            expected_pl = round(-row[0], 2)
            print(f"  stake={row[0]:.2f}, odds={row[1]:.3f}, profit_loss={row[2]:.2f}, "
                  f"expected={expected_pl:.2f}, src={row[4]}")

asyncio.run(main())

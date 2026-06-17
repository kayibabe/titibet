import asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from app.core.database import AsyncSessionLocal
from sqlalchemy import text

FLAT_STAKE = 50_000.0

async def main():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text("""
            SELECT id, odds, result_status FROM tracked_bets
            WHERE source_rule_key IN ('system_dual','system_auto')
        """))
        rows = r.all()

        for bet_id, odds, status in rows:
            if status == 'Won':
                pl = round(FLAT_STAKE * (odds - 1), 2)
            elif status == 'Lost':
                pl = round(-FLAT_STAKE, 2)
            else:
                pl = 0.0

            await db.execute(text("""
                UPDATE tracked_bets SET stake = :stake, profit_loss = :pl WHERE id = :id
            """), {"stake": FLAT_STAKE, "pl": pl, "id": bet_id})

        await db.commit()
        print(f"Updated {len(rows)} bets to flat stake K{FLAT_STAKE:,.2f}")

        # Summary
        r2 = await db.execute(text("""
            SELECT result_status, COUNT(*), SUM(profit_loss), SUM(stake)
            FROM tracked_bets
            WHERE source_rule_key IN ('system_dual','system_auto')
              AND result_status IN ('Won','Lost')
            GROUP BY result_status
        """))
        rows2 = {row[0]: row for row in r2.all()}
        won  = rows2.get('Won',  (None,0,0,0))
        lost = rows2.get('Lost', (None,0,0,0))

        n_won, n_lost = won[1], lost[1]
        settled   = n_won + n_lost
        hit_rate  = n_won / settled * 100 if settled else 0
        total_pl  = (won[3-1] or 0) + (lost[3-1] or 0)  # index 2 = SUM(profit_loss)

        # re-index properly
        w_pl, w_stk = won[2] or 0, won[3] or 0
        l_pl, l_stk = lost[2] or 0, lost[3] or 0
        total_pl  = w_pl + l_pl
        total_stk = w_stk + l_stk
        roi = total_pl / total_stk * 100 if total_stk else 0

        print(f"\nWon:      {n_won}")
        print(f"Lost:     {n_lost}")
        print(f"Hit Rate: {hit_rate:.1f}%")
        print(f"Total Staked: K{total_stk:,.2f}")
        print(f"Total P&L:    K{total_pl:+,.2f}")
        print(f"ROI:          {roi:+.1f}%")

        # by source
        r3 = await db.execute(text("""
            SELECT source_rule_key, result_status, COUNT(*), SUM(profit_loss), SUM(stake)
            FROM tracked_bets
            WHERE source_rule_key IN ('system_dual','system_auto')
              AND result_status IN ('Won','Lost')
            GROUP BY source_rule_key, result_status
        """))
        from collections import defaultdict
        by_src = defaultdict(lambda: {'Won':0,'Lost':0,'pl':0,'stake':0})
        for key, status, n, pl, stake in r3.all():
            by_src[key][status] += n
            by_src[key]['pl']   += (pl or 0)
            by_src[key]['stake']+= (stake or 0)

        print()
        for src, d in sorted(by_src.items()):
            s = d['Won'] + d['Lost']
            hr   = d['Won'] / s * 100 if s else 0
            roi_ = d['pl'] / d['stake'] * 100 if d['stake'] else 0
            print(f"  {src}: {d['Won']}W / {d['Lost']}L  HR={hr:.1f}%  ROI={roi_:+.1f}%  P&L=K{d['pl']:+,.2f}")

asyncio.run(main())

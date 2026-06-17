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
                SUM(stake) as total_staked,
                SUM(profit_loss) as total_pl
            FROM tracked_bets
            WHERE source_rule_key IN ('system_dual','system_auto')
            GROUP BY result_status
        """))
        rows = {row[0]: row for row in r.all()}

        won     = rows.get('Won',    (None,0,0,0))
        lost    = rows.get('Lost',   (None,0,0,0))
        pending = rows.get('Pending',(None,0,0,0))

        n_won, n_lost, n_pending = won[1], lost[1], pending[1]
        settled = n_won + n_lost
        hit_rate = n_won / settled * 100 if settled else 0

        total_pl    = (won[3] or 0) + (lost[3] or 0)
        total_stake = (won[2] or 0) + (lost[2] or 0)
        roi = total_pl / total_stake * 100 if total_stake else 0

        print(f"Won:     {n_won}")
        print(f"Lost:    {n_lost}")
        print(f"Pending: {n_pending}")
        print(f"Hit Rate: {hit_rate:.1f}%")
        print(f"ROI:      {roi:+.1f}%")
        print(f"P&L:     +{total_pl:.2f}" if total_pl >= 0 else f"P&L:     {total_pl:.2f}")
        print(f"Staked:   {total_stake:.2f}")

        # Break down by source
        print()
        r2 = await db.execute(text("""
            SELECT source_rule_key, result_status, COUNT(*), SUM(profit_loss), SUM(stake)
            FROM tracked_bets
            WHERE source_rule_key IN ('system_dual','system_auto')
              AND result_status IN ('Won','Lost')
            GROUP BY source_rule_key, result_status
        """))
        from collections import defaultdict
        by_src = defaultdict(lambda: {'Won':0,'Lost':0,'pl':0,'stake':0})
        for key, status, n, pl, stake in r2.all():
            by_src[key][status] += n
            by_src[key]['pl'] += (pl or 0)
            by_src[key]['stake'] += (stake or 0)

        for src, d in sorted(by_src.items()):
            s = d['Won'] + d['Lost']
            hr = d['Won'] / s * 100 if s else 0
            roi_s = d['pl'] / d['stake'] * 100 if d['stake'] else 0
            print(f"  {src}: {d['Won']}W / {d['Lost']}L  HR={hr:.1f}%  ROI={roi_s:+.1f}%  P&L={d['pl']:+.2f}")

asyncio.run(main())

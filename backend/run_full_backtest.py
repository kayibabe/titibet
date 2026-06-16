"""
run_full_backtest.py — Full backtest across all finished fixtures.
10,000 flat stake, dual engine, all markets and rules active.
"""
import asyncio
import sys
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")


async def run():
    from app.core.database import AsyncSessionLocal
    from app.services.backtester import run_backtest
    import aiosqlite

    # Date range from DB
    async with aiosqlite.connect("titibet.db") as raw:
        raw.row_factory = aiosqlite.Row
        c = await raw.execute(
            "SELECT MIN(event_date) as d_from, MAX(event_date) as d_to, "
            "COUNT(DISTINCT event_date) as days, COUNT(*) as fixtures "
            "FROM fixtures WHERE status IN ('FT','AET','PEN') AND home_score IS NOT NULL"
        )
        meta = dict(await c.fetchone())

    print("=" * 72)
    print("FULL BACKTEST — all signals, all markets, 10,000 flat stake")
    print(f"Date range : {meta['d_from']}  →  {meta['d_to']}")
    print(f"Fixtures   : {meta['fixtures']} finished across {meta['days']} days")
    print("Engine     : dual (Bayesian + Poisson)")
    print("=" * 72)

    async with AsyncSessionLocal() as db:
        summary = await run_backtest(db=db, engine="dual", min_edge=0.05)

    total  = summary["total_bets"]
    wins   = summary["wins"]
    losses = summary["losses"]
    voids  = summary.get("voids", 0)
    roi    = summary["roi"]
    profit = summary["total_profit"]
    staked = summary["total_stake"]
    avg_odds = summary["avg_odds"]
    hit_rate = summary["hit_rate"]

    print(f"\n{'Signals fired':<22}: {total}")
    print(f"{'Wins / Losses':<22}: {wins} / {losses}", end="")
    if voids:
        print(f"  ({voids} void)", end="")
    print()
    print(f"{'Hit rate':<22}: {hit_rate:.1f}%")
    print(f"{'Avg odds':<22}: {avg_odds:.3f}")
    print(f"{'Flat stake':<22}: 10,000")
    print(f"{'Total staked':<22}: {staked:,.0f}")
    print(f"{'Net P&L':<22}: {profit:+,.0f}")
    print(f"{'ROI':<22}: {roi:+.1f}%")

    # ── By market ──────────────────────────────────────────────────────────────
    print("\n" + "─" * 72)
    print(f"{'Market':<26} {'Bets':>5}  {'WR%':>6}  {'Avg odds':>8}  {'ROI%':>7}")
    print("─" * 72)
    for m in sorted(summary["by_market"], key=lambda x: -x["total"]):
        print(f"  {m['market']:<24} {m['total']:>5}  {m['hit_rate']:>5.1f}%  "
              f"{(m['avg_odds'] or 0):>8.3f}  {m['roi']:>+7.1f}%")

    # ── By confidence ──────────────────────────────────────────────────────────
    conf_rows = summary.get("by_confidence", [])
    if conf_rows:
        print("\n" + "─" * 72)
        print(f"{'Confidence':<26} {'Bets':>5}  {'WR%':>6}  {'Avg odds':>8}  {'ROI%':>7}")
        print("─" * 72)
        for c in sorted(conf_rows, key=lambda x: -x["total"]):
            print(f"  {c['confidence']:<24} {c['total']:>5}  {c['hit_rate']:>5.1f}%  "
                  f"{(c.get('avg_odds') or 0):>8.3f}  {c['roi']:>+7.1f}%")

    # ── By agreement ───────────────────────────────────────────────────────────
    agr_rows = summary.get("by_agreement", [])
    if agr_rows:
        print("\n" + "─" * 72)
        print(f"{'Agreement':<26} {'Bets':>5}  {'WR%':>6}  {'Avg odds':>8}  {'ROI%':>7}")
        print("─" * 72)
        for a in sorted(agr_rows, key=lambda x: -x["total"]):
            print(f"  {a['agreement']:<24} {a['total']:>5}  {a['hit_rate']:>5.1f}%  "
                  f"{(a.get('avg_odds') or 0):>8.3f}  {a['roi']:>+7.1f}%")

    print("\n" + "=" * 72)
    print("Backtest complete.")


asyncio.run(run())

"""
analyse_backtest.py — Deep breakdown of existing backtest_results rows.
No re-run needed — reads from what run_full_backtest.py already wrote.
"""
import asyncio, sys
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from app.core.config import BACKTEST_FLAT_STAKE

STAKE = BACKTEST_FLAT_STAKE


def table(rows, col_name, col_w=28):
    header = f"  {col_name:<{col_w}} {'Bets':>5}  {'WR%':>6}  {'Avg odds':>8}  {'ROI%':>7}"
    sep = "─" * (col_w + 38)
    print(sep)
    print(header)
    print(sep)
    for r in rows:
        print(
            f"  {str(r[col_name]):<{col_w}} {r['n']:>5}  {r['wr']:>5.1f}%  "
            f"{r['avg_odds']:>8.3f}  {r['roi']:>+7.1f}%"
        )


async def main():
    import aiosqlite

    async with aiosqlite.connect("titibet.db") as db:
        db.row_factory = aiosqlite.Row

        # ── By league (min 5 bets) ────────────────────────────────────────────
        c = await db.execute("""
            SELECT league_name,
                   COUNT(*) as n,
                   SUM(bet_result) as wins,
                   SUM(profit_loss) as profit,
                   AVG(actual_odd) as avg_odds,
                   league_tier
            FROM backtest_results
            WHERE actual_odd IS NOT NULL
            GROUP BY league_name
            HAVING COUNT(*) >= 5
            ORDER BY SUM(profit_loss) / (COUNT(*) * ?) DESC
        """, (STAKE,))
        rows = await c.fetchall()
        print("\n── By league (≥5 bets) ──────────────────────────────────────────────────")
        league_rows = []
        for r in rows:
            r = dict(r)
            wr = (r["wins"] / r["n"] * 100) if r["n"] else 0
            roi = (r["profit"] / (r["n"] * STAKE) * 100) if r["n"] else 0
            league_rows.append({
                "league_name": f"{r['league_name']} (T{r['league_tier'] or '?'})",
                "n": r["n"], "wr": wr,
                "avg_odds": r["avg_odds"] or 0, "roi": roi,
            })
        table(league_rows, "league_name", col_w=38)

        # ── By odds band ──────────────────────────────────────────────────────
        c = await db.execute("""
            SELECT
                CASE
                    WHEN actual_odd < 1.80 THEN '1.70 – 1.79'
                    WHEN actual_odd < 1.90 THEN '1.80 – 1.89'
                    WHEN actual_odd < 2.00 THEN '1.90 – 1.99'
                    WHEN actual_odd < 2.10 THEN '2.00 – 2.09'
                    WHEN actual_odd < 2.25 THEN '2.10 – 2.24'
                    WHEN actual_odd < 2.50 THEN '2.25 – 2.49'
                    ELSE                       '2.50+'
                END as band,
                COUNT(*) as n,
                SUM(bet_result) as wins,
                SUM(profit_loss) as profit,
                AVG(actual_odd) as avg_odds
            FROM backtest_results
            WHERE actual_odd IS NOT NULL
            GROUP BY band
            ORDER BY MIN(actual_odd)
        """)
        rows = await c.fetchall()
        print("\n── By odds band ─────────────────────────────────────────────────────────")
        band_rows = []
        for r in rows:
            r = dict(r)
            wr = (r["wins"] / r["n"] * 100) if r["n"] else 0
            roi = (r["profit"] / (r["n"] * STAKE) * 100) if r["n"] else 0
            band_rows.append({
                "band": r["band"], "n": r["n"], "wr": wr,
                "avg_odds": r["avg_odds"] or 0, "roi": roi,
            })
        table(band_rows, "band", col_w=16)

        # ── By league tier ────────────────────────────────────────────────────
        c = await db.execute("""
            SELECT league_tier,
                   COUNT(*) as n,
                   SUM(bet_result) as wins,
                   SUM(profit_loss) as profit,
                   AVG(actual_odd) as avg_odds
            FROM backtest_results
            WHERE actual_odd IS NOT NULL
            GROUP BY league_tier
            ORDER BY league_tier
        """)
        rows = await c.fetchall()
        print("\n── By league tier ───────────────────────────────────────────────────────")
        tier_rows = []
        for r in rows:
            r = dict(r)
            wr = (r["wins"] / r["n"] * 100) if r["n"] else 0
            roi = (r["profit"] / (r["n"] * STAKE) * 100) if r["n"] else 0
            tier_rows.append({
                "tier": f"Tier {r['league_tier'] or '?'}", "n": r["n"], "wr": wr,
                "avg_odds": r["avg_odds"] or 0, "roi": roi,
            })
        table(tier_rows, "tier", col_w=10)

        # ── Agreement × Odds band ─────────────────────────────────────────────
        c = await db.execute("""
            SELECT dual_agreement,
                   CASE
                       WHEN actual_odd < 1.90 THEN '< 1.90'
                       WHEN actual_odd < 2.10 THEN '1.90 – 2.09'
                       ELSE                       '2.10+'
                   END as band,
                   COUNT(*) as n,
                   SUM(bet_result) as wins,
                   SUM(profit_loss) as profit,
                   AVG(actual_odd) as avg_odds
            FROM backtest_results
            WHERE actual_odd IS NOT NULL
            GROUP BY dual_agreement, band
            ORDER BY dual_agreement, MIN(actual_odd)
        """)
        rows = await c.fetchall()
        print("\n── Agreement × Odds band ────────────────────────────────────────────────")
        cross_rows = []
        for r in rows:
            r = dict(r)
            wr = (r["wins"] / r["n"] * 100) if r["n"] else 0
            roi = (r["profit"] / (r["n"] * STAKE) * 100) if r["n"] else 0
            cross_rows.append({
                "agreement_band": f"{r['dual_agreement']} | {r['band']}",
                "n": r["n"], "wr": wr,
                "avg_odds": r["avg_odds"] or 0, "roi": roi,
            })
        table(cross_rows, "agreement_band", col_w=28)

        # ── Loss clusters — which leagues lose most ───────────────────────────
        c = await db.execute("""
            SELECT league_name, league_tier,
                   COUNT(*) as n,
                   SUM(CASE WHEN bet_result = 0 THEN 1 ELSE 0 END) as losses,
                   SUM(profit_loss) as profit
            FROM backtest_results
            GROUP BY league_name
            HAVING losses >= 2
            ORDER BY profit ASC
            LIMIT 15
        """)
        rows = await c.fetchall()
        print("\n── Biggest loss clusters (worst P&L, ≥2 losses) ────────────────────────")
        print(f"  {'League':<38} {'T':>2}  {'Bets':>5}  {'Losses':>6}  {'P&L':>10}")
        print("─" * 68)
        for r in rows:
            r = dict(r)
            print(f"  {(r['league_name'] or 'Unknown'):<38} {r['league_tier'] or '?':>2}  "
                  f"{r['n']:>5}  {r['losses']:>6}  {r['profit']:>+10,.0f}")


asyncio.run(main())

"""
run_backtest.py — Standalone backtest comparison script.

Runs two passes:
  A) BASELINE  — replicates old behavior (no new gates) using raw tracked_bets history
  B) NEW RULES — runs full backtester with all new gates active

Prints a detailed comparison report.
"""
import asyncio
import sys
from datetime import date
from collections import defaultdict

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")


async def run():
    from app.core.database import AsyncSessionLocal
    from app.services.backtester import run_backtest
    import aiosqlite

    # ── A) BASELINE from tracked_bets (what actually happened) ──────────────
    print("=" * 70)
    print("A) BASELINE — actual tracked_bets history (pre-improvement rules)")
    print("=" * 70)

    async with aiosqlite.connect("titibet.db") as raw:
        raw.row_factory = aiosqlite.Row

        c = await raw.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result_status='Lost' THEN 1 ELSE 0 END) as losses,
                ROUND(100.0*SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END)
                      /NULLIF(SUM(CASE WHEN result_status IN ('Won','Lost') THEN 1 ELSE 0 END),0),1) as win_rate,
                ROUND(AVG(CASE WHEN result_status IN ('Won','Lost') THEN odds END),3) as avg_odds,
                ROUND(SUM(profit_loss),2) as net_pnl,
                ROUND(SUM(stake),2) as total_staked
            FROM tracked_bets
            WHERE result_status IN ('Won','Lost')
        """)
        row = dict(await c.fetchone())
        roi = round(row["net_pnl"] / row["total_staked"] * 100, 1) if row["total_staked"] else 0
        print(f"  Settled bets  : {row['total']}")
        print(f"  Wins / Losses : {row['wins']} / {row['losses']}")
        print(f"  Win rate      : {row['win_rate']}%")
        print(f"  Avg odds      : {row['avg_odds']}")
        print(f"  Net P&L       : {row['net_pnl']:,}")
        print(f"  Total staked  : {row['total_staked']:,}")
        print(f"  ROI           : {roi}%")

        # By market
        c = await raw.execute("""
            SELECT market_type,
                COUNT(*) as bets,
                SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END) as wins,
                ROUND(100.0*SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),1) as wr,
                ROUND(AVG(odds),3) as avg_odds,
                ROUND(SUM(profit_loss),2) as pnl
            FROM tracked_bets
            WHERE result_status IN ('Won','Lost')
            GROUP BY market_type ORDER BY bets DESC
        """)
        rows = await c.fetchall()
        print("\n  By market:")
        for r in rows:
            d = dict(r)
            stake = d["bets"] * 100  # assuming 100 unit stake in tracker
            roi_m = round(d["pnl"] / stake * 100, 1) if stake else 0
            print(f"    {d['market_type']:<22} {d['bets']:>4} bets  {d['wr']:>5}% WR  "
                  f"avg {d['avg_odds']:.2f}  P&L {d['pnl']:>12,.0f}  ROI {roi_m:>+6.1f}%")

        # Regional breakdown — which leagues were worst
        c = await raw.execute("""
            SELECT league,
                COUNT(*) as bets,
                SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END) as wins,
                ROUND(100.0*SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),1) as wr,
                ROUND(SUM(profit_loss),2) as pnl
            FROM tracked_bets
            WHERE result_status IN ('Won','Lost')
            GROUP BY league
            HAVING COUNT(*) >= 4
            ORDER BY wr ASC LIMIT 10
        """)
        rows = await c.fetchall()
        print("\n  Worst leagues (>= 4 bets, sorted by win rate):")
        for r in rows:
            d = dict(r)
            print(f"    {d['league']:<35} {d['bets']:>3} bets  {d['wr']:>5}%  P&L {d['pnl']:>10,.0f}")

    # ── B) NEW RULES backtest via engine ────────────────────────────────────
    print("\n" + "=" * 70)
    print("B) NEW RULES — backtester with all gates active (2026-05-17 to 2026-05-28)")
    print("=" * 70)

    async with AsyncSessionLocal() as db:
        summary = await run_backtest(
            db=db,
            date_from=date(2026, 5, 17),
            date_to=date(2026, 5, 28),
            engine="dual",
            min_edge=0.05,
        )

    total = summary["total_bets"]
    wins = summary["wins"]
    losses = summary["losses"]
    hit_rate = summary["hit_rate"]
    roi = summary["roi"]
    profit = summary["total_profit"]
    staked = summary["total_stake"]
    avg_odds = summary["avg_odds"]

    print(f"  Signals fired : {total}")
    print(f"  Wins / Losses : {wins} / {losses}")
    print(f"  Hit rate      : {hit_rate}%")
    print(f"  Avg odds      : {avg_odds}")
    print(f"  Net P&L       : {profit:,.2f}  (flat {staked/total:.0f}-unit stakes)" if total else "  No results")
    print(f"  Total staked  : {staked:,.2f}")
    print(f"  ROI           : {roi:+.1f}%")

    print("\n  By market:")
    for m in summary["by_market"]:
        print(f"    {m['market']:<22} {m['total']:>4} bets  {m['hit_rate']:>5}% WR  "
              f"avg {(m['avg_odds'] or 0):.2f}  ROI {m['roi']:>+6.1f}%")

    # ── C) DELTA ────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("C) DELTA — what the new rules eliminate / change")
    print("=" * 70)

    async with aiosqlite.connect("titibet.db") as raw:
        raw.row_factory = aiosqlite.Row

        # Bets that the new rules would have blocked
        blocked_queries = [
            ("Regionalliga suppression",
             "SELECT COUNT(*) as n, SUM(profit_loss) as pnl FROM tracked_bets "
             "WHERE result_status IN ('Won','Lost') AND lower(league) LIKE '%regionalliga%'"),

            ("Away O0.5 odds floor 1.70 (was 1.15)",
             "SELECT COUNT(*) as n, SUM(profit_loss) as pnl FROM tracked_bets "
             "WHERE result_status IN ('Won','Lost') AND source_rule_key='away_o05' "
             "AND odds >= 1.15 AND odds < 1.70"),

            ("Home/Away O1.5 in Tier 3",
             "SELECT COUNT(*) as n, SUM(profit_loss) as pnl FROM tracked_bets tb "
             "JOIN fixtures f ON tb.fixture_id = f.id "
             "WHERE tb.result_status IN ('Won','Lost') "
             "AND tb.source_rule_key IN ('home_o15','away_o15') "
             "AND f.league_tier >= 3"),

            ("End-of-season gate (Tier 3 over-goals, May 10-Jun 30)",
             "SELECT COUNT(*) as n, SUM(profit_loss) as pnl FROM tracked_bets tb "
             "JOIN fixtures f ON tb.fixture_id = f.id "
             "WHERE tb.result_status IN ('Won','Lost') "
             "AND tb.source_rule_key IN ('home_o05','away_o05','home_o15','away_o15') "
             "AND f.league_tier >= 3 "
             "AND (substr(tb.event_date,6,2)='05' AND CAST(substr(tb.event_date,9,2) AS INT) >= 10 "
             "     OR substr(tb.event_date,6,2)='06')"),

            ("Women's away-over > 2.30 odds",
             "SELECT COUNT(*) as n, SUM(profit_loss) as pnl FROM tracked_bets "
             "WHERE result_status IN ('Won','Lost') "
             "AND source_rule_key IN ('away_o05','away_o15') "
             "AND odds > 2.30 "
             "AND (lower(league) LIKE '%women%' OR lower(league) LIKE '%nwsl%' "
             "     OR lower(league) LIKE '%femenina%' OR lower(league) LIKE '%concacaf w%')"),
        ]

        total_blocked_bets = 0
        total_blocked_pnl = 0.0

        for label, sql in blocked_queries:
            c = await raw.execute(sql)
            r = dict(await c.fetchone())
            n = r["n"] or 0
            pnl = r["pnl"] or 0.0
            total_blocked_bets += n
            total_blocked_pnl += pnl
            direction = "AVOIDED" if pnl < 0 else "would lose wins"
            sign = "+" if pnl >= 0 else ""
            print(f"  {label}")
            print(f"    {n} bets  |  P&L {sign}{pnl:,.0f}  ({direction})")

        print(f"\n  TOTAL blocked bets  : {total_blocked_bets}")
        print(f"  TOTAL blocked P&L   : {'+' if total_blocked_pnl >= 0 else ''}{total_blocked_pnl:,.0f}  "
              f"({'net loss avoided' if total_blocked_pnl < 0 else 'net wins removed'})")

    # ── D) DEEP DIVE on remaining losses ────────────────────────────────────
    print("\n" + "=" * 70)
    print("D) REMAINING LOSSES after new rules — what still needs work")
    print("=" * 70)

    async with aiosqlite.connect("titibet.db") as raw:
        raw.row_factory = aiosqlite.Row

        # Losses that the new rules would NOT have blocked
        c = await raw.execute("""
            SELECT tb.market_type, tb.league, tb.match_name, tb.odds,
                   tb.dual_confidence, tb.source_rule_key, tb.event_date,
                   f.league_tier
            FROM tracked_bets tb
            LEFT JOIN fixtures f ON tb.fixture_id = f.id
            WHERE tb.result_status='Lost'
              AND lower(tb.league) NOT LIKE '%regionalliga%'
              AND NOT (tb.source_rule_key='away_o05' AND tb.odds >= 1.15 AND tb.odds < 1.70)
              AND NOT (tb.source_rule_key IN ('home_o15','away_o15') AND (f.league_tier IS NULL OR f.league_tier >= 3))
            ORDER BY tb.event_date, tb.league
        """)
        remaining = await c.fetchall()
        print(f"\n  {len(remaining)} losses survive the new rules:\n")

        # Group by market
        by_mkt = defaultdict(list)
        for r in remaining:
            by_mkt[r["market_type"]].append(dict(r))

        for mkt, bets in sorted(by_mkt.items(), key=lambda x: -len(x[1])):
            avg_o = sum(b["odds"] for b in bets) / len(bets)
            wins_possible = sum(1 for b in bets if b["odds"] < 1.5)
            tier3 = sum(1 for b in bets if (b["league_tier"] or 3) >= 3)
            print(f"  {mkt} — {len(bets)} remaining losses  avg odds {avg_o:.2f}  "
                  f"tier3={tier3}/{len(bets)}")
            for b in bets[:5]:
                print(f"    {b['event_date']}  {b['match_name'][:35]:<35}  "
                      f"@ {b['odds']:.2f}  [{b['league'][:25]}]  tier={b['league_tier']}")
            if len(bets) > 5:
                print(f"    ... and {len(bets)-5} more")
            print()

    # ── E) ODDS BAND analysis after rules ───────────────────────────────────
    print("=" * 70)
    print("E) ODDS BAND performance — surviving bets only (post-filter)")
    print("=" * 70)

    async with aiosqlite.connect("titibet.db") as raw:
        raw.row_factory = aiosqlite.Row
        c = await raw.execute("""
            SELECT
                CASE
                    WHEN odds < 1.50 THEN '<1.50'
                    WHEN odds < 1.70 THEN '1.50-1.69'
                    WHEN odds < 2.00 THEN '1.70-1.99'
                    WHEN odds < 2.50 THEN '2.00-2.49'
                    ELSE '>=2.50'
                END as band,
                COUNT(*) as bets,
                SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END) as wins,
                ROUND(100.0*SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),1) as wr,
                ROUND(SUM(profit_loss),2) as pnl
            FROM tracked_bets tb
            LEFT JOIN fixtures f ON tb.fixture_id = f.id
            WHERE result_status IN ('Won','Lost')
              AND lower(tb.league) NOT LIKE '%regionalliga%'
              AND NOT (tb.source_rule_key='away_o05' AND tb.odds >= 1.15 AND tb.odds < 1.70)
              AND NOT (tb.source_rule_key IN ('home_o15','away_o15')
                       AND (f.league_tier IS NULL OR f.league_tier >= 3))
            GROUP BY band ORDER BY MIN(odds)
        """)
        rows = await c.fetchall()
        print()
        for r in rows:
            d = dict(r)
            print(f"  {d['band']:<12}  {d['bets']:>4} bets  {d['wr']:>5}% WR  P&L {d['pnl']:>12,.0f}")

    print("\nBacktest complete.")


asyncio.run(run())

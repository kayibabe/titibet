"""
run_backtest_10k.py — Full backtest at 10,000 flat stake per bet.

Covers all available finished fixtures (2026-05-17 to 2026-05-28) with every
improvement gate active. Reports overall P&L, ROI, hit rate, and per-market
breakdown at 10,000 stake units.
"""
import asyncio
import sys
from datetime import date

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

FLAT_STAKE = 10_000.0
DATE_FROM  = date(2026, 5, 17)
DATE_TO    = date(2026, 5, 28)


async def run():
    # Patch stake before backtester module caches the config value
    import app.services.backtester as bt_mod
    bt_mod.BACKTEST_FLAT_STAKE = FLAT_STAKE

    from app.core.database import AsyncSessionLocal
    from app.services.backtester import run_backtest

    print("=" * 72)
    print(f"  TiTiBet Backtest — flat stake {FLAT_STAKE:,.0f} per bet")
    print(f"  Date range : {DATE_FROM}  →  {DATE_TO}")
    print(f"  Engine     : dual  (Bayesian + Poisson)")
    print(f"  Min edge   : 5%")
    print("=" * 72)

    async with AsyncSessionLocal() as db:
        summary = await run_backtest(
            db=db,
            date_from=DATE_FROM,
            date_to=DATE_TO,
            engine="dual",
            min_edge=0.05,
        )

    total   = summary["total_bets"]
    wins    = summary["wins"]
    losses  = summary["losses"]
    hit     = summary["hit_rate"]
    roi     = summary["roi"]
    profit  = summary["total_profit"]
    staked  = summary["total_stake"]
    avg_odd = summary["avg_odds"]

    if not total:
        print("\n  No signals fired — check that fixtures have market snapshots.")
        return

    print(f"\n{'─'*72}")
    print("  OVERALL RESULTS")
    print(f"{'─'*72}")
    print(f"  Signals fired    : {total:,}")
    print(f"  Wins / Losses    : {wins:,} / {losses:,}")
    print(f"  Hit rate         : {hit:.1f}%")
    print(f"  Average odds     : {avg_odd:.3f}" if avg_odd else "  Average odds     : n/a")
    print(f"  Total staked     : {staked:>15,.2f}")
    print(f"  Net P&L          : {profit:>+15,.2f}")
    print(f"  ROI              : {roi:>+.1f}%")
    print(f"  Yield per bet    : {profit/total:>+,.2f}")

    # Bankroll path summary
    curve = summary.get("bankroll_curve", [])
    if curve:
        bankrolls = [c["bankroll"] for c in curve]
        start_bk  = 100_000.0  # notional starting bankroll for context
        peak      = max(bankrolls)
        trough    = min(bankrolls)
        final     = bankrolls[-1]
        print(f"\n  Bankroll curve (starting 100 = normalised unit)")
        print(f"  Peak             : {peak:,.2f}  (+{peak-100:.2f})")
        print(f"  Trough           : {trough:,.2f}  ({trough-100:+.2f})")
        print(f"  Final            : {final:,.2f}  ({final-100:+.2f})")

    # ── By market ─────────────────────────────────────────────────────────────
    print(f"\n{'─'*72}")
    print("  BY MARKET")
    print(f"{'─'*72}")
    print(f"  {'Market':<24} {'Bets':>5} {'Wins':>5} {'WR%':>6} {'AvgOdds':>8} {'P&L':>14} {'ROI':>7}")
    print(f"  {'─'*24} {'─'*5} {'─'*5} {'─'*6} {'─'*8} {'─'*14} {'─'*7}")

    total_bets_check = 0
    for m in sorted(summary["by_market"], key=lambda x: x["profit"], reverse=True):
        bets    = m["total"]
        w       = m["wins"]
        wr      = m["hit_rate"]
        ao      = m["avg_odds"] or 0.0
        pnl     = m["profit"]   # backtester already ran at FLAT_STAKE (10k)
        r       = m["roi"]
        total_bets_check += bets
        pnl_str = f"{pnl:>+14,.2f}"
        print(f"  {m['market']:<24} {bets:>5} {w:>5} {wr:>5.1f}% {ao:>8.3f} {pnl_str} {r:>+6.1f}%")

    # ── Confidence breakdown ───────────────────────────────────────────────────
    print(f"\n{'─'*72}")
    print("  BY CONFIDENCE (from BacktestResult rows)")
    print(f"{'─'*72}")

    async with AsyncSessionLocal() as db2:
        from sqlalchemy import select, func as sqlfunc
        from app.models.backtest import BacktestResult

        rows = (await db2.execute(
            select(
                BacktestResult.dual_confidence,
                sqlfunc.count().label("total"),
                sqlfunc.sum(BacktestResult.bet_result).label("wins"),
                sqlfunc.sum(BacktestResult.profit_loss).label("pnl"),
            )
            .where(BacktestResult.fixture_date >= DATE_FROM)
            .where(BacktestResult.fixture_date <= DATE_TO)
            .group_by(BacktestResult.dual_confidence)
            .order_by(sqlfunc.count().desc())
        )).all()

    print(f"  {'Confidence':<12} {'Bets':>5} {'WR%':>6} {'P&L (scaled)':>16} {'ROI':>7}")
    print(f"  {'─'*12} {'─'*5} {'─'*6} {'─'*16} {'─'*7}")
    for r in rows:
        conf, tot, w, pnl_raw = r.dual_confidence, r.total, (r.wins or 0), (r.pnl or 0.0)
        wr_pct  = w / tot * 100 if tot else 0.0
        pnl_10k = pnl_raw   # already at 10k stake
        stake_  = tot * FLAT_STAKE
        roi_    = pnl_10k / stake_ * 100 if stake_ else 0.0
        print(f"  {conf or 'n/a':<12} {tot:>5} {wr_pct:>5.1f}% {pnl_10k:>+16,.2f} {roi_:>+6.1f}%")

    # ── League tier breakdown ──────────────────────────────────────────────────
    print(f"\n{'─'*72}")
    print("  BY LEAGUE TIER")
    print(f"{'─'*72}")

    async with AsyncSessionLocal() as db3:
        from sqlalchemy import select, func as sqlfunc
        from app.models.backtest import BacktestResult

        tier_rows = (await db3.execute(
            select(
                BacktestResult.league_tier,
                sqlfunc.count().label("total"),
                sqlfunc.sum(BacktestResult.bet_result).label("wins"),
                sqlfunc.sum(BacktestResult.profit_loss).label("pnl"),
            )
            .where(BacktestResult.fixture_date >= DATE_FROM)
            .where(BacktestResult.fixture_date <= DATE_TO)
            .group_by(BacktestResult.league_tier)
            .order_by(BacktestResult.league_tier)
        )).all()

    print(f"  {'Tier':<6} {'Bets':>5} {'WR%':>6} {'P&L (scaled)':>16} {'ROI':>7}")
    print(f"  {'─'*6} {'─'*5} {'─'*6} {'─'*16} {'─'*7}")
    for r in tier_rows:
        tier_l, tot, w, pnl_raw = r.league_tier, r.total, (r.wins or 0), (r.pnl or 0.0)
        wr_pct  = w / tot * 100 if tot else 0.0
        pnl_10k = pnl_raw   # already at 10k stake
        stake_  = tot * FLAT_STAKE
        roi_    = pnl_10k / stake_ * 100 if stake_ else 0.0
        print(f"  Tier {tier_l or '?':<2} {tot:>5} {wr_pct:>5.1f}% {pnl_10k:>+16,.2f} {roi_:>+6.1f}%")

    # ── Top 10 leagues by bets ─────────────────────────────────────────────────
    print(f"\n{'─'*72}")
    print("  TOP LEAGUES (by volume)")
    print(f"{'─'*72}")

    async with AsyncSessionLocal() as db4:
        from sqlalchemy import select, func as sqlfunc
        from app.models.backtest import BacktestResult

        league_rows = (await db4.execute(
            select(
                BacktestResult.league_name,
                BacktestResult.league_tier,
                sqlfunc.count().label("total"),
                sqlfunc.sum(BacktestResult.bet_result).label("wins"),
                sqlfunc.sum(BacktestResult.profit_loss).label("pnl"),
            )
            .where(BacktestResult.fixture_date >= DATE_FROM)
            .where(BacktestResult.fixture_date <= DATE_TO)
            .group_by(BacktestResult.league_name)
            .having(sqlfunc.count() >= 3)
            .order_by(sqlfunc.count().desc())
        )).all()

    print(f"  {'League':<36} {'T':>2} {'Bets':>4} {'WR%':>6} {'P&L (scaled)':>16} {'ROI':>7}")
    print(f"  {'─'*36} {'─'*2} {'─'*4} {'─'*6} {'─'*16} {'─'*7}")
    for r in league_rows[:20]:
        ln, lt, tot, w, pnl_raw = r.league_name, r.league_tier, r.total, (r.wins or 0), (r.pnl or 0.0)
        wr_pct  = w / tot * 100 if tot else 0.0
        pnl_10k = pnl_raw   # already at 10k stake
        stake_  = tot * FLAT_STAKE
        roi_    = pnl_10k / stake_ * 100 if stake_ else 0.0
        print(f"  {(ln or 'Unknown')[:36]:<36} {lt or '?':>2} {tot:>4} {wr_pct:>5.1f}% {pnl_10k:>+16,.2f} {roi_:>+6.1f}%")

    # ── Worst 10 leagues by P&L ────────────────────────────────────────────────
    print(f"\n{'─'*72}")
    print("  WORST LEAGUES (by P&L, min 3 bets)")
    print(f"{'─'*72}")

    async with AsyncSessionLocal() as db5:
        from sqlalchemy import select, func as sqlfunc
        from app.models.backtest import BacktestResult

        worst_rows = (await db5.execute(
            select(
                BacktestResult.league_name,
                BacktestResult.league_tier,
                sqlfunc.count().label("total"),
                sqlfunc.sum(BacktestResult.bet_result).label("wins"),
                sqlfunc.sum(BacktestResult.profit_loss).label("pnl"),
            )
            .where(BacktestResult.fixture_date >= DATE_FROM)
            .where(BacktestResult.fixture_date <= DATE_TO)
            .group_by(BacktestResult.league_name)
            .having(sqlfunc.count() >= 3)
            .order_by(sqlfunc.sum(BacktestResult.profit_loss).asc())
        )).all()

    for r in worst_rows[:10]:
        ln, lt, tot, w, pnl_raw = r.league_name, r.league_tier, r.total, (r.wins or 0), (r.pnl or 0.0)
        wr_pct  = w / tot * 100 if tot else 0.0
        pnl_10k = pnl_raw   # already at 10k stake
        stake_  = tot * FLAT_STAKE
        roi_    = pnl_10k / stake_ * 100 if stake_ else 0.0
        print(f"  {(ln or 'Unknown')[:36]:<36} {lt or '?':>2} {tot:>4} {wr_pct:>5.1f}% {pnl_10k:>+16,.2f} {roi_:>+6.1f}%")

    print(f"\n{'═'*72}")
    print("  Backtest complete.")
    print(f"{'═'*72}\n")


asyncio.run(run())

"""
resync_pending.py — Force-sync all dates with pending bets, then settle.
"""
import asyncio, os, sys
from pathlib import Path
from datetime import date

BACKEND = Path(r'D:\WebApps\titibet\backend')
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select, distinct, text
from app.core.database import AsyncSessionLocal
import app.models.user  # noqa: F401 — ensures users table is registered for FK resolution
from app.models import TrackedBet, Fixture
from app.services import ingestion
from app.services.settlement import settle_bets_for_date, FINAL_STATUSES


async def main():
    async with AsyncSessionLocal() as db:

        # ── 1. Find dates with pending bets ─────────────────────────────────
        result = await db.execute(
            select(distinct(Fixture.event_date))
            .join(TrackedBet, TrackedBet.fixture_id == Fixture.id)
            .where(TrackedBet.result_status == "Pending")
            .where(Fixture.event_date.isnot(None))
            .order_by(Fixture.event_date)
        )
        pending_dates = [r[0] for r in result.all()]
        if isinstance(pending_dates[0] if pending_dates else None, str):
            pending_dates = [date.fromisoformat(d) for d in pending_dates]

        print(f"Dates with pending bets: {len(pending_dates)}")
        for d in pending_dates:
            print(f"  {d}")

        if not pending_dates:
            print("Nothing to sync.")
            return

        # ── 2. Sync each date (force=True bypasses cooldown + cache) ────────
        print("\nSyncing fixture scores...")
        print("=" * 55)
        for d in pending_dates:
            today = date.today()
            if d > today:
                print(f"  {d}  SKIP (future date)")
                continue
            try:
                run = await ingestion.sync_date(db, d, force=True)
                print(f"  {d}  {run.status:<10}  {run.fixtures_pulled} fixtures pulled")
            except Exception as e:
                print(f"  {d}  ERROR: {e}")

        # ── 3. Settle all pending bets ───────────────────────────────────────
        print("\nSettling pending bets...")
        print("=" * 55)
        result = await settle_bets_for_date(db, None)

        settled     = result.get("settled", 0) if isinstance(result, dict) else result
        not_final   = result.get("skip_not_final", 0) if isinstance(result, dict) else 0
        no_score    = result.get("skip_no_score", 0) if isinstance(result, dict) else 0

        print(f"  Settled          : {settled}")
        print(f"  Still not final  : {not_final}  (today's fixtures / live)")
        print(f"  No score data    : {no_score}")

        # ── 4. Show remaining pending ────────────────────────────────────────
        remaining = (await db.execute(text("""
            SELECT tb.result_status, COUNT(*) as n,
                   f.event_date, f.status as fixture_status
            FROM tracked_bets tb
            JOIN fixtures f ON f.id = tb.fixture_id
            WHERE tb.result_status = 'Pending'
            GROUP BY f.event_date, f.status
            ORDER BY f.event_date
        """))).all()

        if remaining:
            print(f"\n  Still pending ({sum(r[1] for r in remaining)} bets):")
            for r in remaining:
                print(f"    {r[2]}  fixture_status={r[3]}  bets={r[1]}")
        else:
            print("\n  All pending bets settled.")

        # ── 5. Final tracker summary ─────────────────────────────────────────
        print("\nTracker summary:")
        summary = (await db.execute(text("""
            SELECT result_status, COUNT(*) as n,
                   ROUND(SUM(profit_loss), 0) as pnl
            FROM tracked_bets
            GROUP BY result_status ORDER BY n DESC
        """))).all()
        for r in summary:
            pnl = r[2] or 0
            print(f"  {r[0] or 'Unknown':<12}  {r[1]:>4} bets  P&L {pnl:>+12,.0f}")

        settled_rows = [(r[1], r[2] or 0) for r in summary if r[0] in ("Won", "Lost")]
        if settled_rows:
            total_n   = sum(r[0] for r in settled_rows)
            total_pnl = sum(r[1] for r in settled_rows)
            wins      = next((r[1] for r in summary if r[0] == "Won"), 0)
            wr        = wins / total_n * 100 if total_n else 0
            roi       = total_pnl / (total_n * 10_000) * 100 if total_n else 0
            print(f"\n  Hit rate (settled) : {wr:.1f}%")
            print(f"  Net P&L (settled)  : {total_pnl:+,.0f}")
            print(f"  ROI @ 10k flat     : {roi:+.1f}%")


asyncio.run(main())

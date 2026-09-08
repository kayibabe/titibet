"""
prod_catchup_sep2026.py — Full sync + track + settle for September 1–8, 2026.

Run on Fly.io:
    flyctl ssh console -a titibet
    python /app/prod_catchup_sep2026.py
"""
import asyncio
import logging
import sys
from datetime import date, timedelta, datetime

sys.path.insert(0, '/app')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
)
logger = logging.getLogger("sep_catchup")

START_DATE = date(2026, 9, 1)
END_DATE = date(2026, 9, 8)   # inclusive; script caps at today if run earlier


async def main():
    from app.core.database import AsyncSessionLocal, init_db
    from app.services import ingestion
    from app.services.signal_engine import compute_signals_for_date
    from app.services.auto_tracker import auto_track_date
    from app.services.settlement import settle_bets_for_date
    from sqlalchemy import text

    await init_db()

    today = date.today()
    end = min(END_DATE, today)

    async with AsyncSessionLocal() as db:
        # ── Step 1: Ingest, compute signals, auto-track for each date ─────────
        current = START_DATE
        total_tracked = 0

        while current <= end:
            logger.info("════════ %s ════════", current)

            # Check whether market snapshots already exist for this date.
            snap_count = (await db.execute(text(
                "SELECT COUNT(*) FROM market_snapshots ms "
                "JOIN fixtures f ON f.id = ms.fixture_id "
                "WHERE f.event_date = :d"
            ), {"d": str(current)})).scalar() or 0

            # --- Ingestion ---
            try:
                run = await ingestion.sync_date(db, current, force=True)
                logger.info("  Sync: status=%s  fixtures=%s", run.status, run.fixtures_pulled)
                if run.status != "success":
                    logger.warning("  Ingestion non-success for %s (%s) — continuing", current, run.status)
            except Exception as exc:
                logger.error("  Ingestion error for %s: %s", current, exc)

            # --- Signal computation ---
            # Re-run whether or not we just ingested — covers re-compute from snapshots.
            try:
                n_sig = await compute_signals_for_date(db, current)
                logger.info("  Signals computed: %d", n_sig or 0)
            except Exception as exc:
                logger.error("  Signal compute error for %s: %s", current, exc)

            # --- Auto-track ---
            try:
                n = await auto_track_date(db, current)
                await db.commit()
                logger.info("  Bets tracked: %d", n)
                total_tracked += n
            except Exception as exc:
                logger.error("  Auto-track error for %s: %s", current, exc)
                await db.rollback()

            current += timedelta(days=1)

        # ── Step 2: Force-sync any past dates with pending bets + stale fixtures ──
        logger.info("══ Checking stale pending bets ══")
        pending_r = await db.execute(text("""
            SELECT DISTINCT date(event_date) FROM tracked_bets
            WHERE result_status = 'Pending'
              AND event_date IS NOT NULL
              AND event_date < :today
        """), {"today": str(today)})
        pending_dates = [row[0] for row in pending_r.fetchall()]
        logger.info("Dates with pending bets: %s", pending_dates)

        for d_str in pending_dates:
            d = datetime.strptime(str(d_str), "%Y-%m-%d").date() if isinstance(d_str, str) else d_str
            stale = (await db.execute(text("""
                SELECT COUNT(*) FROM fixtures
                WHERE event_date = :d
                  AND status NOT IN ('FT','AET','PEN','CANC','ABD','AWD','WO','TBD','PST','INT','SUSP')
            """), {"d": str(d)})).scalar() or 0

            if stale:
                logger.info("  Date %s has %d stale fixture(s) — re-syncing", d, stale)
                try:
                    run = await ingestion.sync_date(db, d, force=True)
                    logger.info("  Sync %s: status=%s  fixtures=%s", d, run.status, run.fixtures_pulled)
                except Exception as exc:
                    logger.error("  Sync error for %s: %s", d, exc)

        # ── Step 3: Settle all pending bets ───────────────────────────────────
        logger.info("══ Running settlement ══")
        settle_info = await settle_bets_for_date(db, None)
        settled = settle_info.get("settled", 0)
        logger.info("Settled: %d bets", settled)

        # ── Final summary ─────────────────────────────────────────────────────
        summary = await db.execute(text(
            "SELECT result_status, COUNT(*) FROM tracked_bets "
            "GROUP BY result_status ORDER BY result_status"
        ))
        logger.info("══ BET SUMMARY ══")
        for row in summary.fetchall():
            logger.info("  %-10s: %d", row[0], row[1])

        sep_r = await db.execute(text("""
            SELECT date(event_date) as d, result_status, COUNT(*) as n
            FROM tracked_bets
            WHERE event_date >= '2026-09-01' AND event_date <= '2026-09-08'
              AND user_id IS NULL AND source_rule_key != 'system_acca'
            GROUP BY d, result_status ORDER BY d
        """))
        logger.info("══ SEPTEMBER BREAKDOWN ══")
        for row in sep_r.fetchall():
            logger.info("  %s  %-10s: %d", row[0], row[1], row[2])

        logger.info("DONE — bets tracked this run: %d | bets settled this run: %d",
                    total_tracked, settled)


asyncio.run(main())

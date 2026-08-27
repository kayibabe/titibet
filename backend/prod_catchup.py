"""
prod_catchup.py — Run on Fly.io via: python /app/prod_catchup.py
Detects missing tracked bets and unsettled matches, re-syncs, tracks, and settles.
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
logger = logging.getLogger("prod_catchup")


async def main():
    from app.core.database import AsyncSessionLocal, init_db
    from sqlalchemy import text

    await init_db()

    async with AsyncSessionLocal() as db:
        today = date.today()
        cutoff = today - timedelta(days=60)
        logger.info("Today=%s  cutoff=%s", today, cutoff)

        # -- Step 1: Find signal dates with no system tracked bets --
        sig_r = await db.execute(text("""
            SELECT DISTINCT date(f.kickoff_at) as d
            FROM signals s
            JOIN fixtures f ON f.id = s.fixture_id
            WHERE s.is_candidate = 0
              AND date(f.kickoff_at) >= :cutoff
              AND date(f.kickoff_at) < :today
        """), {"cutoff": str(cutoff), "today": str(today)})
        sig_dates = {row[0] for row in sig_r.fetchall()}

        tracked_r = await db.execute(text("""
            SELECT DISTINCT event_date FROM tracked_bets
            WHERE event_date IS NOT NULL
              AND event_date >= :cutoff
              AND event_date < :today
              AND user_id IS NULL
              AND source_rule_key != 'system_acca'
        """), {"cutoff": str(cutoff), "today": str(today)})
        tracked_dates = {str(row[0]) for row in tracked_r.fetchall()}

        gap_dates = sorted(d for d in sig_dates if d not in tracked_dates)
        logger.info("Gap dates (signals but no bets): %s", gap_dates)

        from app.services import ingestion
        from app.services.auto_tracker import auto_track_date

        total_tracked = 0
        for d_str in gap_dates:
            d = datetime.strptime(d_str, "%Y-%m-%d").date() if isinstance(d_str, str) else d_str
            logger.info("=== Syncing + tracking %s ===", d)
            try:
                run = await ingestion.sync_date(db, d, force=True)
                logger.info("  Sync: status=%s  fixtures=%s", run.status, run.fixtures_pulled)
            except Exception as e:
                logger.error("  Sync failed: %s", e)
                continue
            try:
                n = await auto_track_date(db, d)
                await db.commit()
                logger.info("  Tracked: %d bets created", n)
                total_tracked += n
            except Exception as e:
                logger.error("  Tracking failed: %s", e)
                await db.rollback()

        # -- Step 2: Force-sync any dates with pending bets + stale fixture statuses --
        pending_r = await db.execute(text("""
            SELECT DISTINCT event_date FROM tracked_bets
            WHERE result_status = 'Pending'
              AND event_date IS NOT NULL
              AND event_date < :today
        """), {"today": str(today)})
        pending_dates = [row[0] for row in pending_r.fetchall()]

        for d in pending_dates:
            d = datetime.strptime(str(d), "%Y-%m-%d").date() if isinstance(d, str) else d
            stale_r = await db.execute(text("""
                SELECT COUNT(*) FROM fixtures
                WHERE event_date = :d
                  AND status NOT IN ('FT','AET','PEN','CANC','ABD','AWD','WO','TBD','PST','INT','SUSP')
            """), {"d": str(d)})
            stale = stale_r.scalar() or 0
            if stale > 0:
                logger.info("Date %s has %d stale fixtures — force-syncing", d, stale)
                try:
                    run = await ingestion.sync_date(db, d, force=True)
                    logger.info("  Sync: status=%s  fixtures=%s", run.status, run.fixtures_pulled)
                except Exception as e:
                    logger.error("  Sync failed for %s: %s", d, e)

        # -- Step 3: Settle all pending bets --
        from app.services.settlement import settle_bets_for_date
        logger.info("=== Running settlement ===")
        settle_info = await settle_bets_for_date(db, None)
        settled = settle_info.get("settled", 0)
        logger.info("Settled: %d bets", settled)

        # -- Final summary --
        summary_r = await db.execute(text(
            "SELECT result_status, COUNT(*) FROM tracked_bets GROUP BY result_status ORDER BY result_status"
        ))
        logger.info("=== FINAL BET SUMMARY ===")
        for row in summary_r.fetchall():
            logger.info("  %-10s: %d", row[0], row[1])

        logger.info("Done. New bets tracked: %d  |  Bets settled this run: %d", total_tracked, settled)


asyncio.run(main())

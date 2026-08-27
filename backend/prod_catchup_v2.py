"""
prod_catchup_v2.py — Targeted track-and-settle for July/August gap dates.
Skips June (contaminated odds period; API won't return historical data anyway).
Run on Fly.io: python /app/prod_catchup_v2.py
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
        # Only look at July 2026 onwards — June was contaminated odds period
        cutoff = date(2026, 7, 1)
        logger.info("Today=%s  scanning from %s", today, cutoff)

        # --- Step 1: Find gap dates (signals but no system tracked bets) ---
        sig_r = await db.execute(text("""
            SELECT DISTINCT date(f.kickoff_at) as d
            FROM signals s
            JOIN fixtures f ON f.id = s.fixture_id
            WHERE s.is_candidate = 0
              AND date(f.kickoff_at) >= :cutoff
              AND date(f.kickoff_at) < :today
            ORDER BY d
        """), {"cutoff": str(cutoff), "today": str(today)})
        sig_dates = [row[0] for row in sig_r.fetchall()]
        logger.info("Signal dates in window: %s", sig_dates)

        tracked_r = await db.execute(text("""
            SELECT DISTINCT date(event_date) FROM tracked_bets
            WHERE event_date IS NOT NULL
              AND event_date >= :cutoff
              AND event_date < :today
              AND user_id IS NULL
              AND source_rule_key != 'system_acca'
        """), {"cutoff": str(cutoff), "today": str(today)})
        tracked_dates = {row[0] for row in tracked_r.fetchall()}
        logger.info("Dates already tracked: %s", sorted(tracked_dates))

        gap_dates = [d for d in sig_dates if d not in tracked_dates]
        logger.info("Gap dates to process: %s", gap_dates)

        from app.services import ingestion
        from app.services.auto_tracker import auto_track_date

        total_tracked = 0
        for d_str in gap_dates:
            d = datetime.strptime(d_str, "%Y-%m-%d").date() if isinstance(d_str, str) else d_str
            logger.info("=== Processing %s ===", d)

            # Force-sync to get current fixture scores
            try:
                run = await ingestion.sync_date(db, d, force=True)
                logger.info("  Sync: status=%s  fixtures=%s", run.status, run.fixtures_pulled)
                if run.status != "success":
                    logger.warning("  Sync failed for %s — skipping tracking", d)
                    continue
            except Exception as e:
                logger.error("  Sync error for %s: %s", d, e)
                continue

            # Check how many fixtures are now final
            final_r = await db.execute(text("""
                SELECT COUNT(*) FROM fixtures
                WHERE event_date = :d AND status IN ('FT','AET','PEN')
            """), {"d": str(d)})
            final_count = final_r.scalar() or 0
            logger.info("  Fixtures with final status: %d", final_count)

            # Auto-track qualifying signals
            try:
                n = await auto_track_date(db, d)
                await db.commit()
                logger.info("  Tracked: %d bets created", n)
                total_tracked += n
            except Exception as e:
                logger.error("  Tracking failed for %s: %s", d, e)
                await db.rollback()

        # --- Step 2: Force-sync any dates with pending bets + stale fixtures ---
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
                    logger.info("  Sync %s: status=%s  fixtures=%s", d, run.status, run.fixtures_pulled)
                except Exception as e:
                    logger.error("  Sync error for %s: %s", d, e)

        # --- Step 3: Settle all pending bets ---
        from app.services.settlement import settle_bets_for_date
        logger.info("=== Running settlement ===")
        settle_info = await settle_bets_for_date(db, None)
        settled = settle_info.get("settled", 0)
        logger.info("Settled: %d bets", settled)

        # --- Final summary ---
        summary_r = await db.execute(text(
            "SELECT result_status, COUNT(*) FROM tracked_bets GROUP BY result_status ORDER BY result_status"
        ))
        logger.info("=== BET SUMMARY ===")
        for row in summary_r.fetchall():
            logger.info("  %-10s: %d", row[0], row[1])

        logger.info("DONE — new bets tracked: %d | bets settled this run: %d", total_tracked, settled)


asyncio.run(main())

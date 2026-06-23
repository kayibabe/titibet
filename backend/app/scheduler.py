"""
scheduler.py — APScheduler jobs for automatic fixture + odds sync.
Runs sync_and_compute() at configured times (default: 06:00, 10:00, 14:00, 18:00, 23:30 UTC).

Startup behaviour
-----------------
On startup:
  1. Catch-up sync: re-pulls any past dates that have pending bets with non-final
     fixture statuses in the DB. This handles the case where the backend was offline
     when yesterday's matches finished, leaving fixtures stuck at "2H"/"1H" in DB.
  2. Today sync: full ingestion + signal compute + settlement for today.

Dev override
------------
Set SKIP_STARTUP_SYNC=true in backend/.env to suppress the startup sync entirely.
This costs zero API calls on hot-reload restarts during development.
The scheduled jobs still run normally — only the one-shot startup pull is skipped.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, distinct

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models import TrackedBet, Fixture
from app.services import ingestion
from app.services.signal_engine import compute_signals_for_date
from app.services.auto_tracker import auto_track_date
from app.services.settlement import settle_bets_for_date, FINAL_STATUSES
from app.services.loss_analysis_agent import run_loss_analysis_pipeline
from app.services.strategy_pipeline import run_strategy_pipeline, check_suppression_reactivations
from app.services.league_watch_guard import run_league_watch_guard
from app.services.telegram import (
    push_kickoff_alerts,
    check_and_push_pending_results,
    push_signal_digest,
    push_morning_digest,
)

logger = logging.getLogger("titibet.scheduler")
settings = get_settings()

_scheduler: AsyncIOScheduler | None = None


async def catchup_past_dates() -> int:
    """
    Re-sync any past dates that have Pending bets whose fixture status is not yet
    final in the DB. Returns the number of bets settled.

    This is the fix for the common scenario where the backend was offline (or a
    scheduled sync failed) when yesterday's matches finished, leaving fixture rows
    stuck at "2H" / "HT" / "1H" in the database.  Without this, settlement never
    triggers because _fixture_is_final() returns False for those stale statuses.

    Uses force=True on ingestion so the 2-hour cooldown and all-final cache guards
    are bypassed — we explicitly need fresh data for these past-date fixtures.
    """
    async with AsyncSessionLocal() as db:
        today = date.today()

        # Find distinct past dates that still have Pending bets.
        pending_dates_result = await db.execute(
            select(distinct(TrackedBet.event_date))
            .where(
                TrackedBet.result_status == "Pending",
                TrackedBet.event_date < today,
                TrackedBet.event_date.isnot(None),
            )
        )
        past_dates = [row[0] for row in pending_dates_result.all() if row[0]]

        if not past_dates:
            return 0

        for d in past_dates:
            # Only re-sync if at least one fixture for that date is NOT yet final in DB.
            stale_result = await db.execute(
                select(Fixture.id)
                .where(
                    Fixture.event_date == d,
                    Fixture.status.notin_(list(FINAL_STATUSES)),
                )
                .limit(1)
            )
            has_stale = stale_result.scalar() is not None

            if not has_stale:
                # All fixtures already final in DB — skip re-sync, just settle.
                logger.info(
                    "Catch-up: %s fixtures all final in DB — skipping API, running settlement.", d
                )
                continue

            logger.info(
                "Catch-up: %s has pending bets with non-final fixtures — force-syncing.", d
            )
            try:
                run = await ingestion.sync_date(db, d, force=True)
                logger.info(
                    "Catch-up sync %s: status=%s fixtures=%s",
                    d, run.status, run.fixtures_pulled,
                )
            except Exception as e:
                logger.error("Catch-up sync failed for %s: %s", d, e)

        # Settle all pending bets now that fixture statuses are refreshed.
        n_settled = (await settle_bets_for_date(db, None))["settled"]
        if n_settled:
            logger.info("Catch-up settlement: %d pending bet(s) settled.", n_settled)
            # Push results for any fully-settled date after catch-up.
            try:
                n_results = await check_and_push_pending_results(db)
                if n_results:
                    logger.info("Catch-up results report: pushed results for %d date(s)", n_results)
            except Exception:
                logger.exception("Catch-up results push failed — continuing normally")
            try:
                report = await run_loss_analysis_pipeline(db)
                logger.info(
                    "Catch-up loss analysis (A): %d bets analysed, %d proposals accepted",
                    report.bets_analysed, len(report.accepted_proposals),
                )
            except Exception:
                logger.exception("Catch-up loss analysis (A) failed — continuing normally")
            try:
                strategy_report = await run_strategy_pipeline(db)
                logger.info(
                    "Catch-up strategy pipeline (B): %d bets analysed, %d/%d proposals accepted",
                    strategy_report.bets_analysed,
                    strategy_report.proposals_accepted,
                    strategy_report.proposals_generated,
                )
            except Exception:
                logger.exception("Catch-up strategy pipeline (B) failed — continuing normally")
            try:
                reactivated_count = await check_suppression_reactivations(db)
                logger.info(
                    "Suppression reactivation check: %d market(s) reactivated",
                    reactivated_count,
                )
            except Exception:
                logger.exception("Suppression reactivation check failed — continuing normally")
            try:
                wg_statuses = await run_league_watch_guard(db)
                suppressed = [s for s in wg_statuses if s.action_taken == "suppressed"]
                recovered  = [s for s in wg_statuses if s.action_taken == "reactivated"]
                warned     = [s for s in wg_statuses if s.state == "WARNING"]
                logger.info(
                    "League watch guard: %d watched  %d suppressed  %d recovered  %d warnings",
                    len(wg_statuses), len(suppressed), len(recovered), len(warned),
                )
                for s in suppressed:
                    logger.warning("Watch guard suppressed: '%s'  ROI=%+.1f%%  bets=%d", s.keyword, s.roi_pct, s.total_bets)
                for s in recovered:
                    logger.info("Watch guard recovered: '%s'  ROI=%+.1f%%", s.keyword, s.roi_pct)
            except Exception:
                logger.exception("League watch guard failed — continuing normally")
        return n_settled


async def sync_and_compute(run_date: date | None = None) -> None:
    if run_date is None:
        run_date = date.today()
    async with AsyncSessionLocal() as db:
        try:
            logger.info("Scheduler: syncing %s", run_date)
            run = await ingestion.sync_date(db, run_date)
            if run.status == "success":
                count = await compute_signals_for_date(db, run_date)
                run.signals_computed = count
                await db.commit()
                # Auto-track all qualifying signals for this date as system picks.
                try:
                    n_tracked = await auto_track_date(db, run_date)
                    if n_tracked:
                        logger.info("Auto-tracker: %d new system bet(s) for %s", n_tracked, run_date)
                except Exception:
                    logger.exception("Auto-tracker failed for %s — continuing normally", run_date)
                # Settle every pending bet with a final fixture (any event_date), not only run_date.
                n_settled = (await settle_bets_for_date(db, None))["settled"]
                logger.info(
                    "Scheduler: %s done — %d fixtures, %d signals, %d bets settled",
                    run_date, run.fixtures_pulled, count, n_settled,
                )
                # Push results for any fully-settled date (today + last 2 days).
                try:
                    n_results = await check_and_push_pending_results(db)
                    if n_results:
                        logger.info("Results report: pushed results for %d date(s)", n_results)
                except Exception:
                    logger.exception("Telegram results push failed — continuing normally")
                # After settlement, run the self-learning loss analysis pipeline.
                # Analyses newly settled losses, detects patterns, proposes threshold changes.
                if n_settled > 0:
                    try:
                        report = await run_loss_analysis_pipeline(db)
                        logger.info(
                            "Loss analysis pipeline (A): %d bets analysed, %d proposals accepted",
                            report.bets_analysed,
                            len(report.accepted_proposals),
                        )
                    except Exception:
                        logger.exception("Loss analysis pipeline (A) failed — continuing normally")
                    try:
                        strategy_report = await run_strategy_pipeline(db)
                        logger.info(
                            "Strategy pipeline (B): %d bets analysed, %d/%d proposals accepted",
                            strategy_report.bets_analysed,
                            strategy_report.proposals_accepted,
                            strategy_report.proposals_generated,
                        )
                    except Exception:
                        logger.exception("Strategy pipeline (B) failed — continuing normally")
                    try:
                        reactivated_count = await check_suppression_reactivations(db)
                        logger.info(
                            "Suppression reactivation check: %d market(s) reactivated",
                            reactivated_count,
                        )
                    except Exception:
                        logger.exception("Suppression reactivation check failed — continuing normally")
                    try:
                        wg_statuses = await run_league_watch_guard(db)
                        suppressed = [s for s in wg_statuses if s.action_taken == "suppressed"]
                        recovered  = [s for s in wg_statuses if s.action_taken == "reactivated"]
                        warned     = [s for s in wg_statuses if s.state == "WARNING"]
                        logger.info(
                            "League watch guard: %d watched  %d suppressed  %d recovered  %d warnings",
                            len(wg_statuses), len(suppressed), len(recovered), len(warned),
                        )
                        for s in suppressed:
                            logger.warning("Watch guard suppressed: '%s'  ROI=%+.1f%%  bets=%d", s.keyword, s.roi_pct, s.total_bets)
                        for s in recovered:
                            logger.info("Watch guard recovered: '%s'  ROI=%+.1f%%", s.keyword, s.roi_pct)
                    except Exception:
                        logger.exception("League watch guard failed — continuing normally")
        except Exception:
            logger.exception("Scheduler error for %s", run_date)
            raise


async def startup_sync() -> None:
    """
    On startup:
      1. Catch-up: re-sync past dates with pending bets that have stale fixture statuses.
         This is the primary fix for bets stuck as Pending after the backend was offline.
      2. Today: full ingestion + signals + settlement.

    Skipped entirely when SKIP_STARTUP_SYNC=true (for local dev — avoids quota burn
    on hot-reload restarts). When skipped, catch-up settlement still runs from DB state.
    """
    if os.getenv("SKIP_STARTUP_SYNC", "").lower() in ("1", "true", "yes"):
        logger.info(
            "SKIP_STARTUP_SYNC is set — skipping startup sync. "
            "Running catch-up settlement from existing DB scores."
        )
        # Even when skipping the full sync, run catch-up so stale past-date fixtures
        # are refreshed and pending bets can settle.
        await catchup_past_dates()
        return

    # Step 1: housekeeping — purge stale snapshots + deactivate redundant proposals.
    await _cleanup_old_snapshots()

    # Step 2: resolve any pending bets from past dates before syncing today.
    await catchup_past_dates()

    # Step 3: sync today normally.
    logger.info("Startup sync: pulling today (%s)", date.today())
    await sync_and_compute(date.today())


async def _cleanup_old_snapshots() -> None:
    """
    Weekly housekeeping — deletes market_snapshots rows for fixtures older than
    30 days.  These odds are no longer needed for signal computation or settlement
    and are the primary driver of DB growth (1.15 M rows → ~300 MB).

    Also deactivates learning proposals whose change_type is no longer consumed
    by the current signal engine (tier_suppression, quality_threshold) or whose
    target is already covered by a hard-coded ban in DISABLED_MARKETS /
    DISABLED_LEAGUES.
    """
    from sqlalchemy import text
    from app.core.config import DISABLED_MARKETS, DISABLED_LEAGUES

    async with AsyncSessionLocal() as db:
        try:
            # 1. Purge stale market snapshots
            result = await db.execute(text("""
                DELETE FROM market_snapshots
                WHERE fixture_id IN (
                    SELECT id FROM fixtures WHERE event_date < date('now', '-30 days')
                )
            """))
            deleted_snaps = result.rowcount
            await db.commit()
            if deleted_snaps:
                logger.info("Cleanup: removed %d stale market_snapshots rows.", deleted_snaps)

            # 2. Deactivate stale learning proposals
            #    — change types not consumed by current code
            unused_types = ("tier_suppression", "quality_threshold")
            r1 = await db.execute(text("""
                UPDATE learning_proposals SET is_active=0
                WHERE is_active=1 AND change_type IN ('tier_suppression','quality_threshold')
            """))
            #    — market_suppression whose target is already in DISABLED_MARKETS
            disabled_mkt_list = ", ".join(f"'{m}'" for m in DISABLED_MARKETS)
            r2 = await db.execute(text(f"""
                UPDATE learning_proposals SET is_active=0
                WHERE is_active=1
                  AND change_type='market_suppression'
                  AND target IN ({disabled_mkt_list})
            """))
            #    — league_suppression whose target is already in DISABLED_LEAGUES
            disabled_lg_list = ", ".join(f"'{lg}'" for lg in DISABLED_LEAGUES)
            r3 = await db.execute(text(f"""
                UPDATE learning_proposals SET is_active=0
                WHERE is_active=1
                  AND change_type='league_suppression'
                  AND lower(trim(target)) IN ({disabled_lg_list})
            """))
            #    — market_odds_ceiling proposals referencing only disabled markets
            r4 = await db.execute(text("""
                UPDATE learning_proposals SET is_active=0
                WHERE is_active=1
                  AND change_type='market_odds_ceiling'
                  AND id IN (25, 28, 31)
            """))
            deactivated = r1.rowcount + r2.rowcount + r3.rowcount + r4.rowcount
            await db.commit()
            if deactivated:
                logger.info("Cleanup: deactivated %d stale learning proposals.", deactivated)

        except Exception:
            logger.exception("Cleanup job failed — continuing normally")


async def _kickoff_alert_job() -> None:
    """Scheduler wrapper — sends pre-kickoff Telegram alerts every 30 min."""
    async with AsyncSessionLocal() as db:
        try:
            n = await push_kickoff_alerts(db)
            if n:
                logger.info("Kickoff alert job: pushed to %d channel(s)", n)
        except Exception:
            logger.exception("Kickoff alert job failed — continuing normally")


async def _morning_digest_job() -> None:
    """
    Morning digest — runs 07:30 UTC (09:30 CAT).

    Broadcasts today's ranked signal picks to all configured Telegram channels
    so subscribers see the day's full list at wake-up time. Runs 90 min after
    the 06:00 UTC sync to guarantee signals are computed.
    No-op when Telegram is not configured.
    """
    async with AsyncSessionLocal() as db:
        try:
            n_sent = await push_morning_digest(db)
            logger.info("Morning digest: broadcast to %d channel(s)", n_sent)
        except Exception:
            logger.exception("Morning digest push failed")


async def _nightly_digest_job() -> None:
    """
    Evening digest — runs 18:30 UTC (20:30 CAT).

    1. Pre-sync TOMORROW (UTC) so matches that kick off after midnight Malawi time
       — which fall on the next UTC date — have computed signals available.
    2. Broadcast the 'tonight + overnight' digest to all configured Telegram
       channels so subscribers can bet on after-midnight fixtures before bed.

    Best-effort: a failed pre-sync still sends the digest from existing data.
    No-op when Telegram is not configured.
    """
    async with AsyncSessionLocal() as db:
        tomorrow = date.today() + timedelta(days=1)
        try:
            run = await ingestion.sync_date(db, tomorrow)
            if run.status == "success":
                n_sig = await compute_signals_for_date(db, tomorrow)
                await db.commit()
                logger.info(
                    "Nightly digest pre-sync: %s — %d fixtures, %d signals",
                    tomorrow, run.fixtures_pulled, n_sig,
                )
            else:
                logger.warning("Nightly digest pre-sync: %s sync status=%s", tomorrow, run.status)
        except Exception:
            logger.exception("Nightly digest pre-sync failed — sending digest from existing data")
        try:
            n_sent = await push_signal_digest(db)
            logger.info("Nightly digest: broadcast to %d channel(s)", n_sent)
        except Exception:
            logger.exception("Nightly digest push failed")


async def _nightly_results_job() -> None:
    """
    02:00 UTC nightly sweep — push results for any date in the last 3 days
    that is fully settled but hasn't been reported to Telegram yet.
    Catches late finishers that weren't picked up by the sync-cycle checks.
    """
    async with AsyncSessionLocal() as db:
        try:
            n = await check_and_push_pending_results(db)
            if n:
                logger.info("Nightly results job: pushed results for %d date(s)", n)
        except Exception:
            logger.exception("Nightly results job failed — continuing normally")


async def _weekly_calibration_job() -> None:
    """
    Weekly calibration audit -- runs every Monday 07:00 UTC.
    Computes Brier skill score, ECE, and per-market calibration gaps over the
    last 90 days of settled bets.  Saves a snapshot for trend tracking and logs
    a WARNING for every market that fails the health threshold (skill < +0.05).
    """
    from app.services.calibration import compute_calibration_metrics, save_snapshot
    async with AsyncSessionLocal() as db:
        try:
            report = await compute_calibration_metrics(db, days=90)
            if report.signal_join_bets < 30:
                logger.info(
                    "Weekly calibration: too few settled bets (%d) — skipping snapshot",
                    report.signal_join_bets,
                )
                return
            await save_snapshot(db, report)
            for line in report.summary_lines():
                logger.info(line)
            for mkt in report.flagged_markets:
                logger.warning(
                    "Calibration FLAGGED: %s -- review and consider suppression or threshold change",
                    mkt,
                )
        except Exception:
            logger.exception("Weekly calibration job failed")


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
        for hour, minute in settings.sync_times_list:
            _scheduler.add_job(
                sync_and_compute,
                CronTrigger(hour=hour, minute=minute),
                id=f"sync-{hour:02d}{minute:02d}",
                replace_existing=True,
                misfire_grace_time=300,
            )
        # Pre-kickoff alert — runs every 60 min, 06:00–00:00 UTC (08:00–02:00 CAT).
        # Sends a compact Telegram message for High+Both signals kicking off
        # within 90 minutes that haven't already been alerted today.
        # No-op when TELEGRAM_BOT_TOKEN is not set.
        # After-midnight (CAT) matches are covered by the evening digest instead.
        _scheduler.add_job(
            _kickoff_alert_job,
            CronTrigger(hour="0,6-23", minute="0"),
            id="kickoff-alerts",
            replace_existing=True,
            misfire_grace_time=120,
        )
        # Morning digest — 07:30 UTC (09:30 CAT). Runs 90 min after the 06:00
        # sync so today's signals are computed. Pushes the day's ranked picks
        # so subscribers see them at wake-up time.
        _scheduler.add_job(
            _morning_digest_job,
            CronTrigger(hour=7, minute=30),
            id="morning-digest",
            replace_existing=True,
            misfire_grace_time=1800,
        )
        # Evening digest — 18:30 UTC (20:30 CAT). Pre-syncs tomorrow so
        # after-midnight (CAT) matches have signals, then broadcasts the
        # 'tonight + overnight' digest to General/Free/Pro channels.
        _scheduler.add_job(
            _nightly_digest_job,
            CronTrigger(hour=18, minute=30),
            id="nightly-digest",
            replace_existing=True,
            misfire_grace_time=1800,
        )
        # Nightly results sweep -- 02:00 UTC, catches late finishers from prior day.
        _scheduler.add_job(
            _nightly_results_job,
            CronTrigger(hour=2, minute=0),
            id="nightly-results",
            replace_existing=True,
            misfire_grace_time=600,
        )
        # Weekly calibration audit -- every Monday 07:00 UTC.
        # Computes Brier skill, ECE, per-market calibration gaps and flags any
        # market where skill < +0.05 or calibration gap > 7pp.
        # Saves a snapshot row for trend tracking; logs a summary with flagged markets.
        _scheduler.add_job(
            _weekly_calibration_job,
            CronTrigger(day_of_week="mon", hour=7, minute=0),
            id="weekly-calibration",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        # Weekly housekeeping -- every Wednesday 04:00 UTC.
        # Purges market_snapshots for fixtures >30 days old and deactivates
        # stale learning proposals whose targets are already hard-banned.
        _scheduler.add_job(
            _cleanup_old_snapshots,
            CronTrigger(day_of_week="wed", hour=4, minute=0),
            id="weekly-cleanup",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info(
            "Scheduler configured for %d sync times + kickoff alerts every 60 min "
            "(06:00-00:00 UTC) + morning digest 07:30 UTC + evening digest 18:30 UTC "
            "+ nightly results 02:00 UTC",
            len(settings.sync_times_list),
        )
    return _scheduler

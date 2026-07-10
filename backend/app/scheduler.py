"""
scheduler.py — APScheduler jobs for automatic fixture + odds sync.
Runs sync_and_compute() at configured times (default: 06:00, 14:00, 18:00, 23:30 UTC).

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
from app.services.auto_tracker import auto_track_date, auto_track_acca_signals
from app.services.settlement import settle_bets_for_date, FINAL_STATUSES
from app.services.loss_analysis_agent import run_loss_analysis_pipeline
from app.services.strategy_pipeline import run_strategy_pipeline, check_suppression_reactivations
from app.services.league_watch_guard import run_league_watch_guard
from app.services.telegram import (
    push_kickoff_alerts,
    check_and_push_pending_results,
    push_signal_digest,
    push_morning_digest,
    push_tomorrow_digest,
    push_ingestion_alert,
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


async def sync_and_compute(run_date: date | None = None, *, morning_extras: bool = False, evening_extras: bool = False) -> None:
    if run_date is None:
        run_date = date.today()
    async with AsyncSessionLocal() as db:
        try:
            logger.info("Scheduler: syncing %s", run_date)
            run = await ingestion.sync_date(db, run_date)
            if run.status != "success":
                try:
                    await push_ingestion_alert(db, run_date, run.status, getattr(run, "error_message", None))
                except Exception:
                    logger.exception("Ingestion alert failed — non-fatal")
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
                # ACCA tracking runs in morning_extras (first daily sync) via auto_track_acca_legs.
                # The signal-model fallback (auto_track_acca_signals) runs at the END of
                # morning_extras — after the advisor ACCA has had a chance to build tickets,
                # so its deference guard correctly skips it when the advisor succeeded.
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

                # ── Morning extras (first daily sync only) ────────────────────
                # Advisory cache + ACCA tracking + morning Telegram digest.
                # Runs after signals are confirmed computed — no timing gap needed.
                if morning_extras:
                    try:
                        from sqlalchemy import select, func as sqlfunc
                        from app.models.bet import TrackedBet as _TB
                        from app.services.advisor_service import get_advisor_insights, auto_track_acca_legs
                        result = await get_advisor_insights(db, run_date, current_user=None, force=True)
                        logger.info("Advisory cache: %d matches analysed for %s", result.get("matches_analysed", 0), run_date)
                        # Check if evening_extras already pre-tracked ACCA legs for today.
                        # If so, skip — the idempotency guard in auto_track_acca_legs
                        # would handle it anyway, but checking first avoids a redundant
                        # LLM call for acca candidates.
                        existing_acca = (await db.execute(
                            select(sqlfunc.count()).where(
                                _TB.source_rule_key == "acca_leg_system",
                                _TB.event_date == run_date,
                                _TB.user_id.is_(None),
                            )
                        )).scalar() or 0
                        if existing_acca:
                            logger.info("Morning extras: ACCA already pre-tracked for %s (%d rows) — skipping", run_date, existing_acca)
                        else:
                            tickets = result.get("accumulators") or []
                            if not tickets:
                                acca = result.get("accumulator", {})
                                if acca.get("legs") and not acca.get("error"):
                                    tickets = [acca]
                            if tickets:
                                n_tracked = await auto_track_acca_legs(db, tickets, run_date)
                                if n_tracked:
                                    logger.info("Advisory cache: auto-tracked %d acca rows for %s", n_tracked, run_date)

                        # ── Stale-odds guard ──────────────────────────────────────
                        # Re-check all pre-tracked ACCA legs against the latest
                        # MarketSnapshot odds.  Any leg whose odds have moved >10%
                        # since tracking is deleted so auto_track_acca_legs can
                        # re-insert it with the current price.
                        from app.models.odds import MarketSnapshot as _MS
                        from sqlalchemy import select as _sel2
                        acca_legs_rows = (await db.execute(
                            _sel2(_TB).where(
                                _TB.source_rule_key == "acca_leg_system",
                                _TB.event_date == run_date,
                                _TB.result_status == "Pending",
                                _TB.user_id.is_(None),
                            )
                        )).scalars().all()
                        voided_count = 0
                        for _leg in acca_legs_rows:
                            if not _leg.fixture_id or not _leg.odds:
                                continue
                            _snap = (await db.execute(
                                _sel2(_MS).where(
                                    _MS.fixture_id == _leg.fixture_id,
                                    _MS.market_type == _leg.market_type,
                                    _MS.selection_name == _leg.selection_name,
                                ).order_by(_MS.pulled_at.desc()).limit(1)
                            )).scalars().first()
                            if _snap and _snap.odds and abs(_snap.odds - _leg.odds) / _leg.odds > 0.10:
                                await db.delete(_leg)
                                voided_count += 1
                        if voided_count:
                            await db.commit()
                            logger.info(
                                "Stale-odds guard: voided %d ACCA leg(s) with >10%% odds movement "
                                "for %s — re-tracking",
                                voided_count, run_date,
                            )
                            retrack_tickets = result.get("accumulators") or []
                            if not retrack_tickets:
                                _rt_acca = result.get("accumulator", {})
                                if _rt_acca.get("legs") and not _rt_acca.get("error"):
                                    retrack_tickets = [_rt_acca]
                            if retrack_tickets:
                                n_retracked = await auto_track_acca_legs(db, retrack_tickets, run_date)
                                logger.info(
                                    "Stale-odds guard: re-tracked %d ACCA leg(s) for %s",
                                    n_retracked, run_date,
                                )
                    except Exception:
                        logger.exception("Advisory cache/ACCA failed — continuing normally")
                    # Signal-model ACCA fallback: builds a ticket from signal candidates
                    # when the advisor produced no accumulators. The deference guard inside
                    # auto_track_acca_signals skips silently when acca_advisory_system rows
                    # already exist for this date — so no duplicates are possible.
                    try:
                        n_signal_acca = await auto_track_acca_signals(db, run_date)
                        if n_signal_acca:
                            logger.info("Signal-model ACCA fallback: %d rows for %s", n_signal_acca, run_date)
                    except Exception:
                        logger.exception("Signal-model ACCA fallback failed — continuing normally")
                    try:
                        n_sent = await push_morning_digest(db)
                        if n_sent:
                            logger.info("Morning digest: sent to %d channel(s)", n_sent)
                    except Exception:
                        logger.exception("Morning digest failed — continuing normally")

                # ── Evening extras (19:00 UTC sync only) ─────────────────────
                # Tomorrow pre-sync at peak odds availability + advisory + ACCA + Telegram.
                if evening_extras:
                    tomorrow = run_date + timedelta(days=1)
                    try:
                        t_run = await ingestion.sync_date(db, tomorrow)
                        if t_run.status == "success":
                            n_sig = await compute_signals_for_date(db, tomorrow)
                            await db.commit()
                            logger.info("Tomorrow pre-sync: %s — %d fixtures, %d signals", tomorrow, t_run.fixtures_pulled, n_sig)
                        else:
                            logger.warning("Tomorrow pre-sync: %s status=%s", tomorrow, t_run.status)
                    except Exception:
                        logger.exception("Tomorrow pre-sync failed — skipping tomorrow advisory")
                    try:
                        from app.services.advisor_service import get_advisor_insights, auto_track_acca_legs
                        t_result = await get_advisor_insights(db, tomorrow, current_user=None, force=True)
                        logger.info("Tomorrow advisory cache: %d matches for %s", t_result.get("matches_analysed", 0), tomorrow)
                        # Track tomorrow's ACCA legs now (16:00 UTC / 18:00 CAT) so that
                        # games starting after midnight UTC (02:00+ CAT) are captured before
                        # the morning sync runs — those games would have already kicked off
                        # by 04:00 UTC.  The kickoff guard in auto_track_acca_legs filters
                        # any leg that starts within 30 min of write time.
                        t_tickets = t_result.get("accumulators") or []
                        if not t_tickets:
                            t_acca = t_result.get("accumulator", {})
                            if t_acca.get("legs") and not t_acca.get("error"):
                                t_tickets = [t_acca]
                        if t_tickets:
                            n_t_tracked = await auto_track_acca_legs(db, t_tickets, tomorrow)
                            if n_t_tracked:
                                logger.info("Evening extras: auto-tracked %d acca leg(s) for tomorrow %s", n_t_tracked, tomorrow)
                    except Exception:
                        logger.exception("Tomorrow advisory cache/ACCA failed — continuing normally")
                    try:
                        n_sent = await push_tomorrow_digest(db, tomorrow)
                        if n_sent:
                            logger.info("Tomorrow digest: sent to %d channel(s) for %s", n_sent, tomorrow)
                    except Exception:
                        logger.exception("Tomorrow digest push failed — continuing normally")

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
            #    — market_odds_ceiling proposals whose target market is already in DISABLED_MARKETS
            r4 = await db.execute(text(f"""
                UPDATE learning_proposals SET is_active=0
                WHERE is_active=1
                  AND change_type='market_odds_ceiling'
                  AND target IN ({disabled_mkt_list})
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



async def _daily_calibration_job() -> None:
    """
    Daily calibration audit — runs at 05:00 UTC every day.

    Computes Brier skill score, ECE, and per-market calibration gaps over the
    last 90 days of settled bets.  Saves a snapshot for trend tracking and logs
    a WARNING for every market that fails the health threshold (skill < +0.05).

    Also writes a market_suppression LearningProposal for any market whose
    brier_skill is below BRIER_SKILL_TARGET in BOTH the current snapshot AND the
    immediately prior saved snapshot (two consecutive windows), indicating
    sustained calibration failure rather than one-off noise.  Each proposal is
    validated through the in-process backtester before persisting.
    """
    from app.services.calibration import (
        compute_calibration_metrics, save_snapshot, load_recent_snapshots,
        BRIER_SKILL_TARGET,
    )
    from app.models.learning_proposal import LearningProposal
    from app.models.bet import TrackedBet
    from sqlalchemy import select as _select, text as _text
    async with AsyncSessionLocal() as db:
        try:
            report = await compute_calibration_metrics(db, days=90)
            if report.signal_join_bets < 30:
                logger.info(
                    "Calibration: too few settled bets (%d) — skipping snapshot",
                    report.signal_join_bets,
                )
                return
            await save_snapshot(db, report)
            for line in report.summary_lines():
                logger.info(line)
            for mkt in report.flagged_markets:
                logger.warning(
                    "Calibration FLAGGED: %s -- brier_skill below target or gap > 7pp",
                    mkt,
                )

            # ── Consecutive-failure suppression gate ─────────────────────────
            # Only propose suppression when the SAME market fails in two
            # consecutive snapshots — one failure could be statistical noise.
            prior_snapshots = await load_recent_snapshots(db, n=2)
            if len(prior_snapshots) < 2:
                return   # need at least 2 snapshots to compare

            # prior_snapshots[0] is the snapshot we just saved (most recent);
            # prior_snapshots[1] is the one before it.
            current_snap = prior_snapshots[0]
            previous_snap = prior_snapshots[1]

            import json as _json
            current_mkt_skill: dict[str, float] = {
                m["market"]: m["brier_skill"]
                for m in (_json.loads(current_snap.get("market_summary") or "[]") if isinstance(current_snap.get("market_summary"), str) else current_snap.get("market_summary") or [])
            }
            previous_mkt_skill: dict[str, float] = {
                m["market"]: m["brier_skill"]
                for m in (_json.loads(previous_snap.get("market_summary") or "[]") if isinstance(previous_snap.get("market_summary"), str) else previous_snap.get("market_summary") or [])
            }

            # Markets failing in BOTH consecutive windows
            both_failing = [
                mkt for mkt in current_mkt_skill
                if (
                    current_mkt_skill[mkt] < BRIER_SKILL_TARGET
                    and mkt in previous_mkt_skill
                    and previous_mkt_skill[mkt] < BRIER_SKILL_TARGET
                )
            ]

            if not both_failing:
                return

            # Load settled bets for backtester validation
            all_settled_result = await db.execute(
                _select(TrackedBet).where(TrackedBet.result_status.in_(["Won", "Lost"]))
            )
            all_settled = list(all_settled_result.scalars().all())

            for mkt in both_failing:
                # Skip if already hard-disabled — no point proposing suppression
                from app.core.config import DISABLED_MARKETS
                if mkt in DISABLED_MARKETS:
                    continue

                # Skip if already have an active market_suppression proposal
                existing = (await db.execute(
                    _select(LearningProposal).where(
                        LearningProposal.change_type == "market_suppression",
                        LearningProposal.target == mkt,
                        LearningProposal.is_active == True,  # noqa: E712
                    )
                )).scalars().first()
                if existing:
                    logger.info(
                        "Calibration: market_suppression for %s already active — skipping",
                        mkt,
                    )
                    continue

                # Simple backtest: does this market have negative ROI?
                market_bets = [b for b in all_settled if b.market_type == mkt]
                if len(market_bets) < 15:
                    logger.info(
                        "Calibration: %s has only %d bets — need 15 for suppression proposal",
                        mkt, len(market_bets),
                    )
                    continue

                total_stake = sum(b.stake for b in market_bets if b.stake)
                total_pl = sum(b.profit_loss for b in market_bets if b.profit_loss is not None)
                roi = (total_pl / total_stake) if total_stake > 0 else 0.0

                if roi >= -0.05:
                    logger.info(
                        "Calibration: %s ROI=%.1f%% not negative enough for suppression "
                        "(need < -5%%)",
                        mkt, roi * 100,
                    )
                    continue

                # Accepted — persist the proposal
                try:
                    new_proposal = LearningProposal(
                        change_type="market_suppression",
                        target=mkt,
                        proposed_value=1.0,
                        rationale=(
                            f"Calibration: brier_skill below {BRIER_SKILL_TARGET} in two "
                            f"consecutive 90-day windows. ROI={roi:.1%} on {len(market_bets)} bets."
                        ),
                        confidence="Medium",
                        backtest_note=(
                            f"Backtested: ROI={roi:.1%} over {len(market_bets)} settled bets — "
                            "negative ROI confirms suppression justified."
                        ),
                        is_active=True,
                    )
                    db.add(new_proposal)
                    await db.commit()
                    logger.warning(
                        "Calibration SUPPRESSION proposed: %s (brier_skill failed 2 windows, ROI=%.1f%%)",
                        mkt, roi * 100,
                    )
                except Exception as exc:
                    logger.error("Calibration: failed to persist proposal for %s: %s", mkt, exc)
                    await db.rollback()

        except Exception:
            logger.exception("Calibration job failed")


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
        # 04:00 UTC — morning extras only: today refresh + settlement +
        #   morning Telegram ("Confirmed" if evening already ran, else full digest).
        # 19:00 UTC — evening extras only: tomorrow ingestion + signals (peak odds
        #   availability) + advisory + ACCA + "Tomorrow's Picks" Telegram digest.
        # 23:00 UTC — settlement-only: core ingest + settle + learning pipelines.
        for i, (hour, minute) in enumerate(settings.sync_times_list):
            _scheduler.add_job(
                sync_and_compute,
                CronTrigger(hour=hour, minute=minute),
                id=f"sync-{hour:02d}{minute:02d}",
                replace_existing=True,
                misfire_grace_time=300,
                kwargs={"morning_extras": i == 0, "evening_extras": i == 1},
            )
        # Pre-kickoff alert — runs every 60 min, 04:00–22:00 UTC (06:00–00:00 CAT).
        # Sends a compact Telegram message for High+Both signals kicking off
        # within 90 minutes that haven't already been alerted today.
        # No-op when TELEGRAM_BOT_TOKEN is not set.
        _scheduler.add_job(
            _kickoff_alert_job,
            CronTrigger(hour="4-22", minute="0"),
            id="kickoff-alerts",
            replace_existing=True,
            misfire_grace_time=120,
        )
        # Daily calibration audit — every day 05:00 UTC.
        # Computes Brier skill, ECE, per-market calibration gaps over the last 90 days.
        # Daily re-runs are cheap (90-day window) and catch model drift faster than weekly.
        # Writes a market_suppression LearningProposal when the same market fails
        # in two consecutive snapshots AND shows negative ROI (two-window guard).
        _scheduler.add_job(
            _daily_calibration_job,
            CronTrigger(hour=5, minute=0),
            id="daily-calibration",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        # Weekly housekeeping -- every Wednesday 02:00 UTC.
        # Purges market_snapshots for fixtures >30 days old and deactivates
        # stale learning proposals whose targets are already hard-banned.
        _scheduler.add_job(
            _cleanup_old_snapshots,
            CronTrigger(day_of_week="wed", hour=2, minute=0),
            id="weekly-cleanup",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info(
            "Scheduler configured: %d syncs "
            "(04:00=morning-confirm, 19:00=evening-tomorrow, 23:00=settle-only) "
            "+ kickoff alerts 04:00-22:00 UTC + daily calibration + weekly cleanup",
            len(settings.sync_times_list),
        )
    return _scheduler

"""
ingestion.py - Fixture + odds pull and DB upsert service.

Flow: fetch_fixtures -> upsert Fixture rows -> snapshot cache check -> fetch_markets
      (only for fixtures that need it) -> upsert MarketSnapshot rows.
Logs each run in ingestion_runs. Signal computation is triggered separately by signal_engine.

Snapshot caching strategy
--------------------------
market_snapshots are the source of truth for both live signal computation and
historical backtesting. Without caching, every sync appends duplicate rows and
final-fixture odds get overwritten with potentially post-match data.

Rules (inner to outer — first match wins):
  1. Recent-success guard: if a successful run for this date already completed
     within SYNC_COOLDOWN_HOURS hours, return immediately — zero API calls.
     This is the primary defence against quota burn from restarts and double-clicks.
  2. All-final cache hit: every fixture is FT/AET/PEN with snapshots → skip all API.
  3. Per-fixture snapshot cache:
       - Final + has snapshots  → CACHED  (preserve pre-match odds for backtesting)
       - Non-final + has snaps  → STALE   (clear and refresh — odds may have moved)
       - No snapshots           → FRESH   (insert normally)
  4. If no fixture needs refreshing → skip the market fetch API call.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, date, timedelta, timezone

from sqlalchemy import select, update, delete, func as sql_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_league_tier, DISABLED_LEAGUES
from app.models import Fixture, MarketSnapshot, IngestionRun
from app.services import api_client

logger = logging.getLogger("titibet.ingestion")

FINAL_STATUSES = {"FT", "AET", "PEN"}

# Minimum gap between successful syncs for the same date.
# Within this window a second sync_date() call returns the cached run immediately.
# Set low enough that pre-match odds refreshes still land (scheduled every ~4 h),
# but high enough to absorb restart storms and accidental double-clicks.
SYNC_COOLDOWN_HOURS = 2


async def sync_date(
    db: AsyncSession,
    run_date: date | None = None,
    force: bool = False,
) -> IngestionRun:
    """Pull fixtures + odds for run_date (defaults to today). Returns the IngestionRun record.

    Args:
        force: bypass the 2-hour cooldown guard and the all-final early exit.
               Use this to recover missing scores after the overwrite-with-null
               bug, or to force a re-fetch after a known API data issue.
    """
    if run_date is None:
        run_date = date.today()
    date_str = run_date.isoformat()

    # -- Guard: recent-success check -----------------------------------------
    # Before creating a new run record, check whether we already successfully
    # synced this date within the cooldown window.  If so, return the previous
    # run immediately — no DB writes, no API calls.
    # Skipped when force=True so callers can recover from bad cached state.
    if not force:
        cooldown_cutoff = datetime.now(timezone.utc) - timedelta(hours=SYNC_COOLDOWN_HOURS)
        # Check 1: a successful completed run within the cooldown window.
        recent_run = await db.scalar(
            select(IngestionRun)
            .where(
                IngestionRun.run_date == run_date,
                IngestionRun.status == "success",
                IngestionRun.ended_at >= cooldown_cutoff,
            )
            .order_by(IngestionRun.ended_at.desc())
            .limit(1)
        )
        if recent_run is not None:
            logger.info(
                "Same-day cache hit for %s: successful run completed at %s "
                "(within %dh cooldown). Using cached data — skipping all API calls.",
                run_date,
                recent_run.ended_at.strftime("%H:%M UTC") if recent_run.ended_at else "?",
                SYNC_COOLDOWN_HOURS,
            )
            return recent_run
        # Check 2: a run that started recently but hasn't finished yet (concurrent
        # instance race — e.g. hot-reload starts a second backend while the first
        # sync is still in progress). ended_at is NULL in this case so Check 1 misses it.
        in_progress = await db.scalar(
            select(IngestionRun)
            .where(
                IngestionRun.run_date == run_date,
                IngestionRun.status == "running",
                IngestionRun.started_at >= cooldown_cutoff,
            )
            .order_by(IngestionRun.started_at.desc())
            .limit(1)
        )
        if in_progress is not None:
            logger.info(
                "Concurrent sync guard for %s: a run started at %s is still in progress — skipping.",
                run_date,
                in_progress.started_at.strftime("%H:%M UTC"),
            )
            return in_progress
    else:
        logger.info("Force sync for %s: bypassing cooldown and cache guards.", run_date)

    run = IngestionRun(run_date=run_date, started_at=datetime.now(timezone.utc), status="running")
    db.add(run)
    await db.commit()
    await db.refresh(run)

    try:
        # -- Early exit: fully-cached historical date --------------------------
        # If every fixture on this date is already final AND has market snapshots
        # AND all scores are populated, there is nothing to update — skip API calls.
        #
        # IMPORTANT: also fetch home_score/away_score here. API-Football sometimes
        # returns FT status a few seconds before it populates the goals object.
        # If any fixture is final but still has null scores we MUST fall through to
        # the fixture fetch so the scores get written — otherwise they stay null
        # forever because subsequent syncs would also hit this early-exit guard.
        existing_fixtures_result = await db.execute(
            select(Fixture.id, Fixture.status, Fixture.home_score, Fixture.away_score)
            .where(Fixture.event_date == run_date)
        )
        existing_fixtures = existing_fixtures_result.all()
        if existing_fixtures:
            all_final = all(f.status in FINAL_STATUSES for f in existing_fixtures)
            if all_final:
                # Only skip if every final fixture also has scores — guards against
                # the API race condition where FT status arrives before goals data.
                all_have_scores = all(
                    f.home_score is not None and f.away_score is not None
                    for f in existing_fixtures
                )
                if not all_have_scores:
                    logger.info(
                        "Score-incomplete cache bypass for %s: all %d fixtures are final "
                        "but %d are missing scores — fetching fixture API to populate scores.",
                        run_date,
                        len(existing_fixtures),
                        sum(1 for f in existing_fixtures if f.home_score is None or f.away_score is None),
                    )
                    # Fall through to the fixture fetch below so scores get written.
                else:
                    existing_ids = [f.id for f in existing_fixtures]
                    snap_count_result = await db.execute(
                        select(sql_func.count(MarketSnapshot.id))
                        .where(MarketSnapshot.fixture_id.in_(existing_ids))
                    )
                    total_snaps = snap_count_result.scalar() or 0
                    if total_snaps > 0 and not force:
                        logger.info(
                            "Full cache hit for %s: all %d fixtures are final with scores and snapshots. "
                            "Skipping API entirely.",
                            run_date, len(existing_fixtures),
                        )
                        run.status = "success"
                        run.ended_at = datetime.now(timezone.utc)
                        run.fixtures_pulled = len(existing_fixtures)
                        run.markets_pulled = 0
                        await db.commit()
                        return run

        # -- Fixtures ----------------------------------------------------------
        # For a past date that still has non-final fixtures in the DB, bypass
        # the 30-day file cache unconditionally — the cached response captured
        # a LIVE status that needs to be resolved to FT regardless of TTL.
        past_with_live = (
            run_date < date.today()
            and existing_fixtures
            and not all(f.status in FINAL_STATUSES for f in existing_fixtures)
        )
        fixture_rows = await api_client.fetch_fixtures(date_str, force=force or past_with_live)
        fixtures_upserted = 0

        SKIP_STATUSES = {"CANC", "PST", "TBD", "AWD", "ABD", "WO", "INT", "SUSP"}

        for row in fixture_rows:
            ext_id = row["external_fixture_id"]
            if ext_id is None:
                continue

            fixture_status_short = row.get("status", "")
            if fixture_status_short in SKIP_STATUSES:
                logger.debug(
                    "Skipping fixture %s with status %s", ext_id, fixture_status_short
                )
                continue

            existing = await db.scalar(
                select(Fixture).where(Fixture.external_fixture_id == ext_id)
            )
            tier = get_league_tier(row.get("league") or "", row.get("country") or "")

            if existing:
                # Build update dict carefully: never overwrite a real score with
                # null.  API-Football sometimes returns goals:{home:null,away:null}
                # during the brief results-processing window right after FT — if we
                # blindly wrote those nulls we'd wipe scores that settlement already
                # used, leaving Won/Lost bets with no visible scoreline.
                update_vals: dict = {
                    "status": row.get("status"),
                    "league_tier": tier,
                }
                new_home = row.get("home_score")
                new_away = row.get("away_score")
                if new_home is not None:
                    update_vals["home_score"] = new_home
                if new_away is not None:
                    update_vals["away_score"] = new_away
                # HT scores — same null-guard: only write when the API provides them.
                new_home_ht = row.get("home_score_ht")
                new_away_ht = row.get("away_score_ht")
                if new_home_ht is not None:
                    update_vals["home_score_ht"] = new_home_ht
                if new_away_ht is not None:
                    update_vals["away_score_ht"] = new_away_ht
                await db.execute(
                    update(Fixture)
                    .where(Fixture.external_fixture_id == ext_id)
                    .values(**update_vals)
                )
            else:
                db.add(Fixture(
                    external_fixture_id=ext_id,
                    event_date=row.get("event_date"),
                    kickoff_at=row.get("kickoff_at"),
                    home_team=row.get("home_team", ""),
                    away_team=row.get("away_team", ""),
                    country=row.get("country"),
                    league=row.get("league"),
                    league_id=row.get("league_id"),
                    league_tier=tier,
                    season=row.get("season"),
                    status=row.get("status"),
                    home_score=row.get("home_score"),
                    away_score=row.get("away_score"),
                    home_score_ht=row.get("home_score_ht"),
                    away_score_ht=row.get("away_score_ht"),
                ))
                fixtures_upserted += 1

        await db.commit()

        # -- Snapshot cache check ----------------------------------------------
        # Re-query to get current statuses (updated in the upsert above).
        fixture_map_result = await db.execute(
            select(Fixture.id, Fixture.external_fixture_id, Fixture.status).where(
                Fixture.event_date == run_date
            )
        )
        fixture_rows_db = fixture_map_result.all()
        fixture_map = {row.external_fixture_id: row.id for row in fixture_rows_db}
        fixture_status = {row.id: row.status for row in fixture_rows_db}
        all_fixture_ids = list(fixture_map.values())

        # Which fixture IDs already have at least one snapshot in the DB?
        fixtures_with_snapshots: set[int] = set()
        snapshot_latest: dict[int, datetime] = {}
        if all_fixture_ids:
            counts_result = await db.execute(
                select(
                    MarketSnapshot.fixture_id,
                    sql_func.count(MarketSnapshot.id).label("cnt"),
                    sql_func.max(MarketSnapshot.pulled_at).label("latest_pull"),
                )
                .where(MarketSnapshot.fixture_id.in_(all_fixture_ids))
                .group_by(MarketSnapshot.fixture_id)
                .having(sql_func.count(MarketSnapshot.id) > 0)
            )
            snap_rows = counts_result.all()
            fixtures_with_snapshots = {r.fixture_id for r in snap_rows}
            snapshot_latest = {r.fixture_id: r.latest_pull for r in snap_rows}

        # Classify each fixture.
        cached_ids: set[int] = set()    # final + has snapshots -> preserve as-is
        stale_ids: set[int] = set()     # non-final + has snapshots -> clear and refresh
        fresh_ids: set[int] = set()     # no snapshots yet -> insert

        # Minimum age (hours) before a pre-match snapshot is cleared and re-fetched.
        # Prevents the nightly digest's freshly-fetched odds being wiped by a sync
        # that runs a few hours later (e.g. 18:30 UTC digest → 22:01 UTC sync = 3.5 h).
        # Configurable: STALE_MIN_AGE_HOURS env var. Default 4 h covers the largest
        # inter-sync gap while still allowing daily refreshes.
        _stale_min_age_h = float(os.getenv("STALE_MIN_AGE_HOURS", "4"))
        _now_utc = datetime.now(timezone.utc)

        for fid in all_fixture_ids:
            status = fixture_status.get(fid)
            has_snaps = fid in fixtures_with_snapshots
            if has_snaps and status in FINAL_STATUSES:
                cached_ids.add(fid)
            elif has_snaps:
                latest = snapshot_latest.get(fid)
                if latest is not None:
                    latest_aware = latest if latest.tzinfo else latest.replace(tzinfo=timezone.utc)
                    age_h = (_now_utc - latest_aware).total_seconds() / 3600
                else:
                    age_h = _stale_min_age_h  # unknown age — treat as stale
                if age_h >= _stale_min_age_h:
                    stale_ids.add(fid)
                else:
                    cached_ids.add(fid)  # recent odds — keep, avoid wiping digest data
            else:
                fresh_ids.add(fid)

        needs_refresh_ids = stale_ids | fresh_ids

        if not needs_refresh_ids:
            # Every fixture on this date is already cached - skip the API call.
            logger.info(
                "Snapshot cache hit for %s: all %d fixtures cached, skipping market fetch",
                run_date, len(cached_ids),
            )
            run.status = "success"
            run.ended_at = datetime.now(timezone.utc)
            run.fixtures_pulled = len(fixture_rows)
            run.markets_pulled = 0
            await db.commit()
            return run

        # Clear stale snapshots for non-final fixtures so re-synced odds replace them.
        if stale_ids:
            await db.execute(
                delete(MarketSnapshot).where(MarketSnapshot.fixture_id.in_(stale_ids))
            )
            await db.commit()
            logger.info(
                "Cleared stale snapshots for %d non-final fixtures on %s",
                len(stale_ids), run_date,
            )

        logger.info(
            "Fetching market odds for %s: %d fresh, %d stale-refresh, %d cached (skipped)",
            run_date, len(fresh_ids), len(stale_ids), len(cached_ids),
        )

        # -- Market snapshots --------------------------------------------------
        # Build per-league odds fetch list from fixtures that need a refresh.
        # Fetching /odds?league=L&season=S&date=X (one call per league) bypasses
        # the free-plan 3-page cap on /odds?date=X&page=N, which only covers ~30
        # random fixtures per sync. Tier-sorted so Tier 1/2 leagues consume the
        # quota budget first; cap at MAX_LEAGUE_ODDS_CALLS env (default 12).
        _max_league_calls = int(os.getenv("MAX_LEAGUE_ODDS_CALLS", "12"))
        _ext_to_api_row = {
            r["external_fixture_id"]: r
            for r in fixture_rows
            if r.get("external_fixture_id")
        }
        _league_tier_map: dict[tuple[int, int], int] = {}
        for ext_id, int_id in fixture_map.items():
            if int_id not in needs_refresh_ids:
                continue
            api_row = _ext_to_api_row.get(ext_id)
            if not api_row:
                continue
            league_name = (api_row.get("league") or "").lower().strip()
            if any(d in league_name for d in DISABLED_LEAGUES):
                continue
            lid = api_row.get("league_id")
            sea = api_row.get("season")
            if not lid or not sea:
                continue
            key = (int(lid), int(sea))
            if key not in _league_tier_map:
                _league_tier_map[key] = get_league_tier(
                    api_row.get("league") or "", api_row.get("country") or ""
                )

        _leagues_to_fetch = [
            ls for ls, _ in sorted(_league_tier_map.items(), key=lambda x: x[1])
        ][:_max_league_calls]

        if _leagues_to_fetch:
            logger.info(
                "Per-league odds fetch for %s: %d league(s) selected (cap %d, total unique %d)",
                date_str, len(_leagues_to_fetch), _max_league_calls, len(_league_tier_map),
            )
            market_rows_api = await api_client.fetch_markets_by_leagues(date_str, _leagues_to_fetch)
        else:
            logger.warning(
                "Per-league odds: no eligible league IDs found for %s — "
                "falling back to date-paged fetch",
                date_str,
            )
            market_rows_api = await api_client.fetch_markets(date_str)
        markets_inserted = 0
        now = datetime.now(timezone.utc)

        # Deduplicate API rows: the API sometimes returns the same
        # (fixture, bookmaker, market, selection) more than once in a single
        # response.  Keep the last occurrence (most recent odds) to avoid
        # hitting the unique constraint on market_snapshots.
        deduped: dict[tuple, dict] = {}
        for row in market_rows_api:
            internal_id = fixture_map.get(row["external_fixture_id"])
            if internal_id is None or internal_id not in needs_refresh_ids:
                continue
            key = (internal_id, row["bookmaker"], row["market_type"], row["selection_name"])
            deduped[key] = row

        for (internal_id, bookmaker, market_type, selection_name), row in deduped.items():
            db.add(MarketSnapshot(
                fixture_id=internal_id,
                bookmaker=bookmaker,
                market_type=market_type,
                selection_name=selection_name,
                odds=row["odds"],
                pulled_at=row.get("pulled_at", now),
            ))
            markets_inserted += 1

        await db.commit()

        run.status = "success"
        run.ended_at = datetime.now(timezone.utc)
        run.fixtures_pulled = len(fixture_rows)
        run.markets_pulled = markets_inserted
        await db.commit()

    except Exception as exc:
        await db.rollback()
        run.status = "failed"
        run.ended_at = datetime.now(timezone.utc)
        run.error_message = str(exc)[:500]
        await db.commit()
        raise

    return run


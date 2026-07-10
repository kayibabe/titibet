#!/usr/bin/env python3
"""
backfill.py — Historical fixture + signal backfill for TiTiBet.

Usage:
    # Phase D1 — fixture-only: add FT results for Poisson/ZINB form window
    python3 tools/backfill.py --start 2026-04-10 --end 2026-06-09 --mode fixtures-only

    # Phase D2 — full recompute: regenerate signals from existing snapshots
    python3 tools/backfill.py --start 2026-06-10 --end 2026-07-09 --mode full

Run from /app (the backend root) on the Fly machine so relative imports resolve.
Logs progress to stdout and /data/backfill.log.

Modes:
  fixtures-only  Run sync_date() only. Adds FT result rows so the Poisson
                 form-lambda and ZINB model have more training history.
                 API-Football does not retain pre-match odds for dates >7 days
                 ago, so market_snapshots and signals cannot be added for old
                 dates. This mode skips compute_signals_for_date() entirely.

  full           Run sync_date() then compute_signals_for_date(). Use this for
                 dates that already have market_snapshots in the DB (Jun 10+),
                 where recomputing signals with the current rules is meaningful.
                 For older dates, sync_date() will succeed but market_snapshots
                 will remain 0, so any generated signals will be engine-only
                 (Poisson-only, no Bayesian) and should be treated as lower
                 quality.

Pacing:
  SLEEP_BETWEEN_DATES (5 s) lets the live server handle requests between dates.
  SQLite busy_timeout in database.py guards against write lock contention with
  the live scheduler. The scheduled syncs (04:00, 19:00, 23:00 UTC) take
  priority — run this script during off-peak hours (e.g. 10:00–16:00 UTC).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# ── Path setup ─────────────────────────────────────────────────────────────
# When run as `python3 tools/backfill.py` from /app, parent.parent = /app.
# When run as `python3 /data/backfill_v*.py` (uploaded standalone), the
# parent chain resolves to /, so fall back to the Fly machine's fixed path.
_CANDIDATE = Path(__file__).resolve().parent.parent  # /app when in tools/
_ROOT = _CANDIDATE if (_CANDIDATE / "app" / "core").exists() else Path("/app")
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Logging ────────────────────────────────────────────────────────────────
_LOG_FILE = Path("/data/backfill.log")
_handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
try:
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _handlers.append(logging.FileHandler(_LOG_FILE, encoding="utf-8"))
except Exception:
    pass  # /data may not exist in some envs; stdout is always available

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=_handlers,
)
logger = logging.getLogger("backfill")

SLEEP_BETWEEN_DATES = 5  # seconds


async def _row_counts(db) -> dict[str, int]:
    from sqlalchemy import text
    counts = {}
    for tbl in ("fixtures", "market_snapshots", "signals", "tracked_bets"):
        try:
            n = (await db.scalar(text(f"SELECT COUNT(*) FROM {tbl}"))) or 0
            counts[tbl] = n
        except Exception:
            counts[tbl] = -1
    return counts


async def _signal_count_for_date(db, run_date: date) -> int:
    from sqlalchemy import text
    try:
        return (await db.scalar(text("""
            SELECT COUNT(s.id) FROM signals s
            JOIN fixtures f ON s.fixture_id = f.id
            WHERE f.event_date = :d
        """), {"d": run_date.isoformat()})) or 0
    except Exception:
        return -1


async def _fixture_count_for_date(db, run_date: date) -> int:
    from sqlalchemy import text
    try:
        return (await db.scalar(text(
            "SELECT COUNT(*) FROM fixtures WHERE event_date = :d"
        ), {"d": run_date.isoformat()})) or 0
    except Exception:
        return -1


async def run_backfill(start: date, end: date, mode: str = "full") -> None:
    from app.core.database import AsyncSessionLocal, init_db
    from app.services.ingestion import sync_date
    if mode == "full":
        from app.services.signal_engine import compute_signals_for_date

    await init_db()

    total_dates = (end - start).days + 1
    dates = [start + timedelta(days=i) for i in range(total_dates)]

    logger.info("=" * 60)
    logger.info("BACKFILL START [mode=%s]: %s → %s (%d dates)", mode, start, end, total_dates)
    logger.info("=" * 60)

    completed: list[date] = []
    failed: list[tuple[date, str]] = []
    skipped_api: list[date] = []  # dates where sync_date found all-final cache

    t_start = time.monotonic()

    async with AsyncSessionLocal() as db:
        initial_counts = await _row_counts(db)
    logger.info("DB before backfill: %s", initial_counts)

    for idx, run_date in enumerate(dates, 1):
        logger.info("-" * 50)
        logger.info("[%d/%d] Processing %s", idx, total_dates, run_date)
        date_t0 = time.monotonic()

        try:
            async with AsyncSessionLocal() as db:
                ingestion_run = await sync_date(db, run_date)
                markets_pulled = getattr(ingestion_run, "markets_pulled", "?")
                fixtures_pulled = getattr(ingestion_run, "fixtures_pulled", "?")
                if markets_pulled == 0:
                    skipped_api.append(run_date)
                logger.info(
                    "  sync_date: status=%s fixtures=%s markets_pulled=%s",
                    ingestion_run.status, fixtures_pulled, markets_pulled,
                )

            if mode == "full":
                async with AsyncSessionLocal() as db:
                    n_signals = await compute_signals_for_date(db, run_date)
                    n_fixtures = await _fixture_count_for_date(db, run_date)
                    logger.info(
                        "  signals: %d computed | fixtures in DB: %d | elapsed: %.1fs",
                        n_signals, n_fixtures, time.monotonic() - date_t0,
                    )
            else:
                async with AsyncSessionLocal() as db:
                    n_fixtures = await _fixture_count_for_date(db, run_date)
                logger.info(
                    "  [fixtures-only] fixtures in DB: %d | elapsed: %.1fs",
                    n_fixtures, time.monotonic() - date_t0,
                )

            completed.append(run_date)

        except Exception as exc:
            msg = str(exc)[:200]
            logger.error("  FAILED %s: %s", run_date, msg)
            failed.append((run_date, msg))

        # Progress summary every 30 dates
        if idx % 30 == 0 or idx == total_dates:
            elapsed_min = (time.monotonic() - t_start) / 60
            remaining = total_dates - idx
            rate = idx / elapsed_min if elapsed_min > 0 else 0
            eta_min = remaining / rate if rate > 0 else 0
            async with AsyncSessionLocal() as db:
                counts = await _row_counts(db)
            logger.info("=" * 60)
            logger.info(
                "PROGRESS [%d/%d] — %.1f min elapsed | ETA %.1f min",
                idx, total_dates, elapsed_min, eta_min,
            )
            logger.info("  Completed: %d | Failed: %d | API-skipped: %d",
                        len(completed), len(failed), len(skipped_api))
            logger.info("  DB now: %s", counts)
            if failed:
                logger.info("  Failed dates so far: %s",
                            [str(d) for d, _ in failed])
            logger.info("=" * 60)

        if idx < total_dates:
            await asyncio.sleep(SLEEP_BETWEEN_DATES)

    # ── Final report ─────────────────────────────────────────────────────
    elapsed_total = (time.monotonic() - t_start) / 60
    async with AsyncSessionLocal() as db:
        final_counts = await _row_counts(db)

    logger.info("")
    logger.info("=" * 60)
    logger.info("BACKFILL COMPLETE in %.1f minutes", elapsed_total)
    logger.info("  Dates processed: %d / %d", len(completed), total_dates)
    logger.info("  Dates failed:    %d", len(failed))
    logger.info("  API calls saved by cache: %d dates had 0 market pulls",
                len(skipped_api))
    logger.info("DB before: %s", initial_counts)
    logger.info("DB after:  %s", final_counts)
    added = {k: final_counts.get(k, 0) - initial_counts.get(k, 0)
             for k in initial_counts}
    logger.info("Rows added: %s", added)
    if failed:
        logger.info("FAILED DATES:")
        for d, err in failed:
            logger.info("  %s: %s", d, err)
    logger.info("=" * 60)
    logger.info("Log written to %s", _LOG_FILE)


def main() -> None:
    parser = argparse.ArgumentParser(description="TiTiBet historical backfill")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   required=True, help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument("--mode",  choices=["fixtures-only", "full"], default="full",
                        help="fixtures-only: sync fixtures only (no signal computation); "
                             "full: sync + compute signals (default)")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)
    if start > end:
        print("ERROR: --start must be ≤ --end", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run_backfill(start, end, mode=args.mode))


if __name__ == "__main__":
    main()

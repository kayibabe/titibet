"""
bulk_sync_history.py — Pull historical fixtures + odds for a date range.

Usage:
    python bulk_sync_history.py --days 90
    python bulk_sync_history.py --from 2025-02-01 --to 2025-05-10
    python bulk_sync_history.py --days 180 --dry-run

What it does:
  1. Loops through each date in the range (oldest → newest)
  2. Calls sync_date(force=True) — pulls fixtures + market snapshots from API-Football
  3. Runs compute_signals_for_date so each date has signal data for backtesting
  4. Sleeps between dates to stay within API rate limits (default 2 s)
  5. Skips dates that already have fully-cached final fixtures (no API call needed)
  6. Prints a progress summary after every date

API credit estimate:
  Each date costs ~4 API calls (1 fixture call + up to 3 odds pages).
  90 days ≈ 360 calls.  180 days ≈ 720 calls.
  Check your plan quota at https://dashboard.api-football.com before running.

Rate limit:
  Free plan: 100 calls/day.  Basic+: 7,500/day.
  Use --delay to control the pause between dates (default 2 s).
  For free plans: --days 20 max per day (20 × 4 = 80 calls).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bulk_sync")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bulk-sync historical fixture + odds data")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, default=90,
                       help="Number of past days to sync (default: 90)")
    group.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD",
                       help="Start date (inclusive)")
    p.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD",
                   help="End date (inclusive, default: yesterday)")
    p.add_argument("--delay", type=float, default=2.0,
                   help="Seconds to wait between dates (default: 2)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print dates that would be synced without making API calls")
    p.add_argument("--skip-signals", action="store_true",
                   help="Skip signal computation (faster, but backtest won't have signal data)")
    return p.parse_args()


def build_date_range(args: argparse.Namespace) -> list[date]:
    yesterday = date.today() - timedelta(days=1)
    end = date.fromisoformat(args.date_to) if args.date_to else yesterday
    if args.date_from:
        start = date.fromisoformat(args.date_from)
    else:
        start = end - timedelta(days=args.days - 1)
    if start > end:
        log.error("Start date %s is after end date %s", start, end)
        sys.exit(1)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


async def run(args: argparse.Namespace) -> None:
    dates = build_date_range(args)
    total = len(dates)

    log.info("=" * 60)
    log.info("Bulk sync: %d dates  (%s → %s)", total, dates[0], dates[-1])
    log.info("Delay between dates: %.1f s", args.delay)
    if args.dry_run:
        log.info("DRY RUN — no API calls will be made")
    log.info("=" * 60)

    if args.dry_run:
        for d in dates:
            log.info("  would sync: %s", d)
        log.info("Dry run complete — %d dates listed", total)
        return

    # Import here so .env is loaded before settings are read
    from app.core.database import AsyncSessionLocal
    from app.services.ingestion import sync_date
    from app.services.signal_engine import compute_signals_for_date

    ok = skipped = errors = 0

    for idx, d in enumerate(dates, 1):
        prefix = f"[{idx:>3}/{total}] {d}"
        try:
            async with AsyncSessionLocal() as db:
                run_obj = await sync_date(db, d, force=False)

                if run_obj.status == "success" and run_obj.fixtures_pulled == 0:
                    # Already fully cached — no API calls made, skip signals too
                    log.info("%s  CACHED  (fixtures already final)", prefix)
                    skipped += 1
                    continue

                fixtures_n = run_obj.fixtures_pulled or 0
                markets_n  = run_obj.markets_pulled or 0
                log.info("%s  synced  fixtures=%-3d  snapshots=%-4d",
                         prefix, fixtures_n, markets_n)

                if not args.skip_signals:
                    async with AsyncSessionLocal() as sig_db:
                        sigs = await compute_signals_for_date(sig_db, d)
                        log.info("%s  signals computed: %d", prefix, sigs)

                ok += 1

        except Exception as exc:
            log.error("%s  ERROR: %s", prefix, exc)
            errors += 1

        # Rate-limit pause between dates
        if idx < total:
            await asyncio.sleep(args.delay)

    log.info("=" * 60)
    log.info("Done.  ok=%d  skipped(cached)=%d  errors=%d", ok, skipped, errors)
    log.info("Run the backtest on the Backtest page to see results.")
    log.info("=" * 60)


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(args))

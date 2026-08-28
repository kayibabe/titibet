"""Identify viable chronological discovery/validation windows from stored market snapshots.

Research-only. Reports daily coverage of settled fixtures and market snapshots,
plus candidate date splits using only fixtures with stored snapshots. It never
changes production rules or database state.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

import os
import sys
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from app.core.database import AsyncSessionLocal
from app.models import Fixture, MarketSnapshot


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from", dest="date_from", type=date.fromisoformat, default=date(2026, 1, 1))
    p.add_argument("--to", dest="date_to", type=date.fromisoformat, default=date(2026, 6, 30))
    p.add_argument("--min-discovery", type=int, default=100)
    p.add_argument("--min-validation", type=int, default=50)
    p.add_argument("--output", default="conditional_market_window_audit.json")
    args = p.parse_args()

    async with AsyncSessionLocal() as db:
        base_filters = (
            Fixture.event_date >= args.date_from,
            Fixture.event_date <= args.date_to,
            Fixture.status.in_(["FT", "AET", "PEN"]),
            Fixture.home_score.is_not(None),
            Fixture.away_score.is_not(None),
        )

        settled_by_day: Counter[str] = Counter()
        for day_value, count in (await db.execute(
            select(Fixture.event_date, func.count(Fixture.id))
            .where(*base_filters)
            .group_by(Fixture.event_date)
        )).all():
            if day_value:
                settled_by_day[day_value.isoformat()] = int(count)

        # Count fixture coverage via a SQL join instead of materialising all
        # fixture IDs into a giant SQLite IN (...) clause.
        snapshot_by_day: Counter[str] = Counter()
        snapshot_fixture_count_by_day: Counter[str] = Counter()
        snapshot_rows_by_day: Counter[str] = Counter()
        for day_value, fixture_count, snapshot_rows in (await db.execute(
            select(
                Fixture.event_date,
                func.count(func.distinct(Fixture.id)),
                func.count(MarketSnapshot.id),
            )
            .join(MarketSnapshot, MarketSnapshot.fixture_id == Fixture.id)
            .where(*base_filters)
            .group_by(Fixture.event_date)
        )).all():
            if day_value:
                key = day_value.isoformat()
                snapshot_fixture_count_by_day[key] = int(fixture_count)
                snapshot_rows_by_day[key] = int(snapshot_rows)
                snapshot_by_day[key] = int(fixture_count)

        coverage_days = []
        day = args.date_from
        while day <= args.date_to:
            key = day.isoformat()
            coverage_days.append({
                "date": key,
                "settled_fixtures": settled_by_day.get(key, 0),
                "fixtures_with_snapshots": snapshot_by_day.get(key, 0),
                "snapshot_rows": snapshot_rows_by_day.get(key, 0),
            })
            day += timedelta(days=1)

        # Get one row per settled fixture with at least one market snapshot,
        # ordered chronologically. This remains small even for large databases
        # and avoids any parameter explosion on SQLite.
        usable_rows = (await db.execute(
            select(Fixture.event_date, Fixture.id)
            .join(MarketSnapshot, MarketSnapshot.fixture_id == Fixture.id)
            .where(*base_filters)
            .group_by(Fixture.event_date, Fixture.id)
            .order_by(Fixture.event_date, Fixture.id)
        )).all()
        usable = [(d, int(fid)) for d, fid in usable_rows if d is not None]

        split_candidates = []
        if usable:
            # Cumulative counts by date: O(n) rather than repeatedly scanning
            # the complete usable fixture list for every candidate split.
            by_date = Counter(d for d, _ in usable)
            ordered_dates = sorted(by_date)
            total = len(usable)
            discovery_running = 0
            for split_day in ordered_dates:
                validation_n = total - discovery_running
                if discovery_running >= args.min_discovery and validation_n >= args.min_validation:
                    split_candidates.append({
                        "validation_from": split_day.isoformat(),
                        "discovery_fixtures_with_snapshots": discovery_running,
                        "validation_fixtures_with_snapshots": validation_n,
                    })
                discovery_running += by_date[split_day]

        first_snapshot_date = usable[0][0].isoformat() if usable else None
        last_snapshot_date = usable[-1][0].isoformat() if usable else None
        result = {
            "report_type": "conditional_market_window_audit",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_scope": {"date_from": args.date_from.isoformat(), "date_to": args.date_to.isoformat()},
            "settled_fixtures": sum(settled_by_day.values()),
            "fixtures_with_any_snapshots": len(usable),
            "first_snapshot_fixture_date": first_snapshot_date,
            "last_snapshot_fixture_date": last_snapshot_date,
            "daily_coverage": coverage_days,
            "split_constraints": {"min_discovery": args.min_discovery, "min_validation": args.min_validation},
            "viable_splits": split_candidates,
            "decision": "NO_VALID_SPLIT" if not split_candidates else "VALID_SPLIT_AVAILABLE",
            "research_only": True,
        }

    output = Path(args.output)
    if not output.is_absolute():
        output = Path.cwd() / output
    output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))
    print(f"\nSaved market-window audit to: {output.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

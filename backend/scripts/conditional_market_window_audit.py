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

from sqlalchemy import select, func

import os
import sys
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from app.core.database import AsyncSessionLocal
from app.models import Fixture, MarketSnapshot

TARGET_MARKETS = ("Under 2.5", "Under 3.5", "Over 2.5", "Away Under 0.5")

async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from", dest="date_from", type=date.fromisoformat, default=date(2026, 1, 1))
    p.add_argument("--to", dest="date_to", type=date.fromisoformat, default=date(2026, 6, 30))
    p.add_argument("--min-discovery", type=int, default=100)
    p.add_argument("--min-validation", type=int, default=50)
    p.add_argument("--output", default="conditional_market_window_audit.json")
    args = p.parse_args()

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Fixture.event_date, Fixture.id)
            .where(Fixture.event_date >= args.date_from, Fixture.event_date <= args.date_to)
            .where(Fixture.status.in_(["FT", "AET", "PEN"]))
            .where(Fixture.home_score.is_not(None), Fixture.away_score.is_not(None))
            .order_by(Fixture.event_date, Fixture.id)
        )).all()
        fixture_ids = [int(r.id) for r in rows]
        settled_by_day = Counter(r.event_date.isoformat() for r in rows if r.event_date)

        snap_ids: set[int] = set()
        snap_by_fixture: Counter[int] = Counter()
        if fixture_ids:
            snaps = (await db.execute(
                select(MarketSnapshot.fixture_id)
                .where(MarketSnapshot.fixture_id.in_(fixture_ids))
            )).all()
            for r in snaps:
                if r.fixture_id is not None:
                    snap_by_fixture[int(r.fixture_id)] += 1
                    snap_ids.add(int(r.fixture_id))

        fixture_dates = {int(r.id): r.event_date for r in rows if r.event_date}
        coverage_days = []
        day = args.date_from
        while day <= args.date_to:
            ids = [fid for fid, d in fixture_dates.items() if d == day]
            coverage_days.append({
                "date": day.isoformat(),
                "settled_fixtures": len(ids),
                "fixtures_with_snapshots": sum(1 for fid in ids if fid in snap_ids),
            })
            day += timedelta(days=1)

        usable = sorted((d, fid) for fid, d in fixture_dates.items() if fid in snap_ids)
        split_candidates = []
        for split_day in sorted({d for d, _ in usable}):
            discovery_n = sum(1 for d, _ in usable if d < split_day)
            validation_n = sum(1 for d, _ in usable if d >= split_day)
            if discovery_n >= args.min_discovery and validation_n >= args.min_validation:
                split_candidates.append({
                    "validation_from": split_day.isoformat(),
                    "discovery_fixtures_with_snapshots": discovery_n,
                    "validation_fixtures_with_snapshots": validation_n,
                })

        first_snapshot_date = usable[0][0].isoformat() if usable else None
        last_snapshot_date = usable[-1][0].isoformat() if usable else None
        result = {
            "report_type": "conditional_market_window_audit",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_scope": {"date_from": args.date_from.isoformat(), "date_to": args.date_to.isoformat()},
            "settled_fixtures": len(fixture_ids),
            "fixtures_with_any_snapshots": len(snap_ids),
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

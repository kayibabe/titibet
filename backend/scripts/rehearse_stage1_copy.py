"""Rehearse Stage 1 migration/import on a disposable SQLite backup.

This command is intentionally explicit: it never defaults to a target path and
refuses to use the production database as a target. SQLite's backup API copies
committed pages and WAL state consistently without mutating the source.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path


def _counts(connection: sqlite3.Connection) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for table in ("fixtures", "market_snapshots", "tracked_bets"):
        result[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    result["profit_loss"] = float(connection.execute(
        "SELECT COALESCE(SUM(profit_loss), 0) FROM tracked_bets"
    ).fetchone()[0])
    return result


def _backup(source: Path, target: Path, *, source_verified: bool) -> dict[str, int | float]:
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_db:
        if not source_verified and source_db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("source quick_check failed")
        baseline = _counts(source_db)
        with sqlite3.connect(target) as target_db:
            def progress(status: int, remaining: int, total: int) -> None:
                if status == sqlite3.SQLITE_DONE or remaining == total or remaining % 10000 == 0:
                    print(f"backup pages remaining={remaining} total={total}", flush=True)
            source_db.backup(target_db, pages=10000, progress=progress, sleep=0.01)
    with sqlite3.connect(target) as target_db:
        if target_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup integrity_check failed")
    return baseline


async def _migrate_and_import(target: Path):
    os.environ["DB_URL"] = f"sqlite+aiosqlite:///{target.as_posix()}"
    os.environ["SKIP_STARTUP_SYNC"] = "true"
    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from app.core.database import AsyncSessionLocal, engine
    from app.core.migrations import run_migrations
    from app.services.legacy_evidence_importer import import_legacy_evidence
    from sqlalchemy import text

    await run_migrations(engine)
    await run_migrations(engine)
    async with AsyncSessionLocal() as session:
        report = await import_legacy_evidence(session)
    async with engine.connect() as connection:
        async with connection.begin():
            checks = {
                "foreign_keys": (await connection.execute(text("PRAGMA foreign_keys"))).scalar(),
                "fixture_revisions": (await connection.execute(text("SELECT COUNT(*) FROM fixture_revisions"))).scalar(),
                "odds_quotes": (await connection.execute(text("SELECT COUNT(*) FROM odds_quotes"))).scalar(),
                "feature_snapshots": (await connection.execute(text("SELECT COUNT(*) FROM feature_snapshots"))).scalar(),
            }
    await engine.dispose()
    return report, checks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument(
        "--source-verified", action="store_true",
        help="skip the source quick_check only when a prior integrity check was recorded",
    )
    args = parser.parse_args()
    source = args.source.resolve()
    target = args.target.resolve()
    if source == target or target.name.lower() == "titibet_prod.db":
        raise SystemExit("refusing to use the live database as rehearsal target")
    if not source.is_file() or target.exists():
        raise SystemExit("source must exist and target must not exist")
    baseline = _backup(source, target, source_verified=args.source_verified)
    report, checks = asyncio.run(_migrate_and_import(target))
    with sqlite3.connect(target) as connection:
        after = _counts(connection)
    print(json.dumps({"source_counts": baseline, "target_counts": after,
                      "import_report": report.__dict__, "stage1_checks": checks}, indent=2, default=str))


if __name__ == "__main__":
    main()

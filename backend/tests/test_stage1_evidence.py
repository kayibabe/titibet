from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import Base
from app.core.migrations import run_migrations
from app.models import (
    Fixture,
    FixtureRevision,
    MarketSnapshot,
    ModelVersion,
    OddsQuote,
)
from app.services.legacy_evidence_importer import content_sha256, import_legacy_evidence
from app.services.snapshot_store import save_feature_snapshot


@pytest.mark.asyncio
async def test_legacy_import_is_additive_idempotent_and_keeps_receipt_distinct(db):
    fixture = Fixture(
        id=71001,
        external_fixture_id=81001,
        event_date=date(2026, 9, 1),
        home_team="Home",
        away_team="Away",
        status="FT",
        kickoff_at=datetime(2026, 9, 1, 18, tzinfo=timezone.utc),
        home_score=2,
        away_score=1,
    )
    snapshot = MarketSnapshot(
        id=72001,
        fixture_id=fixture.id,
        bookmaker="Book",
        market_type="Match Winner",
        selection_name="Home",
        odds=2.25,
        pulled_at=datetime(2026, 9, 1, 16, tzinfo=timezone.utc),
    )
    db.add_all([fixture, snapshot])
    await db.commit()

    receipt = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)
    first = await import_legacy_evidence(db, imported_at=receipt)
    second = await import_legacy_evidence(
        db, imported_at=datetime(2026, 9, 13, tzinfo=timezone.utc)
    )

    assert first.fixture_revisions_created == 1
    assert first.quotes_created == 1
    assert second.fixture_revisions_created == 0
    assert second.quotes_created == 0
    revision = await db.scalar(select(FixtureRevision))
    quote = await db.scalar(select(OddsQuote))
    assert revision.evidence_class == "legacy_import"
    assert revision.provider_observation_id is None
    assert quote.pulled_at == snapshot.pulled_at.replace(tzinfo=None)
    assert quote.received_at == receipt.replace(tzinfo=None)
    assert quote.availability == "unknown"
    assert quote.provider_observation_id is None
    assert snapshot.odds == 2.25


@pytest.mark.asyncio
async def test_stage1_evidence_rows_are_database_immutable():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await run_migrations(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO model_versions (name, version, config_sha256, parameters_json) VALUES ('m', '1', 'x', '{}')"
            )
        )
        with pytest.raises(Exception, match="immutable evidence"):
            await connection.execute(
                text("UPDATE model_versions SET version='2' WHERE id=1")
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_stage1_disk_rehearsal_runs_fresh_and_repeat_migrations(tmp_path):
    db_path = tmp_path / "stage1-rehearsal.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    await run_migrations(engine)
    await run_migrations(engine)
    async with engine.connect() as connection:
        tables = {
            row[0]
            for row in (
                await connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ).all()
        }
        triggers = {
            row[0]
            for row in (
                await connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            ).all()
        }
        assert {"fixture_revisions", "model_versions", "feature_snapshots"} <= tables
        assert "trg_feature_snapshots_immutable_update" in triggers
        assert (await connection.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_legacy_odds_evidence_does_not_block_startup():
    """Legacy append-only evidence is preserved when the new key is not unique."""
    from app.core.migrations import TABLE_MIGRATIONS

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE fixtures (id INTEGER PRIMARY KEY, status TEXT, kickoff_at DATETIME, home_team TEXT, event_date DATE)"))
        await conn.execute(text("CREATE TABLE market_snapshots (id INTEGER PRIMARY KEY, fixture_id INTEGER, pulled_at DATETIME)"))
        await conn.execute(text("CREATE TABLE signals (id INTEGER PRIMARY KEY, fixture_id INTEGER, market TEXT, computed_at DATETIME)"))
        await conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, tier TEXT, is_admin INTEGER DEFAULT 0)"))
        await conn.execute(text("CREATE TABLE tracked_bets (id INTEGER PRIMARY KEY, user_id INTEGER, fixture_id INTEGER, market_type TEXT, bookmaker TEXT, selection_name TEXT, source_rule_key TEXT, event_date DATE, created_at DATETIME)"))
        await conn.execute(text("CREATE TABLE learning_proposals (id INTEGER PRIMARY KEY, change_type TEXT, target TEXT)"))
        for sql in TABLE_MIGRATIONS:
            await conn.execute(text(sql))
        await conn.execute(text(
            "INSERT INTO provider_observations "
            "(provider, endpoint, received_at, content_sha256) "
            "VALUES ('test', '/odds', CURRENT_TIMESTAMP, 'abc')"
        ))
        for _ in range(2):
            await conn.execute(text(
                "INSERT INTO odds_quotes "
                "(fixture_id, market_key, market_version, bookmaker, selection_name, odds, "
                "pulled_at, received_at, provider_observation_id) "
                "VALUES (1, 'goals', 'v1', 'book', 'Over 2.5', 1.9, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, 1)"
            ))

    await run_migrations(engine)
    async with engine.connect() as conn:
        indexes = {
            row[1]
            for row in (await conn.execute(text(
                "PRAGMA index_list('odds_quotes')"
            ))).all()
        }
        count = await conn.scalar(text("SELECT COUNT(*) FROM odds_quotes"))
    assert "ix_odds_quote_observation_key" in indexes
    assert count == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_feature_snapshot_is_hash_bound_to_revision_and_model(db):
    fixture = Fixture(id=73001, external_fixture_id=83001, home_team="H", away_team="A")
    db.add(fixture)
    await db.flush()
    revision = FixtureRevision(
        fixture_id=fixture.id,
        received_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
        evidence_class="provider",
    )
    model = ModelVersion(
        name="dual", version="1", config_sha256="c" * 64, parameters_json="{}"
    )
    db.add_all([revision, model])
    await db.flush()
    snapshot = await save_feature_snapshot(
        db,
        fixture_revision=revision,
        model_version=model,
        as_of=datetime(2026, 9, 12, tzinfo=timezone.utc),
        features={"lambda_h": 1.2, "missing": None},
        input_refs={"observation_ids": [4]},
        transform_version="t1",
        evidence_class="prospective",
    )
    assert snapshot.content_sha256 == content_sha256(
        {
            "fixture_revision_id": revision.id,
            "as_of": "2026-09-12T00:00:00",
            "features": {"lambda_h": 1.2, "missing": None},
            "input_refs": {"observation_ids": [4]},
            "transform_version": "t1",
        }
    )
    await db.commit()


def test_snapshot_hash_is_canonical():
    assert content_sha256({"b": 2, "a": 1}) == content_sha256({"a": 1, "b": 2})

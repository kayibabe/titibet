"""
Shared test fixtures.

Environment is configured BEFORE any app import so Settings picks up a safe
throwaway configuration: in-memory SQLite, dummy JWT secret, no API keys,
startup sync skipped. Nothing in these tests can touch the real database,
API-Football quota, or any external service.
"""
import os

# Must run before importing anything from `app.*` — get_settings() is cached.
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "unit-test-secret-not-for-production-0123456789abcdef")
os.environ.setdefault("API_FOOTBALL_KEY", "")
os.environ.setdefault("SKIP_STARTUP_SYNC", "true")
# Existing endpoint/unit tests exercise the legacy published flow deliberately.
# Production defaults remain fail-closed; dedicated gate tests cover that default.
os.environ.setdefault("PUBLICATION_ENABLED", "true")
os.environ.setdefault("PROMOTED_STRATEGY_VERSION", "test-strategy-v1")
os.environ.setdefault("SOURCE_REVISION", "0123456789abcdef0123456789abcdef01234567")

import pytest_asyncio
from datetime import datetime, timedelta, timezone
import json
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
import app.models  # noqa: F401 — registers all ORM models on Base.metadata
import app.models.user  # noqa: F401 — User isn't exported by app.models but tracked_bets FKs users.id
from app.models import (
    ExperimentEvaluation,
    ExperimentRegistration,
    MarketDefinition,
    PromotionReview,
    StrategyVersion,
)
from app.services.promotion_readiness import (
    evaluation_content_sha256,
    registration_content_sha256,
    review_content_sha256,
)


@pytest_asyncio.fixture
async def db():
    """Fresh in-memory SQLite database per test, with all tables created."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        strategy = StrategyVersion(
            name="test-policy",
            version=os.environ["PROMOTED_STRATEGY_VERSION"],
            source_revision=os.environ["SOURCE_REVISION"],
            config_sha256="a" * 64,
            config_json="{}",
            status="research",
        )
        market = MarketDefinition(
            canonical_key="__portfolio__",
            version="canonical-v1",
            settlement_rules="{}",
        )
        session.add_all([strategy, market])
        await session.flush()
        metrics = {
            "settled_count": 100,
            "distinct_fixtures": 100,
            "calendar_days": 60,
            "ece": 0.02,
            "brier_score": 0.15,
            "max_drawdown_pct": 5.0,
            "roi_lower_bound": 0.01,
            "max_concentration_pct": 10.0,
            "excluded_count": 0,
            "lineage_complete_count": 100,
        }
        registration = ExperimentRegistration(
            strategy_version_id=strategy.id,
            market_definition_id=market.id,
            version="test-v1",
            status="frozen",
            min_distinct_fixtures=30,
            min_settled_predictions=50,
            min_calendar_days=30,
            max_ece=0.05,
            max_brier_score=0.20,
            max_drawdown_pct=10.0,
            min_roi_lower_bound=0.0,
            max_concentration_pct=20.0,
            resampling_seed=7,
            resampling_repetitions=1000,
            costs_json='{"commission_pct": 0.0, "slippage_pct": 0.0}',
            risk_limits_json='{"max_stake_pct": 1.0}',
            content_sha256="b" * 64,
            registered_at=datetime.now(timezone.utc),
        )
        registration.content_sha256 = registration_content_sha256(registration)
        session.add(registration)
        await session.flush()
        evaluation = ExperimentEvaluation(
            registration_id=registration.id,
            metrics_json=json.dumps(metrics),
            evaluation_started_at=registration.registered_at + timedelta(seconds=1),
            evaluation_ended_at=registration.registered_at + timedelta(days=60),
            evidence_manifest_sha256="9" * 64,
            evaluator_source_revision=os.environ["SOURCE_REVISION"],
            content_sha256="c" * 64,
            evaluated_at=registration.registered_at + timedelta(days=60),
        )
        evaluation.content_sha256 = evaluation_content_sha256(evaluation)
        session.add(evaluation)
        await session.flush()
        review = PromotionReview(
            evaluation_id=evaluation.id,
            decision="approved",
            reviewer_identity="pytest-fixture",
            rationale="test-only approved evidence",
            content_sha256="d" * 64,
            reviewed_at=evaluation.evaluation_ended_at + timedelta(seconds=1),
        )
        review.content_sha256 = review_content_sha256(review)
        session.add(review)
        await session.commit()
        yield session
    await engine.dispose()

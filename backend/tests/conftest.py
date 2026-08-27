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

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
import app.models  # noqa: F401 — registers all ORM models on Base.metadata
import app.models.user  # noqa: F401 — User isn't exported by app.models but tracked_bets FKs users.id


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
        yield session
    await engine.dispose()

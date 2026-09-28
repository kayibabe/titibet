"""Versioned strategy identity and frozen prospective research registration."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    DISABLED_LEAGUES, DISABLED_MARKETS, MARKET_MAX_ODDS, MARKET_MIN_ODDS,
    MAX_DAILY_EXPOSURE, MAX_PUBLISHED_SIGNALS_PER_DAY, POISSON_RULES,
    PROSPECTIVE_RESEARCH_MARKETS, WOMEN_OVER_SUPPRESSED_MARKETS, Settings,
)
from app.models import ExperimentRegistration
from app.services.evidence_lineage import get_or_create_market_definition, get_or_create_strategy_version
from app.services.promotion_readiness import registration_content_sha256


def active_strategy_version(settings: Settings) -> str:
    if settings.publication_enabled:
        return settings.promoted_strategy_version or "publication-unversioned"
    return settings.research_strategy_version or "research-unversioned"


def strategy_configuration(settings: Settings) -> dict:
    """Configuration whose hash identifies the exact decision policy."""
    return {
        "source_revision": settings.source_revision,
        "disabled_markets": sorted(DISABLED_MARKETS),
        "disabled_leagues": sorted(DISABLED_LEAGUES),
        "market_min_odds": dict(MARKET_MIN_ODDS),
        "market_max_odds": dict(MARKET_MAX_ODDS),
        "poisson_rules": dict(POISSON_RULES),
        "tracking_quote_max_age_minutes": settings.tracking_quote_max_age_minutes,
        "min_ev_pct": settings.min_ev_pct,
        "max_daily_exposure": MAX_DAILY_EXPOSURE,
        "max_published_signals_per_day": MAX_PUBLISHED_SIGNALS_PER_DAY,
        "paper_stake_units": 1.0,
        "kelly_enabled_for_research": False,
        "women_suppressed_markets": sorted(WOMEN_OVER_SUPPRESSED_MARKETS),
        "selection_requires_nonnegative_ev": True,
        "prospective_markets": list(PROSPECTIVE_RESEARCH_MARKETS),
    }


async def _ensure_registration(db: AsyncSession, *, strategy_id: int, market_id: int, version: str) -> ExperimentRegistration:
    existing = await db.scalar(select(ExperimentRegistration).where(
        ExperimentRegistration.strategy_version_id == strategy_id,
        ExperimentRegistration.market_definition_id == market_id,
        ExperimentRegistration.version == version,
    ))
    if existing is not None:
        return existing
    registration = ExperimentRegistration(
        strategy_version_id=strategy_id, market_definition_id=market_id,
        version=version, status="frozen", min_distinct_fixtures=200,
        min_settled_predictions=200, min_calendar_days=60, max_ece=0.05,
        max_brier_score=0.20, max_drawdown_pct=12.0, min_roi_lower_bound=0.0,
        max_concentration_pct=20.0, resampling_seed=20260913,
        resampling_repetitions=5000,
        costs_json=json.dumps({"flat_stake_units": 1.0, "commission_pct": 0.0,
                               "execution_odds_haircut_pct": 8.0}, sort_keys=True),
        risk_limits_json=json.dumps({"kelly_enabled": False,
                                     "max_open_exposure_units": 8.0,
                                     "max_picks_per_day": MAX_PUBLISHED_SIGNALS_PER_DAY,
                                     "max_stake_per_selection_units": 1.0,
                                     "multiple_testing_method": "bonferroni",
                                     "family_size": len(PROSPECTIVE_RESEARCH_MARKETS),
                                     "family_alpha": 0.05}, sort_keys=True),
        content_sha256="pending", registered_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    registration.content_sha256 = registration_content_sha256(registration)
    db.add(registration)
    await db.flush()
    return registration


async def register_under35_research(db: AsyncSession, settings: Settings) -> ExperimentRegistration:
    """Create the frozen three-market prospective cohort; never enables publication."""
    revision = settings.source_revision.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{12,64}", revision):
        raise ValueError("SOURCE_REVISION must be an exact 12-64 character hexadecimal revision")
    version = settings.research_strategy_version.strip()
    if not version:
        raise ValueError("RESEARCH_STRATEGY_VERSION must be configured")
    if settings.publication_enabled:
        raise ValueError("research registration requires PUBLICATION_ENABLED=false")
    strategy = await get_or_create_strategy_version(
        db, name="titibet-serving-policy", version=version,
        source_revision=revision, config=strategy_configuration(settings),
    )
    registrations: list[ExperimentRegistration] = []
    for market_key in PROSPECTIVE_RESEARCH_MARKETS:
        market = await get_or_create_market_definition(db, market_key)
        slug = "" if market_key == "Under 3.5" else market_key.lower().replace(" ", "-").replace(".", "") + "-"
        registrations.append(await _ensure_registration(
            db, strategy_id=strategy.id, market_id=market.id,
            version=f"{version}-{slug}prospective",
        ))
    portfolio_market = await get_or_create_market_definition(db, "__portfolio__")
    await _ensure_registration(
        db, strategy_id=strategy.id, market_id=portfolio_market.id,
        version=f"{version}-portfolio-prospective",
    )
    await db.commit()
    await db.refresh(registrations[0])
    return registrations[0]

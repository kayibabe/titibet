from types import SimpleNamespace

import pytest

from app.models import ExperimentRegistration, MarketDefinition
from app.services.strategy_registry import active_strategy_version, register_under35_research
from sqlalchemy import select


def test_research_and_promoted_versions_are_separate():
    research = SimpleNamespace(
        publication_enabled=False,
        research_strategy_version="u35-research-v1",
        promoted_strategy_version="published-v1",
    )
    published = SimpleNamespace(
        publication_enabled=True,
        research_strategy_version="u35-research-v1",
        promoted_strategy_version="published-v1",
    )
    assert active_strategy_version(research) == "u35-research-v1"
    assert active_strategy_version(published) == "published-v1"


@pytest.mark.asyncio
async def test_registration_stays_frozen_and_publication_off(db):
    settings = SimpleNamespace(
        source_revision="a" * 64,
        research_strategy_version="u35-research-v1",
        promoted_strategy_version="",
        publication_enabled=False,
        tracking_quote_max_age_minutes=120,
        min_ev_pct=0.0,
    )
    registration = await register_under35_research(db, settings)
    repeated = await register_under35_research(db, settings)
    assert repeated.id == registration.id
    assert registration.status == "frozen"
    assert registration.version == "u35-research-v1-prospective"
    assert registration.min_settled_predictions == 200
    assert registration.min_calendar_days == 60
    portfolio = await db.scalar(
        select(ExperimentRegistration)
        .join(MarketDefinition, ExperimentRegistration.market_definition_id == MarketDefinition.id)
        .where(
            MarketDefinition.canonical_key == "__portfolio__",
            ExperimentRegistration.version == "u35-research-v1-portfolio-prospective",
        )
    )
    assert portfolio is not None
    assert portfolio.status == "frozen"
    assert portfolio.version == "u35-research-v1-portfolio-prospective"
    market_keys = {
        item.canonical_key
        for item in (await db.scalars(select(MarketDefinition))).all()
    }
    assert {"Under 3.5", "Over 2.5", "Home Over 0.5", "__portfolio__"} <= market_keys


@pytest.mark.asyncio
async def test_registration_rejects_publication_mode(db):
    settings = SimpleNamespace(
        source_revision="a" * 64,
        research_strategy_version="u35-research-v1",
        publication_enabled=True,
    )
    with pytest.raises(ValueError, match="PUBLICATION_ENABLED=false"):
        await register_under35_research(db, settings)

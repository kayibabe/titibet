import pytest
from types import SimpleNamespace

from app.models import Fixture, OddsQuote, Signal
from app.services.paper_observations import paper_selection_reasons


def test_paper_selector_rejects_negative_ev_and_unvalidated_womens_fixture():
    signal = Signal(
        fixture_id=1, market="Under 3.5", bayesian_prob=0.70,
        poisson_prob=0.69, dual_confidence="Medium", contradiction=False,
    )
    fixture = Fixture(
        id=1, external_fixture_id=1, home_team="Example W", away_team="Visitors W",
        league="National Women League",
    )
    quote = OddsQuote(
        fixture_id=1, market_key="Under 3.5", bookmaker="Book",
        selection_name="Under 3.5", odds=1.35,
        pulled_at=__import__('datetime').datetime.now(), received_at=__import__('datetime').datetime.now(),
    )
    reasons = paper_selection_reasons(signal, fixture, quote, SimpleNamespace(min_ev_pct=0.0))
    assert "NONPOSITIVE_EV" in reasons
    assert "WOMENS_MODEL_NOT_VALIDATED" in reasons


@pytest.mark.asyncio
async def test_empty_paper_review_is_research_only(db):
    from app.services.paper_observations import walk_forward_paper_report

    report = await walk_forward_paper_report(db)

    assert report["observation_count"] == 0
    assert report["lineage_complete_count"] == 0
    assert report["folds"] == []
    assert report["ready_for_promotion"] is False

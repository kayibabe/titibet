"""Prospective zero-stake paper collection and chronological evaluation."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    MARKET_MAX_ODDS, MARKET_MIN_ODDS, PROSPECTIVE_RESEARCH_MARKETS,
    Settings, get_settings, is_womens_fixture, WOMEN_OVER_SUPPRESSED_MARKETS,
)
from app.models import Fixture, OddsQuote, PaperObservation, Signal, SignalDecision, StrategyVersion
from app.quant.metrics import brier_score, log_loss, roi
from app.quant.walk_forward import make_expanding_folds
from app.services.settlement import FINAL_STATUSES, VOID_STATUSES, _score_condition


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def paper_selection_reasons(
    signal: Signal,
    fixture: Fixture,
    quote: OddsQuote,
    settings: Settings,
) -> tuple[str, ...]:
    """Apply the frozen research selector without consulting match outcomes."""
    reasons: list[str] = []
    probability = max(signal.bayesian_prob or 0.0, signal.poisson_prob or 0.0)
    minimum = MARKET_MIN_ODDS.get(signal.market)
    maximum = MARKET_MAX_ODDS.get(signal.market)
    if minimum is not None and quote.odds < minimum:
        reasons.append("ODDS_BELOW_STRATEGY_MINIMUM")
    if maximum is not None and quote.odds >= maximum:
        reasons.append("ODDS_AT_OR_ABOVE_STRATEGY_MAXIMUM")
    if probability * quote.odds - 1.0 < settings.min_ev_pct / 100.0:
        reasons.append("NONPOSITIVE_EV")
    if signal.dual_confidence == "Low":
        reasons.append("LOW_CONFIDENCE")
    if signal.contradiction:
        reasons.append("MODEL_CONTRADICTION")
    if signal.market in WOMEN_OVER_SUPPRESSED_MARKETS and is_womens_fixture(
        fixture.league, fixture.home_team, fixture.away_team
    ):
        reasons.append("WOMENS_MODEL_NOT_VALIDATED")
    return tuple(reasons)


async def collect_paper_observations(
    db: AsyncSession,
    run_date: date,
    *,
    market: str = "Under 3.5",
    now: datetime | None = None,
    settings: Settings | None = None,
) -> int:
    """Capture only complete, eligible, pre-kickoff decisions; never stakes money."""
    as_of = _utc(now or datetime.now(timezone.utc))
    active_settings = settings or get_settings()
    rows = (await db.execute(
        select(Signal, Fixture, SignalDecision, OddsQuote)
        .join(Fixture, Signal.fixture_id == Fixture.id)
        .join(SignalDecision, Signal.decision_id == SignalDecision.id)
        .join(OddsQuote, SignalDecision.executable_quote_id == OddsQuote.id)
        .where(Fixture.event_date == run_date)
        .where(Fixture.status == "NS")
        .where(Fixture.kickoff_at.is_not(None))
        .where(Signal.market == market)
        .where(SignalDecision.eligibility_status == "eligible_for_review")
        .where(SignalDecision.lineage_complete.is_(True))
    )).all()

    inserted = 0
    for signal, fixture, decision, quote in rows:
        kickoff = _utc(fixture.kickoff_at)
        computed_at = _utc(decision.computed_at)
        received_at = _utc(quote.received_at)
        probability = max(signal.bayesian_prob or 0.0, signal.poisson_prob or 0.0)
        if as_of >= kickoff or computed_at >= kickoff or received_at > computed_at:
            continue
        if not (1.0 < quote.odds and 0.0 < probability < 1.0):
            continue
        if paper_selection_reasons(signal, fixture, quote, active_settings):
            continue
        existing = await db.scalar(
            select(PaperObservation.id)
            .where(PaperObservation.fixture_id == fixture.id)
            .where(PaperObservation.market_type == market)
            .where(PaperObservation.strategy_version_id == decision.strategy_version_id)
        )
        if existing is not None:
            continue
        db.add(PaperObservation(
            decision_id=decision.id,
            fixture_id=fixture.id,
            market_type=market,
            event_date=fixture.event_date,
            kickoff_at=kickoff.replace(tzinfo=None),
            observed_at=computed_at.replace(tzinfo=None),
            odds=quote.odds,
            model_probability=probability,
            quote_id=quote.id,
            feature_snapshot_id=decision.feature_snapshot_id,
            model_version_id=decision.model_version_id,
            strategy_version_id=decision.strategy_version_id,
            evidence_class="prospective",
        ))
        inserted += 1
    if inserted:
        await db.commit()
    return inserted


async def collect_paper_observations_for_research_cohort(
    db: AsyncSession,
    run_date: date,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> int:
    """Collect the frozen cohort; each market keeps its own immutable row."""
    total = 0
    for market in PROSPECTIVE_RESEARCH_MARKETS:
        total += await collect_paper_observations(
            db, run_date, market=market, now=now, settings=settings
        )
    return total


async def settle_paper_observations(db: AsyncSession) -> int:
    """Settle paper rows from final fixture scores; no user or live-bet mutation."""
    rows = (await db.execute(
        select(PaperObservation, Fixture)
        .join(Fixture, PaperObservation.fixture_id == Fixture.id)
        .where(PaperObservation.result_status == "Pending")
    )).all()
    settled = 0
    condition_cache: dict[str, object] = {}
    for observation, fixture in rows:
        status = (fixture.status or "").strip().upper()
        if status in VOID_STATUSES:
            observation.result_status = "Void"
            observation.profit_loss = 0.0
        elif status in FINAL_STATUSES and fixture.home_score is not None and fixture.away_score is not None:
            condition = condition_cache.setdefault(observation.market_type, _score_condition(observation.market_type))
            if condition is None:
                continue
            won = condition(fixture.home_score, fixture.away_score)
            observation.result_status = "Won" if won else "Lost"
            observation.profit_loss = round(observation.odds - 1.0, 6) if won else -1.0
        else:
            continue
        observation.settled_at = datetime.now(timezone.utc).replace(tzinfo=None)
        settled += 1
    if settled:
        await db.commit()
    return settled


async def walk_forward_paper_report(
    db: AsyncSession,
    *,
    market: str = "Under 3.5",
    minimum_train: int = 30,
    test_size: int = 10,
    strategy_version: str | None = None,
) -> dict:
    """Evaluate frozen paper probabilities in chronological expanding test folds."""
    query = (
        select(PaperObservation)
        .join(StrategyVersion, PaperObservation.strategy_version_id == StrategyVersion.id)
        .where(PaperObservation.market_type == market)
        .where(PaperObservation.result_status.in_(("Won", "Lost")))
    )
    if strategy_version:
        query = query.where(StrategyVersion.version == strategy_version)
    rows = (await db.execute(
        query.order_by(PaperObservation.observed_at, PaperObservation.id)
    )).scalars().all()
    folds = make_expanding_folds(len(rows), minimum_train=minimum_train, test_size=test_size) if len(rows) > minimum_train else ()
    reports: list[dict] = []
    for fold in folds:
        test = rows[fold.test_slice]
        outcomes = [1 if row.result_status == "Won" else 0 for row in test]
        probabilities = [row.model_probability for row in test]
        profits = [row.profit_loss for row in test]
        reports.append({
            "train_count": fold.train_end,
            "test_count": len(test),
            "test_start": test[0].event_date.isoformat() if test[0].event_date else None,
            "test_end": test[-1].event_date.isoformat() if test[-1].event_date else None,
            "brier": round(brier_score(probabilities, outcomes), 6),
            "log_loss": round(log_loss(probabilities, outcomes), 6),
            "roi": round(roi(profits, [1.0] * len(test)), 6),
        })
    return {
        "market": market,
        "strategy_version": strategy_version,
        "observation_count": len(rows),
        "minimum_train": minimum_train,
        "test_size": test_size,
        "lineage_complete_count": len(rows),
        "folds": reports,
        "ready_for_promotion": False,
        "promotion_note": "Paper evidence requires registered gates and human review; this report never enables publication.",
    }

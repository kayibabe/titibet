from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import (
    EvidenceExclusion,
    ExperimentEvaluation,
    ExperimentRegistration,
    FeatureSnapshot,
    Fixture,
    FixtureRevision,
    ModelVersion,
    OddsQuote,
    PromotionReview,
)
from app.services.evidence_lineage import (
    SnapshotAssessment,
    assess_snapshot,
    record_snapshot_exclusions,
)
from app.services.promotion_readiness import (
    configuration_readiness,
    evaluation_content_sha256,
    evaluate_registration,
    publication_readiness,
    registration_content_sha256,
    review_content_sha256,
)


def _revision(now: datetime, *, status: str = "NS") -> FixtureRevision:
    return FixtureRevision(
        fixture_id=1,
        kickoff_at=now + timedelta(hours=2),
        status=status,
        received_at=now - timedelta(minutes=5),
        evidence_class="provider",
    )


def _quote(now: datetime, *, received_at: datetime | None = None) -> OddsQuote:
    return OddsQuote(
        fixture_id=1,
        market_key="Goals Over/Under",
        bookmaker="Book",
        selection_name="Over 2.5",
        odds=1.9,
        pulled_at=now - timedelta(minutes=2),
        received_at=received_at or now - timedelta(minutes=1),
    )


def test_snapshot_assessment_requires_pre_kickoff_scheduled_complete_quotes():
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    assert assess_snapshot(
        fixture_revision=_revision(now), as_of=now, required_quotes=[_quote(now)]
    ).prospective

    late = assess_snapshot(
        fixture_revision=_revision(now),
        as_of=now + timedelta(hours=2),
        required_quotes=[_quote(now)],
    )
    assert late.evidence_class == "research_excluded"
    assert "SNAPSHOT_AT_OR_AFTER_KICKOFF" in late.reasons

    missing = assess_snapshot(
        fixture_revision=_revision(now), as_of=now, required_quotes=[None]
    )
    assert "MISSING_REQUIRED_QUOTE" in missing.reasons

    post_decision = assess_snapshot(
        fixture_revision=_revision(now),
        as_of=now,
        required_quotes=[_quote(now, received_at=now + timedelta(minutes=1))],
    )
    assert "QUOTE_RECEIVED_AFTER_DECISION" in post_decision.reasons


@pytest.mark.asyncio
async def test_exclusion_audit_is_additive_and_idempotent(db):
    now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
    fixture = Fixture(
        id=91001,
        external_fixture_id=92001,
        home_team="H",
        away_team="A",
    )
    db.add(fixture)
    await db.flush()
    revision = FixtureRevision(
        fixture_id=fixture.id,
        kickoff_at=now - timedelta(hours=1),
        status="FT",
        received_at=now,
        evidence_class="provider",
    )
    model = ModelVersion(
        name="m", version="v", source_revision="a" * 40,
        config_sha256="c" * 64, parameters_json="{}",
    )
    db.add_all([revision, model])
    await db.flush()
    snapshot = FeatureSnapshot(
        fixture_id=fixture.id,
        fixture_revision_id=revision.id,
        model_version_id=model.id,
        as_of=now,
        features_json="{}",
        input_refs_json="{}",
        transform_version="t",
        content_sha256="d" * 64,
        evidence_class="research_excluded",
    )
    db.add(snapshot)
    await db.flush()
    assessment = SnapshotAssessment(
        "research_excluded", ("SNAPSHOT_AT_OR_AFTER_KICKOFF",)
    )
    await record_snapshot_exclusions(
        db, snapshot=snapshot, assessment=assessment, detected_at=now
    )
    await db.commit()
    await record_snapshot_exclusions(
        db, snapshot=snapshot, assessment=assessment, detected_at=now
    )
    await db.commit()
    rows = list((await db.scalars(select(EvidenceExclusion))).all())
    assert len(rows) == 1
    assert snapshot.evidence_class == "research_excluded"


def test_publication_configuration_is_default_off_and_version_bound():
    blocked = configuration_readiness(SimpleNamespace(
        publication_enabled=False,
        promoted_strategy_version="",
        source_revision="working-tree",
    ))
    assert blocked.ready is False
    assert set(blocked.reasons) == {
        "PUBLICATION_DISABLED",
        "MISSING_PROMOTED_STRATEGY_VERSION",
        "MISSING_EXACT_SOURCE_REVISION",
    }


def test_registered_experiment_requires_every_gate():
    registration = ExperimentRegistration(
        id=7,
        strategy_version_id=1,
        market_definition_id=1,
        version="v1",
        status="frozen",
        min_distinct_fixtures=30,
        min_settled_predictions=30,
        min_calendar_days=30,
        max_ece=0.05,
        max_brier_score=0.20,
        max_drawdown_pct=10,
        min_roi_lower_bound=0,
        max_concentration_pct=20,
        resampling_seed=7,
        resampling_repetitions=1000,
        costs_json='{"commission_pct":0,"slippage_pct":0}',
        risk_limits_json='{"max_stake_pct":1}',
        content_sha256="e" * 64,
        registered_at=datetime.now(timezone.utc),
    )
    registration.content_sha256 = registration_content_sha256(registration)
    evaluation = ExperimentEvaluation(
        id=8,
        registration_id=7,
        metrics_json='{"settled_count":40,"distinct_fixtures":40,"calendar_days":45,'
                     '"ece":0.03,"brier_score":0.15,"max_drawdown_pct":8,'
                     '"roi_lower_bound":0.01,"max_concentration_pct":10,'
                     '"excluded_count":0,"lineage_complete_count":40}',
        evaluation_started_at=registration.registered_at + timedelta(seconds=1),
        evaluation_ended_at=registration.registered_at + timedelta(days=45),
        evidence_manifest_sha256="8" * 64,
        evaluator_source_revision="7" * 40,
        content_sha256="f" * 64,
        evaluated_at=registration.registered_at + timedelta(days=45),
    )
    evaluation.content_sha256 = evaluation_content_sha256(evaluation)
    review = PromotionReview(
        id=9,
        evaluation_id=8,
        decision="approved",
        reviewer_identity="independent-reviewer",
        rationale="all gates passed",
        content_sha256="1" * 64,
        reviewed_at=registration.registered_at + timedelta(days=45, seconds=1),
    )
    review.content_sha256 = review_content_sha256(review)
    assert evaluate_registration(registration, evaluation, review).ready
    evaluation.metrics_json = evaluation.metrics_json.replace(
        '"excluded_count":0', '"excluded_count":1'
    )
    failed = evaluate_registration(registration, evaluation, review)
    assert not failed.ready
    assert "EXCLUDED_EVIDENCE_PRESENT" in failed.reasons


@pytest.mark.asyncio
async def test_persisted_publication_gate_requires_exact_approved_chain(db):
    readiness = await publication_readiness(db)
    assert readiness.ready

    review = await db.scalar(select(PromotionReview))
    review.decision = "rejected"
    await db.commit()
    blocked = await publication_readiness(db)
    assert not blocked.ready
    assert "MISSING_APPROVED_PROMOTION_REVIEW" in blocked.reasons


def test_third_scheduler_slot_is_settlement_only(monkeypatch):
    from app import scheduler

    monkeypatch.setattr(scheduler, "_scheduler", None)
    configured = scheduler.get_scheduler()
    jobs = {job.id: job.func for job in configured.get_jobs()}
    assert jobs["sync-2300"] is scheduler.settlement_only
    assert jobs["sync-0400"] is scheduler.sync_and_compute
    assert jobs["sync-1900"] is scheduler.sync_and_compute


@pytest.mark.asyncio
async def test_publication_and_operational_sinks_fail_closed(db, monkeypatch):
    from app.services import auto_tracker, promotion_readiness, telegram

    blocked_settings = SimpleNamespace(
        publication_enabled=False,
        promoted_strategy_version="",
        source_revision="",
    )
    monkeypatch.setattr(promotion_readiness, "get_settings", lambda: blocked_settings)

    with pytest.raises(HTTPException) as exc_info:
        await promotion_readiness.require_publication_ready(db)
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "RESEARCH_ONLY"

    assert await auto_tracker.auto_track_date(db, datetime.now().date()) == 0
    assert await telegram.push_signal_digest(db) == 0

"""Persisted prospective evaluation and human promotion-review workflow."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import (
    EvidenceExclusion,
    ExperimentEvaluation,
    ExperimentRegistration,
    MarketDefinition,
    PaperObservation,
    PromotionReview,
    SignalDecision,
    StrategyVersion,
)
from app.services.promotion_readiness import (
    ReadinessResult,
    evaluation_content_sha256,
    evaluate_registration,
    review_content_sha256,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _brier(probabilities: list[float], outcomes: list[int]) -> float:
    if not probabilities:
        return 0.0
    return sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / len(probabilities)


def _ece(probabilities: list[float], outcomes: list[int], bins: int = 10) -> float:
    if not probabilities:
        return 0.0
    total = len(probabilities)
    error = 0.0
    for bucket in range(bins):
        values = [
            (p, y) for p, y in zip(probabilities, outcomes)
            if min(bins - 1, int(p * bins)) == bucket
        ]
        if values:
            error += len(values) / total * abs(
                sum(p for p, _ in values) / len(values)
                - sum(y for _, y in values) / len(values)
            )
    return error


def _max_drawdown_pct(profits: list[float]) -> float:
    bankroll = peak = 100.0
    maximum = 0.0
    for profit in profits:
        bankroll += profit
        peak = max(peak, bankroll)
        if peak > 0:
            maximum = max(maximum, (peak - bankroll) / peak * 100.0)
    return maximum


def _roi_lower_bound(
    profits: list[float], seed: int, repetitions: int, alpha: float = 0.025
) -> float:
    if not profits:
        return 0.0
    if len(profits) == 1:
        return profits[0]
    rng = random.Random(seed)
    estimates: list[float] = []
    n = len(profits)
    for _ in range(max(1, repetitions)):
        estimates.append(sum(profits[rng.randrange(n)] for _ in range(n)) / n)
    estimates.sort()
    return estimates[max(0, int(alpha * (len(estimates) - 1)))]


async def _registration_context(db: AsyncSession, registration_id: int):
    row = await db.execute(
        select(ExperimentRegistration, MarketDefinition, StrategyVersion)
        .join(MarketDefinition, ExperimentRegistration.market_definition_id == MarketDefinition.id)
        .join(StrategyVersion, ExperimentRegistration.strategy_version_id == StrategyVersion.id)
        .where(ExperimentRegistration.id == registration_id)
    )
    return row.one_or_none()


async def evaluate_registered_experiment(
    db: AsyncSession,
    registration_id: int,
    *,
    evaluator_source_revision: str | None = None,
) -> ExperimentEvaluation:
    """Compute and persist one immutable evaluation for a frozen registration."""
    context = await _registration_context(db, registration_id)
    if context is None:
        raise ValueError("experiment registration was not found")
    registration, market, strategy = context
    if registration.status != "frozen":
        raise ValueError("only frozen registrations can be evaluated")

    query = (
        select(PaperObservation)
        .where(PaperObservation.strategy_version_id == registration.strategy_version_id)
        .where(PaperObservation.result_status.in_(("Won", "Lost")))
        .order_by(PaperObservation.observed_at, PaperObservation.id)
    )
    if market.canonical_key != "__portfolio__":
        query = query.where(PaperObservation.market_type == market.canonical_key)
    rows = list((await db.scalars(query)).all())
    probabilities = [float(row.model_probability) for row in rows]
    outcomes = [1 if row.result_status == "Won" else 0 for row in rows]
    profits = [float(row.profit_loss) for row in rows]
    event_dates = {row.event_date for row in rows if row.event_date is not None}
    fixture_ids = {row.fixture_id for row in rows}
    daily_counts: dict[object, int] = {}
    for row in rows:
        daily_counts[row.event_date] = daily_counts.get(row.event_date, 0) + 1

    decision_ids = {row.decision_id for row in rows}
    decisions = []
    if decision_ids:
        decisions = list(
            (await db.scalars(
                select(SignalDecision).where(SignalDecision.id.in_(decision_ids))
            )).all()
        )
    lineage_complete_count = sum(1 for item in decisions if item.lineage_complete)
    snapshot_ids = {item.feature_snapshot_id for item in decisions}
    excluded_count = 0
    if snapshot_ids:
        excluded_count = len(
            set((await db.scalars(
                select(EvidenceExclusion.evidence_id)
                .where(EvidenceExclusion.evidence_type == "feature_snapshot")
                .where(EvidenceExclusion.evidence_id.in_(snapshot_ids))
            )).all())
        )

    manifest = [
        {
            "id": row.id,
            "decision_id": row.decision_id,
            "fixture_id": row.fixture_id,
            "feature_snapshot_id": row.feature_snapshot_id,
            "quote_id": row.quote_id,
            "observed_at": row.observed_at,
            "result_status": row.result_status,
            "odds": row.odds,
            "model_probability": row.model_probability,
            "profit_loss": row.profit_loss,
        }
        for row in rows
    ]
    metrics = {
        "settled_count": len(rows),
        "distinct_fixtures": len(fixture_ids),
        "calendar_days": len(event_dates),
        "ece": round(_ece(probabilities, outcomes), 8),
        "brier_score": round(_brier(probabilities, outcomes), 8),
        "max_drawdown_pct": round(_max_drawdown_pct(profits), 8),
        "roi_lower_bound": round(
            _roi_lower_bound(
                profits,
                registration.resampling_seed,
                registration.resampling_repetitions,
                alpha=(
                    float(json.loads(registration.risk_limits_json).get("family_alpha", 0.05))
                    / max(1, int(json.loads(registration.risk_limits_json).get("family_size", 1)))
                    / 2.0
                ),
            ),
            8,
        ),
        "multiple_testing_method": json.loads(registration.risk_limits_json).get(
            "multiple_testing_method", "none"
        ),
        "max_concentration_pct": round(
            max(daily_counts.values(), default=0) / len(rows) * 100.0 if rows else 0.0,
            8,
        ),
        "excluded_count": excluded_count,
        "lineage_complete_count": lineage_complete_count,
        "roi": round(sum(profits) / len(profits), 8) if profits else 0.0,
        "manifest_count": len(manifest),
    }
    now = _utc_now()
    observed = [row.observed_at for row in rows if row.observed_at is not None]
    settled = [row.settled_at for row in rows if row.settled_at is not None]
    started = max(registration.registered_at, min(observed, default=registration.registered_at))
    ended = max(settled, default=now)
    evaluation = ExperimentEvaluation(
        registration_id=registration.id,
        metrics_json=json.dumps(metrics, sort_keys=True, separators=(",", ":")),
        evaluation_started_at=started,
        evaluation_ended_at=ended,
        evidence_manifest_sha256=_sha256(manifest),
        evaluator_source_revision=(evaluator_source_revision or get_settings().source_revision).strip().lower(),
        content_sha256="pending",
        evaluated_at=now,
    )
    evaluation.content_sha256 = evaluation_content_sha256(evaluation)
    db.add(evaluation)
    await db.commit()
    await db.refresh(evaluation)
    return evaluation


async def approve_evaluation(
    db: AsyncSession,
    evaluation_id: int,
    *,
    reviewer_identity: str,
    rationale: str,
    decision: str = "approved",
) -> tuple[PromotionReview, ReadinessResult]:
    """Record a human decision, only approving an evaluation that passes all gates."""
    evaluation = await db.get(ExperimentEvaluation, evaluation_id)
    if evaluation is None:
        raise ValueError("evaluation was not found")
    registration = await db.get(ExperimentRegistration, evaluation.registration_id)
    if registration is None:
        raise ValueError("evaluation registration was not found")
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    if not reviewer_identity.strip() or not rationale.strip():
        raise ValueError("reviewer identity and rationale are required")
    provisional_review = PromotionReview(
        evaluation_id=evaluation.id,
        decision=decision,
        reviewer_identity=reviewer_identity.strip(),
        rationale=rationale.strip(),
        content_sha256="pending",
        reviewed_at=max(_utc_now(), evaluation.evaluation_ended_at),
    )
    provisional_review.content_sha256 = review_content_sha256(provisional_review)
    readiness = evaluate_registration(registration, evaluation, provisional_review)
    if decision == "approved" and not readiness.ready:
        raise ValueError("evaluation does not satisfy promotion gates: " + ",".join(readiness.reasons))
    review = PromotionReview(
        evaluation_id=evaluation.id,
        decision=decision,
        reviewer_identity=reviewer_identity.strip(),
        rationale=rationale.strip(),
        content_sha256="pending",
        reviewed_at=_utc_now(),
    )
    review.content_sha256 = review_content_sha256(review)
    db.add(review)
    await db.commit()
    await db.refresh(review)
    return review, evaluate_registration(registration, evaluation, review)

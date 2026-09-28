"""Registered experiment readiness and default-off publication gate."""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import (
    ExperimentEvaluation,
    ExperimentRegistration,
    MarketDefinition,
    PromotionReview,
    Signal,
    SignalDecision,
    StrategyVersion,
)


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    reasons: tuple[str, ...]
    registration_id: int | None = None


def published_signal_scope(query, settings: Settings | None = None):
    """Apply the exact immutable-decision scope used by every publication sink."""
    active = settings or get_settings()
    return (
        query.join(SignalDecision, Signal.decision_id == SignalDecision.id)
        .join(StrategyVersion, SignalDecision.strategy_version_id == StrategyVersion.id)
        .where(
            SignalDecision.eligibility_status == "eligible_for_review",
            SignalDecision.lineage_complete.is_(True),
            StrategyVersion.version == active.promoted_strategy_version,
            StrategyVersion.source_revision == active.source_revision,
        )
    )


def _content_sha256(payload: dict) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def registration_content_sha256(registration: ExperimentRegistration) -> str:
    return _content_sha256({
        "strategy_version_id": registration.strategy_version_id,
        "market_definition_id": registration.market_definition_id,
        "version": registration.version,
        "status": registration.status,
        "min_distinct_fixtures": registration.min_distinct_fixtures,
        "min_settled_predictions": registration.min_settled_predictions,
        "min_calendar_days": registration.min_calendar_days,
        "max_ece": registration.max_ece,
        "max_brier_score": registration.max_brier_score,
        "max_drawdown_pct": registration.max_drawdown_pct,
        "min_roi_lower_bound": registration.min_roi_lower_bound,
        "max_concentration_pct": registration.max_concentration_pct,
        "resampling_seed": registration.resampling_seed,
        "resampling_repetitions": registration.resampling_repetitions,
        "costs": json.loads(registration.costs_json),
        "risk_limits": json.loads(registration.risk_limits_json),
    })


def evaluation_content_sha256(evaluation: ExperimentEvaluation) -> str:
    return _content_sha256({
        "registration_id": evaluation.registration_id,
        "metrics": json.loads(evaluation.metrics_json),
        "evaluation_started_at": _utc(evaluation.evaluation_started_at).isoformat(),
        "evaluation_ended_at": _utc(evaluation.evaluation_ended_at).isoformat(),
        "evidence_manifest_sha256": evaluation.evidence_manifest_sha256,
        "evaluator_source_revision": evaluation.evaluator_source_revision,
    })


def review_content_sha256(review: PromotionReview) -> str:
    return _content_sha256({
        "evaluation_id": review.evaluation_id,
        "decision": review.decision,
        "reviewer_identity": review.reviewer_identity,
        "rationale": review.rationale,
    })


def configuration_readiness(settings: Settings) -> ReadinessResult:
    reasons: list[str] = []
    if not settings.publication_enabled:
        reasons.append("PUBLICATION_DISABLED")
    if not settings.promoted_strategy_version:
        reasons.append("MISSING_PROMOTED_STRATEGY_VERSION")
    revision = settings.source_revision.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{12,64}", revision):
        reasons.append("MISSING_EXACT_SOURCE_REVISION")
    return ReadinessResult(not reasons, tuple(reasons))


def evaluate_registration(
    registration: ExperimentRegistration,
    evaluation: ExperimentEvaluation | None = None,
    review: PromotionReview | None = None,
) -> ReadinessResult:
    reasons: list[str] = []
    if registration.status != "frozen":
        reasons.append("EXPERIMENT_NOT_FROZEN")
    if registration.resampling_repetitions < 1000:
        reasons.append("INSUFFICIENT_RESAMPLING_REPETITIONS")
    for field, value in (
        ("costs_json", registration.costs_json),
        ("risk_limits_json", registration.risk_limits_json),
    ):
        try:
            decoded = json.loads(value)
            if not isinstance(decoded, dict) or not decoded:
                raise ValueError
        except (TypeError, ValueError):
            reasons.append(f"INVALID_{field.upper()}")
    try:
        risk_limits = json.loads(registration.risk_limits_json)
        family_size = int(risk_limits.get("family_size", 1))
        if family_size > 1 and risk_limits.get("multiple_testing_method") != "bonferroni":
            reasons.append("MULTIPLE_TESTING_CONTROL_MISSING")
    except (TypeError, ValueError, AttributeError):
        pass
    try:
        if registration.content_sha256 != registration_content_sha256(registration):
            reasons.append("REGISTRATION_HASH_MISMATCH")
    except (TypeError, ValueError):
        if "INVALID_COSTS_JSON" not in reasons and "INVALID_RISK_LIMITS_JSON" not in reasons:
            reasons.append("INVALID_REGISTRATION_CONTENT")
    if evaluation is None:
        reasons.append("MISSING_EVALUATION_REPORT")
    else:
        if _utc(evaluation.evaluation_started_at) < _utc(registration.registered_at):
            reasons.append("EVALUATION_PRECEDES_REGISTRATION")
        if _utc(evaluation.evaluation_ended_at) < _utc(evaluation.evaluation_started_at):
            reasons.append("INVALID_EVALUATION_WINDOW")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", evaluation.evidence_manifest_sha256):
            reasons.append("INVALID_EVIDENCE_MANIFEST_SHA")
        if not re.fullmatch(r"[0-9a-fA-F]{12,64}", evaluation.evaluator_source_revision):
            reasons.append("INVALID_EVALUATOR_SOURCE_REVISION")
        try:
            if evaluation.content_sha256 != evaluation_content_sha256(evaluation):
                reasons.append("EVALUATION_HASH_MISMATCH")
        except (TypeError, ValueError):
            reasons.append("INVALID_EVALUATION_CONTENT")
    if review is None or review.decision != "approved":
        reasons.append("MISSING_APPROVED_PROMOTION_REVIEW")
    elif not review.reviewer_identity.strip():
        reasons.append("MISSING_REVIEWER_IDENTITY")
    else:
        if evaluation is not None and _utc(review.reviewed_at) < _utc(evaluation.evaluation_ended_at):
            reasons.append("REVIEW_PRECEDES_EVALUATION")
        if review.content_sha256 != review_content_sha256(review):
            reasons.append("REVIEW_HASH_MISMATCH")
    try:
        metrics = json.loads(evaluation.metrics_json if evaluation else "{}")
    except (TypeError, ValueError):
        metrics = {}
        reasons.append("INVALID_METRICS_JSON")
    required = {
        "settled_count", "distinct_fixtures", "calendar_days", "ece",
        "brier_score", "max_drawdown_pct", "roi_lower_bound",
        "max_concentration_pct", "excluded_count", "lineage_complete_count",
    }
    missing = sorted(required - metrics.keys())
    if missing:
        reasons.append("MISSING_METRICS:" + ",".join(missing))
    else:
        try:
            settled = int(metrics["settled_count"])
            if settled < registration.min_settled_predictions:
                reasons.append("INSUFFICIENT_SETTLED_SAMPLE")
            if int(metrics["distinct_fixtures"]) < registration.min_distinct_fixtures:
                reasons.append("INSUFFICIENT_DISTINCT_FIXTURES")
            if int(metrics["calendar_days"]) < registration.min_calendar_days:
                reasons.append("INSUFFICIENT_CALENDAR_SPAN")
            if float(metrics["ece"]) > registration.max_ece:
                reasons.append("CALIBRATION_GATE_FAILED")
            if float(metrics["brier_score"]) > registration.max_brier_score:
                reasons.append("BRIER_GATE_FAILED")
            if float(metrics["max_drawdown_pct"]) > registration.max_drawdown_pct:
                reasons.append("DRAWDOWN_GATE_FAILED")
            if float(metrics["roi_lower_bound"]) <= registration.min_roi_lower_bound:
                reasons.append("ROI_CONFIDENCE_GATE_FAILED")
            if float(metrics["max_concentration_pct"]) > registration.max_concentration_pct:
                reasons.append("CONCENTRATION_GATE_FAILED")
            if int(metrics["excluded_count"]) != 0:
                reasons.append("EXCLUDED_EVIDENCE_PRESENT")
            if int(metrics["lineage_complete_count"]) != settled:
                reasons.append("INCOMPLETE_LINEAGE")
        except (TypeError, ValueError):
            reasons.append("INVALID_METRIC_VALUES")
    return ReadinessResult(not reasons, tuple(reasons), registration.id)


async def publication_readiness(
    db: AsyncSession, *, market_key: str | None = None
) -> ReadinessResult:
    configured = configuration_readiness(get_settings())
    if not configured.ready:
        return configured
    settings = get_settings()
    try:
        strategy = await db.scalar(
            select(StrategyVersion).where(
                StrategyVersion.version == settings.promoted_strategy_version,
                StrategyVersion.source_revision == settings.source_revision,
            )
        )
        if strategy is None:
            return ReadinessResult(False, ("PROMOTED_STRATEGY_NOT_REGISTERED",))
        configured_markets = []
        try:
            configured_markets = json.loads(strategy.config_json).get("prospective_markets", [])
        except (TypeError, ValueError, AttributeError):
            configured_markets = []
        required_markets = [market_key] if market_key else ["__portfolio__", *configured_markets]
        registrations = []
        for required_market in dict.fromkeys(required_markets):
            query = select(ExperimentRegistration).join(
                MarketDefinition,
                ExperimentRegistration.market_definition_id == MarketDefinition.id,
            ).where(
                ExperimentRegistration.strategy_version_id == strategy.id,
                MarketDefinition.canonical_key == required_market,
            )
            registration = await db.scalar(query.order_by(
                ExperimentRegistration.registered_at.desc(), ExperimentRegistration.id.desc()
            ).limit(1))
            if registration is None:
                return ReadinessResult(False, (f"MISSING_REGISTERED_EXPERIMENT:{required_market}",))
            registrations.append((required_market, registration))
    except Exception:
        return ReadinessResult(False, ("READINESS_SCHEMA_UNAVAILABLE",))
    evaluated = []
    for required_market, registration in registrations:
        result = await _evaluate_persisted_registration(db, registration)
        if not result.ready:
            prefix = "" if required_market == "__portfolio__" else f"{required_market}:"
            result = ReadinessResult(
                False,
                tuple(f"{prefix}{reason}" for reason in result.reasons),
                registration.id,
            )
        evaluated.append(result)
    reasons = tuple(dict.fromkeys(r for item in evaluated for r in item.reasons))
    if not reasons:
        portfolio = next(
            (result for (key, _registration), result in zip(registrations, evaluated)
             if key == "__portfolio__"),
            evaluated[0],
        )
        return portfolio
    return ReadinessResult(False, reasons)


async def registered_market_readiness(
    db: AsyncSession, market_key: str
) -> ReadinessResult:
    """Evaluate the latest frozen registration without publication config shortcuts."""
    try:
        registration = await db.scalar(
            select(ExperimentRegistration)
            .join(
                MarketDefinition,
                ExperimentRegistration.market_definition_id == MarketDefinition.id,
            )
            .where(MarketDefinition.canonical_key == market_key)
            .order_by(
                ExperimentRegistration.registered_at.desc(),
                ExperimentRegistration.id.desc(),
            )
            .limit(1)
        )
    except Exception:
        return ReadinessResult(False, ("READINESS_SCHEMA_UNAVAILABLE",))
    if registration is None:
        return ReadinessResult(False, ("MISSING_REGISTERED_EXPERIMENT",))
    return await _evaluate_persisted_registration(db, registration)


async def _evaluate_persisted_registration(
    db: AsyncSession, registration: ExperimentRegistration
) -> ReadinessResult:
    strategy = await db.get(StrategyVersion, registration.strategy_version_id)
    evaluation = await db.scalar(
        select(ExperimentEvaluation)
        .where(ExperimentEvaluation.registration_id == registration.id)
        .order_by(
            ExperimentEvaluation.evaluated_at.desc(),
            ExperimentEvaluation.id.desc(),
        )
        .limit(1)
    )
    review = None
    if evaluation is not None:
        review = await db.scalar(
            select(PromotionReview)
            .where(PromotionReview.evaluation_id == evaluation.id)
            .order_by(PromotionReview.reviewed_at.desc(), PromotionReview.id.desc())
            .limit(1)
        )
    result = evaluate_registration(registration, evaluation, review)
    if (
        evaluation is not None
        and strategy is not None
        and evaluation.evaluator_source_revision != strategy.source_revision
    ):
        return ReadinessResult(
            False,
            tuple(dict.fromkeys((*result.reasons, "EVALUATOR_SOURCE_REVISION_MISMATCH"))),
            registration.id,
        )
    return result


async def require_publication_ready(db: AsyncSession) -> None:
    result = await publication_readiness(db)
    if not result.ready:
        raise HTTPException(
            status_code=503,
            detail={"code": "RESEARCH_ONLY", "reasons": list(result.reasons)},
        )

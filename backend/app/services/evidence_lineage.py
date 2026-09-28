"""Fail-closed evidence classification and immutable decision lineage."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    EvidenceExclusion,
    FeatureSnapshot,
    FixtureRevision,
    MarketDefinition,
    ModelVersion,
    OddsQuote,
    SignalDecision,
    StrategyVersion,
)
from app.services.legacy_evidence_importer import content_sha256

AUDIT_VERSION = "prospective-eligibility-v1"
MARKET_VERSION = "canonical-v1"


def utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


@dataclass(frozen=True)
class SnapshotAssessment:
    evidence_class: str
    reasons: tuple[str, ...]

    @property
    def prospective(self) -> bool:
        return not self.reasons


def assess_snapshot(
    *,
    fixture_revision: FixtureRevision,
    as_of: datetime,
    required_quotes: Iterable[OddsQuote | None],
) -> SnapshotAssessment:
    """Classify from fixture-level time/status and every required quote."""
    reasons: list[str] = []
    decision_time = utc(as_of)
    kickoff = utc(fixture_revision.kickoff_at) if fixture_revision.kickoff_at else None
    if kickoff is None:
        reasons.append("MISSING_KICKOFF")
    elif decision_time >= kickoff:
        reasons.append("SNAPSHOT_AT_OR_AFTER_KICKOFF")
    revision_received = utc(fixture_revision.received_at)
    if revision_received > decision_time:
        reasons.append("FIXTURE_REVISION_AFTER_DECISION")
    if kickoff is not None and revision_received >= kickoff:
        reasons.append("FIXTURE_REVISION_AT_OR_AFTER_KICKOFF")
    if (fixture_revision.status or "").strip().upper() != "NS":
        reasons.append("FIXTURE_NOT_SCHEDULED")

    quotes = list(required_quotes)
    if not quotes or any(quote is None for quote in quotes):
        reasons.append("MISSING_REQUIRED_QUOTE")
    for quote in (q for q in quotes if q is not None):
        received = utc(quote.received_at)
        if received > decision_time:
            reasons.append("QUOTE_RECEIVED_AFTER_DECISION")
        if kickoff is not None and received >= kickoff:
            reasons.append("QUOTE_RECEIVED_AT_OR_AFTER_KICKOFF")

    unique = tuple(dict.fromkeys(reasons))
    return SnapshotAssessment(
        evidence_class="prospective" if not unique else "research_excluded",
        reasons=unique,
    )


async def record_snapshot_exclusions(
    db: AsyncSession,
    *,
    snapshot: FeatureSnapshot,
    assessment: SnapshotAssessment,
    detected_at: datetime,
) -> None:
    for reason in assessment.reasons:
        existing = await db.scalar(
            select(EvidenceExclusion.id).where(
                EvidenceExclusion.evidence_type == "feature_snapshot",
                EvidenceExclusion.evidence_id == snapshot.id,
                EvidenceExclusion.reason_code == reason,
            )
        )
        if existing is None:
            db.add(
                EvidenceExclusion(
                    evidence_type="feature_snapshot",
                    evidence_id=snapshot.id,
                    reason_code=reason,
                    details_json=json.dumps(
                        {"evidence_class": snapshot.evidence_class},
                        sort_keys=True,
                    ),
                    audit_version=AUDIT_VERSION,
                    detected_at=utc(detected_at).replace(tzinfo=None),
                )
            )


async def get_or_create_market_definition(
    db: AsyncSession, market_key: str
) -> MarketDefinition:
    existing = await db.scalar(
        select(MarketDefinition).where(
            MarketDefinition.canonical_key == market_key,
            MarketDefinition.version == MARKET_VERSION,
        )
    )
    if existing is not None:
        return existing
    definition = MarketDefinition(
        canonical_key=market_key,
        version=MARKET_VERSION,
        settlement_rules=json.dumps(
            {
                "contract": "legacy-market-settlement-v1",
                "market": market_key,
                "includes_extra_time": False,
            },
            sort_keys=True,
        ),
    )
    db.add(definition)
    await db.flush()
    return definition


async def get_or_create_strategy_version(
    db: AsyncSession,
    *,
    name: str,
    version: str,
    source_revision: str,
    config: dict,
) -> StrategyVersion:
    config_json = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    config_hash = content_sha256(json.loads(config_json))
    existing = await db.scalar(
        select(StrategyVersion).where(
            StrategyVersion.name == name,
            StrategyVersion.version == version,
            StrategyVersion.config_sha256 == config_hash,
        )
    )
    if existing is not None:
        return existing
    strategy = StrategyVersion(
        name=name,
        version=version,
        source_revision=source_revision or "unversioned",
        config_sha256=config_hash,
        config_json=config_json,
        status="research",
    )
    db.add(strategy)
    await db.flush()
    return strategy


async def create_signal_decision(
    db: AsyncSession,
    *,
    fixture_id: int,
    market_key: str,
    computed_at: datetime,
    snapshot: FeatureSnapshot,
    model_version: ModelVersion,
    strategy_version: StrategyVersion,
    market_definition: MarketDefinition,
    executable_quote: OddsQuote | None,
    assessment: SnapshotAssessment,
) -> SignalDecision:
    reasons = list(assessment.reasons)
    if executable_quote is None:
        reasons.append("MISSING_EXECUTABLE_QUOTE")
    if not re.fullmatch(
        r"[0-9a-fA-F]{12,64}", (model_version.source_revision or "").strip()
    ):
        reasons.append("UNVERSIONED_MODEL_SOURCE")
    if not re.fullmatch(
        r"[0-9a-fA-F]{12,64}", (strategy_version.source_revision or "").strip()
    ):
        reasons.append("UNVERSIONED_STRATEGY_SOURCE")
    reasons = list(dict.fromkeys(reasons))
    payload = {
        "fixture_id": fixture_id,
        "market_key": market_key,
        "computed_at": utc(computed_at).replace(tzinfo=None).isoformat(),
        "feature_snapshot_id": snapshot.id,
        "model_version_id": model_version.id,
        "strategy_version_id": strategy_version.id,
        "market_definition_id": market_definition.id,
        "executable_quote_id": executable_quote.id if executable_quote else None,
        "eligibility_reasons": reasons,
    }
    decision = SignalDecision(
        fixture_id=fixture_id,
        feature_snapshot_id=snapshot.id,
        model_version_id=model_version.id,
        strategy_version_id=strategy_version.id,
        market_definition_id=market_definition.id,
        executable_quote_id=executable_quote.id if executable_quote else None,
        market_key=market_key,
        computed_at=utc(computed_at).replace(tzinfo=None),
        eligibility_status="eligible_for_review" if not reasons else "research_only",
        eligibility_reason=",".join(reasons) if reasons else None,
        content_sha256=content_sha256(payload),
        lineage_complete=not reasons,
    )
    db.add(decision)
    await db.flush()
    return decision

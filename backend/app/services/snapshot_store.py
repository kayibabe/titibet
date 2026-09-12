"""Persistence helpers for immutable model and feature snapshots."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FeatureSnapshot, FixtureRevision, ModelVersion
from app.services.legacy_evidence_importer import content_sha256


def _utc_naive(value: datetime) -> datetime:
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return aware.replace(tzinfo=None)


async def get_or_create_model_version(
    db: AsyncSession,
    *,
    name: str,
    version: str,
    config: dict[str, Any],
    evidence_class: str,
) -> ModelVersion:
    config_hash = content_sha256(config)
    existing = await db.scalar(
        select(ModelVersion).where(
            ModelVersion.name == name,
            ModelVersion.version == version,
            ModelVersion.config_sha256 == config_hash,
        )
    )
    if existing is not None:
        return existing
    model = ModelVersion(
        name=name,
        version=version,
        source_revision="working-tree",
        config_sha256=config_hash,
        parameters_json=json.dumps(config, sort_keys=True, separators=(",", ":"), default=str),
        evidence_class=evidence_class,
    )
    db.add(model)
    await db.flush()
    return model


async def save_feature_snapshot(
    db: AsyncSession,
    *,
    fixture_revision: FixtureRevision,
    model_version: ModelVersion,
    as_of: datetime,
    features: dict[str, Any],
    input_refs: dict[str, Any],
    transform_version: str,
    evidence_class: str,
) -> FeatureSnapshot:
    """Insert one content-addressed snapshot, returning an existing equal row."""
    features_json = json.dumps(features, sort_keys=True, separators=(",", ":"), default=str)
    refs_json = json.dumps(input_refs, sort_keys=True, separators=(",", ":"), default=str)
    digest = content_sha256({
        "fixture_revision_id": fixture_revision.id,
        "as_of": _utc_naive(as_of).isoformat(),
        "features": json.loads(features_json),
        "input_refs": json.loads(refs_json),
        "transform_version": transform_version,
    })
    existing = await db.scalar(
        select(FeatureSnapshot).where(FeatureSnapshot.content_sha256 == digest)
    )
    if existing is not None:
        return existing
    snapshot = FeatureSnapshot(
        fixture_id=fixture_revision.fixture_id,
        fixture_revision_id=fixture_revision.id,
        model_version_id=model_version.id,
        as_of=_utc_naive(as_of),
        features_json=features_json,
        input_refs_json=refs_json,
        transform_version=transform_version,
        content_sha256=digest,
        evidence_class=evidence_class,
    )
    db.add(snapshot)
    await db.flush()
    return snapshot

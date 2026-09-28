from sqlalchemy import select

import pytest

from app.models import ExperimentRegistration
from app.services.promotion_workflow import (
    approve_evaluation,
    evaluate_registered_experiment,
)


@pytest.mark.asyncio
async def test_evaluation_is_persisted_but_empty_prospective_evidence_cannot_be_approved(db):
    registration = await db.scalar(select(ExperimentRegistration))
    evaluation = await evaluate_registered_experiment(db, registration.id)

    assert evaluation.registration_id == registration.id
    assert evaluation.evidence_manifest_sha256
    assert evaluation.content_sha256
    assert '"settled_count":0' in evaluation.metrics_json

    with pytest.raises(ValueError, match="does not satisfy promotion gates"):
        await approve_evaluation(
            db,
            evaluation.id,
            reviewer_identity="admin@example.test",
            rationale="insufficient prospective sample",
            decision="approved",
        )

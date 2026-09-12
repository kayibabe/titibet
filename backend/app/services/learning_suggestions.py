"""Persist diagnostic suggestions without changing any active production rule."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.learning_proposal import LearningProposal


async def save_suggestion(db: AsyncSession, proposal: LearningProposal) -> LearningProposal:
    proposal.is_active = False
    # Repeated scheduled diagnostics should not flood the review queue with the
    # same suggestion. Changed evidence remains a separate record.
    existing = await db.scalar(
        select(LearningProposal).where(
            LearningProposal.change_type == proposal.change_type,
            LearningProposal.target == proposal.target,
            LearningProposal.proposed_value == proposal.proposed_value,
            LearningProposal.rationale == proposal.rationale,
            LearningProposal.backtest_note == proposal.backtest_note,
            LearningProposal.confidence == proposal.confidence,
        ).order_by(LearningProposal.id.desc()).limit(1)
    )
    if existing is not None:
        return existing  # Never deactivate an existing active record.
    db.add(proposal)
    await db.flush()
    return proposal

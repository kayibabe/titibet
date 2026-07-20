"""
loss_analysis.py — API endpoints for AI-powered loss analysis and self-learning.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.auth import get_current_user, get_current_user_optional
from app.models.user import User
from app.services.loss_analysis_agent import (
    run_loss_analysis_pipeline,
    get_loss_analysis_summary,
)

router = APIRouter(prefix="/api/loss-analysis", tags=["loss-analysis"])


@router.get("/summary")
async def get_summary(
    lookback_days: int = Query(default=30, ge=7, le=180),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """
    Return aggregated loss analysis summary for the analytics dashboard.
    No LLM calls — pure DB read of existing LossAnalysis rows.
    """
    return await get_loss_analysis_summary(db, lookback_days=lookback_days)


@router.post("/run")
async def trigger_pipeline(
    lookback_days: int = Query(default=90, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually trigger the full four-agent loss analysis pipeline.
    Analyses unanalysed losses, detects patterns, proposes threshold changes.
    Requires authentication. May take 10-30 seconds when Groq is active.
    """
    report = await run_loss_analysis_pipeline(
        db,
        user_id=current_user.id,
        lookback_days=lookback_days,
    )
    return {
        "bets_analysed": report.bets_analysed,
        "patterns_detected": report.patterns_detected,
        "threshold_proposals": report.threshold_proposals,
        "accepted_proposals": report.accepted_proposals,
        "skipped_proposals": report.skipped_proposals,
        "errors": report.errors,
    }

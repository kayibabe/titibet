from __future__ import annotations

import time
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_optional
from app.core.database import get_db
from app.models.user import User
from app.services.advisor_service import get_advisor_insights, track_acca_for_user

router = APIRouter(prefix="/api/advisor", tags=["advisor"])

# force=True re-runs the full AI pipeline (4 LLM calls) on the paid provider
# chain — throttle it per user so a subscriber can't hammer the quota.
_FORCE_COOLDOWN_SECONDS = 60
_last_force_at: dict[int, float] = {}


def _require_pro(current_user: Optional[User]) -> User:
    if (
        current_user is None
        or current_user.tier not in ("pro", "elite")
        or current_user.subscription_status != "active"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI Advisory requires a Pro or Elite subscription.",
        )
    return current_user


def _parse_date(date_str: Optional[str]) -> date:
    try:
        return date.fromisoformat(date_str) if date_str else date.today()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid date — expected YYYY-MM-DD.",
        )


@router.get("")
async def advisor_insights(
    date_str:    Optional[str]  = Query(None, alias="date"),
    fixture_ids: Optional[str]  = Query(None, description="Comma-separated fixture IDs to limit analysis"),
    force:       bool           = Query(False, description="Bypass cache and re-run AI pipeline"),
    db:          AsyncSession   = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Run the AI advisory council for a given date.

    Tries each configured provider in order (Claude → Gemini → Cerebras → Groq → Mistral)
    and runs Scout, Strategist, Skeptic concurrently. At least one provider key must be set
    in backend/.env; returns a setup message when none are configured.
    """
    current_user = _require_pro(current_user)
    target_date = _parse_date(date_str)

    if force:
        now = time.monotonic()
        last = _last_force_at.get(current_user.id)
        if last is not None and now - last < _FORCE_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Refresh is rate-limited — try again in a minute.",
            )
        _last_force_at[current_user.id] = now

    ids = [int(i) for i in fixture_ids.split(",") if i.strip().isdigit()] if fixture_ids else None
    return await get_advisor_insights(db, target_date, fixture_ids=ids, current_user=current_user, force=force)


@router.post("/track-acca")
async def track_acca(
    date_str:     Optional[str]  = Query(None, alias="date"),
    db:           AsyncSession   = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Add the day's AI acca to the current user's bet tracker (opt-in — viewing
    the advisory never tracks). Idempotent: one acca row per user per date.
    """
    current_user = _require_pro(current_user)
    target_date = _parse_date(date_str)

    result = await track_acca_for_user(db, target_date, current_user)
    if not result.get("tracked"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.get("message") or "No accumulator available to track.",
        )
    return result

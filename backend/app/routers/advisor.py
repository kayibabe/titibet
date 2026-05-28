from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_optional
from app.core.database import get_db
from app.models.user import User
from app.services.advisor_service import get_advisor_insights

router = APIRouter(prefix="/api/advisor", tags=["advisor"])


@router.get("")
async def advisor_insights(
    date_str:    Optional[str] = Query(None, alias="date"),
    fixture_ids: Optional[str] = Query(None, description="Comma-separated fixture IDs to limit analysis"),
    db:          AsyncSession  = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Run the AI advisory council for a given date.

    Tries each configured provider in order (Claude → Gemini → Cerebras → Groq → Mistral)
    and runs Scout, Strategist, Skeptic concurrently. At least one provider key must be set
    in backend/.env; returns a setup message when none are configured.
    """
    # Require Pro or Elite subscription
    is_pro = (
        current_user is not None
        and current_user.tier in ("pro", "elite")
        and current_user.subscription_status == "active"
    )
    if not is_pro:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI Advisory requires a Pro or Elite subscription.",
        )

    target_date = date.fromisoformat(date_str) if date_str else date.today()
    ids = [int(i) for i in fixture_ids.split(",") if i.strip().isdigit()] if fixture_ids else None
    return await get_advisor_insights(db, target_date, fixture_ids=ids, current_user=current_user)

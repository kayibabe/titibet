from __future__ import annotations

import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_optional
from app.core.database import get_db
from app.models.bet import TrackedBet
from app.models.user import User
from app.services.acca_builder import build_acca_candidates, build_accumulator

router = APIRouter(prefix="/api/accumulators", tags=["accumulators"])

ACCUMULATOR_TIERS = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
_FREE_LEG_LIMIT = 2


def _gate_legs(legs: list[dict], is_pro: bool) -> list[dict]:
    if is_pro:
        return legs
    return [
        {**leg, "locked": i >= _FREE_LEG_LIMIT}
        for i, leg in enumerate(legs)
    ]


async def _load_tracked_acca(db: AsyncSession, target_date: date) -> dict | None:
    """
    Return the most-recently auto-tracked system_acca ticket for target_date
    reconstructed as a build_accumulator()-compatible dict, or None if absent.
    """
    row = await db.scalar(
        select(TrackedBet)
        .where(
            TrackedBet.source_rule_key == "system_acca",
            TrackedBet.event_date == target_date,
            TrackedBet.user_id.is_(None),
        )
        .order_by(TrackedBet.id.desc())
        .limit(1)
    )
    if not row or not row.notes:
        return None
    try:
        notes = json.loads(row.notes)
        legs = notes.get("legs") or []
        if not legs:
            return None
        return {
            "target_odds":              row.odds,
            "combined_odds":            row.odds,
            "legs":                     legs,
            "leg_count":                len(legs),
            "expected_win_probability": notes.get("expected_win_probability", 0.0),
            "insufficient_picks":       False,
            "tracked":                  True,
            "result_status":            row.result_status,
        }
    except Exception:
        return None


@router.get("")
async def get_accumulators(
    date_str: Optional[str] = Query(None, alias="date"),
    target_odds: Optional[float] = Query(None, description="Single tier. If omitted, returns all 6 tiers."),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    target_date = date.fromisoformat(date_str) if date_str else date.today()

    is_pro = (
        current_user is not None
        and current_user.tier in ("pro", "elite")
        and current_user.subscription_status == "active"
    )

    # Prefer the already-tracked system_acca over a live recomputation.
    # The tracker and the display card must show the same ticket — live
    # recomputation diverges as odds shift after the sync that created the bet.
    tracked = await _load_tracked_acca(db, target_date)
    if tracked:
        tracked["legs"] = _gate_legs(tracked["legs"], is_pro)
        if target_odds is not None:
            tracked["date"] = str(target_date)
            return tracked
        empty = {
            "target_odds": 0.0, "combined_odds": 1.0, "legs": [],
            "leg_count": 0, "expected_win_probability": 0.0, "insufficient_picks": True,
        }
        tiers: dict[str, dict] = {str(t): {**empty, "target_odds": t} for t in ACCUMULATOR_TIERS}
        # Slot the tracked ticket at its nearest tier so pickBestTicket finds it.
        best_key = str(min(ACCUMULATOR_TIERS, key=lambda t: abs(t - tracked["combined_odds"])))
        tiers[best_key] = tracked
        return {
            "date": str(target_date),
            "tiers": tiers,
            "total_qualifying": tracked["leg_count"],
        }

    # No tracked acca yet — build live from current signal candidates.
    candidates = await build_acca_candidates(db, target_date)

    if target_odds is not None:
        acc = build_accumulator(candidates, target_odds)
        acc["legs"] = _gate_legs(acc["legs"], is_pro)
        acc["date"] = str(target_date)
        return acc

    tiers = {}
    for t in ACCUMULATOR_TIERS:
        acc = build_accumulator(candidates, t)
        acc["legs"] = _gate_legs(acc["legs"], is_pro)
        tiers[str(t)] = acc

    return {
        "date": str(target_date),
        "tiers": tiers,
        "total_qualifying": len(candidates),
    }

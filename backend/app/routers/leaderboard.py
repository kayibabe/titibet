from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import TrackedBet
from app.models.user import User

router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])

_MIN_BETS = 5  # minimum settled bets to appear on leaderboard


def _pseudonym(user_id: int, email: str | None) -> str:
    prefix = (email or "")[:2].upper() or "??"
    return f"{prefix}•{user_id:04d}"


@router.get("")
async def leaderboard(db: AsyncSession = Depends(get_db)):
    """
    Public leaderboard of user betting performance (pseudonymous).
    Requires at least {MIN_BETS} settled bets to qualify.
    Ranked by win rate, tie-broken by total bets.
    """
    bets = (
        await db.execute(
            select(TrackedBet).where(
                TrackedBet.user_id.is_not(None),
                TrackedBet.result_status.in_(["Won", "Lost"]),
            )
        )
    ).scalars().all()

    stats: dict[int, dict] = defaultdict(lambda: {"wins": 0, "total": 0, "roi_sum": 0.0, "stake_sum": 0.0})
    for bet in bets:
        s = stats[bet.user_id]
        s["total"] += 1
        if bet.result_status == "Won":
            s["wins"] += 1
        s["roi_sum"] += bet.profit_loss or 0.0
        s["stake_sum"] += bet.stake or 0.0

    # Fetch user emails for pseudonym generation (one query)
    user_ids = list(stats.keys())
    users_map: dict[int, str] = {}
    if user_ids:
        users = (
            await db.execute(select(User.id, User.email).where(User.id.in_(user_ids)))
        ).all()
        users_map = {uid: email for uid, email in users}

    rows = []
    for uid, s in stats.items():
        if s["total"] < _MIN_BETS:
            continue
        win_rate = round(s["wins"] / s["total"] * 100, 1)
        roi = round(s["roi_sum"] / s["stake_sum"] * 100, 1) if s["stake_sum"] > 0 else 0.0
        rows.append({
            "user_id": uid,
            "name": _pseudonym(uid, users_map.get(uid)),
            "bets": s["total"],
            "wins": s["wins"],
            "win_rate": win_rate,
            "roi": roi,
        })

    rows.sort(key=lambda r: (-r["win_rate"], -r["bets"]))
    for i, r in enumerate(rows[:20], 1):
        r["rank"] = i

    return {"min_bets": _MIN_BETS, "entries": rows[:20]}

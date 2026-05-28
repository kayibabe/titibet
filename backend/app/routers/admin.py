from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.models.learning_proposal import LearningProposal
import httpx

from app.core.config import get_settings
from app.services.api_client import get_quota_info
from app.services.settlement import refresh_stale_fixtures_and_settle
from app.services.loss_analysis_agent import run_loss_analysis_pipeline
from app.services.strategy_pipeline import run_strategy_pipeline
from app.services.telegram import (
    _send_to as telegram_send_to,
    push_titibet_tickets as telegram_push_titibet,
    push_results_report as telegram_push_results,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.tier != "elite":
        raise HTTPException(status_code=403, detail="Admin access requires Elite tier")
    return current_user


class UserAdminOut(BaseModel):
    id: int
    email: str
    name: Optional[str]
    tier: str
    subscription_status: str
    subscription_expires_at: Optional[datetime]
    is_active: bool
    created_at: datetime
    model_config = {"from_attributes": True}


class UserAdminUpdate(BaseModel):
    tier: Optional[str] = None
    subscription_status: Optional[str] = None
    is_active: Optional[bool] = None
    name: Optional[str] = None


class AdminStats(BaseModel):
    total_users: int
    active_subscriptions: int
    free_users: int
    pro_users: int
    elite_users: int


@router.get("/stats", response_model=AdminStats)
async def admin_stats(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    rows = await db.execute(select(User))
    users = list(rows.scalars().all())
    return AdminStats(
        total_users=len(users),
        active_subscriptions=sum(1 for u in users if u.subscription_status == "active"),
        free_users=sum(1 for u in users if u.tier == "free"),
        pro_users=sum(1 for u in users if u.tier == "pro"),
        elite_users=sum(1 for u in users if u.tier == "elite"),
    )


@router.get("/users", response_model=list[UserAdminOut])
async def list_users(
    search: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    q = select(User).order_by(User.created_at.desc()).limit(limit)
    if search:
        q = q.where(
            User.email.ilike(f"%{search}%") | User.name.ilike(f"%{search}%")
        )
    if tier:
        q = q.where(User.tier == tier)
    if status:
        q = q.where(User.subscription_status == status)
    result = await db.execute(q)
    return list(result.scalars().all())


@router.get("/users/{user_id}", response_model=UserAdminOut)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return user


@router.patch("/users/{user_id}", response_model=UserAdminOut)
async def update_user(
    user_id: int,
    body: UserAdminUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    VALID_TIERS = {"free", "pro", "elite"}
    VALID_STATUSES = {"inactive", "active", "cancelled", "past_due"}

    if body.tier is not None:
        if body.tier not in VALID_TIERS:
            raise HTTPException(400, f"tier must be one of {VALID_TIERS}")
        user.tier = body.tier
    if body.subscription_status is not None:
        if body.subscription_status not in VALID_STATUSES:
            raise HTTPException(400, f"subscription_status must be one of {VALID_STATUSES}")
        user.subscription_status = body.subscription_status
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.name is not None:
        user.name = body.name

    user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/settle")
async def trigger_settlement(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    # Batch-refresh stale fixtures by date (1 API call per date, not per fixture),
    # settle all pending bets, then run learning pipelines.
    # Returns: { refreshed_fixtures, settled, voided, errors, api_calls_made }
    result = await refresh_stale_fixtures_and_settle(db)
    result["quota"] = get_quota_info()

    if result["settled"] > 0 or result["voided"] > 0:
        try:
            await run_loss_analysis_pipeline(db)
        except Exception:
            pass
        try:
            await run_strategy_pipeline(db)
        except Exception:
            pass

    return result


@router.delete("/users/{user_id}")
async def deactivate_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    if user_id == admin.id:
        raise HTTPException(400, "Cannot deactivate your own account")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    user.is_active = False
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"deactivated": True, "user_id": user_id}


# ── Telegram diagnostics ──────────────────────────────────────────────────────

@router.get("/telegram/status")
async def telegram_status(_admin: User = Depends(_require_admin)):
    """Show Telegram config for the three named TiTiBet channels."""
    cfg = get_settings()
    return {
        "bot_token_set":   bool(cfg.telegram_bot_token),
        "general_chat_id": cfg.telegram_general_chat_id or None,
        "free_chat_id":    cfg.telegram_free_chat_id or None,
        "pro_chat_id":     cfg.telegram_pro_chat_id or None,
    }


@router.post("/telegram/test")
async def telegram_test(_admin: User = Depends(_require_admin)):
    """
    Send a test message to all three TiTiBet Telegram channels.
    Returns per-channel success/failure.
    """
    cfg = get_settings()

    if not cfg.telegram_bot_token:
        raise HTTPException(400, "TELEGRAM_BOT_TOKEN is not set in .env")

    channels = [
        ("TiTiBet General", cfg.telegram_general_chat_id),
        ("TiTiBet Free",    cfg.telegram_free_chat_id),
        ("TiTiBet Pro",     cfg.telegram_pro_chat_id),
    ]
    channels = [(label, cid) for label, cid in channels if cid]

    if not channels:
        raise HTTPException(400, "No Telegram channel IDs configured in .env")

    results = []
    for label, chat_id in channels:
        text = (
            f"<b>TiTiBet — Test message</b>\n\n"
            f"This is the <b>{label}</b> channel.\n"
            f"Signal digests will be pushed here after each daily sync."
        )
        ok = await telegram_send_to(chat_id, text)
        results.append({"label": label, "chat_id": chat_id, "sent": ok})

    return {"results": results}


@router.get("/telegram/preview")
async def telegram_preview(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """
    Preview what each TiTiBet channel would receive today, without sending.
    Returns per-channel pick list for the admin UI.
    """
    from datetime import date as _date
    from app.services.telegram import _query_all_rows, _best_per_fixture, _system_rank
    from app.services.recommended_tickets import load_titibet_tickets

    today = _date.today()
    cfg = get_settings()

    channels_config: list[tuple[str, str]] = []
    if cfg.telegram_general_chat_id:
        channels_config.append(("general", cfg.telegram_general_chat_id))
    if cfg.telegram_free_chat_id:
        channels_config.append(("free", cfg.telegram_free_chat_id))
    if cfg.telegram_pro_chat_id:
        channels_config.append(("pro", cfg.telegram_pro_chat_id))

    if not channels_config:
        return {"date": today.isoformat(), "channels": []}

    tickets = await load_titibet_tickets(db, today)
    all_rows = await _query_all_rows(db, today)
    general_rows = _best_per_fixture(all_rows)
    general_rows.sort(key=lambda r: _system_rank(r[0], r[1]), reverse=True)

    CHANNEL_META = {
        "general": {"label": "TiTiBet General", "emoji": "📋", "profile": "balanced",
                    "subtitle": "All signal matches · ranked by model confidence"},
        "free":    {"label": "TiTiBet Free",    "emoji": "🎯", "profile": "conservative",
                    "subtitle": "3 deterministic daily free picks"},
        "pro":     {"label": "TiTiBet Pro",     "emoji": "💎", "profile": "aggressive",
                    "subtitle": "Premium bundle · High Conf ACCA · Goals ACCA · Safe Ticket · Best Singles"},
    }

    result = []
    for channel_type, chat_id in channels_config:
        meta = CHANNEL_META.get(channel_type, {})
        all_picks: list[dict] = []

        if channel_type == "general":
            for sig, fix in general_rows:
                primary = max(
                    (v for v in [sig.bayesian_prob, sig.poisson_prob] if v is not None),
                    default=None,
                )
                all_picks.append({
                    "fixture":     f"{fix.home_team} vs {fix.away_team}",
                    "country":     fix.country,
                    "league":      fix.league,
                    "market":      sig.market,
                    "probability": primary,
                    "confidence":  sig.dual_confidence,
                })

        elif channel_type == "free":
            free_ticket = tickets.get("free") or {}
            for leg in (free_ticket.get("selected_legs") or []):
                all_picks.append({
                    "fixture":     leg.get("match_name", ""),
                    "country":     None,
                    "league":      leg.get("league", ""),
                    "market":      leg.get("market", ""),
                    "probability": leg.get("probability"),
                    "confidence":  leg.get("confidence"),
                })

        elif channel_type == "pro":
            pro_ticket = tickets.get("pro") or {}
            for sub in (pro_ticket.get("sub_tickets") or []):
                for leg in (sub.get("legs") or []):
                    all_picks.append({
                        "fixture":     leg.get("match_name", ""),
                        "country":     None,
                        "league":      leg.get("league", ""),
                        "market":      f"[{sub.get('label', sub.get('key', ''))}] {leg.get('market', '')}",
                        "probability": leg.get("probability"),
                        "confidence":  leg.get("confidence"),
                    })

        result.append({
            "label":      meta.get("label", channel_type),
            "emoji":      meta.get("emoji", "📢"),
            "profile":    meta.get("profile", "balanced"),
            "chat_id":    chat_id,
            "subtitle":   meta.get("subtitle", ""),
            "pick_count": len(all_picks),
            "picks":      all_picks[:10],
        })

    return {"date": today.isoformat(), "channels": result}


@router.post("/telegram/push")
async def telegram_push(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """Manually push today's TiTiBet tickets to all three Telegram channels."""
    from datetime import date as _date
    today = _date.today()
    sent  = await telegram_push_titibet(db, today)
    return {"sent": sent, "date": today.isoformat()}


@router.post("/telegram/push-results")
async def telegram_push_results_endpoint(
    date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format (defaults to today)"),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """
    Push results digest for a given date to all configured Telegram channels.
    Uses force=True so it sends even if results were already sent or not all
    games are finished (useful for manual admin override).
    Defaults to today when no date is provided.
    """
    from datetime import date as _date
    if date:
        try:
            target = _date.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, f"Invalid date format: {date!r}. Use YYYY-MM-DD.")
    else:
        target = _date.today()
    sent = await telegram_push_results(db, target, force=True)
    return {"sent": sent, "date": target.isoformat()}


# ── API-Football Quota ────────────────────────────────────────────────────────

@router.get("/quota")
async def api_quota(_admin: User = Depends(_require_admin)):
    """
    Return the current API-Football request quota snapshot.
    Updated in-memory after every live API call; resets at midnight UTC.
    """
    from datetime import date as _date
    info = get_quota_info()
    limit     = info.get("limit")
    remaining = info.get("remaining")
    pct_used  = None
    if limit and limit > 0 and remaining is not None:
        pct_used = round((limit - remaining) / limit * 100, 1)
    return {
        "limit":      limit,
        "remaining":  remaining,
        "pct_used":   pct_used,
        "reset_note": "Quota resets daily at midnight UTC",
        "date":       _date.today().isoformat(),
    }


# ── Learning Proposals ────────────────────────────────────────────────────────

@router.get("/learning-proposals")
async def list_learning_proposals(
    active_only: bool = Query(True),
    limit: int = Query(50),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """
    List learning proposals written by the self-learning pipelines.
    active_only=True (default) returns only currently applied proposals.
    active_only=False returns all proposals (history).
    """
    q = select(LearningProposal).order_by(desc(LearningProposal.created_at)).limit(limit)
    if active_only:
        q = q.where(LearningProposal.is_active == True)
    rows = list((await db.execute(q)).scalars().all())
    return [
        {
            "id":            r.id,
            "change_type":   r.change_type,
            "target":        r.target,
            "proposed_value": r.proposed_value,
            "rationale":     r.rationale,
            "confidence":    r.confidence,
            "backtest_note": r.backtest_note,
            "is_active":     r.is_active,
            "created_at":    r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/learning-proposals/{proposal_id}/deactivate")
async def deactivate_learning_proposal(
    proposal_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """Manually deactivate (override) a learning proposal."""
    proposal = await db.get(LearningProposal, proposal_id)
    if not proposal:
        raise HTTPException(404, "Proposal not found")
    proposal.is_active = False
    await db.commit()
    return {"deactivated": True, "id": proposal_id}


# ── Pipeline triggers ─────────────────────────────────────────────────────────

@router.post("/pipelines/loss-analysis")
async def trigger_loss_analysis(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """Manually trigger Pipeline A — Loss Analysis (threshold tuning from losses)."""
    report = await run_loss_analysis_pipeline(db)
    return {
        "pipeline": "A",
        "bets_analysed":       report.bets_analysed,
        "patterns_detected":   report.patterns_detected,
        "threshold_proposals": report.threshold_proposals,
        "accepted_proposals":  report.accepted_proposals,
        "skipped_proposals":   report.skipped_proposals,
        "errors":              report.errors,
    }


@router.post("/pipelines/strategy")
async def trigger_strategy_pipeline(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """Manually trigger Pipeline B — Strategy (market/league suppression + Kelly adj)."""
    report = await run_strategy_pipeline(db)
    return {
        "pipeline":            "B",
        "bets_analysed":       report.bets_analysed,
        "overall_win_rate":    report.overall_win_rate,
        "proposals_generated": report.proposals_generated,
        "proposals_accepted":  report.proposals_accepted,
        "error":               report.error,
    }

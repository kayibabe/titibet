from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.models.learning_proposal import LearningProposal
import httpx

from app.core.config import (
    get_settings,
    DISABLED_MARKETS,
    DISABLED_LEAGUES,
    OVER_GOALS_SUPPRESSED_LEAGUES,
    COPA_HO05_SUPPRESSED_LEAGUES,
    WOMEN_LEAGUE_KEYWORDS,
    DUAL_HIGH_ODDS_CEILING,
    POISSON_ONLY_MAX_ODDS,
    MARKET_MIN_ODDS,
    HO05_DATA_POOR_COUNTRIES,
    OVER25_SUPPRESSED_TIERS,
)
from app.services.api_client import get_quota_info
from app.services.settlement import refresh_stale_fixtures_and_settle
from app.services.loss_analysis_agent import run_loss_analysis_pipeline
from app.services.strategy_pipeline import run_strategy_pipeline
from app.services.league_watch_guard import get_watchlist_status, run_league_watch_guard
from app.services.telegram import (
    _send_to as telegram_send_to,
    push_results_report as telegram_push_results,
    push_morning_digest as telegram_push_morning_digest,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


class UserAdminOut(BaseModel):
    id: int
    email: str
    name: Optional[str]
    tier: str
    subscription_status: str
    subscription_expires_at: Optional[datetime]
    is_active: bool
    is_admin: bool = False
    created_at: datetime
    last_active_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


class UserAdminUpdate(BaseModel):
    tier: Optional[str] = None
    subscription_status: Optional[str] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None
    name: Optional[str] = None


class AdminStats(BaseModel):
    total_users: int
    active_subscriptions: int
    free_users: int
    pro_users: int


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

    VALID_TIERS = {"free", "pro"}
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
    if body.is_admin is not None:
        user.is_admin = body.is_admin
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
    """Show Telegram config for the two named TiTiBet channels."""
    cfg = get_settings()
    return {
        "bot_token_set":   bool(cfg.telegram_bot_token),
        "free_chat_id":    cfg.telegram_free_chat_id or None,
        "pro_chat_id":     cfg.telegram_pro_chat_id or None,
    }


@router.post("/telegram/test")
async def telegram_test(_admin: User = Depends(_require_admin)):
    """
    Send a test message to both TiTiBet Telegram channels.
    Returns per-channel success/failure.
    """
    cfg = get_settings()

    if not cfg.telegram_bot_token:
        raise HTTPException(400, "TELEGRAM_BOT_TOKEN is not set in .env")

    channels = [
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

    today = _date.today()
    cfg = get_settings()

    channels_config: list[tuple[str, str]] = []
    if cfg.telegram_free_chat_id:
        channels_config.append(("free", cfg.telegram_free_chat_id))
    if cfg.telegram_pro_chat_id:
        channels_config.append(("pro", cfg.telegram_pro_chat_id))

    if not channels_config:
        return {"date": today.isoformat(), "channels": []}

    all_rows = await _query_all_rows(db, today)
    ranked_rows = _best_per_fixture(all_rows)
    ranked_rows.sort(key=lambda r: _system_rank(r[0], r[1]), reverse=True)

    all_picks: list[dict] = []
    for sig, fix in ranked_rows:
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

    result = []
    for channel_type, chat_id in channels_config:
        result.append({
            "label":      f"TiTiBet {channel_type.title()}",
            "emoji":      "📋",
            "profile":    "balanced",
            "chat_id":    chat_id,
            "subtitle":   "All signal matches · ranked by model confidence",
            "pick_count": len(all_picks),
            "picks":      all_picks[:10],
        })

    return {"date": today.isoformat(), "channels": result}


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


@router.post("/telegram/push-digest")
async def telegram_push_digest_endpoint(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """
    Manually push today's signal digest to all configured Telegram channels.
    Useful for testing or forcing a send outside the scheduled 07:30/18:30 UTC windows.
    """
    n_sent = await telegram_push_morning_digest(db)
    return {"sent": n_sent, "message": f"Digest sent to {n_sent} channel(s)"}


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


# ── Autobet catchup ──────────────────────────────────────────────────────────

@router.post("/autobet-catchup")
async def autobet_catchup(
    date_from: str = Query("2026-06-01"),
    date_to: Optional[str] = Query(None, description="ISO date, inclusive (default: today)"),
    dry_run: bool = Query(False, description="Preview only — no DB writes"),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """
    Backfill system auto-picks for a date range.

    For each date:
      1. If 0 signals exist, recompute them from existing market snapshots.
      2. Create TrackedBet rows (user_id=None, source_rule_key='system_auto') for
         every Medium/High confidence non-contradiction signal, one per fixture slot.
      3. Skip dates/fixtures that already have a system pick.
    After all dates, run settlement so past matches resolve Won/Lost immediately.
    """
    import asyncio
    from datetime import date, timedelta
    from sqlalchemy import func as sqlfunc, or_
    from app.models import Signal, Fixture, TrackedBet
    from app.services.signal_engine import compute_signals_for_date
    from app.services.settlement import settle_bets_for_date
    from app.core.config import DISABLED_MARKETS, DISABLED_LEAGUES

    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to) if date_to else date.today()
    if end < start:
        raise HTTPException(400, "date_to must be >= date_from")

    # ── helpers ───────────────────────────────────────────────────────────────
    _OVER_MARKETS = frozenset({
        "Over 1.5", "Over 2.5",
        "Home Over 0.5", "Home Over 1.5", "Away Over 0.5", "Away Over 1.5",
    })
    _UNDER_MARKETS = frozenset({"Under 2.5", "Under 3.5", "Under 1.5"})

    def _slot(market: str) -> str:
        if market in _UNDER_MARKETS:
            return "under"
        if market in _OVER_MARKETS:
            return "over"
        return "other"

    def _rank(sig: Signal, fix: Fixture) -> tuple:
        bp = sig.bayesian_prob or 0.0
        pp = sig.poisson_prob or 0.0
        primary = max(bp, pp)
        avg = ((bp + pp) / 2.0) if bp and pp else primary
        conf = {"High": 3, "Medium": 2, "Low": 1}.get(sig.dual_confidence or "", 0)
        agr = {"Both": 3, "Bayesian Only": 2, "Poisson Only": 1}.get(sig.dual_agreement or "", 0)
        tier = 1 if fix.league_tier == 1 else 0
        return (conf, agr, 1 if primary >= 0.70 else 0, round(primary, 6),
                sig.bayesian_bookmaker_count or 0, tier, round(avg, 6),
                sig.dual_quality_score or 0.0)

    # ── per-date loop ─────────────────────────────────────────────────────────
    results = []
    current = start
    while current <= end:
        date_str = current.isoformat()
        entry: dict = {"date": date_str, "recomputed": False,
                       "signals_found": 0, "bets_created": 0, "bets_skipped": 0}

        # 1. Recompute signals if none exist for this date
        sig_count_row = await db.execute(
            select(sqlfunc.count(Signal.id))
            .join(Fixture, Signal.fixture_id == Fixture.id)
            .where(Fixture.event_date == current)
            .where(Signal.is_candidate == False)  # noqa: E712
        )
        if sig_count_row.scalar() == 0:
            try:
                new_count = await asyncio.wait_for(
                    compute_signals_for_date(db, current), timeout=90
                )
                entry["recomputed"] = True
                entry["recomputed_count"] = new_count
            except asyncio.TimeoutError:
                entry["error"] = "signal recompute timed out (>90s) — skipped"
                results.append(entry)
                current += timedelta(days=1)
                continue
            except Exception as exc:
                entry["error"] = f"signal recompute failed: {exc}"
                results.append(entry)
                current += timedelta(days=1)
                continue

        # 2. Query qualifying signals for this date
        q = (
            select(Signal, Fixture)
            .join(Fixture, Signal.fixture_id == Fixture.id)
            .where(Fixture.event_date == current)
            .where(Signal.is_candidate == False)  # noqa: E712
            .where(Signal.dual_confidence.in_(["High", "Medium"]))
            .where(Signal.dual_agreement.notin_(["Contradiction", "None"]))
            .where(Signal.bayesian_best_odd > 1.0)  # must have a bettable price
        )
        if DISABLED_MARKETS:
            q = q.where(Signal.market.notin_(list(DISABLED_MARKETS)))
        if DISABLED_LEAGUES:
            q = q.where(
                sqlfunc.lower(sqlfunc.trim(Fixture.league)).notin_(DISABLED_LEAGUES)
            )
        rows = list((await db.execute(q)).all())

        # 3. Deduplicate to best signal per (fixture, market_slot)
        best: dict[tuple, tuple] = {}
        for sig, fix in rows:
            key = (sig.fixture_id, _slot(sig.market))
            prev = best.get(key)
            if prev is None or _rank(sig, fix) > _rank(prev[0], prev[1]):
                best[key] = (sig, fix)

        deduped = sorted(best.values(), key=lambda x: _rank(x[0], x[1]), reverse=True)
        entry["signals_found"] = len(deduped)

        # 4. Create TrackedBet rows
        for sig, fix in deduped:
            # Check for existing system pick on this fixture+market.
            # Match the uq_system_signal_bet index: any user_id=NULL row for
            # this (fixture_id, market_type) — regardless of source_rule_key.
            existing = await db.scalar(
                select(TrackedBet).where(
                    TrackedBet.fixture_id == sig.fixture_id,
                    TrackedBet.market_type == sig.market,
                    TrackedBet.user_id.is_(None),
                )
            )
            if existing:
                entry["bets_skipped"] += 1
                continue

            entry["bets_created"] += 1
            if dry_run:
                continue

            bet = TrackedBet(
                user_id=None,
                fixture_id=sig.fixture_id,
                bookmaker=sig.bayesian_bookmaker or "Best Available",
                event_date=fix.event_date,
                match_name=f"{fix.home_team} vs {fix.away_team}",
                league=fix.league,
                market_type=sig.market,
                selection_name=sig.market,
                odds=round(sig.bayesian_best_odd, 4),
                stake=1.0,
                recommended_stake_pct=sig.dual_recommended_stake_pct,
                source_rule_key="system_auto",
                source_rule_label="System Auto Pick",
                signal_grade=sig.poisson_grade,
                dual_confidence=sig.dual_confidence,
                dual_agreement=sig.dual_agreement,
            )
            db.add(bet)

        if not dry_run and entry["bets_created"] > 0:
            await db.commit()

        results.append(entry)
        current += timedelta(days=1)

    # 5. Settle all pending bets now that past scores are known
    settled_count = 0
    if not dry_run:
        try:
            settle_info = await settle_bets_for_date(db, None)
            settled_count = settle_info.get("settled", 0)
        except Exception as exc:
            settled_count = -1

    total_created = sum(r.get("bets_created", 0) for r in results)
    total_skipped = sum(r.get("bets_skipped", 0) for r in results)

    return {
        "dry_run": dry_run,
        "date_from": date_from,
        "date_to": end.isoformat(),
        "days_processed": len(results),
        "total_bets_created": total_created,
        "total_bets_skipped": total_skipped,
        "total_settled": settled_count,
        "detail": results,
    }


# ── Calibration ──────────────────────────────────────────────────────────────

@router.get("/calibration")
async def get_calibration(
    days: int = 90,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """
    Run the weekly calibration audit on demand.
    Returns Brier skill, ECE, reliability diagram, per-market breakdown,
    and a list of markets currently failing the health threshold.
    """
    from app.services.calibration import compute_calibration_metrics
    report = await compute_calibration_metrics(db, days=days)
    return {
        "generated_at":     report.generated_at.isoformat(),
        "window_days":      report.window_days,
        "date_range":       {"min": report.date_min, "max": report.date_max},
        "total_bets":       report.total_bets,
        "signal_join_bets": report.signal_join_bets,
        "overall_win_rate": report.overall_win_rate,
        "brier_score":      report.brier_score,
        "brier_naive":      report.brier_naive,
        "brier_skill":      report.brier_skill,
        "ece":              report.ece,
        "flagged_markets":  report.flagged_markets,
        "reliability": [
            {"bucket": f"[{b.lo:.1f}-{b.hi:.1f})", "n": b.n,
             "mean_model_p": b.mean_model_p, "actual_hit_rate": b.actual_hit_rate,
             "gap": b.gap}
            for b in report.reliability
        ],
        "by_market": [
            {"market": m.market, "n": m.n, "win_rate": m.win_rate,
             "mean_model_p": m.mean_model_p, "calibration_gap": m.calibration_gap,
             "brier_skill": m.brier_skill, "flat_roi_pct": m.flat_roi_pct,
             "flagged": m.flagged}
            for m in report.by_market
        ],
        "by_confidence": [
            {"tier": c.tier, "n": c.n, "win_rate": c.win_rate,
             "mean_model_p": c.mean_model_p, "flat_roi_pct": c.flat_roi_pct}
            for c in report.by_confidence
        ],
    }


@router.get("/calibration/history")
async def get_calibration_history(
    n: int = 12,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """
    Return the last n weekly calibration snapshots for trend tracking.
    """
    from app.services.calibration import load_recent_snapshots
    return {"snapshots": await load_recent_snapshots(db, n=n)}


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


@router.post("/advisory/retrack")
async def retrack_advisory_acca(
    target_date:        str  = Query(description="ISO date to retrack, e.g. 2026-07-03"),
    replace_user_bets:  bool = Query(False, description="Also delete all users' acca_advisory rows for this date so they re-track against the new acca"),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """
    Force-replace a date's system acca tracking rows and optionally user rows.

    Always deletes acca_leg_system rows (user_id=NULL) for the date, then
    re-runs auto_track_acca_legs() against the current advisory cache.

    replace_user_bets=true additionally purges all users' acca_advisory rows
    for the date so they see the new acca as un-tracked and can re-add it.
    Use after a mid-day cache clear where the new acca is materially different.
    """
    from datetime import date as date_type
    from app.services.advisor_service import get_advisor_insights, auto_track_acca_legs

    try:
        d = date_type.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(400, f"Invalid date: {target_date!r} — use ISO format YYYY-MM-DD")

    user_rows_deleted = 0
    if replace_user_bets:
        res = await db.execute(
            text(
                "DELETE FROM tracked_bets "
                "WHERE event_date = :d AND source_rule_key = 'acca_advisory' "
                "AND user_id IS NOT NULL"
            ),
            {"d": d.isoformat()},
        )
        user_rows_deleted = res.rowcount
        await db.commit()

    result = await get_advisor_insights(db, d, current_user=None)
    tickets = result.get("accumulators") or []
    if not tickets:
        acca = result.get("accumulator", {})
        if acca.get("legs") and not acca.get("error"):
            tickets = [acca]
    if not tickets:
        return {
            "replaced": 0,
            "user_rows_deleted": user_rows_deleted,
            "message": "No valid acca available for this date.",
        }

    n = await auto_track_acca_legs(db, tickets, d, replace=True)
    return {
        "replaced":          n,
        "user_rows_deleted": user_rows_deleted,
        "date":              d.isoformat(),
        "tickets":           len(tickets),
        "combined_odds":     [t.get("combined_odds") for t in tickets],
    }


@router.get("/watchguard")
async def get_watchguard_status(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """
    Read-only view of the league watch guard — current state of every monitored league,
    including ROI, bet count, warning/suppression thresholds, and active proposal ID.
    """
    return {"watchlist": await get_watchlist_status(db)}


@router.post("/watchguard/run")
async def trigger_watchguard(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """
    Manually trigger the league watch guard cycle.
    Evaluates all watched leagues and creates/deactivates suppression proposals as needed.
    """
    statuses = await run_league_watch_guard(db)
    return {
        "evaluated": len(statuses),
        "results": [
            {
                "keyword":      s.keyword,
                "state":        s.state,
                "action_taken": s.action_taken,
                "total_bets":   s.total_bets,
                "wins":         s.wins,
                "roi_pct":      s.roi_pct,
                "proposal_id":  s.proposal_id,
                "message":      s.message,
            }
            for s in statuses
        ],
    }


@router.post("/cleanup-tracked-bets")
async def cleanup_tracked_bets(
    dry_run: bool = Query(False, description="Preview counts — no deletes"),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    """
    Delete system tracked_bets (user_id IS NULL) and signal rows that violate
    the current serving-time gates. Idempotent — safe to run multiple times.

    Gates applied:
    1. DISABLED_MARKETS — market type permanently retired
    2. DISABLED_LEAGUES — league permanently disabled
    3. OVER_GOALS_SUPPRESSED_LEAGUES — over-goals markets blocked in these leagues
    4. Over 1.5 non-High confidence gate
    5. Over 2.5 Tier 3 suppression
    6. COPA_HO05_SUPPRESSED_LEAGUES — cup HO0.5 blocked
    7. MARKET_MIN_ODDS floors
    8. Women's league over-goals suppression
    9. DUAL_HIGH_ODDS_CEILING (Both+High ceiling)
    10. POISSON_ONLY_MAX_ODDS (Poisson Only odds cap)
    11. HO05_DATA_POOR_COUNTRIES (Both+High Tier 3 blocked)
    12. Under 2.5 odds > 2.20 cap
    """
    over_goals_markets = (
        "Over 1.5", "Over 2.5", "Home Over 0.5", "Away Over 0.5", "Over 0.5 1H",
    )

    def _list(s): return list(s)
    def _lower_list(s): return [x.lower().strip() for x in s]

    async def _count_and_delete(sql_where: str, params: dict, label: str) -> int:
        count_sql = f"SELECT COUNT(*) FROM tracked_bets WHERE user_id IS NULL AND {sql_where}"
        n = (await db.execute(text(count_sql), params)).scalar() or 0
        if not dry_run and n > 0:
            await db.execute(text(f"DELETE FROM tracked_bets WHERE user_id IS NULL AND {sql_where}"), params)
        return n

    # Build over-goals suppressed LIKE conditions
    og_like = " OR ".join(
        f"lower(trim(league)) LIKE '%' || :og{i} || '%'"
        for i, _ in enumerate(OVER_GOALS_SUPPRESSED_LEAGUES)
    )
    og_params = {f"og{i}": v for i, v in enumerate(OVER_GOALS_SUPPRESSED_LEAGUES)}

    women_like = " OR ".join(
        f"lower(league) LIKE '%' || :wk{i} || '%'"
        for i, _ in enumerate(WOMEN_LEAGUE_KEYWORDS)
    )
    women_params = {f"wk{i}": v for i, v in enumerate(WOMEN_LEAGUE_KEYWORDS)}

    copa_lower = _lower_list(COPA_HO05_SUPPRESSED_LEAGUES)
    data_poor_lower = _lower_list(HO05_DATA_POOR_COUNTRIES)

    dm_placeholders = ",".join(f":dm{i}" for i, _ in enumerate(DISABLED_MARKETS))
    dl_placeholders = ",".join(f":dl{i}" for i, _ in enumerate(DISABLED_LEAGUES))
    copa_placeholders = ",".join(f":copa{i}" for i, _ in enumerate(copa_lower))
    dp_placeholders = ",".join(f":dp{i}" for i, _ in enumerate(data_poor_lower))
    og_mkt_ph = ",".join(f":ogm{i}" for i, _ in enumerate(over_goals_markets))

    dm_params = {f"dm{i}": v for i, v in enumerate(DISABLED_MARKETS)}
    dl_params = {f"dl{i}": v.lower().strip() for i, v in enumerate(DISABLED_LEAGUES)}
    copa_params = {f"copa{i}": v for i, v in enumerate(copa_lower)}
    dp_params = {f"dp{i}": v for i, v in enumerate(data_poor_lower)}
    ogm_params = {f"ogm{i}": v for i, v in enumerate(over_goals_markets)}

    results = {}

    # 1. Disabled markets
    results["disabled_markets"] = await _count_and_delete(
        f"market_type IN ({dm_placeholders})", dm_params, "disabled markets"
    )

    # 2. Disabled leagues
    results["disabled_leagues"] = await _count_and_delete(
        f"lower(trim(league)) IN ({dl_placeholders})", dl_params, "disabled leagues"
    )

    # 3. Over-goals suppressed leagues
    results["og_suppressed_leagues"] = await _count_and_delete(
        f"market_type IN ({og_mkt_ph}) AND ({og_like})",
        {**ogm_params, **og_params},
        "OG suppressed leagues",
    )

    # 4. Over 1.5 non-High confidence
    results["over15_non_high"] = await _count_and_delete(
        "market_type = 'Over 1.5' AND (dual_confidence IS NULL OR dual_confidence != 'High')",
        {}, "Over 1.5 non-High",
    )

    # 4a. Over 1.5 Bayesian Only — Poisson doesn't confirm the goals line
    results["over15_bayesian_only"] = await _count_and_delete(
        "market_type = 'Over 1.5' AND dual_agreement = 'Bayesian Only'",
        {}, "Over 1.5 Bayesian Only",
    )

    # 5. Over 2.5 Tier 3
    results["over25_tier3"] = await _count_and_delete(
        "market_type = 'Over 2.5' AND fixture_id IN (SELECT id FROM fixtures WHERE league_tier >= 3)",
        {}, "Over 2.5 Tier 3",
    )

    # 6. Copa HO0.5
    results["copa_ho05"] = await _count_and_delete(
        f"market_type = 'Home Over 0.5' AND lower(trim(league)) IN ({copa_placeholders})",
        copa_params, "Copa HO0.5",
    )

    # 7. Below MARKET_MIN_ODDS floors
    min_odds_clause = (
        "(market_type = 'Over 1.5'           AND odds IS NOT NULL AND odds < :o15_min)"
        " OR (market_type = 'Over 2.5'       AND odds IS NOT NULL AND odds < :o25_min)"
        " OR (market_type = 'Under 2.5'      AND odds IS NOT NULL AND odds < :u25_min)"
        " OR (market_type = 'Home Over 0.5'  AND odds IS NOT NULL AND odds < :ho05_min)"
        " OR (market_type = 'Away Over 0.5'  AND odds IS NOT NULL AND odds < :ao05_min)"
        " OR (market_type = 'Home Win to Nil' AND odds IS NOT NULL AND odds < :hwtn_min)"
        " OR (market_type = 'Away Win to Nil' AND odds IS NOT NULL AND odds < :awtn_min)"
        " OR (market_type = '1X (Home or Draw)' AND odds IS NOT NULL AND odds < :dc1x_min)"
        " OR (market_type = 'X2 (Draw or Away)' AND odds IS NOT NULL AND odds < :dcx2_min)"
        " OR (market_type = '12 (Home or Away)' AND odds IS NOT NULL AND odds < :dc12_min)"
    )
    results["below_min_odds"] = await _count_and_delete(
        f"({min_odds_clause})",
        {
            "o15_min":   MARKET_MIN_ODDS.get("Over 1.5", 1.50),
            "o25_min":   MARKET_MIN_ODDS.get("Over 2.5", 1.55),
            "u25_min":   MARKET_MIN_ODDS.get("Under 2.5", 2.10),
            "ho05_min":  MARKET_MIN_ODDS.get("Home Over 0.5", 1.30),
            "ao05_min":  MARKET_MIN_ODDS.get("Away Over 0.5", 1.30),
            "hwtn_min":  MARKET_MIN_ODDS.get("Home Win to Nil", 1.40),
            "awtn_min":  MARKET_MIN_ODDS.get("Away Win to Nil", 1.40),
            "dc1x_min":  MARKET_MIN_ODDS.get("1X (Home or Draw)", 1.25),
            "dcx2_min":  MARKET_MIN_ODDS.get("X2 (Draw or Away)", 1.25),
            "dc12_min":  MARKET_MIN_ODDS.get("12 (Home or Away)", 1.30),
        },
        "below min odds",
    )

    # 8. Women's over-goals
    # tracked_bets has no home_team/away_team columns — team names are in match_name
    # as "Home Team vs Away Team". " W" suffix appears as "Team W vs ..." or "... vs Team W".
    results["womens_og"] = await _count_and_delete(
        f"market_type IN ({og_mkt_ph}) AND ({women_like}"
        " OR lower(match_name) LIKE '% w vs %' OR lower(match_name) LIKE '% vs % w')",
        {**ogm_params, **women_params},
        "women's OG",
    )

    # 9. DUAL_HIGH_ODDS_CEILING
    ho05_ceil = DUAL_HIGH_ODDS_CEILING.get("Home Over 0.5", 1.95)
    ao05_ceil = DUAL_HIGH_ODDS_CEILING.get("Away Over 0.5", 2.10)
    results["dual_high_odds_ceiling"] = await _count_and_delete(
        "(market_type = 'Home Over 0.5' AND dual_agreement = 'Both' AND dual_confidence = 'High' AND odds IS NOT NULL AND odds >= :ho05_ceil)"
        " OR (market_type = 'Away Over 0.5' AND dual_agreement = 'Both' AND dual_confidence = 'High' AND odds IS NOT NULL AND odds >= :ao05_ceil)",
        {"ho05_ceil": ho05_ceil, "ao05_ceil": ao05_ceil},
        "dual high odds ceiling",
    )

    # 10. POISSON_ONLY_MAX_ODDS for HO0.5
    po_max = POISSON_ONLY_MAX_ODDS.get("Home Over 0.5", 2.10)
    results["poisson_only_max_odds"] = await _count_and_delete(
        "market_type = 'Home Over 0.5' AND dual_agreement = 'Poisson Only' AND odds IS NOT NULL AND odds >= :po_max",
        {"po_max": po_max},
        "Poisson Only max odds",
    )

    # 11. HO05_DATA_POOR_COUNTRIES (Both+High, Tier 3)
    results["data_poor_countries"] = await _count_and_delete(
        f"market_type = 'Home Over 0.5' AND dual_agreement = 'Both' AND dual_confidence = 'High'"
        f" AND fixture_id IN (SELECT id FROM fixtures WHERE league_tier >= 3 AND lower(country) IN ({dp_placeholders}))",
        dp_params,
        "data-poor countries",
    )

    # 12. Under 2.5 odds > 2.20
    results["under25_odds_cap"] = await _count_and_delete(
        "market_type = 'Under 2.5' AND odds IS NOT NULL AND odds > 2.20",
        {}, "Under 2.5 odds cap",
    )

    total_bets_deleted = sum(results.values())

    # ── Clean up signal rows (disabled markets + disabled leagues + suppressed) ──
    sig_results = {}

    async def _count_and_delete_signals(sql_where: str, params: dict) -> int:
        count_sql = f"SELECT COUNT(*) FROM signals WHERE {sql_where}"
        n = (await db.execute(text(count_sql), params)).scalar() or 0
        if not dry_run and n > 0:
            await db.execute(text(f"DELETE FROM signals WHERE {sql_where}"), params)
        return n

    # signals table has no league/home_team/away_team columns — must join via fixtures
    fix_dl_like = " OR ".join(
        f"lower(trim(f.league)) = :sdl{i}" for i, _ in enumerate(DISABLED_LEAGUES)
    )
    sdl_params = {f"sdl{i}": v.lower().strip() for i, v in enumerate(DISABLED_LEAGUES)}

    fix_og_like = " OR ".join(
        f"lower(trim(f.league)) LIKE '%' || :sog{i} || '%'"
        for i, _ in enumerate(OVER_GOALS_SUPPRESSED_LEAGUES)
    )
    sog_params = {f"sog{i}": v for i, v in enumerate(OVER_GOALS_SUPPRESSED_LEAGUES)}

    fix_wk_like = " OR ".join(
        f"lower(f.league) LIKE '%' || :swk{i} || '%'"
        for i, _ in enumerate(WOMEN_LEAGUE_KEYWORDS)
    )
    swk_params = {f"swk{i}": v for i, v in enumerate(WOMEN_LEAGUE_KEYWORDS)}

    async def _cads(where: str, params: dict) -> int:
        """count-and-delete signals with optional fixture join via EXISTS."""
        sql_c = f"SELECT COUNT(*) FROM signals s WHERE {where}"
        sql_d = f"DELETE FROM signals WHERE id IN (SELECT s.id FROM signals s WHERE {where})"
        n = (await db.execute(text(sql_c), params)).scalar() or 0
        if not dry_run and n > 0:
            await db.execute(text(sql_d), params)
        return n

    sig_results["disabled_markets"] = await _cads(
        f"s.market IN ({dm_placeholders})", dm_params
    )
    sig_results["over15_bayesian_only"] = await _cads(
        "s.market = 'Over 1.5' AND s.dual_agreement = 'Bayesian Only'", {}
    )
    sig_results["disabled_leagues"] = await _cads(
        f"EXISTS (SELECT 1 FROM fixtures f WHERE f.id = s.fixture_id AND ({fix_dl_like}))",
        sdl_params,
    )
    sig_results["og_suppressed_leagues"] = await _cads(
        f"s.market IN ({og_mkt_ph}) AND EXISTS "
        f"(SELECT 1 FROM fixtures f WHERE f.id = s.fixture_id AND ({fix_og_like}))",
        {**ogm_params, **sog_params},
    )
    sig_results["womens_og"] = await _cads(
        f"s.market IN ({og_mkt_ph}) AND EXISTS "
        f"(SELECT 1 FROM fixtures f WHERE f.id = s.fixture_id AND "
        f"({fix_wk_like} OR lower(f.home_team) LIKE '% w' OR lower(f.away_team) LIKE '% w'))",
        {**ogm_params, **swk_params},
    )
    sig_results["below_min_odds"] = await _cads(
        "(s.market = 'Over 1.5'           AND s.bayesian_best_odd IS NOT NULL AND s.bayesian_best_odd < :o15_min)"
        " OR (s.market = 'Over 2.5'       AND s.bayesian_best_odd IS NOT NULL AND s.bayesian_best_odd < :o25_min)"
        " OR (s.market = 'Under 2.5'      AND s.bayesian_best_odd IS NOT NULL AND s.bayesian_best_odd < :u25_min)"
        " OR (s.market = 'Home Over 0.5'  AND s.bayesian_best_odd IS NOT NULL AND s.bayesian_best_odd < :ho05_min)"
        " OR (s.market = 'Away Over 0.5'  AND s.bayesian_best_odd IS NOT NULL AND s.bayesian_best_odd < :ao05_min)"
        " OR (s.market = 'Home Win to Nil' AND s.bayesian_best_odd IS NOT NULL AND s.bayesian_best_odd < :hwtn_min)"
        " OR (s.market = 'Away Win to Nil' AND s.bayesian_best_odd IS NOT NULL AND s.bayesian_best_odd < :awtn_min)"
        " OR (s.market = '1X (Home or Draw)' AND s.bayesian_best_odd IS NOT NULL AND s.bayesian_best_odd < :dc1x_min)"
        " OR (s.market = 'X2 (Draw or Away)' AND s.bayesian_best_odd IS NOT NULL AND s.bayesian_best_odd < :dcx2_min)"
        " OR (s.market = '12 (Home or Away)' AND s.bayesian_best_odd IS NOT NULL AND s.bayesian_best_odd < :dc12_min)",
        {
            "o15_min":  MARKET_MIN_ODDS.get("Over 1.5", 1.50),
            "o25_min":  MARKET_MIN_ODDS.get("Over 2.5", 1.55),
            "u25_min":  MARKET_MIN_ODDS.get("Under 2.5", 2.10),
            "ho05_min": MARKET_MIN_ODDS.get("Home Over 0.5", 1.30),
            "ao05_min": MARKET_MIN_ODDS.get("Away Over 0.5", 1.30),
            "hwtn_min": MARKET_MIN_ODDS.get("Home Win to Nil", 1.40),
            "awtn_min": MARKET_MIN_ODDS.get("Away Win to Nil", 1.40),
            "dc1x_min": MARKET_MIN_ODDS.get("1X (Home or Draw)", 1.25),
            "dcx2_min": MARKET_MIN_ODDS.get("X2 (Draw or Away)", 1.25),
            "dc12_min": MARKET_MIN_ODDS.get("12 (Home or Away)", 1.30),
        },
    )
    total_signals_deleted = sum(sig_results.values())

    if not dry_run:
        await db.commit()

    # ── Post-cleanup analytics ────────────────────────────────────────────────
    analytics_rows = (await db.execute(text("""
        SELECT market_type,
               COUNT(*) total,
               SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END) won,
               SUM(CASE WHEN result_status='Lost' THEN 1 ELSE 0 END) lost,
               ROUND(100.0 * SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END)
                     / NULLIF(SUM(CASE WHEN result_status IN ('Won','Lost') THEN 1 ELSE 0 END),0), 2) wr_pct,
               ROUND(
                 (SUM(CASE WHEN result_status='Won' THEN (odds - 1) * stake ELSE 0 END)
                  - SUM(CASE WHEN result_status='Lost' THEN stake ELSE 0 END))
                 / NULLIF(SUM(CASE WHEN result_status IN ('Won','Lost') THEN stake ELSE 0 END),0) * 100,
               2) roi_pct
        FROM tracked_bets
        WHERE user_id IS NULL AND market_type != 'Accumulator'
          AND result_status IN ('Won','Lost','Pending')
        GROUP BY market_type ORDER BY total DESC
    """))).fetchall()

    overall = (await db.execute(text("""
        SELECT COUNT(*) total,
               SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END) won,
               SUM(CASE WHEN result_status='Lost' THEN 1 ELSE 0 END) lost,
               ROUND(100.0 * SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END)
                     / NULLIF(SUM(CASE WHEN result_status IN ('Won','Lost') THEN 1 ELSE 0 END),0), 2) wr_pct,
               ROUND(
                 (SUM(CASE WHEN result_status='Won' THEN (odds - 1) * stake ELSE 0 END)
                  - SUM(CASE WHEN result_status='Lost' THEN stake ELSE 0 END))
                 / NULLIF(SUM(CASE WHEN result_status IN ('Won','Lost') THEN stake ELSE 0 END),0) * 100,
               2) roi_pct
        FROM tracked_bets
        WHERE user_id IS NULL AND market_type != 'Accumulator'
          AND result_status IN ('Won','Lost')
    """))).fetchone()

    return {
        "dry_run": dry_run,
        "tracked_bets_deleted": {
            **results,
            "total": total_bets_deleted,
        },
        "signal_rows_deleted": {
            **sig_results,
            "total": total_signals_deleted,
        },
        "post_cleanup_analytics": {
            "overall": {
                "total": overall.total,
                "won": overall.won,
                "lost": overall.lost,
                "win_rate_pct": overall.wr_pct,
                "roi_pct": overall.roi_pct,
            },
            "by_market": [
                {
                    "market": r.market_type,
                    "total": r.total,
                    "won": r.won,
                    "lost": r.lost,
                    "win_rate_pct": r.wr_pct,
                    "roi_pct": r.roi_pct,
                }
                for r in analytics_rows
            ],
        },
    }


@router.post("/backfill-dates")
async def backfill_dates(
    date_from: str = Query(description="Start date inclusive, YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="End date inclusive (default: today)"),
    dry_run: bool = Query(False, description="Preview only — no DB writes, no API calls"),
    _admin: User = Depends(_require_admin),
):
    """
    Full re-sync for each date in the range: ingest fixtures/odds from API-Football,
    recompute signals, auto-track qualifying picks, then settle all pending bets.

    For past dates the API returns final scores but no odds — signals recompute from
    existing market_snapshots when available. If a date has no snapshots, signals will
    be skipped (returned in 'no_snapshots' list).

    Use dry_run=true to preview which dates would be processed without making API calls.
    """
    import asyncio
    from datetime import date, timedelta
    from sqlalchemy import func as sqlfunc, text as sqltxt
    from app.services.ingestion import sync_date
    from app.services.signal_engine import compute_signals_for_date
    from app.services.auto_tracker import auto_track_date
    from app.services.settlement import settle_bets_for_date
    from app.models import Signal, Fixture
    from app.core.config import DISABLED_MARKETS, DISABLED_LEAGUES
    from app.core.database import AsyncSessionLocal

    try:
        start = date.fromisoformat(date_from)
    except ValueError:
        raise HTTPException(400, f"Invalid date_from: {date_from!r} — use YYYY-MM-DD")
    try:
        end = date.fromisoformat(date_to) if date_to else date.today()
    except ValueError:
        raise HTTPException(400, f"Invalid date_to: {date_to!r} — use YYYY-MM-DD")
    if end < start:
        raise HTTPException(400, "date_to must be >= date_from")
    if (end - start).days > 60:
        raise HTTPException(400, "Date range cannot exceed 60 days")

    if dry_run:
        # Just preview which dates exist in DB vs missing
        async with AsyncSessionLocal() as db:
            preview = []
            current = start
            while current <= end:
                snap_count = (await db.execute(sqltxt(
                    "SELECT COUNT(*) FROM market_snapshots ms "
                    "JOIN fixtures f ON f.id = ms.fixture_id "
                    "WHERE f.event_date = :d"
                ), {"d": current.isoformat()})).scalar() or 0
                sig_count = (await db.execute(sqltxt(
                    "SELECT COUNT(*) FROM signals s "
                    "JOIN fixtures f ON f.id = s.fixture_id "
                    "WHERE f.event_date = :d AND s.is_candidate = 0"
                ), {"d": current.isoformat()})).scalar() or 0
                bet_count = (await db.execute(sqltxt(
                    "SELECT COUNT(*) FROM tracked_bets WHERE event_date = :d AND user_id IS NULL"
                ), {"d": current.isoformat()})).scalar() or 0
                preview.append({
                    "date": current.isoformat(),
                    "market_snapshots": snap_count,
                    "signals": sig_count,
                    "system_bets": bet_count,
                    "action": "sync+compute+track" if snap_count == 0 else (
                        "compute+track" if sig_count == 0 else (
                            "track" if bet_count == 0 else "skip (already tracked)"
                        )
                    ),
                })
                current += timedelta(days=1)
        return {"dry_run": True, "date_from": date_from, "date_to": end.isoformat(), "preview": preview}

    # Live run — process each date sequentially
    results = []
    current = start
    while current <= end:
        date_str = current.isoformat()
        entry: dict = {
            "date": date_str,
            "ingested": False,
            "signals_computed": 0,
            "bets_tracked": 0,
            "error": None,
        }
        async with AsyncSessionLocal() as db:
            try:
                # Check for existing market snapshots — skip API call if data is already in DB.
                # Past dates have no live odds so sync_date() stalls; recomputing from snapshots
                # is both faster and produces the same result.
                snap_count: int = (await db.execute(sqltxt(
                    "SELECT COUNT(*) FROM market_snapshots ms "
                    "JOIN fixtures f ON f.id = ms.fixture_id "
                    "WHERE f.event_date = :d"
                ), {"d": date_str})).scalar() or 0

                if snap_count > 0:
                    # Snapshots exist — recompute signals without touching the API
                    fix_count: int = (await db.execute(sqltxt(
                        "SELECT COUNT(DISTINCT ms.fixture_id) FROM market_snapshots ms "
                        "JOIN fixtures f ON f.id = ms.fixture_id WHERE f.event_date = :d"
                    ), {"d": date_str})).scalar() or 0
                    entry["ingested"] = True
                    entry["fixtures_pulled"] = fix_count
                    entry["skipped_ingestion"] = True
                else:
                    # No snapshots — try API ingestion (only useful for future/recent dates)
                    run = await asyncio.wait_for(sync_date(db, current, force=True), timeout=120)
                    entry["ingested"] = run.status == "success"
                    entry["fixtures_pulled"] = getattr(run, "fixtures_pulled", 0)
                    if run.status != "success":
                        entry["error"] = f"ingestion status: {run.status}"
                        results.append(entry)
                        current += timedelta(days=1)
                        continue

                n_sig = await asyncio.wait_for(
                    compute_signals_for_date(db, current), timeout=90
                )
                entry["signals_computed"] = n_sig
                await db.commit()
                n_track = await auto_track_date(db, current)
                entry["bets_tracked"] = n_track
            except asyncio.TimeoutError:
                entry["error"] = "timed out (>120s)"
            except Exception as exc:
                entry["error"] = str(exc)
        results.append(entry)
        current += timedelta(days=1)

    # Settle all pending bets (all dates) after back-filling
    settled_count = 0
    async with AsyncSessionLocal() as db:
        try:
            info = await settle_bets_for_date(db, None)
            settled_count = info.get("settled", 0)
        except Exception as exc:
            settled_count = -1

    return {
        "dry_run": False,
        "date_from": date_from,
        "date_to": end.isoformat(),
        "days_processed": len(results),
        "total_bets_tracked": sum(r.get("bets_tracked", 0) for r in results),
        "total_settled": settled_count,
        "detail": results,
    }


@router.post("/sync/trigger")
async def trigger_sync(
    run_date: Optional[str] = Query(None, description="ISO date to sync (default: today)"),
    morning_extras: bool = Query(False, description="Run advisory cache + ACCA + morning digest"),
    evening_extras: bool = Query(False, description="Run tomorrow pre-sync + digests"),
    _admin: User = Depends(_require_admin),
):
    """
    Manually fire sync_and_compute for a date.
    Runs ingestion → signal compute → settlement → self-learning pipelines.
    Useful for on-demand recovery or schedule testing.
    """
    import asyncio
    from datetime import date as _date
    from app.scheduler import sync_and_compute

    target = _date.fromisoformat(run_date) if run_date else _date.today()
    task = asyncio.create_task(
        sync_and_compute(target, morning_extras=morning_extras, evening_extras=evening_extras)
    )
    return {
        "status": "triggered",
        "date": str(target),
        "morning_extras": morning_extras,
        "evening_extras": evening_extras,
        "note": "Running in background — check server logs for progress.",
    }

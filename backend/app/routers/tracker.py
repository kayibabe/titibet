from __future__ import annotations
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, delete, update, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, get_current_user_optional
from app.core.database import get_db
from app.models import Fixture, MarketSnapshot, TrackedBet, AccumulatorTicket, AccumulatorLeg, IngestionRun
from app.models.user import User
from app.schemas.bet import (
    TrackPickRequest,
    BetUpdate,
    BetOut,
    AccumulatorCreate,
    AccumulatorOut,
    ConfirmRecommendedTicketIn,
    ConfirmRecommendedTicketOut,
)
from app.services import ingestion, settlement
from app.services.analytics import build_analytics, build_accumulator_analytics
from app.services.accumulator_generator import (
    generate_rank_bucket_suggestions,
)
from app.services.clv import compute_clv_all
from app.services.performance_intelligence import compute_performance_weights

router = APIRouter(prefix="/api/tracker", tags=["tracker"])


def _require_user(current_user: Optional[User]) -> User:
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    return current_user


def _apply_settlement(bet: TrackedBet, result_status: str) -> None:
    """Keep stake edits and manual status changes consistent with derived P/L."""
    bet.result_status = result_status
    if result_status == "Won":
        bet.profit_loss = round(bet.stake * (bet.odds - 1.0), 2)
        bet.settled_at = datetime.utcnow()
    elif result_status == "Lost":
        bet.profit_loss = round(-bet.stake, 2)
        bet.settled_at = datetime.utcnow()
    elif result_status == "Void":
        bet.profit_loss = 0.0
        bet.settled_at = datetime.utcnow()
    else:
        bet.profit_loss = 0.0
        bet.settled_at = None


def _bet_out_from_models(bet: TrackedBet, fixture: Fixture | None = None) -> BetOut:
    home = fixture.home_team if fixture else None
    away = fixture.away_team if fixture else None
    name = bet.match_name
    if home and away and (not name or name.strip() == "vs"):
        name = f"{home} vs {away}"
    return BetOut(
        id=bet.id,
        fixture_id=bet.fixture_id,
        bookmaker=bet.bookmaker,
        event_date=bet.event_date,
        match_name=name,
        home_team=home,
        away_team=away,
        league=bet.league,
        market_type=bet.market_type,
        selection_name=bet.selection_name,
        odds=bet.odds,
        stake=bet.stake,
        recommended_stake_pct=bet.recommended_stake_pct,
        source_rule_key=bet.source_rule_key,
        source_rule_label=bet.source_rule_label,
        signal_grade=bet.signal_grade,
        dual_confidence=bet.dual_confidence,
        dual_agreement=bet.dual_agreement,
        result_status=bet.result_status,
        profit_loss=bet.profit_loss,
        notes=bet.notes,
        created_at=bet.created_at,
        settled_at=bet.settled_at,
        closing_odds=bet.closing_odds,
        clv_pct=bet.clv_pct,
        home_score=fixture.home_score if fixture else None,
        away_score=fixture.away_score if fixture else None,
        fixture_status=fixture.status if fixture else None,
        kickoff_at=fixture.kickoff_at if fixture else None,
    )


async def _track_or_reuse_bet(
    db: AsyncSession,
    *,
    user: User,
    fixture_id: int | None,
    bookmaker: str,
    market_type: str,
    selection_name: str,
    match_name: str,
    league: str | None,
    event_date: date | None,
    odds: float,
    stake: float,
    dual_confidence: str | None = None,
    dual_agreement: str | None = None,
    recommended_stake_pct: float | None = None,
    source_rule_key: str | None = None,
    source_rule_label: str | None = None,
    signal_grade: str | None = None,
    notes: str | None = None,
) -> tuple[TrackedBet, bool, float]:
    existing = None
    if fixture_id:
        existing = await db.scalar(
            select(TrackedBet).where(
                TrackedBet.fixture_id == fixture_id,
                TrackedBet.bookmaker == bookmaker,
                TrackedBet.market_type == market_type,
                TrackedBet.selection_name == selection_name,
                TrackedBet.user_id == user.id,
            )
        )
    if existing:
        age_hours = (
            (datetime.utcnow() - existing.created_at).total_seconds() / 3600
            if existing.created_at else 0
        )
        effective_odds = existing.odds if age_hours < 24 else odds
        return existing, False, effective_odds

    bet = TrackedBet(
        user_id=user.id,
        fixture_id=fixture_id,
        match_name=match_name,
        league=league,
        event_date=event_date,
        bookmaker=bookmaker,
        market_type=market_type,
        selection_name=selection_name,
        odds=odds,
        stake=round(stake, 2),
        dual_confidence=dual_confidence,
        dual_agreement=dual_agreement,
        recommended_stake_pct=recommended_stake_pct,
        source_rule_key=source_rule_key,
        source_rule_label=source_rule_label,
        signal_grade=signal_grade,
        notes=notes,
    )
    db.add(bet)
    await db.flush()
    return bet, True, odds


# ── Sync ─────────────────────────────────────────────────────────────────────

@router.post("/sync")
async def sync(
    run_date: Optional[str] = Query(None),
    force: bool = Query(False, description="Bypass cooldown + cache guards to recover missing scores"),
    db: AsyncSession = Depends(get_db),
):
    from fastapi import HTTPException
    target = date.fromisoformat(run_date) if run_date else date.today()
    try:
        run = await ingestion.sync_date(db, target, force=force)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Auto-compute signals after sync
    from app.services.signal_engine import compute_signals_for_date
    signal_count = await compute_signals_for_date(db, target)
    run.signals_computed = signal_count
    await db.commit()

    # Auto-settle: now that fixture statuses are refreshed, resolve pending bets.
    # Runs across all pending bets (any event_date), not only the synced date.
    settle_info = await settlement.settle_bets_for_date(db, None)

    return {
        "status": run.status,
        "run_date": target.isoformat(),
        "fixtures_pulled": run.fixtures_pulled,
        "markets_pulled": run.markets_pulled,
        "signals_computed": signal_count,
        "bets_settled": settle_info["settled"],
    }


# ── Track pick ────────────────────────────────────────────────────────────────

import logging as _logging
_track_log = _logging.getLogger("titibet.track_pick")


@router.post("/track-pick", response_model=BetOut)
async def track_pick(
    payload: TrackPickRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    uid = current_user.id if current_user else None

    try:
        # Duplicate check must follow the unique constraint semantics exactly:
        # user-scoped rows should only collide with the same user's rows, while
        # anonymous rows can still be claimed by a logged-in user later.
        existing = None
        if payload.fixture_id:
            q = select(TrackedBet).where(
                TrackedBet.fixture_id == payload.fixture_id,
                TrackedBet.bookmaker == payload.bookmaker,
                TrackedBet.market_type == payload.market_type,
                TrackedBet.selection_name == payload.selection_name,
            )
            if uid is None:
                q = q.where(TrackedBet.user_id.is_(None))
            else:
                q = q.where(
                    or_(
                        TrackedBet.user_id == uid,
                        TrackedBet.user_id.is_(None),
                    )
                )
            existing = await db.scalar(q)

        if existing:
            # Claim anonymous (legacy) row for the current logged-in user
            if uid is not None and existing.user_id is None:
                existing.user_id = uid
                await db.commit()
                await db.refresh(existing)
            return _bet_out_from_models(existing)

        bet = TrackedBet(
            user_id=uid,
            fixture_id=payload.fixture_id,
            bookmaker=payload.bookmaker,
            event_date=payload.event_date,
            match_name=payload.match_name,
            league=payload.league,
            market_type=payload.market_type,
            selection_name=payload.selection_name,
            odds=payload.odds,
            stake=round(payload.stake, 2),
            recommended_stake_pct=payload.recommended_stake_pct,
            source_rule_key=payload.source_rule_key,
            source_rule_label=payload.source_rule_label,
            signal_grade=payload.signal_grade,
            dual_confidence=payload.dual_confidence,
            dual_agreement=payload.dual_agreement,
            notes=payload.notes,
        )
        db.add(bet)
        try:
            await db.commit()
            await db.refresh(bet)
        except IntegrityError:
            await db.rollback()
            # Race condition: another request inserted between our check and INSERT.
            # Fall back to fetching the now-existing row.
            fallback_q = select(TrackedBet).where(
                TrackedBet.fixture_id == payload.fixture_id,
                TrackedBet.bookmaker == payload.bookmaker,
                TrackedBet.market_type == payload.market_type,
                TrackedBet.selection_name == payload.selection_name,
            )
            if uid is None:
                fallback_q = fallback_q.where(TrackedBet.user_id.is_(None))
            else:
                fallback_q = fallback_q.where(
                    or_(
                        TrackedBet.user_id == uid,
                        TrackedBet.user_id.is_(None),
                    )
                )
            bet = await db.scalar(fallback_q)
            if bet is None:
                raise HTTPException(status_code=409, detail="Duplicate bet could not be resolved.")

        return _bet_out_from_models(bet)

    except HTTPException:
        raise
    except Exception as exc:
        _track_log.exception("track_pick failed for user=%s fixture=%s market=%s: %s",
                             uid, payload.fixture_id, payload.market_type, exc)
        raise HTTPException(status_code=500, detail=f"Track pick failed: {type(exc).__name__}: {exc}") from exc


# ── Bets ──────────────────────────────────────────────────────────────────────

@router.get("/bets", response_model=list[BetOut])
async def list_bets(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    market_type: Optional[str] = Query(None),
    result_status: Optional[str] = Query(None),
    league: Optional[str] = Query(None),
    limit: int = Query(300),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    from app.models import Fixture as FixtureModel
    from sqlalchemy.orm import outerjoin

    query = (
        select(TrackedBet, FixtureModel)
        .outerjoin(FixtureModel, TrackedBet.fixture_id == FixtureModel.id)
        .order_by(TrackedBet.created_at.desc())
        .limit(limit)
    )
    if current_user:
        query = query.where(TrackedBet.user_id == current_user.id)
    else:
        query = query.where(TrackedBet.user_id.is_(None))
    if date_from:
        query = query.where(
            TrackedBet.event_date >= date.fromisoformat(date_from)
        )
    if date_to:
        # Include bets where event_date is NULL (manual picks with no fixture) so
        # they are never silently excluded by an upper-bound date filter.
        query = query.where(
            or_(
                TrackedBet.event_date.is_(None),
                TrackedBet.event_date <= date.fromisoformat(date_to),
            )
        )
    if market_type:
        query = query.where(TrackedBet.market_type == market_type)
    if result_status:
        query = query.where(TrackedBet.result_status == result_status)
    if league:
        query = query.where(TrackedBet.league.ilike(f"%{league}%"))

    rows = await db.execute(query)
    out = []
    for bet, fixture in rows.all():
        home = fixture.home_team if fixture else None
        away = fixture.away_team if fixture else None
        # Rebuild match_name from fixture if the stored value looks corrupt (" vs ")
        name = bet.match_name
        if home and away and (not name or name.strip() == "vs"):
            name = f"{home} vs {away}"
        out.append(BetOut(
            id=bet.id, fixture_id=bet.fixture_id,
            bookmaker=bet.bookmaker, event_date=bet.event_date,
            match_name=name, home_team=home, away_team=away,
            league=bet.league, market_type=bet.market_type,
            selection_name=bet.selection_name, odds=bet.odds, stake=bet.stake,
            recommended_stake_pct=bet.recommended_stake_pct,
            source_rule_key=bet.source_rule_key, source_rule_label=bet.source_rule_label,
            signal_grade=bet.signal_grade, dual_confidence=bet.dual_confidence,
            dual_agreement=bet.dual_agreement,
            result_status=bet.result_status, profit_loss=bet.profit_loss,
            notes=bet.notes, created_at=bet.created_at, settled_at=bet.settled_at,
            closing_odds=bet.closing_odds, clv_pct=bet.clv_pct,
            # Match result — None until the fixture finishes and ingestion populates scores
            home_score=fixture.home_score if fixture else None,
            away_score=fixture.away_score if fixture else None,
            fixture_status=fixture.status if fixture else None,
            kickoff_at=fixture.kickoff_at if fixture else None,
        ))
    return out


@router.patch("/bets/{bet_id}", response_model=BetOut)
async def update_bet(
    bet_id: int,
    payload: BetUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    bet = await db.scalar(
        select(TrackedBet).where(
            TrackedBet.id == bet_id,
            TrackedBet.user_id == current_user.id,
        )
    )
    if not bet:
        raise HTTPException(404, "Bet not found")
    prior_status = bet.result_status
    if payload.stake is not None:
        bet.stake = round(payload.stake, 2)
    if payload.result_status is not None:
        _apply_settlement(bet, payload.result_status)
    elif payload.stake is not None and prior_status in {"Won", "Lost", "Void"}:
        # Stake-only edits on settled bets must recompute P/L so analytics, ROI,
        # and ticket summaries remain trustworthy.
        _apply_settlement(bet, prior_status)
    if payload.notes is not None:
        bet.notes = payload.notes
    await db.commit()
    await settlement.refresh_accumulator_tickets(db, user_id=current_user.id)
    await db.refresh(bet)
    return bet


@router.post("/bets/import")
async def bulk_import_bets(
    rows: list[dict],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Bulk-import historical bets from a CSV paste.

    Each row must have: date, match, market, odds, stake
    Optional fields: bookmaker, result (Won/Lost/Void/Pending), notes

    Returns: { imported, skipped, errors }
    """
    imported = 0
    skipped  = 0
    errors: list[str] = []

    VALID_RESULTS = {"Won", "Lost", "Void", "Pending"}

    for i, row in enumerate(rows):
        try:
            raw_date = str(row.get("date", "") or "").strip()
            match    = str(row.get("match", "") or "").strip()
            market   = str(row.get("market", "") or "").strip()
            bookmaker = str(row.get("bookmaker", "") or "Betway").strip() or "Betway"
            notes    = str(row.get("notes", "") or "").strip() or None

            odds  = float(row.get("odds", 0) or 0)
            stake = float(row.get("stake", 0) or 0)

            result_raw = str(row.get("result", "") or "Pending").strip()
            result     = result_raw if result_raw in VALID_RESULTS else "Pending"

            if not match or not market or odds <= 1.0 or stake <= 0:
                skipped += 1
                continue

            event_date = None
            if raw_date:
                try:
                    event_date = date.fromisoformat(raw_date)
                except ValueError:
                    pass

            bet = TrackedBet(
                user_id=current_user.id,
                match_name=match,
                market_type=market,
                selection_name=market,   # no separate selection for manual rows
                bookmaker=bookmaker,
                odds=round(odds, 4),
                stake=round(stake, 2),
                event_date=event_date,
                notes=notes,
                result_status=result,
            )

            # Compute P&L for settled rows
            if result == "Won":
                bet.profit_loss = round(stake * (odds - 1.0), 2)
                bet.settled_at  = datetime.utcnow()
            elif result in ("Lost", "Void"):
                bet.profit_loss = round(-stake, 2) if result == "Lost" else 0.0
                bet.settled_at  = datetime.utcnow()

            db.add(bet)
            imported += 1

        except Exception as exc:
            errors.append(f"Row {i + 1}: {exc}")

    await db.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors}


@router.delete("/bets/pending")
async def delete_pending(
    date_from: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = delete(TrackedBet).where(
        TrackedBet.result_status == "Pending",
        TrackedBet.user_id == current_user.id,
    )
    if date_from:
        q = q.where(TrackedBet.event_date >= date.fromisoformat(date_from))
    await db.execute(q)
    await db.commit()
    return {"deleted": True}


# ── Settlement ────────────────────────────────────────────────────────────────

@router.post("/settle-results")
async def settle_results(
    run_date: Optional[str] = Query(None),
    force_sync: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Settle all pending bets whose fixtures are now final.

    run_date: optional ISO date — restrict to bets for that date (default: all pending).
    force_sync: when True, re-pulls fixture data from the API before settling.
                Use this when bets are stuck Pending because the DB still shows a
                non-final status (e.g. "2H") from when the backend was last online.
    """
    target = date.fromisoformat(run_date) if run_date else None

    if force_sync:
        # Re-sync the target date (or all past dates with pending bets) to refresh
        # fixture statuses from the API before running settlement.
        if target:
            try:
                await ingestion.sync_date(db, target, force=True)
            except Exception as e:
                pass  # log but don't block settlement attempt
        else:
            # No specific date — use the scheduler catch-up logic for all past dates.
            si = await settlement.settle_bets_for_date(db, None, user_id=current_user.id)
            clv_result = await compute_clv_all(db, force=False, user_id=current_user.id)
            return {"settled": si["settled"], "clv": clv_result, "synced": True}

    si = await settlement.settle_bets_for_date(db, target, user_id=current_user.id)
    clv_result = await compute_clv_all(db, force=False, user_id=current_user.id)
    return {"settled": si["settled"], "clv": clv_result, "synced": force_sync}


# ── CLV ───────────────────────────────────────────────────────────────────────

@router.post("/compute-clv")
async def compute_clv(
    force: bool = Query(False, description="Re-compute even if closing_odds already set"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Compute Closing Line Value for all tracked bets that have a fixture_id.
    Looks up the best available market_snapshot odds (our closing-line proxy)
    and stores closing_odds + clv_pct on each TrackedBet.
    """
    result = await compute_clv_all(db, force=force, user_id=current_user.id)
    return result


# ── Accumulators ──────────────────────────────────────────────────────────────

@router.get("/accumulators", response_model=list[AccumulatorOut])
async def list_accumulators(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    user = _require_user(current_user)
    await settlement.refresh_accumulator_tickets(db, user_id=user.id)
    q = (
        select(AccumulatorTicket)
        .where(AccumulatorTicket.user_id == user.id)
        .order_by(AccumulatorTicket.created_at.desc())
    )
    result = await db.execute(q)
    tickets = list(result.scalars().all())
    out = []
    for t in tickets:
        # LEFT join Fixture so legs without a fixture_id (manually-entered bets)
        # still render — they just won't have a score.
        legs_result = await db.execute(
            select(AccumulatorLeg, TrackedBet, Fixture)
            .join(TrackedBet, AccumulatorLeg.tracked_bet_id == TrackedBet.id)
            .outerjoin(Fixture, TrackedBet.fixture_id == Fixture.id)
            .where(AccumulatorLeg.ticket_id == t.id)
            .order_by(AccumulatorLeg.leg_order)
        )
        legs_data = [
            {"leg_order": leg.leg_order, "bet": {
                "id": bet.id, "match_name": bet.match_name,
                "market_type": bet.market_type, "odds": bet.odds,
                "result_status": bet.result_status,
                # Match result — None until the fixture finishes
                "home_score":     fixture.home_score if fixture else None,
                "away_score":     fixture.away_score if fixture else None,
                "fixture_status": fixture.status     if fixture else None,
            }}
            for leg, bet, fixture in legs_result.all()
        ]
        out.append(AccumulatorOut(
            id=t.id, ticket_date=t.ticket_date, name=t.name,
            stake=t.stake, combined_odds=t.combined_odds,
            result_status=t.result_status, profit_loss=t.profit_loss,
            created_at=t.created_at, legs=legs_data,
            ticket_source=t.ticket_source or "manual",
        ))
    return out


@router.post("/accumulators/deduplicate")
async def deduplicate_accumulators(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Remove duplicate accumulator tickets for the current user.
    Duplicates are tickets with the same (name, ticket_date). The oldest
    ticket (lowest id) is kept; all newer duplicates and their legs are deleted.
    Returns { removed } — number of duplicate tickets deleted.
    """
    user = _require_user(current_user)

    # Fetch all tickets for this user ordered oldest first
    result = await db.execute(
        select(AccumulatorTicket)
        .where(AccumulatorTicket.user_id == user.id)
        .order_by(AccumulatorTicket.id.asc())
    )
    all_tickets = list(result.scalars().all())

    # Group by (name, ticket_date) — keep the first (oldest) in each group
    seen: dict[tuple, int] = {}   # (name, date) → kept ticket id
    to_delete: list[int] = []

    for t in all_tickets:
        key = (t.name or "", str(t.ticket_date or ""))
        if key in seen:
            to_delete.append(t.id)
        else:
            seen[key] = t.id

    if to_delete:
        await db.execute(
            delete(AccumulatorLeg).where(AccumulatorLeg.ticket_id.in_(to_delete))
        )
        await db.execute(
            delete(AccumulatorTicket).where(
                AccumulatorTicket.id.in_(to_delete),
                AccumulatorTicket.user_id == user.id,
            )
        )
        await db.commit()

    return {"removed": len(to_delete)}


@router.delete("/accumulators/{ticket_id}")
async def delete_accumulator(
    ticket_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    user = _require_user(current_user)
    ticket = await db.scalar(
        select(AccumulatorTicket).where(
            AccumulatorTicket.id == ticket_id,
            AccumulatorTicket.user_id == user.id,
        )
    )
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    await db.execute(delete(AccumulatorLeg).where(AccumulatorLeg.ticket_id == ticket_id))
    await db.execute(delete(AccumulatorTicket).where(
        AccumulatorTicket.id == ticket_id,
        AccumulatorTicket.user_id == user.id,
    ))
    await db.commit()
    return {"deleted": True, "id": ticket_id}


@router.post("/accumulators", response_model=AccumulatorOut)
async def create_accumulator(
    payload: AccumulatorCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    user = _require_user(current_user)
    if len(payload.legs) < 2:
        raise HTTPException(400, "Accumulator requires at least 2 legs")

    # Fetch all bets
    bet_ids = [leg.tracked_bet_id for leg in payload.legs]
    bets_result = await db.execute(
        select(TrackedBet).where(
            TrackedBet.id.in_(bet_ids),
            TrackedBet.user_id == user.id,
        )
    )
    bets_by_id = {b.id: b for b in bets_result.scalars().all()}

    if len(bets_by_id) != len(bet_ids):
        raise HTTPException(400, "One or more bet IDs were not found for the current user.")

    combined_odds = 1.0
    for bet_id in bet_ids:
        b = bets_by_id.get(bet_id)
        if b:
            combined_odds *= b.odds

    ticket = AccumulatorTicket(
        user_id=user.id,
        ticket_date=payload.ticket_date or date.today(),
        name=payload.name,
        stake=round(payload.stake, 2),
        combined_odds=round(combined_odds, 4),
        ticket_source="manual",
    )
    db.add(ticket)
    await db.flush()

    for leg_req in payload.legs:
        db.add(AccumulatorLeg(
            ticket_id=ticket.id,
            tracked_bet_id=leg_req.tracked_bet_id,
            leg_order=leg_req.leg_order,
        ))
    await db.commit()
    await db.refresh(ticket)
    return AccumulatorOut(
        id=ticket.id, ticket_date=ticket.ticket_date, name=ticket.name,
        stake=ticket.stake, combined_odds=ticket.combined_odds,
        result_status=ticket.result_status, profit_loss=ticket.profit_loss,
        created_at=ticket.created_at, legs=[], ticket_source="manual",
    )


@router.post("/accumulators/generate")
async def generate_accumulators(
    body: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Preview auto-generated accumulator suggestions — does NOT write to DB.
    Returns tiered suggestions (safe/value/bold) plus a flat union for backwards compatibility.
    Client confirms a suggestion via POST /accumulators/confirm.
    Requires Pro or Elite subscription.
    """
    is_pro = (
        current_user is not None
        and current_user.tier in ("pro", "elite")
        and current_user.subscription_status == "active"
    )
    if not is_pro:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accumulator generation requires a Pro or Elite subscription.",
        )

    date_str = body.get("date")
    run_date = date.fromisoformat(date_str) if date_str else date.today()
    generation_mode = "weighted"

    ranked_tickets = await generate_rank_bucket_suggestions(
        db,
        run_date,
        use_performance_weights=True,
        user_id=current_user.id,
    )
    return {
        "date": run_date.isoformat(),
        "tiers": {},
        "ranked_tickets": ranked_tickets,
        "suggestions": ranked_tickets,
        "generation_mode": generation_mode,
    }


@router.post("/accumulators/confirm", response_model=AccumulatorOut)
async def confirm_accumulator(
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Confirm a generated suggestion: auto-tracks each leg as a TrackedBet
    (reuses existing bet if the same fixture+market is already tracked), then
    creates the AccumulatorTicket.
    """
    user = _require_user(current_user)
    legs_data: list[dict] = body.get("legs", [])
    stake = float(body.get("stake", 10.0))
    ranked_bucket_key = body.get("ranked_bucket_key")
    allow_over_100x = body.get("allow_over_100x") is True and ranked_bucket_key == "top10"
    name = body.get("name") or f"Auto Acca — {len(legs_data)} legs"

    if stake <= 0:
        raise HTTPException(400, "Stake must be a positive number")

    if len(legs_data) < 2:
        raise HTTPException(400, "Accumulator requires at least 2 legs")

    # Reject accumulators where the same fixture appears in more than one leg —
    # correlated legs make the combined odds misleading and inflate win probability.
    leg_fixture_ids = [leg["fixture_id"] for leg in legs_data]
    if len(leg_fixture_ids) != len(set(leg_fixture_ids)):
        raise HTTPException(400, "Each fixture may only appear once per accumulator")

    # Pre-validate combined odds ceiling BEFORE tracking any bets so that a
    # rejected ticket never leaves orphan TrackedBet rows in the database.
    combined_odds_preview = 1.0
    for leg in legs_data:
        combined_odds_preview *= float(leg["odds"])
    if combined_odds_preview > 100.0 and not allow_over_100x:
        raise HTTPException(
            400,
            f"Combined odds {combined_odds_preview:.1f} exceed the 100x ceiling. "
            "Remove a leg or choose shorter-odds selections to keep the ticket winnable."
        )

    bet_ids: list[int] = []
    combined_odds = 1.0

    for leg in legs_data:
        fix_id = leg["fixture_id"]
        market = leg["market"]
        bookmaker = leg.get("bookmaker") or "Manual"
        odds = float(leg["odds"])
        match_name = leg.get("match_name", "")
        league = leg.get("league")
        event_date_str = leg.get("event_date")
        event_date_val = date.fromisoformat(event_date_str) if event_date_str else None
        bet, _, effective_odds = await _track_or_reuse_bet(
            db,
            user=user,
            fixture_id=fix_id,
            bookmaker=bookmaker,
            market_type=market,
            selection_name=leg.get("selection_name") or market,
            match_name=match_name,
            league=league,
            event_date=event_date_val,
            odds=odds,
            stake=stake / len(legs_data),
            dual_confidence=leg.get("confidence"),
            dual_agreement=leg.get("agreement"),
            recommended_stake_pct=leg.get("recommended_stake_pct"),
        )
        bet_ids.append(bet.id)
        combined_odds *= effective_odds

    ticket = AccumulatorTicket(
        user_id=user.id,
        ticket_date=date.today(),
        name=name,
        stake=round(stake, 2),
        combined_odds=round(combined_odds, 2),
        ticket_source="goals_acca",
    )
    db.add(ticket)
    await db.flush()

    for order, bet_id in enumerate(bet_ids):
        db.add(AccumulatorLeg(ticket_id=ticket.id, tracked_bet_id=bet_id, leg_order=order))

    await db.commit()
    await db.refresh(ticket)
    return AccumulatorOut(
        id=ticket.id, ticket_date=ticket.ticket_date, name=ticket.name,
        stake=ticket.stake, combined_odds=ticket.combined_odds,
        result_status=ticket.result_status, profit_loss=ticket.profit_loss,
        created_at=ticket.created_at, legs=[], ticket_source="goals_acca",
    )


@router.post("/recommended-tickets/confirm", response_model=ConfirmRecommendedTicketOut)
async def confirm_recommended_ticket(
    payload: ConfirmRecommendedTicketIn,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    user = _require_user(current_user)
    if payload.stake <= 0:
        raise HTTPException(400, "Stake must be a positive number")
    if len(payload.legs) < 2:
        raise HTTPException(400, "Recommended ticket requires at least 2 legs")

    fixture_ids = [leg.fixture_id for leg in payload.legs]
    if len(fixture_ids) != len(set(fixture_ids)):
        raise HTTPException(400, "Each fixture may only appear once per recommended ticket")

    ticket_date_val = payload.ticket_date or date.today()
    derived_name = payload.ticket_name or f"Recommended Ticket — {payload.card_key.replace('_', ' ').title()}"

    # ── Idempotency guard: return existing ticket if already tracked ─────────
    # Match by card_key (new rows) OR by name (legacy rows that stored "ai_ticket")
    existing_ticket = await db.scalar(
        select(AccumulatorTicket).where(
            AccumulatorTicket.user_id == user.id,
            AccumulatorTicket.ticket_date == ticket_date_val,
            or_(
                AccumulatorTicket.ticket_source == payload.card_key,
                AccumulatorTicket.name == derived_name,
            ),
        )
    )
    if existing_ticket:
        legs_result = await db.execute(
            select(AccumulatorLeg, TrackedBet)
            .join(TrackedBet, AccumulatorLeg.tracked_bet_id == TrackedBet.id)
            .where(AccumulatorLeg.ticket_id == existing_ticket.id)
            .order_by(AccumulatorLeg.leg_order)
        )
        existing_bets = [bet for _, bet in legs_result.all()]
        existing_fix_ids = [b.fixture_id for b in existing_bets if b.fixture_id]
        existing_fix_map: dict[int, Fixture] = {}
        if existing_fix_ids:
            fix_res = await db.execute(select(Fixture).where(Fixture.id.in_(existing_fix_ids)))
            existing_fix_map = {f.id: f for f in fix_res.scalars().all()}
        return ConfirmRecommendedTicketOut(
            card_key=payload.card_key,
            accumulator_ticket_id=existing_ticket.id,
            combined_odds=existing_ticket.combined_odds or 1.0,
            tracked_bets=[_bet_out_from_models(b, existing_fix_map.get(b.fixture_id)) for b in existing_bets],
            message=f"Already tracked.",
        )

    tracked_bets: list[TrackedBet] = []
    combined_odds = 1.0
    stake_per_leg = round(payload.stake / len(payload.legs), 2)

    for leg in payload.legs:
        event_date_val = date.fromisoformat(leg.event_date) if leg.event_date else None
        bet, _, effective_odds = await _track_or_reuse_bet(
            db,
            user=user,
            fixture_id=leg.fixture_id,
            bookmaker=leg.bookmaker or "Manual",
            market_type=leg.market,
            selection_name=leg.selection_name or leg.market,
            match_name=leg.match_name,
            league=leg.league,
            event_date=event_date_val,
            odds=leg.odds,
            stake=stake_per_leg,
            dual_confidence=leg.confidence,
            dual_agreement=leg.agreement,
            recommended_stake_pct=leg.recommended_stake_pct,
            source_rule_key=leg.source_rule_key,
            source_rule_label=f"Recommended {payload.card_key}",
            signal_grade=leg.signal_grade,
            notes=" | ".join(leg.why_tags) if leg.why_tags else None,
        )
        tracked_bets.append(bet)
        combined_odds *= effective_odds

    ticket = AccumulatorTicket(
        user_id=user.id,
        ticket_date=ticket_date_val,
        name=payload.ticket_name or f"Recommended Ticket — {payload.card_key.replace('_', ' ').title()}",
        stake=round(payload.stake, 2),
        combined_odds=round(combined_odds, 2),
        ticket_source=payload.card_key,
    )
    db.add(ticket)
    await db.flush()

    for order, bet in enumerate(tracked_bets):
        db.add(AccumulatorLeg(ticket_id=ticket.id, tracked_bet_id=bet.id, leg_order=order))

    await db.commit()
    await db.refresh(ticket)

    out_fixture_ids = [bet.fixture_id for bet in tracked_bets if bet.fixture_id]
    fixture_map: dict[int, Fixture] = {}
    if out_fixture_ids:
        fixture_result = await db.execute(select(Fixture).where(Fixture.id.in_(out_fixture_ids)))
        fixture_map = {fixture.id: fixture for fixture in fixture_result.scalars().all()}

    return ConfirmRecommendedTicketOut(
        card_key=payload.card_key,
        accumulator_ticket_id=ticket.id,
        combined_odds=ticket.combined_odds or round(combined_odds, 2),
        tracked_bets=[_bet_out_from_models(bet, fixture_map.get(bet.fixture_id)) for bet in tracked_bets],
        message=f"Recommended {payload.card_key.replace('_', ' ')} ticket saved.",
    )


# ── Analytics ─────────────────────────────────────────────────────────────────

@router.get("/analytics")
async def analytics(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    market_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    q = select(TrackedBet)
    if current_user:
        q = q.where(TrackedBet.user_id == current_user.id)
    else:
        q = q.where(TrackedBet.user_id.is_(None))
    if date_from:
        q = q.where(TrackedBet.event_date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(TrackedBet.event_date <= date.fromisoformat(date_to))
    if market_type:
        q = q.where(TrackedBet.market_type == market_type)
    rows = await db.execute(q)
    bets = list(rows.scalars().all())
    return build_analytics(bets)


# ── Accumulator analytics ─────────────────────────────────────────────────────

@router.get("/analytics/accumulators")
async def accumulator_analytics(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Performance breakdown for accumulator tickets: hit rate, ROI,
    by-leg-count and by-odds-band breakdowns.
    """
    q = select(AccumulatorTicket).order_by(AccumulatorTicket.created_at.desc())
    if current_user:
        q = q.where(AccumulatorTicket.user_id == current_user.id)
    else:
        q = q.where(AccumulatorTicket.user_id.is_(None))
    result = await db.execute(q)
    tickets = list(result.scalars().all())

    # Attach legs AND market info to each ticket so build_accumulator_analytics
    # can count leg counts and analyse winning market combinations.
    for ticket in tickets:
        legs_result = await db.execute(
            select(AccumulatorLeg, TrackedBet)
            .join(TrackedBet, AccumulatorLeg.tracked_bet_id == TrackedBet.id)
            .where(AccumulatorLeg.ticket_id == ticket.id)
            .order_by(AccumulatorLeg.leg_order)
        )
        pairs = legs_result.all()
        ticket.legs = [leg for leg, _ in pairs]
        ticket.leg_markets = [bet.market_type for _, bet in pairs]

    return build_accumulator_analytics(tickets)


# ── Model insights (self-learning readout) ────────────────────────────────────

@router.get("/analytics/model-insights")
async def model_insights(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Returns the current performance weights used by the self-learning system.
    Shows how each (confidence, market) slice is performing vs expectations,
    and what multiplier is being applied to accumulator game selection.
    """
    weights = await compute_performance_weights(
        db,
        user_id=current_user.id if current_user else None,
    )
    report = weights.as_report()

    # Confidence-level summary for the dashboard cards
    conf_summary = []
    for conf, sl in weights.by_confidence.items():
        conf_summary.append({
            "confidence": conf,
            "samples": sl.samples,
            "wins": sl.wins,
            "losses": sl.losses,
            "win_rate": round(sl.win_rate * 100, 1),
            "roi": round(sl.roi * 100, 1),
            "performance_factor": sl.performance_factor,
        })
    conf_summary.sort(key=lambda x: {"High": 0, "Medium": 1, "Low": 2}.get(x["confidence"], 9))

    # Market-level summary
    market_summary = []
    for mkt, sl in weights.by_market.items():
        market_summary.append({
            "market": mkt,
            "samples": sl.samples,
            "wins": sl.wins,
            "losses": sl.losses,
            "win_rate": round(sl.win_rate * 100, 1),
            "roi": round(sl.roi * 100, 1),
            "performance_factor": sl.performance_factor,
        })
    market_summary.sort(key=lambda x: -x["roi"])

    # Rule-level performance — shows which Poisson signal rules are profitable
    rule_summary = weights.rule_report()

    return {
        "by_confidence": conf_summary,
        "by_market": market_summary,
        "by_rule": rule_summary,
        "detail": report,
    }


# ── Ingestion runs ────────────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(20)
    )
    runs = list(result.scalars().all())
    return [
        {
            "id": r.id, "run_date": r.run_date.isoformat() if r.run_date else None,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
            "fixtures_pulled": r.fixtures_pulled, "markets_pulled": r.markets_pulled,
            "signals_computed": r.signals_computed, "status": r.status,
            "error_message": r.error_message,
        }
        for r in runs
    ]



# ── Fixtures ──────────────────────────────────────────────────────────────────

@router.get("/fixtures")
async def list_fixtures(
    date_str: Optional[str] = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    target = date.fromisoformat(date_str) if date_str else date.today()
    result = await db.execute(
        select(Fixture).where(Fixture.event_date == target).order_by(Fixture.kickoff_at)
    )
    fixtures = list(result.scalars().all())
    return [
        {
            "id": f.id, "external_id": f.external_fixture_id,
            "home_team": f.home_team, "away_team": f.away_team,
            "league": f.league, "country": f.country,
            "kickoff_at": f.kickoff_at.isoformat() if f.kickoff_at else None,
            "status": f.status,
            "home_score": f.home_score, "away_score": f.away_score,
        }
        for f in fixtures
    ]

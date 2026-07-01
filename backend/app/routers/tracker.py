from __future__ import annotations
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, delete, update, or_, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, get_current_user_optional
from app.core.database import get_db
from app.models import Fixture, MarketSnapshot, TrackedBet, IngestionRun
from app.models.user import User
from app.schemas.bet import (
    TrackPickRequest,
    BetUpdate,
    BetOut,
)
from app.services import ingestion, settlement
from app.services.analytics import build_analytics
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

        # Acca bets have no fixture_id — dedup on (user_id, event_date, source_rule_key)
        if payload.source_rule_key == "acca_advisory" and payload.event_date:
            acca_q = select(TrackedBet).where(
                TrackedBet.source_rule_key == "acca_advisory",
                TrackedBet.event_date == payload.event_date,
                TrackedBet.user_id == uid if uid is not None else TrackedBet.user_id.is_(None),
            )
            existing = await db.scalar(acca_q)
            if existing:
                return _bet_out_from_models(existing)

        if payload.fixture_id:
            _system_keys = ("system_auto", "system_dual")
            # For system picks, check (fixture_id, market_type) only — bookmaker
            # varies between frontend-implied and backend-real odds, causing false
            # non-matches that create duplicate rows.
            if payload.source_rule_key in _system_keys:
                existing = await db.scalar(
                    select(TrackedBet).where(
                        TrackedBet.fixture_id == payload.fixture_id,
                        TrackedBet.market_type == payload.market_type,
                        or_(
                            TrackedBet.user_id == uid,
                            TrackedBet.user_id.is_(None),
                        ) if uid is not None else TrackedBet.user_id.is_(None),
                    )
                )
            else:
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
            if payload.source_rule_key in ("system_auto", "system_dual"):
                fallback_q = select(TrackedBet).where(
                    TrackedBet.fixture_id == payload.fixture_id,
                    TrackedBet.market_type == payload.market_type,
                    or_(
                        TrackedBet.user_id == uid,
                        TrackedBet.user_id.is_(None),
                    ) if uid is not None else TrackedBet.user_id.is_(None),
                )
            else:
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

    def _apply_filters(q):
        if date_from:
            q = q.where(TrackedBet.event_date >= date.fromisoformat(date_from))
        if date_to:
            # Include bets where event_date is NULL (manual picks with no fixture)
            q = q.where(or_(
                TrackedBet.event_date.is_(None),
                TrackedBet.event_date <= date.fromisoformat(date_to),
            ))
        if market_type:
            q = q.where(TrackedBet.market_type == market_type)
        if result_status:
            q = q.where(TrackedBet.result_status == result_status)
        if league:
            q = q.where(TrackedBet.league.ilike(f"%{league}%"))
        return q

    # Select individual columns (not whole ORM entities) so SQLAlchemy returns
    # lightweight Row namedtuples instead of instrumented ORM objects.
    # Selecting ORM entities triggers identity-map registration + back_populates
    # sync for every row via the TrackedBet.fixture relationship — ~17ms/row at
    # 204 rows was causing the 5+ second steady-state latency.
    base = (
        select(
            TrackedBet.id,
            TrackedBet.fixture_id,
            TrackedBet.bookmaker,
            TrackedBet.event_date,
            TrackedBet.match_name,
            TrackedBet.league,
            TrackedBet.market_type,
            TrackedBet.selection_name,
            TrackedBet.odds,
            TrackedBet.stake,
            TrackedBet.recommended_stake_pct,
            TrackedBet.source_rule_key,
            TrackedBet.source_rule_label,
            TrackedBet.signal_grade,
            TrackedBet.dual_confidence,
            TrackedBet.dual_agreement,
            TrackedBet.result_status,
            TrackedBet.profit_loss,
            TrackedBet.notes,
            TrackedBet.created_at,
            TrackedBet.settled_at,
            TrackedBet.closing_odds,
            TrackedBet.clv_pct,
            FixtureModel.home_team,
            FixtureModel.away_team,
            FixtureModel.home_score,
            FixtureModel.away_score,
            FixtureModel.status.label("fixture_status"),
            FixtureModel.kickoff_at,
        )
        .select_from(TrackedBet)
        .outerjoin(FixtureModel, TrackedBet.fixture_id == FixtureModel.id)
        .order_by(TrackedBet.created_at.desc())
        .limit(limit)
    )

    sub_queries = []
    if current_user:
        sub_queries.append(_apply_filters(
            base.where(TrackedBet.user_id == current_user.id)
        ))
        sub_queries.append(_apply_filters(
            base.where(
                TrackedBet.user_id.is_(None),
                TrackedBet.source_rule_key.in_(["system_auto", "system_dual"]),
            )
        ))
    else:
        sub_queries.append(_apply_filters(
            base.where(TrackedBet.user_id.is_(None))
        ))

    all_rows = []
    for q in sub_queries:
        result = await db.execute(q)
        all_rows.extend(result.mappings().all())

    all_rows.sort(key=lambda r: r["created_at"] or datetime.min, reverse=True)
    all_rows = all_rows[:limit]

    out = []
    for row in all_rows:
        home = row["home_team"]
        away = row["away_team"]
        name = row["match_name"]
        if home and away and (not name or name.strip() == "vs"):
            name = f"{home} vs {away}"
        out.append(BetOut(
            id=row["id"], fixture_id=row["fixture_id"],
            bookmaker=row["bookmaker"], event_date=row["event_date"],
            match_name=name, home_team=home, away_team=away,
            league=row["league"], market_type=row["market_type"],
            selection_name=row["selection_name"], odds=row["odds"], stake=row["stake"],
            recommended_stake_pct=row["recommended_stake_pct"],
            source_rule_key=row["source_rule_key"],
            source_rule_label=row["source_rule_label"],
            signal_grade=row["signal_grade"],
            dual_confidence=row["dual_confidence"],
            dual_agreement=row["dual_agreement"],
            result_status=row["result_status"], profit_loss=row["profit_loss"],
            notes=row["notes"], created_at=row["created_at"],
            settled_at=row["settled_at"],
            closing_odds=row["closing_odds"], clv_pct=row["clv_pct"],
            home_score=row["home_score"], away_score=row["away_score"],
            fixture_status=row["fixture_status"],
            kickoff_at=row["kickoff_at"],
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
    if payload.odds is not None:
        bet.odds = round(payload.odds, 4)
    if payload.stake is not None:
        bet.stake = round(payload.stake, 2)
    if payload.result_status is not None:
        _apply_settlement(bet, payload.result_status)
    elif (payload.stake is not None or payload.odds is not None) and prior_status in {"Won", "Lost", "Void"}:
        # Stake or odds edits on settled bets must recompute P/L.
        _apply_settlement(bet, prior_status)
    if payload.notes is not None:
        bet.notes = payload.notes
    await db.commit()
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


@router.delete("/bets/{bet_id}")
async def delete_bet(
    bet_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(TrackedBet).where(
            TrackedBet.id == bet_id,
            TrackedBet.user_id == current_user.id,
        )
    )
    bet = result.scalar_one_or_none()
    if not bet:
        raise HTTPException(404, "Bet not found")
    await db.delete(bet)
    await db.commit()
    return {"deleted": True}


@router.post("/bets/normalize-stakes")
async def normalize_stakes(
    stake: float = Query(50_000.0, description="Target flat stake to apply to all bets"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set every tracked bet for this user to the given flat stake and recompute P/L."""
    result = await db.execute(
        select(TrackedBet).where(
            or_(
                TrackedBet.user_id == current_user.id,
                and_(
                    TrackedBet.user_id.is_(None),
                    TrackedBet.source_rule_key.in_(["system_auto", "system_dual"]),
                ),
            )
        )
    )
    bets = list(result.scalars().all())
    updated = 0
    for bet in bets:
        if bet.stake == stake:
            continue
        bet.stake = round(stake, 2)
        if bet.result_status in {"Won", "Lost", "Void"}:
            _apply_settlement(bet, bet.result_status)
        updated += 1
    await db.commit()
    return {"updated": updated, "stake": stake}


@router.post("/bets/deduplicate")
async def deduplicate_bets(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Keep the highest-stake bet per (fixture_id, market_type) group; delete the rest."""
    result = await db.execute(
        select(TrackedBet).where(
            TrackedBet.fixture_id.isnot(None),
            or_(
                TrackedBet.user_id == current_user.id,
                and_(
                    TrackedBet.user_id.is_(None),
                    TrackedBet.source_rule_key.in_(["system_auto", "system_dual"]),
                ),
            ),
        ).order_by(TrackedBet.fixture_id, TrackedBet.market_type, TrackedBet.stake.desc(), TrackedBet.created_at.desc())
    )
    bets = list(result.scalars().all())

    seen: dict[tuple, int] = {}   # key → winning bet id
    user_to_delete: list[int] = []   # user-owned duplicates
    system_to_delete: list[int] = [] # orphan system (user_id=None) duplicates
    for bet in bets:
        key = (bet.fixture_id, bet.market_type)
        if key not in seen:
            seen[key] = bet.id
        else:
            if bet.user_id is None:
                system_to_delete.append(bet.id)
            else:
                user_to_delete.append(bet.id)

    removed = 0
    if user_to_delete:
        r = await db.execute(
            delete(TrackedBet).where(
                TrackedBet.id.in_(user_to_delete),
                TrackedBet.user_id == current_user.id,
            )
        )
        removed += r.rowcount
    if system_to_delete:
        r = await db.execute(
            delete(TrackedBet).where(
                TrackedBet.id.in_(system_to_delete),
                TrackedBet.user_id.is_(None),
            )
        )
        removed += r.rowcount
    if removed:
        await db.commit()

    return {"removed": removed}


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
        q = q.where(
            or_(
                TrackedBet.user_id == current_user.id,
                and_(
                    TrackedBet.user_id.is_(None),
                    TrackedBet.source_rule_key.in_(["system_auto", "system_dual"]),
                ),
            )
        )
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


# ── Model insights (self-learning readout) ────────────────────────────────────

@router.get("/analytics/model-insights")
async def model_insights(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Returns the current performance weights used by the self-learning system.
    Shows how each (confidence, market) slice is performing vs expectations,
    and what multiplier is being applied to signal scoring.
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

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import BacktestResult
from app.services.backtester import run_backtest, _summarise

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.post("/run")
async def run(
    body: dict = {},
    db: AsyncSession = Depends(get_db),
):
    market = body.get("market")
    league_id = body.get("league_id")
    league_name = body.get("league_name")
    min_edge = float(body.get("min_edge", 0.05))
    engine = body.get("engine", "dual")
    confidence_filter = body.get("confidence_filter")
    date_from_str = body.get("date_from")
    date_to_str = body.get("date_to")

    df = date.fromisoformat(date_from_str) if date_from_str else None
    dt = date.fromisoformat(date_to_str) if date_to_str else None

    summary = await run_backtest(
        db=db, market=market, league_id=league_id, league_name=league_name,
        min_edge=min_edge, date_from=df, date_to=dt,
        engine=engine, confidence_filter=confidence_filter,
    )
    return summary


@router.get("/results")
async def results(
    market: Optional[str] = Query(None),
    engine: Optional[str] = Query(None),
    confidence: Optional[str] = Query(None),
    limit: int = Query(500),
    db: AsyncSession = Depends(get_db),
):
    q = select(BacktestResult).order_by(BacktestResult.fixture_date.desc()).limit(limit)
    if market:
        q = q.where(BacktestResult.market == market)
    if engine:
        q = q.where(BacktestResult.source_engine == engine)
    if confidence:
        q = q.where(BacktestResult.dual_confidence == confidence)
    rows = await db.execute(q)
    return list(rows.scalars().all())


@router.get("/summary")
async def summary(
    market: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(BacktestResult)
    if market:
        q = q.where(BacktestResult.market == market)
    rows = await db.execute(q)
    results = list(rows.scalars().all())
    return _summarise(results)


@router.get("/bankroll-curve")
async def bankroll_curve(
    market: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(BacktestResult).order_by(BacktestResult.fixture_date)
    if market:
        q = q.where(BacktestResult.market == market)
    rows = await db.execute(q)
    results = list(rows.scalars().all())
    bankroll = 100.0
    curve = []
    for r in results:
        bankroll += r.profit_loss
        curve.append({
            "date": r.fixture_date.isoformat() if r.fixture_date else None,
            "bankroll": round(bankroll, 2),
            "won": r.bet_result == 1,
            "market": r.market,
        })
    return curve

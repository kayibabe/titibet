"""
backfill_bets.py
One-shot script: create TrackedBets for all signals 2026-05-17 to 2026-05-28,
settle them, then run both learning pipelines.
"""
import asyncio, sys, os
sys.path.insert(0, ".")
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(".env")

from datetime import date, timedelta
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.core.database import AsyncSessionLocal
import app.models  # noqa: F401 — registers all ORM models so FK resolution works
from app.models.user import User  # noqa: F401 — needed for TrackedBet.user_id FK
from app.models.signal import Signal
from app.models.fixture import Fixture
from app.models.bet import TrackedBet
from app.services.settlement import settle_bets_for_date
from app.services.loss_analysis_agent import run_loss_analysis_pipeline
from app.services.strategy_pipeline import run_strategy_pipeline

STAKE   = 10_000.0
USER_ID = 1
START   = date(2026, 5, 17)
END     = date(2026, 5, 28)


async def step1_create_bets():
    created = skipped = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Signal, Fixture)
            .join(Fixture, Signal.fixture_id == Fixture.id)
            .where(Fixture.event_date >= START, Fixture.event_date <= END)
        )
        rows = result.all()
        print(f"[1] Found {len(rows)} signals for {START} to {END}")

        for sig, fix in rows:
            bookmaker = sig.bayesian_bookmaker or "System"
            odds      = sig.bayesian_best_odd or 1.0
            bet = TrackedBet(
                user_id=USER_ID,
                fixture_id=fix.id,
                bookmaker=bookmaker,
                event_date=fix.event_date,
                match_name=f"{fix.home_team} vs {fix.away_team}",
                league=fix.league,
                market_type=sig.market,
                selection_name=sig.market,
                odds=odds,
                stake=STAKE,
                recommended_stake_pct=sig.dual_recommended_stake_pct,
                source_rule_key=sig.poisson_rule_key,
                signal_grade=sig.poisson_grade,
                dual_confidence=sig.dual_confidence,
                dual_agreement=sig.dual_agreement,
                result_status="Pending",
                profit_loss=0.0,
            )
            db.add(bet)
            try:
                await db.flush()
                created += 1
            except IntegrityError:
                await db.rollback()
                skipped += 1

        await db.commit()
        print(f"[1] Created: {created}  |  Skipped (already existed): {skipped}")
    return created


async def step2_settle():
    print("[2] Settling bets for all dates ...")
    totals = dict(settled=0, skip_no_fixture=0, skip_not_final=0, skip_no_score=0, skip_no_market=0)
    d = START
    while d <= END:
        async with AsyncSessionLocal() as db:
            result = await settle_bets_for_date(db, d)
            totals["settled"]         += result["settled"]
            totals["skip_not_final"]  += result["skip_not_final"]
            totals["skip_no_score"]   += result["skip_no_score"]
        d += timedelta(days=1)
    print(f"[2] Settlement complete: {totals}")
    return totals


async def step3_loss_pipeline():
    print("[3] Running loss analysis pipeline ...")
    try:
        async with AsyncSessionLocal() as db:
            report = await run_loss_analysis_pipeline(db, user_id=USER_ID)
        print(f"[3] Loss pipeline done: {report}")
    except Exception as e:
        print(f"[3] Loss pipeline error (non-fatal): {e}")


async def step4_strategy_pipeline():
    print("[4] Running strategy pipeline ...")
    try:
        async with AsyncSessionLocal() as db:
            report = await run_strategy_pipeline(db)
        print(f"[4] Strategy pipeline done: accepted={getattr(report, 'accepted', '?')}  rejected={getattr(report, 'rejected', '?')}")
    except Exception as e:
        print(f"[4] Strategy pipeline error (non-fatal): {e}")


async def main():
    await step1_create_bets()
    await step2_settle()
    await step3_loss_pipeline()
    await step4_strategy_pipeline()
    print("[done] All steps complete.")

asyncio.run(main())

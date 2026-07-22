from __future__ import annotations

# Load .env before any other module reads settings — ensures API keys are in
# os.environ regardless of the working directory uvicorn was launched from.
from pathlib import Path as _Path
from dotenv import load_dotenv as _load_dotenv
_load_dotenv(_Path(__file__).resolve().parent.parent / ".env", override=True)

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import jwt
from jwt import PyJWTError as JWTError
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.database import init_db, engine, AsyncSessionLocal
from app.core.migrations import run_migrations
from app.routers import signals, tracker, analytics, backtest, advisor, arb as arb_router
from app.routers import leaderboard as leaderboard_router
from app.routers import loss_analysis as loss_analysis_router
from app.routers import auth as auth_router
from app.routers import admin as admin_router
from app.routers import payments as payments_router
from app.scheduler import get_scheduler
import app.models.user  # noqa: F401 — ensures users table is created by init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("titibet")
settings = get_settings()


class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    When API_KEY is configured, /api/* requests must include either:
      - matching X-API-Key, or
      - a valid Authorization: Bearer JWT (same secret as auth).

    When API_KEY is empty (default) the middleware is a no-op — safe for local dev.
    /health is always exempt so load-balancers and startup probes keep working.
    """
    async def dispatch(self, request: Request, call_next):
        # Auth endpoints are always public — JWT handles their own security.
        exempt = (
            request.url.path.startswith("/api/auth/")
            or request.url.path.startswith("/api/admin/")
            or request.url.path.startswith("/api/payments/")
        )
        if settings.api_key and request.url.path.startswith("/api/") and not exempt:
            if request.headers.get("X-API-Key", "") == settings.api_key:
                return await call_next(request)
            auth = request.headers.get("Authorization") or ""
            if auth.startswith("Bearer "):
                token = auth.removeprefix("Bearer ").strip()
                try:
                    jwt.decode(
                        token,
                        settings.jwt_secret,
                        algorithms=[settings.jwt_algorithm],
                    )
                    return await call_next(request)
                except JWTError:
                    pass
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )
        return await call_next(request)


async def _cleanup_stale_ingestion_runs() -> int:
    """
    Mark any ingestion run that never finished (no ended_at) as 'error'.
    These are left behind whenever the backend is restarted mid-sync.
    Safe to run at every startup — only touches rows with ended_at IS NULL.
    """
    from sqlalchemy import text
    from app.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(text("""
            UPDATE ingestion_runs
            SET status      = 'error',
                ended_at    = started_at,
                error_message = 'Marked as error on startup cleanup — backend was restarted mid-sync'
            WHERE (status = 'running' OR status IS NULL)
              AND ended_at IS NULL
        """))
        await db.commit()
        return result.rowcount


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await run_migrations(engine)

    # Housekeeping: recover from any mid-sync backend restarts
    stale = await _cleanup_stale_ingestion_runs()
    if stale:
        logger.info("Startup cleanup: marked %d stale ingestion run(s) as error", stale)

    scheduler = get_scheduler()
    scheduler.start()
    logger.info("TiTiBet starting up — scheduler running %d jobs", len(scheduler.get_jobs()))

    # Run the startup sync (catch-up settlement + today's ingestion + signal compute)
    # in the background so it never blocks app startup or health checks. startup_sync
    # self-guards on SKIP_STARTUP_SYNC. It was defined but never wired in, so the app
    # only synced at scheduled cron times and never refreshed "today" on boot — which
    # left the signals/tracker empty after a restart until the next scheduled sync.
    import asyncio as _asyncio
    _force_today = os.getenv("RUN_FORCE_SYNC_TODAY", "").lower() in ("1", "true", "yes")

    # Normal boot: run the startup sync. Skipped when a forced re-sync is requested,
    # so the two don't write to SQLite concurrently (which caused "database is locked").
    if not _force_today:
        from app.scheduler import startup_sync as _startup_sync
        _asyncio.create_task(_startup_sync())

    # One-shot forced re-sync for today, gated by an env flag. Bypasses the sync
    # cooldown/cache to re-pull fixtures AND odds from the live API and recompute
    # signals — used to recover today's signals. Set RUN_FORCE_SYNC_TODAY=true,
    # deploy/restart, then unset. Runs alone (startup_sync skipped above).
    # One-shot: purge pre-Jul-2-2026 contaminated tracked_bets, loss_analyses,
    # and learning_proposals. Set RUN_PURGE_PRE_JUL2=true, deploy/restart, then
    # unset. Pre-Jul-2 tracked_bets stored 1st-half bookmaker odds (~2x real FT
    # odds), making all historical WR/ROI analytics unreliable.
    if os.getenv("RUN_PURGE_PRE_JUL2", "").lower() in ("1", "true", "yes"):
        async def _purge_pre_jul2():
            from app.core.database import AsyncSessionLocal as _S
            from sqlalchemy import text as _text
            CUTOFF = "2026-07-02"
            async with _S() as _db:
                try:
                    r = await _db.execute(_text("SELECT COUNT(*) FROM tracked_bets WHERE event_date < :c"), {"c": CUTOFF})
                    n = r.scalar()
                    await _db.execute(_text("DELETE FROM tracked_bets WHERE event_date < :c"), {"c": CUTOFF})
                    await _db.execute(_text("DELETE FROM loss_analyses"))
                    await _db.execute(_text("DELETE FROM learning_proposals"))
                    await _db.commit()
                    r2 = await _db.execute(_text("SELECT COUNT(*) FROM tracked_bets"))
                    logger.info("PURGE pre-Jul-2: deleted %d bets, %d remaining", n, r2.scalar())
                except Exception:
                    logger.exception("PURGE pre-Jul-2 failed")
        _asyncio.create_task(_purge_pre_jul2())

    # One-shot: delete tracked_bets that fail the current gate stack.
    # Run after any gate change to align historical data with current rules.
    # Set RUN_PURGE_NON_QUALIFYING=true, deploy/restart, then unset.
    if os.getenv("RUN_PURGE_NON_QUALIFYING", "").lower() in ("1", "true", "yes"):
        async def _purge_non_qualifying():
            from app.core.database import AsyncSessionLocal as _S
            from sqlalchemy import text as _text
            from app.core.config import (
                DISABLED_MARKETS, DISABLED_LEAGUES, MARKET_MIN_ODDS,
                DUAL_HIGH_ODDS_CEILING, POISSON_ONLY_MAX_ODDS,
                OVER_GOALS_SUPPRESSED_LEAGUES, HO05_DATA_POOR_COUNTRIES,
                COPA_HO05_SUPPRESSED_LEAGUES, is_womens_fixture,
            )

            WOMEN_OVER = {"Home Over 0.5","Away Over 0.5","Over 1.5","Over 2.5"}
            OVER_MKT   = {"Over 1.5","Over 2.5","Home Over 0.5","Home Over 1.5",
                          "Away Over 0.5","Away Over 1.5"}
            AWAY_SUPP  = {"primera b metropolitana"}
            BOTH_MED_DISABLED = {"copa rio","primera nacional"}

            async with _S() as _db:
                try:
                    rows = (await _db.execute(_text("""
                        SELECT tb.id, tb.market_type, tb.odds, tb.dual_confidence,
                               tb.dual_agreement, tb.league,
                               f.country, f.league_tier, f.home_team, f.away_team,
                               s.contradiction
                        FROM tracked_bets tb
                        LEFT JOIN fixtures f ON f.id = tb.fixture_id
                        LEFT JOIN signals  s ON s.fixture_id = tb.fixture_id
                                             AND s.market = tb.market_type
                        WHERE tb.result_status IN ('Won','Lost') AND tb.stake > 0
                    """))).fetchall()

                    fail_ids = []
                    for r in rows:
                        mkt    = (r.market_type or "").strip()
                        conf   = (r.dual_confidence or "").strip()
                        agree  = (r.dual_agreement or "").strip()
                        odds   = r.odds or 0.0
                        league = (r.league or "").lower().strip()
                        country= (r.country or "").lower().strip()
                        tier   = r.league_tier or 3
                        contra = r.contradiction or 0
                        home_t = r.home_team or ""
                        away_t = r.away_team or ""

                        blocked = False
                        if mkt in DISABLED_MARKETS: blocked = True
                        elif league in DISABLED_LEAGUES or "friendlies" in league: blocked = True
                        elif mkt in OVER_MKT and any(k in league for k in OVER_GOALS_SUPPRESSED_LEAGUES): blocked = True
                        elif mkt in WOMEN_OVER and is_womens_fixture(league, home_t, away_t): blocked = True
                        elif conf == "Low": blocked = True
                        elif contra: blocked = True
                        elif agree == "Both" and conf == "Medium" and (odds < 1.50 or odds >= 1.95): blocked = True
                        elif agree == "Both" and conf == "Medium" and league in BOTH_MED_DISABLED: blocked = True
                        elif mkt == "Over 1.5" and agree == "Bayesian Only": blocked = True
                        elif conf == "High" and agree == "Both" and mkt in DUAL_HIGH_ODDS_CEILING and odds >= DUAL_HIGH_ODDS_CEILING[mkt]: blocked = True
                        elif agree == "Poisson Only" and mkt in POISSON_ONLY_MAX_ODDS and odds >= POISSON_ONLY_MAX_ODDS[mkt]: blocked = True
                        elif mkt == "Home Over 0.5" and conf == "High" and agree == "Both" and tier >= 3 and country in HO05_DATA_POOR_COUNTRIES: blocked = True
                        elif mkt == "Home Over 0.5" and any(kw in league for kw in COPA_HO05_SUPPRESSED_LEAGUES): blocked = True
                        elif mkt in {"Away Over 0.5","Away Over 1.5"} and any(k in league for k in AWAY_SUPP): blocked = True
                        elif mkt == "Over 2.5" and tier >= 3: blocked = True
                        else:
                            min_o = MARKET_MIN_ODDS.get(mkt)
                            if min_o and odds < min_o: blocked = True

                        if blocked:
                            fail_ids.append(r.id)

                    if fail_ids:
                        await _db.execute(
                            _text(f"DELETE FROM tracked_bets WHERE id IN ({','.join(str(i) for i in fail_ids)})")
                        )
                        await _db.commit()

                    r2 = (await _db.execute(_text("SELECT COUNT(*) FROM tracked_bets"))).scalar()
                    logger.info("PURGE non-qualifying: deleted %d bets, %d remaining", len(fail_ids), r2)
                except Exception:
                    logger.exception("PURGE non-qualifying failed")
        _asyncio.create_task(_purge_non_qualifying())

    if _force_today:
        logger.info("RUN_FORCE_SYNC_TODAY set — forcing fresh sync+compute for today")

        async def _force_sync_today():
            from app.core.database import AsyncSessionLocal as _S
            from app.services import ingestion as _ing
            from app.services.signal_engine import compute_signals_for_date as _csfd
            from datetime import date as _d
            async with _S() as _db:
                try:
                    run = await _ing.sync_date(_db, _d.today(), force=True)
                    logger.info("FORCE sync: status=%s fixtures=%s", run.status, run.fixtures_pulled)
                    count = await _csfd(_db, _d.today())
                    await _db.commit()
                    logger.info("FORCE compute: %d signals for today", count)
                except Exception:
                    logger.exception("FORCE sync+compute failed")

        _asyncio.create_task(_force_sync_today())

    yield
    scheduler.shutdown(wait=False)
    logger.info("TiTiBet shut down.")


app = FastAPI(title="TiTiBet", version="1.0.0", lifespan=lifespan)

# Order matters: CORS first so preflight OPTIONS requests are handled before auth check.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-API-Key"],
)
app.add_middleware(APIKeyMiddleware)

app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(payments_router.router)
app.include_router(signals.router)
app.include_router(tracker.router)
app.include_router(analytics.router)
app.include_router(backtest.router)
app.include_router(advisor.router)
app.include_router(loss_analysis_router.router)
app.include_router(arb_router.router)
app.include_router(leaderboard_router.router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


# Serve the React SPA when the frontend build is present (production Docker image).
# Registered LAST so it never shadows any /api/* or /health route above.
_frontend_dist = _Path(__file__).resolve().parent.parent / "frontend_dist"
if _frontend_dist.exists():
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    _assets = _frontend_dist / "assets"
    if _assets.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="static_assets")

    @app.get("/{full_path:path}")
    async def _serve_spa(full_path: str):
        candidate = _frontend_dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_frontend_dist / "index.html"))

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

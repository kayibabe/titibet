from __future__ import annotations

# Load .env before any other module reads settings — ensures API keys are in
# os.environ regardless of the working directory uvicorn was launched from.
from pathlib import Path as _Path
from dotenv import load_dotenv as _load_dotenv
_load_dotenv(_Path(__file__).resolve().parent.parent / ".env", override=True)

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.database import init_db, engine
from app.core.migrations import run_migrations
from app.routers import signals, tracker, analytics, backtest, advisor, arb as arb_router
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


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}

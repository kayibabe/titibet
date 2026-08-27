# TiTiBet

Football betting signals platform. Ingests live fixture and odds data from API-Football, runs Bayesian and Poisson models to generate signals, scores and ranks them, and surfaces the best picks via a React web app. A self-learning pipeline analyses settled losses, detects patterns, and proposes threshold adjustments validated by a backtester.

**Status: Public beta.** The platform is live and accepting test users.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Python 3.13, async (asyncio) |
| Database | SQLite via aiosqlite + SQLAlchemy 2.x async ORM |
| Task queue | APScheduler — no Celery |
| Frontend | React 19 + Vite, Tailwind CSS, lucide-react, recharts |
| Auth | JWT (PyJWT), bcrypt, tier-gated features |
| Payments | Paystack webhook integration |
| Data source | API-Football (api-sports.io) |
| Deployment | Fly.io (Johannesburg region) |

---

## Running locally

### Backend

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp .env.example .env                              # then fill in at minimum API_FOOTBALL_KEY and JWT_SECRET
python run.py
```

The API starts on `http://localhost:8010`. On first run it creates the SQLite database and runs migrations automatically.

Set `SKIP_STARTUP_SYNC=true` in `backend/.env` to skip the full ingestion sync on startup (saves API quota during development restarts). Catch-up settlement still runs.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The React app starts on `http://localhost:5173` and proxies API requests to `localhost:8010`.

---

## Environment variables

Copy `backend/.env.example` to `backend/.env` and fill in your values. Required variables:

| Variable | Description |
|---|---|
| `API_FOOTBALL_KEY` | API-Football key from api-sports.io |
| `JWT_SECRET` | Strong random string — `python -c "import secrets; print(secrets.token_hex(32))"` |

Everything else has working defaults for local dev. See `backend/.env.example` for the full reference including optional AI advisory, Telegram, SMTP, and Paystack settings.

---

## Architecture overview

```
API-Football
    ↓  ingestion.sync_date()
Fixture + MarketSnapshot rows in SQLite
    ↓  compute_signals_for_date()
Signal rows  (bayesian_* + poisson_* + dual_*)
    ↓  GET /api/signals  →  _system_rank()  →  ranked list
React SignalsPage
    ↓  user tracks a pick
TrackedBet row
    ↓  settle_bets_for_date()
TrackedBet.result_status = Won / Lost
    ↓  loss_analysis_agent + strategy_pipeline
LossAnalysis rows  →  LearningProposal rows
Self-learning loop closed
```

The scheduler runs at **04:00 UTC** (morning refresh + settlement), **19:00 UTC** (evening signal pull, peak odds availability), and **23:00 UTC** (settlement-only). Times are configurable via `SYNC_TIMES` in `backend/.env`.

---

## Deployment

The app deploys to [Fly.io](https://fly.io) automatically on push to `main` via GitHub Actions. The deploy is gated on CI: backend tests (`pytest`) and the frontend build must pass first (`.github/workflows/ci.yml`, also run on every PR). The Dockerfile is a two-stage build: Node 22 builds the React app, then Python 3.13 slim serves both the API and the compiled frontend.

### Running the tests

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

Tests run against an isolated in-memory SQLite database with dummy credentials — they never touch a real database or consume API-Football quota.

For the first deploy:

```bash
fly launch          # creates app and volume (already configured in fly.toml)
fly secrets set API_FOOTBALL_KEY=... JWT_SECRET=...
fly deploy
```

See `fly.toml` for the full production environment configuration.

---

## Key directories

```
backend/app/
├── core/       # config, database, auth, migrations
├── engines/    # bayesian.py, poisson.py, dual_engine.py, zinb.py, glicko2.py
├── models/     # SQLAlchemy ORM models
├── routers/    # FastAPI route handlers
├── schemas/    # Pydantic request/response schemas
└── services/   # business logic: ingestion, signals, settlement, analytics, AI pipelines

frontend/src/
├── api/        # fetch wrappers
├── components/ # React components by feature area
├── context/    # AuthContext (JWT decode + tier)
├── pages/      # route-level page components
└── store/      # custom hook stores (module-level cache + pub/sub, no Zustand)
```

---

## Contributing

Issues and pull requests are welcome. The codebase follows standard Python async conventions — all database access is via `await db.execute(...)`. Migrations go in `backend/app/core/migrations.py` as `CREATE TABLE IF NOT EXISTS` statements (no Alembic).

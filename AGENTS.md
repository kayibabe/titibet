# TiTiBet — AI Agent Context

This file gives any AI agent (or new session) instant full context. Read it before touching any code.

## What this project is

TiTiBet is a football betting signals platform. It ingests live fixture and odds data from API-Football, runs two probabilistic models (Bayesian + Poisson) to generate signals, scores and ranks them, and surfaces the best picks to subscribers via a React web app. A self-learning pipeline analyses settled losses, detects patterns, and proposes threshold adjustments that are validated by a backtester before being written to the DB.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Python 3.13, async (asyncio) |
| Database | SQLite via aiosqlite + SQLAlchemy 2.x async ORM |
| Migrations | Custom `run_migrations()` in `app/core/migrations.py` (no Alembic in active use) |
| Task queue | APScheduler (AsyncIOScheduler) — no Celery |
| Frontend | React 19 + Vite, Tailwind CSS, lucide-react, recharts |
| Auth | JWT (PyJWT), bcrypt passwords, tier-gated features |
| Payments | Paystack webhook integration |
| Data source | API-Football via `app/services/api_client.py` |

---

## Directory layout

```
titibet/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, middleware, router mounts, lifespan
│   │   ├── scheduler.py         # APScheduler: sync_and_compute, startup_sync, catchup_past_dates
│   │   ├── core/
│   │   │   ├── auth.py          # JWT helpers, get_current_user, get_current_user_optional
│   │   │   ├── config.py        # Settings (pydantic), DISABLED_MARKETS, DISABLED_LEAGUES, etc.
│   │   │   ├── database.py      # AsyncEngine, AsyncSessionLocal, init_db, get_db
│   │   │   └── migrations.py    # TABLE_MIGRATIONS list — CREATE TABLE IF NOT EXISTS
│   │   ├── engines/
│   │   │   ├── bayesian.py      # Bayesian odds-implied probability engine
│   │   │   ├── poisson.py       # Poisson goal-scoring model
│   │   │   ├── dual_engine.py   # Fuses both engines into dual_* fields on Signal rows
│   │   │   ├── zinb.py          # Zero-inflated negative binomial model for team totals
│   │   │   └── glicko2.py       # Glicko-2 rating gap certainty scores
│   │   ├── models/
│   │   │   ├── fixture.py       # Fixture (id, home_team, away_team, league, country, kickoff_at, status, scores, tier)
│   │   │   ├── signal.py        # Signal (per-fixture per-market row, bayesian_*, poisson_*, dual_*)
│   │   │   ├── odds.py          # MarketSnapshot (raw bookmaker odds snapshot)
│   │   │   ├── bet.py           # TrackedBet (manual + auto bet tracker)
│   │   │   ├── backtest.py      # BacktestResult
│   │   │   ├── ingestion.py     # IngestionRun (audit log per sync)
│   │   │   ├── loss_analysis.py # LossAnalysis (settled loss analysis records)
│   │   │   └── learning_proposal.py # LearningProposal (backtested threshold changes)
│   │   ├── routers/
│   │   │   ├── signals.py       # GET /api/signals, GET /api/signals/{id}, POST /api/signals/compute
│   │   │   ├── tracker.py       # Bet tracker CRUD + settlement
│   │   │   ├── analytics.py     # Analytics endpoints including CLV
│   │   │   ├── backtest.py      # Backtest runner endpoint
│   │   │   ├── advisor.py       # AI advisor endpoint
│   │   │   ├── accumulators.py  # Accumulator builder endpoint
│   │   │   ├── loss_analysis.py # GET /api/analytics/loss-analysis
│   │   │   ├── auth.py          # Register, login, reset password
│   │   │   ├── admin.py         # Admin-only endpoints
│   │   │   └── payments.py      # Paystack webhook + subscription
│   │   └── services/
│   │       ├── signal_engine.py         # compute_signals_for_date — orchestrates engines
│   │       ├── ingestion.py             # sync_date — pulls fixtures + odds from API-Football
│   │       ├── settlement.py            # settle_bets_for_date — resolves pending bets
│   │       ├── auto_tracker.py          # auto_track_date — system bet auto-tracking
│   │       ├── analytics.py             # ROI, CLV, streak, market breakdown stats
│   │       ├── clv.py                   # Closing Line Value helpers
│   │       ├── acca_builder.py          # Accumulator ticket builder
│   │       ├── loss_analysis_agent.py   # 4-agent AI pipeline (Loss Analyst → Pattern Detector → Threshold Tuner → Backtester)
│   │       ├── strategy_pipeline.py     # Strategy pipeline (win+loss analysis → rule proposals)
│   │       ├── performance_intelligence.py # Soft-overlay constants for signal scoring
│   │       ├── backtester.py            # Historical signal backtest runner
│   │       ├── staking.py               # Kelly criterion stake sizing
│   │       ├── match_info.py            # Deep-dive match context (form, H2H)
│   │       ├── advisor_service.py       # AI advisor response generation
│   │       ├── api_client.py            # API-Football HTTP client with file cache
│   │       ├── paystack.py              # Paystack API wrapper
│   │       ├── telegram.py              # Telegram digest sender
│   │       └── email.py                 # Transactional email
│   └── requirements.txt
└── frontend/
    └── src/
        ├── pages/
        │   ├── SignalsPage      # Discovery: Signals | Value Bets | AI Advisory
        │   └── TrackerPage      # Bet tracking: filter bar + BetTable
        ├── components/
        │   ├── signals/         # SignalCard, ValueBetCard, AccaCard
        │   ├── analytics/       # KPIRow, TrendChart, ByMarketTable, LossAnalysisDashboard
        │   ├── tracker/         # BetTable, PLChart, BetStatsBar
        │   ├── backtest/        # BacktestControls, BankrollChart
        │   └── layout/          # AppShell, NavBar, Sidebar, BottomNav
        ├── api/                 # Thin fetch wrappers (signals.js, tracker.js, analytics.js, …)
        ├── store/               # Custom hook stores (useSignals, useTracker, useSettings) — module-level cache + pub/sub, no Zustand
        ├── context/             # AuthContext (JWT decode + tier)
        └── hooks/               # useTier
```

---

## Core data flow

```
API-Football
    ↓  ingestion.sync_date()
Fixture + MarketSnapshot rows in DB
    ↓  compute_signals_for_date()
Signal rows (bayesian_* + poisson_* + dual_*)
    ↓  GET /api/signals  →  _system_rank()  →  ranked list
React SignalsPage
    ↓  user tracks a pick
TrackedBet row
    ↓  settle_bets_for_date()   (run every sync + startup)
TrackedBet.result_status = Won/Lost
    ↓  run_loss_analysis_pipeline()
LossAnalysis rows  →  LearningProposal rows (if accepted)
Self-learning loop closed
```

---

## Signal ranking — _system_rank() tuple

Signals are ranked by this 14-field priority tuple (highest first). The actual
tuple is built in `routers/signals.py:_system_rank()` — keep this in sync.

0. `poisson_medium_flag` — 1 if Poisson grade is Medium
1. `confidence_rank` — High=3, Medium=2, Low=1
2. `agreement_rank` — Both=3, Bayesian Only=2, Poisson Only=1, Contradiction=0
3. `high_probability_flag` — 1 if primary_prob ≥ 0.70
4. `primary_prob` — continuous (max of bayesian/poisson)
5. `bookmaker_support_rank` — 3+ books=2, 2 books=1, else 0
6. `clv_market_rank` — 1 if market has confirmed positive CLV history
7. `drift_rank` — odds-drift signal
8. `dual_model_probability_flag` — 1 if both engines ≥ 0.65
9. `glicko_certainty` — Glicko-2 rating-gap confidence
10. `tier_rank` — 1 if Tier 3+ league
11. `avg_prob` — (bayesian + poisson) / 2
12. `dual_quality_score` — fused quality score from dual_engine
13. `goals_expectation` — poisson_lambda_total (final tie-breaker)

---

## User tiers

- `free` — sees first 5 signals only
- `pro` / `elite` with `subscription_status == "active"` — sees all signals

---

## Self-learning pipelines

Two pipelines run in parallel after every settlement batch.

### Pipeline A — Loss Analysis (`app/services/loss_analysis_agent.py`)
1. **Loss Analyst** — queries recent settled losses
2. **Pattern Detector** — calls LLM to detect patterns in loss data
3. **Threshold Tuner** — proposes `market_odds_ceiling`, `min_probability` changes
4. **Backtester** — validates proposals, accepts or rejects

### Pipeline B — Strategy (`app/services/strategy_pipeline.py`)
1. **Signal Analyst** — pure-Python stats: win rate / ROI by market, confidence, league
2. **Strategy Agent** — calls LLM to propose rule changes
3. **Risk Agent** — backtests each proposal, accepts or rejects

**Persistence:** Accepted proposals → `LearningProposal` table. Old rows set `is_active=False`.

**Trigger:** After every `settle_bets_for_date()` call that settles ≥ 1 bet.

---

## Scheduler schedule (UTC)

- **04:00** — morning refresh: today ingestion + signals + settlement + morning Telegram
- **19:00** — evening pull: tomorrow ingestion + signals (peak odds) + evening Telegram
- **23:00** — settlement-only: re-pull today, settle, run learning pipelines

Override with `SYNC_TIMES=HH:MM,HH:MM,HH:MM` in `backend/.env`.

---

## Key conventions

- All DB access is async — use `await db.execute(...)`, `await db.commit()`
- Never import from `app.models` circular — models import Base only
- `migrations.py` is the migration system — add `CREATE TABLE IF NOT EXISTS` entries there, not Alembic
- Frontend API calls go through `src/api/*.js` wrappers — never raw fetch in components
- Signals router applies serving-time suppression (DISABLED_LEAGUES, DISABLED_MARKETS) on read — signals don't need recomputation when suppression config changes
- `_best_per_fixture()` deduplicates to one signal per fixture before returning

---

## Environment variables (backend/.env)

```
API_FOOTBALL_KEY=<key>
JWT_SECRET=<long random string>
DB_URL=sqlite+aiosqlite:///./titibet.db
API_KEY=                    # empty = no API key guard (local dev)
CORS_ORIGINS=http://localhost:5173
SKIP_STARTUP_SYNC=true      # set during dev
SYNC_TIMES=04:00,19:00,23:00
TITIBET_CLAUDE_KEY=<key>    # for loss analysis pipeline (NOT ANTHROPIC_API_KEY)
GROQ_API_KEY=<key>          # alternative LLM provider
```

See `backend/.env.example` for the complete variable reference.

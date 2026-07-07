# TiTiBet — Claude Agent Context

This file gives any Claude agent (or new session) instant full context. Read it before touching any code.

## What this project is

TiTiBet is a football betting signals platform. It ingests live fixture and odds data from API-Football, runs two probabilistic models (Bayesian + Poisson) to generate signals, scores and ranks them, and surfaces the best picks to subscribers via a React web app. A self-learning pipeline analyses settled losses, detects patterns, and proposes threshold adjustments that are validated by a backtester before being written to the DB.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Python 3.14, async (asyncio) |
| Database | SQLite via aiosqlite + SQLAlchemy 2.x async ORM |
| Migrations | Custom `run_migrations()` in `app/core/migrations.py` (no Alembic in active use) |
| Task queue | APScheduler (AsyncIOScheduler) — no Celery |
| Frontend | React 18 + Vite, Tailwind CSS, lucide-react, recharts |
| Auth | JWT (python-jose), bcrypt passwords, tier-gated features |
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
│   │   │   └── dual_engine.py   # Fuses both engines into dual_* fields on Signal rows
│   │   ├── models/
│   │   │   ├── fixture.py       # Fixture (id, home_team, away_team, league, country, kickoff_at, status, scores, tier)
│   │   │   ├── signal.py        # Signal (per-fixture per-market row, bayesian_*, poisson_*, dual_*)
│   │   │   ├── odds.py          # MarketSnapshot (raw bookmaker odds snapshot)
│   │   │   ├── bet.py           # TrackedBet (manual bet tracker)
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
│   │   │   ├── loss_analysis.py # GET /api/analytics/loss-analysis
│   │   │   ├── auth.py          # Register, login, reset password
│   │   │   ├── admin.py         # Admin-only endpoints
│   │   │   └── payments.py      # Paystack webhook + subscription
│   │   └── services/
│   │       ├── signal_engine.py         # compute_signals_for_date — orchestrates engines
│   │       ├── ingestion.py             # sync_date — pulls fixtures + odds from API-Football
│   │       ├── settlement.py            # settle_bets_for_date — resolves pending bets
│   │       ├── analytics.py             # ROI, CLV, streak, market breakdown stats
│   │       ├── clv.py                   # Closing Line Value helpers (_BET_TO_SELECTION, _MARKET_TYPE_SCOPE)
│   │       ├── loss_analysis_agent.py   # 4-agent AI pipeline (Loss Analyst → Pattern Detector → Threshold Tuner → Backtester)
│   │       ├── performance_intelligence.py # Soft-overlay constants for signal scoring
│   │       ├── backtester.py            # Historical signal backtest runner
│   │       ├── staking.py               # Kelly criterion stake sizing
│   │       ├── match_info.py            # Deep-dive match context (form, H2H)
│   │       ├── advisor_service.py       # AI advisor response generation
│   │       ├── api_client.py            # API-Football HTTP client with file cache
│   │       ├── paystack.py              # Paystack API wrapper
│   │       └── email.py                 # Transactional email
│   └── requirements.txt
└── frontend/
    └── src/
        ├── pages/
        │   ├── SignalsPage      # Discovery only: Signals | Value Bets | AI Advisory
        │   └── TrackerPage      # Bet tracking: filter bar + BetTable
        ├── components/
        │   ├── signals/         # SignalCard, ValueBetCard
        │   ├── analytics/       # KPIRow, TrendChart, ByMarketTable, LossAnalysisDashboard
        │   ├── tracker/         # BetTable, PLChart, BetStatsBar
        │   ├── backtest/        # BacktestControls, BankrollChart
        │   └── layout/          # AppShell, NavBar, Sidebar, BottomNav
        ├── api/                 # Thin fetch wrappers (signals.js, tracker.js, analytics.js, …)
        ├── store/               # Zustand stores (useSignals, useTracker, useSettings)
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

0. `poisson_medium_flag` — 1 if Poisson grade is Medium (gates noisy low-grade signals)
1. `confidence_rank` — High=3, Medium=2, Low=1
2. `agreement_rank` — Both=3, Bayesian Only=2, Poisson Only=1, Contradiction=0
3. `high_probability_flag` — 1 if primary_prob ≥ 0.70
4. `primary_prob` — continuous (max of bayesian/poisson)
5. `bookmaker_support_rank` — 3+ books=2, 2 books=1, else 0
6. `clv_market_rank` — 1 if this market has confirmed positive CLV history
7. `drift_rank` — odds-drift signal (market moving in our favour)
8. `dual_model_probability_flag` — 1 if both engines ≥ 0.65
9. `glicko_certainty` — Glicko-2 rating-gap confidence (higher = more reliable)
10. `tier_rank` — 1 if Tier 1 league
11. `avg_prob` — (bayesian + poisson) / 2
12. `dual_quality_score` — fused quality score from dual_engine
13. `goals_expectation` — poisson_lambda_total (final tie-breaker)

---

## User tiers

- `free` — sees first 5 signals only (FREE_SIGNAL_LIMIT = 5 in signals.py)
- `pro` / `elite` with `subscription_status == "active"` — sees all signals

---

## Self-learning pipelines

Two pipelines run in parallel after every settlement batch. Both write to `LearningProposal` with distinct `change_type` namespaces so they never collide.

### Pipeline A — Loss Analysis (`app/services/loss_analysis_agent.py`)
Focused on losses only. Fine-grained threshold tuning.

1. **Loss Analyst** — queries recent settled losses, structures them for analysis
2. **Pattern Detector** — calls Groq LLM to detect patterns in loss data
3. **Threshold Tuner** — proposes `market_odds_ceiling`, `min_probability` changes
4. **Backtester** — validates proposals against historical data, accepts or rejects

### Pipeline B — Strategy (`app/services/strategy_pipeline.py`)
Analyses ALL settled bets (wins + losses). Broader strategic rule changes.

5. **Signal Analyst** — pure-Python stats: win rate / ROI by market, confidence, league (no LLM)
6. **Strategy Agent** — calls Groq LLM to propose rule changes from the performance report
7. **Risk Agent** — pure-Python backtester: validates each proposal, accepts or rejects

**Pipeline B proposal types:**
- `market_suppression` — flag a consistently-losing market
- `league_suppression` — flag a consistently-losing league
- `kelly_fraction_adj` — reduce quality weight for an underperforming confidence level
- `min_prob_by_agreement` — raise minimum probability for a low-hit agreement type

**Persistence:** Accepted proposals → `LearningProposal` table (one active row per change_type+target slot). Old rows set `is_active=False`.

**Trigger:** After every `settle_bets_for_date()` call that settles ≥ 1 bet (scheduler + startup catch-up).

---

## Scheduler schedule

Default sync times (UTC): `04:00, 18:00, 22:30`

The 18:00 UTC sync (20:00 CAT) is the evening extras run — pulls tomorrow's fixtures/odds, computes signals, pre-warms the AI Advisory cache for tomorrow, pre-tracks tomorrow's ACCA legs (covers after-midnight UTC kickoffs that would be missed by the 04:00 UTC morning sync), and pushes both the "tomorrow" and "tonight + overnight" Telegram digests. The 18:00 UTC timing gives sharper odds (Asian/sharp money has moved by 20:00 CAT) while maintaining a comfortable 4h gap before the 22:05 sync.

Override with `SYNC_TIMES=HH:MM,HH:MM` in `backend/.env`.

Set `SKIP_STARTUP_SYNC=true` to skip the startup sync (saves API quota on hot-reload dev restarts). Catch-up settlement still runs.

---

## Git workflow (CRITICAL)

**The git index.lock problem:** When bash git operations leave stale `.lock` files on the NTFS mount that can't be `rm`'d, use `mv` to rename them:
```bash
mv .git/index.lock .git/index.lock.bak
mv .git/HEAD.lock .git/HEAD.lock.bak
```

**Committing from CMD** (Desktop Commander shell, since bash can't delete lock files):
```cmd
D: && cd WebApps\titibet && git add -A && git commit -F commitmsg.txt
```
Write commit message to `commitmsg.txt` first — CMD mangles `-m "..."` with colons/dots.

**Never** use `git commit -m "..."` with colons or dots in CMD — they break argument parsing.

---

## Environment variables (backend/.env)

```
DATABASE_URL=sqlite+aiosqlite:///./titibet.db
JWT_SECRET=<secret>
JWT_ALGORITHM=HS256
API_FOOTBALL_KEY=<key>
API_KEY=                    # empty = no API key guard (local dev)
CORS_ORIGINS=http://localhost:5173
SKIP_STARTUP_SYNC=true      # set during dev
SYNC_TIMES=06:00,14:00,18:00,23:30
ANTHROPIC_API_KEY=<key>     # for loss analysis pipeline
```

---

## Frontend signal card colours

- **Emerald border** — `isHighProbabilityOutcome`: primary_prob ≥ 70% AND not Medium confidence
- **Amber border** — `isMediumConfidence`: dual_confidence === 'Medium'
- **Default** — everything else

Country is shown in the card header: `{country} · {league}` (country field from Fixture, flows through SignalOut schema and router).

---

## Key conventions

- All DB access is async — use `await db.execute(...)`, `await db.commit()`
- Never import from `app.models` circular — models import Base only
- `migrations.py` is the migration system — add `CREATE TABLE IF NOT EXISTS` entries there, not Alembic
- Frontend API calls go through `src/api/*.js` wrappers — never raw fetch in components
- Signals router applies serving-time suppression (DISABLED_LEAGUES, DISABLED_MARKETS, OVER_GOALS_SUPPRESSED_LEAGUES) on read — signals don't need recomputation when suppression config changes
- `_best_per_fixture()` deduplicates to one signal per fixture before returning the list

---

## What's NOT in this file

Code patterns, function signatures, and file contents are best read fresh from disk. This file captures architecture, conventions, and non-obvious decisions that aren't derivable from reading the code.

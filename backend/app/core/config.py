"""
config.py -- Unified configuration merging FootBet + TiTiBet settings.
All thresholds are tunable here without touching business logic.
"""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve the .env path relative to this file so the server can be launched from any
# working directory (project root, backend/, etc.) without missing the env vars.
# config.py lives at backend/app/core/config.py → go up 3 levels → backend/
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_file_encoding="utf-8-sig", extra="ignore", env_ignore_empty=True)

    api_football_key: str = ""
    # When set, every /api/* request must carry X-API-Key: <value>.
    # Leave empty (default) to disable auth — useful for local-only dev.
    api_key: str = ""
    # Groq AI advisor — free at console.groq.com. Leave empty to disable.
    groq_api_key: str = ""
    # AI Advisory council providers — configure at least one. Leave unused keys empty.
    # Uses TITIBET_CLAUDE_KEY (not ANTHROPIC_API_KEY) to avoid clashing with
    # the Claude Code session token injected into the system environment.
    titibet_claude_key: str = ""   # console.anthropic.com
    gemini_api_key: str = ""       # aistudio.google.com/apikey  (free, no card)
    cerebras_api_key: str = ""     # inference.cerebras.ai       (free, very fast)
    mistral_api_key: str = ""      # console.mistral.ai          (free tier)
    db_url: str = "sqlite+aiosqlite:///./titibet.db"
    backend_port: int = 8010
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Scheduler sync times (HH:MM UTC, comma-separated)
    # 04:00 UTC (06:00 CAT) — morning refresh: today ingestion + signals + settlement
    #   + morning Telegram. If last night's evening digest already sent today's picks,
    #   the morning Telegram is a brief "Confirmed" update; otherwise a full digest.
    # 19:00 UTC (21:00 CAT) — evening pull: tomorrow ingestion + signals (peak odds
    #   availability) + advisory + ACCA + "Tomorrow's Picks" Telegram digest.
    # 23:00 UTC (01:00 CAT) — settlement-only: re-pull today, settle, learn pipelines.
    sync_times: str = "04:00,19:00,23:00"

    # Bayesian engine thresholds
    # (min_value_edge removed 2026-07-02 — EV/edge gating retired from pipeline)
    min_derived_prob: float = 0.50
    min_coverage_threshold: float = 0.65
    min_bookmakers: int = 2
    # 35% above reference price flags as outlier; tuned from sharp-book overround analysis
    bayesian_outlier_factor: float = 1.35

    # ── Execution-price model (soft-book reality) ─────────────────────────────
    # The price we display/score against (William Hill proxy, or the sharp book on
    # fallback) is LONGER than what the user actually gets at betPawa / 888bets /
    # Betway, whose overround runs 15–30%+. We haircut that proxy down to a
    # realistic execution price. Since 2026-07-02 the exec price is diagnostic
    # only (EV gating retired); the haircut still informs displayed exec odds.
    #   - exec_odds_haircut: global fraction the real book is shorter than the proxy.
    # Set EXEC_ODDS_HAIRCUT=0 in .env to disable (restores pre-Fix-1 behaviour).
    exec_odds_haircut: float = 0.08

    # Staking
    kelly_fraction: float = 0.25
    max_kelly_pct: float = 0.02  # Framework cap: max 2% of bankroll per selection
    unit_pct: float = 0.01
    default_bankroll: float = 100.0

    # Signal filter
    min_odds: float = 1.50
    min_edge_pct: float = 5.0

    # Backtest flat stake per bet
    backtest_flat_stake: float = 10.0

    # JWT
    jwt_secret: str = "change-me-in-production-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days

    @model_validator(mode="after")
    def _require_strong_jwt_secret(self) -> "Settings":
        insecure_defaults = {
            "change-me-in-production-use-a-long-random-string",
            "",
        }
        if self.jwt_secret in insecure_defaults:
            raise ValueError(
                "JWT_SECRET is not set or is the insecure default. "
                "Set a strong random secret in backend/.env before starting the server. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return self

    # Email (SMTP)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""           # e.g. noreply@titibet.com
    smtp_password: str = ""       # app password or SMTP password
    smtp_from_name: str = "TiTiBet"
    smtp_from_email: str = ""     # defaults to smtp_user if empty
    app_url: str = "https://www.titibet.com"

    # Telegram Bot — @titibet_alerts (shared across all channels)
    telegram_bot_token: str = ""   # from @BotFather

    # ── Named ticket channels ───────────────────────────────────────────────
    # TiTiBet Free     — limited/blurred teaser of the day's picks
    telegram_free_chat_id: str = ""
    # TiTiBet Pro      — top-ranked signals, full detail
    telegram_pro_chat_id: str = ""


    # Paystack
    paystack_secret_key: str = ""          # sk_live_... or sk_test_...
    paystack_public_key: str = ""          # pk_live_... or pk_test_...
    # Callback URL after Paystack payment — frontend route that reads ?reference=
    paystack_callback_url: str = "https://www.titibet.com/payment/callback"
    # Paystack plan codes — create these in your Paystack dashboard first
    paystack_plan_pro_monthly: str = ""
    paystack_plan_pro_yearly: str = ""
    # Currency — Paystack uses MWK for Malawi
    paystack_currency: str = "MWK"

    # Telegram public invite link for the Free channel — shown in welcome email
    # and onboarding. Generate from Telegram: channel → Manage → Invite Links.
    telegram_free_invite_url: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sync_times_list(self) -> list[tuple[int, int]]:
        result = []
        for t in self.sync_times.split(","):
            t = t.strip()
            if ":" in t:
                h, m = t.split(":", 1)
                result.append((int(h), int(m)))
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Flat stake used in backtest P&L calculations.
BACKTEST_FLAT_STAKE: float = 10_000.0


# =============================================================================
# Correct Score (CS) market — EV-driven exact-score picks served in Value Bets.
# One pick max per fixture (highest-EV scoreline). Calibrated by run_cs_backtest.py.
# =============================================================================

"""
Correct Score re-enable criteria (machine-checkable, do not remove).

CS_ENABLED is the master kill switch. Even when True, CS generation is skipped
unless BOTH runtime thresholds are met:

  CS_MIN_SETTLED_BETS : int   — minimum settled TrackedBet rows where market_type
                                starts with "Correct Score " before CS is allowed.
                                Rationale: CS calibration is unreliable below ~500
                                bets (backtest 2026-07-02: 656 fixtures, 11-31% ROI
                                loss at every combo tested). Set back to True and
                                accumulate this many bets before reenabling.

  CS_MIN_BRIER_SKILL  : float — minimum Brier skill score (from calibration_snapshots)
                                for the CS-market aggregate before CS is allowed.
                                Rationale: CS predictions are overconfident on the
                                9-15% probability cells that EV-picking selects.
                                Positive skill means the model beats a naive base-rate.

To re-enable: set CS_ENABLED=True in this file, ensure enough bets have settled,
and confirm the calibration snapshot shows skill >= CS_MIN_BRIER_SKILL.
"""
CS_ENABLED: bool = False                 # kill switch for live CS signal generation
CS_MIN_SETTLED_BETS: int = 500           # minimum settled CS bets before enabling
CS_MIN_BRIER_SKILL: float = 0.03         # minimum Brier skill score before enabling
CS_MARKET_PREFIX: str = "Correct Score "  # Signal.market = "Correct Score 2-1"
CS_DC_RHO: float = -0.10                 # Dixon-Coles low-score correlation (rho)
CS_MAX_GOALS: int = 6                    # score matrix grid size (0..6 per side)
CS_ODDS_CEILING: float = 15.0            # skip scorelines priced above this — model error dominates
CS_MIN_BOOKMAKERS: int = 2               # scoreline must be priced by at least this many books
CS_MIN_MODEL_PROB: float = 0.06          # skip cells the model itself thinks are near-impossible
CS_MAX_PICKS_PER_DAY: int = 5            # daily cap, best EV first
CS_KELLY_CAP: float = 0.005              # hard stake cap — CS variance is brutal
CS_AUTO_TRACK_STAKE: float = 10_000.0    # flat auto-track stake (vs 50k for normal system picks)
CS_ZINB_VETO_DIVERGENCE: float = 1.0     # skip fixture if |zinb_total − blend_total| exceeds this


# =============================================================================
# API-Football market type name sets
# Match the bet.name field from /odds. Frozensets for O(1) lookup.
# =============================================================================

CORRECT_SCORE_MARKET_NAMES: frozenset = frozenset({
    "Correct Score",
    "Correct Score (Regular Time)",
    "Exact Score",
})

GOALS_MARKET_NAMES: frozenset = frozenset({
    "Goals Over/Under",
    "Total Goals",
    "Over/Under",
    "Goals Over Under",
})

BTTS_MARKET_NAMES: frozenset = frozenset({
    "Both Teams Score",
    "Both Teams To Score",
    # "Results/Both Teams Score" removed 2026-07-02: DB audit shows its selections
    # are combo outcomes only ("Home/Yes", "Draw/No", ...), never pure Yes/No —
    # a Result+BTTS double, not the BTTS market.
    "GG/NG",
    "BTTS",
    # Half-scoped variants ("Both Teams Score - First Half", "Both Teams To Score
    # - Second Half") must NOT be listed — see HOME_GOALS_MARKET_NAMES note.
})

MATCH_WINNER_MARKET_NAMES: frozenset = frozenset({
    "Match Winner",
    "Match Winner (Regular Time)",
    "1X2",
    "Home/Draw/Away",
    "Result",
})

DOUBLE_CHANCE_MARKET_NAMES: frozenset = frozenset({
    "Double Chance",
})

HOME_GOALS_MARKET_NAMES: frozenset = frozenset({
    "Total - Home",
    "Home Team Total Goals",
    # Full-time only. Half-scoped variants like "Home Team Total Goals(1st Half)"
    # must NOT be listed: every consumer (bayesian best-odd, poisson signal odds,
    # CLV scope) merges these names into one pool and takes the max odd, so a
    # 1st-half Over 0.5 (~2.5) silently replaces the full-time price (~1.4).
})

AWAY_GOALS_MARKET_NAMES: frozenset = frozenset({
    "Total - Away",
    "Away Team Total Goals",
})

# Win to Nil pricing (audited against market_snapshots 2026-07-02):
# The only Win-to-Nil market API-Football actually delivers is "Win To Nil"
# with selections "Home" / "Away". The per-side "Win to Nil - Home/Away" names
# have never appeared in the data but are kept in case a bookmaker adds them
# (their selections are Yes/No). "Clean Sheet - Home/Away" was REMOVED: a clean
# sheet does not require winning, so its Yes price belongs to a different bet
# and must never price a Win-to-Nil selection.
WIN_TO_NIL_HOME_MARKET_NAMES: frozenset = frozenset({
    "Win to Nil - Home",
    "Win To Nil - Home",
})

WIN_TO_NIL_AWAY_MARKET_NAMES: frozenset = frozenset({
    "Win to Nil - Away",
    "Win To Nil - Away",
})

# Combined two-selection form: market "Win To Nil", selections "Home"/"Away".
WIN_TO_NIL_COMBINED_MARKET_NAMES: frozenset = frozenset({
    "Win To Nil",
    "Win to Nil",
})

EXACT_GOALS_MARKET_NAMES: frozenset = frozenset({
    "Exact Goals Number",
    "Exact Goals",
})


# =============================================================================
# Market definitions
# =============================================================================

ALLOWED_SCORELINES: set = {
    # Up to 2-2 (original core)
    (0, 0), (1, 0), (0, 1),
    (1, 1), (2, 0), (0, 2),
    (2, 1), (1, 2), (2, 2),
    # High-scoring scorelines — ~30 % of top-flight matches produce ≥ 3 total goals.
    # Excluding these caused the CS distribution to not sum to 1, systematically
    # under-estimating Over 2.5 / BTTS and over-estimating Under market probabilities.
    (3, 0), (0, 3), (3, 1), (1, 3),
    (3, 2), (2, 3), (4, 0), (0, 4),
    (4, 1), (1, 4), (3, 3),
}

MARKETS: dict = {
    # ── Full-game totals ─────────────────────────────────────────────────────
    "Over 1.5":  lambda h, a: (h + a) >= 2,
    "Over 2.5":  lambda h, a: (h + a) >= 3,
    "Under 1.5": lambda h, a: (h + a) <= 1,
    "Under 2.5": lambda h, a: (h + a) <= 2,
    "Under 3.5": lambda h, a: (h + a) <= 3,
    # ── Match result ─────────────────────────────────────────────────────────
    "Home Win":  lambda h, a: h > a,
    "Draw":      lambda h, a: h == a,
    "Away Win":  lambda h, a: h < a,
    "1X (Home or Draw)": lambda h, a: h >= a,
    "X2 (Draw or Away)": lambda h, a: h <= a,
    "12 (Home or Away)": lambda h, a: h != a,
    # ── Team goal totals (for "Total - Home" / "Total - Away" markets) ───────
    "Home Over 0.5":  lambda h, a: h >= 1,
    "Home Under 0.5": lambda h, a: h == 0,
    "Home Over 1.5":  lambda h, a: h >= 2,
    "Home Under 1.5": lambda h, a: h <= 1,
    "Away Over 0.5":  lambda h, a: a >= 1,
    "Away Under 0.5": lambda h, a: a == 0,
    "Away Over 1.5":  lambda h, a: a >= 2,
    "Away Under 1.5": lambda h, a: a <= 1,
    # ── Win to Nil (clean sheet win) ─────────────────────────────────────────
    "Home Win to Nil": lambda h, a: h > a and a == 0,
    "Away Win to Nil": lambda h, a: a > h and h == 0,
    # ── Exact goals ──────────────────────────────────────────────────────────
    "Exactly 1 Goal":  lambda h, a: (h + a) == 1,
    "Exactly 2 Goals": lambda h, a: (h + a) == 2,
    "Exactly 3 Goals": lambda h, a: (h + a) == 3,
}

# Every key in MARKETS is evaluated by the Bayesian pipeline (same as the old
# ACTIVE ∪ BAYESIAN_EXTRA union when all_markets=True). Poisson attaches where
# explicit rules exist — see MARKET_TO_POISSON_KEY in signal_engine.py.
ACTIVE_MARKETS: set = set(MARKETS.keys())


# Markets permanently disabled from signal generation.
# Historical analytics/backtest data for these markets is preserved, but the
# signal engine will not generate new picks for them.
DISABLED_MARKETS: frozenset = frozenset({
    # ── Previously retired markets ────────────────────────────────────────────
    "BTTS No",        # poor historical strike rate
    "BTTS Yes",       # retired 2026-06-15: btts rule removed
    "Away Over 1.5",  # retired 2026-06-02: 41.1% hit (-15.5% ROI) across 73 bets
    "Away Over 0.5",  # retired 2026-06-15: away_o05 rule removed
    "Home Over 1.5",  # retired 2026-06-15: home_o15 rule removed
    "Under 3.5",      # retired 2026-06-15: under35 + u35_flip rules removed
    "Home Under 1.5", # retired 2026-06-15: hu15_flip rule removed
    "Away Under 1.5", # retired 2026-06-15: au15_flip rule removed
    "Over 0.5",       # retired 2026-06-15: over05ft rule removed
    "Over 3.5",       # retired 2026-06-15: over35ft rule removed
    "Over 0.5 1H",    # retired 2026-06-15: over05fh rule removed
    "Underdog Over 1.5 Corners",  # retired 2026-06-15: WTCPM engine removed
    # ── Bayesian-only markets retired 2026-06-15 ─────────────────────────────
    # No Poisson rule → dual-model agreement defaults to "Bayesian Only" with no
    # independent mathematical confirmation. Removed to keep feed focused on the
    # 4 dual-model markets + 2 flip signals where both engines agree.
    "Home Win",
    "Draw",
    "Away Win",
    "1X (Home or Draw)",
    "X2 (Draw or Away)",
    "12 (Home or Away)",
    "Under 1.5",
    "Home Under 0.5",
    "Away Under 0.5",
    "Exactly 1 Goal",
    "Exactly 2 Goals",
    "Exactly 3 Goals",
})

# Leagues permanently disabled from signal generation AND serving.
# Use lowercase, stripped names — matched via lower(trim(league)).
# Add a league here when dynamic ROI suppression hasn't kicked in yet (< 5 bets)
# or when you want an immediate, restart-proof ban.
DISABLED_LEAGUES: frozenset = frozenset({
    "ekstraklasa",   # consistent negative ROI across tracked history
    "regionalliga",  # Austrian Regionalliga (exact name) — 0% WR across 16 bets
    "regionalliga - mitte",           # Austrian Regionalliga Mitte (disabled 2026-06-16)
    "regionalliga - ost",             # Austrian Regionalliga Ost  (disabled 2026-06-16)
    "regionalliga - west",            # Austrian Regionalliga West (disabled 2026-06-16)
    "esiliiga",      # Estonian top/second flight — 0% WR on 3 bets
    "ykkösliiga",    # Finnish Div 2 — 25% WR on 4 bets
    "friendlies",              # International/pre-season friendlies — rotation-heavy
    "friendlies clubs",        # API-Football name for club friendlies (exact-match fix)
    "friendlies international", # API-Football name for international friendlies
    # ── Disabled 2026-06-16 (poor analytics performance) ──────────────────────
    "primera división",               # Bolivia + Chile top-flight — 33% WR, -57k P&L on 3 bets
    "primera división femenina",
    "pro league",
    "reserve league",
    "segunda división",
    "persha liga",
    "première division",
    "serie c - promotion - play-offs",
    "serie d",
    "usl championship",
})

MARKET_PROB_BOUNDS: dict = {
    # Full-game totals
    "Over 1.5":  (0.45, 0.95),
    "Over 2.5":  (0.25, 0.75),
    "Under 2.5": (0.25, 0.75),
    # Team totals — calibrated from settled bets (B-3)
    "Home Over 0.5": (0.412, 0.662),  # empirical 5th-95th pct | hit=75.5% n=94
    # Win to nil
    "Home Win to Nil": (0.03, 0.52),
    "Away Win to Nil": (0.02, 0.42),
}

# MARKET_MIN_EDGE removed 2026-07-02: EV/edge gating retired from the signal
# pipeline. Signals are accepted on probability, confidence, and agreement only.

# Per-market execution-odds haircut overrides (fraction the user's real book is
# shorter than the displayed proxy price). Soft books shade favourites / overs
# harder than longshots / unders, so the haircut is NOT uniform.
# Calibrated values are loaded from exec_haircuts.json (produced by
# tools/calibrate_haircut.py from real spot-check prices); anything not listed
# falls back to the global Settings.exec_odds_haircut. Edit the JSON, not code.
EXEC_HAIRCUT_BY_MARKET: dict[str, float] = {}


def _load_exec_haircuts() -> None:
    """Populate EXEC_HAIRCUT_BY_MARKET from exec_haircuts.json if present.
    Looked up next to the backend root (parent of app/). Fail-silent: a missing
    or malformed file simply leaves the global haircut in effect."""
    import json
    from pathlib import Path
    candidates = [
        Path(__file__).resolve().parents[2] / "exec_haircuts.json",  # backend/exec_haircuts.json
        Path.cwd() / "exec_haircuts.json",
    ]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            cleaned = {
                str(k): float(v)
                for k, v in raw.items()
                if isinstance(v, (int, float)) and 0.0 <= float(v) < 0.6
            }
            if cleaned:
                EXEC_HAIRCUT_BY_MARKET.update(cleaned)
            return
        except Exception:
            return


_load_exec_haircuts()


def exec_haircut_for(market: str) -> float:
    """Fraction to shorten the displayed proxy odd by to estimate the price the
    user actually gets at their book. Per-market override, else global default."""
    return EXEC_HAIRCUT_BY_MARKET.get(market, get_settings().exec_odds_haircut)


def exec_odd_from(display_odd: float, market: str) -> float:
    """Convert a displayed proxy odd into a realistic execution odd.
    Never returns < 1.01 so downstream Kelly/EV math stays well-defined."""
    if not display_odd or display_odd <= 1.0:
        return 0.0
    return max(1.01, round(display_odd * (1.0 - exec_haircut_for(market)), 4))


# Minimum settled TrackedBet rows a league needs before it loses its "provisional"
# status and is treated as a known quantity. Leagues below this threshold are
# capped at 1 signal per day at serving time, preventing a data-sparse new league
# from flooding the pool before a track record is established.
PROVISIONAL_LEAGUE_MIN_BETS: int = 8

# Maximum signals to surface from any single Tier 3 league per day.
# Prevents catastrophic cluster losses when one lower-tier league misbehaves
# (e.g. all 7 Austrian Regionalliga Ost fixtures going 0-0 on the same day).
MAX_SIGNALS_PER_TIER3_LEAGUE: int = 3

# Per-market daily signal cap enforced at serving time (after ranking).
# Highest-ranked signals get priority. Markets not listed are uncapped.
# Prevents a single prolific market from dominating exposure on a given day.
MAX_SIGNALS_PER_MARKET: dict[str, int] = {
    "Home Over 0.5": 30,   # was 40% of total volume; cap forces diversification
    "Away Over 0.5": 25,   # second-highest volume market
}

# Per-market maximum bookmaker odds accepted by the backtester and signal engine.
# Blocks odds that are almost certainly from exotic/Asian book variants with different
# market semantics (e.g. "Home Over 1.5" quoted at 11.5 by an Asian handicap provider
# vs. the standard 2.5-4.0 range at European books).
MARKET_MAX_ODDS: dict[str, float] = {
    "Home Over 1.5": 6.0,  # home team scores 2+ — realistic ceiling ~5.0 in standard markets
    "Away Over 1.5": 6.0,  # away team scores 2+ — similar realistic ceiling
}

# Maximum odds for Poisson-only signals (no Bayesian confirmation).
# Above these odds the Poisson engine loses calibration on the market.
# Backtest 2026-06-15: Home Over 0.5 ≥2.50 → 38.5% WR (+19.5% ROI);
# <2.50 → 80.1% WR (+48.6% ROI). Hard cap at 2.49.
POISSON_ONLY_MAX_ODDS: dict[str, float] = {
    # Lowered 2.49 → 2.10 (Jul 2026): World Cup underdog fixtures (e.g. Guatemala,
    # Vanuatu) caused the Poisson engine to emit Poisson-only HO0.5 signals at 2.20–2.49.
    # These are near-50/50 propositions where one engine without Bayesian confirmation
    # is insufficient. At odds ≥2.10 (implied ≤48%) the model's calibration degrades.
    "Home Over 0.5": 2.10,
}

# Serving-time odds ceiling for Both+High signals where the market is most
# sceptical. Applied at serving time (router) and auto-tracker; does not
# require signal recomputation.
# 2026-06-23 baseline: HO0.5 Both+High at ≥2.50 had 65.3% hit rate vs 85.0%
# for Poisson Only; gap widens at the high-odds tail.
# 2026-07-05 update: 14/71 loss cards across Tier 1/2/3 all independently
# converge on 1.95 as the correct HO0.5 ceiling. Market odds ≥1.95 already
# signals home-team probability <50% — our model consistently fights this and
# loses. Lowered from 2.50 → 1.95.
# Away Over 0.5 ceiling 2.10 added (2026-07-05): 11 loss cards, most avoidable
# market per pipeline. loss_analysis_agent.py had this for detection only;
# now enforced at serving time.
DUAL_HIGH_ODDS_CEILING: dict[str, float] = {
    "Home Over 0.5": 1.95,
    "Away Over 0.5": 2.10,
}

# Acca candidate gate: exclude Over 2.5 legs where confidence is High,
# league tier is unknown (None), and bookmaker odds exceed this ceiling.
# 2026-07-05: initial value 3.46 (from card 1); lowered to 3.10 after full
# 71-card audit — 2 cards independently recommend 3.10 as the threshold.
ACCA_OVER25_UNKNOWN_TIER_CEILING: float = 3.10

# Kelly fraction cap for Poisson-only signals.
# Lower than Dual (max_kelly_pct = 2%) because Poisson-only has no Bayesian
# confirmation — one engine rather than two. Quarter-Kelly applied, capped at 1.5%.
POISSON_ONLY_KELLY_CAP: float = 0.015

# Maximum fraction of bankroll that can be committed across all signals in a day.
# Stakes are normalized to this cap after per-signal Kelly sizing, preserving
# relative weights so the strongest picks still get the largest share.
MAX_DAILY_EXPOSURE: float = 0.15

# =============================================================================
# BOS 2.0 — Match Stability Index thresholds
# =============================================================================
BOS_SI_THRESHOLD: float = 75.0   # SI ≥ 75 → fixture is stable
BOS_O00_MAX: float = 7.0          # Hard reject if 0-0 CS odds > 7
BOS_CMA_MAX: float = 4.0          # CMA ceiling for H-score normalisation

# =============================================================================
# Bayesian Kelly staking — shrinkage parameters
# Bayesian Kelly = standard_kelly × (var_model / (var_model + var_prior))
# =============================================================================
BAYESIAN_KELLY_P_VARIANCE: float = 0.05
BAYESIAN_KELLY_PRIOR_VARIANCE: float = 0.10

MARKET_MIN_ODDS: dict = {
    "Over 1.5":        1.30,
    "Over 2.5":        1.55,
    "Under 2.5":       2.10,  # < 2.10 implies < 48% probability — no value at short Under 2.5
    # Recalibrated 2026-07-02: the old 1.70 floor came from an audit run while
    # team-total odds were contaminated with 1st-half prices (~2x full-time), so
    # its ROI evidence is void. Real full-time Home Over 0.5 prices sit at
    # 1.10-1.55; 1.30 keeps out near-certainty prices where any calibration
    # error flips EV, while readmitting the market. Re-audit on corrected odds
    # before trusting a data-derived floor again.
    "Home Over 0.5":   1.30,
    "Home Win to Nil": 1.40,
    "Away Win to Nil": 1.40,
}


# =============================================================================
# League tier system
# =============================================================================

INTEGRITY_RISK_COUNTRIES = {
    "indonesia", "vietnam", "myanmar", "cambodia", "laos",
    "philippines", "bangladesh", "pakistan",
    # "Premier League" is a generic name; these countries' competitions
    # consistently produce Both+High losses and should not be Tier 1.
    "ethiopia", "barbados",
}

WOMEN_LEAGUE_KEYWORDS = {
    "women", "woman", "ladies", "girls", "feminine", "femenina",
    "femmes", "dames", "frauen", "femminile", "feminino",
    "nwsl", "wsl", "liga f",
    # Scandinavian women's top flights not covered by generic keywords.
    # "damallsvenskan" would otherwise be classified Tier 2 via "allsvenskan" substring.
    # "toppserien" is the Norwegian Women's Football League (men's top flight is "Eliteserien").
    "damallsvenskan", "toppserien",
}


def is_womens_fixture(league: str | None, home_team: str | None = None, away_team: str | None = None) -> bool:
    """Return True if this fixture is a women's match.

    Checks league name keywords first, then falls back to team-name " W" suffix
    (common in Nordic lower-division women's clubs whose league names are gender-neutral,
    e.g. "Lilla Torg W" in "Division 2 - Södra Götaland").
    """
    league_lower = (league or "").lower()
    if any(kw in league_lower for kw in WOMEN_LEAGUE_KEYWORDS):
        return True
    for team in (home_team or "", away_team or ""):
        if team.strip().upper().endswith(" W"):
            return True
    return False


TIER_2_COUNTRIES = {
    "egypt", "saudi arabia", "israel", "iran", "qatar",
    "uae", "united arab emirates", "morocco", "algeria", "tunisia",
    "nigeria", "ghana", "cameroon", "south africa", "kenya",
    "thailand", "malaysia", "india",
    "venezuela", "ecuador", "colombia", "peru", "chile",
    "paraguay", "bolivia", "uruguay", "costa rica", "panama",
}

TIER_1_LEAGUES = {
    "premier league", "la liga", "bundesliga", "serie a", "ligue 1",
    "champions league", "europa league", "conference league",
    "eredivisie", "primeira liga", "jupiler",
    "scottish premiership", "super lig",
    "premier liga", "premiership",
    # International tournaments — World Cup / continental championships are
    # the highest-quality football and should never be suppressed by the
    # end-of-northern-season or underperforming-league gates.
    "world cup", "copa america", "nations league",
    "gold cup", "africa cup", "asian cup",
    "euro",        # UEFA Euro / EURO Championship (substring safe for football)
    "olympic",     # Olympic football tournament
}

TIER_2_LEAGUES = {
    "championship", "serie b", "2. bundesliga", "ligue 2", "segunda",
    "la liga 2", "liga nos", "ekstraklasa", "czech liga", "allsvenskan",
    "eliteserien", "super league", "brasileirao", "serie a brasileira",
    "brasileira", "mls", "a-league", "j1 league",
    "k league", "chinese super", "saudi pro", "roshn saudi",
    "pro league", "ligat",
    # Added from league scouting: consistently good under-goals markets
    "greek super league", "super league greece",
    "swiss super league", "swiss super",
    "liga profesional", "liga profesional argentina",
    # Promoted from Tier 3 based on tracked performance data:
    # Superettan (Sweden 2nd div): 100% WR across 5 tracked bets
    "superettan",
    # Premijer Liga (Bosnia): 83.3% WR across 6 tracked bets
    "premijer liga",
    # HNL (Croatia top flight): consistent signal quality
    "hnl",
    # III Liga groups (Poland 3rd div): 100% WR across 4 tracked bets
    "iii liga",
    # Norwegian/Faroese top flights often misclassified
    "meistaradeildin", "veikkausliiga",
    # Georgian, Icelandic, Bosnian top flights
    "erovnuli liga", "urvalsdeild",
}

# Leagues where under-goals signals (Under 2.5, Under 3.5) are suppressed.
# These competitions consistently produce high-scoring matches -- our Poisson
# under-goals rules fire too often and land as losers.  Any fixture whose
# league name contains one of these substrings will have its under-goals
# signals dropped before writing to the DB, regardless of model output.
UNDER_GOALS_SUPPRESSED_LEAGUES: frozenset = frozenset({
    "mls",
    "major league soccer",  # MLS stored as full name in DB; "mls" key misses it via word-boundary
    "a-league",
    "chinese super",
    "allsvenskan",
    "eliteserien",
    "iranian",        # Iranian PGPL -- very high-scoring, thin CS markets
    "primera b",      # Chilean Segunda División — backtest: 20% hit rate on Home Under 1.5 (1/5)
    "usl league one", # US lower division — high-scoring, volatile scoring patterns
    "usl championship",
})

# Keywords that indicate youth / reserve fixtures.
# These competitions have structurally unpredictable scoring — young teams
# produce blowouts that defeat team-total under signals reliably.
YOUTH_LEAGUE_KEYWORDS: frozenset = frozenset({
    " u17", " u18", " u19", " u20", " u21", " u23",
    "youth", "reserve", "b team", "ii ", " ii)", "under-19", "under-21",
    "junioren", "juvenil", "sub-20", "sub-17", "sub-19",
})

# Leagues where over-goals signals (Over 0.5, Over 1.5, Over 2.5 etc.) are suppressed.
# These competitions are structurally low-scoring — 0-0, 1-0 results dominate —
# so even the lowest-bar Over picks land as losers at a high rate.
# Matched by substring against lower(trim(league)), same pattern as UNDER_GOALS_SUPPRESSED_LEAGUES.
OVER_GOALS_SUPPRESSED_LEAGUES: frozenset = frozenset({
    "ekstraklasa",          # 100% of recent games under 2.5 goals; Over 0.5/1.5 bets consistently lose
    "usl championship",     # 25% WR on 4 bets (-56% ROI); physical US league, low-scoring style
    "usl league one",       # 33% WR on 3 bets (-40% ROI); mirrors USL Championship pattern
    "regionalliga - ost",   # 0% hit rate on 17 Over bets (Home + Away Over 0.5); defensive Austrian/German regional
    "regionalliga - mitte", # 16.7% hit on 6 Away Over 0.5; same structural pattern as Ost
    "regionalliga - west",  # 50% hit rate but borderline — preemptive suppression to avoid further losses
    # Jul 4-5 2026: 2 HO0.5 losses (0-0 @1.34, plus Jul 5 loss) in Argentine Tier 3.
    # Already in AWAY_GOALS_SUPPRESSED_LEAGUES for away-scoring; extended here for home-scoring too.
    "primera b metropolitana",
    # Jul 7 2026: loss audit confirmed Baltic leagues produce structurally low-scoring matches.
    # Suduva Marijampole 0-0 (A Lyga) and Atmosfera 0-1 (1 Lyga) both categorised
    # tier3_exposure + zero_zero by the loss analysis pipeline.
    "a lyga",
    "1 lyga",
})

# Markets suppressed in women's leagues.
# Women's football has structurally lower scoring rates and weaker home advantage
# than men's. Both engines are calibrated on men's data and systematically
# overestimate home scoring in women's fixtures.
# Matched by checking fixture.league against WOMEN_LEAGUE_KEYWORDS at serving time.
WOMEN_OVER_SUPPRESSED_MARKETS: frozenset[str] = frozenset({
    "Home Over 0.5", "Away Over 0.5", "Over 1.5", "Over 2.5",
})

# Countries where Both+High Home Over 0.5 signals are blocked at Tier 3.
# In data-sparse markets both engines can agree confidently but on insufficient
# historical data — overconfidence from noise, not genuine edge.
# Applied only to Both+High; Poisson Only / Medium continues to qualify.
# Derived from June 2026 loss analysis: Ethiopia, Iraq, Mali, Uzbekistan all
# produced Both+High HO0.5 losses at 0% hit rate (0/4 in those countries).
HO05_DATA_POOR_COUNTRIES: frozenset[str] = frozenset({
    "ethiopia", "iraq", "mali", "uzbekistan",
    # Jul 3 2026: 3 Irish losses (Premier Division 0-2, First Division 0-1) + 1 Lebanese loss
    # (Al Hikma vs Al Nejmeh 0-1 @1.38). Both+High at Tier 3 consistently overestimates
    # home-team scoring rates in these lower-quality domestic environments.
    "ireland",
    "lebanon",
})

# South American cup competition name substrings where Home Over 0.5 signals are suppressed.
# Cup fixtures use rotation/reserve line-ups and single-leg knockout format
# incentivises parking the bus — home-team scoring rates drop sharply vs. league games.
# Unlike OVER_GOALS_SUPPRESSED_LEAGUES (which blocks all over markets), this is
# surgical: Over 2.5 and Over 1.5 are unaffected since cup matches can still be
# open; it's the home-scoring guarantee that fails in cup context.
# Matched by substring against lower(trim(league)) at serving time.
COPA_HO05_SUPPRESSED_LEAGUES: frozenset[str] = frozenset({
    "copa argentina",
    "copa colombia",
    "copa chile",
    "copa peru",
    "copa do brasil",
    "copa mx",
})

# League tiers where Over 2.5 signals are suppressed system-wide.
# Tier 3 Over 2.5 shows 57.1% WR and -100% ROI across 14 live bets (Jul 2026).
# Tier 1 and Tier 2 remain unaffected — Tier 2 Over 2.5 is 5W/0L at +71% ROI.
OVER25_SUPPRESSED_TIERS: frozenset[int] = frozenset({3})

# Leagues where away-scoring signals (Away Over 0.5/1.5) are surgically suppressed.
# These competitions show unreliable away-goal patterns that the Poisson/Bayesian
# models overestimate — typically low-tier Argentine/South American leagues with
# defensive home setups, artificial pitches, or late-season motivation asymmetry.
# Matched by substring against lower(trim(league)).
AWAY_GOALS_SUPPRESSED_LEAGUES: frozenset = frozenset({
    "primera b metropolitana",   # Argentine Tier 3 — away scoring 3W/4L at 2.22–2.64 odds, -9.1 net
})

# ─────────────────────────────────────────────────────────────────────────────
# League Watch Guard — automated monitoring of borderline leagues.
#
# Each entry is a substring matched against lower(league_name). When a league
# accumulates enough bets (min_bets_act) AND its ROI falls below suppress_roi_pct,
# the watch guard writes a LearningProposal(change_type="league_suppression") to
# the DB. The signal engine picks this up on the next cycle — no restart required.
#
# When ROI recovers above recover_roi_pct, the proposal is deactivated so the
# league re-enters the signal pool.
#
# Fields:
#   min_bets_warn   — start logging warnings at this bet count
#   min_bets_act    — minimum bets before auto-suppression can trigger
#   warn_roi_pct    — ROI below this → WARNING log only
#   suppress_roi_pct — ROI below this (with min_bets_act) → auto-suppress
#   recover_roi_pct — ROI must rise above this to auto-recover (default: suppress + 15pp)
#   note            — human-readable reason for watching this league
# ─────────────────────────────────────────────────────────────────────────────
LEAGUE_WATCHLIST: dict[str, dict] = {
    "regionalliga - mitte": {
        "min_bets_warn":    6,
        "min_bets_act":     12,
        "warn_roi_pct":     -10.0,
        "suppress_roi_pct": -20.0,
        "recover_roi_pct":  -5.0,
        "note": "German Regionalliga Mitte at -17.5% ROI / 8 bets; trending toward Austrian Regionalliga (banned) pattern.",
    },
    "segunda divisi": {           # substring covers all variants: española, chilena, etc.
        "min_bets_warn":    3,
        "min_bets_act":     6,
        "warn_roi_pct":     -20.0,
        "suppress_roi_pct": -35.0,
        "recover_roi_pct":  -15.0,
        "note": "Multiple Segunda División competitions showing 0% WR on 3 early bets; sample building.",
    },
    "superettan": {
        "min_bets_warn":    5,
        "min_bets_act":     10,
        "warn_roi_pct":     -10.0,
        "suppress_roi_pct": -20.0,
        "recover_roi_pct":  -5.0,
        "note": "Promoted to Tier 2 on good early record; Jun 22 0-0 loss flagged. Watching for structural HO0.5 reliability issues.",
    },
    "veikkausliiga": {
        "min_bets_warn":    5,
        "min_bets_act":     10,
        "warn_roi_pct":     -10.0,
        "suppress_roi_pct": -20.0,
        "recover_roi_pct":  -5.0,
        "note": "Finnish top flight Tier 2; Jun 23 0-0 loss. Watching for pattern before any suppression.",
    },
    "hnl": {
        "min_bets_warn":    5,
        "min_bets_act":     10,
        "warn_roi_pct":     -10.0,
        "suppress_roi_pct": -20.0,
        "recover_roi_pct":  -5.0,
        "note": "Croatian HNL at -17.8% ROI / 6 bets despite Tier 2 classification; may revert to Tier 3.",
    },
}


def get_league_tier(league_name: str, country: str = "") -> int:
    lower_country = country.lower().strip()
    lower_league = league_name.lower().strip()
    if any(k in lower_league for k in WOMEN_LEAGUE_KEYWORDS):
        return 3
    if lower_country in INTEGRITY_RISK_COUNTRIES:
        return 3
    if any(k in lower_league for k in TIER_2_LEAGUES):
        return 2
    if lower_country in TIER_2_COUNTRIES:
        return 2
    if any(k in lower_league for k in TIER_1_LEAGUES):
        return 1
    return 3


# =============================================================================
# Poisson rule thresholds (ported from TiTiBet rules.js v7)
# All values tunable here without touching engine code.
# =============================================================================

POISSON_RULES = {
    # CS cascade
    "cs00_u25_min": 2.0,
    "cs00_u25_max": 7.49,
    "cs00_u35_min": 7.50,
    "cs00_u35_max": 9.99,
    "cs00_o15_min": 13.0,
    "cs00_o15_max": 19.0,
    "cs00_mid_min": 10.0,
    "cs00_mid_max": 12.99,
    "cs00_extreme_min": 19.01,
    # Signal-only (Over 1.5 / 2.5 used for contradiction detection)
    "over15_min_10": 9.0,
    "over15_min_00": 9.0,
    "over15_min_01": 9.0,
    "over15_support_max_11": 9.0,
    "over15_support_max_20": 9.0,
    "over15_support_max_02": 9.0,
    # Over 2.5 rule: lowered from original extreme values (max_22=10, min_00=15)
    # Original required ~λ>3.5 total which almost never fires.
    # New values capture high-scoring matches (λ≈2.8+) while keeping selectivity.
    # Diagnostic (146 fixtures): current=5/146 (3.4%), relaxed=43/146 (29.5% core)
    "over25_max_22": 13.0,  # was 10.0 — 2-2 ≤ 13 needed (was ≤10, blocked 96.6% of fixtures)
    "over25_min_10": 9.0,   # was 10.0
    "over25_min_01": 9.0,   # was 10.0
    "over25_support_max_21": 9.0,
    "over25_support_max_12": 9.0,
    "over25_min_00": 11.0,  # was 15.0 — 0-0 ≥ 11 implies P(0-0) < 6.3%, λ_total > 2.75
    # Overround correction for CS markets (tier-averaged global default).
    "cs_overround_factor": 1.45,
    # Rolling form lambda settings
    "rolling_form_games": 6,
    # form_lambda_weight lowered 0.50->0.35: CS odds lead, form adjusts.
    "form_lambda_weight": 0.35,
    # form_min_games raised 3->5: prevents single-game blowout spikes.
    "form_min_games": 5,
    # Ceiling on blended lambda to prevent extreme form runs wiping under signals.
    "form_lambda_ceiling": 3.0,
    # Max lookback days: excludes previous-season fixtures from form data.
    "form_max_lookback_days": 90,
    # Under 2.5 guard: odds > 2.20 imply < 45% prob of <=2 goals.
    "under25_max_odds": 2.20,
    # Marginal Poisson (team overs): probability floors, replacing the retired
    # edge-vs-market floors (2026-07-02). rule_pass requires the model itself to
    # see the outcome as likely; rule_strong marks high-conviction picks that
    # qualify for the Poisson-only signal tier. 0.60/0.72 sit between what the
    # old 4% edge floor implied at the odds floor (1.30 → prob ≥ 0.81) and the
    # odds ceiling (2.49 → prob ≥ 0.44).
    "team_over_min_prob": 0.60,
    "team_over_strong_prob": 0.72,
}

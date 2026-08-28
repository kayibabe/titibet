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
    # Safety floor for live signals. 0.0 blocks mathematically negative-EV picks;
    # raise this only after out-of-sample validation establishes enough opportunity.
    min_ev_pct: float = 0.0

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
    "GG/NG",
    "BTTS",
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
})

AWAY_GOALS_MARKET_NAMES: frozenset = frozenset({
    "Total - Away",
    "Away Team Total Goals",
})

WIN_TO_NIL_HOME_MARKET_NAMES: frozenset = frozenset({
    "Win to Nil - Home",
    "Win To Nil - Home",
})

WIN_TO_NIL_AWAY_MARKET_NAMES: frozenset = frozenset({
    "Win to Nil - Away",
    "Win To Nil - Away",
})

WIN_TO_NIL_COMBINED_MARKET_NAMES: frozenset = frozenset({
    "Win To Nil",
    "Win to Nil",
})

EXACT_GOALS_MARKET_NAMES: frozenset = frozenset({
    "Exact Goals Number",
    "Exact Goals",
})

FIRST_HALF_GOALS_MARKET_NAMES: frozenset = frozenset({
    "Goals First Half",
    "First Half Goals",
    "Over/Under First Half",
    "First Half Over/Under",
    "1st Half Goals",
    "Goals Half Time",
    "HT Goals Over/Under",
})

CORNERS_MARKET_NAMES: frozenset = frozenset({
    "Corner Kicks",
    "Total Corners",
    "Corners Over/Under",
    "Total Corner Kicks",
    "Corners",
})

CARDS_MARKET_NAMES: frozenset = frozenset({
    "Cards Over/Under",
    "Total Cards",
    "Bookings",
})


ALLOWED_SCORELINES: set = {
    (0, 0), (1, 0), (0, 1),
    (1, 1), (2, 0), (0, 2),
    (2, 1), (1, 2), (2, 2),
    (3, 0), (0, 3), (3, 1), (1, 3),
    (3, 2), (2, 3), (4, 0), (0, 4),
    (4, 1), (1, 4), (3, 3),
}

MARKETS: dict = {
    "Over 1.5":  lambda h, a: (h + a) >= 2,
    "Over 2.5":  lambda h, a: (h + a) >= 3,
    "Under 1.5": lambda h, a: (h + a) <= 1,
    "Under 2.5": lambda h, a: (h + a) <= 2,
    "Under 3.5": lambda h, a: (h + a) <= 3,
    "Home Win":  lambda h, a: h > a,
    "Draw":      lambda h, a: h == a,
    "Away Win":  lambda h, a: h < a,
    "1X (Home or Draw)": lambda h, a: h >= a,
    "X2 (Draw or Away)": lambda h, a: h <= a,
    "12 (Home or Away)": lambda h, a: h != a,
    "Home Over 0.5":  lambda h, a: h >= 1,
    "Home Under 0.5": lambda h, a: h == 0,
    "Home Over 1.5":  lambda h, a: h >= 2,
    "Home Under 1.5": lambda h, a: h <= 1,
    "Away Over 0.5":  lambda h, a: a >= 1,
    "Away Under 0.5": lambda h, a: a == 0,
    "Away Over 1.5":  lambda h, a: a >= 2,
    "Away Under 1.5": lambda h, a: a <= 1,
    "Home Win to Nil": lambda h, a: h > a and a == 0,
    "Away Win to Nil": lambda h, a: a > h and h == 0,
    "Exactly 1 Goal":  lambda h, a: (h + a) == 1,
    "Exactly 2 Goals": lambda h, a: (h + a) == 2,
    "Exactly 3 Goals": lambda h, a: (h + a) == 3,
}

ACTIVE_MARKETS: set = set(MARKETS.keys())

DISABLED_MARKETS: frozenset = frozenset({
    "BTTS No",
    "BTTS Yes",
    "Away Over 1.5",
    "Home Over 1.5",
    "Home Under 1.5",
    "Away Under 1.5",
    "Over 0.5",
    "Over 3.5",
    "Underdog Over 1.5 Corners",
    "Home Win",
    "Draw",
    "Away Win",
    "Under 1.5",
    "Home Under 0.5",
    "Away Under 0.5",
    "Exactly 1 Goal",
    "Exactly 2 Goals",
    "Exactly 3 Goals",
    "Away Over 0.5",
    "Over 0.5 1H",
    "1X (Home or Draw)",
    "X2 (Draw or Away)",
    "12 (Home or Away)",
    "Home Win to Nil",
    "Away Win to Nil",
})

DISABLED_LEAGUES: frozenset = frozenset({
    "ekstraklasa", "regionalliga", "regionalliga - mitte", "regionalliga - ost", "regionalliga - west",
    "esiliiga", "ykkösliiga", "friendlies", "friendlies clubs", "friendlies international",
    "primera división", "primera división femenina", "pro league", "reserve league", "segunda división",
    "persha liga", "première division", "serie c - promotion - play-offs", "serie d", "usl championship",
    "damallsvenskan", "erovnuli liga 2", "superettan", "calcutta premier division", "serie c", "serie b",
})

BOTH_MEDIUM_DISABLED_LEAGUES: frozenset = frozenset({"copa rio", "primera nacional"})
HALVED_STAKE_LEAGUES: frozenset = frozenset()

MARKET_PROB_BOUNDS: dict = {
    "Over 1.5":  (0.45, 0.95),
    "Over 2.5":  (0.25, 0.75),
    "Under 2.5": (0.25, 0.75),
    "Home Over 0.5": (0.412, 0.662),
    "Home Win to Nil": (0.03, 0.52),
    "Away Win to Nil": (0.02, 0.42),
}

EXEC_HAIRCUT_BY_MARKET: dict[str, float] = {}


def _load_exec_haircuts() -> None:
    import json
    candidates = [
        Path(__file__).resolve().parents[2] / "exec_haircuts.json",
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
    return EXEC_HAIRCUT_BY_MARKET.get(market, get_settings().exec_odds_haircut)


def exec_odd_from(display_odd: float, market: str) -> float:
    if not display_odd or display_odd <= 1.0:
        return 0.0
    return max(1.01, round(display_odd * (1.0 - exec_haircut_for(market)), 4))

PROVISIONAL_LEAGUE_MIN_BETS: int = 8
MAX_SIGNALS_PER_TIER3_LEAGUE: int = 4
MAX_SIGNALS_PER_MARKET: dict[str, int] = {
    "Home Over 0.5": 30,
    "Away Over 0.5": 25,
}
MARKET_MAX_ODDS: dict[str, float] = {
    "Home Over 1.5": 6.0,
    "Away Over 1.5": 6.0,
    "Under 3.5": 1.95,
}
POISSON_ONLY_MAX_ODDS: dict[str, float] = {"Home Over 0.5": 2.10}
DUAL_HIGH_ODDS_CEILING: dict[str, float] = {
    "Home Over 0.5": 1.95,
    "Away Over 0.5": 2.10,
}
DUAL_HIGH_CEILING_EXCEPTION_MIN_ODDS: float = 2.50
DUAL_HIGH_CEILING_EXCEPTION_MIN_QUALITY: float = 0.30


def is_grade_c_ceiling_exception(odds: float, quality: float | None) -> bool:
    return odds >= DUAL_HIGH_CEILING_EXCEPTION_MIN_ODDS and (quality or 0.0) >= DUAL_HIGH_CEILING_EXCEPTION_MIN_QUALITY

ACCA_OVER25_UNKNOWN_TIER_CEILING: float = 3.10
POISSON_ONLY_KELLY_CAP: float = 0.015
MAX_DAILY_EXPOSURE: float = 0.15

BOS_SI_THRESHOLD: float = 75.0
BOS_O00_MAX: float = 7.0
BOS_CMA_MAX: float = 4.0
BAYESIAN_KELLY_P_VARIANCE: float = 0.05
BAYESIAN_KELLY_PRIOR_VARIANCE: float = 0.10

MARKET_MIN_ODDS: dict = {
    "Over 1.5": 1.50,
    "Over 2.5": 1.55,
    "Under 2.5": 2.10,
    "Under 3.5": 1.30,
    "Home Over 0.5": 1.50,
    "Away Over 0.5": 1.30,
    "Home Win to Nil": 1.40,
    "Away Win to Nil": 1.40,
    "1X (Home or Draw)": 1.25,
    "X2 (Draw or Away)": 1.25,
    "12 (Home or Away)": 1.30,
    "Over 0.5 1H": 1.65,
    "Over 9.5 Corners": 1.60,
    "Over 8.5 Corners": 1.40,
    "Under 9.5 Corners": 1.60,
}

ZINB_OVER15_MIN_ODDS: float = 1.25
ZINB_UNDER25_MIN_ODDS: float = 1.45
ZINB_UNDER35_MIN_ODDS: float = 1.35

INTEGRITY_RISK_COUNTRIES = {
    "indonesia", "vietnam", "myanmar", "cambodia", "laos", "philippines", "bangladesh", "pakistan",
    "ethiopia", "barbados",
}

WOMEN_LEAGUE_KEYWORDS = {
    "women", "woman", "ladies", "girls", "feminine", "femenina", "femmes", "dames", "frauen", "femminile", "feminino",
    "nwsl", "wsl", "liga f", "damallsvenskan", "toppserien",
}


def is_womens_fixture(league: str | None, home_team: str | None = None, away_team: str | None = None) -> bool:
    league_lower = (league or "").lower()
    if any(kw in league_lower for kw in WOMEN_LEAGUE_KEYWORDS):
        return True
    for team in (home_team or "", away_team or ""):
        if team.strip().upper().endswith(" W"):
            return True
    return False

TIER_2_COUNTRIES = {
    "egypt", "saudi arabia", "israel", "iran", "qatar", "uae", "united arab emirates", "morocco", "algeria", "tunisia",
    "nigeria", "ghana", "cameroon", "south africa", "kenya", "thailand", "malaysia", "india",
    "venezuela", "ecuador", "colombia", "peru", "chile", "paraguay", "bolivia", "uruguay", "costa rica", "panama",
}

TIER_1_LEAGUES = {
    "premier league", "la liga", "bundesliga", "serie a", "ligue 1", "champions league", "europa league", "conference league",
    "eredivisie", "primeira liga", "jupiler", "scottish premiership", "super lig", "premier liga", "premiership",
    "world cup", "copa america", "nations league", "gold cup", "africa cup", "asian cup", "euro", "olympic",
}

TIER_2_LEAGUES = {
    "championship", "serie b", "2. bundesliga", "ligue 2", "segunda", "la liga 2", "liga nos", "ekstraklasa", "czech liga", "allsvenskan",
    "eliteserien", "super league", "brasileirao", "serie a brasileira", "brasileira", "mls", "a-league", "j1 league",
    "k league", "chinese super", "saudi pro", "roshn saudi", "pro league", "ligat", "greek super league", "super league greece",
    "swiss super league", "swiss super", "liga profesional", "liga profesional argentina", "premijer liga", "hnl", "iii liga",
    "meistaradeildin", "veikkausliiga", "erovnuli liga", "urvalsdeild",
}

UNDER_GOALS_SUPPRESSED_LEAGUES: frozenset = frozenset({
    "mls", "major league soccer", "a-league", "chinese super", "allsvenskan", "eliteserien", "iranian", "primera b",
    "usl league one", "usl championship", "meistaradeildin",
})

YOUTH_LEAGUE_KEYWORDS: frozenset = frozenset({
    " u17", " u18", " u19", " u20", " u21", " u23", "youth", "reserve", "b team", "ii ", " ii)", "under-19", "under-21",
    "junioren", "juvenil", "sub-20", "sub-17", "sub-19",
})

OVER_GOALS_SUPPRESSED_LEAGUES: frozenset = frozenset({
    "ekstraklasa", "usl championship", "usl league one", "regionalliga - ost", "regionalliga - mitte", "regionalliga - west",
    "primera b metropolitana", "a lyga", "1 lyga",
})

WOMEN_OVER_SUPPRESSED_MARKETS: frozenset[str] = frozenset({
    "Home Over 0.5", "Away Over 0.5", "Over 1.5", "Over 2.5", "Under 3.5", "Under 2.5",
})

HO05_DATA_POOR_COUNTRIES: frozenset[str] = frozenset({"ethiopia", "iraq", "mali", "uzbekistan", "ireland", "lebanon", "kuwait", "belarus"})
U35_DATA_POOR_COUNTRIES: frozenset[str] = frozenset({"armenia", "nicaragua", "faroe-islands", "andorra", "san marino", "liechtenstein", "mongolia", "guam"})
HO05_ALL_TIERS_SUPPRESSED_COUNTRIES: frozenset[str] = frozenset({"australia"})

UEFA_CLUB_COMP_KEYWORDS: frozenset[str] = frozenset({"champions league", "europa league", "conference league"})
COPA_HO05_SUPPRESSED_LEAGUES: frozenset[str] = frozenset({
    "copa argentina", "copa colombia", "copa chile", "copa peru", "copa do brasil", "copa mx", "copa de la liga",
    "world cup", "copa america", "nations league", "gold cup", "africa cup", "asian cup", "concacaf", "olympic",
})
CUP_UNDER35_SUPPRESSED_LEAGUES: frozenset[str] = frozenset({"toto cup", "pokalen"})
OVER25_SUPPRESSED_TIERS: frozenset[int] = frozenset({3})
AWAY_GOALS_SUPPRESSED_LEAGUES: frozenset = frozenset({"primera b metropolitana"})

LEAGUE_WATCHLIST: dict[str, dict] = {
    "regionalliga - mitte": {"min_bets_warn": 6, "min_bets_act": 12, "warn_roi_pct": -10.0, "suppress_roi_pct": -20.0, "recover_roi_pct": -5.0, "note": "German Regionalliga Mitte at -17.5% ROI / 8 bets; trending toward Austrian Regionalliga (banned) pattern."},
    "segunda divisi": {"min_bets_warn": 3, "min_bets_act": 6, "warn_roi_pct": -20.0, "suppress_roi_pct": -35.0, "recover_roi_pct": -15.0, "note": "Multiple Segunda División competitions showing 0% WR on 3 early bets; sample building."},
    "veikkausliiga": {"min_bets_warn": 5, "min_bets_act": 10, "warn_roi_pct": -10.0, "suppress_roi_pct": -20.0, "recover_roi_pct": -5.0, "note": "Finnish top flight Tier 2; Jun 23 0-0 loss. Watching for pattern before any suppression."},
    "hnl": {"min_bets_warn": 5, "min_bets_act": 10, "warn_roi_pct": -10.0, "suppress_roi_pct": -20.0, "recover_roi_pct": -5.0, "note": "Croatian HNL at -17.8% ROI / 6 bets despite Tier 2 classification; may revert to Tier 3."},
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


POISSON_RULES = {
    "cs00_u25_min": 2.0,
    "cs00_u25_max": 7.49,
    "cs00_u35_min": 7.50,
    "cs00_u35_max": 9.99,
    "cs00_o15_min": 13.0,
    "cs00_o15_max": 19.0,
    "cs00_mid_min": 10.0,
    "cs00_mid_max": 12.99,
    "cs00_extreme_min": 19.01,
    "over15_min_10": 9.0,
    "over15_min_00": 9.0,
    "over15_min_01": 9.0,
    "over15_support_max_11": 9.0,
    "over15_support_max_20": 9.0,
    "over15_support_max_02": 9.0,
    "over25_max_22": 13.0,
    "over25_min_10": 9.0,
    "over25_min_01": 9.0,
    "over25_support_max_21": 9.0,
    "over25_support_max_12": 9.0,
    "over25_min_00": 11.0,
    "cs_overround_factor": 1.45,
    "rolling_form_games": 6,
    "form_lambda_weight": 0.35,
    "form_min_games": 5,
    "form_lambda_ceiling": 3.0,
    "form_max_lookback_days": 90,
    "under25_max_odds": 2.20,
    "team_over_min_prob": 0.60,
    "team_over_strong_prob": 0.72,
    "dc_min_prob": 0.70,
    "dc_strong_prob": 0.80,
    "fh_over05_min_prob": 0.60,
    "fh_over05_strong_prob": 0.72,
}

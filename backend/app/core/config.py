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

    sync_times: str = "04:00,19:00,23:00"

    # Bayesian engine thresholds
    min_derived_prob: float = 0.50
    min_coverage_threshold: float = 0.65
    min_bookmakers: int = 2
    bayesian_outlier_factor: float = 1.35

    # Execution-price model (soft-book reality)
    exec_odds_haircut: float = 0.08

    # Staking
    kelly_fraction: float = 0.25
    max_kelly_pct: float = 0.02
    unit_pct: float = 0.01
    default_bankroll: float = 100.0

    # Signal filters
    min_odds: float = 1.50
    min_edge_pct: float = 5.0
    # Safety floor for live signals. 0.0 means negative-EV bets are blocked,
    # while positive EV remains eligible for subsequent market-specific tuning.
    # This replaces the former state where EV/edge was calculated diagnostically
    # but not enforced by the serving gate.
    min_ev_pct: float = 0.0

    # Backtest flat stake per bet
    backtest_flat_stake: float = 10.0

    # JWT
    jwt_secret: str = "change-me-in-production-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7

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

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_name: str = "TiTiBet"
    smtp_from_email: str = ""
    app_url: str = "https://www.titibet.com"

    telegram_bot_token: str = ""
    telegram_free_chat_id: str = ""
    telegram_pro_chat_id: str = ""

    paystack_secret_key: str = ""
    paystack_public_key: str = ""
    paystack_callback_url: str = "https://www.titibet.com/payment/callback"
    paystack_plan_pro_monthly: str = ""
    paystack_plan_pro_yearly: str = ""
    paystack_currency: str = "MWK"

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


BACKTEST_FLAT_STAKE: float = 10_000.0


"""
Correct Score re-enable criteria (machine-checkable, do not remove).

CS is controlled by runtime feature gates and calibration requirements.
"""

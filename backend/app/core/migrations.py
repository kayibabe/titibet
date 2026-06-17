"""
migrations.py — Lightweight additive migrations for SQLite.

SQLite doesn't support ALTER TABLE ... ADD COLUMN IF NOT EXISTS before 3.37,
so we detect "duplicate column" errors and treat them as benign. Anything
else (locked DB, disk I/O, missing table) is logged as a warning so it can
be diagnosed instead of silently producing a half-migrated schema.
"""
from __future__ import annotations

import logging
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger(__name__)

# Each entry: (table, column, column_def)
COLUMN_MIGRATIONS = [
    ("tracked_bets",        "closing_odds",          "REAL"),
    ("tracked_bets",        "clv_pct",               "REAL"),
    ("signals",             "poisson_mixed_signals", "TEXT"),
    ("tracked_bets",        "user_id",               "INTEGER REFERENCES users(id)"),
    ("signals",             "odds_drift_pct",        "REAL"),
    ("tracked_bets",        "data_completeness",     "TEXT"),
    ("tracked_bets",        "dual_agreement",        "TEXT"),
    ("learning_proposals",  "updated_at",            "DATETIME"),
    # ── BOS 2.0 ───────────────────────────────────────────────────────────────
    ("signals", "bos_si",           "REAL"),
    ("signals", "bos_passed",       "INTEGER"),   # SQLite boolean → INTEGER
    # ── ZINB goal model ───────────────────────────────────────────────────────
    ("signals", "zinb_lambda_h",    "REAL"),
    ("signals", "zinb_lambda_a",    "REAL"),
    # ── Explicit EV score ─────────────────────────────────────────────────────
    ("signals", "ev_score",         "REAL"),
    # ── Glicko-2 rating differential ──────────────────────────────────────────
    ("signals", "glicko_r_diff",    "REAL"),
    # ── BREA (BTTS risk enrichment) ───────────────────────────────────────────
    ("signals", "brea_ri1",         "REAL"),
    ("signals", "brea_fss",         "REAL"),
    # ── FHGI (enhanced FH Over 0.5) ───────────────────────────────────────────
    ("signals", "fhgi_gpi",         "REAL"),
    ("signals", "fhgi_fhgmi",       "REAL"),
    ("signals", "fhgi_p_model",     "REAL"),
    # ── WTCPM (corner signals) ─────────────────────────────────────────────────
    ("signals", "wtcpm_di",         "REAL"),
    ("signals", "wtcpm_ccs",        "REAL"),
    ("signals", "wtcpm_p_corners",  "REAL"),
    # ── Halftime scores (needed by FHGI calibrator) ───────────────────────────
    ("fixtures", "home_score_ht",   "INTEGER"),
    ("fixtures", "away_score_ht",   "INTEGER"),
    # ── Actual corner counts (needed by WTCPM H2H corner service) ──────────────
    ("fixtures", "home_corners",    "INTEGER"),
    ("fixtures", "away_corners",    "INTEGER"),
    # ── Admin flag — explicit boolean; no longer inferred from tier ────────────
    ("users",    "is_admin",        "INTEGER NOT NULL DEFAULT 0"),
    # ── Backtest agreement column ─────────────────────────────────────────────
    ("backtest_results", "dual_agreement", "TEXT"),
    # ── Candidate signals (stored for backtesting, not served) ───────────────
    # Over 1.5 / Over 2.5 Bayesian-only High signals collected to validate
    # performance before enabling as a live tier. Default 0 = served normally.
    ("signals", "is_candidate", "INTEGER NOT NULL DEFAULT 0"),
]

TABLE_MIGRATIONS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS calibration_snapshots (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_date   DATE    NOT NULL,
        window_days     INTEGER NOT NULL DEFAULT 90,
        n_bets          INTEGER NOT NULL,
        win_rate        REAL,
        brier_score     REAL,
        brier_skill     REAL,
        ece             REAL,
        flagged_markets TEXT,
        market_summary  TEXT,
        created_at      DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS system_settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_push_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        push_date    DATE    NOT NULL,
        channel_type TEXT    NOT NULL,
        push_type    TEXT    NOT NULL,
        sent_at      DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
        UNIQUE(push_date, channel_type, push_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS loss_analyses (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tracked_bet_id  INTEGER NOT NULL REFERENCES tracked_bets(id),
        event_date      DATE,
        match_name      VARCHAR(255),
        league          VARCHAR(120),
        league_tier     INTEGER,
        market_type     VARCHAR(120),
        odds            REAL,
        dual_confidence VARCHAR(10),
        source_rule_key VARCHAR(40),
        home_score      INTEGER,
        away_score      INTEGER,
        agent_id        VARCHAR(40) NOT NULL DEFAULT 'loss_analyst',
        failure_categories VARCHAR(500),
        narrative       TEXT,
        recommendation  TEXT,
        avoidability_score REAL,
        created_at      DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS learning_proposals (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        change_type     VARCHAR(60)  NOT NULL,
        target          VARCHAR(120) NOT NULL,
        proposed_value  REAL,
        rationale       TEXT,
        confidence      VARCHAR(10),
        backtest_note   TEXT,
        is_active       INTEGER NOT NULL DEFAULT 1,
        created_at      DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
    )
    """,
]


def _is_duplicate_column_error(exc: BaseException) -> bool:
    """SQLite raises OperationalError with 'duplicate column name' in the message."""
    msg = str(exc).lower()
    return "duplicate column" in msg


INDEX_MIGRATIONS: list[tuple[str, str]] = [
    (
        "uq_bet_user",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_bet_user "
        "ON tracked_bets (user_id, fixture_id, bookmaker, market_type, selection_name) "
        "WHERE user_id IS NOT NULL",
    ),
    (
        "ix_fixture_status",
        "CREATE INDEX IF NOT EXISTS ix_fixture_status ON fixtures(status)",
    ),
    (
        "ix_fixture_kickoff",
        "CREATE INDEX IF NOT EXISTS ix_fixture_kickoff ON fixtures(kickoff_at)",
    ),
    (
        "ix_signal_fixture_market",
        "CREATE INDEX IF NOT EXISTS ix_signal_fixture_market ON signals(fixture_id, market)",
    ),
    (
        "ix_signal_fixture_computed",
        "CREATE INDEX IF NOT EXISTS ix_signal_fixture_computed ON signals(fixture_id, computed_at)",
    ),
    (
        "ix_ms_fixture_pulledat",
        "CREATE INDEX IF NOT EXISTS ix_ms_fixture_pulledat ON market_snapshots(fixture_id, pulled_at)",
    ),
    (
        "ix_lp_change_type_target",
        "CREATE INDEX IF NOT EXISTS ix_lp_change_type_target "
        "ON learning_proposals(change_type, target)",
    ),
]


async def run_migrations(engine: AsyncEngine) -> None:
    """
    Apply all pending column additions. Safe to call on every startup.
    """
    async with engine.begin() as conn:
        for table, column, col_def in COLUMN_MIGRATIONS:
            sql = f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"
            try:
                await conn.execute(text(sql))
                log.info("Migration applied: %s.%s %s", table, column, col_def)
            except OperationalError as e:
                if _is_duplicate_column_error(e):
                    log.debug("Migration already applied: %s.%s", table, column)
                else:
                    log.warning(
                        "Migration FAILED for %s.%s — schema may be out of "
                        "sync with the model. Stop other writers and restart. "
                        "SQL=%r err=%s",
                        table, column, sql, e,
                    )
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "Migration FAILED for %s.%s with unexpected error: %s",
                    table, column, e,
                )

        for sql in TABLE_MIGRATIONS:
            try:
                await conn.execute(text(sql))
                log.info("Table migration applied (CREATE TABLE IF NOT EXISTS)")
            except Exception as e:  # noqa: BLE001
                log.warning("Table migration FAILED: %s", e)

        for index_name, sql in INDEX_MIGRATIONS:
            try:
                await conn.execute(text(sql))
                log.info("Index migration applied: %s", index_name)
            except Exception as e:  # noqa: BLE001
                log.warning("Index migration FAILED for %s: %s", index_name, e)

        # ── Data migrations ───────────────────────────────────────────────────
        # Seed is_admin=1 for any existing elite users who predate the column.
        # Idempotent: rows already at is_admin=1 are untouched.
        try:
            await conn.execute(text(
                "UPDATE users SET is_admin=1 WHERE tier='elite' AND is_admin=0"
            ))
            log.info("Data migration applied: seeded is_admin for elite users")
        except Exception as e:  # noqa: BLE001
            log.warning("Data migration FAILED (is_admin seed): %s", e)

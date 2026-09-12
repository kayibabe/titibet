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

REQUIRED_TABLES = frozenset({
    "fixtures", "market_snapshots", "signals", "tracked_bets",
    "provider_observations", "market_definitions", "odds_quotes",
    "fixture_revisions", "model_versions", "feature_snapshots",
})

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
    # ── Glicko-2 rating differential + rating age ─────────────────────────────
    ("signals", "glicko_r_diff",           "REAL"),
    ("signals", "glicko_rating_age_days",  "INTEGER"),
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
    # ── User activity tracking ────────────────────────────────────────────────
    ("users", "last_active_at", "DATETIME"),
    # ── ACCA ticket grouping — one stable ID per advisory ticket per date ─────
    # Allows analytics to group legs by ticket rather than by event_date alone,
    # fixing incorrect hit-rate counts when multiple tickets exist on one date.
    ("tracked_bets", "acca_ticket_id", "TEXT"),
    ("odds_quotes", "evidence_class", "TEXT NOT NULL DEFAULT 'provider'"),
    ("odds_quotes", "legacy_snapshot_id", "INTEGER"),
]

TABLE_MIGRATIONS: list[str] = [
    """CREATE TABLE IF NOT EXISTS provider_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, provider VARCHAR(80) NOT NULL,
        endpoint VARCHAR(160) NOT NULL, request_scope VARCHAR(255),
        received_at DATETIME NOT NULL, provider_timestamp DATETIME,
        payload_json TEXT, content_sha256 VARCHAR(64) NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS market_definitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_key VARCHAR(120) NOT NULL,
        version VARCHAR(40) NOT NULL, settlement_rules TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(canonical_key, version))""",
    """CREATE TABLE IF NOT EXISTS odds_quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fixture_id INTEGER NOT NULL REFERENCES fixtures(id),
        market_key VARCHAR(120) NOT NULL, market_version VARCHAR(40) NOT NULL DEFAULT 'v1',
        bookmaker VARCHAR(80) NOT NULL, selection_name VARCHAR(120) NOT NULL, odds REAL NOT NULL,
        pulled_at DATETIME NOT NULL, received_at DATETIME NOT NULL,
        provider_observation_id INTEGER REFERENCES provider_observations(id),
        availability VARCHAR(20) NOT NULL DEFAULT 'prematch',
        evidence_class VARCHAR(40) NOT NULL DEFAULT 'provider', legacy_snapshot_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS fixture_revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fixture_id INTEGER NOT NULL REFERENCES fixtures(id),
        external_fixture_id INTEGER, event_date VARCHAR(10), kickoff_at DATETIME,
        status VARCHAR(60), home_score INTEGER, away_score INTEGER,
        home_score_ht INTEGER, away_score_ht INTEGER,
        provider_observation_id INTEGER REFERENCES provider_observations(id),
        received_at DATETIME NOT NULL, supersedes_id INTEGER REFERENCES fixture_revisions(id),
        evidence_class VARCHAR(40) NOT NULL DEFAULT 'provider', legacy_fixture_id INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE(legacy_fixture_id))""",
    """CREATE TABLE IF NOT EXISTS model_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(100) NOT NULL,
        version VARCHAR(80) NOT NULL, source_revision VARCHAR(120),
        config_sha256 VARCHAR(64) NOT NULL, artifact_sha256 VARCHAR(64),
        training_manifest_json TEXT, max_input_available_at DATETIME,
        runtime_json TEXT, seed INTEGER, parameters_json TEXT NOT NULL DEFAULT '{}',
        evidence_class VARCHAR(40) NOT NULL DEFAULT 'prospective',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(name, version, config_sha256))""",
    """CREATE TABLE IF NOT EXISTS feature_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fixture_id INTEGER NOT NULL REFERENCES fixtures(id),
        fixture_revision_id INTEGER NOT NULL REFERENCES fixture_revisions(id),
        model_version_id INTEGER REFERENCES model_versions(id), as_of DATETIME NOT NULL,
        features_json TEXT NOT NULL, input_refs_json TEXT NOT NULL DEFAULT '{}',
        transform_version VARCHAR(80) NOT NULL, content_sha256 VARCHAR(64) NOT NULL,
        evidence_class VARCHAR(40) NOT NULL DEFAULT 'prospective',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(fixture_revision_id, as_of, transform_version, content_sha256))""",
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
    """
    CREATE TABLE IF NOT EXISTS signal_observations (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        observation_type TEXT    NOT NULL,
        fixture_id       INTEGER REFERENCES fixtures(id),
        event_date       DATE,
        match_name       TEXT    NOT NULL,
        league           TEXT,
        country          TEXT,
        league_tier      INTEGER,
        market_type      TEXT    NOT NULL,
        dual_agreement   TEXT,
        dual_confidence  TEXT,
        signal_grade     TEXT,
        odds             REAL    NOT NULL,
        bookmaker        TEXT,
        result_status    TEXT    NOT NULL DEFAULT 'Pending',
        profit_loss      REAL             DEFAULT 0.0,
        created_at       DATETIME         DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
        settled_at       DATETIME
    )
    """,
]


def _is_duplicate_column_error(exc: BaseException) -> bool:
    """SQLite raises OperationalError with 'duplicate column name' in the message."""
    msg = str(exc).lower()
    return "duplicate column" in msg


def _is_no_such_table_error(exc: BaseException) -> bool:
    """Column migration on a table that hasn't been created yet — not an error."""
    return "no such table" in str(exc).lower()


INDEX_MIGRATIONS: list[tuple[str, str]] = [
    ("ix_provider_obs_received", "CREATE INDEX IF NOT EXISTS ix_provider_obs_received ON provider_observations(received_at)"),
    ("ix_odds_quotes_fixture_market_time", "CREATE INDEX IF NOT EXISTS ix_odds_quotes_fixture_market_time ON odds_quotes(fixture_id, market_key, received_at)"),
    ("ix_odds_quotes_received", "CREATE INDEX IF NOT EXISTS ix_odds_quotes_received ON odds_quotes(received_at)"),
    ("ix_fixture_revisions_fixture_received", "CREATE INDEX IF NOT EXISTS ix_fixture_revisions_fixture_received ON fixture_revisions(fixture_id, received_at)"),
    ("ix_model_versions_created", "CREATE INDEX IF NOT EXISTS ix_model_versions_created ON model_versions(created_at)"),
    ("ix_feature_snapshots_fixture_asof", "CREATE INDEX IF NOT EXISTS ix_feature_snapshots_fixture_asof ON feature_snapshots(fixture_id, as_of)"),
    ("uq_odds_quotes_legacy_snapshot", "CREATE UNIQUE INDEX IF NOT EXISTS uq_odds_quotes_legacy_snapshot ON odds_quotes(legacy_snapshot_id) WHERE legacy_snapshot_id IS NOT NULL"),
    ("uq_odds_quote_observation_key", "CREATE UNIQUE INDEX IF NOT EXISTS uq_odds_quote_observation_key ON odds_quotes(provider_observation_id, market_key, market_version, bookmaker, selection_name) WHERE provider_observation_id IS NOT NULL"),
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
    (
        "ix_tb_user_created",
        "CREATE INDEX IF NOT EXISTS ix_tb_user_created "
        "ON tracked_bets(user_id, created_at DESC)",
    ),
    (
        "ix_tb_source_created",
        "CREATE INDEX IF NOT EXISTS ix_tb_source_created "
        "ON tracked_bets(source_rule_key, created_at DESC)",
    ),
    (
        "ix_tb_event_date",
        "CREATE INDEX IF NOT EXISTS ix_tb_event_date "
        "ON tracked_bets(event_date)",
    ),
    # One acca_advisory row per authenticated user per day — DB-level guard
    # (the app-level SELECT-before-INSERT handles the common path; this catches races)
    (
        "uq_acca_per_user_day",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_acca_per_user_day "
        "ON tracked_bets(user_id, event_date) "
        "WHERE source_rule_key = 'acca_advisory' AND user_id IS NOT NULL",
    ),
    # One system signal bet per fixture+market — DB-level guard against duplicate
    # auto-tracking when concurrent startup syncs both read the same empty dedup set
    # before either commits (race condition on consecutive deploys).
    # Excludes accumulators (fixture_id IS NULL) and user-tracked bets (user_id IS NOT NULL).
    (
        "uq_system_signal_bet",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_system_signal_bet "
        "ON tracked_bets (fixture_id, market_type) "
        "WHERE user_id IS NULL AND fixture_id IS NOT NULL AND market_type != 'Accumulator'",
    ),
    # Composite indexes for form_service._fetch_team_goals: the query filters by
    # (home_team OR away_team) AND event_date range.  Without these, SQLite falls
    # back to a full table scan through all fixtures on every call.  With them,
    # two separate range scans (one per index) are merged via bitmap OR, keeping
    # backtest form-lambda lookups to <1 ms each instead of 50–100 ms.
    (
        "ix_fixtures_home_team_date",
        "CREATE INDEX IF NOT EXISTS ix_fixtures_home_team_date "
        "ON fixtures(home_team, event_date DESC)",
    ),
    (
        "ix_fixtures_away_team_date",
        "CREATE INDEX IF NOT EXISTS ix_fixtures_away_team_date "
        "ON fixtures(away_team, event_date DESC)",
    ),
]

REQUIRED_EVIDENCE_INDEXES = frozenset({
    "ix_provider_obs_received", "ix_odds_quotes_fixture_market_time",
    "ix_odds_quotes_received", "ix_fixture_revisions_fixture_received",
    "ix_model_versions_created", "ix_feature_snapshots_fixture_asof",
    "uq_odds_quotes_legacy_snapshot", "uq_odds_quote_observation_key",
})

# One-shot data fixes — each is an idempotent UPDATE with tight WHERE guards.
# Runs on every startup (cheap no-op once the condition is no longer true).
DATA_MIGRATIONS: list[str] = [
    # 2026-07-03: Convert 4 manually-tracked bets to system picks so they appear
    # in the system auto-tracking stats instead of the user's personal tracker.
    # Guard: only touches rows still owned by a user (user_id IS NOT NULL) that
    # aren't already classified as system picks.
    """
    UPDATE tracked_bets
    SET user_id            = NULL,
        source_rule_key    = 'system_dual',
        source_rule_label  = 'Dual Signal (High+Both)'
    WHERE event_date = '2026-07-03'
      AND user_id IS NOT NULL
      AND (source_rule_key IS NULL OR source_rule_key NOT LIKE 'system%')
      AND (
            match_name LIKE '%Treaty United%'
         OR match_name LIKE '%Drogheda United%'
         OR match_name LIKE '%Cobh Ramblers%'
         OR match_name LIKE '%Al Hikma%'
      )
    """,
    # 2026-07-02: Delfin SC vs Emelec was postponed — convert to system pick
    # and void it so it doesn't sit as a stale Pending row.
    """
    UPDATE tracked_bets
    SET user_id            = NULL,
        source_rule_key    = 'system_dual',
        source_rule_label  = 'Dual Signal (High+Both)',
        result_status      = 'Void'
    WHERE event_date = '2026-07-02'
      AND match_name LIKE '%Delfin%'
      AND result_status = 'Pending'
    """,
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
                elif _is_no_such_table_error(e):
                    log.debug(
                        "Column migration deferred for %s.%s — table not yet created "
                        "(TABLE_MIGRATIONS will create it with this column included)",
                        table, column,
                    )
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

        # ── Pre-index dedup pass ──────────────────────────────────────────────────
        # Remove duplicate system signal bets (same fixture+market tracked multiple
        # times due to a bookmaker-field race on concurrent startup syncs).
        # Must run before uq_system_signal_bet index creation to avoid IntegrityError.
        try:
            result = await conn.execute(text("""
                DELETE FROM tracked_bets
                WHERE user_id IS NULL
                  AND fixture_id IS NOT NULL
                  AND market_type != 'Accumulator'
                  AND id NOT IN (
                    SELECT MIN(id)
                    FROM tracked_bets
                    WHERE user_id IS NULL
                      AND fixture_id IS NOT NULL
                      AND market_type != 'Accumulator'
                    GROUP BY fixture_id, market_type
                  )
            """))
            if result.rowcount:
                log.info("Dedup migration: removed %d duplicate system signal bet(s)", result.rowcount)
        except Exception as e:  # noqa: BLE001
            log.warning("Dedup migration FAILED: %s", e)

        for index_name, sql in INDEX_MIGRATIONS:
            try:
                await conn.execute(text(sql))
                log.info("Index migration applied: %s", index_name)
            except Exception as e:  # noqa: BLE001
                log.warning("Index migration FAILED for %s: %s", index_name, e)
                if index_name in REQUIRED_EVIDENCE_INDEXES:
                    raise RuntimeError(f"Required Stage 1 index could not be created: {index_name}") from e

        # Evidence tables are append-only.  Corrections are represented by a new
        # row linked through supersedes_id; never silently rewrite provenance.
        for table in ("provider_observations", "market_definitions", "odds_quotes", "fixture_revisions", "model_versions", "feature_snapshots"):
            trigger = f"trg_{table}_immutable"
            for operation in ("UPDATE", "DELETE"):
                try:
                    await conn.execute(text(
                        f"CREATE TRIGGER IF NOT EXISTS {trigger}_{operation.lower()} "
                        f"BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable evidence: {table}'); END"
                    ))
                except Exception as e:  # noqa: BLE001
                    log.warning("Immutability trigger FAILED for %s %s: %s", table, operation, e)
                    raise RuntimeError(
                        f"Required Stage 1 immutability trigger could not be created: {trigger}_{operation.lower()}"
                    ) from e

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

        for dm_sql in DATA_MIGRATIONS:
            try:
                result = await conn.execute(text(dm_sql))
                if result.rowcount:
                    log.info("Data migration applied: %d row(s) updated", result.rowcount)
            except Exception as e:  # noqa: BLE001
                log.warning("Data migration FAILED: %s", e)

        # Publication must fail closed when a required Stage 1 table is absent.
        # Earlier migrations logged errors and allowed a partially upgraded app
        # to continue, which made evidence completeness impossible to reason about.
        if engine.dialect.name == "sqlite":
            rows = (await conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ))).all()
            present = {row[0] for row in rows}
            missing = REQUIRED_TABLES - present
            if missing:
                raise RuntimeError(f"Required database schema is incomplete; missing tables: {sorted(missing)}")

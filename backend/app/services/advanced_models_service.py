"""
advanced_models_service.py — Lazy-initialised ZINB and Glicko-2 models.

Loads historical fixture data from the DB once per signal batch (per date).
Both models are keyed by league so cross-league contamination can't happen.

Public API used by signal_engine.py:
    service = AdvancedModelsService(db)
    await service.load()

    mu_h, mu_a = service.zinb_predict(league, home_id, away_id, fallback_lh, fallback_la)
    rdiff       = service.glicko_r_diff(home_team, away_team)
    ht_rates    = service.ht_rates(home_team, away_team)  # for BOS
"""
from __future__ import annotations

import asyncio
import concurrent.futures as _cf
import logging
import os
import pickle
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Minimum completed fixtures with scores per league to justify fitting ZINB.
_MIN_FIXTURES_FOR_ZINB: int = 20
# How far back (days) to pull historical fixtures for model fitting.
_LOOKBACK_DAYS: int = 365

# Process-level cache — ZINB + Glicko-2 fitting takes 5+ min; reuse across
# all Recompute calls within the same day. Keyed by reference_date.
_cache: dict[date, "AdvancedModelsService"] = {}

# Disk-persisted cache so fitted models survive process restarts (deploys,
# crashes, Fly maintenance). Without this, every restart re-fits ZINB/Glicko
# for every league from scratch even though the underlying fixture history
# usually hasn't changed since the last fit that same day — the CPU-bound
# refit then blocks the event loop long enough to fail health checks and
# time out unrelated API requests (e.g. /api/tracker/bets) for several minutes.
# Point MODEL_CACHE_DIR at the persistent /data volume in production —
# otherwise every redeploy wipes the cache and forces a full refit.
_MODEL_CACHE_DIR = Path(
    os.getenv("MODEL_CACHE_DIR") or str(Path(__file__).resolve().parents[2] / ".cache" / "advanced_models")
)

# Process pool for ZINB fitting. Separate processes don't share the GIL with
# the asyncio event loop, so ZINB's scipy Nelder-Mead callbacks can't starve
# incoming HTTP requests (bets, signals, health). max_workers=2 matches the
# Fly machine's 2 vCPUs; main process still gets OS-scheduled CPU time.
_ZINB_PROCESS_POOL: _cf.ProcessPoolExecutor | None = None


def _get_process_pool() -> _cf.ProcessPoolExecutor:
    global _ZINB_PROCESS_POOL
    if _ZINB_PROCESS_POOL is None:
        _ZINB_PROCESS_POOL = _cf.ProcessPoolExecutor(max_workers=2)
    return _ZINB_PROCESS_POOL


def _fit_zinb_worker(matches: list[dict]) -> object:
    """Top-level function run in a worker process for picklability."""
    from app.engines.zinb import ZINBGoalModel
    m = ZINBGoalModel()
    m.fit(matches)
    return m


async def _fixture_fingerprint(db: AsyncSession, reference_date: date) -> str:
    """
    Cheap proxy for "has the underlying fixture history changed since the last
    fit" — count + max id of completed fixtures in the lookback window. A single
    indexed COUNT/MAX query, orders of magnitude cheaper than the full fit.

    Excludes reference_date itself so intraday settlements (which add scores to
    today's fixtures) don't change the fingerprint mid-day. ZINB is trained on
    historical fixtures only, so today's settlement doesn't affect the model.
    """
    cutoff = reference_date - timedelta(days=_LOOKBACK_DAYS)
    row = (await db.execute(text("""
        SELECT COUNT(*), COALESCE(MAX(id), 0) FROM fixtures
        WHERE home_score IS NOT NULL AND away_score IS NOT NULL
          AND event_date >= :cutoff AND event_date < :today
    """), {"cutoff": cutoff.isoformat(), "today": reference_date.isoformat()})).first()
    count, max_id = (row[0], row[1]) if row else (0, 0)
    return f"{count}-{max_id}"


def _disk_cache_path(reference_date: date, fingerprint: str) -> Path:
    return _MODEL_CACHE_DIR / f"{reference_date.isoformat()}_{fingerprint}.pkl"


async def get_or_load(db: "AsyncSession", reference_date: date) -> "AdvancedModelsService":
    """
    Return a fully-loaded AdvancedModelsService, reusing the cached instance
    for the same reference_date so models are only fitted once per day per process.

    Falls back to a disk-persisted cache (keyed by reference_date + a fixture-data
    fingerprint) when the in-memory cache is empty — i.e. right after a process
    restart — so a deploy doesn't force a full refit if nothing has changed.
    """
    if reference_date in _cache:
        return _cache[reference_date]

    fingerprint = await _fixture_fingerprint(db, reference_date)
    disk_path = _disk_cache_path(reference_date, fingerprint)

    svc: Optional["AdvancedModelsService"] = None
    if disk_path.is_file():
        try:
            with disk_path.open("rb") as f:
                svc = pickle.load(f)
            svc._db = db
            logger.info("AdvancedModelsService: restored from disk cache (%s)", disk_path.name)
        except Exception as exc:
            logger.warning("AdvancedModelsService: disk cache load failed (%s) — refitting", exc)
            svc = None

    if svc is None:
        svc = AdvancedModelsService(db)
        await svc.load(reference_date=reference_date)
        _persist_to_disk(svc, disk_path)

    _cache[reference_date] = svc
    # Evict any entries for other dates — keep memory bounded.
    for old_date in [d for d in list(_cache) if d != reference_date]:
        del _cache[old_date]
    return svc


def _persist_to_disk(svc: "AdvancedModelsService", disk_path: Path) -> None:
    """Write the fitted service to disk, excluding the live (unpicklable) DB session."""
    try:
        _MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        db_ref, svc._db = svc._db, None
        try:
            with disk_path.open("wb") as f:
                pickle.dump(svc, f)
        finally:
            svc._db = db_ref
        # Bound the cache to one file — older fingerprints/dates are stale.
        for stale in _MODEL_CACHE_DIR.glob("*.pkl"):
            if stale != disk_path:
                stale.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("AdvancedModelsService: failed to write disk cache: %s", exc)


class AdvancedModelsService:
    """
    Per-batch container for ZINB and Glicko-2 models.
    Call await .load() before using any prediction methods.
    """

    def __init__(self, db: AsyncSession):
        self._db = db
        self._zinb_models: dict[str, object] = {}        # league_lc → ZINBGoalModel
        self._glicko: Optional[object] = None            # Glicko2System (global, all teams)
        self._ht_00_rates: dict[str, float] = {}         # team_name_lc → fraction
        self._ht_10_rates: dict[str, float] = {}
        self._atg: dict[str, float] = {}                 # team_name_lc → avg total goals (last 5)
        self._loaded: bool = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    async def load(self, reference_date: Optional[date] = None) -> None:
        """Pull fixture history from DB and fit ZINB + Glicko-2 models."""
        if self._loaded:
            return

        cutoff = (reference_date or date.today()) - timedelta(days=_LOOKBACK_DAYS)

        try:
            rows = await self._db.execute(text("""
                SELECT id, home_team, away_team, league,
                       home_score, away_score, event_date
                FROM fixtures
                WHERE home_score IS NOT NULL
                  AND away_score IS NOT NULL
                  AND event_date >= :cutoff
                ORDER BY event_date ASC
            """), {"cutoff": cutoff.isoformat()})
            records = [dict(r._mapping) for r in rows.all()]
        except Exception as exc:
            logger.warning("AdvancedModelsService: DB query failed — %s", exc)
            self._loaded = True
            return

        if not records:
            self._loaded = True
            return

        # CPU-bound fitting — run in worker threads so the event loop keeps serving
        # requests and health checks. Glicko-2 and ht_proxies are each one thread
        # call (they're fast). ZINB fits each league in a separate thread so the
        # event loop gets a turn between leagues (preventing health-check timeouts
        # during the largest leagues, which previously caused Fly to report 503).
        import asyncio
        await asyncio.to_thread(self._fit_glicko, records)
        await self._fit_zinb_leagues_async(records)
        await asyncio.to_thread(self._compute_ht_proxies, records)
        self._loaded = True
        logger.info(
            "AdvancedModelsService loaded: %d fixtures, %d leagues with ZINB",
            len(records), len(self._zinb_models),
        )

    def _fit_glicko(self, records: list[dict]) -> None:
        try:
            from app.engines.glicko2 import Glicko2System, MatchResult

            sys = Glicko2System()
            # Feed in weekly periods
            from collections import defaultdict
            from datetime import datetime
            weeks: dict[str, list] = defaultdict(list)
            for r in records:
                ev = r["event_date"]
                if isinstance(ev, str):
                    ev_d = datetime.fromisoformat(ev).date()
                else:
                    ev_d = ev
                # ISO week key
                week_key = f"{ev_d.isocalendar()[0]}-W{ev_d.isocalendar()[1]:02d}"
                weeks[week_key].append(r)

            for week_key in sorted(weeks.keys()):
                period_results = []
                for r in weeks[week_key]:
                    try:
                        period_results.append(MatchResult(
                            home_team=r["home_team"],
                            away_team=r["away_team"],
                            home_goals=int(r["home_score"]),
                            away_goals=int(r["away_score"]),
                            match_date=ev_d,
                        ))
                    except (TypeError, ValueError):
                        continue
                if period_results:
                    sys.update_period(period_results)

            self._glicko = sys
        except Exception as exc:
            logger.warning("Glicko-2 fitting failed: %s", exc)

    async def _fit_zinb_leagues_async(self, records: list[dict]) -> None:
        """Fit one ZINB model per league in parallel worker processes.

        ProcessPoolExecutor instead of asyncio.to_thread: separate processes
        don't share the GIL with the asyncio event loop, so scipy Nelder-Mead
        callbacks can't starve HTTP requests. All eligible leagues are submitted
        at once and capped at 2 concurrent workers (matches Fly machine CPUs).
        """
        from collections import defaultdict
        by_league: dict[str, list[dict]] = defaultdict(list)
        for r in records:
            league = (r.get("league") or "unknown").lower().strip()
            by_league[league].append({
                "home_team_id": _team_hash(r["home_team"]),
                "away_team_id": _team_hash(r["away_team"]),
                "home_goals": int(r["home_score"]),
                "away_goals": int(r["away_score"]),
                "match_date": str(r["event_date"]),
            })

        eligible = [
            (league, matches)
            for league, matches in by_league.items()
            if len(matches) >= _MIN_FIXTURES_FOR_ZINB
        ]
        if not eligible:
            return

        loop = asyncio.get_running_loop()
        pool = _get_process_pool()

        futures = [
            loop.run_in_executor(pool, _fit_zinb_worker, matches)
            for _, matches in eligible
        ]
        results = await asyncio.gather(*futures, return_exceptions=True)
        for (league, _), result in zip(eligible, results):
            if isinstance(result, Exception):
                logger.debug("ZINB fit failed for %r: %s", league, result)
            elif getattr(result, "fitted", False):
                self._zinb_models[league] = result

    def _compute_ht_proxies(self, records: list[dict]) -> None:
        """
        Approximate first-half 0-0 and 1-0/0-1 rates from full-time score proxies.
        We use: full-time 0-0 rate as proxy for HT 0-0, 1-0 rate as proxy for HT low-score.
        True HT rates need HT scores in the DB; this is a reasonable approximation
        until half-time score ingestion is added.
        """
        from collections import defaultdict
        team_totals: dict[str, int] = defaultdict(int)
        team_ft00: dict[str, int] = defaultdict(int)
        team_ft10: dict[str, int] = defaultdict(int)
        team_ftgoals: dict[str, list] = defaultdict(list)

        for r in records:
            ht = r["home_team"]
            at = r["away_team"]
            hs = int(r["home_score"])
            as_ = int(r["away_score"])

            team_totals[ht] += 1
            team_totals[at] += 1
            team_ftgoals[ht].append(hs + as_)
            team_ftgoals[at].append(hs + as_)

            if hs == 0 and as_ == 0:
                team_ft00[ht] += 1
                team_ft00[at] += 1
            if (hs == 1 and as_ == 0) or (hs == 0 and as_ == 1):
                team_ft10[ht] += 1
                team_ft10[at] += 1

        for team, n in team_totals.items():
            key = team.lower().strip()
            if n > 0:
                self._ht_00_rates[key] = team_ft00.get(team, 0) / n
                self._ht_10_rates[key] = team_ft10.get(team, 0) / n
                recent = team_ftgoals.get(team, [])[-5:]
                self._atg[key] = sum(recent) / len(recent) if recent else 1.3

    # ------------------------------------------------------------------
    # Prediction API
    # ------------------------------------------------------------------

    def zinb_predict(
        self,
        league: str,
        home_team: str,
        away_team: str,
        fallback_lh: float = 1.35,
        fallback_la: float = 1.10,
    ) -> tuple[float, float]:
        """
        Return ZINB (mu_home, mu_away). Falls back to supplied Poisson lambdas
        when ZINB is unavailable for this league.
        """
        league_key = league.lower().strip()
        model = self._zinb_models.get(league_key)
        if model is None or not model.fitted:
            return fallback_lh, fallback_la

        try:
            return model.predict_goals(
                _team_hash(home_team),
                _team_hash(away_team),
            )
        except Exception:
            return fallback_lh, fallback_la

    def glicko_r_diff(self, home_team: str, away_team: str) -> Optional[float]:
        """Return home_rating - away_rating, or None if Glicko-2 not fitted."""
        if self._glicko is None:
            return None
        try:
            return self._glicko.rating_diff(home_team, away_team)
        except Exception:
            return None

    def glicko_rating_age_days(self, home_team: str, away_team: str) -> Optional[int]:
        """Days since the staler of the two teams' last match (>14 = stale). None if no data."""
        if self._glicko is None:
            return None
        try:
            from datetime import date as _d
            return self._glicko.rating_age_days(home_team, away_team, _d.today())
        except Exception:
            return None

    def ht_rates(
        self, home_team: str, away_team: str
    ) -> dict[str, float]:
        """
        Return {ht_00_home, ht_00_away, ht_10_home, ht_10_away, atg_home, atg_away}
        for BOS computation. Uses league-average defaults when data is absent.
        """
        hk = home_team.lower().strip()
        ak = away_team.lower().strip()
        return {
            "ht_00_home": self._ht_00_rates.get(hk, 0.25),
            "ht_00_away": self._ht_00_rates.get(ak, 0.25),
            "ht_10_home": self._ht_10_rates.get(hk, 0.30),
            "ht_10_away": self._ht_10_rates.get(ak, 0.30),
            "atg_home":   self._atg.get(hk, 1.30),
            "atg_away":   self._atg.get(ak, 1.30),
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _team_hash(name: str) -> int:
    """Stable integer ID from team name (used as ZINB team key)."""
    return hash(name.lower().strip()) & 0x7FFFFFFF

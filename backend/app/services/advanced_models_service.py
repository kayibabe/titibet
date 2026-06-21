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

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Minimum completed fixtures with scores per league to justify fitting ZINB.
_MIN_FIXTURES_FOR_ZINB: int = 20
# How far back (days) to pull historical fixtures for model fitting.
_LOOKBACK_DAYS: int = 365

# Process-level cache — ZINB + Glicko-2 fitting takes ~5 min; reuse across
# all Recompute calls within the same day. Keyed by reference_date.
_cache: dict[date, "AdvancedModelsService"] = {}


async def get_or_load(db: "AsyncSession", reference_date: date) -> "AdvancedModelsService":
    """Return a fully-loaded AdvancedModelsService, reusing the cached instance
    for the same reference_date so models are only fitted once per day per process."""
    if reference_date not in _cache:
        svc = AdvancedModelsService(db)
        await svc.load(reference_date=reference_date)
        _cache[reference_date] = svc
        # Evict any entries for other dates — keep memory bounded.
        for old_date in [d for d in list(_cache) if d != reference_date]:
            del _cache[old_date]
    return _cache[reference_date]


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

        # These fit scipy/numpy models across every league in the history and are
        # CPU-bound — seconds on a small DB, minutes once a full history is loaded.
        # Run them in a worker thread so the async event loop keeps serving requests
        # and health checks instead of freezing the single web worker (which made the
        # Fly proxy return 503 for the whole site during each sync). numpy/scipy
        # release the GIL during the heavy math, so the event loop stays responsive.
        import asyncio
        await asyncio.to_thread(self._fit_glicko, records)
        await asyncio.to_thread(self._fit_zinb_per_league, records)
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
                        ))
                    except (TypeError, ValueError):
                        continue
                if period_results:
                    sys.update_period(period_results)

            self._glicko = sys
        except Exception as exc:
            logger.warning("Glicko-2 fitting failed: %s", exc)

    def _fit_zinb_per_league(self, records: list[dict]) -> None:
        try:
            from app.engines.zinb import ZINBGoalModel

            # Group by league
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

            for league, matches in by_league.items():
                if len(matches) < _MIN_FIXTURES_FOR_ZINB:
                    continue
                try:
                    m = ZINBGoalModel()
                    m.fit(matches)
                    if m.fitted:
                        self._zinb_models[league] = m
                except Exception as exc:
                    logger.debug("ZINB fit failed for %r: %s", league, exc)
        except ImportError:
            pass

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

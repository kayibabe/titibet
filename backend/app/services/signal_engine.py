"""
signal_engine.py — Orchestrates Bayesian + Poisson engines and writes to signals table.

For each fixture on a date:
1. Load market_snapshots from DB, reconstruct engine inputs
2. Run BayesianEngine → BayesianFixtureResult
3. Run PoissonEngine → PoissonFixtureResult
4. For each active market: fuse via DualEngine → DualSignal
5. Upsert into signals table
"""
from __future__ import annotations

import asyncio
import math
import re
from datetime import date, datetime
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    CORRECT_SCORE_MARKET_NAMES,
    GOALS_MARKET_NAMES, MATCH_WINNER_MARKET_NAMES,
    DOUBLE_CHANCE_MARKET_NAMES, POISSON_RULES, get_settings,
    UNDER_GOALS_SUPPRESSED_LEAGUES,
    HOME_GOALS_MARKET_NAMES, AWAY_GOALS_MARKET_NAMES,
    WIN_TO_NIL_HOME_MARKET_NAMES, WIN_TO_NIL_AWAY_MARKET_NAMES,
    WIN_TO_NIL_COMBINED_MARKET_NAMES,
    EXACT_GOALS_MARKET_NAMES,
    DISABLED_MARKETS,
    DISABLED_LEAGUES,
    OVER_GOALS_SUPPRESSED_LEAGUES,
    MARKET_MAX_ODDS,
    MARKET_MIN_ODDS,
    POISSON_ONLY_MAX_ODDS,
    POISSON_ONLY_KELLY_CAP,
    MAX_DAILY_EXPOSURE,
    YOUTH_LEAGUE_KEYWORDS,
    get_league_tier,
)
from app.engines import bayesian as bay_engine
from app.services.form_service import get_team_form_lambdas
from app.engines import poisson as poi_engine
from app.engines import dual_engine
from app.engines import bos as bos_engine
from app.models import Fixture, MarketSnapshot, Signal
from app.services.performance_intelligence import compute_performance_weights, PerformanceWeights
from app.core.config import (
    BOS_SI_THRESHOLD, BOS_O00_MAX, BOS_CMA_MAX,
)

settings = get_settings()

# Map market name -> Poisson rule key (used in dual fusion).
# Keys must match Signal.market values exactly (what the DB stores).
MARKET_TO_POISSON_KEY: dict[str, str] = {
    "Under 2.5":    "cs00u25",   # CS cascade rule — enables dual-model agreement for Under 2.5
    "Over 1.5":     "over15",    # dedicated evaluator (rule_strong capable); cs00o15 cascade hardcodes rule_strong=False
    "Over 2.5":     "over25",
    "Home Over 0.5":  "home_o05",
}

# Maps each mixed-signal description to the specific markets it implicates.
# Used to scope contradiction flags per-market rather than fixture-wide,
# so a clean BTTS signal is not contaminated by an Over/Under conflict on the same fixture.
_MIXED_SIGNAL_MARKETS: dict[str, set[str]] = {
    "O2.5 signal + U2.5 CS":     {"Over 2.5", "Under 2.5"},
    "O2.5 signal + U3.5 Mid":    {"Over 2.5", "Under 3.5"},
}


def _league_matches_suppression(league_lower: str, keys: set) -> bool:
    """
    Check whether a league name matches any key in the suppression set.

    Short keys (< 6 chars, e.g. "mls") use word-boundary regex to prevent
    false positives like "mls" matching "Alliansen MLS Youth".
    Longer keys use plain substring matching for speed.
    """
    for k in keys:
        if len(k) < 6:
            pattern = r'\b' + re.escape(k) + r'\b'
            if re.search(pattern, league_lower):
                return True
        else:
            if k in league_lower:
                return True
    return False


def _latest_snapshots(snapshots: list[MarketSnapshot]) -> list[MarketSnapshot]:
    latest: dict[tuple[str, str, str], MarketSnapshot] = {}
    for snap in snapshots:
        key = (snap.bookmaker, snap.market_type, snap.selection_name)
        current = latest.get(key)
        if current is None:
            latest[key] = snap
            continue
        current_ts = current.pulled_at or datetime.min
        snap_ts = snap.pulled_at or datetime.min
        if snap_ts > current_ts or (snap_ts == current_ts and (snap.id or 0) > (current.id or 0)):
            latest[key] = snap
    return list(latest.values())


def _compute_opening_odds_scoped(snapshots: list[MarketSnapshot]) -> dict[tuple[str, str, str], float]:
    earliest: dict[tuple[str, str, str], tuple] = {}
    for snap in snapshots:
        if snap.pulled_at is None or snap.odds is None:
            continue
        key = (snap.bookmaker, snap.market_type, snap.selection_name)
        if key not in earliest or snap.pulled_at < earliest[key][0]:
            earliest[key] = (snap.pulled_at, snap.odds)
    return {key: value[1] for key, value in earliest.items()}


def _compute_opening_odds(snapshots: list[MarketSnapshot]) -> dict[tuple[str, str], float]:
    """
    Return the odds from the earliest snapshot for each (bookmaker, selection_name) pair.
    Used to compute drift: current_best_odd vs opening_best_odd for that bookmaker/market combo.
    """
    earliest: dict[tuple[str, str], tuple] = {}  # key → (pulled_at, odds)
    for s in snapshots:
        if s.pulled_at is None or s.odds is None:
            continue
        key = (s.bookmaker, s.selection_name)
        if key not in earliest or s.pulled_at < earliest[key][0]:
            earliest[key] = (s.pulled_at, s.odds)
    return {k: v[1] for k, v in earliest.items()}


def _build_cs_by_bookie(snapshots: list[MarketSnapshot]) -> dict[str, list[dict]]:
    """Group CS snapshots by bookmaker -> [{value: "1:0", odd: 6.50}, ...]"""
    result: dict[str, list[dict]] = {}
    for s in snapshots:
        if s.market_type in CORRECT_SCORE_MARKET_NAMES:
            result.setdefault(s.bookmaker, []).append({"value": s.selection_name, "odd": s.odds})
    return result


def _build_goals_ou(snapshots: list[MarketSnapshot]) -> dict[str, dict[str, float]]:
    """Goals O/U: {bookmaker: {label: odds}}"""
    result: dict[str, dict[str, float]] = {}
    for s in snapshots:
        if s.market_type in GOALS_MARKET_NAMES:
            result.setdefault(s.bookmaker, {})[s.selection_name] = s.odds
    return result


def _build_match_winner(snapshots: list[MarketSnapshot]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for s in snapshots:
        if s.market_type in MATCH_WINNER_MARKET_NAMES:
            result.setdefault(s.bookmaker, {})[s.selection_name] = s.odds
    return result


def _build_double_chance(snapshots: list[MarketSnapshot]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for s in snapshots:
        if s.market_type in DOUBLE_CHANCE_MARKET_NAMES:
            result.setdefault(s.bookmaker, {})[s.selection_name] = s.odds
    return result


def _build_home_totals(snapshots: list[MarketSnapshot]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for s in snapshots:
        if s.market_type in HOME_GOALS_MARKET_NAMES:
            result.setdefault(s.bookmaker, {})[s.selection_name] = s.odds
    return result


def _build_away_totals(snapshots: list[MarketSnapshot]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for s in snapshots:
        if s.market_type in AWAY_GOALS_MARKET_NAMES:
            result.setdefault(s.bookmaker, {})[s.selection_name] = s.odds
    return result


def _build_win_to_nil_home(snapshots: list[MarketSnapshot]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for s in snapshots:
        if s.market_type in WIN_TO_NIL_HOME_MARKET_NAMES:
            result.setdefault(s.bookmaker, {})[s.selection_name] = s.odds
        elif s.market_type in WIN_TO_NIL_COMBINED_MARKET_NAMES and s.selection_name == "Home":
            # Combined "Win To Nil" market (selections Home/Away) — normalise to
            # the Yes/No shape the Bayesian lookup maps expect.
            result.setdefault(s.bookmaker, {})["Yes"] = s.odds
    return result


def _build_win_to_nil_away(snapshots: list[MarketSnapshot]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for s in snapshots:
        if s.market_type in WIN_TO_NIL_AWAY_MARKET_NAMES:
            result.setdefault(s.bookmaker, {})[s.selection_name] = s.odds
        elif s.market_type in WIN_TO_NIL_COMBINED_MARKET_NAMES and s.selection_name == "Away":
            result.setdefault(s.bookmaker, {})["Yes"] = s.odds
    return result



def _build_exact_goals(snapshots: list[MarketSnapshot]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for s in snapshots:
        if s.market_type in EXACT_GOALS_MARKET_NAMES:
            result.setdefault(s.bookmaker, {})[s.selection_name] = s.odds
    return result


def _build_poisson_odds(snapshots: list[MarketSnapshot]) -> tuple[dict, dict]:
    """
    Build CS odds dict {s00, s10, s01, ...} and signal_odds {over1_5, over2_5, under2_5, home_o05, away_o05}
    for the Poisson engine.
    """
    cs_map: dict[str, float] = {}

    for s in snapshots:
        if s.market_type in CORRECT_SCORE_MARKET_NAMES:
            key = s.selection_name
            if key not in cs_map or s.odds > cs_map[key]:
                cs_map[key] = s.odds

    def _cs(home: int, away: int) -> Optional[float]:
        for k in [f"{home}:{away}", f"{home}-{away}", f"{home} - {away}"]:
            if k in cs_map:
                return cs_map[k]
        return None

    odds = {
        "s00": _cs(0, 0), "s10": _cs(1, 0), "s01": _cs(0, 1),
        "s11": _cs(1, 1), "s20": _cs(2, 0), "s02": _cs(0, 2),
        "s21": _cs(2, 1), "s12": _cs(1, 2), "s22": _cs(2, 2),
        "s31": _cs(3, 1), "s13": _cs(1, 3),
    }

    signal_odds: dict[str, float] = {}
    for s in snapshots:
        sel = s.selection_name.strip()
        mt = s.market_type
        if mt in GOALS_MARKET_NAMES:
            key_map = {
                "Over 1.5": "over1_5", "Over 2.5": "over2_5",
                "Under 2.5": "under2_5",
            }
            k = key_map.get(sel)
            if k and (k not in signal_odds or s.odds > signal_odds[k]):
                signal_odds[k] = s.odds
        elif mt in HOME_GOALS_MARKET_NAMES:
            if sel == "Over 0.5":
                if "home_o05" not in signal_odds or s.odds > signal_odds["home_o05"]:
                    signal_odds["home_o05"] = s.odds
        elif mt in AWAY_GOALS_MARKET_NAMES:
            if sel == "Over 0.5":
                if "away_o05" not in signal_odds or s.odds > signal_odds["away_o05"]:
                    signal_odds["away_o05"] = s.odds

    return odds, signal_odds


_CONFIDENCE_DOWNGRADE = {"High": "Medium", "Medium": "Low", "Low": "None"}


def _team_total_context_penalty(
    market: str,
    league_tier: Optional[int],
    form_lambdas: Optional[dict],
    best_odd: Optional[float],
    bookmaker_count: Optional[int],
) -> tuple[float, bool]:
    """
    Penalize fragile team-total-over signals before they reach the tracker.

    Focus areas:
    - weak scoring side being asked to score
    - strong mismatch against the selected side
    - long price for a low-bar team-total-over
    - thin bookmaker coverage
    - Tier 3 volatility on team-scoring markets
    """
    if market not in {"Home Over 0.5", "Home Over 1.5", "Away Over 0.5", "Away Over 1.5"}:
        return 1.0, False
    if not form_lambdas:
        return 1.0, False

    lambda_h = float(form_lambdas.get("lambda_h") or 0.0)
    lambda_a = float(form_lambdas.get("lambda_a") or 0.0)
    games_h = int(form_lambdas.get("games_h") or 0)
    games_a = int(form_lambdas.get("games_a") or 0)
    if games_h <= 0 or games_a <= 0:
        return 1.0, False

    is_home_market = market.startswith("Home ")
    is_high_bar = market.endswith("1.5")
    selected_lambda = lambda_h if is_home_market else lambda_a
    opponent_lambda = lambda_a if is_home_market else lambda_h
    mismatch_gap = opponent_lambda - selected_lambda

    penalty = 1.0
    severe = False

    if league_tier == 3:
        penalty *= 0.94

    if is_high_bar:
        if selected_lambda < 1.35:
            penalty *= 0.90
            severe = True
        if mismatch_gap > 0.50:
            penalty *= 0.94
        if best_odd is not None and best_odd >= 2.65:
            penalty *= 0.94
    else:
        if selected_lambda < 0.95:
            penalty *= 0.92
            severe = True
        if mismatch_gap > 0.45:
            penalty *= 0.93
        if best_odd is not None and best_odd >= 2.20:
            penalty *= 0.93

    if bookmaker_count is not None and bookmaker_count < 3:
        penalty *= 0.96

    if mismatch_gap > 0.75 and selected_lambda < (1.20 if is_high_bar else 0.90):
        severe = True

    return round(max(0.78, penalty), 3), severe


def _is_end_of_northern_season(d: date) -> bool:
    """
    True during the Northern Hemisphere end-of-season risk window (May 10 – June 30).
    Most European leagues finish in this period; Tier 3 matches become dead rubbers
    with teams already promoted/relegated, leading to 0-0 and defensive results.
    """
    return (d.month == 5 and d.day >= 10) or d.month == 6


_OVER_GOALS_MARKETS: frozenset = frozenset({
    "Over 1.5", "Over 2.5",
    "Home Over 0.5",
})


async def _get_underperforming_leagues(
    db: AsyncSession,
    min_bets: int = 5,
    min_roi_pct: float = 20.0,
) -> frozenset[str]:
    """
    Returns a frozenset of lowercased league names to suppress, combining:
      1. tracked_bets ROI: leagues with >= min_bets settled bets and ROI < min_roi_pct
      2. Active LearningProposal(change_type="league_suppression") rows written by the
         league watch guard when a watched league crosses its suppression threshold.

    Called once per signal batch so the suppression list is always current.
    """
    from sqlalchemy import text
    from app.models.learning_proposal import LearningProposal

    result = await db.execute(text("""
        SELECT league,
               COUNT(*)        AS n,
               SUM(profit_loss) AS total_pl,
               SUM(stake)       AS total_stake
        FROM tracked_bets
        WHERE result_status IN ('Won', 'Lost')
          AND stake > 0
          AND league IS NOT NULL
        GROUP BY league
        HAVING COUNT(*) >= :min_bets
    """), {"min_bets": min_bets})

    from app.core.config import TIER_1_LEAGUES
    bad: set[str] = set()
    for row in result.all():
        league, _n, total_pl, total_stake = row
        league_lower = (league or "").lower().strip()
        # Never auto-suppress Tier 1 leagues — they may share names across countries
        # (e.g. "Premier League" = England + Ethiopia) and are too important to block.
        if league_lower in TIER_1_LEAGUES:
            continue
        roi = (total_pl / total_stake) * 100 if total_stake else -100.0
        if roi < min_roi_pct:
            bad.add(league_lower)

    # Also include watch-guard-triggered suppressions.
    # These are substring keywords, not exact names — any league whose name contains
    # the keyword is suppressed (same matching logic used by the watch guard).
    try:
        lp_result = await db.execute(
            select(LearningProposal.target)
            .where(LearningProposal.change_type == "league_suppression")
            .where(LearningProposal.is_active == True)  # noqa: E712
        )
        for (target,) in lp_result.all():
            if target:
                bad.add(target.lower().strip())
    except Exception:
        pass  # table may not exist on first run — fail silently

    return frozenset(bad)


async def cs_generation_allowed(db: AsyncSession) -> bool:
    """
    Runtime guard for Correct Score signal generation.

    Returns True only when ALL of the following hold:
      1. CS_ENABLED is True (master kill switch)
      2. Settled CS-market TrackedBet count >= CS_MIN_SETTLED_BETS
      3. Latest calibration snapshot Brier skill for CS >= CS_MIN_BRIER_SKILL
         (or no CS-specific snapshot exists yet, in which case criterion 2 must pass)

    Call this before generating any CS picks. Even when CS_ENABLED is toggled on,
    insufficient bet history or poor calibration will block generation.
    """
    from app.core.config import CS_ENABLED, CS_MIN_SETTLED_BETS, CS_MIN_BRIER_SKILL, CS_MARKET_PREFIX
    from sqlalchemy import text as _text
    if not CS_ENABLED:
        return False
    try:
        row = (await db.execute(_text("""
            SELECT COUNT(*) FROM tracked_bets
            WHERE result_status IN ('Won','Lost')
              AND market_type LIKE :prefix
        """), {"prefix": CS_MARKET_PREFIX + "%"})).scalar() or 0
        if row < CS_MIN_SETTLED_BETS:
            return False
    except Exception:
        return False
    # Check latest calibration snapshot: CS market Brier skill must meet the floor.
    # If no CS-specific snapshot entry exists yet (early data), this check passes
    # so the settled-bets gate remains the only hard gate until calibration data builds.
    try:
        snap_row = (await db.execute(_text("""
            SELECT market_summary FROM calibration_snapshots
            ORDER BY created_at DESC LIMIT 1
        """))).scalar()
        if snap_row:
            import json as _json
            markets = _json.loads(snap_row) if isinstance(snap_row, str) else (snap_row or [])
            cs_skills = [
                float(m.get("brier_skill", 0.0))
                for m in markets
                if str(m.get("market", "")).startswith(CS_MARKET_PREFIX)
            ]
            if cs_skills and max(cs_skills) < CS_MIN_BRIER_SKILL:
                return False
    except Exception:
        pass  # calibration table not yet populated — don't block on it
    return True


async def compute_signals_for_date(db: AsyncSession, run_date: date) -> int:
    """
    Run both engines for all fixtures on run_date. Upserts into signals table.
    Returns count of signals written.

    Adaptive confidence: if historical data shows a (market, league_tier) combination
    has a performance_factor below 0.72 for 25+ settled bets, confidence is downgraded
    by one tier (High→Medium, Medium→Low). This prevents consistently-poor
    market+league combinations from ranking as high-confidence picks.

    Transaction strategy: all per-date signals are deleted in one short commit BEFORE
    the fixture loop, so the loop runs with no open write transaction. This prevents
    the 5-minute write lock that was blocking user track-picks and settlement writes
    (which hit SQLite's busy_timeout=15 s and then propagated as 30 s frontend timeouts).
    """
    fixture_result = await db.execute(
        select(Fixture).where(Fixture.event_date == run_date)
    )
    fixtures: list[Fixture] = list(fixture_result.scalars().all())

    # Load performance weights once for this date's signal batch.
    # Used to apply adaptive confidence downgrade when a (market, tier) slice
    # has proven consistently unreliable in settled history.
    try:
        perf_weights: Optional[PerformanceWeights] = await compute_performance_weights(db)
    except Exception:
        perf_weights = None

    # Leagues with 5+ settled bets and ROI < 20% are suppressed entirely —
    # no signals generated for any fixture from these leagues until performance recovers.
    try:
        underperforming_leagues: frozenset[str] = await _get_underperforming_leagues(db, min_roi_pct=60.0)
    except Exception:
        underperforming_leagues = frozenset()

    # Merge dynamic ROI-suppressed leagues with hard-coded blocklist
    all_suppressed_leagues = underperforming_leagues | DISABLED_LEAGUES

    # Initialise advanced models (ZINB, Glicko-2, BOS rate tables).
    # Fitted lazily from historical fixture data; gracefully no-ops if data
    # or scipy are unavailable.
    from app.services.advanced_models_service import get_or_load as _adv_get
    try:
        adv = await _adv_get(db, run_date)
    except Exception as _adv_err:
        import logging as _l
        _l.getLogger(__name__).warning("AdvancedModelsService.load() failed: %s", _adv_err)
        from app.services.advanced_models_service import AdvancedModelsService
        adv = AdvancedModelsService(db)

    # Pre-delete all existing signals for today in ONE short write transaction,
    # committed immediately before the fixture loop starts.  This keeps the loop
    # itself entirely read-only, so other writers (user track-picks, settlement,
    # auto-tracker) never hit SQLite's busy_timeout waiting for this session.
    if fixtures:
        fixture_ids_today = [f.id for f in fixtures]
        await db.execute(delete(Signal).where(Signal.fixture_id.in_(fixture_ids_today)))
        await db.commit()

        # The cached AI advisory for this date embeds acca legs + odds from the
        # signal rows just deleted — drop it so users never see an acca priced
        # off rows that no longer exist. The next advisory request (or the
        # scheduled cache-warm job) regenerates it from the fresh signals.
        from app.services.advisor_service import invalidate_advisory_cache
        await invalidate_advisory_cache(db, run_date)

    # Collect all new Signal objects across all fixtures before writing to DB.
    # This allows portfolio-level stake normalization (improvement #1) to run
    # after all per-signal Kelly stakes are computed, before the batch commit.
    pending_signals: list[Signal] = []

    count = 0
    _fixture_idx = 0
    for fixture in fixtures:
        _fixture_idx += 1
        # Yield to the event loop every 10 fixtures so HTTP requests can be
        # processed without waiting for the entire computation batch.
        if _fixture_idx % 10 == 0:
            await asyncio.sleep(0)
        # Skip fixtures from suppressed leagues (poor ROI or hard-disabled).
        # Uses _league_matches_suppression (substring for long keys, word-boundary
        # regex for short keys) so that e.g. "Friendlies Clubs" is caught by
        # "friendlies" and any Regionalliga variant by "regionalliga".
        _league_lower_check = (fixture.league or "").lower().strip()
        if all_suppressed_leagues and _league_matches_suppression(_league_lower_check, all_suppressed_leagues):
            continue

        # Skip youth / reserve fixtures — structurally unpredictable scoring.
        _league_lower = (fixture.league or "").lower()
        if any(kw in _league_lower for kw in YOUTH_LEAGUE_KEYWORDS):
            continue

        snap_result = await db.execute(
            select(MarketSnapshot).where(MarketSnapshot.fixture_id == fixture.id)
        )
        snapshots_raw: list[MarketSnapshot] = list(snap_result.scalars().all())
        if not snapshots_raw:
            continue
        snapshots = _latest_snapshots(snapshots_raw)

        cs_by_bookie = _build_cs_by_bookie(snapshots)
        goals_ou = _build_goals_ou(snapshots)
        match_winner = _build_match_winner(snapshots)
        double_chance = _build_double_chance(snapshots)
        home_totals = _build_home_totals(snapshots)
        away_totals = _build_away_totals(snapshots)
        wtn_home = _build_win_to_nil_home(snapshots)
        wtn_away = _build_win_to_nil_away(snapshots)
        exact_goals = _build_exact_goals(snapshots)
        poi_odds, poi_signal_odds = _build_poisson_odds(snapshots)
        opening_odds_map = _compute_opening_odds_scoped(snapshots_raw)

        bay_result = bay_engine.analyse_fixture(
            fixture_id=fixture.id,
            home_team=fixture.home_team,
            away_team=fixture.away_team,
            league=fixture.league or "",
            country=fixture.country or "",
            cs_by_bookie=cs_by_bookie,
            goals_ou=goals_ou,
            btts={},
            match_winner=match_winner,
            double_chance=double_chance,
            home_totals=home_totals,
            away_totals=away_totals,
            win_to_nil_home=wtn_home,
            win_to_nil_away=wtn_away,
            exact_goals=exact_goals,
            all_markets=True,
        )

        # ── Fix #1: rolling 6-game form lambda ───────────────────────────────
        # Query last N completed matches for each team and blend those goal
        # averages into the Poisson lambda.  Falls back to CS-only when there
        # is insufficient historical data (< form_min_games per team).
        form_lambdas = await get_team_form_lambdas(
            db=db,
            home_team=fixture.home_team,
            away_team=fixture.away_team,
            before_date=fixture.event_date or run_date,
        )

        poi_result = poi_engine.analyse_fixture(
            fixture_id=fixture.id,
            odds=poi_odds,
            signal_odds=poi_signal_odds,
            form_lambdas=form_lambdas or None,
        )

        # ── Advanced model enrichment ────────────────────────────────────────
        # Computed per-fixture; results are attached to matching Signal rows below.

        # ZINB: enriched expected goals from Zero-Inflated Negative Binomial model.
        # Falls back to form_lambdas when ZINB is not fitted for this league.
        _fl_h = (form_lambdas or {}).get("lambda_h") or 1.35
        _fl_a = (form_lambdas or {}).get("lambda_a") or 1.10
        _zinb_lh, _zinb_la = adv.zinb_predict(
            league=fixture.league or "",
            home_team=fixture.home_team,
            away_team=fixture.away_team,
            fallback_lh=_fl_h,
            fallback_la=_fl_a,
        )

        # BOS 2.0: Match Stability Index.
        # Uses 0-0 CS odds, match odds balance, historical ATG, and HT rates.
        _bos_result = None
        try:
            _bos_o00 = poi_odds.get("s00") or 0.0
            if _bos_o00 > 1.0:
                _ht_data = adv.ht_rates(fixture.home_team, fixture.away_team)
                # Extract match winner odds (favourite / underdog)
                _mw_odds = [v for bm in match_winner.values() for v in bm.values()]
                _mw_sorted = sorted(set(_mw_odds)) if _mw_odds else []
                _f_odds = _mw_sorted[0] if len(_mw_sorted) >= 2 else 1.70
                _u_odds = _mw_sorted[-1] if len(_mw_sorted) >= 2 else 2.50
                _bos_result = bos_engine.compute_si(
                    o_00=_bos_o00,
                    f_odds=_f_odds,
                    u_odds=_u_odds,
                    atg_home=_ht_data["atg_home"],
                    atg_away=_ht_data["atg_away"],
                    ht_00_home=_ht_data["ht_00_home"],
                    ht_00_away=_ht_data["ht_00_away"],
                    ht_10_home=_ht_data["ht_10_home"],
                    ht_10_away=_ht_data["ht_10_away"],
                    cma_max=BOS_CMA_MAX,
                    threshold=BOS_SI_THRESHOLD,
                    o00_max=BOS_O00_MAX,
                )
        except Exception:
            pass

        # Glicko-2: rating differential + rating freshness for quality scoring.
        _glicko_rdiff = adv.glicko_r_diff(fixture.home_team, fixture.away_team)
        _glicko_age   = adv.glicko_rating_age_days(fixture.home_team, fixture.away_team)

        # Index by rule_key (includes non-passing rules — needed for keyed lookup)
        poi_by_key: dict[str, poi_engine.PoissonResult] = {
            r.rule_key: r for r in poi_result.results
        }
        # Index by market name — only rules that passed (these drive all_markets)
        poi_by_market: dict[str, poi_engine.PoissonResult] = {
            r.market: r for r in poi_result.results if r.rule_pass
        }

        bay_by_market: dict[str, bay_engine.BayesianResult] = {}
        if bay_result:
            for mr in bay_result.market_results:
                bay_by_market[mr.market] = mr

        all_markets = set(bay_by_market.keys()) | set(poi_by_market.keys())

        fixture_league = (fixture.league or "").strip()
        # Recompute tier from league/country name — not the cached DB value.
        # This ensures config changes (e.g. adding World Cup to TIER_1_LEAGUES)
        # take effect immediately without needing to re-ingest fixtures.
        fixture_league_tier = get_league_tier(fixture.league or "", fixture.country or "")

        for market in all_markets:
            # Skip markets that have been permanently disabled (e.g. BTTS No, Under 3.5).
            if market in DISABLED_MARKETS:
                continue

            # ── Market maximum odds cap ───────────────────────────────────────
            # Some bookmakers (especially Asian) price team-total markets with
            # exotic semantics that inflate odds to 8–11+. Cap at a realistic
            # ceiling so the Poisson fallback doesn't latch onto mis-priced lines.
            _max_odd = MARKET_MAX_ODDS.get(market)
            if _max_odd:
                _b_cand = bay_by_market.get(market)
                _best = _b_cand.best_actual_odd if _b_cand else None
                if _best is not None and _best > _max_odd:
                    continue

            # ── Market minimum odds floor (Bayesian-level enforcement) ────────
            # MARKET_MIN_ODDS is already applied inside the Poisson evaluators,
            # but Bayesian-only signals bypass that path. Enforce the floor here
            # so near-certainty picks at terrible EV never reach the feed.
            _min_odd = MARKET_MIN_ODDS.get(market)
            if _min_odd:
                _b_cand_min = bay_by_market.get(market)
                _best_min = _b_cand_min.best_actual_odd if _b_cand_min else None
                if _best_min is not None and _best_min < _min_odd:
                    continue

            # League × market granular suppression: skip this specific combination
            # if historical performance shows it consistently loses money (ROI < 0,
            # 5+ settled bets). More surgical than whole-league suppression — e.g.
            # "Away Over 0.5 in Ekstraklasa" can be suppressed while other markets
            # in that league remain active.
            if perf_weights is not None and perf_weights.should_suppress_league_market(fixture_league, market):
                continue

            b = bay_by_market.get(market)
            p = poi_by_market.get(market)

            # Prefer the keyed Poisson lookup (covers markets not in poi_by_market
            # because they came in via a different rule_key, e.g. cs00o15 -> Over 1.5)
            p_key = MARKET_TO_POISSON_KEY.get(market)
            if p_key and p_key in poi_by_key:
                p = poi_by_key[p_key]

            # Filter mixed signals to only those that implicate this specific market.
            # Fixture-wide contradictions (e.g. O2.5 vs U3.5) must not contaminate
            # unrelated markets — a clean BTTS signal should not be flagged because
            # an Over/Under conflict exists on the same fixture.
            market_mixed = [
                s for s in poi_result.mixed_signals
                if market in _MIXED_SIGNAL_MARKETS.get(s, set())
            ]

            ds = dual_engine.fuse(
                fixture_id=fixture.id,
                market=market,
                bayesian=b,
                poisson=p,
                mixed_signals=market_mixed,
            )

            # ── Candidate pre-check (must happen before confidence gate) ──────
            # Candidate signals are stored in DB but NOT served to users.
            # They bypass the confidence="None" gate so we collect year-round data
            # for Over 1.5 / Over 2.5 even when only one engine fires weakly.
            # Two paths to candidacy:
            #   A) Poisson-strong (CS odds present): rule_pass AND rule_strong both True.
            #      Over 1.5 requires 2+ CS support conditions; Over 2.5 requires all
            #      core CS conditions to pass.
            #   B) Bayesian fallback (Goals O/U odds present, CS odds absent or weak):
            #      Bayesian derived_prob >= 0.70 AND odds imply positive EV (prob*odd > 1).
            #      Covers inter-season fixtures where only goals lines are published.
            # is_candidate is refined after is_dual_signal is known (below).
            _candidate_markets = {"Over 1.5", "Over 2.5"}
            _cand_best_odd = (
                poi_signal_odds.get("over1_5") if market == "Over 1.5"
                else poi_signal_odds.get("over2_5") if market == "Over 2.5"
                else None
            )
            _poi_strong_candidate = p is not None and p.rule_pass and p.rule_strong
            _bay_only_candidate = (
                b is not None
                and (b.derived_prob or 0.0) >= 0.70
                and _cand_best_odd is not None
                and ds.confidence == "None"  # dual engine weak — Bayesian solo signal
            )
            # Preliminary flag; refined to `is_candidate = ... and not is_dual_signal` below.
            is_candidate = (
                market in _candidate_markets
                and (_poi_strong_candidate or _bay_only_candidate)
                and (ds.confidence != "High" or ds.agreement != "Both")
                and _cand_best_odd is not None
                and _cand_best_odd >= 1.30
            )

            # Skip signals with no actionable confidence. This covers two cases:
            # (a) Zombie: both engines failed (confidence="None", agreement="None")
            # (b) Poisson-only grade-C or Bayesian-downgraded to None — untrackable noise.
            # Candidates bypass this gate — their confidence is irrelevant for data collection.
            if ds.confidence == "None" and not is_candidate:
                continue

            # ── Home Over 1.5 — Both-agreement only ──────────────────────────
            # Backtest: Poisson-only Home Over 1.5 hit 38% on 71 bets (-21% ROI).
            # Both-agreement hit 66.7% on 18 bets (+91% ROI). Poisson fires too
            # aggressively on this high-bar market without bookmaker confirmation.
            if market == "Home Over 1.5" and ds.agreement == "Poisson Only":
                continue

            # Also require displayable odds — Poisson-only signals are fine without
            # Bayesian odds, but pure Bayesian failures with no odds are not trackable.
            if b is None and (p is None or not p.rule_pass):
                continue

            # Under 2.5 odds cap: when best odds > 2.20 (< 45% prob),
            # bookmakers are pricing a high-scoring game -- drop the signal.
            if market == "Under 2.5":
                under25_cap = float(POISSON_RULES.get("under25_max_odds", 2.20))
                best_u25_odd = b.best_actual_odd if b else None
                if best_u25_odd is not None and best_u25_odd > under25_cap:
                    continue

            # League under-goals suppression.
            if market == "Under 2.5":
                league_lower = (fixture.league or "").lower()
                if _league_matches_suppression(league_lower, UNDER_GOALS_SUPPRESSED_LEAGUES):
                    continue

            # League over-goals suppression for remaining active over markets.
            if market in {"Over 1.5", "Over 2.5", "Home Over 0.5"}:
                league_lower = (fixture.league or "").lower()
                if _league_matches_suppression(league_lower, OVER_GOALS_SUPPRESSED_LEAGUES):
                    continue

            # Adaptive confidence downgrade.
            # If the (market, league_tier) slice has a performance_factor < 0.72 for
            # 25+ settled bets, this combination has historically underperformed.
            # Downgrade confidence by one tier so it ranks lower in the signal list.
            # The raw engine output is NOT changed — dual_agreement
            # still reflects what the models said, only dual_confidence is adjusted.
            league_tier = fixture_league_tier
            final_confidence = ds.confidence
            if (
                perf_weights is not None
                and ds.confidence not in ("None", "Low")
                and perf_weights.confidence_needs_downgrade(market, league_tier)
            ):
                final_confidence = _CONFIDENCE_DOWNGRADE.get(ds.confidence, ds.confidence)

            team_total_penalty, severe_team_total_flag = _team_total_context_penalty(
                market=market,
                league_tier=league_tier,
                form_lambdas=form_lambdas or None,
                best_odd=b.best_actual_odd if b else None,
                bookmaker_count=b.bookmaker_count if b else None,
            )
            adjusted_quality_score = round(ds.quality_score * team_total_penalty, 4)
            if severe_team_total_flag and final_confidence in ("High", "Medium"):
                final_confidence = _CONFIDENCE_DOWNGRADE.get(final_confidence, final_confidence)

            # Low confidence disabled entirely — overall +0.6% ROI not worth variance.
            if final_confidence == "Low":
                continue

            # ── Signal tier gates ─────────────────────────────────────────────
            # Tier 1 — Dual Signal: Both engines agree at High confidence.
            # Audit 2026-06-15: Both+High = +44.4% ROI, 61% WR (41 bets).
            is_dual_signal = final_confidence == "High" and ds.agreement == "Both"

            # Tier 2 — Poisson Signal: Poisson-only, rule_strong, odds < 2.49.
            # Applies only to Home Over 0.5 — the only market with validated
            # Poisson-only performance. Bayesian can't fire because CS odds are
            # absent for many fixtures (data gap, not model disagreement).
            # Backtest 2026-06-15: 269 signals, 78.1% WR, +47.2% ROI.
            # <2.50 cap: ≥2.50 band drops to 38.5% WR — Poisson loses calibration.
            _p_key_for_tier = MARKET_TO_POISSON_KEY.get(market, "")
            _poi_best_odd = float(poi_signal_odds.get(_p_key_for_tier) or 0.0)
            _poi_only_max = POISSON_ONLY_MAX_ODDS.get(market)
            is_poisson_signal = (
                market == "Home Over 0.5"
                and ds.agreement == "Poisson Only"
                and p is not None and p.rule_strong
                and _poi_best_odd > 1.0
                and (_poi_only_max is None or _poi_best_odd < _poi_only_max)
            )

            # Tier 3 — Bayesian-led Signal: Bayesian engine found value but
            # the dual gate didn't activate. Accepts High OR Medium Bayesian
            # confidence (edge ≥ 5%, positive EV at exec price). Two sub-cases:
            # (a) "Bayesian Only": Poisson had no data. Dual engine downgrades
            #     one tier: High→Medium, Medium→Low. We check raw b.confidence,
            #     NOT the downgraded final_confidence.
            # (b) "Both" at Medium: both agree direction but Poisson grade < A.
            # Accepting Medium catches markets where exec_odd fix unlocks
            # positive EV (e.g. 1xBet Goals O/U when William Hill is absent)
            # but edge is 5-7% (below the 7% High threshold).
            # Quarter-Kelly staking, same cap as Poisson-only.
            is_bayesian_signal = (
                b is not None
                and b.confidence in ("High", "Medium")
                and not is_dual_signal
                and ds.agreement in ("Bayesian Only", "Both")
            )

            # Refine is_candidate now that is_dual_signal is known.
            # A dual signal (Both+High) is already served live — no need to also flag as candidate.
            # Once ≥50 settled candidates exist, run a hit-rate / ROI audit and
            # enable as Tier 3 if numbers hold.
            is_candidate = is_candidate and not is_dual_signal

            if not is_dual_signal and not is_poisson_signal and not is_bayesian_signal and not is_candidate:
                continue

            # ── Stake sizing ──────────────────────────────────────────────────
            # Kelly retired 2026-07-02 (it self-zeroes without a model-vs-market
            # edge). Stakes are probability-scaled flat: cap × model probability.
            # Dual Signal: dual_engine already applied cap=max_kelly_pct (2%) via
            # the Bayesian engine. Single-engine tiers (Poisson-only / Bayesian-
            # led) use the lower POISSON_ONLY_KELLY_CAP (1.5%) — one engine only.
            if is_poisson_signal and p is not None and p.poisson_prob:
                adjusted_stake_pct = round(POISSON_ONLY_KELLY_CAP * p.poisson_prob, 4)
            elif is_bayesian_signal and not is_dual_signal and b is not None and b.derived_prob:
                adjusted_stake_pct = round(POISSON_ONLY_KELLY_CAP * b.derived_prob, 4)
            else:
                adjusted_stake_pct = ds.recommended_stake_pct

            # Performance-weighted multiplier applies to both tiers.
            # Proven (league, market) combos → stake boosted up to 1.75×
            # Underperforming combos → stake reduced down to 0.50×
            # No history yet → neutral (1.0×)
            if perf_weights is not None and adjusted_stake_pct:
                multiplier = perf_weights.stake_multiplier(fixture_league, market, final_confidence)
                adjusted_stake_pct = round(
                    min(0.10, max(0.002, adjusted_stake_pct * multiplier)), 4
                )

            # Odds drift: compare current best odd vs opening best odd for that bookmaker.
            # Negative drift = line shortened = sharp money confirmed our model.
            odds_drift: float | None = None
            if b and b.best_bookmaker and b.best_bookmaker != "N/A" and b.best_actual_odd:
                # Map Bayesian market name → selection_name used in snapshots
                from app.services.clv import _BET_TO_SELECTION, _MARKET_TYPE_SCOPE
                sel_name = _BET_TO_SELECTION.get(market, market)
                market_scope = _MARKET_TYPE_SCOPE.get(market, frozenset({market}))
                opening_candidates = [
                    opening_odds_map[(b.best_bookmaker, market_type, sel_name)]
                    for market_type in market_scope
                    if (b.best_bookmaker, market_type, sel_name) in opening_odds_map
                ]
                opening_odd = max(opening_candidates) if opening_candidates else None
                if opening_odd and opening_odd > 0:
                    odds_drift = round((b.best_actual_odd - opening_odd) / opening_odd * 100, 2)

            # ── Probability shrinkage — empirical calibration correction ─────
            # Backtest: signals at 80–89% model prob hit only 76.9% (-7.6pp).
            # At 90%+ they hit 71.4% (-21.6pp). The Bayesian overround correction
            # is not fully eliminating bookmaker margin, leaving probabilities
            # systematically inflated above 75%. Shrink toward the threshold to
            # prevent over-staking on overconfident picks.
            _shrink_threshold = float(POISSON_RULES.get("prob_shrink_threshold", 0.75))
            _shrink_factor    = float(POISSON_RULES.get("prob_shrink_factor", 0.88))
            _shrink_threshold_hi = float(POISSON_RULES.get("prob_shrink_threshold_hi", 0.80))
            _shrink_factor_hi    = float(POISSON_RULES.get("prob_shrink_factor_hi", 0.35))
            _raw_prob = (b.derived_prob if b else None) or (p.poisson_prob if p else None)
            if _raw_prob is not None and _raw_prob > _shrink_threshold:
                # Stage 1: gentle correction for 75-80% band
                _raw_prob = _shrink_threshold + (_raw_prob - _shrink_threshold) * _shrink_factor
                # Stage 2: stronger correction for 80%+ band, applied to stage-1 output.
                # Audit 2026-06-03: 80-90% bucket showed -6.9pp calibration error.
                if _raw_prob > _shrink_threshold_hi:
                    _raw_prob = _shrink_threshold_hi + (_raw_prob - _shrink_threshold_hi) * _shrink_factor_hi

            # ── BOS quality boost / penalty ───────────────────────────────────
            # BOS high SI = stable, LOW-scoring fixture.  Apply a boost only to
            # markets that benefit from low-scoring stability (Under 2.5 and Win
            # to Nil).  For Over-goals markets, BOS passing is contradictory —
            # apply a 5% quality penalty to reflect the misalignment.
            _BOS_BOOSTED_MARKETS = frozenset({"Under 2.5", "Home Win to Nil", "Away Win to Nil"})
            _final_quality = adjusted_quality_score
            if _bos_result is not None and _bos_result.passed:
                si_norm = min(_bos_result.si / 400.0, 1.0)  # normalise to [0,1]
                if market in _BOS_BOOSTED_MARKETS:
                    _final_quality = round(_final_quality * (1.0 + 0.10 * si_norm), 4)
                elif market in _OVER_GOALS_MARKETS:
                    # Stable low-scoring fixture contradicts an over-goals pick.
                    _final_quality = round(_final_quality * 0.95, 4)

            # ── ZINB × Under 2.5 cross-check ─────────────────────────────────
            # When the Zero-Inflated NB model predicts total expected goals > 3.0
            # the enriched scoring model disagrees with the Under 2.5 signal.
            # Drop the pick rather than let the Bayesian CS model overrule a
            # second opinion that saw more fixture context (home/away form, league
            # attack/defence rates).  Guard: only block when ZINB is fitted for
            # this league (lambda_h > 0.1 — values near zero indicate fallback).
            if market == "Under 2.5" and _zinb_lh and _zinb_la:
                if _zinb_lh > 0.1 and _zinb_la > 0.1 and (_zinb_lh + _zinb_la) > 3.0:
                    continue

            # ── End-of-northern-season caution (May 10 – June 30) ────────────
            # European leagues finish in this window.  Teams already promoted /
            # relegated or with nothing to play for rotate squads, producing
            # 0-0 and defensive results that defeat over-goals models.
            # For Tier 2/3 fixtures in this period: drop Over-goals signals
            # (Over 1.5, Over 2.5, Home Over 0.5) and downgrade any remaining
            # signal confidence by one tier so dead-rubber picks rank lower.
            if _is_end_of_northern_season(run_date):
                _tier = fixture_league_tier
                # Only suppress Tier 3 for over-goals markets.
                # Tier 2 leagues active in June are summer/year-round competitions
                # (Erovnuli Liga, Veikkausliiga, MLS Next Pro, Botola Pro, etc.) that
                # are mid-season — not European dead-rubber end-of-season games.
                # European Tier 2 leagues (Championship, Serie B, etc.) finish by
                # end of May and produce no fixtures in June.
                if _tier >= 3 and market in _OVER_GOALS_MARKETS:
                    # Candidates bypass the seasonal suppression — we need year-round
                    # data collection to build a representative backtest sample.
                    if not is_candidate:
                        continue

            # For Poisson-only signals, surface the bookmaker odds from the
            # Poisson signal odds dict so the router can display and rank them.
            # Candidates for Over 1.5/2.5 use _cand_best_odd as final fallback
            # because _poi_best_odd's key ("over25") differs from poi_signal_odds ("over2_5").
            # Use exec_odd (not the raw display price) — exec_odd is already
            # haircut-adjusted toward what local bookmakers (betPawa/Betway)
            # actually offer, vs. the William Hill proxy price which runs higher.
            _effective_best_odd = (
                (b.exec_odd if b else None)
                or (_poi_best_odd if _poi_best_odd > 1.0 else None)
                or (_cand_best_odd if (is_candidate and _cand_best_odd and _cand_best_odd > 1.0) else None)
            )

            sig = Signal(
                fixture_id=fixture.id,
                market=market,
                bayesian_prob=b.derived_prob if b else None,
                bayesian_edge=b.edge if b else None,
                bayesian_best_odd=_effective_best_odd,
                bayesian_bookmaker=b.best_bookmaker if b else None,
                bayesian_overround=b.overround if b else None,
                bayesian_coverage=b.coverage if b else None,
                bayesian_bookmaker_count=b.bookmaker_count if b else None,
                bayesian_is_value=b.is_value if b else None,
                bayesian_confidence=b.confidence if b else None,
                bayesian_quality_score=b.quality_score if b else None,
                bayesian_kelly_pct=b.kelly_pct if b else None,
                bayesian_odds_outlier=b.is_outlier_odds if b else None,
                bayesian_consensus_odd=b.consensus_odd if b else None,
                poisson_lambda_h=p.lambda_h if p else None,
                poisson_lambda_a=p.lambda_a if p else None,
                poisson_lambda_total=p.lambda_total if p else None,
                poisson_prob=p.poisson_prob if p else None,
                poisson_rule_key=p.rule_key if p else None,
                poisson_rule_pass=p.rule_pass if p else None,
                poisson_rule_strong=p.rule_strong if p else None,
                poisson_edge_pct=p.edge_pct if p else None,
                poisson_grade=p.grade if p else None,
                # Fixture-level — same list on every row for this fixture, by design.
                poisson_mixed_signals=poi_result.mixed_signals or None,
                # dual_confidence uses the adaptive downgraded value when performance
                # history shows this (market, league_tier) is consistently unreliable.
                dual_confidence=final_confidence,
                dual_agreement=ds.agreement,
                dual_quality_score=_final_quality,
                dual_recommended_stake_pct=adjusted_stake_pct,
                contradiction=ds.contradiction,
                odds_drift_pct=odds_drift,
                # ── Advanced model fields ────────────────────────────────────
                bos_si=_bos_result.si if _bos_result else None,
                bos_passed=_bos_result.passed if _bos_result else None,
                zinb_lambda_h=round(_zinb_lh, 4) if _zinb_lh else None,
                zinb_lambda_a=round(_zinb_la, 4) if _zinb_la else None,
                glicko_r_diff=_glicko_rdiff,
                glicko_rating_age_days=_glicko_age,
                is_candidate=is_candidate,
            )
            pending_signals.append(sig)
            count += 1

        # ── Shared lambda values used by flip signals ────────────────────────────
        _fl_lh = (poi_by_key.get("home_o05") or None)
        _fl_la = (poi_by_key.get("away_o05") or None)
        _fl_lambda_h = _fl_lh.lambda_h if _fl_lh else None
        _fl_lambda_a = _fl_la.lambda_a if _fl_la else None

        def _flip_signal(
            market: str,
            rule_key: str,
            prob: float | None,
            best_odd: float | None,
            bay: object,
            lh: float | None,
            la: float | None,
            strong: bool,
        ) -> Signal | None:
            """Build a Poisson-only flip Signal, running it through dual_engine.fuse."""
            if prob is None or best_odd is None or best_odd <= 1.0:
                return None
            # _edge is diagnostic only (edge_pct field) — the edge floor gate
            # was removed 2026-07-02 with the rest of the EV gating.
            _edge = prob - (1.0 / best_odd)
            _grade = "A" if strong else "B"
            _p = poi_engine.PoissonResult(
                rule_key=rule_key, market=market,
                rule_pass=True, rule_strong=strong,
                poisson_prob=prob,
                edge_pct=round(_edge * 100, 2),
                has_edge=True,
                grade=_grade,
                lambda_h=lh, lambda_a=la,
                lambda_total=(lh or 0) + (la or 0),
                form_blended=bool(form_lambdas),
            )
            _ds = dual_engine.fuse(
                fixture_id=fixture.id, market=market,
                bayesian=bay, poisson=_p, mixed_signals=[],
            )
            if _ds.confidence == "None":
                return None
            # Apply same confidence + agreement gates as main signal path.
            if _ds.confidence != "High" or _ds.agreement != "Both":
                return None
            if perf_weights is not None and perf_weights.should_suppress_league_market(fixture_league, market):
                return None
            _qs = _ds.quality_score
            if _bos_result is not None and _bos_result.passed:
                _qs = round(_qs * (1.0 + 0.10 * min(_bos_result.si / 400.0, 1.0)), 4)
            _stake = _ds.recommended_stake_pct
            if perf_weights is not None and _stake:
                _m = perf_weights.stake_multiplier(fixture_league, market, _ds.confidence)
                _stake = round(min(0.10, max(0.002, _stake * _m)), 4)
            return Signal(
                fixture_id=fixture.id, market=market,
                bayesian_prob=bay.derived_prob if bay else None,
                bayesian_edge=bay.edge if bay else None,
                bayesian_best_odd=bay.exec_odd if bay else None,
                bayesian_bookmaker=bay.best_bookmaker if bay else None,
                bayesian_overround=bay.overround if bay else None,
                bayesian_coverage=bay.coverage if bay else None,
                bayesian_bookmaker_count=bay.bookmaker_count if bay else None,
                bayesian_is_value=bay.is_value if bay else None,
                bayesian_confidence=bay.confidence if bay else None,
                bayesian_quality_score=bay.quality_score if bay else None,
                bayesian_kelly_pct=bay.kelly_pct if bay else None,
                bayesian_odds_outlier=bay.is_outlier_odds if bay else None,
                bayesian_consensus_odd=bay.consensus_odd if bay else None,
                poisson_lambda_h=lh, poisson_lambda_a=la,
                poisson_lambda_total=(lh or 0) + (la or 0),
                poisson_prob=prob,
                poisson_rule_key=rule_key,
                poisson_rule_pass=True, poisson_rule_strong=strong,
                poisson_edge_pct=round(_edge * 100, 2),
                poisson_grade=_grade,
                poisson_mixed_signals=None,
                dual_confidence=_ds.confidence, dual_agreement=_ds.agreement,
                dual_quality_score=_qs, dual_recommended_stake_pct=_stake,
                contradiction=False, odds_drift_pct=None,
                bos_si=_bos_result.si if _bos_result else None,
                bos_passed=_bos_result.passed if _bos_result else None,
                zinb_lambda_h=round(_zinb_lh, 4) if _zinb_lh else None,
                zinb_lambda_a=round(_zinb_la, 4) if _zinb_la else None,
                glicko_r_diff=_glicko_rdiff,
                glicko_rating_age_days=_glicko_age,
            )

        # ── Home Win to Nil flip — weak away scorer + scoring home ───────────────
        # Empirical: when Away Over 0.5 loses (away=0), home scored ≥1 in 67% of cases.
        # P(Home WtN) ≈ P(away=0) × P(home≥1) = e^(-λ_a) × (1 - e^(-λ_h))
        # Trigger: λ_away < 0.90 (likely to blank) AND λ_home > 1.0 (likely to score).
        if (
            "Home Win to Nil" not in DISABLED_MARKETS
            and _fl_lambda_h is not None and _fl_lambda_a is not None
            and _fl_lambda_a < float(POISSON_RULES.get("hwtn_flip_away_lambda", 0.90))
            and _fl_lambda_h > float(POISSON_RULES.get("hwtn_flip_home_lambda_min", 1.0))
            and not any(s.market == "Home Win to Nil" and s.fixture_id == fixture.id for s in pending_signals)
        ):
            _b_hwtn = bay_by_market.get("Home Win to Nil")
            _hwtn_odd = _b_hwtn.best_actual_odd if _b_hwtn else None
            _hwtn_min = float(MARKET_MIN_ODDS.get("Home Win to Nil", 1.40))
            if _hwtn_odd and _hwtn_odd >= _hwtn_min:
                _hwtn_prob = math.exp(-_fl_lambda_a) * (1.0 - math.exp(-_fl_lambda_h))
                _hwtn_strong = _fl_lambda_a < 0.70 and _fl_lambda_h > 1.3
                _sig = _flip_signal("Home Win to Nil", "hwtn_flip", _hwtn_prob, _hwtn_odd,
                                    _b_hwtn, _fl_lambda_h, _fl_lambda_a, _hwtn_strong)
                if _sig:
                    pending_signals.append(_sig)
                    count += 1

        # ── Away Win to Nil flip — weak home scorer + scoring away ───────────────
        # Empirical: when Home Over 0.5 loses (home=0), away scored ≥1 in 48% of cases.
        # Tighter lambda gate than Home WtN to keep accuracy above 60% threshold.
        # P(Away WtN) ≈ P(home=0) × P(away≥1) = e^(-λ_h) × (1 - e^(-λ_a))
        # Trigger: λ_home < 0.70 (very weak home scorer) AND λ_away > 1.0.
        if (
            "Away Win to Nil" not in DISABLED_MARKETS
            and _fl_lambda_h is not None and _fl_lambda_a is not None
            and _fl_lambda_h < float(POISSON_RULES.get("awtn_flip_home_lambda", 0.70))
            and _fl_lambda_a > float(POISSON_RULES.get("awtn_flip_away_lambda_min", 1.0))
            and not any(s.market == "Away Win to Nil" and s.fixture_id == fixture.id for s in pending_signals)
        ):
            _b_awtn = bay_by_market.get("Away Win to Nil")
            _awtn_odd = _b_awtn.best_actual_odd if _b_awtn else None
            _awtn_min = float(MARKET_MIN_ODDS.get("Away Win to Nil", 1.40))
            if _awtn_odd and _awtn_odd >= _awtn_min:
                _awtn_prob = math.exp(-_fl_lambda_h) * (1.0 - math.exp(-_fl_lambda_a))
                _awtn_strong = _fl_lambda_h < 0.55 and _fl_lambda_a > 1.3
                _sig = _flip_signal("Away Win to Nil", "awtn_flip", _awtn_prob, _awtn_odd,
                                    _b_awtn, _fl_lambda_h, _fl_lambda_a, _awtn_strong)
                if _sig:
                    pending_signals.append(_sig)
                    count += 1

    # ── Portfolio stake normalization ─────────────────────────────────────────
    # Cap total daily recommended exposure at MAX_DAILY_EXPOSURE (15 % of bankroll).
    # Scale all stakes proportionally so the strongest signals keep the largest
    # share while the aggregate stays within safe bankroll limits.
    stakeable = [s for s in pending_signals if (s.dual_recommended_stake_pct or 0) > 0]
    if stakeable:
        total_stake = sum(s.dual_recommended_stake_pct for s in stakeable)
        if total_stake > MAX_DAILY_EXPOSURE:
            scale = MAX_DAILY_EXPOSURE / total_stake
            for s in stakeable:
                s.dual_recommended_stake_pct = round(s.dual_recommended_stake_pct * scale, 4)

    # Write signals in small batches so the write lock is released between
    # commits, letting other requests (bets, health checks) slip through.
    _WRITE_BATCH = 50
    for i in range(0, len(pending_signals), _WRITE_BATCH):
        for sig in pending_signals[i : i + _WRITE_BATCH]:
            db.add(sig)
        await db.commit()
        await asyncio.sleep(0)   # yield between batches

    return count

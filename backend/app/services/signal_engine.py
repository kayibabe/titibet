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

import re
from datetime import date, datetime
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    CORRECT_SCORE_MARKET_NAMES,
    FIRST_HALF_CS_MARKET_NAMES, GOALS_MARKET_NAMES,
    BTTS_MARKET_NAMES, MATCH_WINNER_MARKET_NAMES,
    DOUBLE_CHANCE_MARKET_NAMES, POISSON_RULES, get_settings,
    UNDER_GOALS_SUPPRESSED_LEAGUES,
    HOME_GOALS_MARKET_NAMES, AWAY_GOALS_MARKET_NAMES,
    WIN_TO_NIL_HOME_MARKET_NAMES, WIN_TO_NIL_AWAY_MARKET_NAMES,
    EXACT_GOALS_MARKET_NAMES,
    DISABLED_MARKETS,
    DISABLED_LEAGUES,
    OVER_GOALS_SUPPRESSED_LEAGUES,
    AWAY_GOALS_SUPPRESSED_LEAGUES,
    MARKET_MAX_ODDS,
    MAX_DAILY_EXPOSURE,
    WOMEN_LEAGUE_KEYWORDS,
)
from app.engines import bayesian as bay_engine
from app.services.form_service import get_team_form_lambdas
from app.engines import poisson as poi_engine
from app.engines import dual_engine
from app.models import Fixture, MarketSnapshot, Signal
from app.services.performance_intelligence import compute_performance_weights, PerformanceWeights

settings = get_settings()

# Map market name -> Poisson rule key (used in dual fusion).
# Keys must match Signal.market values exactly (what the DB stores).
MARKET_TO_POISSON_KEY: dict[str, str] = {
    "BTTS Yes":     "btts",
    "Under 3.5":    "under35",
    "Under 2.5":    "cs00u25",   # CS cascade rule — enables dual-model agreement for Under 2.5
    "Over 0.5":     "over05ft",
    "Over 1.5":     "cs00o15",   # primary Poisson signal for Over 1.5
    "Over 2.5":     "over25",
    "Over 3.5":     "over35ft",
    "Over 0.5 1H":  "over05fh",  # first-half signal - Poisson only (no Bayesian equivalent)
    "Home Over 0.5":  "home_o05",
    "Home Over 1.5":  "home_o15",
    "Away Over 0.5":  "away_o05",
    "Away Over 1.5":  "away_o15",
}

# Maps each mixed-signal description to the specific markets it implicates.
# Used to scope contradiction flags per-market rather than fixture-wide,
# so a clean BTTS signal is not contaminated by an Over/Under conflict on the same fixture.
_MIXED_SIGNAL_MARKETS: dict[str, set[str]] = {
    "O2.5 signal + U3.5":        {"Over 2.5", "Under 3.5"},
    "O3.5 marginal λ + U3.5":   {"Over 3.5", "Under 3.5"},
    "O2.5 signal + U2.5 CS":     {"Over 2.5", "Under 2.5"},
    "O2.5 signal + U3.5 Mid":    {"Over 2.5", "Under 3.5"},
    "O1.5 signal + U3.5":        {"Over 1.5", "Under 3.5"},
    "O1.5 CS + U3.5":            {"Over 1.5", "Under 3.5"},
    "O1.5 Extreme + U3.5":       {"Over 1.5", "Under 3.5"},
    "Strong BTTS + strong U3.5": {"BTTS Yes", "Under 3.5"},
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


def _build_btts(snapshots: list[MarketSnapshot]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for s in snapshots:
        if s.market_type in BTTS_MARKET_NAMES:
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
    return result


def _build_win_to_nil_away(snapshots: list[MarketSnapshot]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for s in snapshots:
        if s.market_type in WIN_TO_NIL_AWAY_MARKET_NAMES:
            result.setdefault(s.bookmaker, {})[s.selection_name] = s.odds
    return result


def _build_exact_goals(snapshots: list[MarketSnapshot]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for s in snapshots:
        if s.market_type in EXACT_GOALS_MARKET_NAMES:
            result.setdefault(s.bookmaker, {})[s.selection_name] = s.odds
    return result


def _build_poisson_odds(snapshots: list[MarketSnapshot]) -> tuple[dict, dict]:
    """
    Build CS odds dict {s00, s10, s01, ...} and signal_odds {btts_yes, under3_5, ...}
    for the Poisson engine.
    """
    cs_map: dict[str, float] = {}
    fh_cs_map: dict[str, float] = {}

    for s in snapshots:
        if s.market_type in CORRECT_SCORE_MARKET_NAMES:
            key = s.selection_name
            if key not in cs_map or s.odds > cs_map[key]:
                cs_map[key] = s.odds
        elif s.market_type in FIRST_HALF_CS_MARKET_NAMES:
            key = s.selection_name
            if key not in fh_cs_map or s.odds > fh_cs_map[key]:
                fh_cs_map[key] = s.odds

    def _cs(home: int, away: int) -> Optional[float]:
        for k in [f"{home}:{away}", f"{home}-{away}", f"{home} - {away}"]:
            if k in cs_map:
                return cs_map[k]
        return None

    def _fh(h: int, a: int) -> Optional[float]:
        for k in [f"{h}:{a}", f"{h}-{a}"]:
            if k in fh_cs_map:
                return fh_cs_map[k]
        return None

    odds = {
        "s00": _cs(0, 0), "s10": _cs(1, 0), "s01": _cs(0, 1),
        "s11": _cs(1, 1), "s20": _cs(2, 0), "s02": _cs(0, 2),
        "s21": _cs(2, 1), "s12": _cs(1, 2), "s22": _cs(2, 2),
        "s31": _cs(3, 1), "s13": _cs(1, 3),
        "fh_s00": _fh(0, 0), "fh_s11": _fh(1, 1),
    }

    signal_odds: dict[str, float] = {}
    for s in snapshots:
        sel = s.selection_name.strip()
        mt = s.market_type
        if mt in GOALS_MARKET_NAMES:
            key_map = {
                "Over 0.5": "over0_5",
                "Over 1.5": "over1_5", "Over 2.5": "over2_5",
                "Over 3.5": "over3_5",
                "Under 2.5": "under2_5", "Under 3.5": "under3_5",
            }
            k = key_map.get(sel)
            if k and (k not in signal_odds or s.odds > signal_odds[k]):
                signal_odds[k] = s.odds
        elif mt in HOME_GOALS_MARKET_NAMES:
            hk = {"Over 0.5": "home_o05", "Over 1.5": "home_o15"}.get(sel)
            if hk and (hk not in signal_odds or s.odds > signal_odds[hk]):
                signal_odds[hk] = s.odds
        elif mt in AWAY_GOALS_MARKET_NAMES:
            ak = {"Over 0.5": "away_o05", "Over 1.5": "away_o15"}.get(sel)
            if ak and (ak not in signal_odds or s.odds > signal_odds[ak]):
                signal_odds[ak] = s.odds
        elif mt in BTTS_MARKET_NAMES:
            if sel in ("Yes", "GG") and ("btts_yes" not in signal_odds or s.odds > signal_odds["btts_yes"]):
                signal_odds["btts_yes"] = s.odds
        if "first half" in mt.lower() or "halftime" in mt.lower():
            if sel == "Over 0.5":
                if "over0_5_fh" not in signal_odds or s.odds > signal_odds["over0_5_fh"]:
                    signal_odds["over0_5_fh"] = s.odds

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
    "Over 0.5", "Over 1.5", "Over 2.5", "Over 3.5",
    "Home Over 0.5", "Home Over 1.5",
    "Away Over 0.5", "Away Over 1.5",
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

    bad: set[str] = set()
    for row in result.all():
        league, _n, total_pl, total_stake = row
        roi = (total_pl / total_stake) * 100 if total_stake else -100.0
        if roi < min_roi_pct:
            bad.add(league.lower().strip())

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


async def compute_signals_for_date(db: AsyncSession, run_date: date) -> int:
    """
    Run both engines for all fixtures on run_date. Upserts into signals table.
    Returns count of signals written.

    Adaptive confidence: if historical data shows a (market, league_tier) combination
    has a performance_factor below 0.72 for 25+ settled bets, confidence is downgraded
    by one tier (High→Medium, Medium→Low). This prevents the accumulator from treating
    consistently-poor market+league combinations as high-confidence picks.
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

    # Collect all new Signal objects across all fixtures before writing to DB.
    # This allows portfolio-level stake normalization (improvement #1) to run
    # after all per-signal Kelly stakes are computed, before the batch commit.
    pending_signals: list[Signal] = []

    count = 0
    for fixture in fixtures:
        # Skip fixtures from suppressed leagues (poor ROI or hard-disabled).
        if all_suppressed_leagues and (fixture.league or "").lower().strip() in all_suppressed_leagues:
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
        btts_dict = _build_btts(snapshots)
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
            btts=btts_dict,
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

        await db.execute(delete(Signal).where(Signal.fixture_id == fixture.id))

        fixture_league = (fixture.league or "").strip()

        for market in all_markets:
            # Skip markets that have been permanently disabled (e.g. BTTS No, Under 3.5).
            if market in DISABLED_MARKETS:
                continue

            # ── Improvement: Tier restriction for Over 1.5 markets ────────────
            # Home/Away Over 1.5 requires a team to score 2+ goals — unreliable
            # in Tier 3 leagues (weaker attack, more defensive play, end-of-season).
            if market in {"Home Over 1.5", "Away Over 1.5"} and (fixture.league_tier or 3) >= 3:
                continue


            # ── Improvement: Women's league over-goals odds ceiling ───────────
            # Women's football averages fewer goals than men's. The Poisson model
            # (calibrated on mixed data) over-estimates scoring rates. Cap both
            # away and home over-goals at tighter odds in women's competitions.
            if market in {"Away Over 0.5", "Away Over 1.5", "Home Over 0.5", "Home Over 1.5"}:
                league_lower = (fixture.league or "").lower()
                if any(kw in league_lower for kw in WOMEN_LEAGUE_KEYWORDS):
                    _women_best_odd = None
                    _b_candidate = bay_by_market.get(market)
                    if _b_candidate:
                        _women_best_odd = _b_candidate.best_actual_odd
                    # Away ceiling: 2.30; Home ceiling: 2.50 (home teams more predictable)
                    _women_ceil = 2.30 if market.startswith("Away") else 2.50
                    if _women_best_odd is not None and _women_best_odd > _women_ceil:
                        continue

            # ── Improvement: Tier 3 away-over high-odds ceiling ──────────────
            # Away teams in lower-tier leagues are particularly unpredictable at
            # long odds. Cap Away Over 0.5 at 2.50 for Tier 3 leagues to avoid
            # backing low-probability away scorers in volatile markets.
            if market == "Away Over 0.5" and (fixture.league_tier or 3) >= 3:
                _t3_best_odd = None
                _b_candidate = bay_by_market.get(market)
                if _b_candidate:
                    _t3_best_odd = _b_candidate.best_actual_odd
                if _t3_best_odd is not None and _t3_best_odd > 2.50:
                    continue

            # ── Targeted away-goals suppression ──────────────────────────────
            # Leagues in AWAY_GOALS_SUPPRESSED_LEAGUES have structurally poor
            # away-scoring reliability regardless of odds. Skip Away Over signals
            # entirely to avoid losses driven by home-field dominance or
            # defensive-style play that models cannot capture.
            if market in {"Away Over 0.5", "Away Over 1.5"}:
                if AWAY_GOALS_SUPPRESSED_LEAGUES and _league_matches_suppression(
                    (fixture.league or "").lower(), AWAY_GOALS_SUPPRESSED_LEAGUES
                ):
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

            # Skip signals with no actionable confidence. This covers two cases:
            # (a) Zombie: both engines failed (confidence="None", agreement="None")
            # (b) Poisson-only grade-C or Bayesian-downgraded to None — untrackable noise.
            if ds.confidence == "None":
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

            # Under 3.5 odds cap: when best odds > 1.85 (< 54% prob),
            # bookmakers are pricing a high-scoring match -- drop the signal.
            if market == "Under 3.5":
                under35_cap = float(POISSON_RULES.get("under35_max_odds", 1.85))
                best_u35_odd = b.best_actual_odd if b else None
                if best_u35_odd is not None and best_u35_odd > under35_cap:
                    continue

            # League under-goals suppression.
            # MLS, A-League, Chinese Super League, Allsvenskan, Eliteserien,
            # and Iranian PGPL are structurally high-scoring leagues.
            # Under 2.5 and Under 3.5 signals from these leagues have poor
            # historical win-rates -- suppress them regardless of model output.
            if market in ("Under 2.5", "Under 3.5"):
                league_lower = (fixture.league or "").lower()
                if _league_matches_suppression(league_lower, UNDER_GOALS_SUPPRESSED_LEAGUES):
                    continue

            # League over-goals suppression.
            # Some leagues are structurally low-scoring — 0-0 and 1-0 dominate —
            # so even the lowest-bar Over picks (Over 0.5, Over 1.5) land as losers.
            # Suppress all Over-goals and team-total-over markets for these leagues.
            _OVER_MARKETS = {
                "Over 0.5", "Over 1.5", "Over 2.5", "Over 3.5",
                "Home Over 0.5", "Home Over 1.5",
                "Away Over 0.5", "Away Over 1.5",
            }
            if market in _OVER_MARKETS:
                league_lower = (fixture.league or "").lower()
                if _league_matches_suppression(league_lower, OVER_GOALS_SUPPRESSED_LEAGUES):
                    continue

            # Adaptive confidence downgrade.
            # If the (market, league_tier) slice has a performance_factor < 0.72 for
            # 25+ settled bets, this combination has historically underperformed.
            # Downgrade confidence by one tier so the accumulator generator sees it
            # as less reliable. The raw engine output is NOT changed — dual_agreement
            # still reflects what the models said, only dual_confidence is adjusted.
            league_tier = fixture.league_tier
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

            # Low-confidence quality gate.
            # After any downgrade: if final confidence is "Low" AND the league×market
            # factor is below 0.85 (i.e., this combo has a poor track record), skip
            # the signal entirely rather than surfacing a weak, underperforming pick.
            if final_confidence == "Low" and perf_weights is not None:
                lm_factor = perf_weights.factor_for_league_market(fixture_league, market)
                if lm_factor < 0.85:
                    continue

            # Low confidence in Tier 3 = near-zero edge in highest-variance context.
            # Backtest shows Low confidence overall at +0.6% ROI — not worth the risk
            # in leagues with structural unpredictability.
            if final_confidence == "Low" and (fixture.league_tier or 3) >= 3:
                continue

            # Performance-weighted stake sizing.
            # Multiply the engine's recommended stake by a combined factor derived
            # from (league, market) recency-adjusted performance × (confidence, market)
            # all-time performance. This means:
            #   • Proven combos (La Liga Away Over 0.5) → stake boosted up to 1.75×
            #   • Underperforming combos → stake reduced down to 0.50×
            #   • No data yet → neutral (1.0×, stake unchanged)
            adjusted_stake_pct = ds.recommended_stake_pct
            if perf_weights is not None and ds.recommended_stake_pct:
                multiplier = perf_weights.stake_multiplier(fixture_league, market, final_confidence)
                adjusted_stake_pct = round(
                    min(0.10, max(0.002, ds.recommended_stake_pct * multiplier)), 4
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

            sig = Signal(
                fixture_id=fixture.id,
                market=market,
                bayesian_prob=b.derived_prob if b else None,
                bayesian_edge=b.edge if b else None,
                bayesian_best_odd=b.best_actual_odd if b else None,
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
                # Populated even when this market's `p` is None, because contradictions
                # surface across markets (e.g. Over 2.5 from Bayesian vs Under from λ).
                poisson_mixed_signals=poi_result.mixed_signals or None,
                # dual_confidence uses the adaptive downgraded value when performance
                # history shows this (market, league_tier) is consistently unreliable.
                # dual_agreement always reflects raw engine output for transparency.
                dual_confidence=final_confidence,
                dual_agreement=ds.agreement,
                dual_quality_score=adjusted_quality_score,
                dual_recommended_stake_pct=adjusted_stake_pct,
                contradiction=ds.contradiction,
                odds_drift_pct=odds_drift,
            )
            pending_signals.append(sig)
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

    # Write all signals in a single batch commit.
    for sig in pending_signals:
        db.add(sig)
    await db.commit()

    return count

"""
bayesian.py — Bayesian bookmaker consensus probability engine.

Ported from FootBet/odds_engine.py. The core algorithm:
1. Aggregate best correct-score odds across bookmakers (loaded from market_snapshots)
2. Normalise raw implied probabilities (divide by overround) → true CS distribution
3. Derive market probabilities by summing matching scoreline probabilities
4. Compare against actual market odds → compute edge, Kelly stake, confidence

All thresholds come from config.py so they can be tuned without code changes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.core.config import (
    ACTIVE_MARKETS,
    ALLOWED_SCORELINES,
    BAYESIAN_EXTRA_MARKETS,
    MARKET_MIN_EDGE,
    MARKET_MIN_ODDS,
    MARKET_PROB_BOUNDS,
    MARKETS,
    POISSON_RULES,
    get_league_tier,
    get_settings,
)
from app.services.api_client import SHARP_BOOKMAKER_NAMES, TARGET_BOOKMAKER_NAMES

settings = get_settings()


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ScorelineProb:
    home: int
    away: int
    probability: float
    raw_implied: float


@dataclass
class BayesianResult:
    market: str
    derived_prob: float
    best_actual_odd: float
    best_bookmaker: str
    implied_prob: float
    edge: float
    kelly_pct: float
    is_value: bool
    confidence: str   # High / Medium / Low / N/A
    quality_score: float
    overround: float
    coverage: float
    bookmaker_count: int
    ev_pct: float
    # Reference price used for EV/edge/Kelly math.
    # When Pinnacle/Bet365 has odds: consensus_odd == best_actual_odd (sharp price).
    # Fallback (no sharp book): consensus_odd = second-best to guard against
    # stale soft-book outliers; is_outlier_odds = True when triggered.
    consensus_odd: float = 0.0
    is_outlier_odds: bool = False


@dataclass
class BayesianFixtureResult:
    fixture_id: int
    home_team: str
    away_team: str
    league: str
    country: str
    league_tier: int
    scoreline_probs: list[ScorelineProb]
    market_results: list[BayesianResult]
    overround: float
    coverage: float
    bookmakers_used: list[str]
    value_markets: list[BayesianResult] = field(default_factory=list)

    def __post_init__(self):
        self.value_markets = [m for m in self.market_results if m.is_value]


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_scoreline(s: str) -> Optional[tuple[int, int]]:
    nums = re.findall(r"\d+", str(s))
    if len(nums) >= 2:
        h, a = int(nums[0]), int(nums[1])
        if h <= 9 and a <= 9:
            return (h, a)
    return None


# ── Aggregation ───────────────────────────────────────────────────────────────

def _best_odds_per_scoreline(cs_by_bookie: dict[str, list[dict]]) -> dict[tuple[int, int], float]:
    best: dict[tuple[int, int], float] = {}
    for _, values in cs_by_bookie.items():
        for item in values:
            score_str = item.get("value", "")
            try:
                odd = float(item.get("odd", 0))
            except (ValueError, TypeError):
                continue
            if odd <= 1.0:
                continue
            parsed = _parse_scoreline(score_str)
            if parsed is None:
                continue
            if parsed not in best or odd > best[parsed]:
                best[parsed] = odd
    return best


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalize(odds_dict: dict[tuple[int, int], float]) -> tuple[list[ScorelineProb], float, float]:
    raw: dict[tuple[int, int], float] = {k: 1.0 / v for k, v in odds_dict.items() if v > 1.0}
    if not raw:
        return [], 0.0, 0.0

    overround = sum(raw.values())
    normalised = {k: v / overround for k, v in raw.items()}

    probs = [
        ScorelineProb(home=h, away=a, probability=p, raw_implied=raw[(h, a)])
        for (h, a), p in sorted(normalised.items(), key=lambda x: -x[1])
    ]
    cs_overround_factor = float(POISSON_RULES.get("cs_overround_factor", 1.45))
    return probs, overround, min(1.0, overround / cs_overround_factor)


# ── Market derivation ─────────────────────────────────────────────────────────

def _derive_markets(probs: list[ScorelineProb]) -> dict[str, float]:
    results: dict[str, float] = {}
    for name, condition in MARKETS.items():
        results[name] = round(sum(sp.probability for sp in probs if condition(sp.home, sp.away)), 6)
    return results


# ── Value calculation ─────────────────────────────────────────────────────────

def _kelly(prob: float, odds: float, fraction: float, cap: float) -> float:
    b = odds - 1.0
    if b <= 0:
        return 0.0
    full = (b * prob - (1.0 - prob)) / b
    return min(max(0.0, full * fraction), cap)


def _confidence(edge: float, prob: float) -> str:
    if edge >= 0.07 and prob >= 0.60:
        return "High"
    elif edge >= 0.05 or prob >= 0.55:
        return "Medium"
    else:
        return "Low"


def _quality_score(ev_pct: float, prob: float, tier: int, n_bookies: int, confidence: str) -> float:
    # ev_pct is a percentage (e.g. 15.0 for 15% edge) — convert to fraction before
    # multiplying so quality_score stays in [0, 1] range.
    ev = ev_pct / 100.0
    tf = {1: 1.0, 2: 0.85, 3: 0.70}.get(tier, 0.70)
    bf = min(n_bookies / 2.0, 1.0)  # 2 bookies = full score (Bet365 + 1xBet)
    cf = {"High": 1.0, "Medium": 0.85, "Low": 0.70}.get(confidence, 0.70)
    return round(ev * prob * tf * bf * cf, 4)


# ── Reference pricing ────────────────────────────────────────────────────────
# Sharp bookmakers (Pinnacle, Bet365) have the lowest overround in the market
# (~2-5%) and are used as the gold-standard reference for EV/edge/Kelly math.
#
# Priority when computing the reference (consensus) price:
#   1. Best price among sharp bookmakers (Pinnacle first, Bet365 second)
#   2. If no sharp book has this market: outlier-filtered second-best from all
#
# The "best available" odds (potentially from a soft book like 10Bet) are still
# stored and displayed so users can take advantage of better prices elsewhere,
# but edge/EV reflect the sharp-book fair value rather than an inflated line.
# Outlier factor is read from settings so it can be tuned via .env without code changes.
# Default: 1.35 (35% above reference → flag as outlier); see bayesian_outlier_factor in config.py.


# ── Best actual odd lookup ─────────────────────────────────────────────────────

def _best_odd_for_market(
    market_name: str,
    goals_ou: dict[str, dict[str, float]],
    btts: dict[str, dict[str, float]],
    match_winner: dict[str, dict[str, float]],
    double_chance: dict[str, dict[str, float]],
    home_totals: dict[str, dict[str, float]] | None = None,
    away_totals: dict[str, dict[str, float]] | None = None,
    win_to_nil_home: dict[str, dict[str, float]] | None = None,
    win_to_nil_away: dict[str, dict[str, float]] | None = None,
    exact_goals: dict[str, dict[str, float]] | None = None,
) -> tuple[float, str, float, bool]:
    """
    Returns (display_odd, display_bookmaker, reference_odd, is_outlier_odds).

    Reference price (EV/edge/Kelly math) always comes from the sharpest available
    bookmaker (Pinnacle → Bet365 → outlier-filtered second-best).

    Display price (shown to the user) comes from TARGET_BOOKMAKER_NAMES (e.g. Betway)
    when they have odds for this market, falling back to the sharp price otherwise.
    This lets the user see exactly what they'll get at their bookmaker while keeping
    EV calculations anchored to an efficient market.

    Priority 1 — sharp book available (Pinnacle / Bet365):
      reference_odd = sharp best price; is_outlier = False.
      display_odd   = target book price if available, else sharp price.

    Priority 2 — no sharp book for this market (soft book only):
      display_odd = best available; reference_odd = second-best (outlier guard);
      is_outlier = True when best is 35%+ above second-best.
    """
    ou_map = {
        "Over 0.5": ["Over 0.5"],   "Over 1.5": ["Over 1.5"],
        "Over 2.5": ["Over 2.5"],   "Over 3.5": ["Over 3.5"],
        "Under 1.5": ["Under 1.5"], "Under 2.5": ["Under 2.5"],
        "Under 3.5": ["Under 3.5"],
    }
    btts_map  = {"BTTS Yes": ["Yes", "GG"], "BTTS No": ["No", "NG"]}
    mw_map    = {"Home Win": ["Home Win"], "Draw": ["Draw"], "Away Win": ["Away Win"]}
    dc_map    = {
        "1X (Home or Draw)": ["1X (Home or Draw)", "1X", "Home/Draw"],
        "X2 (Draw or Away)": ["X2 (Draw or Away)", "X2", "Draw/Away"],
        "12 (Home or Away)": ["12 (Home or Away)", "12", "Home/Away"],
    }
    home_ou_map = {
        "Home Over 0.5": ["Over 0.5"],  "Home Under 0.5": ["Under 0.5"],
        "Home Over 1.5": ["Over 1.5"],  "Home Under 1.5": ["Under 1.5"],
        "Home Over 2.5": ["Over 2.5"],  "Home Under 2.5": ["Under 2.5"],
    }
    away_ou_map = {
        "Away Over 0.5": ["Over 0.5"],  "Away Under 0.5": ["Under 0.5"],
        "Away Over 1.5": ["Over 1.5"],  "Away Under 1.5": ["Under 1.5"],
        "Away Over 2.5": ["Over 2.5"],  "Away Under 2.5": ["Under 2.5"],
    }
    # Win to Nil: "Yes" = team wins clean, "No" = anything else
    wtn_home_map = {"Home Win to Nil": ["Yes"], "Home Not Win to Nil": ["No"]}
    wtn_away_map = {"Away Win to Nil": ["Yes"], "Away Not Win to Nil": ["No"]}
    # Exact Goals: selection_name in DB is the digit string ("1", "2", "3") or "" for 0
    exact_map = {
        "Exactly 1 Goal":  ["1"],
        "Exactly 2 Goals": ["2"],
        "Exactly 3 Goals": ["3"],
    }

    best_odd, best_bookie = 0.0, ""
    best_sharp_odd, best_sharp_bookie = 0.0, ""
    best_target_odd, best_target_bookie = 0.0, ""   # target (e.g. Betway) for display
    all_valid_odds: list[float] = []   # all prices > 1.0, for fallback outlier detection

    def _search(source: dict | None, key_map: dict):
        nonlocal best_odd, best_bookie, best_sharp_odd, best_sharp_bookie
        nonlocal best_target_odd, best_target_bookie
        if not source:
            return
        for label in key_map.get(market_name, []):
            for bookie, odds_dict in source.items():
                odd = odds_dict.get(label, 0.0)
                if odd > 1.0:
                    all_valid_odds.append(odd)
                    if bookie in SHARP_BOOKMAKER_NAMES and odd > best_sharp_odd:
                        best_sharp_odd, best_sharp_bookie = odd, bookie
                    if bookie in TARGET_BOOKMAKER_NAMES and odd > best_target_odd:
                        best_target_odd, best_target_bookie = odd, bookie
                if odd > best_odd:
                    best_odd, best_bookie = odd, bookie

    _search(goals_ou, ou_map)
    _search(btts, btts_map)
    _search(match_winner, mw_map)
    _search(double_chance, dc_map)
    _search(home_totals, home_ou_map)
    _search(away_totals, away_ou_map)
    _search(win_to_nil_home, wtn_home_map)
    _search(win_to_nil_away, wtn_away_map)
    _search(exact_goals, exact_map)

    if best_sharp_odd > 1.0:
        # Sharp book (Pinnacle / Bet365) is the EV reference — always.
        # Display the target bookmaker price (e.g. Betway) if available so the user
        # sees what they'll actually get; fall back to the sharp price otherwise.
        if best_target_odd > 1.0:
            return best_target_odd, best_target_bookie, best_sharp_odd, False
        return best_sharp_odd, best_sharp_bookie, best_sharp_odd, False
    elif len(all_valid_odds) >= 2:
        # No sharp book for this market — use best available with outlier guard.
        sorted_desc = sorted(all_valid_odds, reverse=True)
        second_best = sorted_desc[1]
        is_outlier = best_odd > second_best * settings.bayesian_outlier_factor
        reference_odd = second_best if is_outlier else best_odd
        return best_odd, best_bookie, reference_odd, is_outlier
    else:
        return best_odd, best_bookie, best_odd, False


# ── No-vig edge helpers ───────────────────────────────────────────────────────

_OPPOSITE_MARKET: dict[str, str] = {
    # Full-game totals
    "Over 0.5":  "Under 0.5",  "Over 1.5":  "Under 1.5",
    "Over 2.5":  "Under 2.5",  "Over 3.5":  "Under 3.5",
    "Under 1.5": "Over 1.5",   "Under 2.5": "Over 2.5",
    "Under 3.5": "Over 3.5",
    # BTTS
    "BTTS Yes":  "BTTS No",    "BTTS No":   "BTTS Yes",
    # Team totals — two-way pairs
    "Home Over 0.5":  "Home Under 0.5",  "Home Under 0.5": "Home Over 0.5",
    "Home Over 1.5":  "Home Under 1.5",  "Home Under 1.5": "Home Over 1.5",
    "Away Over 0.5":  "Away Under 0.5",  "Away Under 0.5": "Away Over 0.5",
    "Away Over 1.5":  "Away Under 1.5",  "Away Under 1.5": "Away Over 1.5",
    # Win to Nil — Yes/No is a two-way market
    "Home Win to Nil": "Home Not Win to Nil",
    "Away Win to Nil": "Away Not Win to Nil",
}


_THREE_WAY_WINNER = frozenset({"Home Win", "Draw", "Away Win"})


def _shin_no_vig_3way(
    market_name: str,
    goals_ou: dict,
    btts: dict,
    match_winner: dict,
    double_chance: dict,
    home_totals: dict | None = None,
    away_totals: dict | None = None,
    win_to_nil_home: dict | None = None,
    win_to_nil_away: dict | None = None,
    best_odd_fallback: float = 1.0,
) -> float | None:
    """
    Proportional (Shin-style) no-vig for 3-way Match Winner markets.
    Collects the best available odd for each of the three sides, converts to
    implied probabilities, normalises by the overround, and returns the
    no-vig probability for ``market_name``.
    Returns None if odds for all three sides are not available.
    """
    raw: dict[str, float] = {}
    for side in _THREE_WAY_WINNER:
        odd, _, _, _ = _best_odd_for_market(
            side, goals_ou, btts, match_winner, double_chance,
            home_totals=home_totals, away_totals=away_totals,
            win_to_nil_home=win_to_nil_home, win_to_nil_away=win_to_nil_away,
        )
        if odd > 1.0:
            raw[side] = 1.0 / odd
    if len(raw) < 3:
        return None
    total = sum(raw.values())          # overround (typically 1.05–1.10)
    if total <= 0:
        return None
    return raw[market_name] / total    # normalised fair probability


def _no_vig_prob(
    market_name: str,
    best_odd: float,
    goals_ou: dict,
    btts: dict,
    match_winner: dict,
    double_chance: dict,
    home_totals: dict | None = None,
    away_totals: dict | None = None,
    win_to_nil_home: dict | None = None,
    win_to_nil_away: dict | None = None,
) -> float:
    """
    Two-way no-vig implied probability.
    Strips bookmaker margin by normalising both sides: p = back / (back + lay).
    For three-way Match Winner markets uses proportional (Shin-style) normalisation
    across all three sides. Falls back to raw implied (1/best_odd) when
    opposite-side odds are unavailable.
    """
    # ── Three-way markets: proportional no-vig across all three sides ─────────
    if market_name in _THREE_WAY_WINNER:
        result = _shin_no_vig_3way(
            market_name, goals_ou, btts, match_winner, double_chance,
            home_totals=home_totals, away_totals=away_totals,
            win_to_nil_home=win_to_nil_home, win_to_nil_away=win_to_nil_away,
            best_odd_fallback=best_odd,
        )
        if result is not None:
            return result
        return 1.0 / best_odd          # fallback if any side's odds missing

    dc_opp_map = {
        "1X (Home or Draw)": "Away Win",
        "X2 (Draw or Away)": "Home Win",
        "12 (Home or Away)": "Draw",
    }
    dc_opposite = dc_opp_map.get(market_name)
    if dc_opposite:
        opp_odd, _, _ref, _out = _best_odd_for_market(
            dc_opposite, goals_ou, btts, match_winner, double_chance,
            home_totals=home_totals, away_totals=away_totals,
            win_to_nil_home=win_to_nil_home, win_to_nil_away=win_to_nil_away,
        )
        if opp_odd > 1.0:
            raw_back = 1.0 / best_odd
            raw_opp = 1.0 / opp_odd
            return raw_back / (raw_back + raw_opp)

    opposite = _OPPOSITE_MARKET.get(market_name)
    if not opposite:
        return 1.0 / best_odd
    opp_odd, _, _ref, _out = _best_odd_for_market(
        opposite, goals_ou, btts, match_winner, double_chance,
        home_totals=home_totals, away_totals=away_totals,
        win_to_nil_home=win_to_nil_home, win_to_nil_away=win_to_nil_away,
    )
    if opp_odd <= 1.0:
        return 1.0 / best_odd
    raw_back = 1.0 / best_odd
    raw_opp  = 1.0 / opp_odd
    return raw_back / (raw_back + raw_opp)


# ── Full pipeline ─────────────────────────────────────────────────────────────

def analyse_fixture(
    fixture_id: int,
    home_team: str,
    away_team: str,
    league: str,
    country: str,
    cs_by_bookie: dict[str, list[dict]],
    goals_ou: dict[str, dict[str, float]],
    btts: dict[str, dict[str, float]],
    match_winner: dict[str, dict[str, float]],
    double_chance: dict[str, dict[str, float]] | None = None,
    home_totals: dict[str, dict[str, float]] | None = None,
    away_totals: dict[str, dict[str, float]] | None = None,
    win_to_nil_home: dict[str, dict[str, float]] | None = None,
    win_to_nil_away: dict[str, dict[str, float]] | None = None,
    exact_goals: dict[str, dict[str, float]] | None = None,
    all_markets: bool = False,
) -> Optional[BayesianFixtureResult]:
    """
    Run the full Bayesian analysis pipeline for one fixture.
    cs_by_bookie: {bookmaker_name: [{value: "1:0", odd: "6.50"}, ...]}
    goals_ou: {bookmaker_name: {"Over 2.5": 1.85, ...}}
    btts: {bookmaker_name: {"Yes": 1.70, ...}}
    match_winner: {bookmaker_name: {"Home Win": 2.10, ...}}
    double_chance: {bookmaker_name: {"1X (Home or Draw)": 1.25, ...}}
    home_totals: {bookmaker_name: {"Over 0.5": 1.08, "Over 1.5": 1.55, ...}}
    away_totals: {bookmaker_name: {"Over 0.5": 1.15, "Over 1.5": 1.72, ...}}
    win_to_nil_home: {bookmaker_name: {"Yes": 3.50, "No": 1.25}}
    win_to_nil_away: {bookmaker_name: {"Yes": 5.00, "No": 1.10}}
    exact_goals: {bookmaker_name: {"1": 4.50, "2": 3.75, "3": 4.20, ...}}
    """
    if not cs_by_bookie:
        return None

    n_bookies = len(cs_by_bookie)
    if n_bookies < settings.min_bookmakers:
        return None

    odds_dict = _best_odds_per_scoreline(cs_by_bookie)
    odds_dict = {k: v for k, v in odds_dict.items() if k in ALLOWED_SCORELINES}

    if len(odds_dict) < 5:
        return None

    probs, overround, coverage = _normalize(odds_dict)
    if coverage < settings.min_coverage_threshold or overround == 0:
        return None

    derived = _derive_markets(probs)
    tier = get_league_tier(league, country)

    markets_to_eval = (ACTIVE_MARKETS | BAYESIAN_EXTRA_MARKETS) if all_markets else ACTIVE_MARKETS

    results: list[BayesianResult] = []
    for market_name, derived_prob in derived.items():
        if market_name not in markets_to_eval:
            continue
        if derived_prob < 0.01:
            continue

        bounds = MARKET_PROB_BOUNDS.get(market_name)
        if bounds and not (bounds[0] <= derived_prob <= bounds[1]):
            continue

        best_odd, best_bookie, consensus_odd, is_outlier_odds = _best_odd_for_market(
            market_name, goals_ou, btts, match_winner, double_chance or {},
            home_totals=home_totals, away_totals=away_totals,
            win_to_nil_home=win_to_nil_home, win_to_nil_away=win_to_nil_away,
            exact_goals=exact_goals,
        )

        if best_odd <= 1.0:
            results.append(BayesianResult(
                market=market_name, derived_prob=derived_prob,
                best_actual_odd=0.0, best_bookmaker="N/A",
                implied_prob=0.0, edge=0.0, kelly_pct=0.0,
                is_value=False, confidence="N/A", quality_score=0.0,
                overround=overround, coverage=coverage, bookmaker_count=n_bookies, ev_pct=0.0,
                consensus_odd=0.0, is_outlier_odds=False,
            ))
            continue

        min_market_odd = MARKET_MIN_ODDS.get(market_name)
        if min_market_odd is not None and best_odd < min_market_odd:
            continue

        # When a sharp book (Pinnacle/Bet365) is available, best_odd IS the sharp
        # price and is_outlier_odds is False — effective_odd == best_odd.
        # Fallback path (no sharp book): effective_odd uses the outlier-filtered
        # second-best to guard against stale soft-book lines.
        effective_odd = consensus_odd if (is_outlier_odds and consensus_odd > 1.0) else best_odd

        no_vig = _no_vig_prob(
            market_name, effective_odd, goals_ou, btts, match_winner, double_chance or {},
            home_totals=home_totals, away_totals=away_totals,
            win_to_nil_home=win_to_nil_home, win_to_nil_away=win_to_nil_away,
        )
        edge = derived_prob - no_vig
        implied_prob = no_vig   # vig-free reference probability
        market_edge_floor = MARKET_MIN_EDGE.get(market_name, settings.min_value_edge)
        is_value = edge >= market_edge_floor and derived_prob >= settings.min_derived_prob
        ks = _kelly(derived_prob, effective_odd, settings.kelly_fraction, settings.max_kelly_pct) if edge > 0 else 0.0
        conf = _confidence(edge, derived_prob) if is_value else "Low"
        ev_pct = round((derived_prob * effective_odd - 1.0) * 100, 2)
        # Continuous quality: sub-threshold signals get a proportional fraction (max 0.5×)
        # so borderline picks can still surface as accumulator candidates at reduced weight.
        # is_value still gates staking and confidence — this only affects quality_score.
        if is_value:
            edge_scale = 1.0
        elif edge > 0:
            edge_scale = min(0.5, edge / market_edge_floor * 0.5)
        else:
            edge_scale = 0.0
        qs = round(_quality_score(ev_pct, derived_prob, tier, n_bookies, conf) * edge_scale, 4)

        results.append(BayesianResult(
            market=market_name, derived_prob=derived_prob,
            best_actual_odd=best_odd, best_bookmaker=best_bookie,
            implied_prob=implied_prob, edge=edge, kelly_pct=ks,
            is_value=is_value, confidence=conf, quality_score=qs,
            overround=overround, coverage=coverage, bookmaker_count=n_bookies, ev_pct=ev_pct,
            consensus_odd=consensus_odd, is_outlier_odds=is_outlier_odds,
        ))

    results.sort(key=lambda m: (-int(m.is_value), -m.edge))

    return BayesianFixtureResult(
        fixture_id=fixture_id,
        home_team=home_team, away_team=away_team,
        league=league, country=country, league_tier=tier,
        scoreline_probs=probs, market_results=results,
        overround=overround, coverage=coverage,
        bookmakers_used=list(cs_by_bookie.keys()),
    )

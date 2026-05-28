from __future__ import annotations

import math
from datetime import date
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.accumulator_generator import (
    _load_candidates,
    LOW_WIN_PROB_THRESHOLD,
    VERY_LOW_WIN_PROB_THRESHOLD,
)

# Market pairs that share an underlying systematic factor across different fixtures.
# Kept in sync with accumulator_generator._CORRELATED_PAIRS.
CORRELATED_PAIRS = {
    # High-scoring factor
    frozenset({"Over 2.5", "BTTS Yes"}),
    frozenset({"Over 3.5", "BTTS Yes"}),
    frozenset({"Over 1.5", "BTTS Yes"}),
    frozenset({"Over 0.5", "BTTS Yes"}),
    frozenset({"Over 2.5", "Over 3.5"}),
    frozenset({"Over 1.5", "Over 2.5"}),
    frozenset({"Over 1.5", "Over 3.5"}),
    # Low-scoring factor
    frozenset({"Under 2.5", "Under 3.5"}),
    frozenset({"Under 1.5", "Under 2.5"}),
    # Team-scoring systematic exposure
    frozenset({"Home Over 0.5", "Home Over 1.5"}),
    frozenset({"Away Over 0.5", "Away Over 1.5"}),
    frozenset({"Home Over 1.5", "BTTS Yes"}),
    frozenset({"Away Over 1.5", "BTTS Yes"}),
}

# Goals-type markets — used to build the Goals ACCA sub-ticket in Pro.
GOALS_MARKETS: frozenset = frozenset({
    "Over 0.5", "Over 1.5", "Over 2.5", "Over 3.5",
    "Home Over 0.5", "Home Over 1.5",
    "Away Over 0.5", "Away Over 1.5",
    "BTTS Yes",
})

# Number of randomly selected matches for the Free ticket.
FREE_PICK_COUNT = 3

# Safe Ticket selection constraints: 3-4 legs, combined odds 8-25x, leg odds 1.20-5.00.
_SAFE_LEG_MIN = 1.20
_SAFE_LEG_MAX = 5.00
_SAFE_ODDS_MIN = 8.0
_SAFE_ODDS_MAX = 25.0
_SAFE_MIN_LEGS = 3
_SAFE_MAX_LEGS = 4

# Smart ACCA selection constraints
_ACCA_MAX_PER_LEAGUE  = 2   # max legs from the same league in any smart sub-ticket
_HIGH_CONF_MIN_LEGS   = 3   # minimum legs to emit a High Confidence ACCA
_HIGH_CONF_MAX_LEGS   = 6   # maximum legs in High Confidence ACCA
_GOALS_MIN_LEGS       = 3   # minimum legs to emit a Goals ACCA
_GOALS_MAX_LEGS       = 5   # maximum legs in Goals ACCA
_GOALS_MAX_SAME_MKT   = 2   # max legs with the identical market in Goals ACCA
_BEST_SINGLES_COUNT   = 5   # number of singles to select
_SHARP_DRIFT_THRESHOLD = -5.0  # odds_drift_pct <= this triggers "sharp money" alert


def _has_correlated_pair(legs: list[dict]) -> bool:
    markets = [leg["market"] for leg in legs]
    for idx, market in enumerate(markets):
        for other in markets[idx + 1:]:
            if frozenset({market, other}) in CORRELATED_PAIRS:
                return True
    return False


def _combo_prob(legs: list[dict]) -> float | None:
    if not legs:
        return None
    prob = 1.0
    for leg in legs:
        leg_prob = leg.get("probability")
        if leg_prob is None:
            leg_prob = 1.0 / leg["odds"]
        prob *= leg_prob * 0.95
    return round(prob, 4)


def _ticket_kelly_pct(combined_odds: float | None, win_prob: float | None) -> float | None:
    """
    Full-Kelly fraction for the whole ticket as a percentage of bankroll.

    f = (b × P − Q) / b   where b = combined_odds − 1, P = win_prob, Q = 1 − P

    Returns None when inputs are unavailable or the bet has negative expected value
    (negative Kelly → no bet). Expressed as a fraction in [0, 1]; the UI multiplies
    by 100 to display as a percentage.
    """
    if combined_odds is None or win_prob is None or combined_odds <= 1.0 or win_prob <= 0.0:
        return None
    b = combined_odds - 1.0
    kelly = (b * win_prob - (1.0 - win_prob)) / b
    return round(max(0.0, kelly), 4)


def _candidate_to_leg(c: dict) -> dict:
    return {
        "signal_id":             c.get("signal_id", 0),
        "fixture_id":            c.get("fixture_id", 0),
        "match_name":            c.get("match_name", ""),
        "home_team":             c.get("home_team", ""),
        "away_team":             c.get("away_team", ""),
        "league":                c.get("league"),
        "league_tier":           c.get("league_tier"),
        "kickoff_at":            c.get("kickoff_at"),
        "event_date":            c.get("event_date"),
        "market":                c.get("market", ""),
        "selection_name":        c.get("market", ""),
        "bookmaker":             c.get("bookmaker", "Manual"),
        "odds":                  c.get("odds", 1.0),
        "probability":           max(c.get("bayesian_prob") or 0.0, c.get("poisson_prob") or 0.0) or None,
        "ev_pct":                None,
        "confidence":            c.get("confidence"),
        "agreement":             c.get("agreement"),
        "recommended_stake_pct": c.get("recommended_stake_pct"),
        "source_rule_key":       c.get("source_rule_key"),
        "signal_grade":          None,
        "odds_drift_pct":        c.get("odds_drift_pct"),
        "why_tags":              [],
    }


def _sub_ticket(
    key: str,
    label: str,
    description: str,
    legs: list[dict],
    is_singles: bool = False,
) -> dict:
    if not legs:
        return {
            "key": key, "label": label, "description": description,
            "legs": [], "combined_odds": None, "win_probability_estimate": None,
            "low_win_prob_warning": False, "very_low_win_prob": False,
            "kelly_stake_pct": None,
            "summary_tags": [], "empty_reason": "No qualifying signals for this ticket.",
            "is_singles": is_singles,
        }

    combined = round(math.prod(leg["odds"] for leg in legs), 2) if not is_singles else None
    win_prob = _combo_prob(legs) if not is_singles and len(legs) >= 2 else None
    kelly_pct = _ticket_kelly_pct(combined, win_prob)

    tags: list[str] = []
    high_count = sum(1 for l in legs if l.get("confidence") == "High")
    both_count = sum(1 for l in legs if l.get("agreement") == "Both")
    if high_count == len(legs):
        tags.append("all high confidence")
    elif high_count >= len(legs) // 2:
        tags.append("high confidence core")
    if both_count == len(legs):
        tags.append("all legs: both engines agree")
    elif both_count >= max(1, len(legs) // 2):
        tags.append("majority: both engines agree")

    return {
        "key": key,
        "label": label,
        "description": description,
        "legs": [_candidate_to_leg(l) for l in legs],
        "combined_odds": combined,
        "win_probability_estimate": win_prob,
        "low_win_prob_warning": win_prob is not None and win_prob < LOW_WIN_PROB_THRESHOLD,
        "very_low_win_prob": win_prob is not None and win_prob < VERY_LOW_WIN_PROB_THRESHOLD,
        "kelly_stake_pct": kelly_pct,
        "summary_tags": tags[:3],
        "empty_reason": None,
        "is_singles": is_singles,
    }


def _build_safe_ticket_legs(candidates: list[dict]) -> list[dict]:
    """Select 3-4 uncorrelated legs targeting 8-25x combined odds."""
    pool = [
        c for c in candidates
        if c.get("odds") is not None and _SAFE_LEG_MIN <= c["odds"] <= _SAFE_LEG_MAX
    ]
    selected: list[dict] = []
    fixture_ids: set[int] = set()

    for leg in pool:
        if leg["fixture_id"] in fixture_ids:
            continue
        candidate = [*selected, leg]
        if _has_correlated_pair(candidate):
            continue
        selected.append(leg)
        fixture_ids.add(leg["fixture_id"])
        combined_odds = math.prod(item["odds"] for item in selected)
        if len(selected) >= _SAFE_MIN_LEGS and combined_odds >= _SAFE_ODDS_MIN:
            break
        if len(selected) >= _SAFE_MAX_LEGS:
            break

    while len(selected) > _SAFE_MIN_LEGS and math.prod(item["odds"] for item in selected) > _SAFE_ODDS_MAX:
        selected.pop()

    if len(selected) < _SAFE_MIN_LEGS:
        return []
    if math.prod(item["odds"] for item in selected) < _SAFE_ODDS_MIN:
        return []
    return selected


def _leg_composite_score(c: dict) -> float:
    """
    Composite leg score for smart ACCA construction.

    Blends the pre-computed adjusted_quality (performance-weighted quality score
    from _load_candidates) with:
      - Kelly proxy — fractional Kelly fraction, clipped to [0, 1].
        Rewards legs where the model sees genuine positive expected value
        relative to the offered odds. Blended at 30% weight so quality
        is dominant (70%) and Kelly just adjusts the ranking.
      - Agreement bonus — "Both" engines agree → ×1.10 uplift
      - League tier bonus — Tier 1 (liquid, sharp) → ×1.05; Tier 3+ → ×0.95
      - Odds drift — sharp money shortening the line → ×1.05;
                     line lengthening (public money) → ×0.95
    """
    quality = c.get("quality_score") or 0.0

    # Kelly proxy using best available probability estimate
    p = max(c.get("bayesian_prob") or 0.0, c.get("poisson_prob") or 0.0)
    odds = c.get("odds") or 1.0
    b = odds - 1.0
    if b > 0 and p > 0:
        kelly = max(0.0, min(1.0, ((b * p) - (1.0 - p)) / b))
    else:
        kelly = 0.0
    # quality is 70% weight, Kelly adjusts the remaining 30%
    kelly_factor = 0.70 + 0.30 * kelly

    # Agreement bonus
    agree_mult = 1.10 if (c.get("agreement") or "") == "Both" else 1.00

    # League tier bonus/penalty
    tier = c.get("league_tier") or 2
    tier_mult = 1.05 if tier == 1 else (0.95 if tier >= 3 else 1.00)

    # Odds drift: negative = sharp money confirming our position
    drift = c.get("odds_drift_pct") or 0.0
    drift_mult = 1.05 if drift <= -3.0 else (0.95 if drift >= 3.0 else 1.00)

    return quality * kelly_factor * agree_mult * tier_mult * drift_mult


def _smart_acca_legs(
    candidates: list[dict],
    filter_fn: Callable[[dict], bool] | None = None,
    min_legs: int = 3,
    max_legs: int = 6,
    max_per_league: int = _ACCA_MAX_PER_LEAGUE,
    max_same_market: int | None = None,
    exclude_fixtures: set[int] | None = None,
) -> list[dict]:
    """
    Greedy smart ACCA leg builder.

    Scores every eligible candidate with _leg_composite_score, then greedily
    adds the highest-scoring leg that satisfies all constraints:
      - Optional filter_fn pre-filter (e.g. confidence == "High")
      - Optional exclude_fixtures set — fixtures already claimed by a prior
        sub-ticket are skipped, ensuring each sub-ticket covers different games
      - Per-fixture uniqueness (candidates are already deduplicated by
        _load_candidates, but enforced here for safety)
      - Per-league cap (max_per_league) — avoids correlated league exposure
      - Same-market diversity cap (max_same_market) — avoids e.g. 5× "BTTS Yes"
      - Correlated market pair exclusion (CORRELATED_PAIRS)

    Returns empty list if fewer than min_legs legs can be selected, so the
    caller can fall back to the original behaviour.
    """
    pool = [c for c in candidates if filter_fn is None or filter_fn(c)]
    pool.sort(key=_leg_composite_score, reverse=True)

    selected: list[dict] = []
    fixture_ids: set[int] = set(exclude_fixtures or ())
    league_counts: dict[str, int] = {}
    market_counts: dict[str, int] = {}

    for leg in pool:
        if len(selected) >= max_legs:
            break

        fid = leg.get("fixture_id", 0)
        league = leg.get("league") or ""
        market = leg.get("market") or ""

        if fid in fixture_ids:
            continue
        if league_counts.get(league, 0) >= max_per_league:
            continue
        if max_same_market is not None and market_counts.get(market, 0) >= max_same_market:
            continue
        if _has_correlated_pair([*selected, leg]):
            continue

        selected.append(leg)
        fixture_ids.add(fid)
        league_counts[league] = league_counts.get(league, 0) + 1
        market_counts[market] = market_counts.get(market, 0) + 1

    if len(selected) < min_legs:
        return []
    return selected


def _build_high_conf_acca_legs(
    candidates: list[dict],
    exclude_fixtures: set[int] | None = None,
) -> list[dict]:
    """
    Smart High Confidence ACCA: 3-6 legs, High confidence only.

    Uses composite scoring (quality × Kelly × agreement × tier × drift).
    Caps at 2 legs per league to avoid correlated fixtures.
    Skips any fixture IDs in exclude_fixtures (sub-ticket exclusivity).
    Falls back to the full High-confidence candidate list if smart
    selection cannot produce the minimum 3 legs.
    """
    legs = _smart_acca_legs(
        candidates,
        filter_fn=lambda c: c.get("confidence") == "High",
        min_legs=_HIGH_CONF_MIN_LEGS,
        max_legs=_HIGH_CONF_MAX_LEGS,
        max_per_league=_ACCA_MAX_PER_LEAGUE,
        exclude_fixtures=exclude_fixtures,
    )
    if legs:
        return legs
    # Fallback: all High confidence candidates (original behaviour)
    excl = exclude_fixtures or set()
    return [c for c in candidates if c.get("confidence") == "High" and c.get("fixture_id", 0) not in excl]


def _build_goals_acca_legs(
    candidates: list[dict],
    exclude_fixtures: set[int] | None = None,
) -> list[dict]:
    """
    Smart Goals ACCA: 3-5 legs from goals-type markets only.

    Limits repetition of the same market (max 2 identical markets, e.g.
    no more than 2 "BTTS Yes" legs) and caps at 2 legs per league to
    encourage fixture diversity.
    Skips any fixture IDs in exclude_fixtures (sub-ticket exclusivity).
    Falls back to the full goals-market candidate list if smart
    selection cannot produce the minimum 3 legs.
    """
    legs = _smart_acca_legs(
        candidates,
        filter_fn=lambda c: c.get("market") in GOALS_MARKETS,
        min_legs=_GOALS_MIN_LEGS,
        max_legs=_GOALS_MAX_LEGS,
        max_per_league=_ACCA_MAX_PER_LEAGUE,
        max_same_market=_GOALS_MAX_SAME_MKT,
        exclude_fixtures=exclude_fixtures,
    )
    if legs:
        return legs
    # Fallback: all goals-market candidates (original behaviour)
    excl = exclude_fixtures or set()
    return [c for c in candidates if c.get("market") in GOALS_MARKETS and c.get("fixture_id", 0) not in excl]


def _build_best_singles_legs(
    candidates: list[dict],
    count: int = _BEST_SINGLES_COUNT,
    exclude_fixtures: set[int] | None = None,
) -> list[dict]:
    """
    EV-weighted Best Singles: top-N legs ranked by a 50/50 blend of
    Expected Value and composite quality.

      EV = p × odds - 1      (positive = value bet, negative = against value)
      composite = _leg_composite_score(c)
      final_score = 0.5 × EV + 0.5 × composite

    Skips fixtures already claimed by High Conf or Goals ACCA so the
    singles list surfaces genuinely different picks from the acca sub-tickets.
    """
    excl = exclude_fixtures or set()

    def _singles_score(c: dict) -> float:
        p = max(c.get("bayesian_prob") or 0.0, c.get("poisson_prob") or 0.0)
        odds_val = c.get("odds") or 1.0
        ev = p * odds_val - 1.0
        return 0.5 * ev + 0.5 * _leg_composite_score(c)

    pool = [c for c in candidates if c.get("fixture_id", 0) not in excl]
    scored = sorted(pool, key=_singles_score, reverse=True)
    return scored[:count]


def _build_sharp_moves_legs(candidates: list[dict]) -> list[dict]:
    """
    Sharp Money alerts: signals where the odds have shortened by at least
    _SHARP_DRIFT_THRESHOLD% since the opening line (negative = shortening).

    These are the day's highest-conviction picks — the market itself is
    confirming the model's position by moving money in. Even a single
    qualifying signal is worth showing as a standalone alert.

    Sorted by most negative drift first (strongest line movement at the top).
    Capped at 5 to keep the sub-ticket focused.
    """
    sharp = [
        c for c in candidates
        if c.get("odds_drift_pct") is not None
        and c["odds_drift_pct"] <= _SHARP_DRIFT_THRESHOLD
    ]
    sharp.sort(key=lambda c: c.get("odds_drift_pct") or 0.0)  # most negative first
    return sharp[:5]


def _build_general_ticket(candidates: list[dict]) -> dict:
    """All signal candidates for the day — tracking is optional."""
    if not candidates:
        return {
            "key": "general", "label": "TiTiBet General",
            "description": "All signal matches for today",
            "legs": [], "combined_odds": None, "win_probability_estimate": None,
            "low_win_prob_warning": False, "very_low_win_prob": False,
            "empty_reason": "No qualifying signals for today.",
        }
    legs = [_candidate_to_leg(c) for c in candidates]
    combined = round(math.prod(leg["odds"] for leg in legs), 2)
    win_prob = _combo_prob(legs) if len(legs) >= 2 else None
    return {
        "key": "general", "label": "TiTiBet General",
        "description": f"All {len(legs)} signal matches for today",
        "legs": legs,
        "combined_odds": combined,
        "win_probability_estimate": win_prob,
        "low_win_prob_warning": win_prob is not None and win_prob < LOW_WIN_PROB_THRESHOLD,
        "very_low_win_prob": win_prob is not None and win_prob < VERY_LOW_WIN_PROB_THRESHOLD,
        "empty_reason": None,
    }


def _build_free_ticket(candidates: list[dict], run_date: date) -> dict:
    """
    3 top-value picks selected deterministically by EV × composite quality.

    Uses the same scoring as Best Singles (0.5 × EV + 0.5 × composite) so
    the Free picks are the three highest-value signals of the day — not
    random. Every user sees the same 3 picks because the ranking is
    deterministic (no date seed needed).

    All other matches are returned as 'other_legs' (shown greyed in the UI
    to encourage Pro upgrades).
    Includes kelly_stake_pct for bankroll guidance.
    """
    if not candidates:
        return {
            "key": "free", "label": "TiTiBet Free",
            "description": "3 top-value picks for today",
            "selected_legs": [], "other_legs": [],
            "combined_odds": None, "win_probability_estimate": None,
            "kelly_stake_pct": None,
            "empty_reason": "No qualifying signals for today.",
        }

    def _ev_score(c: dict) -> float:
        p = max(c.get("bayesian_prob") or 0.0, c.get("poisson_prob") or 0.0)
        odds_val = c.get("odds") or 1.0
        ev = p * odds_val - 1.0
        return 0.5 * ev + 0.5 * _leg_composite_score(c)

    ranked = sorted(candidates, key=_ev_score, reverse=True)
    n_select = min(FREE_PICK_COUNT, len(ranked))
    selected_raw = ranked[:n_select]
    selected_ids = {c["fixture_id"] for c in selected_raw}
    other_raw = [c for c in candidates if c["fixture_id"] not in selected_ids]

    selected_legs = [_candidate_to_leg(c) for c in selected_raw]
    other_legs    = [_candidate_to_leg(c) for c in other_raw]

    combined  = round(math.prod(leg["odds"] for leg in selected_legs), 2) if selected_legs else None
    win_prob  = _combo_prob(selected_legs) if len(selected_legs) >= 2 else None
    kelly_pct = _ticket_kelly_pct(combined, win_prob)

    return {
        "key": "free", "label": "TiTiBet Free",
        "description": f"Top {n_select} value picks for today",
        "selected_legs": selected_legs,
        "other_legs": other_legs,
        "combined_odds": combined,
        "win_probability_estimate": win_prob,
        "kelly_stake_pct": kelly_pct,
        "empty_reason": None,
    }


def _build_pro_tickets(candidates: list[dict]) -> dict:
    """
    5 sub-tickets for Pro users:

      high_conf_acca — 3-6 best High confidence legs (smart, claims fixtures)
      goals_acca     — 3-5 best goals-market legs, market-diversified (smart,
                       skips fixtures already in high_conf_acca)
      safe_ticket    — 3-4 legs, 8-25x combined odds (UNCHANGED, no exclusivity)
      best_singles   — top 5 EV × quality picks (skips fixtures in both accas)
      sharp_moves    — signals where the line shortened ≥5% (sharp money alert,
                       no exclusivity — market confirmation is the whole point)

    Fixture exclusivity flows: High Conf → Goals ACCA → Best Singles.
    Safe Ticket and Sharp Moves are exempt: Safe has hard odds constraints that
    require the full pool, and Sharp Moves is an overlay alert not a pick list.
    """
    claimed: set[int] = set()

    # ── High Confidence ACCA — first claim ───────────────────────────────────
    high_legs = _build_high_conf_acca_legs(candidates, exclude_fixtures=claimed)
    claimed.update(c.get("fixture_id", 0) for c in high_legs)
    high_sub = _sub_ticket(
        "high_conf_acca", "High Confidence ACCA",
        "Best High confidence picks — ranked by quality, Kelly fraction & agreement",
        high_legs,
    )

    # ── Goals ACCA — second claim, different fixtures from High Conf ──────────
    goals_legs = _build_goals_acca_legs(candidates, exclude_fixtures=claimed)
    claimed.update(c.get("fixture_id", 0) for c in goals_legs)
    goals_sub = _sub_ticket(
        "goals_acca", "Goals ACCA",
        "Best goals-market picks — Overs, BTTS & team scoring, diversified by market & league",
        goals_legs,
    )

    # ── Safe Ticket — UNCHANGED, full pool ───────────────────────────────────
    safe_raw = _build_safe_ticket_legs(candidates)
    if safe_raw:
        safe_converted = [_candidate_to_leg(l) for l in safe_raw]
        safe_combined  = round(math.prod(l["odds"] for l in safe_converted), 2)
        safe_win_prob  = _combo_prob(safe_converted)
        safe_kelly     = _ticket_kelly_pct(safe_combined, safe_win_prob)
        safe_sub = {
            "key": "safe_ticket",
            "label": "Safe Ticket",
            "description": "3-4 leg accumulator in the 8-25x combined odds band",
            "legs": safe_converted,
            "combined_odds": safe_combined,
            "win_probability_estimate": safe_win_prob,
            "low_win_prob_warning": safe_win_prob is not None and safe_win_prob < LOW_WIN_PROB_THRESHOLD,
            "very_low_win_prob": safe_win_prob is not None and safe_win_prob < VERY_LOW_WIN_PROB_THRESHOLD,
            "kelly_stake_pct": safe_kelly,
            "summary_tags": [],
            "empty_reason": None,
            "is_singles": False,
        }
    else:
        safe_sub = _sub_ticket(
            "safe_ticket", "Safe Ticket",
            "3-4 leg accumulator in the 8-25x combined odds band",
            [],
        )

    # ── Best Singles — third claim, different fixtures from both accas ────────
    best_legs = _build_best_singles_legs(candidates, exclude_fixtures=claimed)
    best_sub = _sub_ticket(
        "best_singles", "Best Singles",
        "Top 5 value picks ranked by Expected Value × composite quality",
        best_legs,
        is_singles=True,
    )

    # ── Sharp Moves — overlay alert, no exclusivity ───────────────────────────
    sharp_legs = _build_sharp_moves_legs(candidates)
    sharp_sub = _sub_ticket(
        "sharp_moves", "Sharp Moves",
        "Signals where the market shortened ≥5% — sharp money confirming our position",
        sharp_legs,
    )

    return {
        "key": "pro", "label": "TiTiBet Pro",
        "description": "Premium ticket bundle — 5 grouped sub-tickets",
        "sub_tickets": [high_sub, goals_sub, safe_sub, best_sub, sharp_sub],
    }


async def load_titibet_tickets(
    db: AsyncSession,
    run_date: date,
    include_finished: bool = False,
) -> dict:
    """
    Build the three TiTiBet named tickets for run_date:
      general — all signal matches (optional tracking)
      free    — 3 date-seeded random picks (auto-tracked)
      pro     — 4 sub-tickets: High Conf ACCA, Goals ACCA, Safe Ticket, Best Singles (auto-tracked)

    Pass include_finished=True when reconstructing historical tickets for results
    reporting — bypasses the "exclude finished fixtures" filter in _load_candidates
    so that the same picks that were sent are recovered even after matches have ended.
    """
    raw = await _load_candidates(db, run_date, include_finished=include_finished)

    candidates: list[dict] = []
    for c in raw:
        bp = c.get("bayesian_prob") or 0.0
        pp = c.get("poisson_prob") or 0.0
        candidates.append({
            **c,
            "probability":    max(bp, pp) if (bp or pp) else None,
            "selection_name": c.get("market"),
            "ev_pct":         None,
            "signal_grade":   None,
            "why_tags":       [],
        })

    return {
        "date":            run_date.isoformat(),
        "generation_mode": "titibet_tickets",
        "general":         _build_general_ticket(candidates),
        "free":            _build_free_ticket(candidates, run_date),
        "pro":             _build_pro_tickets(candidates),
    }

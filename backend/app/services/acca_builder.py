"""
acca_builder.py — Shared ACCA candidate query and combiner logic.

Imported by both the accumulators router (HTTP serving) and auto_tracker
(scheduler-driven auto-tracking) so the two paths are always in sync.
"""
from __future__ import annotations

import math
from datetime import date

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Signal, Fixture
from app.core.config import (
    DISABLED_MARKETS, DISABLED_LEAGUES,
    OVER_GOALS_SUPPRESSED_LEAGUES, AWAY_GOALS_SUPPRESSED_LEAGUES, OVER25_SUPPRESSED_TIERS,
    DUAL_HIGH_ODDS_CEILING, WOMEN_LEAGUE_KEYWORDS, WOMEN_OVER_SUPPRESSED_MARKETS,
    HO05_DATA_POOR_COUNTRIES, ACCA_OVER25_UNKNOWN_TIER_CEILING,
    COPA_HO05_SUPPRESSED_LEAGUES,
    is_womens_fixture,
)
from app.services.signal_engine import _get_underperforming_leagues

# 2026-07-11 ACCA tightening: raised all probability floors after -33.5% ROI audit.
# 2026-08-05 ACCA tightening: raised _MIN_PROB and _ACCA_WIN_PROB_FLOOR to improve
# hit rate. The won Aug-3 ticket had probs 0.79-0.82 and 53.4% expected win rate;
# the Aug-5 ticket had probs 0.77 and only 45.3% expected win rate. The old 0.30
# floor allowed 3-leg tickets at 67% per leg — a 30% win rate — which is too loose.
# New floor of 0.48 requires either 3 legs averaging ≥79% (0.79³=49%) or 2 legs
# averaging ≥72% (0.72²=52%). On weak-signal days no acca is built — better than
# a structurally poor ticket.
_MIN_PROB = 0.72            # was 0.68 — global floor for all ACCA legs
# HO0.5 legs require higher conviction than other markets in ACCA context.
# The market's track record of 0-0 losses (especially Tier 2/3) means we need
# the model to be meaningfully more confident before compounding the leg.
_HO05_ACCA_MIN_PROB = 0.76  # was 0.75 — lifted by 1pp to stay above new _MIN_PROB
_ALLOWED_CONFIDENCE = {"High", "Medium"}  # Both+Medium has 77.3% WR on clean data
# For Both+High ACCA legs both engines must individually clear this floor.
# Raised 0.73 → 0.76 so weak Both+High signals that borderline-qualify for singles
# are excluded from ACCA legs where the compounding risk is higher.
_ACCA_DUAL_HIGH_MIN_PROB = 0.78  # was 0.76 — lifted by 2pp in line with tighter gates

# Expected ticket win probability floor. A ticket whose leg probabilities multiply
# to below this value is not built.
# 0.48 ≈ 3-leg ticket at 79% per leg, or 2-leg ticket at 69% per leg.
# Old value of 0.30 was too permissive — allowed 30% expected win rate (3 legs @67%).
_ACCA_WIN_PROB_FLOOR = 0.48

# Odds band for any individual ACCA leg: [_ACCA_MIN_LEG_ODDS, _ACCA_MAX_LEG_ODDS].
#
# Ceiling (1.60): high-odds Under legs indicate bookmaker uncertainty about a
# blowout — the exact variance that kills multi-leg tickets.
# Aug-2026: Myanmar 7-2 Laos @1.93 and Sydkysten 3-1 Ishøj @1.74 sank the ACCA.
#
# Floor (1.28): legs priced below 1.28 (implied prob >78%) are dead-weight.
# They contribute only ~0.28 to the combined multiplier but still carry full loss
# risk — when they fail the whole ticket is gone for minimal odds uplift.
# At prob ≥0.78 the bookmaker has already priced out the edge; including these
# legs is paying loss risk without receiving corresponding reward.
_ACCA_MIN_LEG_ODDS = 1.28
_ACCA_MAX_LEG_ODDS = 1.60


def _primary_prob(sig: Signal) -> float:
    bayes   = sig.bayesian_prob  or 0.0
    poisson = sig.poisson_prob   or 0.0
    return max(bayes, poisson)


def _is_correlated(leg_a: dict, leg_b: dict) -> bool:
    """
    Return True when two legs share the same league AND the same market family.

    "Market family" = the first word of the market string (Over, Under, Home,
    Away, BTTS, Draw, …).  Stacking two Over-goals bets from the same league
    on the same day exposes the ticket to shared environmental factors (referee
    assignment, weather, tactical meta) that make both legs move together.
    Same-league + same-family pairs are excluded from a single ticket.
    """
    league_a = (leg_a.get("league") or "").strip().lower()
    league_b = (leg_b.get("league") or "").strip().lower()
    if not league_a or league_a != league_b:
        return False
    mkt_a = (leg_a.get("market") or "").split(" ")[0].lower()
    mkt_b = (leg_b.get("market") or "").split(" ")[0].lower()
    return bool(mkt_a) and mkt_a == mkt_b


def build_accumulator(
    candidates: list[dict],
    target_odds: float,
    max_legs: int = 3,
) -> dict:
    """
    Greedy combiner with minimised overshoot, max-legs cap, and correlation filter.

    Algorithm (candidates already sorted by fair_odds ascending — most certain first):
    1. At each step check if any unused, non-correlated candidate can close the
       remaining gap to target on its own.  If yes, pick the one with the
       smallest overshoot (fair_odds − remaining_needed) and stop.
    2. If no single closer exists, take the next unused non-correlated candidate
       and continue.
    3. Stop when max_legs is reached, target is hit, or no valid candidates remain.

    Returns expected_win_probability so callers can enforce a win-rate floor
    before accepting the ticket.
    """
    legs: list[dict] = []
    used: set[int] = set()
    combined = 1.0

    def _uncorrelated(c: dict) -> bool:
        return not any(_is_correlated(c, leg) for leg in legs)

    while len(legs) < max_legs and combined < target_odds:
        remaining_needed = target_odds / combined

        # Phase 1 — look for a single closer that bridges the gap on its own.
        closers = [
            (j, c) for j, c in enumerate(candidates)
            if j not in used and c["fair_odds"] >= remaining_needed and _uncorrelated(c)
        ]
        if closers:
            best_j, best_c = min(closers, key=lambda jc: jc[1]["fair_odds"] - remaining_needed)
            legs.append(best_c)
            combined *= best_c["fair_odds"]
            used.add(best_j)
            break

        # Phase 2 — no closer; take the next unused non-correlated candidate.
        took = False
        for j, c in enumerate(candidates):
            if j not in used and _uncorrelated(c):
                legs.append(c)
                combined *= c["fair_odds"]
                used.add(j)
                took = True
                break
        if not took:
            break

    expected_win_prob = round(
        math.prod(c["primary_prob"] for c in legs), 4
    ) if legs else 0.0

    # Drop the ticket entirely if win probability is below the structural floor.
    if expected_win_prob < _ACCA_WIN_PROB_FLOOR:
        legs = []
        combined = 1.0
        expected_win_prob = 0.0

    # Bookmaker combined: multiply actual bookmaker odds per leg (what you'd
    # see on a real slip). Falls back to fair_odds when no bookmaker price exists.
    bookmaker_combined = round(
        math.prod(c.get("odd") or c["fair_odds"] for c in legs), 4
    ) if legs else 1.0

    return {
        "target_odds":              target_odds,
        "combined_odds":            bookmaker_combined,
        "legs":                     legs,
        "leg_count":                len(legs),
        "expected_win_probability": expected_win_prob,
        "insufficient_picks":       combined < target_odds or not legs,
    }


async def build_acca_candidates(
    db: AsyncSession,
    target_date: date,
    *,
    exclude_fixture_ids: set[int] | None = None,
) -> list[dict]:
    """
    Return sorted ACCA candidate list for target_date, restricted to
    Both-engine signals at High or Medium confidence.  Clean post-Jul-2 data
    shows Medium+Both at 77.3% WR — the probability floors (_MIN_PROB,
    _HO05_ACCA_MIN_PROB) and Both-agreement requirement are the real quality
    gates.  Single-engine legs are still excluded.  Optionally exclude
    specific fixture IDs so subsequent calls produce non-overlapping leg sets.
    """
    query = (
        select(Signal, Fixture)
        .join(Fixture, Signal.fixture_id == Fixture.id)
        .where(Fixture.event_date == target_date)
        .where(Signal.is_candidate == False)  # noqa: E712
        .where(Signal.dual_confidence.in_(list(_ALLOWED_CONFIDENCE)))
        .where(Signal.dual_agreement == "Both")  # T2-only: single-engine legs lose money
    )

    bad_leagues   = await _get_underperforming_leagues(db, min_roi_pct=-20.0)
    all_suppressed = bad_leagues | DISABLED_LEAGUES
    if all_suppressed:
        query = query.where(func.lower(func.trim(Fixture.league)).notin_(all_suppressed))
        query = query.where(~func.lower(func.trim(Fixture.league)).contains("friendlies"))
    if DISABLED_MARKETS:
        query = query.where(Signal.market.notin_(list(DISABLED_MARKETS)))
    if OVER_GOALS_SUPPRESSED_LEAGUES:
        _OVER = ["Over 1.5", "Over 2.5", "Home Over 0.5", "Home Over 1.5",
                 "Away Over 0.5", "Away Over 1.5"]
        for lk in OVER_GOALS_SUPPRESSED_LEAGUES:
            query = query.where(
                ~(func.lower(func.trim(Fixture.league)).contains(lk) & Signal.market.in_(_OVER))
            )
    if AWAY_GOALS_SUPPRESSED_LEAGUES:
        _AWAY = ["Away Over 0.5", "Away Over 1.5"]
        for lk in AWAY_GOALS_SUPPRESSED_LEAGUES:
            query = query.where(
                ~(func.lower(func.trim(Fixture.league)).contains(lk) & Signal.market.in_(_AWAY))
            )
    if OVER25_SUPPRESSED_TIERS:
        query = query.where(
            ~(
                (Signal.market == "Over 2.5")
                & Fixture.league_tier.in_(list(OVER25_SUPPRESSED_TIERS))
            )
        )

    rows = (await db.execute(query)).all()

    if DUAL_HIGH_ODDS_CEILING:
        # In ACCA context gate ANY Both-agreement signal above the ceiling —
        # per-leg errors compound so the stricter standard applies vs singles (High+Both only).
        rows = [(sig, fix) for sig, fix in rows if not (
            sig.dual_agreement == "Both"
            and sig.market in DUAL_HIGH_ODDS_CEILING
            and (sig.bayesian_best_odd or 0.0) >= DUAL_HIGH_ODDS_CEILING[sig.market]
        )]
    if WOMEN_OVER_SUPPRESSED_MARKETS:
        rows = [(sig, fix) for sig, fix in rows if not (
            sig.market in WOMEN_OVER_SUPPRESSED_MARKETS
            and is_womens_fixture(fix.league, fix.home_team, fix.away_team)
        )]
    if HO05_DATA_POOR_COUNTRIES:
        rows = [(sig, fix) for sig, fix in rows if not (
            sig.market == "Home Over 0.5" and sig.dual_confidence == "High"
            and sig.dual_agreement == "Both" and (fix.league_tier or 3) >= 3
            and (fix.country or "").lower() in HO05_DATA_POOR_COUNTRIES
        )]
    rows = [(sig, fix) for sig, fix in rows if not (
        sig.market == "Over 2.5"
        and sig.dual_confidence == "High"
        and fix.league_tier is None
        and (sig.bayesian_best_odd or 0.0) >= ACCA_OVER25_UNKNOWN_TIER_CEILING
    )]

    # Quality floor — HO0.5 legs use a stricter floor than other markets.
    rows = [
        (sig, fix) for sig, fix in rows
        if _primary_prob(sig) >= (
            _HO05_ACCA_MIN_PROB if sig.market == "Home Over 0.5" else _MIN_PROB
        )
    ]

    # Leg odds band: [_ACCA_MIN_LEG_ODDS, _ACCA_MAX_LEG_ODDS].
    # Upper cap — high odds signal bookmaker uncertainty about a blowout.
    # Lower floor — legs below 1.28 contribute barely 0.28× to combined odds
    # but still carry full loss risk; the edge has been priced out by the market.
    # Aug-2026: Myanmar @1.93 and Sydkysten @1.74 sank the ticket (upper gate).
    rows = [
        (sig, fix) for sig, fix in rows
        if _ACCA_MIN_LEG_ODDS <= (sig.bayesian_best_odd or 0.0) <= _ACCA_MAX_LEG_ODDS
    ]

    # Both+High ACCA gate: both engines must individually clear the same floor
    # applied by auto_tracker for singles. A Both+High signal at 0.65 primary_prob
    # (which singles rejects) must not sneak into an ACCA leg via the lower _MIN_PROB.
    rows = [
        (sig, fix) for sig, fix in rows
        if not (
            sig.dual_confidence == "High"
            and sig.dual_agreement == "Both"
            and min(sig.bayesian_prob or 0.0, sig.poisson_prob or 0.0) < _ACCA_DUAL_HIGH_MIN_PROB
        )
    ]

    # HO0.5 Tier 3 ACCA gate: exclude Home Over 0.5 legs from Tier 3 leagues.
    # Loss audit (Jul 2026): every system loss came from HO0.5. The majority are
    # Tier 3 fixtures where home-team scoring rates are structurally unreliable —
    # the model is overconfident on data-sparse lower leagues. Compounding per-leg
    # errors makes the tier gate more important in ACCA context than in singles.
    rows = [(sig, fix) for sig, fix in rows if not (
        sig.market == "Home Over 0.5"
        and (fix.league_tier or 3) >= 3
    )]

    # HO0.5 quality gate for the 1.50–1.64 odds band.
    # This band has 71.4% WR vs 92.9% for the 1.65–2.09 value band — the weakest
    # zone in the system. At these odds the model is highly confident but per-leg
    # errors still compound; require Grade A quality (>= 0.55) before including
    # as an ACCA leg. Mirrors the auto_tracker low-odds HO0.5 gate applied at <1.70.
    # Aug-2026: Bate Borisov @1.56 (0-1), Al Hikma @1.58 (0-1) — pre-gate losses.
    rows = [(sig, fix) for sig, fix in rows if not (
        sig.market == "Home Over 0.5"
        and 1.50 <= (sig.bayesian_best_odd or 0.0) < 1.65
        and (sig.dual_quality_score or 0.0) < 0.55
    )]

    # Over 2.5 Tier 3 ACCA gate: exclude Over 2.5 legs from Tier 3 leagues.
    # Loss audit (Jul 2026): Norway 1. Division (Tier 3) Over 2.5 @1.57 failed.
    # Tier 3 competitions have thin market coverage and volatile scoring patterns —
    # the goal-scoring models lose calibration on data-sparse lower leagues.
    # In ACCA context the risk compounds across legs; require Tier 1 or Tier 2.
    rows = [(sig, fix) for sig, fix in rows if not (
        sig.market == "Over 2.5"
        and (fix.league_tier or 3) >= 3
    )]

    # Over 1.5 Tier 3 ACCA gate: exclude Over 1.5 legs from Tier 3 leagues.
    # Mirrors the Over 2.5 Tier 3 gate — the 1.5-goal bar gives false security
    # in data-sparse lower leagues where 0-0 and 1-0 results are elevated.
    # A Tier 3 fixture where one goal is at all uncertain should not be an ACCA leg.
    rows = [(sig, fix) for sig, fix in rows if not (
        sig.market == "Over 1.5"
        and (fix.league_tier or 3) >= 3
    )]

    # Copa/cup gate: suppress Home Over 0.5 in South American cup competitions.
    # Rotation/reserve line-ups and knockout incentives depress home scoring.
    if COPA_HO05_SUPPRESSED_LEAGUES:
        rows = [(sig, fix) for sig, fix in rows if not (
            sig.market == "Home Over 0.5"
            and any(kw in (fix.league or "").lower() for kw in COPA_HO05_SUPPRESSED_LEAGUES)
        )]

    # BOS gate for Over-goals ACCA legs: stable/defensive fixture (bos_passed=True)
    # contradicts an Over-goals pick. Compounding per-leg errors makes this
    # constraint more important in ACCA context than in singles.
    _BOS_OVER_MKTS = {"Home Over 0.5", "Away Over 0.5", "Over 1.5", "Over 2.5",
                      "Home Over 1.5", "Away Over 1.5"}
    rows = [(sig, fix) for sig, fix in rows if not (
        sig.bos_passed and sig.market in _BOS_OVER_MKTS
    )]

    # Best signal per fixture
    best: dict[int, tuple[Signal, Fixture]] = {}
    for sig, fix in rows:
        prob = _primary_prob(sig)
        existing = best.get(sig.fixture_id)
        if existing is None or prob > _primary_prob(existing[0]):
            best[sig.fixture_id] = (sig, fix)

    # Apply exclusion list after dedup (so a fixture excluded from ACCA 1 is
    # completely absent from the remaining pool for ACCA 2+)
    if exclude_fixture_ids:
        best = {fid: pair for fid, pair in best.items() if fid not in exclude_fixture_ids}

    candidates: list[dict] = []
    for sig, fix in best.values():
        prob = _primary_prob(sig)
        if prob <= 0:
            continue
        candidates.append({
            "signal_id":     sig.id,
            "fixture_id":    sig.fixture_id,
            "match_name":    f"{fix.home_team} vs {fix.away_team}",
            "home_team":     fix.home_team,
            "away_team":     fix.away_team,
            "league":        fix.league,
            "country":       fix.country,
            "league_tier":   fix.league_tier,
            "kickoff_at":    fix.kickoff_at.isoformat() if fix.kickoff_at else None,
            "status":        fix.status,
            "home_score":    fix.home_score,
            "away_score":    fix.away_score,
            "market":        sig.market,
            "confidence":    sig.dual_confidence,
            "agreement":     sig.dual_agreement,
            "primary_prob":  round(prob, 4),
            "fair_odds":     round(1.0 / prob, 4),
            "odd":           sig.bayesian_best_odd or round(1.0 / prob, 4),
            "bookmaker_odds": sig.bayesian_best_odd,
            "bookmaker":     sig.bayesian_bookmaker,
            "quality_score": sig.dual_quality_score,
        })

    # Prefer legs in the 1.70–1.99 bookmaker-odds band, ranked by confidence:
    #   0 — Both+High  in 1.70–1.99  (highest conviction, confirmed 100% WR)
    #   1 — Both+Medium in 1.70–1.99 (still strong, second pick)
    #   2 — everything else (outside band or single-engine, fallback only)
    # Within each priority tier sort by fair_odds ascending (most certain first).
    def _band_priority(c: dict) -> int:
        odd = c.get("odd") or c["fair_odds"]
        in_band = 1.70 <= odd < 2.00
        if in_band and c.get("confidence") == "High":
            return 0
        if in_band and c.get("confidence") == "Medium":
            return 1
        return 2

    candidates.sort(key=lambda c: (_band_priority(c), c["fair_odds"]))
    return candidates

"""
acca_builder.py — Shared ACCA candidate query and combiner logic.

Imported by both the accumulators router (HTTP serving) and auto_tracker
(scheduler-driven auto-tracking) so the two paths are always in sync.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Signal, Fixture
from app.core.config import (
    DISABLED_MARKETS, DISABLED_LEAGUES,
    OVER_GOALS_SUPPRESSED_LEAGUES, AWAY_GOALS_SUPPRESSED_LEAGUES,
    DUAL_HIGH_ODDS_CEILING, WOMEN_LEAGUE_KEYWORDS, WOMEN_OVER_SUPPRESSED_MARKETS,
    HO05_DATA_POOR_COUNTRIES, ACCA_OVER25_UNKNOWN_TIER_CEILING,
    is_womens_fixture,
)
from app.services.signal_engine import _get_underperforming_leagues

_MIN_PROB = 0.62
_ALLOWED_CONFIDENCE = {"Medium", "High"}
# For Both+High ACCA legs both engines must individually clear this floor.
# Mirrors the auto_tracker gate (DUAL_HIGH_MIN_PROB) so weak Both+High signals
# that are barred from singles are also barred from ACCA legs — compounding
# per-leg errors makes the threshold more important, not less, in ACCA context.
_ACCA_DUAL_HIGH_MIN_PROB = 0.73


def _primary_prob(sig: Signal) -> float:
    bayes   = sig.bayesian_prob  or 0.0
    poisson = sig.poisson_prob   or 0.0
    return max(bayes, poisson)


def build_accumulator(candidates: list[dict], target_odds: float) -> dict:
    """
    Greedy combiner with minimised overshoot.

    At each step, check whether any single remaining candidate can close the
    remaining gap to target on its own.  If yes, take the one whose fair_odds
    is closest to the exact remaining needed amount (minimising overshoot).
    If no single candidate can close the gap alone, take the shortest-odds
    pick and continue.
    """
    legs: list[dict] = []
    combined = 1.0

    for i, c in enumerate(candidates):
        if combined >= target_odds:
            break
        remaining_needed = target_odds / combined
        eligible = [c2 for c2 in candidates[i:] if c2["fair_odds"] >= remaining_needed]
        if eligible:
            best_last = min(eligible, key=lambda c2: c2["fair_odds"] - remaining_needed)
            legs.append(best_last)
            combined *= best_last["fair_odds"]
            break
        legs.append(c)
        combined *= c["fair_odds"]

    return {
        "target_odds":     target_odds,
        "combined_odds":   round(combined, 4),
        "legs":            legs,
        "leg_count":       len(legs),
        "insufficient_picks": combined < target_odds,
    }


async def build_acca_candidates(
    db: AsyncSession,
    target_date: date,
    *,
    exclude_fixture_ids: set[int] | None = None,
) -> list[dict]:
    """
    Return sorted ACCA candidate list for target_date, applying all
    suppression gates.  Optionally exclude specific fixture IDs so that
    subsequent calls on the same day produce non-overlapping leg sets.
    """
    query = (
        select(Signal, Fixture)
        .join(Fixture, Signal.fixture_id == Fixture.id)
        .where(Fixture.event_date == target_date)
        .where(Signal.is_candidate == False)  # noqa: E712
        .where(Signal.dual_confidence.in_(list(_ALLOWED_CONFIDENCE)))
    )

    bad_leagues   = await _get_underperforming_leagues(db, min_roi_pct=60.0)
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

    rows = (await db.execute(query)).all()

    if DUAL_HIGH_ODDS_CEILING:
        rows = [(sig, fix) for sig, fix in rows if not (
            sig.dual_confidence == "High" and sig.dual_agreement == "Both"
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

    # Quality floor
    rows = [(sig, fix) for sig, fix in rows if _primary_prob(sig) >= _MIN_PROB]

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

    candidates.sort(key=lambda c: c["fair_odds"])
    return candidates

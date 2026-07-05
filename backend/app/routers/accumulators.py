from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_optional
from app.core.database import get_db
from app.core.config import (
    DISABLED_MARKETS, DISABLED_LEAGUES,
    OVER_GOALS_SUPPRESSED_LEAGUES, AWAY_GOALS_SUPPRESSED_LEAGUES,
    DUAL_HIGH_ODDS_CEILING, WOMEN_LEAGUE_KEYWORDS, WOMEN_OVER_SUPPRESSED_MARKETS,
    HO05_DATA_POOR_COUNTRIES,
)
from app.models import Signal, Fixture
from app.models.user import User
from app.services.signal_engine import _get_underperforming_leagues

router = APIRouter(prefix="/api/accumulators", tags=["accumulators"])

ACCUMULATOR_TIERS = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
_MIN_PROB = 0.55
_ALLOWED_CONFIDENCE = {"Medium", "High"}
_FREE_LEG_LIMIT = 2


def _primary_prob(sig: Signal) -> float:
    bayes = sig.bayesian_prob or 0.0
    poisson = sig.poisson_prob or 0.0
    return max(bayes, poisson)


def _build_accumulator(candidates: list[dict], target_odds: float) -> dict:
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
        # Which remaining candidates can single-handedly reach target?
        eligible = [
            c2 for c2 in candidates[i:]
            if c2["fair_odds"] >= remaining_needed
        ]
        if eligible:
            # Take the pick whose odds land closest to (but at or above) remaining_needed
            best_last = min(eligible, key=lambda c2: c2["fair_odds"] - remaining_needed)
            legs.append(best_last)
            combined *= best_last["fair_odds"]
            break
        # No single remaining pick can close the gap — add the shortest-odds pick
        legs.append(c)
        combined *= c["fair_odds"]

    return {
        "target_odds": target_odds,
        "combined_odds": round(combined, 4),
        "legs": legs,
        "leg_count": len(legs),
        "insufficient_picks": combined < target_odds,
    }


def _gate_legs(legs: list[dict], is_pro: bool) -> list[dict]:
    if is_pro:
        return legs
    return [
        {**leg, "locked": i >= _FREE_LEG_LIMIT}
        for i, leg in enumerate(legs)
    ]


@router.get("")
async def get_accumulators(
    date_str: Optional[str] = Query(None, alias="date"),
    target_odds: Optional[float] = Query(None, description="Single tier. If omitted, returns all 6 tiers."),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    target_date = date.fromisoformat(date_str) if date_str else date.today()

    query = (
        select(Signal, Fixture)
        .join(Fixture, Signal.fixture_id == Fixture.id)
        .where(Fixture.event_date == target_date)
        .where(Signal.is_candidate == False)  # noqa: E712
        .where(Signal.dual_confidence.in_(list(_ALLOWED_CONFIDENCE)))
    )

    bad_leagues = await _get_underperforming_leagues(db, min_roi_pct=60.0)
    all_suppressed = bad_leagues | DISABLED_LEAGUES
    if all_suppressed:
        query = query.where(func.lower(func.trim(Fixture.league)).notin_(all_suppressed))
        query = query.where(~func.lower(func.trim(Fixture.league)).contains("friendlies"))
    if DISABLED_MARKETS:
        query = query.where(Signal.market.notin_(list(DISABLED_MARKETS)))
    if OVER_GOALS_SUPPRESSED_LEAGUES:
        _OVER = ["Over 1.5", "Over 2.5", "Home Over 0.5", "Home Over 1.5", "Away Over 0.5", "Away Over 1.5"]
        for lk in OVER_GOALS_SUPPRESSED_LEAGUES:
            query = query.where(~(func.lower(func.trim(Fixture.league)).contains(lk) & Signal.market.in_(_OVER)))
    if AWAY_GOALS_SUPPRESSED_LEAGUES:
        _AWAY = ["Away Over 0.5", "Away Over 1.5"]
        for lk in AWAY_GOALS_SUPPRESSED_LEAGUES:
            query = query.where(~(func.lower(func.trim(Fixture.league)).contains(lk) & Signal.market.in_(_AWAY)))

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
            and any(kw in (fix.league or "").lower() for kw in WOMEN_LEAGUE_KEYWORDS)
        )]
    if HO05_DATA_POOR_COUNTRIES:
        rows = [(sig, fix) for sig, fix in rows if not (
            sig.market == "Home Over 0.5" and sig.dual_confidence == "High"
            and sig.dual_agreement == "Both" and (fix.league_tier or 3) >= 3
            and (fix.country or "").lower() in HO05_DATA_POOR_COUNTRIES
        )]

    # Quality floor
    rows = [(sig, fix) for sig, fix in rows if _primary_prob(sig) >= _MIN_PROB]

    # Best signal per fixture by primary_prob
    best: dict[int, tuple[Signal, Fixture]] = {}
    for sig, fix in rows:
        prob = _primary_prob(sig)
        existing = best.get(sig.fixture_id)
        if existing is None or prob > _primary_prob(existing[0]):
            best[sig.fixture_id] = (sig, fix)

    # Build sorted candidate list (shortest fair-value odds = highest prob first)
    candidates: list[dict] = []
    for sig, fix in best.values():
        prob = _primary_prob(sig)
        if prob <= 0:
            continue
        candidates.append({
            "signal_id": sig.id,
            "fixture_id": sig.fixture_id,
            "match_name": f"{fix.home_team} vs {fix.away_team}",
            "home_team": fix.home_team,
            "away_team": fix.away_team,
            "league": fix.league,
            "country": fix.country,
            "league_tier": fix.league_tier,
            "kickoff_at": fix.kickoff_at.isoformat() if fix.kickoff_at else None,
            "status": fix.status,
            "home_score": fix.home_score,
            "away_score": fix.away_score,
            "market": sig.market,
            "confidence": sig.dual_confidence,
            "agreement": sig.dual_agreement,
            "primary_prob": round(prob, 4),
            "fair_odds": round(1.0 / prob, 4),
            "bookmaker_odds": sig.bayesian_best_odd,
            "bookmaker": sig.bayesian_bookmaker,
            "quality_score": sig.dual_quality_score,
        })

    candidates.sort(key=lambda c: c["fair_odds"])

    is_pro = (
        current_user is not None
        and current_user.tier in ("pro", "elite")
        and current_user.subscription_status == "active"
    )

    if target_odds is not None:
        acc = _build_accumulator(candidates, target_odds)
        acc["legs"] = _gate_legs(acc["legs"], is_pro)
        acc["date"] = str(target_date)
        return acc

    tiers: dict[str, dict] = {}
    for t in ACCUMULATOR_TIERS:
        acc = _build_accumulator(candidates, t)
        acc["legs"] = _gate_legs(acc["legs"], is_pro)
        tiers[str(t)] = acc

    return {
        "date": str(target_date),
        "tiers": tiers,
        "total_qualifying": len(candidates),
    }

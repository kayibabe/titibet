from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_optional, get_current_user
from app.core.database import get_db
from sqlalchemy import func
from app.core.config import get_settings, DISABLED_MARKETS, DISABLED_LEAGUES, OVER_GOALS_SUPPRESSED_LEAGUES, AWAY_GOALS_SUPPRESSED_LEAGUES, MAX_SIGNALS_PER_TIER3_LEAGUE, MAX_SIGNALS_PER_MARKET
from app.models import Signal, Fixture, TrackedBet
from app.models.odds import MarketSnapshot
from app.models.user import User
from app.schemas.signal import SignalOut, BayesianOut, PoissonOut, AdvancedModelsOut, BookmakerOdds
from app.services.signal_engine import compute_signals_for_date, _get_underperforming_leagues
from app.services.match_info import get_match_info
from app.services.clv import _BET_TO_SELECTION, _MARKET_TYPE_SCOPE

FREE_SIGNAL_LIMIT = 5

router = APIRouter(prefix="/api/signals", tags=["signals"])
settings = get_settings()


async def _compute_clv_market_ranks(db: AsyncSession) -> dict[str, int]:
    """
    Returns {market_type: 1} for markets where tracked history shows consistent
    positive CLV (avg > 1.5 %, positive-CLV rate > 58 %, min 10 settled bets).
    Used to boost ranking of signals in markets where the model reliably beats
    the closing line — the strongest long-run edge indicator in sports betting.
    """
    from sqlalchemy import text
    result = await db.execute(text("""
        SELECT market_type,
               COUNT(*) AS n,
               AVG(clv_pct) AS avg_clv,
               SUM(CASE WHEN clv_pct > 0 THEN 1.0 ELSE 0.0 END) / COUNT(*) AS pos_rate
        FROM tracked_bets
        WHERE clv_pct IS NOT NULL
          AND result_status IN ('Won', 'Lost')
        GROUP BY market_type
        HAVING COUNT(*) >= 10
    """))
    ranks: dict[str, int] = {}
    for row in result.all():
        market, _n, avg_clv, pos_rate = row
        if avg_clv is not None and avg_clv > 1.5 and pos_rate > 0.58:
            ranks[market] = 1
    return ranks


def _system_rank(
    sig: Signal,
    fixture: Fixture | None = None,
    clv_ranks: dict[str, int] | None = None,
) -> tuple:
    """
    Rank signals by evidence-backed priority order:
      1. confidence_rank       — High=3 > Medium=2 > Low=1
      2. agreement_rank        — Both=3 > Bayesian Only=2 > Poisson Only=1
      3. high_probability_flag — primary_prob ≥ 0.70
      4. primary_prob          — continuous, max(bayesian, poisson)
      5. bookmaker_support     — 3+ books = strongest price consensus
      6. clv_market_rank       — market has consistent positive CLV history (≥10 bets)
      7. drift_rank            — odds shortened since opening (sharp money confirmed)
      8. dual_model_prob_flag  — both engines ≥ 0.65
      9. glicko_certainty      — normalised |Glicko-2 rating gap| (more predictable fixtures rank higher)
     10. tier_rank             — Tier 1 league
     11. avg_prob              — (bayesian + poisson) / 2
     12. quality_score         — tie-breaker only
     13. goals_expectation     — lambda total (final tie-breaker)

    Later tuple items act only as tie-breakers for earlier priorities.
    """
    bayes_prob = sig.bayesian_prob or 0.0
    poisson_prob = sig.poisson_prob or 0.0
    primary_prob = max(bayes_prob, poisson_prob)
    avg_prob = ((bayes_prob + poisson_prob) / 2.0) if bayes_prob and poisson_prob else primary_prob
    goals_expectation = sig.poisson_lambda_total or 0.0
    books = sig.bayesian_bookmaker_count or 0
    quality = sig.dual_quality_score or 0.0

    confidence_rank = {
        "High": 3,
        "Medium": 2,
        "Low": 1,
    }.get(sig.dual_confidence or "", 0)
    agreement_rank = {
        "Both": 3,
        "Bayesian Only": 2,
        "Poisson Only": 1,
        "Contradiction": 0,
    }.get(sig.dual_agreement or "", 0)

    high_probability_flag = 1 if primary_prob >= 0.70 else 0
    dual_model_probability_flag = 1 if bayes_prob >= 0.65 and poisson_prob >= 0.65 else 0
    bookmaker_support_rank = 2 if books >= 3 else 1 if books == 2 else 0
    tier_rank = 1 if (fixture and fixture.league_tier == 1) else 0

    # CLV market rank: boost markets where the model consistently beats closing line.
    clv_market_rank = (clv_ranks or {}).get(sig.market or "", 0)

    # Drift rank: negative drift = odds shortened = sharp money confirmed our pick.
    # Threshold -3 % avoids noise from tiny market adjustments.
    drift = sig.odds_drift_pct
    drift_rank = 1 if (drift is not None and drift < -3.0) else 0

    # Glicko-2 certainty: |rating_diff| / 400 clamped to [0, 1].
    # A large rating gap (e.g. +300 pts) means the fixture is more predictable
    # regardless of direction — models are more reliable in lopsided matches.
    # Only acts as a late tie-breaker (position 9).
    glicko_certainty = 0.0
    if sig.glicko_r_diff is not None:
        glicko_certainty = min(abs(sig.glicko_r_diff) / 400.0, 1.0)

    return (
        confidence_rank,
        agreement_rank,
        high_probability_flag,
        round(primary_prob, 6),
        bookmaker_support_rank,
        clv_market_rank,              # position 6 — consistent CLV edge
        drift_rank,                   # position 7 — sharp money confirmation
        dual_model_probability_flag,  # position 8 — both engines high prob
        round(glicko_certainty, 4),   # position 9 — Glicko-2 match predictability
        tier_rank,                    # position 10
        round(avg_prob, 6),
        round(quality, 6),            # position 12 — tie-breaker only
        round(goals_expectation, 6),
    )


def _sort_metric(
    sig: Signal,
    sort_by: str,
    fixture: Fixture | None = None,
    clv_ranks: dict[str, int] | None = None,
):
    if sort_by == "system":
        return _system_rank(sig, fixture, clv_ranks)
    if sort_by == "ev":
        # Use pre-computed ev_score when available (Bayesian Kelly shrinkage applied)
        if sig.ev_score is not None:
            return sig.ev_score * 100
        if sig.bayesian_prob and sig.bayesian_best_odd:
            return (sig.bayesian_prob * sig.bayesian_best_odd - 1.0) * 100
        return float("-inf")
    if sort_by == "probability":
        return sig.bayesian_prob if sig.bayesian_prob is not None else float("-inf")
    if sort_by == "stake":
        return sig.dual_recommended_stake_pct if sig.dual_recommended_stake_pct is not None else float("-inf")
    return sig.dual_quality_score if sig.dual_quality_score is not None else float("-inf")


_UNDER_MARKETS: frozenset[str] = frozenset({"Under 2.5", "Under 3.5", "Under 1.5", "Away Under 1.5", "Home Under 1.5"})
_OVER_MARKETS: frozenset[str] = frozenset({
    "Over 1.5", "Over 2.5",
    "Home Over 0.5", "Home Over 1.5", "Away Over 0.5", "Away Over 1.5",
})


def _market_slot(market: str) -> str:
    """
    Map a market to a deduplication slot.

    Under markets and Over/team-scoring markets are independent betting positions —
    you can back both 'Home Over 0.5' AND 'Under 3.5' on the same fixture.
    Collapsing them to one signal loses valid, orthogonal picks.

    Slots:
      "under"  — Under X.5 goals (total or team)
      "over"   — Over X.5 goals or team-scoring markets
      "other"  — BTTS, corners, match winner, etc.
    """
    if market in _UNDER_MARKETS:
        return "under"
    if market in _OVER_MARKETS:
        return "over"
    return "other"


def _best_per_fixture(
    rows: list[tuple[Signal, Fixture]],
    sort_by: str,
    clv_ranks: dict[str, int] | None = None,
) -> list[tuple[Signal, Fixture]]:
    # Key: (fixture_id, market_slot) — keeps the best Over signal AND the best
    # Under signal per fixture, rather than collapsing all markets to one pick.
    best: dict[tuple[int, str], tuple[Signal, Fixture]] = {}
    for sig, fix in rows:
        slot = _market_slot(sig.market)
        key = (sig.fixture_id, slot)
        current = best.get(key)
        if current is None:
            best[key] = (sig, fix)
            continue
        current_sig, _ = current
        candidate_metric = _sort_metric(sig, sort_by, fix, clv_ranks)
        current_metric = _sort_metric(current_sig, sort_by, current[1], clv_ranks)
        if candidate_metric > current_metric or (
            candidate_metric == current_metric and
            (sig.dual_quality_score or 0.0) > (current_sig.dual_quality_score or 0.0)
        ):
            best[key] = (sig, fix)
    return list(best.values())


def _to_signal_out(
    sig: Signal,
    fixture: Fixture,
    bookmaker_odds: list[BookmakerOdds] | None = None,
) -> SignalOut:
    bayesian = None
    if sig.bayesian_prob is not None:
        bayesian = BayesianOut(
            prob=sig.bayesian_prob, edge=sig.bayesian_edge,
            best_odd=sig.bayesian_best_odd, bookmaker=sig.bayesian_bookmaker,
            overround=sig.bayesian_overround, coverage=sig.bayesian_coverage,
            bookmaker_count=sig.bayesian_bookmaker_count, is_value=sig.bayesian_is_value,
            confidence=sig.bayesian_confidence, quality_score=sig.bayesian_quality_score,
            kelly_pct=sig.bayesian_kelly_pct,
            ev_pct=round((sig.bayesian_prob * sig.bayesian_best_odd - 1.0) * 100, 2)
            if sig.bayesian_prob and sig.bayesian_best_odd else None,
        )
    poisson = None
    # Construct PoissonOut whenever ANY Poisson-side info exists — a market
    # may have no per-market poisson_prob but still carry fixture-level
    # mixed_signals worth surfacing for the contradiction alert.
    if sig.poisson_prob is not None or sig.poisson_mixed_signals:
        poisson = PoissonOut(
            lambda_h=sig.poisson_lambda_h, lambda_a=sig.poisson_lambda_a,
            lambda_total=sig.poisson_lambda_total, prob=sig.poisson_prob,
            rule_key=sig.poisson_rule_key, rule_pass=sig.poisson_rule_pass,
            rule_strong=sig.poisson_rule_strong, edge_pct=sig.poisson_edge_pct,
            grade=sig.poisson_grade,
            mixed_signals=sig.poisson_mixed_signals,
        )
    # Advanced model enrichment — only populate when at least one field is non-None
    _has_advanced = any([
        sig.bos_si is not None, sig.zinb_lambda_h is not None,
        sig.ev_score is not None, sig.glicko_r_diff is not None,
    ])
    advanced = None
    if _has_advanced:
        advanced = AdvancedModelsOut(
            bos_si=sig.bos_si,
            bos_passed=sig.bos_passed,
            zinb_lambda_h=sig.zinb_lambda_h,
            zinb_lambda_a=sig.zinb_lambda_a,
            ev_score=sig.ev_score,
            glicko_r_diff=sig.glicko_r_diff,
        )

    return SignalOut(
        id=sig.id, fixture_id=sig.fixture_id, market=sig.market,
        bayesian=bayesian, poisson=poisson,
        dual_confidence=sig.dual_confidence, dual_agreement=sig.dual_agreement,
        dual_quality_score=sig.dual_quality_score,
        dual_recommended_stake_pct=sig.dual_recommended_stake_pct,
        contradiction=sig.contradiction, computed_at=sig.computed_at,
        selection_name=sig.market,
        odds_drift_pct=sig.odds_drift_pct,
        advanced=advanced,
        bookmaker_odds=bookmaker_odds,
        home_team=fixture.home_team, away_team=fixture.away_team,
        league=fixture.league, league_tier=fixture.league_tier,
        country=fixture.country,
        kickoff_at=fixture.kickoff_at, status=fixture.status,
        home_score=fixture.home_score, away_score=fixture.away_score,
    )


@router.get("", response_model=list[SignalOut])
async def list_signals(
    date_str: Optional[str] = Query(None, alias="date"),
    confidence: Optional[str] = Query(None, description="Comma-separated: High,Medium"),
    agreement: Optional[str] = Query(None),
    market: Optional[str] = Query(None),
    min_quality: float = Query(0.0),
    sort_by: str = Query("system"),
    best_per_fixture: bool = Query(True, description="When true (default), return only the highest-ranked signal per fixture. Set false to see all signals for each game."),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    target_date = date.fromisoformat(date_str) if date_str else date.today()

    query = (
        select(Signal, Fixture)
        .join(Fixture, Signal.fixture_id == Fixture.id)
        .where(Fixture.event_date == target_date)
        .where(Signal.is_candidate == False)  # noqa: E712 — exclude data-collection candidates
    )

    if confidence:
        conf_list = [c.strip() for c in confidence.split(",")]
        query = query.where(Signal.dual_confidence.in_(conf_list))
    if agreement:
        query = query.where(Signal.dual_agreement == agreement)
    if market:
        query = query.where(Signal.market == market)
    if min_quality > 0:
        query = query.where(Signal.dual_quality_score >= min_quality)

    # Serving-time suppression — catches signals that were generated before
    # suppression rules were configured, or when the backend was restarted.
    bad_leagues = await _get_underperforming_leagues(db, min_roi_pct=60.0)
    # Merge dynamic ROI-suppressed leagues with the hard-coded blocklist
    all_suppressed_leagues = bad_leagues | DISABLED_LEAGUES
    if all_suppressed_leagues:
        query = query.where(func.lower(func.trim(Fixture.league)).notin_(all_suppressed_leagues))
    if DISABLED_MARKETS:
        query = query.where(Signal.market.notin_(list(DISABLED_MARKETS)))

    # Over-goals suppression for structurally low-scoring leagues.
    # Even if a league is not hard-banned, Over 0.5/1.5 etc. are suppressed
    # when the league consistently produces 0-0 / 1-0 results.
    if OVER_GOALS_SUPPRESSED_LEAGUES:
        _OVER_MKT_LIST = [
            "Over 1.5", "Over 2.5",
            "Home Over 0.5", "Home Over 1.5",
            "Away Over 0.5", "Away Over 1.5",
        ]
        for _league_key in OVER_GOALS_SUPPRESSED_LEAGUES:
            query = query.where(
                ~(
                    func.lower(func.trim(Fixture.league)).contains(_league_key)
                    & Signal.market.in_(_OVER_MKT_LIST)
                )
            )

    # Away-goals suppression for leagues with structurally poor away-scoring reliability.
    if AWAY_GOALS_SUPPRESSED_LEAGUES:
        _AWAY_MKT_LIST = ["Away Over 0.5", "Away Over 1.5"]
        for _league_key in AWAY_GOALS_SUPPRESSED_LEAGUES:
            query = query.where(
                ~(
                    func.lower(func.trim(Fixture.league)).contains(_league_key)
                    & Signal.market.in_(_AWAY_MKT_LIST)
                )
            )

    rows = (await db.execute(query)).all()

    # CLV market ranks: one DB query, used for all signals in this response.
    # Only computed for the default "system" sort where the ranking matters most.
    clv_ranks: dict[str, int] = {}
    if sort_by == "system":
        try:
            clv_ranks = await _compute_clv_market_ranks(db)
        except Exception:
            pass

    if best_per_fixture:
        rows = _best_per_fixture(rows, sort_by, clv_ranks)

    reverse = sort_by != "kickoff"
    if sort_by == "kickoff":
        rows.sort(
            key=lambda row: row[1].kickoff_at.timestamp() if row[1].kickoff_at else float("inf")
        )
    else:
        rows.sort(key=lambda row: _sort_metric(row[0], sort_by, row[1], clv_ranks), reverse=reverse)

    results = [_to_signal_out(sig, fix) for sig, fix in rows]

    # ── Diversity cap: max MAX_SIGNALS_PER_TIER3_LEAGUE picks per Tier 3 league ──
    # Prevents a single lower-division league flooding the list and causing
    # cluster losses when the whole league behaves defensively on one day.
    tier3_league_counts: dict[str, int] = {}
    capped: list = []
    for r in results:
        if (r.league_tier or 3) >= 3:
            n = tier3_league_counts.get(r.league or "", 0)
            if n >= MAX_SIGNALS_PER_TIER3_LEAGUE:
                continue
            tier3_league_counts[r.league or ""] = n + 1
        capped.append(r)
    results = capped

    # ── Per-market daily cap ───────────────────────────────────────────────────
    # Some high-volume markets (Home/Away Over 0.5) can dominate the signal list,
    # creating concentrated single-market exposure. Highest-ranked signals win.
    if MAX_SIGNALS_PER_MARKET:
        mkt_counts: dict[str, int] = {}
        mkt_capped: list = []
        for r in results:
            mkt_cap = MAX_SIGNALS_PER_MARKET.get(r.market or "", 0)
            if mkt_cap:
                n = mkt_counts.get(r.market or "", 0)
                if n >= mkt_cap:
                    continue
                mkt_counts[r.market or ""] = n + 1
            mkt_capped.append(r)
        results = mkt_capped

    # Enforce free-tier signal limit — pro/elite users see all signals
    is_pro = (
        current_user is not None
        and current_user.tier in ("pro", "elite")
        and current_user.subscription_status == "active"
    )
    if not is_pro:
        results = results[:FREE_SIGNAL_LIMIT]

    return results


@router.get("/stat-picks")
async def stat_driven_picks(
    date_str: Optional[str] = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    """
    Precision picks based on historical performance analysis.

    Returns only Home Over 0.5 / Away Over 0.5 signals where:
      - dual_confidence == High
      - dual_agreement  == Both   (both engines agree)
      - no contradiction
      - odds are available

    These two markets hit 75–77.8 % in tracked history when both engines agree,
    at average odds of 2.05–2.15 — the strongest documented edge in the system.

    Response shape:
      { date, singles: [...] }
    """
    target_date = date.fromisoformat(date_str) if date_str else date.today()

    _STAT_MARKETS = ["Home Over 0.5", "Away Over 0.5"]

    query = (
        select(Signal, Fixture)
        .join(Fixture, Signal.fixture_id == Fixture.id)
        .where(Fixture.event_date == target_date)
        .where(Signal.market.in_(_STAT_MARKETS))
        .where(Signal.dual_confidence == "High")
        .where(Signal.dual_agreement == "Both")
        .where(Signal.contradiction == False)  # noqa: E712
        .where(Signal.bayesian_best_odd.isnot(None))
    )

    bad_leagues = await _get_underperforming_leagues(db, min_roi_pct=60.0)
    all_suppressed = bad_leagues | DISABLED_LEAGUES
    if all_suppressed:
        query = query.where(func.lower(func.trim(Fixture.league)).notin_(all_suppressed))
    if OVER_GOALS_SUPPRESSED_LEAGUES:
        for _lk in OVER_GOALS_SUPPRESSED_LEAGUES:
            query = query.where(
                ~(func.lower(func.trim(Fixture.league)).contains(_lk)
                  & Signal.market.in_(_STAT_MARKETS))
            )
    if AWAY_GOALS_SUPPRESSED_LEAGUES:
        _AWAY_STAT = [m for m in _STAT_MARKETS if "Away Over" in m]
        if _AWAY_STAT:
            for _lk in AWAY_GOALS_SUPPRESSED_LEAGUES:
                query = query.where(
                    ~(func.lower(func.trim(Fixture.league)).contains(_lk)
                      & Signal.market.in_(_AWAY_STAT))
                )

    rows = (await db.execute(query)).all()

    clv_ranks: dict[str, int] = {}
    try:
        clv_ranks = await _compute_clv_market_ranks(db)
    except Exception:
        pass

    rows = _best_per_fixture(rows, "system", clv_ranks)
    rows.sort(key=lambda r: _sort_metric(r[0], "system", r[1], clv_ranks), reverse=True)

    def _primary_prob(sig: Signal) -> float | None:
        vals = [v for v in (sig.bayesian_prob, sig.poisson_prob) if v is not None]
        return max(vals) if vals else None

    def _ev(sig: Signal) -> float | None:
        if sig.bayesian_prob and sig.bayesian_best_odd:
            return round((sig.bayesian_prob * sig.bayesian_best_odd - 1.0) * 100, 2)
        return None

    def _leg(sig: Signal, fix: Fixture) -> dict:
        return {
            "signal_id":            sig.id,
            "fixture_id":           sig.fixture_id,
            "match_name":           f"{fix.home_team} vs {fix.away_team}",
            "home_team":            fix.home_team,
            "away_team":            fix.away_team,
            "league":               fix.league,
            "country":              fix.country,
            "league_tier":          fix.league_tier,
            "kickoff_at":           fix.kickoff_at.isoformat() if fix.kickoff_at else None,
            "event_date":           fix.event_date.isoformat() if fix.event_date else None,
            "market":               sig.market,
            "selection_name":       sig.market,
            "bookmaker":            sig.bayesian_bookmaker or "Manual",
            "odds":                 sig.bayesian_best_odd,
            "probability":          _primary_prob(sig),
            "ev_pct":               _ev(sig),
            "confidence":           sig.dual_confidence,
            "agreement":            sig.dual_agreement,
            "quality_score":        sig.dual_quality_score,
            "recommended_stake_pct": sig.dual_recommended_stake_pct,
            "source_rule_key":      sig.poisson_rule_key,
            "signal_grade":         sig.poisson_grade,
        }

    singles = [_leg(sig, fix) for sig, fix in rows]

    return {
        "date": str(target_date),
        "singles": singles,
    }


@router.get("/{fixture_id}/explain")
async def explain_signal(
    fixture_id: int,
    market: Optional[str] = Query(None, description="Specific market to explain (optional — uses best signal if omitted)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Plain-English explanation of why a signal was generated.
    Fully deterministic — no LLM, instant response.
    Covers: model agreement, probability vs bookmaker, edge, odds drift, coverage.
    """
    from fastapi import HTTPException
    q = (
        select(Signal, Fixture)
        .join(Fixture, Signal.fixture_id == Fixture.id)
        .where(Signal.fixture_id == fixture_id)
        .order_by(Signal.dual_quality_score.desc().nullslast())
    )
    if market:
        q = q.where(Signal.market == market)

    result = (await db.execute(q)).first()
    if not result:
        raise HTTPException(status_code=404, detail="Signal not found")
    sig, fix = result

    paragraphs: list[str] = []

    # ── 1. Lead sentence ───────────────────────────────────────────────────────
    conf_map = {
        "High":   "strong conviction",
        "Medium": "moderate conviction",
        "Low":    "limited conviction",
    }
    conf_phrase = conf_map.get(sig.dual_confidence or "", "an unrated conviction")
    paragraphs.append(
        f"The system has {conf_phrase} in the **{sig.market}** outcome for "
        f"**{fix.home_team} vs {fix.away_team}**."
    )

    # ── 2. Engine agreement ────────────────────────────────────────────────────
    agree_map = {
        "Both":           "Both the market-consensus (Bayesian) engine and the goal-scoring (Poisson) engine independently agree on this outcome — the strongest evidence the system can produce.",
        "Bayesian Only":  "The market-consensus engine supports this pick, but the Poisson goal model does not confirm it. The signal rests on bookmaker price movements, not goal expectation.",
        "Poisson Only":   "The Poisson goal model supports this pick based on projected scoring rates, but bookmaker prices don't fully reflect this probability — the market may be lagging.",
        "Contradiction":  "The two models disagree: one says this outcome is likely, the other says it isn't. Treat this as high-uncertainty.",
    }
    if sig.dual_agreement:
        paragraphs.append(agree_map.get(sig.dual_agreement, ""))

    # ── 3. Probability vs bookmaker ────────────────────────────────────────────
    if sig.bayesian_prob is not None and sig.bayesian_best_odd is not None:
        model_pct  = round(sig.bayesian_prob * 100, 1)
        book_pct   = round(100 / sig.bayesian_best_odd, 1)
        edge_txt   = ""
        if sig.bayesian_edge is not None:
            edge_dir  = "positive edge" if sig.bayesian_edge > 0 else "negative edge"
            edge_txt  = f" This gives a {abs(sig.bayesian_edge):.1%} {edge_dir}."
        paragraphs.append(
            f"The Bayesian model assigns a {model_pct}% probability to this outcome. "
            f"The best available odds ({sig.bayesian_best_odd} at {sig.bayesian_bookmaker or 'bookmaker'}) "
            f"imply only {book_pct}% — a difference of {abs(model_pct - book_pct):.1f} percentage points.{edge_txt}"
        )

    # ── 4. Poisson goal context ────────────────────────────────────────────────
    if sig.poisson_prob is not None and sig.poisson_lambda_total is not None:
        lh = sig.poisson_lambda_h or 0
        la = sig.poisson_lambda_a or 0
        paragraphs.append(
            f"The Poisson model projects {lh:.2f} goals from {fix.home_team} and {la:.2f} from "
            f"{fix.away_team} (total expectation: {sig.poisson_lambda_total:.2f} goals), "
            f"yielding a {round(sig.poisson_prob * 100, 1)}% probability for this market."
        )

    # ── 5. Odds drift ──────────────────────────────────────────────────────────
    if sig.odds_drift_pct is not None:
        if sig.odds_drift_pct < -3.0:
            paragraphs.append(
                f"Odds have shortened {abs(sig.odds_drift_pct):.1f}% since the market opened — "
                f"a sign that sharp money is backing the same side as the model."
            )
        elif sig.odds_drift_pct > 3.0:
            paragraphs.append(
                f"Odds have drifted out {sig.odds_drift_pct:.1f}% since opening — the market is "
                f"moving against this pick. This is a yellow flag worth noting."
            )

    # ── 6. Bookmaker coverage ──────────────────────────────────────────────────
    bc = sig.bayesian_bookmaker_count
    if bc is not None:
        coverage_map = {
            1: "Thin coverage: only 1 bookmaker is pricing this market. The signal has less statistical grounding than a multi-book consensus.",
            2: "Moderate coverage: 2 bookmakers are pricing this market.",
        }
        if bc >= 3:
            paragraphs.append(f"Strong coverage: {bc} bookmakers are pricing this market, giving the model a robust consensus to work from.")
        elif bc in coverage_map:
            paragraphs.append(coverage_map[bc])

    # ── 7. Quality tier ────────────────────────────────────────────────────────
    q_score = sig.dual_quality_score
    if q_score is not None:
        grade = "A" if q_score >= 0.08 else "B" if q_score >= 0.055 else "C" if q_score >= 0.035 else "D"
        grade_desc = {
            "A": "top-tier quality — among the strongest signals the system produces",
            "B": "above-average quality — meaningful edge with good model support",
            "C": "average quality — proceed with standard caution",
            "D": "below-average quality — marginal signal, stake conservatively",
        }
        paragraphs.append(
            f"Overall signal grade: **{grade}** ({grade_desc[grade]}). "
            f"Raw quality score: {q_score:.4f}."
        )

    return {
        "fixture_id":  fixture_id,
        "fixture":     f"{fix.home_team} vs {fix.away_team}",
        "market":      sig.market,
        "confidence":  sig.dual_confidence,
        "agreement":   sig.dual_agreement,
        "paragraphs":  paragraphs,
    }


@router.get("/diag")
async def signals_diag(
    date_str: Optional[str] = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    """Read-only diagnostics for a date: fixture / odds / signal counts. Counts
    only (no PII), so it needs no auth — used to pinpoint why signals are empty.
    Defined before /{fixture_id} so 'diag' isn't parsed as a fixture id."""
    d = date.fromisoformat(date_str) if date_str else date.today()

    fixtures = await db.scalar(
        select(func.count(Fixture.id)).where(Fixture.event_date == d)
    ) or 0
    snaps = await db.scalar(
        select(func.count(MarketSnapshot.id))
        .select_from(MarketSnapshot)
        .join(Fixture, MarketSnapshot.fixture_id == Fixture.id)
        .where(Fixture.event_date == d)
    ) or 0
    fixtures_with_odds = await db.scalar(
        select(func.count(func.distinct(MarketSnapshot.fixture_id)))
        .select_from(MarketSnapshot)
        .join(Fixture, MarketSnapshot.fixture_id == Fixture.id)
        .where(Fixture.event_date == d)
    ) or 0
    signals = await db.scalar(
        select(func.count(Signal.id))
        .select_from(Signal)
        .join(Fixture, Signal.fixture_id == Fixture.id)
        .where(Fixture.event_date == d)
    ) or 0
    max_fixture_date = await db.scalar(select(func.max(Fixture.event_date)))
    total_snaps = await db.scalar(select(func.count(MarketSnapshot.id))) or 0
    leagues = (await db.execute(
        select(Fixture.league, func.count(Fixture.id).label("c"))
        .where(Fixture.event_date == d)
        .group_by(Fixture.league)
        .order_by(func.count(Fixture.id).desc())
        .limit(8)
    )).all()

    # Market types present in today's snapshots — helps diagnose engine mismatches.
    market_type_rows = (await db.execute(
        select(MarketSnapshot.market_type, func.count(MarketSnapshot.id).label("c"))
        .select_from(MarketSnapshot)
        .join(Fixture, MarketSnapshot.fixture_id == Fixture.id)
        .where(Fixture.event_date == d)
        .group_by(MarketSnapshot.market_type)
        .order_by(func.count(MarketSnapshot.id).desc())
        .limit(20)
    )).all()

    # Fixtures with odds and their leagues — shows what's actually getting odds data.
    leagues_with_odds = (await db.execute(
        select(Fixture.league, func.count(func.distinct(MarketSnapshot.fixture_id)).label("c"))
        .select_from(MarketSnapshot)
        .join(Fixture, MarketSnapshot.fixture_id == Fixture.id)
        .where(Fixture.event_date == d)
        .group_by(Fixture.league)
        .order_by(func.count(func.distinct(MarketSnapshot.fixture_id)).desc())
        .limit(15)
    )).all()

    # Per-bookmaker × market-type breakdown for today's fixtures.
    # Shows exactly which bookmakers have CS data (critical: bayesian engine
    # needs ≥2 bookmakers with Correct Score / Exact Score to produce any signals).
    from sqlalchemy import text as _sql_text
    bk_mkt_rows = (await db.execute(_sql_text("""
        SELECT ms.bookmaker, ms.market_type, COUNT(*) as cnt
        FROM market_snapshots ms
        JOIN fixtures f ON ms.fixture_id = f.id
        WHERE f.event_date = :d
          AND ms.market_type IN (
            'Correct Score', 'Correct Score (Regular Time)', 'Exact Score',
            'Goals Over/Under', 'Total Goals', 'Over/Under',
            'Match Winner', '1X2', 'Total - Home', 'Total - Away'
          )
        GROUP BY ms.bookmaker, ms.market_type
        ORDER BY ms.bookmaker, cnt DESC
    """), {"d": d.isoformat()})).all()

    bk_summary: dict = {}
    for bk, mt, cnt in bk_mkt_rows:
        bk_summary.setdefault(bk, {})[mt] = cnt

    # How many distinct bookmakers have Correct Score / Exact Score?
    cs_market_types = {"Correct Score", "Correct Score (Regular Time)", "Exact Score"}
    cs_bookmakers = [bk for bk, mkt_map in bk_summary.items() if any(mt in cs_market_types for mt in mkt_map)]

    # Suppressed leagues for today — shows what the signal engine is filtering out.
    from app.services.signal_engine import _get_underperforming_leagues
    from app.core.config import DISABLED_LEAGUES
    try:
        bad_leagues = await _get_underperforming_leagues(db, min_roi_pct=60.0)
    except Exception:
        bad_leagues = frozenset()
    all_suppressed = bad_leagues | DISABLED_LEAGUES

    return {
        "date": d.isoformat(),
        "fixtures": fixtures,
        "fixtures_with_odds": fixtures_with_odds,
        "market_snapshots": snaps,
        "signals": signals,
        "max_fixture_date_in_db": str(max_fixture_date),
        "total_market_snapshots_all_dates": total_snaps,
        "top_leagues_today": [{"league": lg, "fixtures": c} for lg, c in leagues],
        "market_types_in_snapshots": [{"market_type": mt, "count": c} for mt, c in market_type_rows],
        "leagues_with_odds": [{"league": lg, "fixtures_with_odds": c} for lg, c in leagues_with_odds],
        "suppressed_leagues_count": len(all_suppressed),
        "leagues_with_odds_suppressed": [
            lg for lg, _ in leagues_with_odds
            if (lg or "").lower().strip() in all_suppressed
        ],
        "bookmaker_market_breakdown": bk_summary,
        "cs_bookmakers_count": len(cs_bookmakers),
        "cs_bookmakers": cs_bookmakers,
        "bayesian_min_bookmakers_required": 2,
    }


@router.get("/{fixture_id}", response_model=list[SignalOut])
async def fixture_signals(fixture_id: int, db: AsyncSession = Depends(get_db)):
    """All markets for one fixture (Deep Dive). Includes per-bookmaker odds from snapshots."""
    # Load signals
    sig_query = (
        select(Signal, Fixture)
        .join(Fixture, Signal.fixture_id == Fixture.id)
        .where(Signal.fixture_id == fixture_id)
        .order_by(Signal.dual_quality_score.desc().nullslast())
    )
    if DISABLED_MARKETS:
        sig_query = sig_query.where(Signal.market.notin_(list(DISABLED_MARKETS)))
    rows = await db.execute(sig_query)
    signal_rows = rows.all()

    # Load all market snapshots for this fixture in one query
    snap_rows = await db.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.fixture_id == fixture_id)
        .order_by(MarketSnapshot.market_type, MarketSnapshot.odds.desc().nullslast())
    )
    snapshots = snap_rows.scalars().all()

    # Group snapshots by (selection_name, market_type) for correct cross-market lookup.
    # Signal.market is standardized ("Home Over 0.5") while MarketSnapshot.market_type is
    # the raw API name ("Total - Home"). We resolve by matching selection_name + market scope.
    from collections import defaultdict
    # key: (selection_name, market_type) → BookmakerOdds list
    snap_by_sel_type: dict[tuple[str, str], list[BookmakerOdds]] = defaultdict(list)
    for snap in snapshots:
        if snap.odds is not None:
            snap_by_sel_type[(snap.selection_name, snap.market_type)].append(
                BookmakerOdds(bookmaker=snap.bookmaker, selection=snap.selection_name, odds=snap.odds)
            )

    def _bookmaker_odds_for_signal(market: str) -> list[BookmakerOdds] | None:
        sel = _BET_TO_SELECTION.get(market, market)
        scope = _MARKET_TYPE_SCOPE.get(market)
        result: list[BookmakerOdds] = []
        for (sn, mt), bos in snap_by_sel_type.items():
            if sn != sel:
                continue
            if scope and mt not in scope:
                continue
            result.extend(bos)
        return sorted(result, key=lambda x: x.odds, reverse=True) or None

    return [
        _to_signal_out(sig, fix, bookmaker_odds=_bookmaker_odds_for_signal(sig.market))
        for sig, fix in signal_rows
    ]


@router.post("/compute")
async def compute_signals(
    body: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Recompute signals for a date. Requires authentication."""
    date_str = body.get("date")
    target_date = date.fromisoformat(date_str) if date_str else date.today()
    count = await compute_signals_for_date(db, target_date)
    return {
        "signals_computed": count,
        "date": target_date.isoformat(),
    }


@router.get("/{fixture_id}/odds-matrix")
async def odds_matrix(fixture_id: int, db: AsyncSession = Depends(get_db)):
    """
    Bookmaker × market matrix for a fixture.
    Returns all bookmaker prices grouped by market_type + selection so the
    frontend can render a comparison table for line shopping.
    """
    snap_rows = await db.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.fixture_id == fixture_id)
        .order_by(MarketSnapshot.market_type, MarketSnapshot.selection_name)
    )
    snapshots = snap_rows.scalars().all()

    from collections import defaultdict
    # {market_type → {selection_name → {bookmaker: best_odds}}}
    data: dict = defaultdict(lambda: defaultdict(dict))
    bookmakers_seen: set[str] = set()

    for snap in snapshots:
        if snap.odds and snap.odds > 1.0:
            existing = data[snap.market_type][snap.selection_name].get(snap.bookmaker, 0.0)
            if snap.odds > existing:
                data[snap.market_type][snap.selection_name][snap.bookmaker] = snap.odds
                bookmakers_seen.add(snap.bookmaker)

    # Sharp books first so column order is meaningful
    sharp = {"Pinnacle", "Bet365"}
    bookmakers = sorted(bookmakers_seen, key=lambda b: (0 if b in sharp else 1, b))

    rows = []
    for market_type in sorted(data.keys()):
        for sel_name in sorted(data[market_type].keys()):
            odds_map = data[market_type][sel_name]
            best_bookie = max(odds_map, key=odds_map.get)
            rows.append({
                "market_type": market_type,
                "selection": sel_name,
                "odds": {bk: odds_map.get(bk) for bk in bookmakers},
                "best_bookie": best_bookie,
            })

    return {"bookmakers": bookmakers, "rows": rows}


@router.get("/{fixture_id}/match-info")
async def match_info(fixture_id: int, db: AsyncSession = Depends(get_db)):
    """
    Contextual match intelligence: team stats, form, performance highlights,
    H2H history, and probabilities — all computed from local fixture data.
    """
    return await get_match_info(db, fixture_id)

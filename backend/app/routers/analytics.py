from __future__ import annotations

from collections import defaultdict
from datetime import date
from itertools import combinations
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_optional
from app.core.database import get_db
from app.models import TrackedBet, Signal
from app.models.user import User
from app.models.learning_proposal import LearningProposal
from app.services.analytics import build_analytics, compute_parameter_status
from app.services.performance_intelligence import compute_performance_weights

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


_CORE_SYSTEM_KEYS = ["system_auto", "system_dual", "system_acca"]
_ADVISORY_KEYS = ["scout_pick", "strategist_pick", "skeptic_pick"]
_SYSTEM_PICK_KEYS = _CORE_SYSTEM_KEYS + _ADVISORY_KEYS  # kept for CLV/revoke compat


def _base_query(current_user: Optional[User]):
    """Start a TrackedBet query scoped to the current user + core system picks.
    Advisory picks are excluded here — they only appear via source='advisory'."""
    q = select(TrackedBet)
    if current_user:
        q = q.where(
            or_(
                TrackedBet.user_id == current_user.id,
                and_(
                    TrackedBet.user_id.is_(None),
                    TrackedBet.source_rule_key.in_(_CORE_SYSTEM_KEYS),
                ),
            )
        )
    else:
        q = q.where(
            TrackedBet.user_id.is_(None),
            TrackedBet.source_rule_key.in_(_CORE_SYSTEM_KEYS),
        )
    return q


@router.get("/summary")
async def summary(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    market_type: Optional[str] = Query(None),
    league: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    q = _base_query(current_user)
    if date_from:
        q = q.where(TrackedBet.event_date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(TrackedBet.event_date <= date.fromisoformat(date_to))
    if market_type:
        q = q.where(TrackedBet.market_type == market_type)
    if league:
        q = q.where(TrackedBet.league.ilike(f"%{league}%"))
    rows = await db.execute(q)
    bets = list(rows.scalars().all())
    result = build_analytics(bets)
    return {
        "total_bets": result["total_bets"],
        "settled_bets": result["settled_bets"],
        "pending_bets": result["pending_bets"],
        "wins": result["wins"],
        "losses": result["losses"],
        "win_rate": result["win_rate"],
        "roi": result["roi"],
        "total_profit_loss": result["total_profit_loss"],
        "total_stake": result["total_stake"],
        "longest_win_streak": result["longest_win_streak"],
        "longest_loss_streak": result["longest_loss_streak"],
        "current_streak_type": result["current_streak_type"],
        "current_streak_len": result["current_streak_len"],
        "avg_clv": result["avg_clv"],
        "clv_coverage_pct": result["clv_coverage_pct"],
        "positive_clv_pct": result["positive_clv_pct"],
    }


@router.get("/by-market")
async def by_market(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    q = _base_query(current_user)
    if date_from:
        q = q.where(TrackedBet.event_date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(TrackedBet.event_date <= date.fromisoformat(date_to))
    rows = await db.execute(q)
    bets = list(rows.scalars().all())
    return build_analytics(bets)["by_market"]


@router.get("/by-league")
async def by_league(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    q = _base_query(current_user)
    if date_from:
        q = q.where(TrackedBet.event_date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(TrackedBet.event_date <= date.fromisoformat(date_to))
    rows = await db.execute(q)
    bets = list(rows.scalars().all())
    return build_analytics(bets)["by_league"]


@router.get("/by-rule")
async def by_rule(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    q = _base_query(current_user)
    if date_from:
        q = q.where(TrackedBet.event_date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(TrackedBet.event_date <= date.fromisoformat(date_to))
    rows = await db.execute(q)
    bets = list(rows.scalars().all())
    return build_analytics(bets)["by_rule"]


@router.get("/trend")
async def trend(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    q = _base_query(current_user)
    if date_from:
        q = q.where(TrackedBet.event_date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(TrackedBet.event_date <= date.fromisoformat(date_to))
    rows = await db.execute(q)
    bets = list(rows.scalars().all())
    return build_analytics(bets)["daily_trend"]


@router.get("/streaks")
async def streaks(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    q = _base_query(current_user)
    rows = await db.execute(q)
    bets = list(rows.scalars().all())
    result = build_analytics(bets)
    return {
        "longest_win_streak": result["longest_win_streak"],
        "longest_loss_streak": result["longest_loss_streak"],
        "current_streak_type": result["current_streak_type"],
        "current_streak_len": result["current_streak_len"],
    }


@router.get("/intelligence")
async def intelligence(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Full self-learning performance intelligence report.

    Returns four breakdowns derived from settled bet history:
      - by_confidence_market: win rate / ROI / factor per (confidence, market) pair
      - by_rule:              Poisson rule performance + auto-suppress flags
      - by_market_tier:       Market performance split by league tier (1/2/3)
      - calibration:          Expected vs actual win rate per confidence tier

    This is the primary diagnostic tool for understanding WHY picks win or lose:
      - Rules / market+tier combos with factor < 0.62 are candidates for suppression
      - Calibration errors > 10% mean the engine is overconfident in that tier
      - Use by_market_tier to find which leagues are killing a specific market's ROI
    """
    weights = await compute_performance_weights(
        db,
        user_id=current_user.id if current_user else None,
    )
    conf_summary = []
    for conf, sl in weights.by_confidence.items():
        conf_summary.append({
            "confidence": conf,
            "samples": sl.samples,
            "wins": sl.wins,
            "losses": sl.losses,
            "win_rate": round(sl.win_rate * 100, 1),
            "roi": round(sl.roi * 100, 1),
            "performance_factor": sl.performance_factor,
        })
    conf_summary.sort(key=lambda x: {"High": 0, "Medium": 1, "Low": 2}.get(x["confidence"], 9))

    market_summary = []
    for market, sl in weights.by_market.items():
        market_summary.append({
            "market": market,
            "samples": sl.samples,
            "wins": sl.wins,
            "losses": sl.losses,
            "win_rate": round(sl.win_rate * 100, 1),
            "roi": round(sl.roi * 100, 1),
            "performance_factor": sl.performance_factor,
        })
    market_summary.sort(key=lambda x: -x["roi"])

    return {
        "by_confidence": conf_summary,
        "by_market": market_summary,
        "by_confidence_market": weights.as_report(),
        "by_rule": weights.rule_report(),
        "by_market_tier": weights.market_tier_report(),
        "calibration": weights.calibration_report(),
        "auto_suppress_rules": sorted(weights.auto_suppress_rules),
        "auto_suppress_market_tiers": [
            {"market": m, "league_tier": t}
            for m, t in sorted(weights.auto_suppress_market_tiers)
        ],
        "auto_suppress_league_markets": [
            {"league": league, "market": market}
            for league, market in sorted(weights.auto_suppress_league_markets)
        ],
    }


@router.get("/calibration")
async def calibration(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Confidence tier calibration report.

    Compares the model's designed expected win rate for each tier (High=63%,
    Medium=54%, Low=44%) against the actual observed win rate from settled bets.

    If calibration_error > 10% and is_overconfident=true, the engine is producing
    too many signals at that tier — tighten the relevant thresholds in config.py:
      - High overconfident: raise min_value_edge or min_derived_prob
      - Medium overconfident: raise min_edge_pct
      - Low overconfident: consider raising min_derived_prob for Low confidence signals
    """
    weights = await compute_performance_weights(
        db,
        user_id=current_user.id if current_user else None,
    )
    return weights.calibration_report()


@router.get("/parameter-status")
async def parameter_status(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Returns active / suspended / monitoring status for every market and league
    the user has tracked bets on.  Uses ALL historical bets (no date filter) so
    the sample sizes are as large as possible for reliable classification.

    Status rules:
      active     — ≥8 settled, ROI ≥ +5 %, hit rate ≥ 50 %
      suspended  — ≥8 settled, ROI ≤ −10 %
      monitoring — insufficient data or neutral performance

    Suspended parameters still generate signals — they are just flagged so the
    UI can offer an "Active Only" focus mode on the signals page.
    """
    q = _base_query(current_user)
    rows = await db.execute(q)
    bets = list(rows.scalars().all())
    return compute_parameter_status(bets)


@router.get("/full")
async def full(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    market_type: Optional[str] = Query(None),
    league: Optional[str] = Query(None),
    result_status: Optional[str] = Query(None),
    source: Optional[str] = Query(None, description="'system' or 'manual' — filters by source_rule_key bucket"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Single endpoint that returns the complete analytics payload in one DB query.
    Replaces the pattern of calling /summary + /by-market + /by-league + /trend separately.
    The frontend AnalyticsPage AND TrackerPage should use this exclusively — TrackerPage's
    stats bar passes through its own filters (date range, result_status, source) so both
    pages always compute win rate / ROI / streaks from this single implementation.
    """
    q = _base_query(current_user)
    if date_from:
        q = q.where(TrackedBet.event_date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(TrackedBet.event_date <= date.fromisoformat(date_to))
    if market_type:
        q = q.where(TrackedBet.market_type == market_type)
    if league:
        q = q.where(TrackedBet.league.ilike(f"%{league}%"))
    if result_status:
        q = q.where(TrackedBet.result_status == result_status)
    if source == "system":
        q = q.where(TrackedBet.market_type != "Accumulator")
    elif source == "manual":
        q = q.where(TrackedBet.market_type == "Accumulator")
    elif source == "advisory":
        q = q.where(TrackedBet.source_rule_key.in_(_ADVISORY_KEYS))
    rows = await db.execute(q)
    bets = list(rows.scalars().all())
    return build_analytics(bets)


@router.get("/staking-simulation")
async def staking_simulation(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Settled bets in chronological order for staking plan simulation.
    Frontend uses this to plot flat / half-Kelly / Kelly equity curves.
    """
    q = _base_query(current_user)
    q = q.where(TrackedBet.result_status.in_(["Won", "Lost"]))
    if date_from:
        q = q.where(TrackedBet.event_date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(TrackedBet.event_date <= date.fromisoformat(date_to))
    q = q.order_by(TrackedBet.event_date.asc(), TrackedBet.id.asc())
    rows = (await db.execute(q)).scalars().all()

    return [
        {
            "date": b.event_date.isoformat() if b.event_date else None,
            "odds": b.odds,
            "result": b.result_status,
            "market": b.market_type,
            "stake_pct": b.recommended_stake_pct,  # model's Kelly %; None = flat only
            "profit_loss": b.profit_loss,
            "stake": b.stake,
        }
        for b in rows
    ]


@router.get("/probability-calibration")
async def probability_calibration(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Model calibration: groups settled bets by Bayesian probability bucket and
    compares the model's predicted win rate against actual outcomes.
    A well-calibrated model should have actual_win_rate ≈ avg_model_prob.
    """
    q = (
        select(TrackedBet, Signal)
        .outerjoin(
            Signal,
            and_(
                Signal.fixture_id == TrackedBet.fixture_id,
                Signal.market == TrackedBet.market_type,
            ),
        )
        .where(TrackedBet.result_status.in_(["Won", "Lost"]))
    )
    if current_user:
        q = q.where(
            or_(
                TrackedBet.user_id == current_user.id,
                and_(
                    TrackedBet.user_id.is_(None),
                    TrackedBet.source_rule_key.in_(_CORE_SYSTEM_KEYS),
                ),
            )
        )
    else:
        q = q.where(
            TrackedBet.user_id.is_(None),
            TrackedBet.source_rule_key.in_(_CORE_SYSTEM_KEYS),
        )
    if date_from:
        q = q.where(TrackedBet.event_date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(TrackedBet.event_date <= date.fromisoformat(date_to))

    rows = (await db.execute(q)).all()

    buckets: dict = defaultdict(lambda: {"wins": 0, "total": 0, "sum_prob": 0.0})
    for bet, sig in rows:
        prob = sig.bayesian_prob if sig and sig.bayesian_prob else None
        if prob is None:
            # Fallback: no-vig implied prob from the bookmaker odds
            prob = 1.0 / bet.odds if bet.odds and bet.odds > 1.0 else None
        if prob is None:
            continue
        bucket = min(int(prob * 10) * 10, 90)  # clamp to 0–90 (each bucket covers +10pp)
        buckets[bucket]["wins"] += 1 if bet.result_status == "Won" else 0
        buckets[bucket]["total"] += 1
        buckets[bucket]["sum_prob"] += prob

    result = []
    for bmin in range(0, 100, 10):
        b = buckets[bmin]
        if b["total"] > 0:
            avg_prob = b["sum_prob"] / b["total"] * 100
            actual_wr = b["wins"] / b["total"] * 100
            result.append({
                "bucket": bmin,
                "label": f"{bmin}–{bmin + 10}%",
                "avg_model_prob": round(avg_prob, 1),
                "actual_win_rate": round(actual_wr, 1),
                "sample_size": b["total"],
                "calibration_error": round(actual_wr - avg_prob, 1),
            })

    return result


@router.get("/model-intelligence")
async def get_model_intelligence(
    db: AsyncSession = Depends(get_db),
):
    """
    Return all active LearningProposal rows for the Model Intelligence dashboard.
    Groups by change_type and includes the full proposal detail so the UI can
    show what the self-learning system has decided to change, and why.
    """
    result = await db.execute(
        select(LearningProposal)
        .where(LearningProposal.is_active == True)   # noqa: E712
        .order_by(LearningProposal.created_at.desc())
    )
    proposals = result.scalars().all()

    # Also fetch last 5 inactive proposals as history
    history_result = await db.execute(
        select(LearningProposal)
        .where(LearningProposal.is_active == False)  # noqa: E712
        .order_by(LearningProposal.created_at.desc())
        .limit(5)
    )
    history = history_result.scalars().all()

    def _fmt(p: LearningProposal) -> dict:
        return {
            "id":             p.id,
            "change_type":    p.change_type,
            "target":         p.target,
            "proposed_value": p.proposed_value,
            "rationale":      p.rationale,
            "confidence":     p.confidence,
            "backtest_note":  p.backtest_note,
            "is_active":      p.is_active,
            "created_at":     p.created_at.isoformat() if p.created_at else None,
        }

    return {
        "active_count":   len(proposals),
        "active":         [_fmt(p) for p in proposals],
        "history":        [_fmt(p) for p in history],
    }


@router.get("/acca-performance")
async def acca_performance(
    db: AsyncSession = Depends(get_db),
):
    """
    ACCA accumulator performance analytics for system-tracked legs.

    Returns three breakdowns over all settled acca_leg_system bets:
      - by_market:         leg hit rate / ROI per market type
      - by_leg_count:      ticket-level hit rate by number of legs (ticket wins only if ALL legs win)
      - two_market_combos: ticket hit rate for the most common two-market pairings
    """
    q = select(TrackedBet).where(
        TrackedBet.source_rule_key == "acca_leg_system",
        TrackedBet.result_status.in_(["Won", "Lost"]),
        TrackedBet.user_id.is_(None),
    )
    bets = (await db.execute(q)).scalars().all()

    if not bets:
        return {"by_market": [], "by_leg_count": [], "two_market_combos": []}

    # ── By market ──────────────────────────────────────────────────────────
    mkt_stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "total": 0, "pl": 0.0, "stake": 0.0})
    for bet in bets:
        s = mkt_stats[bet.market_type]
        s["wins"] += 1 if bet.result_status == "Won" else 0
        s["total"] += 1
        s["pl"] += bet.profit_loss or 0.0
        s["stake"] += bet.stake or 0.0

    by_market = sorted(
        [
            {
                "market": mkt,
                "legs": s["total"],
                "wins": s["wins"],
                "hit_rate": round(s["wins"] / s["total"] * 100, 1),
                "roi": round(s["pl"] / s["stake"] * 100, 1) if s["stake"] else 0.0,
            }
            for mkt, s in mkt_stats.items()
        ],
        key=lambda x: -x["hit_rate"],
    )

    # ── By leg count ───────────────────────────────────────────────────────
    # Group by (event_date, acca_ticket_id) so multiple advisory tickets on the
    # same date are counted as separate tickets. Fall back to event_date string
    # for legacy rows that predate the acca_ticket_id column.
    ticket_legs: dict = defaultdict(list)
    for bet in bets:
        ticket_key = (bet.event_date, getattr(bet, "acca_ticket_id", None) or str(bet.event_date))
        ticket_legs[ticket_key].append(bet)

    lc_stats: dict[int, dict] = defaultdict(lambda: {"tickets": 0, "wins": 0})
    for legs in ticket_legs.values():
        n = len(legs)
        lc_stats[n]["tickets"] += 1
        if all(lg.result_status == "Won" for lg in legs):
            lc_stats[n]["wins"] += 1

    by_leg_count = sorted(
        [
            {
                "leg_count": n,
                "tickets": s["tickets"],
                "wins": s["wins"],
                "hit_rate": round(s["wins"] / s["tickets"] * 100, 1),
            }
            for n, s in lc_stats.items()
        ],
        key=lambda x: x["leg_count"],
    )

    # ── Two-market combos ──────────────────────────────────────────────────
    combo_stats: dict[tuple, dict] = defaultdict(lambda: {"tickets": 0, "wins": 0})
    for legs in ticket_legs.values():
        if len(legs) < 2:
            continue
        ticket_won = all(lg.result_status == "Won" for lg in legs)
        markets = sorted({lg.market_type for lg in legs})
        for mkt_a, mkt_b in combinations(markets, 2):
            combo_stats[(mkt_a, mkt_b)]["tickets"] += 1
            if ticket_won:
                combo_stats[(mkt_a, mkt_b)]["wins"] += 1

    two_market_combos = sorted(
        [
            {
                "market_a": k[0],
                "market_b": k[1],
                "tickets": s["tickets"],
                "wins": s["wins"],
                "hit_rate": round(s["wins"] / s["tickets"] * 100, 1),
            }
            for k, s in combo_stats.items()
            if s["tickets"] >= 2
        ],
        key=lambda x: -x["hit_rate"],
    )[:10]

    return {
        "by_market": by_market,
        "by_leg_count": by_leg_count,
        "two_market_combos": two_market_combos,
    }

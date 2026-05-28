"""
accumulator_generator.py — Automatic accumulator ticket generation with self-learning.

Quality filters applied before search:
  - Minimum quality score (default 0.04) — cuts weak signals
  - Agreement must be "Both" or "Bayesian Only" — "Poisson Only" too uncertain for accas
  - Confidence must be Medium or High (no downgraded "Low" legs)
  - Auto-suppressed (market, league_tier) or rule combinations are excluded

Self-learning (three-layer):
  1. (confidence, market) slice factor — from historical win-rate and ROI per tier/market
  2. source_rule_key factor — from historical performance of the specific Poisson rule
  3. (market, league_tier) factor — NEW: catches league-specific market failures
  Combined: adjusted_quality = raw × (0.55 × conf_market + 0.25 × rule + 0.20 × tier_market)
  All factors default to 1.0 (neutral) until MIN_SAMPLES is reached.

Auto-suppression pre-filter:
  Candidates whose (market, league_tier) or rule_key are in the auto-suppress sets
  are dropped before combination search. This prevents consistently-losing signals
  from ever appearing in accumulator legs regardless of quality score.

Tiered generation:
  mini  — 3–10×  combined odds, 2–3 legs: highest-probability, minimal exposure
  safe  — 10–25× combined odds, 3–5 legs: lower risk, solid confidence
  value — 25–60× combined odds, 4–6 legs: balanced risk-reward
  bold  — 60–100× combined odds, 4–6 legs: high upside, stricter quality filter

Diversity penalties applied during scoring:
  - Market concentration penalty — too many same markets degrade the score
  - League concentration penalty — correlated fixtures from the same league score lower
  - Correlated market pairs (O2.5+BTTS, etc.) — independence penalty

Bonuses:
  - "Both" agreement legs → +0.005 each
  - "High" confidence legs → +0.003 each
  - Negative odds drift (sharp money confirmed) → +0.010 each
  - Tier 1 league → +0.004 per leg (more liquid, sharper markets)
  - Tier 3 league → -0.006 per leg (thin markets, integrity risk)

Leg count preference:
  - Prefers fewer legs at higher individual odds over many legs at low odds
  - Fewer legs = lower correlation risk, better win probability for same combined odds
"""
from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta, timezone
from itertools import combinations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Signal, Fixture
from app.models.learning_proposal import LearningProposal
from app.services.performance_intelligence import compute_performance_weights

logger = logging.getLogger(__name__)

# Minimum quality score for a signal to be considered as an accumulator leg.
MIN_LEG_QUALITY = 0.04

# Agreements accepted for accumulator legs (ordered by strength)
ACCEPTED_AGREEMENTS = {"Both", "Bayesian Only"}

# Confidence tiers accepted (Medium and High only — no downgraded legs)
ACCEPTED_CONFIDENCE = {"High", "Medium"}

# Fixture statuses that mean the match is over — exclude from accumulator candidates.
# Mirrors the FINAL_STATUSES set in the frontend SignalCard.
FINAL_STATUSES = {"FT", "AET", "PEN"}

# Per-market maximum odds ceiling.
# Odds above these values indicate the market itself considers the outcome unlikely
# (implied probability < 50%). Backing these goes against market consensus and
# produces the "high_odds_risk" failure pattern seen in loss analysis.
# Mirrors MARKET_ODDS_CEILING in loss_analysis_agent.py — keep in sync.
MARKET_MAX_ODDS: dict[str, float] = {
    "Home Over 0.5":  2.05,
    "Away Over 0.5":  2.10,
    "Home Over 1.5":  3.00,   # raised from 2.60 — 3.00+ band: +24.1% ROI on 14 settled signals
    "Away Over 1.5":  3.50,   # raised from 2.80 — 3.00+ band: +40.5% ROI on 17 settled signals
    "Over 0.5":       1.50,
    "Over 1.5":       2.20,
    "Over 2.5":       2.40,
    "BTTS Yes":       2.40,
}

# Per-market minimum quality score overrides (applied on top of global MIN_LEG_QUALITY = 0.04).
# Over 1.5 team-scoring markets need higher conviction since the agreement filter
# still admits Bayesian-only signals with marginal quality scores.
MARKET_MIN_QUALITY: dict[str, float] = {
    "Home Over 1.5": 0.15,   # audit: sub-0.15 quality gave borderline ROI; raised gate
    "Away Over 1.5": 0.15,   # audit: same — both/bayesian-only floor raised for quality gate
}

# Team-scoring markets — the markets where a single team must score.
# These are more sensitive to team strength mismatch and bookmaker depth than
# total-goals markets, so they receive additional scrutiny below.
TEAM_SCORING_MARKETS = {
    "Home Over 0.5", "Away Over 0.5",
    "Home Over 1.5", "Away Over 1.5",
    "Home Win to Nil", "Away Win to Nil",
}

# Model-vs-market divergence threshold for team-scoring markets.
# When the model (Bayesian/Poisson) is ≥20% more confident than the market's
# implied probability, the market is signalling doubt that both engines missed.
# In liquid markets (Tier 1, multiple bookmakers) the market is usually right.
MISMATCH_DIVERGENCE_THRESHOLD = 0.20  # fraction (20 percentage points)
MISMATCH_PENALTY_FACTOR       = 0.82  # multiply adjusted_quality by this when mismatch detected

# Tier 3 team-scoring prior.
# In lower-tier leagues, team-scoring markets are inherently less reliable:
# weaker data, thinner odds, less consistent playing styles.
# Apply a static downward prior that corrects upward as settled results accumulate.
TIER3_TEAM_SCORING_FACTOR = 0.85  # 15% quality reduction for Tier 3 team-scoring

# Bookmaker depth gate for team-scoring markets.
# Fewer than 3 bookmakers = thin price discovery, less reliable signal.
MIN_BOOKMAKER_COUNT_TEAM_SCORING = 3
THIN_MARKET_PENALTY_FACTOR       = 0.90  # 10% quality reduction for thin coverage

# Maximum age for a market_suppression LearningProposal to be honoured at read time.
# Proposals older than this are silently skipped even if is_active=True, providing
# a defence-in-depth expiry layer alongside the reactivation monitor in strategy_pipeline.py.
SUPPRESSION_MAX_AGE = timedelta(days=90)

# Hard cap on legs per ticket — tickets beyond 8 legs have negligible win probability
# and serve no useful purpose for a bettor. Truncate to the highest-quality legs.
MAX_LEGS_HARD_CAP = 8

# Win probability warning thresholds — applied after ticket win_probability is computed.
LOW_WIN_PROB_THRESHOLD      = 0.05   # 5%  — show amber warning
VERY_LOW_WIN_PROB_THRESHOLD = 0.02   # 2%  — show red warning, suggest smaller stake


def _build_leg_rationale(sig, market: str) -> str:
    """One-sentence plain-English rationale for why this leg was selected."""
    parts: list[str] = []
    agreement = sig.dual_agreement or ""
    confidence = sig.dual_confidence or "Low"
    if agreement == "Both":
        parts.append("Both engines agree")
    elif agreement == "Bayesian Only":
        parts.append("Market analysis signal")
    elif agreement == "Poisson Only":
        parts.append("Statistical model signal")
    else:
        parts.append("Signal")
    if sig.bayesian_prob:
        parts.append(f"{round(sig.bayesian_prob * 100, 0):.0f}% model probability")
    lam = sig.poisson_lambda_total
    if lam:
        parts.append(f"λ {round(lam, 1)} goals expected")
    parts.append(f"{confidence.lower()} confidence")
    if sig.odds_drift_pct and sig.odds_drift_pct <= -3.0:
        parts.append("line shortening ↓")
    return " · ".join(parts)


# ── Candidate selection ────────────────────────────────────────────────────────

async def _load_candidates(
    db: AsyncSession,
    run_date: date,
    use_performance_weights: bool = True,
    user_id: int | None = None,
    include_finished: bool = False,
) -> list[dict]:
    """
    Load qualifying signals for run_date.
    Applies quality, agreement, confidence, and auto-suppress filters.
    Adjusts quality_score with three-layer historical performance weights.
    Returns best signal per fixture, top 20 by adjusted quality.
    """
    # ── Load learned market odds ceilings from DB ─────────────────────────────
    # Start from the static defaults and override with any accepted proposals
    # that the loss-analysis pipeline has validated and persisted.  Falls back
    # to the static dict silently if the table is absent or the query fails
    # (e.g. first-run before migrations have executed).
    effective_ceilings: dict[str, float] = dict(MARKET_MAX_ODDS)
    suppressed_markets: set[str] = set()
    suppressed_league_keywords: set[str] = set()
    kelly_adj: dict[str, float] = {}   # confidence level → multiplier (Pipeline B)
    try:
        proposal_result = await db.execute(
            select(LearningProposal).where(
                LearningProposal.is_active == True,  # noqa: E712
            )
        )
        _now = datetime.now(timezone.utc)
        for proposal in proposal_result.scalars().all():
            ct = proposal.change_type
            tgt = proposal.target or ""
            val = proposal.proposed_value

            # Silently skip market_suppression proposals that exceed SUPPRESSION_MAX_AGE.
            # This provides read-time defence-in-depth on top of the reactivation monitor
            # in strategy_pipeline.check_suppression_reactivations().
            if ct == "market_suppression":
                created = proposal.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if (_now - created) > SUPPRESSION_MAX_AGE:
                    logger.debug(
                        "Accumulator: skipping stale market suppression for %s (age %d days > %d)",
                        tgt, (_now - created).days, SUPPRESSION_MAX_AGE.days,
                    )
                    continue

            if ct == "market_odds_ceiling" and tgt and val is not None:
                # The LLM sometimes writes combined targets like "Home/Away Over 0.5"
                # or "Home/Away Over 0.5, Home/Away Over 1.5" (slash/comma notation).
                # Expand into the individual market names that signals actually carry.
                # Pattern: "A/B suffix" → ["A suffix", "B suffix"]
                # e.g. "Home/Away Over 0.5" → ["Home Over 0.5", "Away Over 0.5"]
                for part in tgt.split(","):
                    part = part.strip()
                    if "/" in part:
                        slash_parts = [p.strip() for p in part.split("/")]
                        # The last slash-part carries the full "Direction Condition" form
                        base_words = slash_parts[-1].split()
                        if len(base_words) >= 2:
                            suffix = " ".join(base_words[1:])  # e.g. "Over 0.5"
                            for sp in slash_parts:
                                # sp = "Home" or "Away Over 0.5" — take only its first word
                                direction = sp.split()[0]
                                market_name = f"{direction} {suffix}"
                                effective_ceilings[market_name] = val
                                logger.debug("Accumulator: learned ceiling %s → %.2f (expanded from '%s')", market_name, val, tgt)
                        else:
                            effective_ceilings[part] = val
                    else:
                        effective_ceilings[part] = val
                        logger.debug("Accumulator: learned ceiling %s → %.2f", part, val)

            elif ct == "market_suppression" and tgt:
                suppressed_markets.add(tgt)
                logger.debug("Accumulator: learned market suppression → %s", tgt)

            elif ct == "league_suppression" and tgt:
                suppressed_league_keywords.add(tgt.lower())
                logger.debug("Accumulator: learned league suppression → %s", tgt)

            elif ct == "kelly_fraction_adj" and tgt and val is not None:
                kelly_adj[tgt] = float(val)
                logger.debug("Accumulator: learned Kelly adj %s → %.2f×", tgt, val)

    except Exception as e:
        logger.debug("Could not load learning_proposals — using static defaults: %s", e)

    _where = [
        Fixture.event_date == run_date,
        Signal.bayesian_best_odd.isnot(None),
        Signal.dual_quality_score >= MIN_LEG_QUALITY,
        Signal.dual_confidence.in_(list(ACCEPTED_CONFIDENCE)),
        Signal.dual_agreement.in_(list(ACCEPTED_AGREEMENTS)),
        Signal.contradiction.is_(False),
        Signal.bayesian_best_odd > 1.20,
    ]
    if not include_finished:
        # Exclude finished matches — FT/AET/PEN results can't be bet on.
        _where.append(Fixture.status.notin_(list(FINAL_STATUSES)))

    rows = await db.execute(
        select(Signal, Fixture)
        .join(Fixture, Signal.fixture_id == Fixture.id)
        .where(*_where)
        .order_by(Signal.dual_quality_score.desc().nullslast())
    )
    all_rows = rows.all()

    # Load historical performance weights — used to adjust quality scores.
    # Falls back gracefully when there is no historical data (all factors = 1.0).
    perf_weights = None
    if use_performance_weights:
        try:
            perf_weights = await asyncio.wait_for(
                compute_performance_weights(db, user_id=user_id),
                timeout=1.0,
            )
        except Exception:
            perf_weights = None

    # Best signal per fixture (first occurrence is highest raw quality due to sort)
    seen_fixtures: set[int] = set()
    candidates = []
    for sig, fix in all_rows:
        market = sig.market or ""
        league_tier = fix.league_tier or 0
        # Signal rows carry the Poisson rule on `poisson_rule_key`, while tracked
        # bets persist the same concept as `source_rule_key`. Accept either so the
        # accumulator path remains compatible across in-flight schema refactors.
        rule_key = (
            getattr(sig, "source_rule_key", None)
            or getattr(sig, "poisson_rule_key", None)
            or ""
        )

        # Per-market odds ceiling — reject signals where the market itself prices
        # the outcome as unlikely (implied prob < 50%). Uses effective_ceilings which
        # merges the static MARKET_MAX_ODDS defaults with any DB-persisted learned
        # proposals from the loss-analysis pipeline (tighter = better-calibrated).
        market_ceiling = effective_ceilings.get(market)
        raw_odds = sig.bayesian_best_odd or 0.0
        if market_ceiling and raw_odds > market_ceiling:
            continue

        # Pipeline B: market suppression — drop markets flagged by Strategy pipeline
        if market in suppressed_markets:
            logger.debug("Accumulator: dropping %s (learned market suppression)", market)
            continue

        # Pipeline B: league suppression — drop leagues flagged by Strategy pipeline
        fixture_league_lower = (fix.league or "").lower()
        if any(kw in fixture_league_lower for kw in suppressed_league_keywords):
            logger.debug(
                "Accumulator: dropping %s / %s (learned league suppression)",
                fix.league, market,
            )
            continue

        # Auto-suppress: skip signals in underperforming (market, tier) or rule combos.
        if perf_weights and perf_weights.should_suppress(market, league_tier, rule_key):
            continue

        # Per-market quality floor — Over 1.5 markets need higher conviction than the
        # global MIN_LEG_QUALITY = 0.04 floor since Bayesian-only signals at q < 0.15
        # in these markets showed marginal or negative accumulator ROI in the audit.
        raw_quality = sig.dual_quality_score or 0.0
        market_min_q = MARKET_MIN_QUALITY.get(market, MIN_LEG_QUALITY)
        if raw_quality < market_min_q:
            continue

        if sig.fixture_id in seen_fixtures:
            continue
        seen_fixtures.add(sig.fixture_id)

        # Layer 1: (confidence, market) performance factor
        conf_market_factor = (
            perf_weights.factor_for(sig.dual_confidence or "", market)
            if perf_weights else 1.0
        )
        # Layer 2: Poisson rule performance factor
        rule_factor = (
            perf_weights.factor_for_rule(rule_key)
            if perf_weights else 1.0
        )
        # Layer 3: (market, league_tier) performance factor — NEW
        tier_factor = (
            perf_weights.factor_for_market_tier(market, league_tier)
            if perf_weights else 1.0
        )

        # Combined factor: conf+market primary (55%), rule secondary (25%), tier tertiary (20%)
        combined_factor = (
            conf_market_factor * 0.55
            + rule_factor * 0.25
            + tier_factor * 0.20
        )

        adjusted_quality = round(raw_quality * combined_factor, 6)

        # Pipeline B: Kelly fraction adjustment — reduce quality score for underperforming
        # confidence levels so they rank lower and are less likely to appear in accumulators.
        kelly_mult = kelly_adj.get(sig.dual_confidence or "", 1.0)
        if kelly_mult < 1.0:
            adjusted_quality = round(adjusted_quality * kelly_mult, 6)

        # Calibration downgrade: if this tier is overconfident, apply a soft penalty
        cal_penalty = 0.0
        if perf_weights:
            cal = perf_weights.calibration.get(sig.dual_confidence or "")
            if cal and cal.is_overconfident:
                cal_penalty = cal.calibration_error * 0.5  # proportional penalty
                adjusted_quality = max(0.0, adjusted_quality * (1.0 - cal_penalty))

        # ── Mismatch penalty (team-scoring markets only) ───────────────────────
        # When model probability significantly exceeds market-implied probability,
        # the market is expressing doubt both engines missed — most often because
        # the selected team is the weaker side of a mismatch (e.g. Zwolle at home
        # vs Feyenoord: model said 72.7% but market priced it at 43.7% implied).
        # In liquid Tier 1 markets with multiple bookmakers, the market is usually
        # right about team-scoring suppression. We penalise the quality score so
        # the pick ranks lower and is less likely to appear in accumulators.
        mismatch_penalty = 1.0
        if market in TEAM_SCORING_MARKETS and raw_odds > 0:
            market_implied_prob = 1.0 / raw_odds
            model_prob = max(sig.bayesian_prob or 0.0, sig.poisson_prob or 0.0)
            divergence = model_prob - market_implied_prob
            if divergence >= MISMATCH_DIVERGENCE_THRESHOLD:
                mismatch_penalty = MISMATCH_PENALTY_FACTOR
                adjusted_quality = round(adjusted_quality * mismatch_penalty, 6)

        # ── Tier 3 team-scoring prior ──────────────────────────────────────────
        # Lower-tier leagues have thinner data, less consistent playing styles,
        # and more frequent 0-0 results. Apply a downward prior that self-corrects
        # upward as settled results accumulate in performance_intelligence.
        tier3_penalty = 1.0
        if market in TEAM_SCORING_MARKETS and league_tier >= 3:
            tier3_penalty = TIER3_TEAM_SCORING_FACTOR
            adjusted_quality = round(adjusted_quality * tier3_penalty, 6)

        # ── Bookmaker depth gate (team-scoring markets) ────────────────────────
        # Fewer than 3 bookmakers means thin price discovery: odds are less
        # reliable, market-implied probability is noisy. Reduce quality so these
        # picks rank below well-covered alternatives; don't exclude outright
        # since some valid picks come from niche markets.
        bk_count = sig.bayesian_bookmaker_count or 0
        depth_penalty = 1.0
        if market in TEAM_SCORING_MARKETS and 0 < bk_count < MIN_BOOKMAKER_COUNT_TEAM_SCORING:
            depth_penalty = THIN_MARKET_PENALTY_FACTOR
            adjusted_quality = round(adjusted_quality * depth_penalty, 6)

        # ── Away Over 0.5 outperformance preference ───────────────────────────
        # Audit: Away Over 0.5 delivers +55.5% ROI vs Home Over 0.5 +35.7% ROI
        # at identical confidence tiers and hit rates (~76% vs ~72%). When both
        # are available for the same fixture, seen_fixtures deduplication keeps
        # only the higher-quality signal — this nudge ensures Away is preferred
        # when both scores are close, without overriding a clearly superior Home.
        away_preference = 1.0
        if market == "Away Over 0.5":
            away_preference = 1.06  # +6% quality lift
            adjusted_quality = round(adjusted_quality * away_preference, 6)

        candidates.append({
            "signal_id": sig.id,
            "fixture_id": sig.fixture_id,
            "match_name": f"{fix.home_team} vs {fix.away_team}",
            "home_team": fix.home_team,
            "away_team": fix.away_team,
            "league": fix.league or "Unknown",
            "league_tier": league_tier,
            "kickoff_at": fix.kickoff_at.isoformat() if fix.kickoff_at else None,
            "market": market,
            "odds": sig.bayesian_best_odd,
            "bookmaker": sig.bayesian_bookmaker or "Manual",
            "confidence": sig.dual_confidence,
            "agreement": sig.dual_agreement,
            "rationale": _build_leg_rationale(sig, market),
            "quality_score": adjusted_quality,
            "raw_quality_score": raw_quality,
            "conf_market_factor": round(conf_market_factor, 3),
            "rule_factor": round(rule_factor, 3),
            "tier_factor": round(tier_factor, 3),
            "performance_factor": round(combined_factor, 3),
            "calibration_penalty": round(cal_penalty, 3),
            "mismatch_penalty": round(mismatch_penalty, 3),
            "tier3_penalty": round(tier3_penalty, 3),
            "depth_penalty": round(depth_penalty, 3),
            "away_preference": round(away_preference, 3),
            "bookmaker_count": bk_count,
            "bayesian_prob": sig.bayesian_prob,
            "poisson_prob": sig.poisson_prob,
            "recommended_stake_pct": sig.dual_recommended_stake_pct,
            "event_date": fix.event_date.isoformat() if fix.event_date else None,
            "odds_drift_pct": sig.odds_drift_pct,
            "source_rule_key": rule_key,
        })

    # Re-sort by adjusted quality so the top-30 slice reflects learned performance.
    candidates.sort(key=lambda c: c["quality_score"], reverse=True)
    return candidates[:30]


# ── Correlation table ──────────────────────────────────────────────────────────

_CORRELATED_PAIRS: list[frozenset] = [
    frozenset({"Over 2.5", "BTTS Yes"}),
    frozenset({"Over 3.5", "BTTS Yes"}),
    frozenset({"Over 2.5", "Over 3.5"}),
    frozenset({"Under 2.5", "Under 3.5"}),
    frozenset({"Over 1.5", "BTTS Yes"}),
    # Win + clean-sheet pairs — highly correlated
    frozenset({"Home Win", "Home Win to Nil"}),
    frozenset({"Away Win", "Away Win to Nil"}),
    # Home win + BTTS No: directional overlap on same "clean sheet" outcome
    frozenset({"Home Win", "BTTS No"}),
]
_CORRELATION_PENALTY = 0.020


# ── Scoring ────────────────────────────────────────────────────────────────────

def _score_combo(combo: tuple[dict, ...]) -> float:
    """
    Score a combination. Higher is better.

    Base: average adjusted quality score of legs.

    Penalties:
      - Market concentration: >2 legs with the same market type → -0.01 per extra
      - League concentration: >2 legs from the same league → -0.005 per extra
      - Leg count: slight penalty for more legs (prefer tighter, higher-quality tickets)
      - Correlated market pairs: -0.015 per pair
      - Tier 3 league legs: -0.006 each (thin markets, integrity risk)
      - Positive odds drift > 3% (market moving against us): -0.008 each

    Bonuses:
      - "Both" agreement legs: +0.005 each
      - "High" confidence legs: +0.003 each
      - Negative odds drift < -2% (sharp money confirmed): +0.010 each
      - Tier 1 league legs: +0.004 each (liquid, sharp markets)
    """
    n = len(combo)
    avg_q = sum(c["quality_score"] for c in combo) / n

    market_counts: dict[str, int] = {}
    for c in combo:
        market_counts[c["market"]] = market_counts.get(c["market"], 0) + 1
    market_penalty = sum(max(0, v - 2) * 0.010 for v in market_counts.values())

    league_counts: dict[str, int] = {}
    for c in combo:
        league_counts[c["league"]] = league_counts.get(c["league"], 0) + 1
    league_penalty = sum(max(0, v - 2) * 0.005 for v in league_counts.values())

    # Exponential leg count penalty — penalises 6-leg tickets far more than 4-leg ones.
    # max(0, n-4)**1.5 * 0.003: 4 legs→0, 5 legs→0.003, 6 legs→0.0085, 7 legs→0.016
    leg_penalty = (max(0, n - 4) ** 1.5) * 0.003

    # Time-spread bonus — legs spread across different kickoff hours reduce settlement
    # correlation (one injured player or referee decision can't kill the whole ticket).
    kickoff_hours: set[int] = set()
    for c in combo:
        event_date = c.get("event_date")
        ko = c.get("kickoff_at")
        if ko:
            try:
                from datetime import datetime as _dt
                hour = _dt.fromisoformat(ko.replace("Z", "+00:00")).hour
                kickoff_hours.add(hour)
            except (ValueError, AttributeError):
                pass
    time_spread_bonus = 0.004 if len(kickoff_hours) >= 3 else (0.002 if len(kickoff_hours) == 2 else 0.0)

    markets_in_combo = set(c["market"] for c in combo)
    correlation_penalty = sum(
        _CORRELATION_PENALTY for pair in _CORRELATED_PAIRS if pair.issubset(markets_in_combo)
    )

    # Tier-based scoring — NEW
    tier_bonus = sum(0.004 for c in combo if c.get("league_tier") == 1)
    tier_penalty = sum(0.006 for c in combo if c.get("league_tier") == 3)

    agreement_bonus = sum(0.005 for c in combo if c["agreement"] == "Both")
    confidence_bonus = sum(0.003 for c in combo if c["confidence"] == "High")

    # Enhanced CLV/drift scoring — strengthened from 0.008 → 0.010
    drift_bonus = sum(
        0.010 for c in combo
        if c.get("odds_drift_pct") is not None and c["odds_drift_pct"] < -2.0
    )
    # Penalty for positive drift (market shortening against our pick) — NEW
    drift_penalty = sum(
        0.008 for c in combo
        if c.get("odds_drift_pct") is not None and c["odds_drift_pct"] > 3.0
    )

    score = (
        avg_q
        - market_penalty
        - league_penalty
        - leg_penalty
        - correlation_penalty
        - tier_penalty
        - drift_penalty
        + tier_bonus
        + agreement_bonus
        + confidence_bonus
        + drift_bonus
        + time_spread_bonus
    )
    return round(score, 6)


# ── Combination search ─────────────────────────────────────────────────────────

def _find_combos(
    candidates: list[dict],
    min_odds: float,
    max_odds: float,
    min_legs: int = 4,
    max_legs: int = 6,
    top_n: int = 3,
) -> list[dict]:
    """Search all valid combinations in log-space, return top_n by score."""
    # Hard cap — never build a ticket with more than MAX_LEGS_HARD_CAP legs.
    max_legs = min(max_legs, MAX_LEGS_HARD_CAP)
    if len(candidates) < min_legs:
        return []

    log_min = math.log(min_odds)
    log_max = math.log(max_odds)

    results: list[tuple[float, tuple]] = []

    for n in range(min_legs, max_legs + 1):
        for combo in combinations(candidates, n):
            if len({c["fixture_id"] for c in combo}) < n:
                continue
            log_odds = sum(math.log(c["odds"]) for c in combo)
            if log_min <= log_odds <= log_max:
                results.append((_score_combo(combo), combo))

    results.sort(key=lambda x: x[0], reverse=True)

    seen_fixture_sets: set[frozenset] = set()
    unique: list[dict] = []

    for score, combo in results:
        fset = frozenset(c["fixture_id"] for c in combo)
        if fset in seen_fixture_sets:
            continue
        seen_fixture_sets.add(fset)

        combined_odds = math.prod(c["odds"] for c in combo)
        markets = list({c["market"] for c in combo})
        unique.append({
            "name": f"Auto Acca — {len(combo)} legs @ {combined_odds:.1f}",
            "combined_odds": round(combined_odds, 2),
            "avg_quality": round(score, 4),
            "market_mix": markets,
            "legs": list(combo),
        })
        if len(unique) >= top_n:
            break

    return unique


# ── Estimated win probability ──────────────────────────────────────────────────

def estimate_win_probability(suggestion: dict) -> float:
    """
    Estimate ticket win probability using the model's derived probability per leg.
    Falls back to bookmaker-implied (1/odds * 0.95) when no model probability is stored.
    Model probability is already margin-free, so no overround discount is applied to it;
    the bookmaker fallback retains the 0.95 discount since it contains their margin.
    """
    prob = 1.0
    for leg in suggestion["legs"]:
        model_prob = leg.get("bayesian_prob")
        if model_prob and model_prob > 0:
            prob *= model_prob
        else:
            prob *= (1.0 / leg["odds"]) * 0.95
    return round(prob, 4)


# ── Public API ─────────────────────────────────────────────────────────────────

MAX_COMBINED_ODDS = 100.0

# Per-tier configuration: odds range + preferred leg count window
TIER_CONFIG: dict[str, dict] = {
    "mini":  {"lo": 3.0,   "hi": 10.0,  "min_legs": 2, "max_legs": 3},
    "safe":  {"lo": 10.0,  "hi": 25.0,  "min_legs": 3, "max_legs": 5},
    "value": {"lo": 25.0,  "hi": 60.0,  "min_legs": 4, "max_legs": 6},
    "bold":  {"lo": 60.0,  "hi": 100.0, "min_legs": 4, "max_legs": 6},
}

# Legacy flat tiers dict (kept for backwards compatibility with generate_suggestions)
TIERS: dict[str, tuple[float, float]] = {k: (v["lo"], v["hi"]) for k, v in TIER_CONFIG.items()}


async def generate_suggestions(
    db: AsyncSession,
    run_date: date,
    min_odds: float = 35.0,
    max_odds: float = 60.0,
    top_n: int = 3,
    use_performance_weights: bool = True,
    user_id: int | None = None,
) -> list[dict]:
    """
    Generate accumulator suggestions for run_date with combined odds in [min_odds, max_odds].
    Applies quality/confidence/diversity/auto-suppress filters.
    Returns suggestion dicts (not saved to DB).
    """
    candidates = await _load_candidates(
        db,
        run_date,
        use_performance_weights=use_performance_weights,
        user_id=user_id,
    )
    if len(candidates) < 2:
        return []

    max_odds = min(max_odds, MAX_COMBINED_ODDS)
    if min_odds > max_odds:
        min_odds = max_odds * 0.5

    suggestions = _find_combos(candidates, min_odds, max_odds, top_n=top_n)

    for s in suggestions:
        win_prob = estimate_win_probability(s)
        s["win_probability"] = win_prob
        s["low_win_prob_warning"] = win_prob < LOW_WIN_PROB_THRESHOLD
        s["very_low_win_prob"] = win_prob < VERY_LOW_WIN_PROB_THRESHOLD

    return suggestions


async def generate_tiered_suggestions(
    db: AsyncSession,
    run_date: date,
    top_n: int = 2,
    use_performance_weights: bool = True,
    user_id: int | None = None,
) -> dict[str, list[dict]]:
    """
    Generate accumulator suggestions split into mini / safe / value / bold tiers.
    Each tier searches a distinct odds band and leg-count window so tickets are
    truly different in risk profile.
    Returns a dict keyed by tier name; each value is a list of up to top_n suggestions.
    """
    candidates = await _load_candidates(
        db,
        run_date,
        use_performance_weights=use_performance_weights,
        user_id=user_id,
    )

    output: dict[str, list[dict]] = {}
    for tier_name, cfg in TIER_CONFIG.items():
        combos = _find_combos(
            candidates,
            cfg["lo"],
            cfg["hi"],
            min_legs=cfg["min_legs"],
            max_legs=cfg["max_legs"],
            top_n=top_n,
        )
        for s in combos:
            win_prob = estimate_win_probability(s)
            s["win_probability"] = win_prob
            s["low_win_prob_warning"] = win_prob < LOW_WIN_PROB_THRESHOLD
            s["very_low_win_prob"] = win_prob < VERY_LOW_WIN_PROB_THRESHOLD
            s["tier"] = tier_name
        output[tier_name] = combos

    return output


def _fit_bucket_within_odds_ceiling(legs: list[dict], max_combined_odds: float = 100.0) -> tuple[list[dict], int]:
    """
    Trim the highest-impact legs until the ticket fits the combined-odds ceiling.
    Returns the kept legs plus the number of removed legs.
    """
    kept = list(legs)
    removed = 0

    def combined_odds(items: list[dict]) -> float:
        return math.prod(leg["odds"] for leg in items) if items else 1.0

    while len(kept) > 2 and combined_odds(kept) > max_combined_odds:
        leg_to_remove = max(
            kept,
            key=lambda leg: (
                leg.get("odds") or 0.0,
                -(leg.get("quality_score") or 0.0),
            ),
        )
        kept.remove(leg_to_remove)
        removed += 1

    return kept, removed


def _build_ticket_notes(suggestion: dict) -> list[str]:
    legs = suggestion.get("legs", [])
    leg_count = len(legs)
    notes: list[str] = []
    if leg_count >= 8:
        notes.append("Large multi-leg slip. Keep stake small and treat it as an upside ticket.")
    elif suggestion.get("combined_odds", 0) <= 25:
        notes.append("Shorter odds band with a better hit chance than the higher-risk slips.")

    market_mix = suggestion.get("market_mix") or []
    if leg_count >= 4 and len(market_mix) <= max(1, leg_count // 3):
        notes.append("Market mix is concentrated, so correlation risk is higher than usual.")

    both_count = sum(1 for leg in legs if leg.get("agreement") == "Both")
    if both_count == leg_count and leg_count > 0:
        notes.append("Every leg has both-engine agreement.")
    elif both_count >= max(2, leg_count // 2):
        notes.append("Most legs have both-engine agreement, which keeps the slip aligned with the strongest signals.")

    tier1_count = sum(1 for leg in legs if leg.get("league_tier") == 1)
    if tier1_count >= max(2, leg_count // 2):
        notes.append("This ticket leans on Tier 1 leagues, which are usually the most liquid and reliable.")

    return notes[:3]


async def generate_rank_bucket_suggestions(
    db: AsyncSession,
    run_date: date,
    use_performance_weights: bool = True,
    user_id: int | None = None,
) -> list[dict]:
    """
    Build the ranked accumulator tickets from the current board:
    the full top 10 list, plus the strongest five-leg ticket selected from
    inside that top-10 board.
    """
    candidates = await _load_candidates(
        db,
        run_date,
        use_performance_weights=use_performance_weights,
        user_id=user_id,
    )
    if len(candidates) < 2:
        return []

    top_ten_legs = candidates[:MAX_LEGS_HARD_CAP]
    if len(top_ten_legs) < 2:
        return []

    best_five_combo = _find_combos(
        top_ten_legs,
        1.0,
        MAX_COMBINED_ODDS,
        min_legs=5,
        max_legs=5,
        top_n=1,
    )
    if not best_five_combo:
        best_five_combo = _find_combos(
            top_ten_legs,
            1.0,
            10000.0,
            min_legs=5,
            max_legs=5,
            top_n=1,
        )
    best_five_legs = best_five_combo[0]["legs"] if best_five_combo else top_ten_legs[:5]

    bucket_specs = [
        (
            "top10",
            "Top 10 Ranked Ticket",
            "The full headline ranked board for the selected date.",
            top_ten_legs,
            False,
        ),
        (
            "best5",
            "Best 5 From Top 10",
            "The strongest five-leg betting ticket selected from inside the top-10 ranked board.",
            best_five_legs,
            False,
        ),
    ]

    suggestions: list[dict] = []
    for bucket_key, name, rationale, source_legs, enforce_odds_ceiling in bucket_specs:
        if len(source_legs) < 2:
            continue
        if enforce_odds_ceiling:
            legs, trimmed_count = _fit_bucket_within_odds_ceiling(source_legs, MAX_COMBINED_ODDS)
        else:
            legs, trimmed_count = list(source_legs), 0
        if len(legs) < 2:
            continue
        combined_odds = round(math.prod(leg["odds"] for leg in legs), 2)
        avg_quality = round(sum(leg["quality_score"] for leg in legs) / len(legs), 4)
        suggestion = {
            "bucket_key": bucket_key,
            "bucket_label": name,
            "bucket_size": len(source_legs),
            "trimmed_count": trimmed_count,
            "rationale": rationale,
            "combined_odds": combined_odds,
            "avg_quality": avg_quality,
            "market_mix": list({leg["market"] for leg in legs}),
            "legs": list(legs),
        }
        suggestion["notes"] = _build_ticket_notes(suggestion)
        win_prob = estimate_win_probability(suggestion)
        suggestion["win_probability"] = win_prob
        suggestion["low_win_prob_warning"] = win_prob < LOW_WIN_PROB_THRESHOLD
        suggestion["very_low_win_prob"] = win_prob < VERY_LOW_WIN_PROB_THRESHOLD
        suggestions.append(suggestion)

    return suggestions

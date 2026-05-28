"""
strategy_pipeline.py — Pipeline B: Signal Analyst → Strategy Agent → Risk Agent

Runs after every settlement batch in parallel with loss_analysis_agent (Pipeline A).
Analyses ALL settled bets (wins + losses) over the last 30 days, proposes broader
strategic rule changes, and stress-tests them before persisting to LearningProposal.

Pipeline A (loss_analysis_agent) handles:
  market_odds_ceiling, min_probability — fine-grained threshold tuning from losses only.

Pipeline B (this file) handles:
  market_suppression   — suppress a consistently-losing market in the accumulator
  league_suppression   — suppress a consistently-losing league in the accumulator
  kelly_fraction_adj   — reduce Kelly stake multiplier for a confidence level
  min_prob_by_agreement — raise min probability required for a low-hit agreement type

Both pipelines write to LearningProposal with their own change_type namespace so they
never overwrite each other's slots.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.bet import TrackedBet
from app.models.fixture import Fixture
from app.models.learning_proposal import LearningProposal

logger = logging.getLogger("titibet.strategy_pipeline")

# ── Constants ──────────────────────────────────────────────────────────────────

ANALYSIS_WINDOW_DAYS = 30       # Signal Analyst lookback
BACKTEST_WINDOW_DAYS = 60       # Risk Agent backtest window

# Minimum sample sizes — tiered by decision severity.
# Suppressions are hard blocks that persist until manually reversed; they need
# a much larger evidence base than soft adjustments like Kelly fraction tweaks.
# Root cause of Away Over 0.5 wrong-suppression: 21 tracked bets passed the old
# MIN_BETS_FOR_PROPOSAL=5 and the 8pp win-rate gap check, but 21 bets ≠ reliable.
MIN_BETS_FOR_PROPOSAL    = 10   # soft adjustments (kelly_fraction_adj, min_prob_by_agreement)
MIN_BETS_FOR_SUPPRESSION = 30   # hard blocks (market_suppression, league_suppression)

# Explicit whitelist of change_types that the accumulator_generator actually consumes.
# The LLM (llama-3.1-8b-instant) sometimes hallucinates plausible-sounding types like
# "quality_threshold", "tier_suppression", "rule_disable" etc. that no downstream code
# reads. Whitelisting here means the Risk Agent fast-rejects them before any DB write.
VALID_CHANGE_TYPES = frozenset({
    "market_suppression",
    "league_suppression",
    "kelly_fraction_adj",
    "min_prob_by_agreement",
})

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"

MAX_LLM_RETRIES = 3
LLM_RETRY_DELAYS = [2, 5, 10]

# Suppression reactivation thresholds.
# A market_suppression proposal is deactivated (market reactivated) when:
#   - At least REACTIVATION_MIN_BETS bets have settled on that market SINCE the
#     suppression was applied, AND the post-suppression ROI exceeds REACTIVATION_ROI_THRESHOLD.
# OR
#   - The suppression proposal is older than SUPPRESSION_HARD_EXPIRY_DAYS regardless of ROI.
REACTIVATION_MIN_BETS         = 15      # minimum post-suppression sample before evaluating ROI
REACTIVATION_LOOKBACK_BETS    = 30      # max bets to examine (most recent post-suppression)
REACTIVATION_ROI_THRESHOLD    = -0.02   # ROI > -2% → market has recovered
SUPPRESSION_HARD_EXPIRY_DAYS  = 90      # deactivate regardless of ROI after this age


# ── Data structures ────────────────────────────────────────────────────────────

@dataclasses.dataclass
class MarketStats:
    market: str
    n_bets: int
    win_rate: float
    avg_odds: float
    roi: float


@dataclasses.dataclass
class GroupStats:
    key: str
    n_bets: int
    win_rate: float


@dataclasses.dataclass
class PerformanceReport:
    overall_win_rate: float
    overall_roi: float
    n_bets_total: int
    by_market: list[MarketStats]
    by_confidence: dict[str, GroupStats]
    by_league: dict[str, GroupStats]         # top worst leagues by name
    worst_markets: list[str]                 # markets with ROI < overall - 10%
    worst_confidence_levels: list[str]       # confidence levels underperforming
    analysis_window_days: int = ANALYSIS_WINDOW_DAYS


@dataclasses.dataclass
class StrategyPipelineReport:
    bets_analysed: int
    overall_win_rate: float
    proposals_generated: int
    proposals_accepted: int
    accepted_proposals: list[dict]
    rejected_proposals: list[dict]
    error: Optional[str] = None


# ── Agent 5: Signal Analyst ────────────────────────────────────────────────────

async def run_signal_analyst(db: AsyncSession) -> PerformanceReport | None:
    """
    Pure-Python statistical analysis of all settled bets over the last N days.
    Joins TrackedBet with Fixture to get league_tier for richer analysis.
    No LLM call — just arithmetic on DB data.
    """
    cutoff = date.today() - timedelta(days=ANALYSIS_WINDOW_DAYS)

    rows = await db.execute(
        select(TrackedBet, Fixture)
        .outerjoin(Fixture, TrackedBet.fixture_id == Fixture.id)
        .where(
            TrackedBet.result_status.in_(["Won", "Lost"]),
            TrackedBet.event_date >= cutoff,
        )
    )
    pairs = rows.all()

    if not pairs:
        logger.info("Signal Analyst: no settled bets in last %d days", ANALYSIS_WINDOW_DAYS)
        return None

    bets_and_fixtures = [(tb, fix) for tb, fix in pairs]
    n_total = len(bets_and_fixtures)
    n_won = sum(1 for tb, _ in bets_and_fixtures if tb.result_status == "Won")
    overall_win_rate = n_won / n_total if n_total else 0.0

    # Overall ROI using odds and stake
    total_staked = sum(tb.recommended_stake_pct or 1.0 for tb, _ in bets_and_fixtures)
    total_return = sum(
        (tb.odds - 1.0) * (tb.recommended_stake_pct or 1.0)
        for tb, _ in bets_and_fixtures
        if tb.result_status == "Won" and tb.odds
    )
    overall_roi = (total_return - total_staked) / total_staked if total_staked else 0.0

    # ── By market_type ─────────────────────────────────────────────────────────
    by_market_raw: dict[str, list] = {}
    for tb, _ in bets_and_fixtures:
        key = tb.market_type or "unknown"
        by_market_raw.setdefault(key, []).append(tb)

    by_market: list[MarketStats] = []
    for market, mbets in by_market_raw.items():
        mn = len(mbets)
        mw = sum(1 for b in mbets if b.result_status == "Won")
        mstaked = sum(b.recommended_stake_pct or 1.0 for b in mbets)
        mreturn = sum(
            (b.odds - 1.0) * (b.recommended_stake_pct or 1.0)
            for b in mbets if b.result_status == "Won" and b.odds
        )
        mroi = (mreturn - mstaked) / mstaked if mstaked else 0.0
        avg_odds = sum(b.odds or 2.0 for b in mbets) / mn
        by_market.append(MarketStats(
            market=market, n_bets=mn,
            win_rate=mw / mn,
            avg_odds=round(avg_odds, 2),
            roi=round(mroi, 4),
        ))
    by_market.sort(key=lambda x: x.roi)  # worst ROI first

    # ── By dual_confidence ─────────────────────────────────────────────────────
    by_confidence: dict[str, GroupStats] = {}
    for conf in ["High", "Medium", "Low"]:
        cbets = [tb for tb, _ in bets_and_fixtures if tb.dual_confidence == conf]
        if cbets:
            cw = sum(1 for b in cbets if b.result_status == "Won")
            by_confidence[conf] = GroupStats(key=conf, n_bets=len(cbets), win_rate=cw / len(cbets))

    # ── By league (bottom 10 worst win-rate leagues) ───────────────────────────
    by_league_raw: dict[str, list] = {}
    for tb, _ in bets_and_fixtures:
        key = (tb.league or "unknown").strip()
        by_league_raw.setdefault(key, []).append(tb)

    by_league: dict[str, GroupStats] = {}
    for league, lbets in by_league_raw.items():
        if len(lbets) >= MIN_BETS_FOR_PROPOSAL:
            lw = sum(1 for b in lbets if b.result_status == "Won")
            by_league[league] = GroupStats(key=league, n_bets=len(lbets), win_rate=lw / len(lbets))

    # Sort by win rate ascending (worst first), keep top 10 for prompt context
    worst_leagues_sorted = sorted(by_league.values(), key=lambda x: x.win_rate)[:10]
    by_league_worst = {gs.key: gs for gs in worst_leagues_sorted}

    # ── Derived signals ────────────────────────────────────────────────────────
    # worst_markets feeds into market_suppression proposals → use the higher bar.
    worst_markets = [
        m.market for m in by_market
        if m.n_bets >= MIN_BETS_FOR_SUPPRESSION and m.roi < overall_roi - 0.10
    ]
    # worst_confidence_levels feeds into kelly_fraction_adj → soft bar is fine.
    worst_confidence_levels = [
        c for c, gs in by_confidence.items()
        if gs.n_bets >= MIN_BETS_FOR_PROPOSAL and gs.win_rate < overall_win_rate - 0.08
    ]

    logger.info(
        "Signal Analyst: %d bets, %.1f%% win rate, %.1f%% ROI — "
        "%d underperforming markets, %d underperforming confidence levels",
        n_total, overall_win_rate * 100, overall_roi * 100,
        len(worst_markets), len(worst_confidence_levels),
    )

    return PerformanceReport(
        overall_win_rate=round(overall_win_rate, 4),
        overall_roi=round(overall_roi, 4),
        n_bets_total=n_total,
        by_market=by_market,
        by_confidence=by_confidence,
        by_league=by_league_worst,
        worst_markets=worst_markets,
        worst_confidence_levels=worst_confidence_levels,
    )


# ── Agent 6: Strategy Agent ────────────────────────────────────────────────────

_STRATEGY_SYSTEM = """\
You are a football betting strategy analyst. You receive performance statistics
from a live signals platform and propose data-backed rule changes to improve ROI.
Only propose changes where the data shows clear underperformance.
Respond ONLY with a JSON object containing a single key "proposals" — an array of proposals.
"""


async def run_strategy_agent(report: PerformanceReport) -> list[dict]:
    """
    Calls Groq LLM with the performance report and returns a list of strategic proposals.
    Falls back to empty list if Groq is unavailable or the call fails.
    """
    settings = get_settings()
    if not settings.groq_api_key:
        logger.info("Strategy Agent: Groq key not set — skipping LLM proposals")
        return []

    market_rows = [
        {
            "market": m.market,
            "n_bets": m.n_bets,
            "win_rate": round(m.win_rate, 3),
            "roi": round(m.roi, 3),
            "avg_odds": m.avg_odds,
        }
        for m in report.by_market
    ]
    conf_rows = {
        k: {"n_bets": gs.n_bets, "win_rate": round(gs.win_rate, 3)}
        for k, gs in report.by_confidence.items()
    }
    league_rows = [
        {"league": gs.key, "n_bets": gs.n_bets, "win_rate": round(gs.win_rate, 3)}
        for gs in sorted(report.by_league.values(), key=lambda x: x.win_rate)
    ]

    user_msg = f"""Performance data — last {report.analysis_window_days} days, {report.n_bets_total} bets:
Overall win rate: {report.overall_win_rate:.1%}   Overall ROI: {report.overall_roi:.1%}

By market (worst ROI first):
{json.dumps(market_rows, indent=2)}

By confidence level:
{json.dumps(conf_rows, indent=2)}

Worst leagues by win rate (min {MIN_BETS_FOR_PROPOSAL} bets):
{json.dumps(league_rows, indent=2)}

Propose up to 5 concrete rule changes backed by the data above.
For market_suppression and league_suppression: only propose for groups with at least {MIN_BETS_FOR_SUPPRESSION} bets.
For kelly_fraction_adj and min_prob_by_agreement: only propose for groups with at least {MIN_BETS_FOR_PROPOSAL} bets.

Each proposal must have exactly these fields:
- change_type: one of "market_suppression", "league_suppression", "kelly_fraction_adj", "min_prob_by_agreement"
- target: market name, league name, confidence level (High/Medium/Low), or agreement type
- proposed_value: float — for kelly_fraction_adj: multiplier 0.1-0.9; for min_prob_by_agreement: probability 0.5-0.85; for suppressions: 1.0
- rationale: one sentence max
- confidence: "High", "Medium", or "Low"

Return: {{"proposals": [...]}}"""

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": _STRATEGY_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "max_tokens": 800,
    }

    last_exc: Optional[Exception] = None
    for attempt in range(MAX_LLM_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(
                    GROQ_API_URL,
                    headers={
                        "Authorization": f"Bearer {settings.groq_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(raw)
                proposals = parsed.get("proposals", [])
                logger.info("Strategy Agent generated %d proposals", len(proposals))
                return proposals if isinstance(proposals, list) else []
        except Exception as exc:
            last_exc = exc
            is_retriable = (
                isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                or (hasattr(exc, "status_code") and exc.status_code in (429, 500, 502, 503, 504))
            )
            if is_retriable and attempt < MAX_LLM_RETRIES - 1:
                delay = LLM_RETRY_DELAYS[attempt]
                logger.warning(
                    "Strategy Agent Groq call failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, MAX_LLM_RETRIES, delay, exc,
                )
                await asyncio.sleep(delay)
                continue
            break

    logger.error("Strategy Agent Groq call failed after %d attempts: %s", MAX_LLM_RETRIES, last_exc)
    return []


# ── Agent 7: Risk Agent ────────────────────────────────────────────────────────

async def run_risk_agent(
    db: AsyncSession,
    proposals: list[dict],
    report: PerformanceReport,
) -> tuple[list[dict], list[dict]]:
    """
    Backtests each proposal against the last BACKTEST_WINDOW_DAYS of settled bets.
    Returns (accepted_proposals, rejected_proposals).

    Acceptance criteria (all must pass):
      - Sufficient sample: >= MIN_BETS_FOR_PROPOSAL bets in the target group
      - Clear underperformance: win rate >= 8pp below overall (market/league/confidence)
      - Directionally sound: proposed_value is a genuine tightening (lower Kelly, etc.)
    """
    cutoff = date.today() - timedelta(days=BACKTEST_WINDOW_DAYS)
    result = await db.execute(
        select(TrackedBet)
        .where(
            TrackedBet.result_status.in_(["Won", "Lost"]),
            TrackedBet.event_date >= cutoff,
        )
    )
    all_bets: list[TrackedBet] = result.scalars().all()

    if not all_bets:
        logger.info("Risk Agent: no settled bets for backtesting — rejecting all proposals")
        for p in proposals:
            p["backtest_note"] = "No historical data available for backtesting"
        return [], proposals

    overall_wr = sum(1 for b in all_bets if b.result_status == "Won") / len(all_bets)

    accepted: list[dict] = []
    rejected: list[dict] = []

    for proposal in proposals:
        change_type = proposal.get("change_type", "")
        target = str(proposal.get("target", ""))
        proposed_value = proposal.get("proposed_value")

        # Fast-reject LLM-hallucinated change_types before any evaluation.
        # Only types that accumulator_generator._load_candidates() actually reads
        # are meaningful; everything else is dead weight in the DB.
        if change_type not in VALID_CHANGE_TYPES:
            proposal["backtest_note"] = (
                f"Rejected: '{change_type}' is not a recognised change_type. "
                f"Valid types: {sorted(VALID_CHANGE_TYPES)}"
            )
            rejected.append(proposal)
            logger.info(
                "Risk Agent REJECTED (unknown type): %s/%s", change_type, target
            )
            continue

        try:
            verdict, note = _evaluate_proposal(all_bets, overall_wr, change_type, target, proposed_value)
        except Exception as exc:
            verdict, note = False, f"Evaluation error: {exc}"

        proposal["backtest_note"] = note
        if verdict:
            accepted.append(proposal)
            logger.info("Risk Agent ACCEPTED: %s/%s — %s", change_type, target, note)
        else:
            rejected.append(proposal)
            logger.info("Risk Agent REJECTED: %s/%s — %s", change_type, target, note)

    return accepted, rejected


def _evaluate_proposal(
    bets: list[TrackedBet],
    overall_wr: float,
    change_type: str,
    target: str,
    proposed_value,
) -> tuple[bool, str]:
    """
    Synchronous evaluation of a single proposal against historical bets.
    Returns (accept: bool, explanation: str).
    """
    if change_type == "market_suppression":
        subset = [b for b in bets if b.market_type == target]
        n = len(subset)
        # Suppression is a hard, persistent block — requires MIN_BETS_FOR_SUPPRESSION.
        # Comparing win rate to the overall average is misleading because each market
        # has its own natural probability profile (Over 0.5 markets have naturally lower
        # odds and WR than Match Winner markets). Use actual ROI instead: suppress only
        # markets that are demonstrably losing money, not just below the average WR.
        if n < MIN_BETS_FOR_SUPPRESSION:
            return False, (
                f"Only {n} bets on '{target}' — need {MIN_BETS_FOR_SUPPRESSION} "
                f"for a market suppression decision (hard block requires large sample)"
            )
        total_stake = sum(b.stake for b in subset if b.stake)
        total_pl    = sum(b.profit_loss for b in subset if b.profit_loss is not None)
        roi = (total_pl / total_stake) if total_stake > 0 else 0.0
        wr  = sum(1 for b in subset if b.result_status == "Won") / n
        if roi < -0.05:
            return True, (
                f"'{target}': ROI={roi:.1%} (negative, n={n}, wr={wr:.1%}) "
                f"— suppression justified by actual loss record"
            )
        return False, (
            f"'{target}': ROI={roi:.1%} — not negative enough to justify hard block "
            f"(need ROI < -5%, n={n}, wr={wr:.1%})"
        )

    elif change_type == "league_suppression":
        target_lower = target.lower()
        subset = [b for b in bets if b.league and target_lower in b.league.lower()]
        n = len(subset)
        # Same elevated bar as market_suppression. Same ROI-based criterion.
        if n < MIN_BETS_FOR_SUPPRESSION:
            return False, (
                f"Only {n} bets in '{target}' — need {MIN_BETS_FOR_SUPPRESSION} "
                f"for a league suppression decision"
            )
        total_stake = sum(b.stake for b in subset if b.stake)
        total_pl    = sum(b.profit_loss for b in subset if b.profit_loss is not None)
        roi = (total_pl / total_stake) if total_stake > 0 else 0.0
        wr  = sum(1 for b in subset if b.result_status == "Won") / n
        if roi < -0.05:
            return True, (
                f"'{target}': ROI={roi:.1%} (negative, n={n}, wr={wr:.1%}) "
                f"— league suppression justified by actual loss record"
            )
        return False, (
            f"'{target}': ROI={roi:.1%} — not negative enough to justify league block "
            f"(need ROI < -5%, n={n}, wr={wr:.1%})"
        )

    elif change_type == "kelly_fraction_adj":
        subset = [b for b in bets if b.dual_confidence == target]
        n = len(subset)
        if n < MIN_BETS_FOR_PROPOSAL:
            return False, f"Only {n} '{target}' confidence bets — need {MIN_BETS_FOR_PROPOSAL}"
        wr = sum(1 for b in subset if b.result_status == "Won") / n
        gap = overall_wr - wr
        if proposed_value is None or not (0.1 <= proposed_value <= 0.95):
            return False, f"Proposed Kelly multiplier {proposed_value} out of valid range [0.1, 0.95]"
        if gap >= 0.05 and proposed_value < 1.0:
            return True, (
                f"'{target}' confidence: {wr:.1%} win rate ({gap:.1%} below average, n={n}) "
                f"— reducing Kelly to {proposed_value:.2f}× justified"
            )
        return False, (
            f"'{target}' confidence: {wr:.1%} win rate — gap {gap:.1%} below 5pp threshold or multiplier >= 1.0"
        )

    elif change_type == "min_prob_by_agreement":
        # TrackedBet doesn't carry dual_agreement; check by source_rule_key prefix as proxy
        # "Poisson Only" signals carry rule keys starting with known Poisson prefixes.
        # For now we accept if proposed_value is sensible and target is a known agreement type.
        valid_targets = {"Both", "Bayesian Only", "Poisson Only", "Contradiction"}
        if target not in valid_targets:
            return False, f"Unknown agreement type: '{target}'"
        if proposed_value is None or not (0.50 <= proposed_value <= 0.85):
            return False, f"Proposed min probability {proposed_value} out of range [0.50, 0.85]"
        # Accept conservatively — this type is hard to backtest without agreement on TrackedBet
        if target in {"Poisson Only", "Contradiction"} and proposed_value <= 0.75:
            return True, (
                f"'{target}': raising min probability to {proposed_value:.2f} "
                f"is a conservative tightening — accepted on directional grounds"
            )
        return False, (
            f"'{target}': insufficient directional justification for min_prob={proposed_value}"
        )

    return False, f"Unknown change_type: '{change_type}'"


# ── Suppression reactivation monitor ──────────────────────────────────────────

async def check_suppression_reactivations(db: AsyncSession) -> int:
    """
    Runs after every settlement batch. Checks all active market_suppression proposals
    to see if the suppressed market has recovered. If ROI > -2% over the last 30 bets
    SINCE suppression was applied, deactivates the proposal (reactivates the market).

    Also deactivates any proposal older than SUPPRESSION_HARD_EXPIRY_DAYS regardless
    of ROI (time-based hard expiry).

    Returns the number of proposals reactivated.
    """
    now = datetime.now(timezone.utc)
    hard_expiry_cutoff = now - timedelta(days=SUPPRESSION_HARD_EXPIRY_DAYS)

    # Fetch all active market_suppression proposals.
    result = await db.execute(
        select(LearningProposal).where(
            LearningProposal.change_type == "market_suppression",
            LearningProposal.is_active == True,  # noqa: E712
        )
    )
    active_suppressions: list[LearningProposal] = result.scalars().all()

    if not active_suppressions:
        return 0

    reactivated = 0

    for proposal in active_suppressions:
        market = proposal.target or ""

        # Normalise created_at to UTC for comparison.
        created_at = proposal.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        # ── Hard expiry: deactivate if proposal is older than 90 days ─────────
        if created_at <= hard_expiry_cutoff:
            proposal.is_active = False
            reactivated += 1
            logger.info(
                "Reactivating suppressed market %s: proposal age %d days exceeds hard expiry of %d days",
                market,
                (now - created_at).days,
                SUPPRESSION_HARD_EXPIRY_DAYS,
            )
            continue

        # ── ROI-based reactivation: check post-suppression performance ────────
        # Query settled bets on this market placed AFTER the suppression was written.
        # Limit to REACTIVATION_LOOKBACK_BETS most recent bets for efficiency.
        bets_result = await db.execute(
            select(TrackedBet)
            .where(
                TrackedBet.market_type == market,
                TrackedBet.result_status.in_(["Won", "Lost"]),
                TrackedBet.created_at >= created_at,
            )
            .order_by(TrackedBet.created_at.desc())
            .limit(REACTIVATION_LOOKBACK_BETS)
        )
        post_bets: list[TrackedBet] = bets_result.scalars().all()
        n = len(post_bets)

        if n < REACTIVATION_MIN_BETS:
            # Not enough data yet — leave suppression in place.
            logger.debug(
                "Suppression check for %s: only %d post-suppression bets (need %d) — skipping",
                market, n, REACTIVATION_MIN_BETS,
            )
            continue

        # Calculate ROI using stake and profit_loss fields (mirrors TrackedBet usage in analytics.py).
        total_stake = sum(b.stake for b in post_bets if b.stake)
        total_pl = sum(b.profit_loss for b in post_bets if b.profit_loss is not None)
        roi = (total_pl / total_stake) if total_stake > 0 else 0.0

        if roi > REACTIVATION_ROI_THRESHOLD:
            proposal.is_active = False
            reactivated += 1
            logger.info(
                "Reactivating suppressed market %s: ROI=%.1f%% over %d post-suppression bets "
                "(threshold: %.1f%%)",
                market, roi * 100, n, REACTIVATION_ROI_THRESHOLD * 100,
            )
        else:
            logger.debug(
                "Suppression maintained for %s: ROI=%.1f%% over %d post-suppression bets "
                "(needs > %.1f%%)",
                market, roi * 100, n, REACTIVATION_ROI_THRESHOLD * 100,
            )

    if reactivated:
        await db.commit()

    return reactivated


# ── Pipeline orchestrator ──────────────────────────────────────────────────────

async def run_strategy_pipeline(db: AsyncSession) -> StrategyPipelineReport:
    """
    Runs Pipeline B: Signal Analyst → Strategy Agent → Risk Agent.

    Safe to call after every settlement — bails out gracefully at every step.
    Persists accepted proposals to LearningProposal table under Pipeline B
    change_types (market_suppression, league_suppression, kelly_fraction_adj,
    min_prob_by_agreement) without touching Pipeline A slots.
    """
    logger.info("Strategy pipeline (B) starting")

    # ── Agent 5: Signal Analyst ────────────────────────────────────────────────
    try:
        report = await run_signal_analyst(db)
    except Exception as exc:
        logger.warning("Signal Analyst failed: %s", exc)
        return StrategyPipelineReport(
            bets_analysed=0, overall_win_rate=0.0,
            proposals_generated=0, proposals_accepted=0,
            accepted_proposals=[], rejected_proposals=[],
            error=f"Signal Analyst error: {exc}",
        )

    if report is None:
        return StrategyPipelineReport(
            bets_analysed=0, overall_win_rate=0.0,
            proposals_generated=0, proposals_accepted=0,
            accepted_proposals=[], rejected_proposals=[],
        )

    # ── Agent 6: Strategy Agent ────────────────────────────────────────────────
    try:
        proposals = await run_strategy_agent(report)
    except Exception as exc:
        logger.warning("Strategy Agent failed: %s", exc)
        proposals = []

    if not proposals:
        return StrategyPipelineReport(
            bets_analysed=report.n_bets_total,
            overall_win_rate=report.overall_win_rate,
            proposals_generated=0, proposals_accepted=0,
            accepted_proposals=[], rejected_proposals=[],
        )

    # ── Agent 7: Risk Agent ────────────────────────────────────────────────────
    try:
        accepted, rejected = await run_risk_agent(db, proposals, report)
    except Exception as exc:
        logger.warning("Risk Agent failed: %s", exc)
        accepted, rejected = [], proposals

    # ── Persist accepted proposals ─────────────────────────────────────────────
    if accepted:
        for proposal in accepted:
            change_type = proposal.get("change_type", "")
            target = proposal.get("target", "")
            if not change_type or not target:
                continue

            try:
                # Deactivate existing active proposal for the same slot
                existing_result = await db.execute(
                    select(LearningProposal).where(
                        LearningProposal.change_type == change_type,
                        LearningProposal.target == target,
                        LearningProposal.is_active == True,  # noqa: E712
                    )
                )
                for old_row in existing_result.scalars().all():
                    old_row.is_active = False
                    logger.info(
                        "LearningProposal deactivated: change_type=%s target=%s (superseded by new proposal)",
                        old_row.change_type, old_row.target,
                    )

                await db.flush()  # Flush deactivation before insert

                # Insert new active proposal
                new_proposal = LearningProposal(
                    change_type=change_type,
                    target=target,
                    proposed_value=proposal.get("proposed_value"),
                    rationale=proposal.get("rationale"),
                    confidence=proposal.get("confidence"),
                    backtest_note=proposal.get("backtest_note"),
                    is_active=True,
                )
                db.add(new_proposal)
                await db.commit()
                logger.info(
                    "Strategy pipeline persisted accepted proposal to LearningProposal: %s/%s",
                    change_type, target,
                )
                logger.info(
                    "LearningProposal accepted: change_type=%s target=%s value=%s",
                    new_proposal.change_type, new_proposal.target, new_proposal.proposed_value,
                )
            except Exception as exc:
                logger.error(
                    "Failed to persist LearningProposal for %s/%s: %s",
                    change_type, target, exc,
                )
                await db.rollback()

    return StrategyPipelineReport(
        bets_analysed=report.n_bets_total,
        overall_win_rate=report.overall_win_rate,
        proposals_generated=len(proposals),
        proposals_accepted=len(accepted),
        accepted_proposals=accepted,
        rejected_proposals=rejected,
    )

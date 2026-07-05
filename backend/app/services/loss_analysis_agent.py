"""
loss_analysis_agent.py — AI-powered loss analysis and self-learning engine.

Architecture
------------
Four agents work in sequence after each settlement batch:

  1. Loss Analyst  (Groq llama-3.1-8b-instant, fast)
     Runs per-bet. Reads match context and produces structured failure
     categories + a narrative explanation of why the bet lost.

  2. Pattern Detector  (Groq llama-3.3-70b-versatile)
     Runs once per batch. Reads the full set of recent loss analyses and
     finds systematic patterns: which markets, tiers, odds ranges, and
     rule keys are producing the most avoidable losses.

  3. Threshold Tuner  (Groq llama-3.3-70b-versatile)
     Consumes Pattern Detector output. Proposes concrete, quantified
     threshold changes: max_odds per market, tier suppression overrides,
     minimum confidence levels, rule key disables.

  4. Backtester (in-process Python, no LLM)
     Validates each Threshold Tuner proposal against the settled-bet
     history. Rejects proposals that would have damaged profitable
     bets. Writes accepted changes to LearningConfig.

Self-learning loop
------------------
Settlement → Loss Analyst (per-bet) → Pattern Detector (batch)
          → Threshold Tuner → Backtester → LearningConfig update
          → Signal engine reads LearningConfig on next run

Failure categories (structured tags)
-------------------------------------
  high_odds_risk         Odds > market-implied ceiling for this market
  tier3_exposure         Tier 3 league with insufficient suppression data
  zero_zero              Match ended 0-0 (both teams nil)
  away_team_blank        Away team scored 0 — backed away_o05
  home_team_blank        Home team scored 0 — backed home_o05
  end_of_season          Last few rounds of the season (motivation drop)
  defensive_game         Score + xG context suggests defensive setup
  model_overconfidence   High confidence signal lost at <2.00 odds
  market_mispricing      Odds implied <50% probability for Over 0.5 markets
  data_gap               Missing H2H / form data likely degraded signal
  genuine_variance       Loss was within expected variance, not systematic

Requires GROQ_API_KEY in backend/.env. Falls back gracefully when absent
(rules-based tagging only, no narrative). Mirrors the pattern in advisor_service.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from typing import Any, Optional

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.bet import TrackedBet
from app.models.fixture import Fixture
from app.models.loss_analysis import LossAnalysis
from app.models.learning_proposal import LearningProposal

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── Failure category constants ────────────────────────────────────────────────

CAT_HIGH_ODDS_RISK        = "high_odds_risk"
CAT_TIER3_EXPOSURE        = "tier3_exposure"
CAT_ZERO_ZERO             = "zero_zero"
CAT_AWAY_TEAM_BLANK       = "away_team_blank"
CAT_HOME_TEAM_BLANK       = "home_team_blank"
CAT_END_OF_SEASON         = "end_of_season"
CAT_DEFENSIVE_GAME        = "defensive_game"
CAT_MODEL_OVERCONFIDENCE  = "model_overconfidence"
CAT_MARKET_MISPRICING     = "market_mispricing"
CAT_DATA_GAP              = "data_gap"
CAT_GENUINE_VARIANCE      = "genuine_variance"

ALL_CATEGORIES = {
    CAT_HIGH_ODDS_RISK, CAT_TIER3_EXPOSURE, CAT_ZERO_ZERO,
    CAT_AWAY_TEAM_BLANK, CAT_HOME_TEAM_BLANK, CAT_END_OF_SEASON,
    CAT_DEFENSIVE_GAME, CAT_MODEL_OVERCONFIDENCE, CAT_MARKET_MISPRICING,
    CAT_DATA_GAP, CAT_GENUINE_VARIANCE,
}

# Per-market odds above which the market itself is saying the selection is
# below 50% likely — a structural warning sign before any AI analysis.
MARKET_ODDS_CEILING: dict[str, float] = {
    "Home Over 0.5":  2.05,   # home team scores ≥1: >2.05 = market doubts it strongly
    "Away Over 0.5":  2.10,   # away team less certain; slightly higher ceiling
    "Home Over 1.5":  2.60,
    "Away Over 1.5":  2.80,
    "Over 0.5":       1.50,
    "Over 1.5":       2.20,
    "Over 2.5":       2.40,
    "BTTS Yes":       2.40,
}

# End-of-season months. July added 2026-07-05: all Scandinavian (Veikkausliiga,
# Superettan, Toppserien, Ykkösliiga, Ettan, Division 2), Irish (Premier
# Division), and Icelandic leagues finish in July — 62 of 71 July losses
# carried this tag via LLM but the rules engine was missing month 7, causing
# understated avoidability scores and no rules-based threshold proposals.
END_OF_SEASON_MONTHS = {5, 6, 7}


# ── Rules-based pre-classifier (no LLM, always runs) ─────────────────────────

def _rules_based_categories(
    bet: TrackedBet,
    fixture: Optional[Fixture],
) -> list[str]:
    """
    Fast rules-based failure tagging that runs even when Groq is unavailable.
    Catches the most common, structurally obvious failure modes.
    """
    cats: list[str] = []
    market = bet.market_type or ""
    odds = bet.odds or 0.0
    tier = fixture.league_tier if fixture else None
    home_score = fixture.home_score if fixture else None
    away_score = fixture.away_score if fixture else None
    event_month = bet.event_date.month if bet.event_date else None

    # Odds ceiling breach
    ceiling = MARKET_ODDS_CEILING.get(market)
    if ceiling and odds > ceiling:
        cats.append(CAT_HIGH_ODDS_RISK)
        cats.append(CAT_MARKET_MISPRICING)

    # Tier 3 exposure
    if tier == 3:
        cats.append(CAT_TIER3_EXPOSURE)

    # Score-based categories
    if home_score is not None and away_score is not None:
        if home_score == 0 and away_score == 0:
            cats.append(CAT_ZERO_ZERO)
            cats.append(CAT_DEFENSIVE_GAME)
        if market in ("Away Over 0.5", "Away Over 1.5") and away_score == 0:
            cats.append(CAT_AWAY_TEAM_BLANK)
        if market in ("Home Over 0.5", "Home Over 1.5") and home_score == 0:
            cats.append(CAT_HOME_TEAM_BLANK)

    # End of season
    if event_month in END_OF_SEASON_MONTHS:
        cats.append(CAT_END_OF_SEASON)

    # Model overconfidence: High confidence on a sub-evens pick that lost
    if bet.dual_confidence == "High" and odds < 2.00:
        cats.append(CAT_MODEL_OVERCONFIDENCE)

    # Avoidability: if no structural red flags, likely genuine variance
    structural_flags = {
        CAT_HIGH_ODDS_RISK, CAT_TIER3_EXPOSURE, CAT_MARKET_MISPRICING,
        CAT_MODEL_OVERCONFIDENCE,
    }
    if not any(c in structural_flags for c in cats):
        cats.append(CAT_GENUINE_VARIANCE)

    return list(dict.fromkeys(cats))   # deduplicate, preserve order


def _avoidability_score(categories: list[str]) -> float:
    """
    Score how avoidable the loss was, 1–10.
    10 = system had the data to avoid this; 1 = pure bad luck.
    """
    score = 1.0
    weights = {
        CAT_HIGH_ODDS_RISK:       3.0,
        CAT_MARKET_MISPRICING:    2.5,
        CAT_TIER3_EXPOSURE:       2.0,
        CAT_MODEL_OVERCONFIDENCE: 2.0,
        CAT_END_OF_SEASON:        1.5,
        CAT_ZERO_ZERO:            1.0,
        CAT_DATA_GAP:             1.5,
        CAT_GENUINE_VARIANCE:    -2.0,   # penalises the avoidability score
    }
    for cat in categories:
        score += weights.get(cat, 0.0)
    return round(min(10.0, max(1.0, score)), 1)


# ── Groq client ───────────────────────────────────────────────────────────────

MAX_LLM_RETRIES = 3
LLM_RETRY_DELAYS = [2, 5, 10]


async def _call_groq(
    system: str,
    user: str,
    model: str = "llama-3.1-8b-instant",
    temperature: float = 0.3,
    timeout: float = 20.0,
) -> Optional[dict]:
    settings = get_settings()
    api_key = settings.groq_api_key
    if not api_key:
        return None

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "max_tokens": 600,
    }

    last_exc: Optional[Exception] = None
    for attempt in range(MAX_LLM_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    GROQ_API_URL,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"]
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    logger.warning(
                        "Groq returned unparseable JSON for agent step (%s): %s",
                        model, text[:200],
                    )
                    return None
        except Exception as exc:
            last_exc = exc
            is_retriable = (
                isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                or (hasattr(exc, "status_code") and exc.status_code in (429, 500, 502, 503, 504))
            )
            if is_retriable and attempt < MAX_LLM_RETRIES - 1:
                delay = LLM_RETRY_DELAYS[attempt]
                logger.warning(
                    "Groq call failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, MAX_LLM_RETRIES, delay, exc,
                )
                await asyncio.sleep(delay)
                continue
            break

    logger.error("Groq call failed after %d attempts: %s", MAX_LLM_RETRIES, last_exc)
    return None


# ── Agent 1: Loss Analyst — per-bet narrative ─────────────────────────────────

_ANALYST_SYSTEM = """You are a professional football betting post-mortem analyst.
You receive a single losing bet with match context. Your job is to explain concisely why it lost
and assign structured failure categories from the provided list.

Be brutally honest and specific. Do NOT pad with generic statements.
Respond only with valid JSON — no markdown, no extra text."""

_ANALYST_TASK = """Analyse this lost bet and respond with this exact JSON shape:
{{
  "narrative": "2-3 sentence specific explanation of why this bet lost",
  "recommendation": "One concrete, specific system change to avoid this in future (e.g. 'Cap Home Over 0.5 at 1.95 odds in Tier 1 when home team is bottom-half')",
  "extra_categories": ["category1", "category2"],
  "avoidability_notes": "Brief note on whether this was avoidable"
}}

Allowed extra_categories (only add ones not already tagged by the rules engine):
{all_cats}

Bet context:
{context}"""


async def _analyse_single_loss(
    bet: TrackedBet,
    fixture: Optional[Fixture],
    rules_cats: list[str],
) -> dict[str, Any]:
    """Run the Loss Analyst agent on a single losing bet."""
    context = {
        "match": bet.match_name,
        "league": bet.league,
        "league_tier": fixture.league_tier if fixture else "unknown",
        "market": bet.market_type,
        "odds": bet.odds,
        "confidence": bet.dual_confidence,
        "rule_key": bet.source_rule_key,
        "final_score": (
            f"{fixture.home_score}-{fixture.away_score}"
            if fixture and fixture.home_score is not None
            else "unknown"
        ),
        "event_date": str(bet.event_date),
        "already_tagged_categories": rules_cats,
        "stake": bet.stake,
    }

    result = await _call_groq(
        system=_ANALYST_SYSTEM,
        user=_ANALYST_TASK.format(
            context=json.dumps(context, indent=2),
            all_cats=", ".join(sorted(ALL_CATEGORIES - set(rules_cats))),
        ),
        model="llama-3.1-8b-instant",
        temperature=0.2,
    )

    if not result:
        return {
            "narrative": f"Rules-based analysis only (Groq unavailable). Categories: {', '.join(rules_cats)}.",
            "recommendation": "Review per-market odds ceilings.",
            "extra_categories": [],
        }
    return result


# ── Agent 2: Pattern Detector — batch pattern analysis ───────────────────────

_PATTERN_SYSTEM = """You are a quantitative betting strategy analyst specialising in systematic failure detection.
You receive a batch of recent loss analyses from an automated football betting system.
Your task: find the 2-4 most significant systematic patterns causing losses.
Look across markets, leagues, tiers, rule keys, odds ranges, and failure categories.
Respond only with valid JSON."""

_PATTERN_TASK = """Analyse these {n} recent losses and identify systematic patterns.

Loss data:
{losses_json}

Respond with:
{{
  "top_patterns": [
    {{
      "pattern_id": "short_slug",
      "description": "What the pattern is",
      "affected_market": "e.g. Home Over 0.5",
      "affected_tier": null or 1|2|3,
      "frequency": "how many of the losses match this",
      "root_cause": "underlying reason",
      "severity": "High|Medium|Low"
    }}
  ],
  "summary": "2-3 sentence overall assessment"
}}"""


async def _detect_patterns(loss_summaries: list[dict]) -> Optional[dict]:
    """Run the Pattern Detector agent over a batch of recent losses."""
    if not loss_summaries:
        return None

    return await _call_groq(
        system=_PATTERN_SYSTEM,
        user=_PATTERN_TASK.format(
            n=len(loss_summaries),
            losses_json=json.dumps(loss_summaries, indent=2),
        ),
        model="llama-3.3-70b-versatile",
        temperature=0.1,
        timeout=40.0,
    )


# ── Agent 3: Threshold Tuner — concrete parameter changes ────────────────────

_TUNER_SYSTEM = """You are a quantitative betting systems engineer.
You receive systematic loss patterns detected in a betting engine and must propose
specific, quantified threshold changes to reduce future losses without over-fitting.
Be conservative — only propose changes with strong evidence.
Respond only with valid JSON."""

_TUNER_TASK = """Based on these detected patterns, propose specific threshold changes.

Patterns:
{patterns_json}

Current thresholds context:
- MIN_LEG_QUALITY: 0.04
- AUTO_SUPPRESS_MIN_SAMPLES: 25
- Tier 3 penalty: -0.006 per leg
- No per-market odds ceiling is currently enforced (new feature)

Respond with:
{{
  "proposals": [
    {{
      "proposal_id": "short_slug",
      "change_type": "market_odds_ceiling|tier_suppression|min_confidence|rule_disable|quality_threshold",
      "target": "e.g. Home Over 0.5",
      "current_value": null or number,
      "proposed_value": number,
      "rationale": "Why this specific change",
      "confidence": "High|Medium|Low",
      "reversible": true
    }}
  ],
  "do_not_change": ["list of things that look fine"],
  "summary": "Overall recommendation"
}}"""


async def _tune_thresholds(patterns: dict) -> Optional[dict]:
    """Run the Threshold Tuner agent on detected patterns."""
    return await _call_groq(
        system=_TUNER_SYSTEM,
        user=_TUNER_TASK.format(patterns_json=json.dumps(patterns, indent=2)),
        model="llama-3.3-70b-versatile",
        temperature=0.1,
        timeout=40.0,
    )


# ── Agent 4: Backtester — validate proposals against history ─────────────────

@dataclass
class BacktestResult:
    proposal_id: str
    accepted: bool
    reason: str
    affected_wins_saved: int = 0    # wins that would have been wrongly excluded
    affected_losses_avoided: int = 0  # losses that would have been avoided


def _backtest_proposal(
    proposal: dict,
    settled_bets: list[TrackedBet],
) -> BacktestResult:
    """
    In-process backtest: apply a proposed threshold to historical settled bets
    and check whether it would have helped or hurt.

    Currently handles: market_odds_ceiling proposals.
    Other types are accepted by default (insufficient data to reject them).
    """
    pid = proposal.get("proposal_id", "unknown")
    change_type = proposal.get("change_type", "")
    target = proposal.get("target", "")
    proposed_value = proposal.get("proposed_value")
    confidence = proposal.get("confidence", "Low")

    if change_type != "market_odds_ceiling" or proposed_value is None:
        return BacktestResult(pid, accepted=True, reason="Accepted (no backtest available for this type)")

    # Filter settled bets for this market
    market_bets = [b for b in settled_bets if b.market_type == target and b.odds is not None]

    if not market_bets:
        return BacktestResult(pid, accepted=True, reason="Accepted (no historical data for market)")

    # Bets that would be EXCLUDED by the new ceiling
    excluded = [b for b in market_bets if b.odds > proposed_value]
    would_exclude_wins = [b for b in excluded if b.result_status == "Won"]
    would_exclude_losses = [b for b in excluded if b.result_status == "Lost"]

    # Compare P&L impact, not raw counts.
    # A ceiling that saves one £20 loss but costs ten 1.05-odds wins is a net gain;
    # the old count check (10 wins > 1 loss → reject) was silently discarding valid proposals.
    # Fallback stake = 1.0 unit when stake is None (preserves count-ratio behaviour as last resort).
    profit_sacrificed = sum(
        (b.odds - 1.0) * (b.stake or 1.0) for b in would_exclude_wins
    )
    stake_saved = sum(
        (b.stake or 1.0) for b in would_exclude_losses
    )

    # Reject only when giving up more profit than we save in stake.
    if profit_sacrificed > stake_saved:
        return BacktestResult(
            pid,
            accepted=False,
            reason=(
                f"Rejected — ceiling {proposed_value} sacrifices {profit_sacrificed:.2f} profit "
                f"({len(would_exclude_wins)} wins) to save {stake_saved:.2f} stake "
                f"({len(would_exclude_losses)} losses) — net negative"
            ),
            affected_wins_saved=len(would_exclude_wins),
            affected_losses_avoided=len(would_exclude_losses),
        )

    return BacktestResult(
        pid,
        accepted=True,
        reason=(
            f"Accepted — ceiling {proposed_value} saves {stake_saved:.2f} stake "
            f"({len(would_exclude_losses)} losses), costs {profit_sacrificed:.2f} profit "
            f"({len(would_exclude_wins)} wins) — net positive"
        ),
        affected_wins_saved=len(would_exclude_wins),
        affected_losses_avoided=len(would_exclude_losses),
    )


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _already_analysed(db: AsyncSession, tracked_bet_id: int) -> bool:
    """Return True if this bet already has a LossAnalysis row."""
    result = await db.execute(
        select(LossAnalysis.id).where(LossAnalysis.tracked_bet_id == tracked_bet_id).limit(1)
    )
    return result.scalar() is not None


async def _load_unanalysed_losses(
    db: AsyncSession,
    lookback_days: int = 90,
    user_id: Optional[int] = None,
) -> list[tuple[TrackedBet, Optional[Fixture]]]:
    """Load settled Lost bets that don't yet have a LossAnalysis row."""
    cutoff = date.today() - timedelta(days=lookback_days)

    stmt = (
        select(TrackedBet, Fixture)
        .outerjoin(Fixture, TrackedBet.fixture_id == Fixture.id)
        .where(
            TrackedBet.result_status == "Lost",
            TrackedBet.event_date >= cutoff,
        )
        .order_by(TrackedBet.event_date.desc())
    )
    if user_id is not None:
        stmt = stmt.where(TrackedBet.user_id == user_id)

    rows = await db.execute(stmt)
    pairs = rows.all()

    # Filter to only those without an existing analysis
    result = []
    for bet, fix in pairs:
        if not await _already_analysed(db, bet.id):
            result.append((bet, fix))

    return result


async def _load_recent_analyses(
    db: AsyncSession,
    lookback_days: int = 30,
) -> list[LossAnalysis]:
    """Load recent LossAnalysis rows for pattern detection."""
    cutoff = date.today() - timedelta(days=lookback_days)
    result = await db.execute(
        select(LossAnalysis)
        .where(LossAnalysis.event_date >= cutoff)
        .order_by(LossAnalysis.event_date.desc())
    )
    return list(result.scalars().all())


# ── Public API ────────────────────────────────────────────────────────────────

@dataclass
class LossAnalysisReport:
    """Output of a full run_loss_analysis_pipeline call."""
    bets_analysed: int = 0
    patterns_detected: Optional[dict] = None
    threshold_proposals: Optional[dict] = None
    backtest_results: list[BacktestResult] = field(default_factory=list)
    accepted_proposals: list[dict] = field(default_factory=list)
    skipped_proposals: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def run_loss_analysis_pipeline(
    db: AsyncSession,
    user_id: Optional[int] = None,
    lookback_days: int = 90,
    skip_llm: bool = False,
) -> LossAnalysisReport:
    """
    Full four-agent self-learning pipeline.

    1. Loss Analyst: tag + narrate each unanalysed loss (rules + LLM)
    2. Pattern Detector: find systematic patterns in recent analyses
    3. Threshold Tuner: propose concrete parameter changes
    4. Backtester: validate proposals against settled history

    Safe to call multiple times — skips already-analysed bets.
    """
    report = LossAnalysisReport()
    settings = get_settings()
    groq_available = bool(settings.groq_api_key) and not skip_llm

    # ── Step 1: Analyse unanalysed losses ─────────────────────────────────
    pairs = await _load_unanalysed_losses(db, lookback_days=lookback_days, user_id=user_id)
    logger.info("Loss analysis pipeline: %d unanalysed losses to process", len(pairs))

    for bet, fixture in pairs:
        try:
            rules_cats = _rules_based_categories(bet, fixture)

            llm_result: dict = {}
            if groq_available:
                llm_result = await _analyse_single_loss(bet, fixture, rules_cats) or {}

            # Merge categories from rules + LLM
            extra_cats = [
                c for c in llm_result.get("extra_categories", [])
                if c in ALL_CATEGORIES
            ]
            all_cats = list(dict.fromkeys(rules_cats + extra_cats))
            avoidability = _avoidability_score(all_cats)

            analysis = LossAnalysis(
                tracked_bet_id=bet.id,
                event_date=bet.event_date,
                match_name=bet.match_name,
                league=bet.league,
                league_tier=fixture.league_tier if fixture else None,
                market_type=bet.market_type,
                odds=bet.odds,
                dual_confidence=bet.dual_confidence,
                source_rule_key=bet.source_rule_key,
                home_score=fixture.home_score if fixture else None,
                away_score=fixture.away_score if fixture else None,
                agent_id="loss_analyst" + ("_groq" if groq_available else "_rules"),
                failure_categories=",".join(all_cats),
                narrative=llm_result.get("narrative", f"Rules-based: {', '.join(all_cats)}"),
                recommendation=llm_result.get("recommendation"),
                avoidability_score=avoidability,
            )
            db.add(analysis)
            report.bets_analysed += 1

        except Exception as e:
            logger.warning("Loss analysis failed for bet %s: %s", bet.id, e)
            report.errors.append(f"bet {bet.id}: {e}")

    if report.bets_analysed:
        await db.commit()
        logger.info("Loss analysis: %d bets analysed and saved", report.bets_analysed)

    # ── Step 2: Pattern detection across recent analyses ───────────────────
    if not groq_available:
        logger.info("Groq unavailable — skipping Pattern Detector and Threshold Tuner")
        return report

    recent_analyses = await _load_recent_analyses(db, lookback_days=30)
    if len(recent_analyses) < 2:
        logger.info("Fewer than 2 recent analyses — skipping pattern detection")
        return report

    loss_summaries = [
        {
            "match": a.match_name,
            "market": a.market_type,
            "odds": a.odds,
            "tier": a.league_tier,
            "league": a.league,
            "confidence": a.dual_confidence,
            "rule": a.source_rule_key,
            "score": f"{a.home_score}-{a.away_score}" if a.home_score is not None else "?",
            "categories": a.failure_categories,
            "avoidability": a.avoidability_score,
            "date": str(a.event_date),
        }
        for a in recent_analyses
    ]

    patterns = await _detect_patterns(loss_summaries)
    if patterns:
        report.patterns_detected = patterns
        logger.info(
            "Pattern Detector found %d patterns",
            len(patterns.get("top_patterns", [])),
        )
    else:
        logger.info("Pattern Detector returned no results")
        return report

    # ── Step 3: Threshold tuning ───────────────────────────────────────────
    tuner_output = await _tune_thresholds(patterns)
    if not tuner_output:
        return report

    report.threshold_proposals = tuner_output
    proposals = tuner_output.get("proposals", [])
    logger.info("Threshold Tuner proposed %d changes", len(proposals))

    # ── Step 4: Backtest proposals ─────────────────────────────────────────
    # Load all settled bets for backtesting
    all_settled_result = await db.execute(
        select(TrackedBet).where(TrackedBet.result_status.in_(["Won", "Lost"]))
    )
    all_settled = list(all_settled_result.scalars().all())

    for proposal in proposals:
        bt = _backtest_proposal(proposal, all_settled)
        report.backtest_results.append(bt)
        if bt.accepted:
            report.accepted_proposals.append({**proposal, "backtest": bt.reason})
            logger.info("Proposal ACCEPTED: %s — %s", proposal.get("proposal_id"), bt.reason)
        else:
            report.skipped_proposals.append({**proposal, "backtest": bt.reason})
            logger.info("Proposal REJECTED: %s — %s", proposal.get("proposal_id"), bt.reason)

    # ── Step 5: Persist accepted proposals ───────────────────────────────────
    # Whitelist keeps Pipeline A's namespace distinct from Pipeline B (strategy_pipeline).
    # If the LLM hallucinates a Pipeline-B type (market_suppression, league_suppression,
    # kelly_fraction_adj, min_prob_by_agreement) it would collide with that pipeline's
    # active rows and deactivate them.
    _LOSS_ANALYSIS_VALID_CHANGE_TYPES = frozenset({
        "market_odds_ceiling",
        "min_probability",
        "tier_suppression",
        "min_confidence",
        "rule_disable",
        "quality_threshold",
    })

    if report.accepted_proposals:
        for proposal in report.accepted_proposals:
            change_type = proposal.get("change_type", "")
            target = proposal.get("target", "")
            if not change_type or not target:
                continue
            if change_type not in _LOSS_ANALYSIS_VALID_CHANGE_TYPES:
                logger.warning(
                    "LossAnalysis: skipping proposal with unrecognised change_type=%r "
                    "(would collide with Pipeline B namespace)",
                    change_type,
                )
                continue

            try:
                # Deactivate any existing active proposal for the same slot
                existing = await db.execute(
                    select(LearningProposal).where(
                        LearningProposal.change_type == change_type,
                        LearningProposal.target == target,
                        LearningProposal.is_active == True,  # noqa: E712
                    )
                )
                for old_row in existing.scalars().all():
                    old_row.is_active = False
                    logger.info(
                        "LearningProposal deactivated: change_type=%s target=%s (superseded by new proposal)",
                        old_row.change_type, old_row.target,
                    )

                await db.flush()  # Flush deactivation before insert

                # Insert the new active proposal
                new_row = LearningProposal(
                    change_type=change_type,
                    target=target,
                    proposed_value=proposal.get("proposed_value"),
                    rationale=proposal.get("rationale"),
                    confidence=proposal.get("confidence"),
                    backtest_note=proposal.get("backtest"),
                    is_active=True,
                )
                db.add(new_row)

                await db.commit()
                logger.info(
                    "Persisted accepted proposal to learning_proposals: %s/%s",
                    change_type, target,
                )
                logger.info(
                    "LearningProposal accepted: change_type=%s target=%s value=%s",
                    new_row.change_type, new_row.target, new_row.proposed_value,
                )
            except Exception as exc:
                logger.error(
                    "Failed to persist LearningProposal for %s/%s: %s",
                    change_type, target, exc,
                )
                await db.rollback()

    return report



async def get_loss_analysis_summary(
    db: AsyncSession,
    lookback_days: int = 30,
) -> dict:
    """
    Lightweight summary for the analytics API — no LLM calls, just DB reads.
    Returns aggregated category counts, pattern trends, and avoidability stats.
    """
    cutoff = date.today() - timedelta(days=lookback_days)
    analyses = await _load_recent_analyses(db, lookback_days=lookback_days)

    if not analyses:
        return {
            "total_losses_analysed": 0,
            "category_counts": {},
            "avg_avoidability": None,
            "most_avoidable_market": None,
            "analyses": [],
        }

    # Count categories
    cat_counts: dict[str, int] = {}
    for a in analyses:
        for cat in (a.failure_categories or "").split(","):
            cat = cat.strip()
            if cat:
                cat_counts[cat] = cat_counts.get(cat, 0) + 1

    # Per-market avoidability
    market_avoid: dict[str, list[float]] = {}
    for a in analyses:
        if a.market_type and a.avoidability_score is not None:
            market_avoid.setdefault(a.market_type, []).append(a.avoidability_score)

    most_avoidable = None
    if market_avoid:
        most_avoidable = max(market_avoid, key=lambda m: sum(market_avoid[m]) / len(market_avoid[m]))

    avg_avoid = (
        round(sum(a.avoidability_score for a in analyses if a.avoidability_score) / len(analyses), 1)
        if analyses else None
    )

    return {
        "total_losses_analysed": len(analyses),
        "category_counts": dict(sorted(cat_counts.items(), key=lambda x: -x[1])),
        "avg_avoidability": avg_avoid,
        "most_avoidable_market": most_avoidable,
        "analyses": [
            {
                "id": a.id,
                "date": str(a.event_date),
                "match": a.match_name,
                "league": a.league,
                "tier": a.league_tier,
                "market": a.market_type,
                "odds": a.odds,
                "score": f"{a.home_score}-{a.away_score}" if a.home_score is not None else None,
                "categories": (a.failure_categories or "").split(","),
                "narrative": a.narrative,
                "recommendation": a.recommendation,
                "avoidability": a.avoidability_score,
                "agent": a.agent_id,
            }
            for a in analyses
        ],
    }

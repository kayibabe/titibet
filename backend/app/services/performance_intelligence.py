"""
performance_intelligence.py — Self-learning performance weights from historical bet outcomes.

Analyzes settled tracked_bets to compute win-rate and ROI per (confidence, market) slice,
per source_rule_key, and per (market, league_tier). These weights feed back into the
accumulator generator and signal engine so game selection improves over time.

Learning loop:
  Bet tracked → Settled (Won/Lost) → Weights recomputed → Signals/Accas scored adjusted
  → Better leg selection → Higher hit rates → Better weights → ...

Three-layer weight hierarchy:
  Layer 1: (dual_confidence, market)  — primary; captures tier × market interaction
  Layer 2: (market, league_tier)      — NEW; detects league-specific market failures
  Layer 3: source_rule_key            — Poisson rule performance

Auto-suppression:
  When a (market, league_tier) slice has factor < AUTO_SUPPRESS_THRESHOLD for
  AUTO_SUPPRESS_MIN_SAMPLES+ settled bets, it is added to auto_suppress_market_tiers.
  Similarly for rules. Suppressed slices are excluded from accumulator candidates.

Calibration monitoring:
  Tracks expected vs actual win rate per confidence tier. If High-confidence signals
  are only winning 50% (vs expected 63%), the model is overconfident and thresholds
  need tightening. Calibration error > 0.10 triggers an is_overconfident flag.

Bayesian smoothing:
  At MIN_SAMPLES (15), the observed factor carries only 30% weight, blending towards
  neutral (1.0) until enough data confirms the trend. Ramps to ~90% confidence at
  60 samples.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select, func, case, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bet import TrackedBet
from app.models.fixture import Fixture

MIN_SAMPLES = 15           # minimum settled bets before a slice influences scoring
AUTO_SUPPRESS_THRESHOLD = 0.62   # performance_factor below this → flag for suppression
AUTO_SUPPRESS_MIN_SAMPLES = 25   # minimum samples before auto-suppression can trigger

# Soft overlay — applies a gentle learning signal before MIN_SAMPLES is reached.
# With a sample size this small (2-14 bets), the previous floor of 0.88 produced
# near-neutral factors (≈0.998) because the ramp was too slow and the floor too high.
# New settings react from sample 1, produce a meaningful ~4-8% penalty by sample 2,
# and reach their full range by sample 5. This is still conservative enough to avoid
# overreacting to a single result, but noticeable enough to affect ranking.
SOFT_MIN_SAMPLES = 1       # react after just 1 settled bet (was 2)
SOFT_MAX_SAMPLES = 5       # full overlay range reached by 5 samples (was 7)
SOFT_MIN_FACTOR = 0.92     # was 0.80 — single results shouldn't cause >8% quality drop
SOFT_MAX_FACTOR = 1.08     # was 1.12 — cap upside symmetrically

# League × market granular learning thresholds — lower bar because cells are smaller.
_LEAGUE_MKT_MIN_SAMPLES = 3          # activate factor once 3 settled bets exist for combo (was 5)
_LEAGUE_MKT_SUPPRESS_MIN_SAMPLES = 5 # suppress combo after 5+ bets with negative ROI
_LEAGUE_MKT_SUPPRESS_ROI = 0.0       # ROI below this → suppress (losing money)

# Recency weighting — bets in last 30 days count double in the blended factor.
# After 60+ days, recent window may be empty so all-time factor dominates.
_RECENT_DAYS = 30
_RECENT_WEIGHT = 0.65   # 65% weight on recent ROI when recent data exists

# Expected win rates per confidence tier (derived from model design thresholds).
# High: Bayesian edge ≥7% AND prob ≥60%, dual agreement → ~63% true probability.
# Medium: edge ≥5% OR prob ≥55% → ~54%.
# Low: single engine or mild disagreement → ~44%.
_EXPECTED_WIN_RATES: dict[str, float] = {
    "High": 0.63,
    "Medium": 0.54,
    "Low": 0.44,
}

# Calibration overconfidence threshold: if actual win rate is this far below expected,
# the confidence tier is considered miscalibrated.
_CALIBRATION_ERROR_THRESHOLD = 0.10


@dataclass
class PerformanceSlice:
    samples: int
    wins: int
    losses: int
    win_rate: float        # observed fraction, 0–1
    roi: float             # profit_loss / total_stake, can be negative
    performance_factor: float  # quality-score multiplier, range [0.50, 1.50]
    # Recency fields — last-30-day window (None when insufficient data)
    recent_samples: int = 0
    recent_roi: Optional[float] = None   # ROI over last 30 days only
    recency_factor: float = 1.0          # blended factor that weights recent > old


@dataclass
class CalibrationMetric:
    """Calibration check for a confidence tier: expected vs actual win rate."""
    confidence: str
    expected_win_rate: float   # from model design (_EXPECTED_WIN_RATES)
    actual_win_rate: float     # observed from settled bets
    calibration_error: float   # expected - actual (positive = model overconfident)
    samples: int
    is_overconfident: bool     # calibration_error > _CALIBRATION_ERROR_THRESHOLD

    def status(self) -> str:
        if self.samples < MIN_SAMPLES:
            return "Insufficient data"
        if self.is_overconfident:
            return f"Overconfident — model expects {self.expected_win_rate:.0%}, actual {self.actual_win_rate:.0%}"
        return "Well-calibrated"


@dataclass
class PerformanceWeights:
    """
    Layered lookup: specific slice → confidence level → market level → rule → neutral (1.0).
    Each layer is only applied when it has enough samples (MIN_SAMPLES).

    New in v2:
      by_market_tier  — (market, league_tier) performance, catches league-specific failures.
      calibration     — per-confidence expected vs actual win rate.
      auto_suppress_rules / auto_suppress_market_tiers — slices to skip in accumulator.
    """
    # (dual_confidence, market_type) → slice
    by_confidence_market: dict[tuple[str, str], PerformanceSlice] = field(default_factory=dict)
    # dual_confidence → slice
    by_confidence: dict[str, PerformanceSlice] = field(default_factory=dict)
    # market_type → slice
    by_market: dict[str, PerformanceSlice] = field(default_factory=dict)
    # source_rule_key → slice (Poisson rule performance)
    by_rule: dict[str, PerformanceSlice] = field(default_factory=dict)
    # (market_type, league_tier) → slice
    by_market_tier: dict[tuple[str, int], PerformanceSlice] = field(default_factory=dict)
    # (league_name_lower, market_type) → slice  — granular self-learning
    by_league_market: dict[tuple[str, str], PerformanceSlice] = field(default_factory=dict)
    # Calibration metrics per confidence tier
    calibration: dict[str, CalibrationMetric] = field(default_factory=dict)
    # Auto-suppress sets
    auto_suppress_rules: set[str] = field(default_factory=set)
    auto_suppress_market_tiers: set[tuple[str, int]] = field(default_factory=set)
    auto_suppress_league_markets: set[tuple[str, str]] = field(default_factory=set)

    # ── Primary factor lookups ─────────────────────────────────────────────────

    def factor_for(self, confidence: str, market: str) -> float:
        """
        Return the performance multiplier for a given (confidence, market) pair.
        Falls through from specific → broad → neutral when sample counts are too low.
        """
        specific = self.by_confidence_market.get((confidence, market))
        if specific:
            return _effective_factor(specific)

        conf_slice = self.by_confidence.get(confidence)
        if conf_slice:
            return _effective_factor(conf_slice)

        mkt_slice = self.by_market.get(market)
        if mkt_slice:
            return _effective_factor(mkt_slice)

        return 1.0  # insufficient data — neutral

    def factor_for_rule(self, rule_key: str) -> float:
        """Return the performance multiplier for a specific Poisson rule."""
        if not rule_key:
            return 1.0
        sl = self.by_rule.get(rule_key)
        if sl:
            return _effective_factor(sl)
        return 1.0

    def factor_for_market_tier(self, market: str, league_tier: Optional[int]) -> float:
        """
        Return the performance multiplier for a (market, league_tier) combination.
        Only activates when the slice has MIN_SAMPLES settled bets.
        Tier 0 / None = unknown → neutral.
        """
        if not league_tier:
            return 1.0
        sl = self.by_market_tier.get((market, league_tier))
        if sl:
            return _effective_factor(sl)
        return 1.0

    def factor_for_league_market(self, league: str, market: str) -> float:
        """
        Return the recency-adjusted performance multiplier for a specific
        (league, market) pair — the most granular learning signal available.
        Uses recency_factor which blends recent-30-day ROI with all-time ROI
        so the engine responds quickly to changing market conditions.
        Falls back to 1.0 (neutral) when sample count is below minimum.
        """
        if not league:
            return 1.0
        sl = self.by_league_market.get((league.lower().strip(), market))
        if sl:
            return _effective_factor(sl, prefer_recency=True)
        return 1.0

    def stake_multiplier(self, league: str, market: str, confidence: str) -> float:
        """
        Combined stake scaling factor for a (league, market, confidence) triple.
        Multiplies the three independent factors and clamps to [0.50, 1.75] so
        a strong track record can increase stake by up to 75% and a poor one
        cuts it by up to 50%.  Neutral when data is insufficient (returns 1.0).
        """
        f_lm   = self.factor_for_league_market(league, market)
        f_cm   = self.factor_for(confidence, market)
        combined = f_lm * f_cm
        return round(min(1.75, max(0.50, combined)), 3)

    # ── Suppression checks ────────────────────────────────────────────────────

    def should_suppress(
        self,
        market: str,
        league_tier: Optional[int],
        rule_key: str = "",
    ) -> bool:
        """
        Returns True if this market/tier/rule combination should be excluded from
        accumulator candidates based on historical under-performance.
        Suppression requires AUTO_SUPPRESS_MIN_SAMPLES settled bets.
        """
        if rule_key and rule_key in self.auto_suppress_rules:
            return True
        if league_tier and (market, league_tier) in self.auto_suppress_market_tiers:
            return True
        return False

    def should_suppress_league_market(self, league: str, market: str) -> bool:
        """
        Returns True when the specific (league, market) pair has proven consistently
        unprofitable: _LEAGUE_MKT_SUPPRESS_MIN_SAMPLES settled bets with ROI < 0.
        More conservative than the tier-level suppression — requires the exact league
        to confirm the pattern before cutting it off.
        """
        if not league:
            return False
        return (league.lower().strip(), market) in self.auto_suppress_league_markets

    def confidence_needs_downgrade(self, market: str, league_tier: Optional[int]) -> bool:
        """
        Returns True if the (market, league_tier) slice is performing poorly enough
        that a signal's confidence should be downgraded by one tier.
        Threshold: factor < 0.72 with AUTO_SUPPRESS_MIN_SAMPLES.
        """
        if not league_tier:
            return False
        sl = self.by_market_tier.get((market, league_tier))
        if sl and sl.samples >= AUTO_SUPPRESS_MIN_SAMPLES and sl.performance_factor < 0.72:
            return True
        return False

    # ── Report helpers ────────────────────────────────────────────────────────

    def as_report(self) -> list[dict]:
        """Serialisable summary for the analytics API (confidence × market detail)."""
        rows: list[dict] = []
        for (conf, mkt), sl in sorted(self.by_confidence_market.items()):
            rows.append({
                "confidence": conf,
                "market": mkt,
                "samples": sl.samples,
                "wins": sl.wins,
                "losses": sl.losses,
                "win_rate": round(sl.win_rate * 100, 1),
                "roi": round(sl.roi * 100, 1),
                "performance_factor": sl.performance_factor,
            })
        return rows

    def rule_report(self) -> list[dict]:
        """Serialisable summary of rule-level performance, sorted by ROI descending."""
        rows: list[dict] = []
        for rule_key, sl in sorted(self.by_rule.items(), key=lambda x: -x[1].roi):
            rows.append({
                "rule_key": rule_key,
                "samples": sl.samples,
                "wins": sl.wins,
                "losses": sl.losses,
                "win_rate": round(sl.win_rate * 100, 1),
                "roi": round(sl.roi * 100, 1),
                "performance_factor": sl.performance_factor,
                "auto_suppressed": rule_key in self.auto_suppress_rules,
            })
        return rows

    def market_tier_report(self) -> list[dict]:
        """Serialisable summary of (market, league_tier) performance — NEW."""
        tier_labels = {1: "Tier 1 (Top)", 2: "Tier 2 (Mid)", 3: "Tier 3 (Lower)"}
        rows: list[dict] = []
        for (mkt, tier), sl in sorted(
            self.by_market_tier.items(),
            key=lambda x: (x[0][0], x[0][1]),
        ):
            rows.append({
                "market": mkt,
                "league_tier": tier,
                "tier_label": tier_labels.get(tier, f"Tier {tier}"),
                "samples": sl.samples,
                "wins": sl.wins,
                "losses": sl.losses,
                "win_rate": round(sl.win_rate * 100, 1),
                "roi": round(sl.roi * 100, 1),
                "performance_factor": sl.performance_factor,
                "auto_suppressed": (mkt, tier) in self.auto_suppress_market_tiers,
            })
        return rows

    def calibration_report(self) -> list[dict]:
        """Expected vs actual win rate per confidence tier — NEW."""
        CONF_ORDER = {"High": 0, "Medium": 1, "Low": 2}
        rows: list[dict] = []
        for conf, cm in sorted(self.calibration.items(), key=lambda x: CONF_ORDER.get(x[0], 99)):
            rows.append({
                "confidence": conf,
                "expected_win_rate": round(cm.expected_win_rate * 100, 1),
                "actual_win_rate": round(cm.actual_win_rate * 100, 1),
                "calibration_error": round(cm.calibration_error * 100, 1),
                "samples": cm.samples,
                "is_overconfident": cm.is_overconfident,
                "status": cm.status(),
            })
        return rows


# ── Factor computation ────────────────────────────────────────────────────────

def _recency_adjusted_factor(
    all_time_factor: float,
    recent_roi: Optional[float],
    recent_samples: int,
) -> float:
    """
    Blend all-time performance factor with recent-window factor.
    If recent data exists (≥ _LEAGUE_MKT_MIN_SAMPLES bets in last 30 days):
      recency_factor = _RECENT_WEIGHT × recent_factor + (1-_RECENT_WEIGHT) × all_time_factor
    Otherwise returns all_time_factor unchanged.

    This ensures the engine quickly responds when a previously good market
    starts losing (or vice versa) without waiting for long-term averages to shift.
    """
    if recent_roi is None or recent_samples < _LEAGUE_MKT_MIN_SAMPLES:
        return all_time_factor
    recent_factor = _factor_from_stats(
        # Approximate win rate from ROI isn't perfect but sufficient for blending.
        # For the recency component we only have ROI; use 0.60 as neutral win rate.
        win_rate=max(0.0, min(1.0, 0.60 + recent_roi * 0.3)),
        roi=recent_roi,
        samples=recent_samples,
    )
    blended = _RECENT_WEIGHT * recent_factor + (1.0 - _RECENT_WEIGHT) * all_time_factor
    return round(min(1.50, max(0.50, blended)), 3)


def _soft_factor_from_stats(win_rate: float, roi: float, samples: int) -> float:
    """
    Gentle low-sample overlay used before the hard MIN_SAMPLES threshold.

    This keeps early learning conservative: two or three results can nudge a slice
    away from neutral, but they cannot trigger the large swings reserved for
    established samples.
    """
    if samples < SOFT_MIN_SAMPLES:
        return 1.0

    roi_component = max(-0.18, min(0.18, roi * 0.22))
    win_component = max(-0.14, min(0.14, (win_rate - 0.55) * 0.28))
    raw = 1.0 + roi_component + win_component
    bounded = min(SOFT_MAX_FACTOR, max(SOFT_MIN_FACTOR, raw))

    ramp = min(1.0, max(0.0, (samples - SOFT_MIN_SAMPLES + 1) / (SOFT_MAX_SAMPLES - SOFT_MIN_SAMPLES + 1)))
    softened = 1.0 + (bounded - 1.0) * ramp
    return round(min(SOFT_MAX_FACTOR, max(SOFT_MIN_FACTOR, softened)), 3)


def _factor_from_stats(win_rate: float, roi: float, samples: int) -> float:
    """
    Convert observed win_rate + ROI into a quality-score multiplier [0.50, 1.50].

    ROI (profit / stake) carries 60% weight — it captures both hit rate and
    price quality. Win rate carries 40% — it captures directional accuracy.

    Bayesian smoothing blends the observed factor towards neutral (1.0) based
    on sample count so that early small-sample noise doesn't steer the engine.
    At MIN_SAMPLES (15): 30% observed, 70% neutral.
    At 60 samples:       ~90% observed.
    """
    if samples < MIN_SAMPLES:
        return 1.0

    # ROI factor — primary signal
    if roi > 0.25:
        roi_f = 1.40
    elif roi > 0.15:
        roi_f = 1.25
    elif roi > 0.05:
        roi_f = 1.10
    elif roi >= -0.05:
        roi_f = 1.00
    elif roi >= -0.15:
        roi_f = 0.85
    elif roi >= -0.25:
        roi_f = 0.70
    else:
        roi_f = 0.55

    # Win-rate factor — secondary signal
    if win_rate > 0.70:
        wr_f = 1.30
    elif win_rate > 0.60:
        wr_f = 1.15
    elif win_rate > 0.50:
        wr_f = 1.05
    elif win_rate > 0.40:
        wr_f = 0.95
    elif win_rate > 0.30:
        wr_f = 0.80
    else:
        wr_f = 0.65

    raw = roi_f * 0.60 + wr_f * 0.40

    # Bayesian smoothing: ramp from 30% confidence at MIN_SAMPLES to 90% at 60 samples.
    confidence = min(0.90, 0.30 + 0.60 * min(1.0, (samples - MIN_SAMPLES) / 45.0))
    smoothed = confidence * raw + (1.0 - confidence) * 1.0

    return round(min(1.50, max(0.50, smoothed)), 3)


def _effective_factor(sl: PerformanceSlice, prefer_recency: bool = False) -> float:
    """
    Return the actionable factor for a slice.

    Hard-sample factors use the stored performance/recency factors. Below the hard
    threshold, fall back to a soft overlay so recent settled bets can start nudging
    rankings and stake sizing without creating aggressive swings.
    """
    if prefer_recency and sl.recent_samples >= _LEAGUE_MKT_MIN_SAMPLES and sl.recent_roi is not None:
        return sl.recency_factor
    if sl.samples >= MIN_SAMPLES:
        return sl.performance_factor
    return _soft_factor_from_stats(sl.win_rate, sl.roi, sl.samples)


# ── DB queries ────────────────────────────────────────────────────────────────

async def compute_performance_weights(
    db: AsyncSession,
    min_samples: int = MIN_SAMPLES,
    user_id: int | None = None,
) -> PerformanceWeights:
    """
    Query settled tracked_bets and return PerformanceWeights for all valid slices.

    Runs four aggregate queries:
      1. Grouped by (dual_confidence, market_type) — confidence × market grid.
      2. Grouped by source_rule_key — Poisson rule intelligence.
      3. Grouped by (market_type, league_tier) — NEW: league-tier × market grid.
      4. Calibration: per confidence tier, actual win rate vs expected.
    """
    weights = PerformanceWeights()

    # ── Query 1: confidence × market ─────────────────────────────────────────
    stmt = (
        select(
            TrackedBet.dual_confidence,
            TrackedBet.market_type,
            func.count(TrackedBet.id).label("samples"),
            func.sum(
                case((TrackedBet.result_status == "Won", 1), else_=0)
            ).label("wins"),
            func.sum(TrackedBet.profit_loss).label("total_pl"),
            func.sum(TrackedBet.stake).label("total_stake"),
        )
        .where(TrackedBet.result_status.in_(["Won", "Lost"]))
        .group_by(TrackedBet.dual_confidence, TrackedBet.market_type)
    )
    if user_id is not None:
        stmt = stmt.where(TrackedBet.user_id == user_id)

    rows = await db.execute(stmt)

    conf_agg: dict[str, dict] = {}
    market_agg: dict[str, dict] = {}

    for row in rows.all():
        conf = row.dual_confidence or "Unknown"
        mkt = row.market_type or "Unknown"
        samples = row.samples or 0
        wins = int(row.wins or 0)
        total_pl = float(row.total_pl or 0)
        total_stake = float(row.total_stake or 0)
        losses = samples - wins

        if samples == 0:
            continue

        win_rate = wins / samples
        roi = total_pl / total_stake if total_stake > 0 else 0.0
        factor = _factor_from_stats(win_rate, roi, samples) if samples >= min_samples else 1.0

        weights.by_confidence_market[(conf, mkt)] = PerformanceSlice(
            samples=samples, wins=wins, losses=losses,
            win_rate=round(win_rate, 4), roi=round(roi, 4),
            performance_factor=factor,
        )

        for bucket, key in [(conf_agg, conf), (market_agg, mkt)]:
            if key not in bucket:
                bucket[key] = {"samples": 0, "wins": 0, "pl": 0.0, "stake": 0.0}
            bucket[key]["samples"] += samples
            bucket[key]["wins"] += wins
            bucket[key]["pl"] += total_pl
            bucket[key]["stake"] += total_stake

    for conf, d in conf_agg.items():
        s, w = d["samples"], d["wins"]
        win_rate = w / s if s > 0 else 0.0
        roi = d["pl"] / d["stake"] if d["stake"] > 0 else 0.0
        weights.by_confidence[conf] = PerformanceSlice(
            samples=s, wins=w, losses=s - w,
            win_rate=round(win_rate, 4), roi=round(roi, 4),
            performance_factor=_factor_from_stats(win_rate, roi, s),
        )

    # ── SL-1: Literature priors — pre-seed confidence tiers before real data ───
    # When a confidence tier has no settled bets yet, the system can't learn from
    # historical data. Instead of returning neutral (1.0), seed a mild literature-
    # derived prior so signals are differentiated from day one.
    # Source: football betting literature + model design thresholds (see _EXPECTED_WIN_RATES).
    # These are overridden by real data as soon as any bets settle for that tier.
    _LITERATURE_PRIORS: dict[str, tuple[float, float]] = {
        # (expected_win_rate, expected_ROI) derived from model probability thresholds
        "High":   (0.63, 0.08),    # High confidence → model expects edge ≥7%
        "Medium": (0.54, 0.02),    # Medium → modest expected value
        "Low":    (0.44, -0.04),   # Low → below break-even on average
    }
    _PRIOR_VIRTUAL_SAMPLES = 4  # small enough that real data quickly dominates via Bayesian smoothing
    for conf_key, (prior_wr, prior_roi) in _LITERATURE_PRIORS.items():
        if conf_key not in weights.by_confidence:
            prior_wins = round(prior_wr * _PRIOR_VIRTUAL_SAMPLES)
            weights.by_confidence[conf_key] = PerformanceSlice(
                samples=_PRIOR_VIRTUAL_SAMPLES,
                wins=prior_wins,
                losses=_PRIOR_VIRTUAL_SAMPLES - prior_wins,
                win_rate=round(prior_wr, 4),
                roi=round(prior_roi, 4),
                performance_factor=1.0,  # hard threshold not met; soft factor applies via _effective_factor
            )

    for mkt, d in market_agg.items():
        s, w = d["samples"], d["wins"]
        win_rate = w / s if s > 0 else 0.0
        roi = d["pl"] / d["stake"] if d["stake"] > 0 else 0.0
        weights.by_market[mkt] = PerformanceSlice(
            samples=s, wins=w, losses=s - w,
            win_rate=round(win_rate, 4), roi=round(roi, 4),
            performance_factor=_factor_from_stats(win_rate, roi, s),
        )

    # ── Query 2: rule-level performance ──────────────────────────────────────
    rule_stmt = (
        select(
            TrackedBet.source_rule_key,
            func.count(TrackedBet.id).label("samples"),
            func.sum(
                case((TrackedBet.result_status == "Won", 1), else_=0)
            ).label("wins"),
            func.sum(TrackedBet.profit_loss).label("total_pl"),
            func.sum(TrackedBet.stake).label("total_stake"),
        )
        .where(
            TrackedBet.result_status.in_(["Won", "Lost"]),
            TrackedBet.source_rule_key.isnot(None),
            TrackedBet.source_rule_key != "",
        )
        .group_by(TrackedBet.source_rule_key)
    )
    if user_id is not None:
        rule_stmt = rule_stmt.where(TrackedBet.user_id == user_id)

    rule_rows = await db.execute(rule_stmt)

    for row in rule_rows.all():
        rule_key = row.source_rule_key
        samples = row.samples or 0
        wins = int(row.wins or 0)
        total_pl = float(row.total_pl or 0)
        total_stake = float(row.total_stake or 0)

        if samples == 0 or not rule_key:
            continue

        win_rate = wins / samples
        roi = total_pl / total_stake if total_stake > 0 else 0.0
        factor = _factor_from_stats(win_rate, roi, samples) if samples >= min_samples else 1.0

        weights.by_rule[rule_key] = PerformanceSlice(
            samples=samples, wins=wins, losses=samples - wins,
            win_rate=round(win_rate, 4), roi=round(roi, 4),
            performance_factor=factor,
        )

        # Auto-suppress rules with consistent under-performance
        if (samples >= AUTO_SUPPRESS_MIN_SAMPLES and factor < AUTO_SUPPRESS_THRESHOLD):
            weights.auto_suppress_rules.add(rule_key)

    # ── Query 3: market × league_tier  — NEW ─────────────────────────────────
    # Join TrackedBet → Fixture to get league_tier per bet.
    tier_stmt = (
        select(
            TrackedBet.market_type,
            Fixture.league_tier,
            func.count(TrackedBet.id).label("samples"),
            func.sum(
                case((TrackedBet.result_status == "Won", 1), else_=0)
            ).label("wins"),
            func.sum(TrackedBet.profit_loss).label("total_pl"),
            func.sum(TrackedBet.stake).label("total_stake"),
        )
        .join(Fixture, TrackedBet.fixture_id == Fixture.id)
        .where(
            TrackedBet.result_status.in_(["Won", "Lost"]),
            Fixture.league_tier.isnot(None),
        )
        .group_by(TrackedBet.market_type, Fixture.league_tier)
    )
    if user_id is not None:
        tier_stmt = tier_stmt.where(TrackedBet.user_id == user_id)

    tier_rows = await db.execute(tier_stmt)

    for row in tier_rows.all():
        mkt = row.market_type or "Unknown"
        tier = row.league_tier
        samples = row.samples or 0
        wins = int(row.wins or 0)
        total_pl = float(row.total_pl or 0)
        total_stake = float(row.total_stake or 0)

        if samples == 0 or not tier:
            continue

        win_rate = wins / samples
        roi = total_pl / total_stake if total_stake > 0 else 0.0
        factor = _factor_from_stats(win_rate, roi, samples) if samples >= min_samples else 1.0

        weights.by_market_tier[(mkt, tier)] = PerformanceSlice(
            samples=samples, wins=wins, losses=samples - wins,
            win_rate=round(win_rate, 4), roi=round(roi, 4),
            performance_factor=factor,
        )

        # Auto-suppress market+tier combos with consistent under-performance
        if samples >= AUTO_SUPPRESS_MIN_SAMPLES and factor < AUTO_SUPPRESS_THRESHOLD:
            weights.auto_suppress_market_tiers.add((mkt, tier))

    # ── Query 4: calibration — expected vs actual win rate per tier ───────────
    # Compare the model's designed expected win rate for each confidence tier
    # against the actual observed win rate. A large gap means the engine is
    # overconfident (or underconfident) and thresholds need adjustment.
    calib_stmt = (
        select(
            TrackedBet.dual_confidence,
            func.count(TrackedBet.id).label("samples"),
            func.sum(
                case((TrackedBet.result_status == "Won", 1), else_=0)
            ).label("wins"),
        )
        .where(TrackedBet.result_status.in_(["Won", "Lost"]))
        .group_by(TrackedBet.dual_confidence)
    )
    if user_id is not None:
        calib_stmt = calib_stmt.where(TrackedBet.user_id == user_id)

    calib_rows = await db.execute(calib_stmt)

    for row in calib_rows.all():
        conf = row.dual_confidence or "Unknown"
        samples = row.samples or 0
        wins = int(row.wins or 0)

        if samples == 0:
            continue

        actual_wr = wins / samples
        expected_wr = _EXPECTED_WIN_RATES.get(conf, 0.50)
        cal_error = expected_wr - actual_wr  # positive = overconfident

        weights.calibration[conf] = CalibrationMetric(
            confidence=conf,
            expected_win_rate=round(expected_wr, 4),
            actual_win_rate=round(actual_wr, 4),
            calibration_error=round(cal_error, 4),
            samples=samples,
            is_overconfident=(
                samples >= MIN_SAMPLES
                and cal_error > _CALIBRATION_ERROR_THRESHOLD
            ),
        )

    # ── Query 5: league × market  — granular self-learning ────────────────────
    # The most specific learning layer: tracks performance for each exact
    # (league, market) pair. With a lower sample threshold (_LEAGUE_MKT_MIN_SAMPLES=5)
    # it activates earlier than the tier-level slice, allowing the engine to
    # stop generating "Away Over 0.5" in Ekstraklasa while keeping it in La Liga.
    # A second query pulls recent-30-day data for the recency bias calculation.
    from sqlalchemy import text as sa_text

    lm_user_filter = "AND user_id = :user_id" if user_id is not None else ""
    lm_params = {"user_id": user_id} if user_id is not None else {}

    lm_all_rows = await db.execute(sa_text(f"""
        SELECT
            lower(trim(league))   AS league_key,
            market_type,
            COUNT(*)              AS n,
            SUM(CASE WHEN result_status = 'Won' THEN 1 ELSE 0 END) AS wins,
            SUM(profit_loss)      AS total_pl,
            SUM(stake)            AS total_stake
        FROM tracked_bets
        WHERE result_status IN ('Won', 'Lost')
          AND league IS NOT NULL
          AND market_type IS NOT NULL
          AND stake > 0
          {lm_user_filter}
        GROUP BY lower(trim(league)), market_type
    """), lm_params)

    lm_recent_rows = await db.execute(sa_text(f"""
        SELECT
            lower(trim(league))   AS league_key,
            market_type,
            COUNT(*)              AS n,
            SUM(profit_loss)      AS total_pl,
            SUM(stake)            AS total_stake
        FROM tracked_bets
        WHERE result_status IN ('Won', 'Lost')
          AND league IS NOT NULL
          AND market_type IS NOT NULL
          AND stake > 0
          {lm_user_filter}
          AND settled_at >= datetime('now', '-{_RECENT_DAYS} days')
        GROUP BY lower(trim(league)), market_type
    """), lm_params)

    # Index recent data for O(1) lookup
    recent_lm: dict[tuple[str, str], tuple[int, float]] = {}
    for row in lm_recent_rows.all():
        key = (row.league_key, row.market_type)
        recent_n = int(row.n or 0)
        recent_pl = float(row.total_pl or 0)
        recent_stake = float(row.total_stake or 0)
        recent_roi_val = recent_pl / recent_stake if recent_stake > 0 else None
        recent_lm[key] = (recent_n, recent_roi_val)

    for row in lm_all_rows.all():
        key = (row.league_key, row.market_type)
        samples = int(row.n or 0)
        wins = int(row.wins or 0)
        total_pl = float(row.total_pl or 0)
        total_stake = float(row.total_stake or 0)

        if samples == 0 or not row.league_key or not row.market_type:
            continue

        win_rate = wins / samples
        roi = total_pl / total_stake if total_stake > 0 else 0.0
        all_time_factor = _factor_from_stats(win_rate, roi, samples) if samples >= _LEAGUE_MKT_MIN_SAMPLES else 1.0

        # Blend with recent performance if available
        recent_n, recent_roi_val = recent_lm.get(key, (0, None))
        recency_f = _recency_adjusted_factor(all_time_factor, recent_roi_val, recent_n)

        weights.by_league_market[key] = PerformanceSlice(
            samples=samples, wins=wins, losses=samples - wins,
            win_rate=round(win_rate, 4), roi=round(roi, 4),
            performance_factor=all_time_factor,
            recent_samples=recent_n,
            recent_roi=round(recent_roi_val, 4) if recent_roi_val is not None else None,
            recency_factor=recency_f,
        )

        # Auto-suppress: 5+ bets, ROI below zero (consistently losing money on this combo)
        if samples >= _LEAGUE_MKT_SUPPRESS_MIN_SAMPLES and roi < _LEAGUE_MKT_SUPPRESS_ROI:
            weights.auto_suppress_league_markets.add(key)

    return weights

"""
calibration.py -- Weekly model calibration audit service.

Computes reliability metrics (Brier score, ECE, per-market calibration gaps)
over settled tracked bets joined to their signal rows.  Designed to be run
weekly to catch model drift before it compounds into sustained losses.

Primary health metric: Brier skill score (vs naive base-rate predictor).
Target: skill > +0.05 across all major markets.
Markets currently known to fail this bar: Home Over 1.5, Under 3.5.

Public API
----------
  compute_calibration_metrics(db, days=90) -> CalibrationReport
  save_snapshot(db, report)                -> None
  load_recent_snapshots(db, n=12)          -> list[dict]
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Brier skill threshold below which a market is flagged as unhealthy.
BRIER_SKILL_TARGET: float = 0.05

# Calibration gap threshold (|actual_hit - mean_model_p|) that triggers a warning.
CALIBRATION_GAP_WARN: float = 0.07

# Minimum bets required before a market appears in the per-market breakdown.
MIN_MARKET_BETS: int = 15


@dataclass
class MarketCalibration:
    market: str
    n: int
    win_rate: float
    mean_model_p: float
    calibration_gap: float      # actual_hit - mean_model_p  (negative = overconfident)
    brier_score: float
    brier_skill: float          # vs naive base-rate benchmark
    flat_roi_pct: float
    flagged: bool               # True when brier_skill < target or |gap| > warn threshold


@dataclass
class ReliabilityBucket:
    lo: float
    hi: float
    n: int
    mean_model_p: float         # bucket midpoint
    actual_hit_rate: float
    gap: float                  # actual - mean_model_p


@dataclass
class ConfidenceTier:
    tier: str
    n: int
    win_rate: float
    mean_model_p: float
    flat_roi_pct: float


@dataclass
class CalibrationReport:
    generated_at: datetime
    window_days: int
    date_min: Optional[str]
    date_max: Optional[str]
    total_bets: int
    signal_join_bets: int       # subset that joined to a signal row

    overall_win_rate: float
    brier_score: float
    brier_naive: float          # naive (base-rate) benchmark
    brier_skill: float          # 1 - brier / brier_naive
    ece: float                  # Expected Calibration Error

    reliability: list[ReliabilityBucket] = field(default_factory=list)
    by_market: list[MarketCalibration] = field(default_factory=list)
    by_confidence: list[ConfidenceTier] = field(default_factory=list)
    flagged_markets: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        """Plain-text summary suitable for logging or Telegram alerts."""
        lines = [
            f"Calibration audit ({self.date_min} to {self.date_max}, n={self.signal_join_bets})",
            f"  Brier skill: {self.brier_skill:+.3f}  ECE: {self.ece:.4f}  WR: {self.overall_win_rate:.1%}",
        ]
        if self.flagged_markets:
            lines.append(f"  FLAGGED: {', '.join(self.flagged_markets)}")
        else:
            lines.append("  No markets flagged.")
        for m in self.by_market:
            roi_str = f"{m.flat_roi_pct:+.1f}%"
            flag_str = " [!]" if m.flagged else ""
            lines.append(
                f"  {m.market:>22}: n={m.n:>4}  WR={m.win_rate:.1%}"
                f"  gap={m.calibration_gap:+.3f}  skill={m.brier_skill:+.3f}"
                f"  ROI={roi_str}{flag_str}"
            )
        return lines


async def compute_calibration_metrics(
    db: AsyncSession,
    days: int = 90,
) -> CalibrationReport:
    """
    Pull settled bets from the last `days` days, join to signal rows,
    and compute calibration metrics.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date()

    # Total settled bets in window
    total_result = await db.execute(
        text(
            "SELECT COUNT(*) FROM tracked_bets "
            "WHERE result_status IN ('Won','Lost') AND event_date >= :since"
        ),
        {"since": since.isoformat()},
    )
    total_bets = total_result.scalar() or 0

    # Date range
    dr = await db.execute(
        text(
            "SELECT MIN(event_date), MAX(event_date) FROM tracked_bets "
            "WHERE result_status IN ('Won','Lost')"
        )
    )
    date_min, date_max = dr.fetchone()

    # Main join query
    rows_result = await db.execute(
        text("""
            SELECT
                COALESCE(s.bayesian_prob, s.poisson_prob) AS model_prob,
                tb.dual_confidence,
                tb.market_type,
                tb.result_status,
                CAST(tb.odds AS REAL) AS odds
            FROM tracked_bets tb
            JOIN signals s ON s.fixture_id = tb.fixture_id AND s.market = tb.market_type
            WHERE tb.result_status IN ('Won','Lost')
              AND tb.event_date >= :since
              AND COALESCE(s.bayesian_prob, s.poisson_prob) IS NOT NULL
        """),
        {"since": since.isoformat()},
    )
    rows = rows_result.fetchall()

    if not rows:
        now = datetime.now(timezone.utc)
        return CalibrationReport(
            generated_at=now, window_days=days,
            date_min=date_min, date_max=date_max,
            total_bets=total_bets, signal_join_bets=0,
            overall_win_rate=0.0, brier_score=0.0,
            brier_naive=0.0, brier_skill=0.0, ece=0.0,
        )

    n = len(rows)
    outcomes = [1 if r[3] == "Won" else 0 for r in rows]
    probs    = [r[0] for r in rows]
    odds_    = [r[4] or 1.5 for r in rows]

    wr = sum(outcomes) / n
    brier = sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / n
    naive = wr * (1 - wr)
    skill = (1 - brier / naive) if naive > 0 else 0.0

    # ECE — bucket by 0.1 intervals
    buckets: dict[float, list[tuple[float, int]]] = defaultdict(list)
    for p, o in zip(probs, outcomes):
        b = min(int(p * 10) / 10, 0.9)
        buckets[b].append((p, o))

    ece_num = 0.0
    reliability: list[ReliabilityBucket] = []
    for b in sorted(buckets):
        bucket_data = buckets[b]
        k = len(bucket_data)
        mid = b + 0.05
        hit = sum(o for _, o in bucket_data) / k
        gap = hit - mid
        ece_num += k * abs(gap)
        reliability.append(ReliabilityBucket(
            lo=b, hi=round(b + 0.1, 1), n=k,
            mean_model_p=mid,
            actual_hit_rate=round(hit, 4),
            gap=round(gap, 4),
        ))
    ece = ece_num / n

    # Per-market breakdown
    mkt_data: dict[str, list[tuple[float, int, float]]] = defaultdict(list)
    for p, conf, mkt, res, odd in rows:
        mkt_data[mkt].append((p, 1 if res == "Won" else 0, odd or 1.5))

    by_market: list[MarketCalibration] = []
    flagged: list[str] = []
    for mkt in sorted(mkt_data, key=lambda m: -len(mkt_data[m])):
        g = mkt_data[mkt]
        if len(g) < MIN_MARKET_BETS:
            continue
        k   = len(g)
        hit = sum(o for _, o, _ in g) / k
        mp  = sum(p for p, _, _ in g) / k
        b_score = sum((p - o) ** 2 for p, o, _ in g) / k
        b_naive = hit * (1 - hit)
        b_skill = (1 - b_score / b_naive) if b_naive > 0 else 0.0
        roi = sum(((o * odd) - 1) for _, o, odd in g) / k * 100
        cal_gap = hit - mp
        is_flagged = (b_skill < BRIER_SKILL_TARGET) or (abs(cal_gap) > CALIBRATION_GAP_WARN)
        mc = MarketCalibration(
            market=mkt, n=k,
            win_rate=round(hit, 4),
            mean_model_p=round(mp, 4),
            calibration_gap=round(cal_gap, 4),
            brier_score=round(b_score, 4),
            brier_skill=round(b_skill, 4),
            flat_roi_pct=round(roi, 2),
            flagged=is_flagged,
        )
        by_market.append(mc)
        if is_flagged:
            flagged.append(mkt)

    # By confidence tier
    conf_data: dict[str, list[tuple[float, int, float]]] = defaultdict(list)
    for p, conf, mkt, res, odd in rows:
        conf_data[conf or "None"].append((p, 1 if res == "Won" else 0, odd or 1.5))

    by_confidence: list[ConfidenceTier] = []
    for tier in ["High", "Medium", "Low", "None"]:
        g = conf_data.get(tier, [])
        if not g:
            continue
        k   = len(g)
        hit = sum(o for _, o, _ in g) / k
        mp  = sum(p for p, _, _ in g) / k
        roi = sum(((o * odd) - 1) for _, o, odd in g) / k * 100
        by_confidence.append(ConfidenceTier(
            tier=tier, n=k,
            win_rate=round(hit, 4),
            mean_model_p=round(mp, 4),
            flat_roi_pct=round(roi, 2),
        ))

    return CalibrationReport(
        generated_at=datetime.now(timezone.utc),
        window_days=days,
        date_min=date_min,
        date_max=date_max,
        total_bets=total_bets,
        signal_join_bets=n,
        overall_win_rate=round(wr, 4),
        brier_score=round(brier, 4),
        brier_naive=round(naive, 4),
        brier_skill=round(skill, 4),
        ece=round(ece, 4),
        reliability=reliability,
        by_market=by_market,
        by_confidence=by_confidence,
        flagged_markets=flagged,
    )


async def save_snapshot(db: AsyncSession, report: CalibrationReport) -> None:
    """
    Persist a slim summary row to calibration_snapshots for trend tracking.
    Only saves top-level metrics + flagged market list — not the full per-bucket data.
    """
    import json
    try:
        flagged_json = json.dumps(report.flagged_markets)
        market_summary = json.dumps([
            {"market": m.market, "n": m.n, "brier_skill": m.brier_skill,
             "cal_gap": m.calibration_gap, "roi_pct": m.flat_roi_pct, "flagged": m.flagged}
            for m in report.by_market
        ])
        await db.execute(
            text("""
                INSERT INTO calibration_snapshots
                    (snapshot_date, window_days, n_bets, win_rate,
                     brier_score, brier_skill, ece, flagged_markets, market_summary)
                VALUES
                    (:snap_date, :window_days, :n_bets, :win_rate,
                     :brier_score, :brier_skill, :ece, :flagged, :mkt_summary)
            """),
            {
                "snap_date":    report.generated_at.date().isoformat(),
                "window_days":  report.window_days,
                "n_bets":       report.signal_join_bets,
                "win_rate":     report.overall_win_rate,
                "brier_score":  report.brier_score,
                "brier_skill":  report.brier_skill,
                "ece":          report.ece,
                "flagged":      flagged_json,
                "mkt_summary":  market_summary,
            },
        )
        await db.commit()
        log.info("Calibration snapshot saved: skill=%+.3f  flagged=%s",
                 report.brier_skill, report.flagged_markets or "none")
    except Exception as exc:
        log.warning("Failed to save calibration snapshot: %s", exc)


async def load_recent_snapshots(db: AsyncSession, n: int = 12) -> list[dict]:
    """Load the n most recent calibration snapshots for trend display."""
    import json
    result = await db.execute(
        text("""
            SELECT snapshot_date, window_days, n_bets, win_rate,
                   brier_score, brier_skill, ece, flagged_markets, market_summary
            FROM calibration_snapshots
            ORDER BY snapshot_date DESC
            LIMIT :n
        """),
        {"n": n},
    )
    rows = result.fetchall()
    out = []
    for r in rows:
        out.append({
            "snapshot_date":  r[0],
            "window_days":    r[1],
            "n_bets":         r[2],
            "win_rate":       r[3],
            "brier_score":    r[4],
            "brier_skill":    r[5],
            "ece":            r[6],
            "flagged_markets": json.loads(r[7]) if r[7] else [],
            "market_summary":  json.loads(r[8]) if r[8] else [],
        })
    return out

"""
backfill_accas.py — Reconstruct historical AI accumulator tickets from settled
Signal + Fixture data and produce a structured JSON performance report.

Usage:
    python backend/scripts/backfill_accas.py

Reads DATABASE_URL from backend/.env; defaults to sqlite+aiosqlite:///./backend/titibet.db
when the env file is absent or the key is unset.

Pool tiering mirrors get_advisor_insights() in advisor_service.py exactly.
Leg settlement uses _score_condition() from settlement.py directly.
"""
import asyncio
import json
import os
import sys
from collections import defaultdict
from functools import reduce
from pathlib import Path

# Allow running from the repo root: python backend/scripts/backfill_accas.py
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# ── Load DATABASE_URL from backend/.env ──────────────────────────────────────

def _load_env() -> str:
    env_path = ROOT / "backend" / ".env"
    default = "sqlite+aiosqlite:///./backend/titibet.db"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    return default

DATABASE_URL = _load_env()

# ── Imports that require sys.path to be set ───────────────────────────────────
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.models.fixture import Fixture
from app.models.signal import Signal
from app.services.settlement import FINAL_STATUSES, VOID_STATUSES, _score_condition

# ── Constants matching advisor_service.py ─────────────────────────────────────
MAX_LEG_ODD = 3.5
MIN_LEGS = 3
MAX_LEGS = 5

OUTPUT_PATH = ROOT / "backend" / "scripts" / "backfill_accas_output.json"


# ── Pool tiering — exact replica of get_advisor_insights logic ────────────────

def _primary_prob(sig: Signal) -> float:
    return max(sig.bayesian_prob or 0.0, sig.poisson_prob or 0.0)


def _select_pool(signals: list[Signal]) -> tuple[str, list[Signal]]:
    t1 = [s for s in signals
          if s.dual_confidence == "High" and s.dual_agreement == "Both"
          and _primary_prob(s) >= 0.70]
    if len(t1) >= MIN_LEGS:
        return "T1", t1

    t2 = [s for s in signals
          if s.dual_confidence == "High" and s.dual_agreement == "Both"]
    if len(t2) >= MIN_LEGS:
        return "T2", t2

    t3 = [s for s in signals
          if s.dual_agreement == "Both" and _primary_prob(s) >= 0.60]
    if len(t3) >= MIN_LEGS:
        return "T3", t3

    t4 = [s for s in signals if _primary_prob(s) >= 0.60]
    if len(t4) >= MIN_LEGS:
        return "T4", t4

    return "fallback", signals


# ── Deterministic leg selection ───────────────────────────────────────────────

def _select_legs(
    pool: list[tuple[Signal, Fixture]],
) -> list[tuple[Signal, Fixture]]:
    """
    Greedy selector ordered by (dual_quality_score DESC, primary_prob DESC).

    Hard constraints matching ACCA_BUILDER prompt:
      - bayesian_best_odd must be > 1.0 and <= 3.5
      - no team appears more than once
    Soft constraint (max 2 legs per league) applied as a hard gate to keep
    the selection deterministic and consistent with advisor_service pool notes.
    """
    sorted_pool = sorted(
        pool,
        key=lambda sp: (sp[0].dual_quality_score or 0.0, _primary_prob(sp[0])),
        reverse=True,
    )

    league_counts: dict[str, int] = {}
    teams_used: set[str] = set()
    selected: list[tuple[Signal, Fixture]] = []

    for sig, fix in sorted_pool:
        if len(selected) >= MAX_LEGS:
            break

        odd = sig.bayesian_best_odd
        if odd is None or odd <= 1.0 or odd > MAX_LEG_ODD:
            continue

        home = (fix.home_team or "").strip()
        away = (fix.away_team or "").strip()
        league = (fix.league or "Unknown").strip()

        if home in teams_used or away in teams_used:
            continue
        if league_counts.get(league, 0) >= 2:
            continue

        selected.append((sig, fix))
        league_counts[league] = league_counts.get(league, 0) + 1
        teams_used.add(home)
        teams_used.add(away)

    return selected


# ── Leg settlement ─────────────────────────────────────────────────────────────

def _settle_leg(sig: Signal, fix: Fixture) -> str:
    """Return 'won', 'lost', or 'void'."""
    status = (fix.status or "").strip().upper()
    if status in VOID_STATUSES:
        return "void"
    if status not in FINAL_STATUSES:
        return "void"
    if fix.home_score is None or fix.away_score is None:
        return "void"

    condition = _score_condition(sig.market)
    if condition is None:
        return "void"

    return "won" if condition(fix.home_score, fix.away_score) else "lost"


# ── Main backfill logic ────────────────────────────────────────────────────────

async def run(db: AsyncSession) -> dict:
    # 1. Collect all eligible settled dates
    date_rows = await db.execute(text("""
        SELECT DISTINCT f.event_date
        FROM signals s
        JOIN fixtures f ON s.fixture_id = f.id
        WHERE f.status IN ('FT', 'AET', 'PEN')
          AND s.dual_confidence IN ('High', 'Medium')
          AND s.dual_agreement IN ('Both', 'Bayesian Only', 'Poisson Only')
          AND f.home_score IS NOT NULL
        ORDER BY f.event_date
    """))
    dates = [row[0] for row in date_rows.all()]
    print(f"Eligible settled dates: {len(dates)}")

    accas: list[dict] = []
    market_stats: dict[str, dict] = defaultdict(lambda: {"won": 0, "lost": 0, "void": 0})
    tier_distribution: dict[str, int] = {"T1": 0, "T2": 0, "T3": 0, "T4": 0, "fallback": 0}
    odds_buckets: dict[str, dict] = {
        "3-6":   {"built": 0, "won": 0},
        "6-12":  {"built": 0, "won": 0},
        "12-20": {"built": 0, "won": 0},
        "20+":   {"built": 0, "won": 0},
    }

    for event_date in dates:
        # Load top 12 signals with joined fixture data, settled fixtures only
        result = await db.execute(
            select(Signal, Fixture)
            .join(Fixture, Signal.fixture_id == Fixture.id)
            .where(
                Fixture.event_date == event_date,
                Fixture.status.in_(FINAL_STATUSES),
                Fixture.home_score.isnot(None),
                Signal.dual_confidence.in_(["High", "Medium"]),
                Signal.dual_agreement.in_(["Both", "Bayesian Only", "Poisson Only"]),
            )
            .order_by(Signal.dual_quality_score.desc().nullslast())
            .limit(12)
        )
        rows = result.all()

        if not rows:
            continue

        signals_only = [sig for sig, _ in rows]
        tier, pool_sigs = _select_pool(signals_only)

        # Map pool signals back to (sig, fix) pairs
        pool_sig_ids = {s.id for s in pool_sigs}
        pool_pairs = [(sig, fix) for sig, fix in rows if sig.id in pool_sig_ids]

        legs = _select_legs(pool_pairs)

        if len(legs) < MIN_LEGS:
            continue

        # Settle each leg
        settled_legs: list[dict] = []
        has_void = False

        for sig, fix in legs:
            result_str = _settle_leg(sig, fix)
            if result_str == "void":
                has_void = True

            market_stats[sig.market][result_str] += 1

            settled_legs.append({
                "home_team":       fix.home_team or "",
                "away_team":       fix.away_team or "",
                "league":          fix.league or "",
                "market":          sig.market or "",
                "odd":             round(sig.bayesian_best_odd or 0.0, 3),
                "dual_confidence": sig.dual_confidence or "",
                "dual_agreement":  sig.dual_agreement or "",
                "bayesian_prob":   round((sig.bayesian_prob or 0.0), 4),
                "poisson_prob":    round((sig.poisson_prob or 0.0), 4),
                "result":          result_str,
            })

        # Acca result — void legs excluded from the ticket's win/loss
        active_legs = [lg for lg in settled_legs if lg["result"] != "void"]
        has_lost = any(lg["result"] == "lost" for lg in active_legs)
        all_void = not active_legs

        if has_void and not has_lost and not all_void:
            acca_result = "void"
        elif has_lost:
            acca_result = "lost"
        elif all_void:
            acca_result = "void"
        else:
            acca_result = "won"

        # Combined odds (product of non-void legs' odds)
        non_void_odds = [lg["odd"] for lg in settled_legs if lg["result"] != "void" and lg["odd"] > 1.0]
        combined_odds: float | None = None
        if non_void_odds:
            product = reduce(lambda x, y: x * y, non_void_odds)
            combined_odds = round(product, 3)

        tier_distribution[tier] = tier_distribution.get(tier, 0) + 1

        if combined_odds and acca_result != "void":
            bucket = (
                "3-6"   if combined_odds < 6 else
                "6-12"  if combined_odds < 12 else
                "12-20" if combined_odds < 20 else
                "20+"
            )
            odds_buckets[bucket]["built"] += 1
            if acca_result == "won":
                odds_buckets[bucket]["won"] += 1

        accas.append({
            "date":          event_date if isinstance(event_date, str) else str(event_date),
            "tier":          tier,
            "pool_size":     len(pool_pairs),
            "legs":          settled_legs,
            "combined_odds": combined_odds,
            "acca_result":   acca_result,
        })

    # Aggregate
    built = [a for a in accas if a["acca_result"] != "skipped"]
    won   = [a for a in built if a["acca_result"] == "won"]
    lost  = [a for a in built if a["acca_result"] == "lost"]
    void  = [a for a in built if a["acca_result"] == "void"]

    # Hit rate denominator excludes void accas
    decidable = won + lost
    hit_rate = round(len(won) / len(decidable) * 100, 1) if decidable else 0.0
    avg_legs = round(sum(len(a["legs"]) for a in built) / len(built), 2) if built else 0.0
    valid_odds = [a["combined_odds"] for a in decidable if a.get("combined_odds")]
    avg_combined_odds = round(sum(valid_odds) / len(valid_odds), 2) if valid_odds else 0.0

    market_win_rates: dict[str, dict] = {}
    for mkt, s in sorted(market_stats.items()):
        total = s["won"] + s["lost"]
        market_win_rates[mkt] = {
            "won":     s["won"],
            "lost":    s["lost"],
            "void":    s["void"],
            "win_pct": round(s["won"] / total * 100, 1) if total else 0.0,
        }

    combined_odds_buckets: dict[str, dict] = {}
    for bucket, d in odds_buckets.items():
        b = d["built"]
        combined_odds_buckets[bucket] = {
            "built":   b,
            "won":     d["won"],
            "hit_pct": round(d["won"] / b * 100, 1) if b else 0.0,
        }

    report = {
        "summary": {
            "dates_analysed":    len(dates),
            "accas_built":       len(built),
            "accas_won":         len(won),
            "accas_lost":        len(lost),
            "accas_void":        len(void),
            "hit_rate_pct":      hit_rate,
            "avg_legs":          avg_legs,
            "avg_combined_odds": avg_combined_odds,
            "tier_distribution": tier_distribution,
            "market_win_rates":  market_win_rates,
            "combined_odds_buckets": combined_odds_buckets,
        },
        "accas": accas,
    }

    return report


async def main() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        report = await run(db)

    await engine.dispose()

    OUTPUT_PATH.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    s = report["summary"]
    print(f"\n{'='*60}")
    print(f"  ACCA BACKFILL REPORT")
    print(f"{'='*60}")
    print(f"  Dates analysed:      {s['dates_analysed']}")
    print(f"  Accas built:         {s['accas_built']}")
    print(f"  Won:                 {s['accas_won']}")
    print(f"  Lost:                {s['accas_lost']}")
    print(f"  Void:                {s['accas_void']}")
    print(f"  Hit rate:            {s['hit_rate_pct']}%")
    print(f"  Avg legs:            {s['avg_legs']}")
    print(f"  Avg combined odds:   {s['avg_combined_odds']}")
    print(f"\n  Tier distribution:")
    for tier, n in s["tier_distribution"].items():
        print(f"    {tier}: {n}")
    print(f"\n  Market leg win rates:")
    for mkt, ms in s["market_win_rates"].items():
        print(f"    {mkt:<22} {ms['won']}W {ms['lost']}L {ms['void']}V  {ms['win_pct']}%")
    print(f"\n  Combined odds buckets:")
    for bucket, bd in s["combined_odds_buckets"].items():
        print(f"    {bucket:<6}  built={bd['built']}  won={bd['won']}  hit={bd['hit_pct']}%")
    print(f"\n  Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())

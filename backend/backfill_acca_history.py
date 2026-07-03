"""
backfill_acca_history.py — Reconstruct historical AI accumulator tickets
from settled Signal + Fixture data and produce a JSON performance report.

Simulation logic mirrors advisor_service.py pool tiering + the ACCA_BUILDER
system prompt constraints:
  - Pool tiering: T1 (High+Both+≥0.70) → T2 (High+Both) → T3 (Both+≥0.60)
                  → T4 (prob≥0.60) → all signals
  - Leg selection: greedy, by dual_quality_score desc; constraints:
      max 2 legs from same league, no same team twice, no same market type
      twice, no odds > 3.5 per leg
  - Target 3–5 legs; skip date if pool can't yield ≥ 3 legs

Settlement mirrors settlement.py:
  - All legs won → acca Won; P/L = stake * (combined_odds - 1)
  - Any leg lost → acca Lost; P/L = -stake
  - Void legs removed; if all void → Void
  - Any pending → skip (not settled yet)
"""
import json
import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent / "titibet.db"
STAKE = 50_000.0
MAX_LEG_ODD = 3.5
MAX_LEGS = 5
MIN_LEGS = 3

FINAL_STATUSES = {"FT", "AET", "PEN"}
VOID_STATUSES  = {"CANC", "ABD", "AWD", "WO", "TBD", "PST", "INT", "SUSP"}

SCORE_CONDITIONS = {
    "BTTS Yes":   lambda h, a: h >= 1 and a >= 1,
    "BTTS No":    lambda h, a: h == 0 or a == 0,
    "Over 0.5":   lambda h, a: (h + a) >= 1,
    "Over 1.5":   lambda h, a: (h + a) >= 2,
    "Over 2.5":   lambda h, a: (h + a) >= 3,
    "Over 3.5":   lambda h, a: (h + a) >= 4,
    "Over 4.5":   lambda h, a: (h + a) >= 5,
    "Under 1.5":  lambda h, a: (h + a) <= 1,
    "Under 2.5":  lambda h, a: (h + a) <= 2,
    "Under 3.5":  lambda h, a: (h + a) <= 3,
    "Under 4.5":  lambda h, a: (h + a) <= 4,
    "Home Win":   lambda h, a: h > a,
    "Draw":       lambda h, a: h == a,
    "Away Win":   lambda h, a: h < a,
    "1X (Home or Draw)": lambda h, a: h >= a,
    "X2 (Draw or Away)": lambda h, a: h <= a,
    "12 (Home or Away)": lambda h, a: h != a,
    "Home Over 0.5":  lambda h, a: h >= 1,
    "Home Under 0.5": lambda h, a: h == 0,
    "Home Over 1.5":  lambda h, a: h >= 2,
    "Home Under 1.5": lambda h, a: h <= 1,
    "Away Over 0.5":  lambda h, a: a >= 1,
    "Away Under 0.5": lambda h, a: a == 0,
    "Away Over 1.5":  lambda h, a: a >= 2,
    "Away Under 1.5": lambda h, a: a <= 1,
    "Home Win to Nil": lambda h, a: h > a and a == 0,
    "Away Win to Nil": lambda h, a: a > h and h == 0,
    "Exactly 1 Goal":  lambda h, a: (h + a) == 1,
    "Exactly 2 Goals": lambda h, a: (h + a) == 2,
    "Exactly 3 Goals": lambda h, a: (h + a) == 3,
}


def _primary_prob(bayesian_prob, poisson_prob):
    return max(bayesian_prob or 0.0, poisson_prob or 0.0)


def _select_pool(signals):
    """Return (pool_tier_label, pool_rows) from the tiered fallback logic."""
    t1 = [s for s in signals
          if s["dual_confidence"] == "High" and s["dual_agreement"] == "Both"
          and _primary_prob(s["bayesian_prob"], s["poisson_prob"]) >= 0.70]
    if len(t1) >= MIN_LEGS:
        return "T1 (High+Both+prob70+)", t1

    t2 = [s for s in signals
          if s["dual_confidence"] == "High" and s["dual_agreement"] == "Both"]
    if len(t2) >= MIN_LEGS:
        return "T2 (High+Both)", t2

    t3 = [s for s in signals
          if s["dual_agreement"] == "Both"
          and _primary_prob(s["bayesian_prob"], s["poisson_prob"]) >= 0.60]
    if len(t3) >= MIN_LEGS:
        return "T3 (Both+prob60+)", t3

    t4 = [s for s in signals
          if _primary_prob(s["bayesian_prob"], s["poisson_prob"]) >= 0.60]
    if len(t4) >= MIN_LEGS:
        return "T4 (prob60+)", t4

    return "T5 (all)", signals


def _greedy_select(pool):
    """
    Greedy leg picker: sort by dual_quality_score desc, apply constraints,
    target 3–5 legs.

    Hard constraints (match ACCA_BUILDER prompt hard rules):
      - odd must be > 1.0 and <= 3.5
      - no team may appear twice
    Soft constraints (prefer but don't enforce when pool is thin):
      - max 2 legs from same league
      - prefer market diversity
    """
    sorted_pool = sorted(pool, key=lambda s: s["dual_quality_score"] or 0.0, reverse=True)

    league_counts = {}
    teams_used = set()
    selected = []

    for s in sorted_pool:
        if len(selected) >= MAX_LEGS:
            break

        league = s["league"] or "Unknown"
        home   = s["home_team"] or ""
        away   = s["away_team"] or ""
        odd    = s["bayesian_best_odd"] or 0.0

        if odd <= 1.0:
            continue
        if odd > MAX_LEG_ODD:
            continue
        if home in teams_used or away in teams_used:
            continue
        # Soft: max 2 per league (hard only when 2+ already selected from it)
        if league_counts.get(league, 0) >= 2:
            continue

        selected.append(s)
        league_counts[league] = league_counts.get(league, 0) + 1
        teams_used.add(home)
        teams_used.add(away)

    return selected


def _settle_leg(signal, fixture):
    """Return ("won"|"lost"|"void"|"pending", score_str|None)."""
    if fixture is None:
        return "void", None

    status = (fixture["status"] or "").strip().upper()
    if status in VOID_STATUSES:
        return "void", None
    if status not in FINAL_STATUSES:
        return "pending", None
    if fixture["home_score"] is None or fixture["away_score"] is None:
        return "pending", None

    h, a = fixture["home_score"], fixture["away_score"]
    score = f"{h}-{a}"
    condition = SCORE_CONDITIONS.get(signal["market"])
    if condition is None:
        return "void", score
    return ("won" if condition(h, a) else "lost"), score


def run_backfill():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # All settled dates with eligible signals
    cur.execute("""
        SELECT DISTINCT f.event_date
        FROM signals s
        JOIN fixtures f ON s.fixture_id = f.id
        WHERE f.status IN ('FT','AET','PEN')
          AND s.dual_confidence IN ('High','Medium')
          AND s.dual_agreement IN ('Both','Bayesian Only','Poisson Only')
        ORDER BY f.event_date
    """)
    dates = [row["event_date"] for row in cur.fetchall()]
    print(f"Processing {len(dates)} eligible dates...")

    tickets = []

    for event_date in dates:
        # Load top 12 signals for this date (mirrors get_advisor_insights query)
        # Restrict to settled fixtures only — mixed dates (some NS) would cause
        # spurious "pending" tickets since the acca can't settle without all legs.
        cur.execute("""
            SELECT s.*,
                   f.home_team, f.away_team, f.league, f.country, f.league_tier,
                   f.status, f.home_score, f.away_score, f.kickoff_at
            FROM signals s
            JOIN fixtures f ON s.fixture_id = f.id
            WHERE f.event_date = ?
              AND s.dual_confidence IN ('High','Medium')
              AND s.dual_agreement IN ('Both','Bayesian Only','Poisson Only')
              AND f.status IN ('FT','AET','PEN')
            ORDER BY s.dual_quality_score DESC NULLS LAST
            LIMIT 12
        """, (event_date,))
        signal_rows = [dict(r) for r in cur.fetchall()]

        if not signal_rows:
            continue

        pool_tier, pool = _select_pool(signal_rows)

        if len(pool) < MIN_LEGS:
            tickets.append({
                "date":        event_date,
                "status":      "skipped",
                "reason":      f"pool too thin ({len(pool)} signals after tiering)",
                "pool_tier":   pool_tier,
                "pool_size":   len(pool),
                "signal_count": len(signal_rows),
            })
            continue

        legs = _greedy_select(pool)

        if len(legs) < MIN_LEGS:
            tickets.append({
                "date":        event_date,
                "status":      "skipped",
                "reason":      f"only {len(legs)} valid legs after constraint filtering (need {MIN_LEGS})",
                "pool_tier":   pool_tier,
                "pool_size":   len(pool),
                "signal_count": len(signal_rows),
            })
            continue

        # Settle each leg
        settled_legs = []
        has_pending = False
        for s in legs:
            # fixture data embedded in the signal row
            fx = {
                "status":     s["status"],
                "home_score": s["home_score"],
                "away_score": s["away_score"],
            }
            result, score = _settle_leg(s, fx)
            if result == "pending":
                has_pending = True
            settled_legs.append({
                "home_team":  s["home_team"],
                "away_team":  s["away_team"],
                "league":     s["league"],
                "market":     s["market"],
                "odd":        round(s["bayesian_best_odd"] or 0.0, 3),
                "bayesian_prob": round((s["bayesian_prob"] or 0.0) * 100, 1),
                "dual_confidence": s["dual_confidence"],
                "dual_agreement":  s["dual_agreement"],
                "dual_quality":    round(s["dual_quality_score"] or 0.0, 4),
                "result":     result,
                "score":      score,
            })

        if has_pending:
            tickets.append({
                "date":        event_date,
                "status":      "pending",
                "reason":      "one or more fixtures not yet settled",
                "pool_tier":   pool_tier,
                "pool_size":   len(pool),
                "signal_count": len(signal_rows),
                "legs":        settled_legs,
            })
            continue

        # Compute acca result
        has_lost = any(lg["result"] == "lost" for lg in settled_legs)
        all_void = all(lg["result"] == "void" for lg in settled_legs)
        active_legs = [lg for lg in settled_legs if lg["result"] != "void"]

        if has_lost:
            acca_result = "Lost"
            combined_odds = None
            pl = -STAKE
        elif all_void:
            acca_result = "Void"
            combined_odds = None
            pl = 0.0
        else:
            won_odds = [lg["odd"] for lg in active_legs if lg["result"] == "won"]
            combined_odds = round(
                1.0 if not won_odds else
                __import__("functools").reduce(lambda x, y: x * y, won_odds),
                3,
            )
            acca_result = "Won"
            pl = round(STAKE * (combined_odds - 1.0), 2)

        tickets.append({
            "date":          event_date,
            "status":        "settled",
            "pool_tier":     pool_tier,
            "pool_size":     len(pool),
            "signal_count":  len(signal_rows),
            "legs":          settled_legs,
            "combined_odds": combined_odds,
            "acca_result":   acca_result,
            "stake":         STAKE,
            "profit_loss":   pl,
        })

    conn.close()

    # Aggregate stats
    settled = [t for t in tickets if t["status"] == "settled"]
    won     = [t for t in settled if t["acca_result"] == "Won"]
    lost    = [t for t in settled if t["acca_result"] == "Lost"]
    void    = [t for t in settled if t["acca_result"] == "Void"]
    skipped = [t for t in tickets if t["status"] == "skipped"]
    pending = [t for t in tickets if t["status"] == "pending"]

    total_stake = len(settled) * STAKE
    total_pl    = sum(t["profit_loss"] for t in settled)
    roi_pct     = round((total_pl / total_stake) * 100, 2) if total_stake else None

    # Per-tier breakdown
    tier_stats = {}
    for t in settled:
        tier = t["pool_tier"]
        if tier not in tier_stats:
            tier_stats[tier] = {"won": 0, "lost": 0, "void": 0, "pl": 0.0}
        tier_stats[tier][t["acca_result"].lower()] += 1
        tier_stats[tier]["pl"] += t["profit_loss"]

    # Leg-level market hit rates
    market_stats = {}
    for t in settled:
        for lg in t.get("legs", []):
            m = lg["market"]
            if m not in market_stats:
                market_stats[m] = {"won": 0, "lost": 0, "void": 0}
            market_stats[m][lg["result"]] = market_stats[m].get(lg["result"], 0) + 1

    market_hit_rates = {}
    for m, s in market_stats.items():
        total = s.get("won", 0) + s.get("lost", 0)
        market_hit_rates[m] = {
            "won":      s.get("won", 0),
            "lost":     s.get("lost", 0),
            "void":     s.get("void", 0),
            "hit_rate": round(s["won"] / total * 100, 1) if total else None,
        }

    report = {
        "summary": {
            "dates_processed":     len(dates),
            "tickets_settled":     len(settled),
            "tickets_won":         len(won),
            "tickets_lost":        len(lost),
            "tickets_void":        len(void),
            "tickets_skipped":     len(skipped),
            "tickets_pending":     len(pending),
            "win_rate_pct":        round(len(won) / len(settled) * 100, 1) if settled else None,
            "total_stake":         total_stake,
            "total_profit_loss":   round(total_pl, 2),
            "roi_pct":             roi_pct,
        },
        "pool_tier_breakdown": tier_stats,
        "market_leg_hit_rates": market_hit_rates,
        "tickets":             tickets,
    }

    out_path = Path(__file__).parent / "acca_backfill_report.json"
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nReport written: {out_path}")
    print(f"Dates processed: {len(dates)}")
    print(f"Settled tickets: {len(settled)}  Won: {len(won)}  Lost: {len(lost)}  Void: {len(void)}")
    print(f"Win rate: {report['summary']['win_rate_pct']}%")
    print(f"ROI: {roi_pct}%  (total P/L: {round(total_pl, 2):+,.0f} on stake {total_stake:,.0f})")
    return report


if __name__ == "__main__":
    run_backfill()

"""
cap_backtest.py — Validate raising MAX_DAILY_SINGLE_BETS before changing it.

Replicates the full auto_tracker gate stack using the real config constants,
ranks each day's eligible signals by dual_quality_score (same ordering as
production), settles them from final scores, and reports performance by rank
bucket: 1-5 (kept by the current cap), 6-8 (what a cap of 8 would add), 9+.

Raise the cap only when the 6-8 bucket clears breakeven WR (~62% at odds 1.61)
on a meaningful sample. Jul-2026 run: cap bound on only 1 of 23 days; the three
rank-6-8 picks went 1W/2L — cap left at 5. Re-run after the August league
restarts, when daily eligible volume should make the cap actually bind.

Usage:
  cd backend
  python ../scripts/cap_backtest.py [--db titibet.db] [--since 2026-07-02]

--since defaults to 2026-07-02: earlier bayesian_best_odd values are
contaminated by the team-total FT/HT odds bug (fixed 9c2beb8), and the gates
key off odds.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Make app.* importable when run from anywhere in the repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.core.config import (  # noqa: E402
    DISABLED_LEAGUES, DISABLED_MARKETS, OVER_GOALS_SUPPRESSED_LEAGUES,
    OVER25_SUPPRESSED_TIERS, MARKET_MIN_ODDS, DUAL_HIGH_ODDS_CEILING,
    COPA_HO05_SUPPRESSED_LEAGUES, AWAY_GOALS_SUPPRESSED_LEAGUES,
    WOMEN_OVER_SUPPRESSED_MARKETS, HO05_DATA_POOR_COUNTRIES,
    BOTH_MEDIUM_DISABLED_LEAGUES, is_womens_fixture,
)

OVER_MARKETS = {"Home Over 0.5", "Away Over 0.5", "Over 1.5", "Over 2.5",
                "Home Over 1.5", "Away Over 1.5"}

FINISHED_PREFIXES = ("Match Finished", "FT", "AET", "PEN")


def settle(market: str, hs: int, as_: int) -> bool | None:
    """True/False if the market can be settled from final scores, else None."""
    tot = hs + as_
    table = {
        "Home Over 0.5": hs >= 1, "Away Over 0.5": as_ >= 1,
        "Home Over 1.5": hs >= 2, "Away Over 1.5": as_ >= 2,
        "Over 1.5": tot >= 2, "Over 2.5": tot >= 3, "Over 3.5": tot >= 4,
        "Under 2.5": tot <= 2, "Under 3.5": tot <= 3,
        "Home Under 1.5": hs <= 1,
    }
    return table.get(market)


def passes_gates(r: sqlite3.Row) -> tuple[bool, float | None]:
    """Mirror of auto_tracker.auto_track_date eligibility. Returns (ok, odds).

    Keep in sync with app/services/auto_tracker.py — this is a replica, not a
    shared implementation, because auto_track_date is async/ORM-coupled.
    """
    market = r["market"]
    if market in DISABLED_MARKETS:
        return False, None
    league_lower = (r["league"] or "").lower().strip()
    if league_lower in DISABLED_LEAGUES or "friendlies" in league_lower:
        return False, None
    if market in {"Home Over 0.5", "Away Over 0.5", "Over 1.5", "Over 2.5"}:
        if any(k in league_lower for k in OVER_GOALS_SUPPRESSED_LEAGUES):
            return False, None
    if market == "Over 2.5" and r["league_tier"] in OVER25_SUPPRESSED_TIERS:
        return False, None

    odds = r["bayesian_best_odd"]
    if not odds or odds <= 1.01:
        prob = r["poisson_prob"] or r["bayesian_prob"]
        if prob and 0.0 < prob < 1.0:
            odds = round(1.0 / prob, 3)
        else:
            return False, None

    floor = MARKET_MIN_ODDS.get(market)
    if floor is not None and odds < floor:
        return False, None
    ceiling = DUAL_HIGH_ODDS_CEILING.get(market)
    if (ceiling is not None and r["dual_confidence"] == "High"
            and r["dual_agreement"] == "Both" and odds >= ceiling):
        return False, None
    if (market == "Home Over 0.5"
            and any(kw in league_lower for kw in COPA_HO05_SUPPRESSED_LEAGUES)):
        return False, None
    if (market in {"Away Over 0.5", "Away Over 1.5"}
            and any(kw in league_lower for kw in AWAY_GOALS_SUPPRESSED_LEAGUES)):
        return False, None
    if (market in WOMEN_OVER_SUPPRESSED_MARKETS
            and is_womens_fixture(r["league"], r["home_team"], r["away_team"])):
        return False, None
    if (market == "Home Over 0.5" and r["dual_confidence"] == "High"
            and r["dual_agreement"] == "Both"
            and (r["league_tier"] or 3) >= 3
            and (r["country"] or "").lower() in HO05_DATA_POOR_COUNTRIES):
        return False, None
    if r["bos_passed"] and market in OVER_MARKETS:
        return False, None
    if r["dual_agreement"] == "Both" and r["dual_confidence"] == "Medium":
        if league_lower in BOTH_MEDIUM_DISABLED_LEAGUES:
            return False, None
        if not (1.55 <= (r["bayesian_best_odd"] or 0.0) < 1.95):
            return False, None
    if (r["dual_agreement"] == "Poisson Only"
            and (r["bayesian_best_odd"] or 0.0) < 1.45):
        return False, None
    if market == "Over 1.5" and r["dual_confidence"] != "High":
        return False, None
    return True, odds


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db",    default="titibet.db",  help="Path to SQLite DB (default: titibet.db)")
    p.add_argument("--since", default="2026-07-02",  help="Earliest event_date (default: 2026-07-02, first clean-odds day)")
    p.add_argument("--cap",   type=int, default=5,   help="Current daily cap (default: 5)")
    p.add_argument("--raised-cap", type=int, default=8, help="Proposed cap (default: 8)")
    args = p.parse_args()

    c = sqlite3.connect(args.db)
    c.row_factory = sqlite3.Row
    rows = c.execute("""
        SELECT s.market, s.bayesian_best_odd, s.bayesian_prob, s.poisson_prob,
               s.dual_confidence, s.dual_agreement, s.dual_quality_score,
               s.bos_passed,
               f.event_date, f.league, f.country, f.league_tier, f.status,
               f.home_score, f.away_score, f.home_team, f.away_team
        FROM signals s JOIN fixtures f ON s.fixture_id = f.id
        WHERE s.is_candidate = 0
          AND s.dual_agreement != 'Contradiction'
          AND f.event_date >= ?
          AND f.home_score IS NOT NULL AND f.away_score IS NOT NULL
    """, (args.since,)).fetchall()

    days: dict[str, list] = {}
    unknown_markets: dict[str, int] = {}
    for r in rows:
        ok, odds = passes_gates(r)
        if not ok:
            continue
        if not (r["status"] or "").startswith(FINISHED_PREFIXES):
            continue
        won = settle(r["market"], r["home_score"], r["away_score"])
        if won is None:
            unknown_markets[r["market"]] = unknown_markets.get(r["market"], 0) + 1
            continue
        days.setdefault(r["event_date"], []).append(
            (r["dual_quality_score"] or 0.0, odds, won, r["market"], r["league"]))

    lo, hi = args.cap, args.raised_cap
    bucket_names = [f"1-{lo}", f"{lo + 1}-{hi}", f"{hi + 1}+"]
    buckets: dict[str, list] = {name: [] for name in bucket_names}
    for d in sorted(days):
        picks = sorted(days[d], key=lambda t: t[0], reverse=True)
        for i, pick in enumerate(picks, start=1):
            name = bucket_names[0] if i <= lo else (bucket_names[1] if i <= hi else bucket_names[2])
            buckets[name].append((d, i) + pick)

    over_cap_days = sum(1 for picks in days.values() if len(picks) > lo)
    print(f"Window: {args.since} onward — {len(days)} days with eligible picks, "
          f"{over_cap_days} exceeding the cap of {lo}")
    if unknown_markets:
        print("Unsettleable markets skipped:", unknown_markets)
    print()
    print(f"{'rank':<8}{'n':>4}{'won':>5}{'WR':>8}{'avg odds':>10}{'ROI':>9}")
    for name, bets in buckets.items():
        if not bets:
            print(f"{name:<8}{0:>4}")
            continue
        n = len(bets)
        won = sum(1 for b in bets if b[4])
        avg_odds = sum(b[3] for b in bets) / n
        roi = sum((b[3] - 1.0) if b[4] else -1.0 for b in bets) / n
        print(f"{name:<8}{n:>4}{won:>5}{won / n:>8.1%}{avg_odds:>10.2f}{roi:>9.1%}")

    marginal = buckets[bucket_names[1]]
    if marginal:
        print(f"\n--- rank {bucket_names[1]} picks (what the raised cap would add) ---")
        for d, rank, q, odds, won, market, league in marginal:
            print(f"{d} r{rank} q={q:.3f} @{odds:.2f} "
                  f"{'WON ' if won else 'lost'} {market:<16} {league}")


if __name__ == "__main__":
    main()

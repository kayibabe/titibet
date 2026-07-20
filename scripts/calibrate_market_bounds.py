"""
calibrate_market_bounds.py — B-3: Empirical probability bounds calibration.

Queries settled tracked bets to compute the 5th–95th percentile of the
model's derived probability per market type, then prints the resulting
MARKET_PROB_BOUNDS dict so it can be pasted into app/core/config.py.

Why this matters:
  The engine uses MARKET_PROB_BOUNDS to gate which signals are surfaced as
  high-confidence. If the bounds are too wide, low-quality signals leak through.
  If too tight, real edges are filtered out. This script derives bounds
  empirically from your actual settled history rather than theoretical guesses.

Usage:
  cd backend
  python ../scripts/calibrate_market_bounds.py [--db titibet.db] [--min-samples 10] [--percentile 5]

Output:
  Prints a Python dict suitable for copy-pasting into config.py.
  Also prints a summary table for human review.
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from collections import defaultdict


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db",          default="titibet.db", help="Path to SQLite DB (default: titibet.db)")
    p.add_argument("--min-samples", type=int, default=10,  help="Minimum settled bets required per market (default: 10)")
    p.add_argument("--percentile",  type=int, default=5,   help="Tail percentile to cut (default: 5 → 5th–95th)")
    p.add_argument("--confidence",  default=None,           help="Filter to a specific confidence tier (High/Medium/Low)")
    p.add_argument("--verbose",     action="store_true",    help="Show raw data per market")
    return p.parse_args()


def percentile(data: list[float], pct: float) -> float:
    """Simple percentile using linear interpolation (equivalent to numpy percentile)."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    n = len(sorted_data)
    idx = pct / 100.0 * (n - 1)
    lo  = int(idx)
    hi  = lo + 1
    if hi >= n:
        return sorted_data[-1]
    frac = idx - lo
    return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])


def main() -> None:
    args = parse_args()

    try:
        conn = sqlite3.connect(args.db)
    except sqlite3.OperationalError as exc:
        print(f"ERROR: Cannot open database '{args.db}': {exc}", file=sys.stderr)
        print("Run from the backend/ directory, or pass --db path/to/titibet.db", file=sys.stderr)
        sys.exit(1)

    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Check tracked_bets table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tracked_bets'")
    if not cur.fetchone():
        print("ERROR: 'tracked_bets' table not found in database.", file=sys.stderr)
        sys.exit(1)

    # Pull relevant columns from settled bets.
    # 'probability' is not stored in tracked_bets — implied probability is derived
    # from 1/odds (no-vig approximation, sufficient for percentile bounding).
    query = """
        SELECT
            market_type,
            dual_confidence,
            odds,
            result_status
        FROM tracked_bets
        WHERE result_status IN ('Won', 'Lost')
          AND market_type IS NOT NULL
          AND odds IS NOT NULL
          AND odds > 1.0
    """
    params: list = []
    if args.confidence:
        query += " AND dual_confidence = ?"
        params.append(args.confidence)

    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("No settled bets found matching criteria.", file=sys.stderr)
        sys.exit(0)

    # Group derived probabilities by market_type
    probs_by_market: dict[str, list[float]] = defaultdict(list)
    wins_by_market:  dict[str, int]         = defaultdict(int)

    for row in rows:
        market = row["market_type"]
        odds   = float(row["odds"])

        # Derive implied probability from bookmaker odds (1/odds approximation)
        prob = 1.0 / odds

        probs_by_market[market].append(prob)
        if row["result_status"] == "Won":
            wins_by_market[market] += 1

    pct_lo = args.percentile
    pct_hi = 100 - args.percentile

    # Build bounds per market
    results: list[dict] = []
    for market, probs in sorted(probs_by_market.items()):
        n = len(probs)
        if n < args.min_samples:
            continue

        lo   = round(percentile(probs, pct_lo), 3)
        hi   = round(percentile(probs, pct_hi), 3)
        mean = round(statistics.mean(probs), 3)
        wins = wins_by_market[market]
        hit  = round(wins / n * 100, 1)

        results.append({
            "market":  market,
            "n":       n,
            "wins":    wins,
            "hit_pct": hit,
            "mean":    mean,
            "lo":      lo,
            "hi":      hi,
        })

    if not results:
        print(f"No market had >= {args.min_samples} settled bets. Lower --min-samples.", file=sys.stderr)
        sys.exit(0)

    # ── Summary table ──────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"MARKET PROBABILITY BOUNDS - {pct_lo}th-{pct_hi}th percentile")
    conf_label = f" (confidence={args.confidence})" if args.confidence else " (all confidence tiers)"
    print(f"Source: {len(rows)} settled bets{conf_label}")
    print("=" * 70)
    print(f"{'Market':<30} {'N':>6} {'Hit%':>6} {'Mean':>6} {'p{lo}':>6} {'p{hi}':>6}".format(lo=pct_lo, hi=pct_hi))
    print("-" * 70)

    for r in results:
        flag = ""
        if r["hit_pct"] < 40:
            flag = " [!] low hit rate"
        elif r["hit_pct"] > 75:
            flag = " [+] strong hit rate"
        print(
            f"{r['market']:<30} {r['n']:>6} {r['hit_pct']:>5.1f}%"
            f" {r['mean']:>6.3f} {r['lo']:>6.3f} {r['hi']:>6.3f}{flag}"
        )

    if args.verbose:
        print("\n--- Raw probability lists ---")
        for r in results:
            m = r["market"]
            raw = sorted(probs_by_market[m])
            print(f"\n{m} (n={r['n']}):")
            print("  " + "  ".join(f"{v:.3f}" for v in raw))

    # ── Generated config dict ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("# Paste into app/core/config.py -> MARKET_PROB_BOUNDS")
    print(f"# Generated from {len(rows)} settled bets | {pct_lo}th-{pct_hi}th percentile")
    print("=" * 70)
    print("MARKET_PROB_BOUNDS: dict[str, tuple[float, float]] = {")
    for r in results:
        print(f'    "{r["market"]}": ({r["lo"]}, {r["hi"]}),  # n={r["n"]}, hit={r["hit_pct"]}%')
    print("}")

    # ── Calibration alert ──────────────────────────────────────────────────────
    low_hit = [r for r in results if r["hit_pct"] < 40 and r["n"] >= 20]
    if low_hit:
        print("\n[!] CALIBRATION ALERT -- markets with hit rate < 40% (n>=20):")
        for r in low_hit:
            print(f"   {r['market']}: {r['hit_pct']}% hit rate over {r['n']} bets -- model may be systematically overconfident")
        print("   Consider raising the min_prob threshold for these markets in config.py.")

    high_hit = [r for r in results if r["hit_pct"] > 78 and r["n"] >= 20]
    if high_hit:
        print("\n[+] HIGH PERFORMERS -- markets with hit rate > 78% (n>=20):")
        for r in high_hit:
            print(f"   {r['market']}: {r['hit_pct']}% hit rate over {r['n']} bets -- strong empirical edge")


if __name__ == "__main__":
    main()

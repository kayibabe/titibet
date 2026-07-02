"""
run_cs_backtest.py — Correct Score calibration sweep.

Replays the single-best-EV-scoreline-per-fixture strategy over historical
fixtures with stored CS odds, sweeping (min EV x odds ceiling x Dixon-Coles
rho) on a chronological 70/30 train/validate split.

Usage:
    python run_cs_backtest.py --from 2026-03-01 --to 2026-06-30

Acceptance gate (per plan): a combo with n >= 100 train bets and positive ROI
on BOTH splits. If nothing qualifies, CS stays off.
"""
import argparse
import asyncio
import sys
from datetime import date

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

EV_GRID = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]
CEILING_GRID = [10.0, 15.0, 20.0]
RHO_GRID = [0.0, -0.05, -0.10, -0.15]
MIN_TRAIN_BETS = 100


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


async def run(date_from: date, date_to: date):
    from app.core.database import AsyncSessionLocal
    from app.services.backtester import collect_cs_backtest_data, evaluate_cs_params

    print("=" * 78)
    print(f"CORRECT SCORE CALIBRATION SWEEP  {date_from} -> {date_to}")
    print("=" * 78)

    async with AsyncSessionLocal() as db:
        samples = await collect_cs_backtest_data(db, date_from=date_from, date_to=date_to)

    print(f"\nSamples collected: {len(samples)} finished fixtures with CS board + lambdas")
    if len(samples) < 150:
        print("Too few samples for a meaningful 70/30 split. Aborting.")
        return

    split = int(len(samples) * 0.7)
    train, valid = samples[:split], samples[split:]
    print(f"Train: {len(train)} fixtures ({train[0].event_date} -> {train[-1].event_date})")
    print(f"Valid: {len(valid)} fixtures ({valid[0].event_date} -> {valid[-1].event_date})")

    # Matrices depend only on (fixture, rho) — cache per rho across the sweep.
    results = []
    for rho in RHO_GRID:
        matrices: dict = {}
        for ceiling in CEILING_GRID:
            for min_ev in EV_GRID:
                r = evaluate_cs_params(
                    train, min_ev=min_ev, odds_ceiling=ceiling, rho=rho,
                    matrices=matrices,
                )
                results.append(r)

    results.sort(key=lambda r: r["roi"], reverse=True)

    print("\n" + "-" * 78)
    print("TRAIN SPLIT — all combos (sorted by ROI, top 20)")
    print("-" * 78)
    print(f"{'EV>=':>5} {'ceil':>5} {'rho':>6} | {'n':>5} {'wins':>5} {'hit%':>6} {'ROI%':>7} {'avgOdd':>7} {'avgEV':>6}")
    for r in results[:20]:
        print(f"{r['min_ev']:>5.2f} {r['odds_ceiling']:>5.0f} {r['rho']:>6.2f} | "
              f"{r['n']:>5} {r['wins']:>5} {r['hit_rate']:>6.1f} {r['roi']:>+7.1f} "
              f"{(r['avg_odds'] or 0):>7.2f} {(r['avg_ev'] or 0):>6.3f}")

    qualifying = [r for r in results if r["n"] >= MIN_TRAIN_BETS and r["roi"] > 0]
    print(f"\nCombos with n >= {MIN_TRAIN_BETS} train bets and positive train ROI: {len(qualifying)}")

    if not qualifying:
        print("\nVERDICT: NO qualifying combo. Per the acceptance gate, CS stays OFF.")
        return

    print("\n" + "-" * 78)
    print("VALIDATION SPLIT — qualifying combos re-run out-of-sample (top 10 by train ROI)")
    print("-" * 78)
    print(f"{'EV>=':>5} {'ceil':>5} {'rho':>6} | {'trainROI':>9} | {'n':>5} {'wins':>5} {'hit%':>6} {'validROI':>9}")
    passed = []
    for r in qualifying[:10]:
        matrices: dict = {}
        v = evaluate_cs_params(
            valid, min_ev=r["min_ev"], odds_ceiling=r["odds_ceiling"], rho=r["rho"],
            matrices=matrices,
        )
        flag = ""
        if v["roi"] > 0 and v["n"] > 0:
            passed.append((r, v))
            flag = "  <-- PASSES"
        print(f"{r['min_ev']:>5.2f} {r['odds_ceiling']:>5.0f} {r['rho']:>6.2f} | "
              f"{r['roi']:>+9.1f} | {v['n']:>5} {v['wins']:>5} {v['hit_rate']:>6.1f} {v['roi']:>+9.1f}{flag}")

    print("\n" + "=" * 78)
    if not passed:
        print("VERDICT: no combo survives validation. Per the acceptance gate, CS stays OFF.")
        return

    best_r, best_v = passed[0]
    print("VERDICT: PASS — recommended config constants:")
    print(f"  CS_MIN_EV       = {best_r['min_ev']}")
    print(f"  CS_ODDS_CEILING = {best_r['odds_ceiling']}")
    print(f"  CS_DC_RHO       = {best_r['rho']}")
    print(f"  (train: n={best_r['n']} ROI {best_r['roi']:+.1f}% | valid: n={best_v['n']} ROI {best_v['roi']:+.1f}%)")

    # Scoreline distribution sanity check for the winning combo (train+valid).
    from collections import Counter
    all_picks = best_r["picks"] + best_v["picks"]
    dist = Counter(p["scoreline"] for p in all_picks)
    won_by = Counter(p["scoreline"] for p in all_picks if p["won"])
    print("\n  Picks by scoreline (won/total):")
    for line, n in dist.most_common():
        print(f"    {line:<6} {won_by.get(line, 0)}/{n}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", type=_parse_date, required=True)
    ap.add_argument("--to", dest="date_to", type=_parse_date, required=True)
    args = ap.parse_args()
    asyncio.run(run(args.date_from, args.date_to))

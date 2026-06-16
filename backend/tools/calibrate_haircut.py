#!/usr/bin/env python3
"""
calibrate_haircut.py — turn real spot-check prices into per-market exec haircuts.

WHY THIS EXISTS
---------------
TiTiBet scores value against a displayed PROXY price (William Hill / sharp from
API-Football). You actually bet at betPawa / 888bets / Betway, whose odds are
shorter. Fix 1 haircuts the proxy down to a realistic EXECUTION price before
computing EV / Kelly / is_value — but the haircut size must come from reality,
not a guess. This script measures it.

Soft books do NOT shade uniformly: they crush favourites / overs harder than
longshots / unders. So the haircut is calibrated PER MARKET.

HOW TO USE
----------
1. Copy haircut_spotcheck_template.csv and fill it with real prices: for the same
   selection on the same fixture, record the proxy_odd TiTiBet shows and the
   my_book_odd your book offers. 5+ rows per market is a good start.
2. Run:  python tools/calibrate_haircut.py tools/haircut_spotcheck.csv
3. It writes backend/exec_haircuts.json, which config.py loads automatically into
   EXEC_HAIRCUT_BY_MARKET. No code changes, no restart logic — next signal batch
   and next backtest use the calibrated numbers.

The per-market haircut is the mean of (1 - my_book_odd / proxy_odd) across rows,
clamped to [0, 0.55]. Markets you never spot-check keep the global default
(Settings.exec_odds_haircut).
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

CLAMP_MAX = 0.55  # refuse absurd haircuts that usually mean a data-entry error
OUT_PATH = Path(__file__).resolve().parents[1] / "exec_haircuts.json"


def _parse_rows(csv_path: Path) -> list[tuple[str, float, float, str]]:
    rows: list[tuple[str, float, float, str]] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header_seen = False
        for raw in reader:
            if not raw or not raw[0].strip() or raw[0].lstrip().startswith("#"):
                continue
            if not header_seen and raw[0].strip().lower() == "market":
                header_seen = True
                continue
            try:
                market = raw[0].strip()
                proxy = float(raw[1])
                mine = float(raw[2])
                note = raw[3].strip() if len(raw) > 3 else ""
            except (ValueError, IndexError):
                print(f"  ! skipping malformed row: {raw}", file=sys.stderr)
                continue
            if proxy <= 1.0 or mine <= 1.0:
                print(f"  ! skipping row with odd <= 1.0: {raw}", file=sys.stderr)
                continue
            rows.append((market, proxy, mine, note))
    return rows


def calibrate(rows: list[tuple[str, float, float, str]]) -> dict[str, dict]:
    by_market: dict[str, list[float]] = defaultdict(list)
    for market, proxy, mine, _ in rows:
        haircut = 1.0 - (mine / proxy)
        haircut = max(0.0, min(CLAMP_MAX, haircut))
        by_market[market].append(haircut)
    out: dict[str, dict] = {}
    for market, cuts in by_market.items():
        mean = sum(cuts) / len(cuts)
        out[market] = {
            "haircut": round(mean, 4),
            "n": len(cuts),
            "min": round(min(cuts), 4),
            "max": round(max(cuts), 4),
        }
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        print("usage: python tools/calibrate_haircut.py <spotcheck.csv>")
        return 1
    csv_path = Path(sys.argv[1])
    if not csv_path.is_file():
        print(f"error: file not found: {csv_path}", file=sys.stderr)
        return 1

    rows = _parse_rows(csv_path)
    if not rows:
        print("error: no valid rows found.", file=sys.stderr)
        return 1

    detail = calibrate(rows)
    flat = {market: d["haircut"] for market, d in sorted(detail.items())}

    print(f"\nCalibrated {len(flat)} market(s) from {len(rows)} spot-check rows:\n")
    print(f"  {'market':<24} {'haircut':>8} {'n':>4} {'min':>7} {'max':>7}")
    print(f"  {'-'*24} {'-'*8} {'-'*4} {'-'*7} {'-'*7}")
    for market, d in sorted(detail.items()):
        print(f"  {market:<24} {d['haircut']*100:7.1f}% {d['n']:>4} "
              f"{d['min']*100:6.1f}% {d['max']*100:6.1f}%")

    OUT_PATH.write_text(json.dumps(flat, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH}")
    print("config.py will load these into EXEC_HAIRCUT_BY_MARKET on next import.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

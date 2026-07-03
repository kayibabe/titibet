"""
fix_contaminated_odds.py — Repair 1st-half odds contamination in signals and tracked_bets.

Background:
  Before commit 1ca778f (2026-07-02) HOME_GOALS_MARKET_NAMES included
  'Home Team Total Goals(1st Half)', so _best_odd_for_market() took the MAX
  across FT + 1H market types. Pinnacle prices the 1H Home Over 0.5 market
  at ~2x the FT price (e.g. fixture 1007: FT=1.43, 1H=2.23). The code picked
  the higher 1H price, inflating every signal and tracked bet in this market.

What this script does:
  1. For each Home Over 0.5 / Away Over 0.5 signal that has a FT price in
     market_snapshots, compare stored bayesian_best_odd against the correct
     FT best odds.
  2. Where stored > FT + 0.01 (contaminated), update:
       - signals.bayesian_best_odd = best FT odd
       - tracked_bets.odds = best FT odd  (for bets linked to that fixture)
       - tracked_bets.profit_loss recalculated for Won bets
  3. Prints a summary + writes fix_contaminated_odds_report.json next to itself.

Dry-run mode:
  Set DRY_RUN = True to see what would change without touching the DB.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DB_PATH    = Path(__file__).parent.parent / "titibet.db"
OUT_PATH   = Path(__file__).parent / "fix_contaminated_odds_report.json"
DRY_RUN    = False   # set True to preview without writing

# FT market name → correct selection_name for each signal market
FT_LOOKUP: dict[str, tuple[str, str]] = {
    "Home Over 0.5": ("Total - Home", "Over 0.5"),
    "Away Over 0.5": ("Total - Away", "Over 0.5"),
}


def _best_ft_odd(cur: sqlite3.Cursor, fixture_id: int, market_type: str, selection: str) -> float | None:
    cur.execute(
        """
        SELECT MAX(odds)
        FROM market_snapshots
        WHERE fixture_id = ?
          AND market_type = ?
          AND selection_name = ?
        """,
        (fixture_id, market_type, selection),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else None


def run() -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    signal_updates:  list[dict] = []   # signals that need updating
    bet_updates:     list[dict] = []   # tracked_bets that need updating
    skipped_no_snap: list[dict] = []   # signals where no FT snapshot exists
    already_clean:   list[dict] = []   # signals where stored <= FT + 0.01

    for signal_market, (snap_market, snap_selection) in FT_LOOKUP.items():
        cur.execute(
            """
            SELECT s.id, s.fixture_id, s.bayesian_best_odd, s.bayesian_bookmaker,
                   f.home_team, f.away_team, f.event_date, f.league
            FROM signals s
            JOIN fixtures f ON s.fixture_id = f.id
            WHERE s.market = ?
              AND s.bayesian_best_odd IS NOT NULL
            ORDER BY f.event_date
            """,
            (signal_market,),
        )
        signals = [dict(r) for r in cur.fetchall()]

        for sig in signals:
            ft_odd = _best_ft_odd(cur, sig["fixture_id"], snap_market, snap_selection)

            if ft_odd is None:
                skipped_no_snap.append({
                    "signal_id":   sig["id"],
                    "fixture_id":  sig["fixture_id"],
                    "match":       f"{sig['home_team']} vs {sig['away_team']}",
                    "date":        sig["event_date"],
                    "market":      signal_market,
                    "stored_odd":  sig["bayesian_best_odd"],
                    "reason":      "no FT snapshot in market_snapshots",
                })
                continue

            stored = sig["bayesian_best_odd"]
            if stored <= ft_odd + 0.01:
                already_clean.append({
                    "signal_id": sig["id"],
                    "stored":    stored,
                    "ft_odd":    ft_odd,
                })
                continue

            signal_updates.append({
                "signal_id":   sig["id"],
                "fixture_id":  sig["fixture_id"],
                "match":       f"{sig['home_team']} vs {sig['away_team']}",
                "date":        sig["event_date"],
                "league":      sig["league"],
                "market":      signal_market,
                "old_odd":     stored,
                "new_odd":     ft_odd,
            })

    # For each contaminated signal, find the linked TrackedBet rows
    signal_fixture_map = {su["signal_id"]: su for su in signal_updates}
    # We need to match bets by fixture_id + market_type, not by signal FK
    contaminated_fixtures: dict[str, float] = {}   # "fixture_id|market" → new_odd
    for su in signal_updates:
        key = f"{su['fixture_id']}|{su['market']}"
        contaminated_fixtures[key] = su["new_odd"]

    if contaminated_fixtures:
        # Fetch all tracked_bets for contaminated fixture+market combos
        for signal_market in FT_LOOKUP:
            cur.execute(
                """
                SELECT id, fixture_id, market_type, selection_name,
                       odds, stake, result_status, profit_loss, match_name, event_date
                FROM tracked_bets
                WHERE market_type = ?
                """,
                (signal_market,),
            )
            bets = [dict(r) for r in cur.fetchall()]

            for bet in bets:
                key = f"{bet['fixture_id']}|{bet['market_type']}"
                new_odd = contaminated_fixtures.get(key)
                if new_odd is None:
                    continue

                old_odd = bet["odds"]
                if old_odd is None or old_odd <= new_odd + 0.01:
                    continue   # already correct or not contaminated

                stake = bet["stake"] or 1.0
                status = bet["result_status"]
                old_pl = bet["profit_loss"]

                if status == "Won":
                    new_pl = round(stake * (new_odd - 1.0), 2)
                elif status == "Lost":
                    new_pl = round(-stake, 2)   # loss is stake-agnostic of odds
                else:
                    new_pl = old_pl   # Pending / Void — leave unchanged

                bet_updates.append({
                    "bet_id":      bet["id"],
                    "fixture_id":  bet["fixture_id"],
                    "match_name":  bet["match_name"],
                    "date":        str(bet["event_date"]),
                    "market":      bet["market_type"],
                    "status":      status,
                    "old_odd":     old_odd,
                    "new_odd":     new_odd,
                    "stake":       stake,
                    "old_pl":      old_pl,
                    "new_pl":      new_pl if status in ("Won", "Lost") else old_pl,
                    "pl_delta":    round((new_pl - old_pl), 2) if status in ("Won", "Lost") else 0.0,
                })

    # ── Compute summary stats ─────────────────────────────────────────────────
    total_pl_delta = round(sum(u["pl_delta"] for u in bet_updates), 2)
    won_bets   = [u for u in bet_updates if u["status"] == "Won"]
    lost_bets  = [u for u in bet_updates if u["status"] == "Lost"]
    pend_bets  = [u for u in bet_updates if u["status"] not in ("Won", "Lost")]

    print(f"\n{'='*64}")
    print(f"  ODDS CONTAMINATION REPAIR — {'DRY RUN' if DRY_RUN else 'LIVE'}")
    print(f"{'='*64}")
    print(f"  Signals to fix:          {len(signal_updates)}")
    print(f"  Signals already clean:   {len(already_clean)}")
    print(f"  Signals no snapshot:     {len(skipped_no_snap)}")
    print(f"  TrackedBets to fix:      {len(bet_updates)}")
    print(f"    Won (odds+P/L update): {len(won_bets)}")
    print(f"    Lost (odds only):      {len(lost_bets)}")
    print(f"    Pending (odds only):   {len(pend_bets)}")
    print(f"  Total P/L delta:         {total_pl_delta:+,.2f}")
    if won_bets:
        old_avg = sum(u["old_odd"] for u in won_bets) / len(won_bets)
        new_avg = sum(u["new_odd"] for u in won_bets) / len(won_bets)
        print(f"  Won bets avg odds:       {old_avg:.3f} -> {new_avg:.3f}")

    if DRY_RUN:
        print("\n  ** DRY RUN — no DB changes made **")
    else:
        # ── Apply updates inside a transaction ────────────────────────────────
        conn.execute("BEGIN")
        try:
            for su in signal_updates:
                cur.execute(
                    "UPDATE signals SET bayesian_best_odd = ? WHERE id = ?",
                    (su["new_odd"], su["signal_id"]),
                )

            for bu in bet_updates:
                if bu["status"] in ("Won", "Lost"):
                    cur.execute(
                        "UPDATE tracked_bets SET odds = ?, profit_loss = ? WHERE id = ?",
                        (bu["new_odd"], bu["new_pl"], bu["bet_id"]),
                    )
                else:
                    cur.execute(
                        "UPDATE tracked_bets SET odds = ? WHERE id = ?",
                        (bu["new_odd"], bu["bet_id"]),
                    )

            conn.commit()
            print(f"\n  Updated {len(signal_updates)} signal rows.")
            print(f"  Updated {len(bet_updates)} tracked_bet rows.")
        except Exception as exc:
            conn.rollback()
            print(f"\n  ERROR — rolled back: {exc}")
            raise

    conn.close()

    report = {
        "dry_run": DRY_RUN,
        "summary": {
            "signals_fixed":          len(signal_updates),
            "signals_already_clean":  len(already_clean),
            "signals_no_snapshot":    len(skipped_no_snap),
            "bets_fixed":             len(bet_updates),
            "bets_won":               len(won_bets),
            "bets_lost":              len(lost_bets),
            "bets_pending":           len(pend_bets),
            "total_pl_delta":         total_pl_delta,
        },
        "signal_updates":  signal_updates,
        "bet_updates":     bet_updates,
        "skipped_signals": skipped_no_snap,
    }

    OUT_PATH.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n  Report: {OUT_PATH}")

    return report


if __name__ == "__main__":
    run()

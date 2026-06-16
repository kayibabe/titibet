"""
backfill_auto_track.py — Retroactively create system_auto TrackedBet rows for
every High/Medium confidence signal already stored in the database.

Idempotent: loads all existing system_auto bets into memory first, then only
inserts rows that are genuinely new.  Safe to run multiple times.

After inserting, runs settle_bets_for_date(None) so every finished match gets
Won/Lost immediately — giving the full system performance history at once.

Usage:
    cd backend && python backfill_auto_track.py
"""
from __future__ import annotations

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models import Signal, Fixture, TrackedBet
from app.models.user import User  # must import to register users table in metadata
from app.services.settlement import settle_bets_for_date

BACKFILL_BANKROLL = 1000.0   # notional bankroll for stake sizing


def _grade(q: float | None) -> str | None:
    if q is None:
        return None
    if q >= 0.08:  return "A"
    if q >= 0.055: return "B"
    if q >= 0.035: return "C"
    return "D"


async def backfill() -> None:
    async with AsyncSessionLocal() as db:

        # ── 1. Load existing system_auto bets to avoid duplicate inserts ──────
        existing_rows = list(
            (await db.execute(
                select(TrackedBet.fixture_id, TrackedBet.market_type, TrackedBet.bookmaker)
                .where(TrackedBet.source_rule_key == "system_auto")
            )).all()
        )
        existing_keys: set[tuple] = {
            (r.fixture_id, r.market_type, r.bookmaker) for r in existing_rows
        }
        print(f"Existing system_auto bets: {len(existing_keys)}")

        # ── 2. Load all High/Medium signals with their fixture ─────────────────
        rows = list(
            (await db.execute(
                select(Signal, Fixture)
                .join(Fixture, Signal.fixture_id == Fixture.id)
                .where(Signal.dual_confidence.in_(["High", "Medium"]))
                .order_by(Fixture.event_date.asc())
            )).all()
        )
        print(f"Signals to process: {len(rows)}")

        # ── 3. Insert missing bets ─────────────────────────────────────────────
        inserted = 0
        skipped  = 0
        no_odds  = 0

        for signal, fixture in rows:
            bookmaker = signal.bayesian_bookmaker or "Best Available"
            key = (signal.fixture_id, signal.market, bookmaker)

            if key in existing_keys:
                skipped += 1
                continue

            # Determine odds
            odds = signal.bayesian_best_odd
            if not odds or odds <= 1.01:
                prob = signal.poisson_prob or signal.bayesian_prob
                if prob and 0.0 < prob < 1.0:
                    odds = round(1.0 / prob, 3)
                else:
                    no_odds += 1
                    continue

            # Stake from recommended pct or flat 1% default
            if signal.dual_recommended_stake_pct:
                stake = round(signal.dual_recommended_stake_pct * BACKFILL_BANKROLL, 2)
                stake = max(1.0, stake)
            else:
                stake = round(BACKFILL_BANKROLL * 0.01, 2)

            match_name = f"{fixture.home_team} vs {fixture.away_team}"

            bet = TrackedBet(
                user_id           = None,                     # anonymous system row
                fixture_id        = signal.fixture_id,
                bookmaker         = bookmaker,
                event_date        = fixture.event_date,
                match_name        = match_name,
                league            = fixture.league,
                market_type       = signal.market,
                selection_name    = signal.market,
                odds              = odds,
                stake             = stake,
                recommended_stake_pct = signal.dual_recommended_stake_pct,
                source_rule_key   = "system_auto",
                source_rule_label = "System Auto-Pick",
                signal_grade      = _grade(signal.dual_quality_score),
                dual_confidence   = signal.dual_confidence,
                dual_agreement    = signal.dual_agreement,
                result_status     = "Pending",
            )
            db.add(bet)
            existing_keys.add(key)   # prevent intra-batch duplicates
            inserted += 1

            if inserted % 200 == 0:
                await db.commit()
                print(f"  ... committed {inserted} bets so far")

        await db.commit()
        print(
            f"\nInsert complete: {inserted} new | {skipped} already existed | "
            f"{no_odds} skipped (no valid odds)"
        )

        # ── 4. Settle all pending system bets ─────────────────────────────────
        print("\nRunning settlement on all pending bets …")
        result = await settle_bets_for_date(db, run_date=None, user_id=None)
        await db.commit()
        settled = result.get("settled", 0)
        skips   = {k: v for k, v in result.items() if k != "settled"}
        print(f"Settled: {settled} bets   {skips}")

        # ── 5. Summary ────────────────────────────────────────────────────────
        final_rows = list(
            (await db.execute(
                select(TrackedBet)
                .where(TrackedBet.source_rule_key == "system_auto")
            )).scalars().all()
        )
        won     = sum(1 for b in final_rows if b.result_status == "Won")
        lost    = sum(1 for b in final_rows if b.result_status == "Lost")
        pending = sum(1 for b in final_rows if b.result_status == "Pending")
        settled_total = won + lost
        hit_rate = round(won / settled_total * 100, 1) if settled_total else None
        total_pl = sum(b.profit_loss for b in final_rows if b.result_status != "Pending")
        total_stake = sum(b.stake for b in final_rows if b.result_status != "Pending")
        roi = round(total_pl / total_stake * 100, 1) if total_stake else None

        print("\n--- System Performance Summary ---")
        print(f"  Total auto-tracked: {len(final_rows)}")
        print(f"  Won:     {won}")
        print(f"  Lost:    {lost}")
        print(f"  Pending: {pending}")
        if hit_rate is not None:
            print(f"  Hit rate: {hit_rate}%")
        if roi is not None:
            print(f"  ROI:      {roi}%  (on {total_stake:.0f} staked)")
        print("----------------------------------")


if __name__ == "__main__":
    asyncio.run(backfill())

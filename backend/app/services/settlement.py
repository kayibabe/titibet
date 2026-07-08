"""
settlement.py — Auto-settlement for tracked bets based on match scores.
Ported from TiTiBet settlement.py. Supports all active markets.
"""
from __future__ import annotations

import logging
from datetime import datetime, date, timezone
from typing import Callable

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Fixture, TrackedBet

logger = logging.getLogger("titibet.settlement")

# Imported lazily (inside settle_bets_for_date) to avoid circular imports.
# Computes closing odds + CLV for each just-settled bet from market_snapshots.

FINAL_STATUSES = {"FT", "AET", "PEN"}
# Terminal statuses that are not playable — treat as Void for settlement purposes
VOID_STATUSES  = {"CANC", "ABD", "AWD", "WO", "TBD", "PST", "INT", "SUSP"}


def _fixture_is_final(fixture: Fixture) -> bool:
    st = (fixture.status or "").strip().upper()
    return st in FINAL_STATUSES


# Markets that can be auto-settled from final score (must match `market_type` on TrackedBet).
# Keep in sync with app.core.config MARKETS for totals / team markets / exact goals.
SCORE_SETTLEABLE_MARKETS: dict[str, Callable[[int, int], bool]] = {
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


def _settle_bet(bet: TrackedBet, won: bool) -> None:
    if won:
        bet.result_status = "Won"
        bet.profit_loss = round(bet.stake * (bet.odds - 1.0), 2)
    else:
        bet.result_status = "Lost"
        bet.profit_loss = round(-bet.stake, 2)
    bet.settled_at = datetime.now(timezone.utc)


def _normalize_market(market_type: str | None) -> str:
    """Normalize LLM-generated market labels to canonical settlement names."""
    mt = (market_type or "").strip()
    # "Over 0.5 Goals" / "Over 0.5 goals" / "Under 2.5 Goals" → strip suffix (case-insensitive)
    if mt.lower().endswith(" goals") and not mt.lower().startswith("exactly"):
        mt = mt[: -len(" goals")].strip()
    # "Home Team Over 0.5" / "Away Team Over 1.5" → canonical names
    mt = mt.replace("Home Team Over ", "Home Over ").replace("Home Team Under ", "Home Under ")
    mt = mt.replace("Away Team Over ", "Away Over ").replace("Away Team Under ", "Away Under ")
    return mt


def _score_condition(market_type: str | None) -> Callable[[int, int], bool] | None:
    mt = _normalize_market(market_type)
    return SCORE_SETTLEABLE_MARKETS.get(mt)


async def settle_bets_for_date(
    db: AsyncSession,
    run_date: date | None = None,
    user_id: int | None = None,
) -> dict:
    """
    Auto-settle all pending bets for fixtures that are now final.

    Returns a dict:
      { settled, skip_no_fixture, skip_not_final, skip_no_score, skip_no_market }
    """
    query = select(TrackedBet).where(TrackedBet.result_status == "Pending")
    if run_date:
        # Include bets explicitly on this date AND bets with no stored event_date
        # (manually entered picks where kickoff_at was null). The loop validates
        # each bet against its fixture's final status anyway, so NULL-date bets
        # that aren't ready to settle simply pass through harmlessly.
        query = query.where(
            or_(
                TrackedBet.event_date == run_date,
                TrackedBet.event_date.is_(None),
            )
        )
    if user_id is not None:
        query = query.where(TrackedBet.user_id == user_id)

    result = await db.execute(query)
    bets: list[TrackedBet] = list(result.scalars().all())

    settled           = 0
    skip_no_fixture   = 0
    skip_not_final    = 0
    skip_no_score     = 0
    skip_no_market    = 0
    just_settled: list[TrackedBet] = []

    for bet in bets:
        if bet.fixture_id is None:
            skip_no_fixture += 1
            continue
        fixture = await db.get(Fixture, bet.fixture_id)
        if fixture is None or not _fixture_is_final(fixture):
            skip_not_final += 1
            logger.debug(
                "settle: skipping bet %s — fixture %s status=%s",
                bet.id, bet.fixture_id,
                fixture.status if fixture else "not_found",
            )
            continue
        if fixture.home_score is None or fixture.away_score is None:
            skip_no_score += 1
            logger.warning(
                "settle: skipping bet %s — fixture %s (%s vs %s) is %s but score is null",
                bet.id, bet.fixture_id,
                fixture.home_team, fixture.away_team, fixture.status,
            )
            continue

        condition = _score_condition(bet.market_type)
        if condition is None:
            skip_no_market += 1
            logger.debug(
                "settle: skipping bet %s — no condition for market_type=%r",
                bet.id, bet.market_type,
            )
            continue

        won = condition(fixture.home_score, fixture.away_score)
        _settle_bet(bet, won)
        just_settled.append(bet)
        settled += 1

    logger.info(
        "settle_bets_for_date: settled=%d  skip_no_fixture=%d  "
        "skip_not_final=%d  skip_no_score=%d  skip_no_market=%d",
        settled, skip_no_fixture, skip_not_final, skip_no_score, skip_no_market,
    )

    await db.commit()

    # Compute CLV for each just-settled bet while market_snapshots are still present.
    if just_settled:
        from app.services.clv import compute_clv_for_bet
        clv_updated = 0
        for bet in just_settled:
            closing, clv_pct = await compute_clv_for_bet(bet, db)
            if closing is not None:
                bet.closing_odds = closing
                bet.clv_pct = clv_pct
                clv_updated += 1
        if clv_updated:
            await db.commit()
        logger.info("CLV: computed for %d/%d just-settled bets", clv_updated, len(just_settled))

    # Also settle any pending accumulator bets
    acca_info = await settle_acca_bets(db)

    return {
        "settled":          settled + acca_info["acca_settled"],
        "acca_settled":     acca_info["acca_settled"],
        "skip_no_fixture":  skip_no_fixture,
        "skip_not_final":   skip_not_final,
        "skip_no_score":    skip_no_score,
        "skip_no_market":   skip_no_market,
    }


async def settle_acca_bets(db: AsyncSession) -> dict:
    """
    Auto-settle pending accumulator bets (source_rule_key='acca_advisory').

    Rules (standard accumulator settlement):
    - All legs Won → ticket Wins; P/L = stake × (combined_odds − 1)
    - Any leg Lost → ticket Lost; P/L = −stake
    - Void leg(s) → removed; remaining legs decide the ticket; adjusted
      odds = product of non-void leg odds; P/L = stake × (adjusted_odds − 1)
    - All legs Void → ticket Void; P/L = 0
    - Any fixture not yet final → ticket stays Pending

    Per-leg result ("won"/"lost"/"void"/"pending") and score are written back
    into notes.legs on every pass — even while the ticket as a whole is still
    Pending — so the Tracker page can show each leg's outcome as matches
    finish, not just once the entire ticket settles.
    """
    import json as _json

    q = select(TrackedBet).where(
        TrackedBet.result_status == "Pending",
        TrackedBet.source_rule_key == "acca_advisory",
    )
    result = await db.execute(q)
    acca_bets: list[TrackedBet] = list(result.scalars().all())

    settled = 0
    notes_updated = 0

    for bet in acca_bets:
        try:
            notes_data = _json.loads(bet.notes or "{}")
            legs = notes_data.get("legs", [])
        except Exception:
            continue

        if not legs or bet.event_date is None:
            continue

        leg_outcomes: list[tuple[str, float] | None] = []  # ("won"|"lost"|"void", odd) or None if pending
        notes_changed = False

        for leg in legs:
            home_team = (leg.get("home_team") or "").strip()
            away_team = (leg.get("away_team") or "").strip()
            market    = (leg.get("market") or "").strip()
            odd       = float(leg.get("odd") or 1.0)

            fixture: Fixture | None = None
            if leg.get("fixture_id"):
                fixture = await db.get(Fixture, leg["fixture_id"])
            if fixture is None and home_team and away_team:
                fixture = await db.scalar(
                    select(Fixture).where(
                        Fixture.event_date == bet.event_date,
                        Fixture.home_team  == home_team,
                        Fixture.away_team  == away_team,
                    )
                )

            if fixture is None or not market:
                outcome = ("void", odd)
            else:
                fx_status = (fixture.status or "").strip().upper()
                if fx_status in VOID_STATUSES:
                    outcome = ("void", odd)
                elif not _fixture_is_final(fixture) or fixture.home_score is None or fixture.away_score is None:
                    outcome = None
                else:
                    condition = _score_condition(market)
                    if condition is None:
                        outcome = ("void", odd)
                    else:
                        won = condition(fixture.home_score, fixture.away_score)
                        outcome = ("won" if won else "lost", odd)

            leg_outcomes.append(outcome)

            new_result = outcome[0] if outcome else "pending"
            new_score = (
                f"{fixture.home_score}-{fixture.away_score}"
                if fixture and fixture.home_score is not None and new_result != "void"
                else None
            )
            if leg.get("result") != new_result or leg.get("score") != new_score:
                leg["result"] = new_result
                leg["score"] = new_score
                notes_changed = True

        if notes_changed:
            notes_data["legs"] = legs
            bet.notes = _json.dumps(notes_data)
            notes_updated += 1

        if any(outcome is None for outcome in leg_outcomes):
            continue  # one or more fixtures still pending — ticket stays Pending

        decided = [outcome for outcome in leg_outcomes if outcome is not None]
        has_lost = any(outcome == "lost" for outcome, _ in decided)
        all_void = all(outcome == "void" for outcome, _ in decided)

        if has_lost:
            bet.result_status = "Lost"
            bet.profit_loss   = round(-bet.stake, 2)
        elif all_void:
            bet.result_status = "Void"
            bet.profit_loss   = 0.0
        else:
            # Won — multiply odds of non-void legs only
            adjusted_odds = 1.0
            for outcome, odd in decided:
                if outcome == "won":
                    adjusted_odds *= odd
            bet.result_status = "Won"
            bet.profit_loss   = round(bet.stake * (adjusted_odds - 1.0), 2)

        bet.settled_at = datetime.now(timezone.utc)
        settled += 1
        logger.info(
            "settle_acca: bet %s → %s  P/L=%.2f",
            bet.id, bet.result_status, bet.profit_loss,
        )

    if settled or notes_updated:
        await db.commit()

    logger.info(
        "settle_acca_bets: settled %d acca bet(s), %d leg-result update(s)",
        settled, notes_updated,
    )
    return {"acca_settled": settled}


async def refresh_stale_fixtures_and_settle(db: AsyncSession) -> dict:
    """
    Refresh stale fixture statuses and settle all pending bets.

    API call strategy — batch by date, not by fixture:
      OLD: 1 call per fixture  → N calls (quota-expensive, fails at 17+ fixtures)
      NEW: 1 call per unique event_date → typically 1-3 calls regardless of bet count

    Each date call fetches ALL fixtures for that day, so we update every
    stale fixture for a date in a single round-trip.

    Returns: { refreshed_fixtures, settled, voided, errors, api_calls_made }
    """
    from app.services.api_client import fetch_fixtures, get_quota_info

    # 1. Load all pending bets
    result = await db.execute(
        select(TrackedBet).where(TrackedBet.result_status == "Pending")
    )
    pending: list[TrackedBet] = list(result.scalars().all())

    if not pending:
        return {
            "refreshed_fixtures": 0, "settled": 0,
            "voided": 0, "errors": 0, "api_calls_made": 0,
        }

    # 2. Load all relevant fixtures and group by event_date
    fixture_ids = {b.fixture_id for b in pending if b.fixture_id is not None}
    fixtures_by_id: dict[int, Fixture] = {}
    for fid in fixture_ids:
        fix = await db.get(Fixture, fid)
        if fix:
            fixtures_by_id[fid] = fix

    # Group stale fixture IDs by their event_date (skip already-final ones)
    now = datetime.now(timezone.utc)
    dates_to_fetch: dict[str, list[int]] = {}   # date_str → [fixture DB ids]

    for fid, fixture in fixtures_by_id.items():
        # Already final with scores — nothing to do
        if fixture.status in FINAL_STATUSES and fixture.home_score is not None and fixture.away_score is not None:
            continue
        if not fixture.external_fixture_id:
            continue
        # NS with future kickoff — game hasn't started, skip to save quota
        if fixture.status == "NS" and fixture.kickoff_at is not None:
            kickoff = fixture.kickoff_at
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
            if kickoff > now:
                logger.debug(
                    "refresh_stale: skipping %s vs %s — NS, kickoff not reached",
                    fixture.home_team, fixture.away_team,
                )
                continue

        event_date = (
            fixture.event_date.isoformat()
            if fixture.event_date
            else fixture.kickoff_at.date().isoformat()
            if fixture.kickoff_at else None
        )
        if not event_date:
            continue
        dates_to_fetch.setdefault(event_date, []).append(fid)

    if not dates_to_fetch:
        logger.info("refresh_stale: all pending fixtures are already final or NS-future — no API calls needed")
        settle_info = await settle_bets_for_date(db, None)
        return {
            "refreshed_fixtures": 0,
            "settled":            settle_info["settled"],
            "voided":             0,
            "errors":             0,
            "api_calls_made":     0,
            **{k: settle_info[k] for k in ("skip_no_fixture", "skip_not_final", "skip_no_score", "skip_no_market")},
        }

    quota = get_quota_info()
    logger.info(
        "refresh_stale: %d unique date(s) to refresh for %d fixtures "
        "(quota: %s/%s remaining)",
        len(dates_to_fetch),
        sum(len(v) for v in dates_to_fetch.values()),
        quota.get("remaining", "?"), quota.get("limit", "?"),
    )

    # 3. One API call per date — fetch all fixtures for that date
    refreshed  = 0
    voided_bets = 0
    errors     = 0
    api_calls  = 0

    for date_str, fids_for_date in dates_to_fetch.items():
        try:
            rows = await fetch_fixtures(date_str, force=True)
            api_calls += 1
        except Exception as exc:
            logger.warning("refresh_stale: date batch fetch failed for %s: %s", date_str, exc)
            errors += len(fids_for_date)
            continue

        # Build lookup: external_fixture_id → fresh data
        fresh: dict[int, dict] = {
            row["external_fixture_id"]: row
            for row in rows
            if row.get("external_fixture_id")
        }

        for fid in fids_for_date:
            fixture = fixtures_by_id.get(fid)
            if fixture is None:
                continue

            data = fresh.get(fixture.external_fixture_id)
            if data is None:
                logger.warning(
                    "refresh_stale: ext_id=%s (%s vs %s) not found in date batch for %s",
                    fixture.external_fixture_id,
                    fixture.home_team, fixture.away_team, date_str,
                )
                errors += 1
                continue

            new_status = (data.get("status") or "").strip().upper()
            new_home   = data.get("home_score")
            new_away   = data.get("away_score")
            logger.info(
                "refresh_stale: %s (%s vs %s) %s → %s  score=%s-%s",
                fixture.external_fixture_id,
                fixture.home_team, fixture.away_team,
                fixture.status, new_status,
                new_home, new_away,
            )

            fixture.status = new_status or fixture.status
            # Only overwrite scores when the API returns real values — never
            # clobber an existing score with None (API-Football sometimes returns
            # null goals for a short window even after a game goes FT).
            if new_home is not None:
                fixture.home_score = new_home
            if new_away is not None:
                fixture.away_score = new_away
            refreshed += 1

            if new_status in VOID_STATUSES:
                for bet in pending:
                    if bet.fixture_id == fid:
                        bet.result_status = "Void"
                        bet.profit_loss   = 0.0
                        bet.settled_at    = datetime.now(timezone.utc)
                        voided_bets += 1

    await db.commit()

    quota_after = get_quota_info()
    logger.info(
        "refresh_stale: done — %d fixture(s) refreshed via %d API call(s) "
        "(quota remaining: %s)",
        refreshed, api_calls, quota_after.get("remaining", "?"),
    )

    # 4. Settle all pending bets now that statuses are up to date
    settle_info = await settle_bets_for_date(db, None)

    return {
        "refreshed_fixtures": refreshed,
        "settled":            settle_info["settled"],
        "voided":             voided_bets,
        "errors":             errors,
        "api_calls_made":     api_calls,
        **{k: settle_info[k] for k in ("skip_no_fixture", "skip_not_final", "skip_no_score", "skip_no_market")},
    }

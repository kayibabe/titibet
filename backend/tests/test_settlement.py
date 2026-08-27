"""
Tests for app/services/settlement.py — the money-critical path.

Covers:
  * Market condition lambdas (score/HT/corners settlement maps)
  * LLM label normalization (_normalize_market)
  * Scope routing (_settlement_scope)
  * P/L math on win / loss (_settle_bet)
  * settle_bets_for_date end-to-end against an in-memory DB:
      wins, losses, voids, not-final skips, null-score skips,
      unknown-market skips, and stale-bet age-out voiding.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models import Fixture, TrackedBet
from app.services.settlement import (
    CORNERS_SETTLEABLE_MARKETS,
    HT_SETTLEABLE_MARKETS,
    SCORE_SETTLEABLE_MARKETS,
    _fixture_is_final,
    _normalize_market,
    _score_condition,
    _settle_bet,
    _settlement_scope,
    settle_bets_for_date,
)

# ---------------------------------------------------------------------------
# Pure-function tests — market condition lambdas
# ---------------------------------------------------------------------------

# (market, home, away, expected_won)
SCORE_CASES = [
    ("BTTS Yes", 1, 1, True), ("BTTS Yes", 2, 0, False),
    ("BTTS No", 2, 0, True), ("BTTS No", 1, 1, False),
    ("Over 2.5", 2, 1, True), ("Over 2.5", 1, 1, False),
    ("Under 2.5", 1, 1, True), ("Under 2.5", 2, 1, False),
    ("Under 3.5", 2, 1, True), ("Under 3.5", 2, 2, False),
    ("Over 0.5", 0, 1, True), ("Over 0.5", 0, 0, False),
    ("Home Win", 2, 1, True), ("Home Win", 1, 1, False),
    ("Away Win", 0, 1, True), ("Away Win", 1, 1, False),
    ("Draw", 1, 1, True), ("Draw", 2, 1, False),
    ("1X (Home or Draw)", 1, 1, True), ("1X (Home or Draw)", 0, 1, False),
    ("X2 (Draw or Away)", 0, 1, True), ("X2 (Draw or Away)", 2, 1, False),
    ("12 (Home or Away)", 2, 1, True), ("12 (Home or Away)", 1, 1, False),
    ("Home Over 0.5", 1, 0, True), ("Home Over 0.5", 0, 3, False),
    ("Away Over 1.5", 0, 2, True), ("Away Over 1.5", 0, 1, False),
    ("Home Win to Nil", 2, 0, True), ("Home Win to Nil", 2, 1, False),
    ("Away Win to Nil", 0, 1, True), ("Away Win to Nil", 1, 2, False),
    ("Exactly 2 Goals", 1, 1, True), ("Exactly 2 Goals", 2, 1, False),
]


@pytest.mark.parametrize("market,h,a,expected", SCORE_CASES)
def test_score_settleable_markets(market, h, a, expected):
    assert SCORE_SETTLEABLE_MARKETS[market](h, a) is expected


@pytest.mark.parametrize("market,h,a,expected", [
    ("Over 0.5 1H", 1, 0, True), ("Over 0.5 1H", 0, 0, False),
    ("Under 0.5 1H", 0, 0, True), ("Under 0.5 1H", 0, 1, False),
    ("Over 1.5 1H", 1, 1, True), ("Over 1.5 1H", 1, 0, False),
    ("Under 1.5 1H", 1, 0, True), ("Under 1.5 1H", 1, 1, False),
])
def test_ht_settleable_markets(market, h, a, expected):
    assert HT_SETTLEABLE_MARKETS[market](h, a) is expected


@pytest.mark.parametrize("market,h,a,expected", [
    ("Over 8.5 Corners", 5, 4, True), ("Over 8.5 Corners", 4, 4, False),
    ("Under 9.5 Corners", 5, 4, True), ("Under 9.5 Corners", 5, 5, False),
])
def test_corners_settleable_markets(market, h, a, expected):
    assert CORNERS_SETTLEABLE_MARKETS[market](h, a) is expected


# ---------------------------------------------------------------------------
# _normalize_market — LLM label canonicalisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Over 2.5 Goals", "Over 2.5"),
    ("Over 0.5 goals", "Over 0.5"),
    ("Under 3.5 Goals", "Under 3.5"),
    ("Exactly 2 Goals", "Exactly 2 Goals"),     # 'Exactly' prefix must NOT be stripped
    ("Home Team Over 0.5", "Home Over 0.5"),
    ("Away Team Under 1.5", "Away Under 1.5"),
    ("  BTTS Yes  ", "BTTS Yes"),
    (None, ""),
    ("", ""),
])
def test_normalize_market(raw, expected):
    assert _normalize_market(raw) == expected


def test_normalized_labels_resolve_to_conditions():
    """LLM-style labels must map onto real settlement conditions."""
    assert _score_condition("Over 2.5 Goals") is SCORE_SETTLEABLE_MARKETS["Over 2.5"]
    assert _score_condition("Home Team Over 0.5") is SCORE_SETTLEABLE_MARKETS["Home Over 0.5"]
    assert _score_condition("Nonexistent Market") is None


@pytest.mark.parametrize("market,scope", [
    ("Over 2.5", "ft"),
    ("Over 0.5 1H", "ht"),
    ("Over 9.5 Corners", "corners"),
    ("Totally Unknown", "ft"),   # unknown defaults to full-time
])
def test_settlement_scope(market, scope):
    assert _settlement_scope(market) == scope


# ---------------------------------------------------------------------------
# _settle_bet — P/L math
# ---------------------------------------------------------------------------

def _bet(**kw) -> TrackedBet:
    defaults = dict(
        bookmaker="TestBook", match_name="A vs B", market_type="Over 2.5",
        selection_name="Over 2.5", odds=2.0, stake=10.0, result_status="Pending",
    )
    defaults.update(kw)
    return TrackedBet(**defaults)


def test_settle_bet_won_pl():
    bet = _bet(odds=1.85, stake=10.0)
    _settle_bet(bet, won=True)
    assert bet.result_status == "Won"
    assert bet.profit_loss == pytest.approx(8.5)
    assert bet.settled_at is not None


def test_settle_bet_lost_pl():
    bet = _bet(odds=1.85, stake=10.0)
    _settle_bet(bet, won=False)
    assert bet.result_status == "Lost"
    assert bet.profit_loss == pytest.approx(-10.0)


def test_fixture_is_final_statuses():
    for st, expected in [("FT", True), ("AET", True), ("PEN", True),
                         ("ft", True), (" FT ", True),
                         ("NS", False), ("1H", False), (None, False), ("", False)]:
        assert _fixture_is_final(Fixture(external_fixture_id=1, home_team="A", away_team="B", status=st)) is expected


# ---------------------------------------------------------------------------
# settle_bets_for_date — end-to-end against in-memory DB
# ---------------------------------------------------------------------------

def _fixture(ext_id: int, **kw) -> Fixture:
    defaults = dict(
        external_fixture_id=ext_id, home_team=f"Home{ext_id}", away_team=f"Away{ext_id}",
        league="Test League", event_date=date(2026, 8, 26), status="FT",
        home_score=2, away_score=1,
    )
    defaults.update(kw)
    return Fixture(**defaults)


async def test_settle_win_loss_and_void(db):
    fx_final = _fixture(101, home_score=2, away_score=1)           # 3 goals
    fx_cancelled = _fixture(102, status="CANC", home_score=None, away_score=None)
    fx_live = _fixture(103, status="1H", home_score=0, away_score=0)
    db.add_all([fx_final, fx_cancelled, fx_live])
    await db.flush()

    win = _bet(fixture_id=fx_final.id, market_type="Over 2.5", event_date=fx_final.event_date, odds=1.9, stake=10)
    loss = _bet(fixture_id=fx_final.id, market_type="Under 2.5", selection_name="Under 2.5", event_date=fx_final.event_date, odds=2.1, stake=10)
    void = _bet(fixture_id=fx_cancelled.id, market_type="Over 2.5", event_date=fx_cancelled.event_date)
    pending = _bet(fixture_id=fx_live.id, market_type="Over 2.5", event_date=fx_live.event_date)
    db.add_all([win, loss, void, pending])
    await db.commit()

    result = await settle_bets_for_date(db, run_date=date(2026, 8, 26))

    assert result["settled"] == 2
    assert result["voided"] == 1
    assert result["skip_not_final"] == 1

    assert win.result_status == "Won"
    assert win.profit_loss == pytest.approx(9.0)
    assert loss.result_status == "Lost"
    assert loss.profit_loss == pytest.approx(-10.0)
    assert void.result_status == "Void"
    assert void.profit_loss == 0.0
    assert pending.result_status == "Pending"


async def test_settle_skips_final_fixture_with_null_score(db):
    fx = _fixture(201, status="FT", home_score=None, away_score=None)
    db.add(fx)
    await db.flush()
    bet = _bet(fixture_id=fx.id, event_date=fx.event_date)
    db.add(bet)
    await db.commit()

    result = await settle_bets_for_date(db, run_date=fx.event_date)
    assert result["skip_no_score"] == 1
    assert bet.result_status == "Pending"


async def test_settle_skips_unknown_market(db):
    fx = _fixture(301)
    db.add(fx)
    await db.flush()
    bet = _bet(fixture_id=fx.id, market_type="Martian Handicap", event_date=fx.event_date)
    db.add(bet)
    await db.commit()

    result = await settle_bets_for_date(db, run_date=fx.event_date)
    assert result["skip_no_market"] == 1
    assert bet.result_status == "Pending"


async def test_settle_ht_market_uses_ht_score(db):
    # FT score 3 total, but HT score 0-0 — an HT Over bet must LOSE.
    fx = _fixture(401, home_score=2, away_score=1, home_score_ht=0, away_score_ht=0)
    db.add(fx)
    await db.flush()
    bet = _bet(fixture_id=fx.id, market_type="Over 0.5 1H", selection_name="Over 0.5 1H", event_date=fx.event_date)
    db.add(bet)
    await db.commit()

    await settle_bets_for_date(db, run_date=fx.event_date)
    assert bet.result_status == "Lost"


async def test_settle_corners_market_uses_corner_counts(db):
    fx = _fixture(501, home_corners=6, away_corners=5)  # 11 corners
    db.add(fx)
    await db.flush()
    bet = _bet(fixture_id=fx.id, market_type="Over 10.5 Corners", selection_name="Over 10.5 Corners", event_date=fx.event_date)
    db.add(bet)
    await db.commit()

    await settle_bets_for_date(db, run_date=fx.event_date)
    assert bet.result_status == "Won"


async def test_ageout_voids_stale_pending_bets_only_on_global_run(db):
    stale_date = date.today() - timedelta(days=10)
    fx = _fixture(601, event_date=stale_date, status="NS", home_score=None, away_score=None)
    db.add(fx)
    await db.flush()
    stale = _bet(fixture_id=fx.id, event_date=stale_date)
    db.add(stale)
    await db.commit()

    # Dated run: stale bet must NOT be aged out.
    await settle_bets_for_date(db, run_date=stale_date)
    assert stale.result_status == "Pending"

    # Global run (run_date=None): stale bet must be voided.
    result = await settle_bets_for_date(db, run_date=None)
    assert stale.result_status == "Void"
    assert stale.profit_loss == 0.0
    assert result["voided"] == 1


async def test_settle_normalizes_llm_market_labels(db):
    fx = _fixture(701, home_score=1, away_score=2)  # 3 goals
    db.add(fx)
    await db.flush()
    bet = _bet(fixture_id=fx.id, market_type="Over 2.5 Goals", selection_name="Over 2.5 Goals", event_date=fx.event_date, odds=2.0, stake=5.0)
    db.add(bet)
    await db.commit()

    await settle_bets_for_date(db, run_date=fx.event_date)
    assert bet.result_status == "Won"
    assert bet.profit_loss == pytest.approx(5.0)

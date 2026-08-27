"""
Tests for app/services/clv.py — Closing Line Value computation.

Covers:
  * CLV formula: (closing / bet_odds − 1) × 100
  * 4-hour pre-kickoff snapshot window: inside counts, outside is ignored
    (no wide fallback — a missing CLV is honest, a tautological one is not)
  * Best (max) odds selection across bookmakers
  * Market scoping: "Over 1.5" full-game totals never priced from team totals
  * Selection remapping: BTTS Yes → "Yes", Home Win to Nil → "Home", etc.
  * Guard clauses: missing fixture_id / market / odds
  * compute_clv_all: skip-already-computed vs force recompute, user scoping
  * _best_odds_from_api_rows parsing helper
"""
from datetime import date, datetime, timedelta

import pytest

from app.models import Fixture, TrackedBet
from app.models.odds import MarketSnapshot
from app.services.clv import (
    _best_odds_from_api_rows,
    compute_clv_all,
    compute_clv_for_bet,
)

EVENT_DATE = date(2026, 8, 26)
KICKOFF = datetime(2026, 8, 26, 15, 0, 0)  # naive UTC, matching storage convention


def _fixture(ext_id: int, **kw) -> Fixture:
    defaults = dict(
        external_fixture_id=ext_id, home_team=f"Home{ext_id}", away_team=f"Away{ext_id}",
        league="Test League", event_date=EVENT_DATE, kickoff_at=KICKOFF, status="FT",
    )
    defaults.update(kw)
    return Fixture(**defaults)


def _bet(fixture: Fixture, **kw) -> TrackedBet:
    defaults = dict(
        bookmaker="TestBook", match_name="A vs B", market_type="Over 2.5",
        selection_name="Over 2.5", odds=2.0, stake=10.0, result_status="Pending",
        fixture_id=fixture.id, event_date=EVENT_DATE,
    )
    defaults.update(kw)
    return TrackedBet(**defaults)


def _snap(fixture: Fixture, *, market_type="Goals Over/Under", selection="Over 2.5",
          odds=2.0, pulled_at=None, bookmaker="BookA") -> MarketSnapshot:
    return MarketSnapshot(
        fixture_id=fixture.id, bookmaker=bookmaker, market_type=market_type,
        selection_name=selection, odds=odds,
        pulled_at=pulled_at or (KICKOFF - timedelta(hours=1)),
    )


# ---------------------------------------------------------------------------
# compute_clv_for_bet — core math + window semantics
# ---------------------------------------------------------------------------

async def test_positive_clv_when_closing_higher_than_bet(db):
    fx = _fixture(3001)
    db.add(fx)
    await db.flush()
    bet = _bet(fx, odds=2.0)
    db.add_all([bet, _snap(fx, odds=2.2)])
    await db.commit()

    closing, clv = await compute_clv_for_bet(bet, db)
    assert closing == pytest.approx(2.2)
    assert clv == pytest.approx(10.0)  # (2.2/2.0 − 1) × 100


async def test_negative_clv_when_closing_lower_than_bet(db):
    fx = _fixture(3002)
    db.add(fx)
    await db.flush()
    bet = _bet(fx, odds=2.0)
    db.add_all([bet, _snap(fx, odds=1.8)])
    await db.commit()

    closing, clv = await compute_clv_for_bet(bet, db)
    assert closing == pytest.approx(1.8)
    assert clv == pytest.approx(-10.0)


async def test_best_odds_across_bookmakers_wins(db):
    fx = _fixture(3003)
    db.add(fx)
    await db.flush()
    bet = _bet(fx, odds=2.0)
    db.add_all([
        bet,
        _snap(fx, odds=2.05, bookmaker="BookA"),
        _snap(fx, odds=2.15, bookmaker="BookB"),
        _snap(fx, odds=1.95, bookmaker="BookC"),
    ])
    await db.commit()

    closing, clv = await compute_clv_for_bet(bet, db)
    assert closing == pytest.approx(2.15)
    assert clv == pytest.approx(7.5)


async def test_snapshot_outside_4h_window_is_ignored(db):
    fx = _fixture(3004)
    db.add(fx)
    await db.flush()
    bet = _bet(fx, odds=2.0)
    db.add_all([
        bet,
        _snap(fx, odds=2.5, pulled_at=KICKOFF - timedelta(hours=5), bookmaker="BookEarly"),   # too early
        _snap(fx, odds=2.4, pulled_at=KICKOFF + timedelta(minutes=1), bookmaker="BookLate"),  # after kickoff
    ])
    await db.commit()

    closing, clv = await compute_clv_for_bet(bet, db)
    assert closing is None
    assert clv is None


async def test_no_clv_without_kickoff_time(db):
    fx = _fixture(3005, kickoff_at=None)
    db.add(fx)
    await db.flush()
    bet = _bet(fx, odds=2.0)
    db.add_all([bet, _snap(fx, odds=2.2, pulled_at=datetime(2026, 8, 26, 14, 0))])
    await db.commit()

    closing, clv = await compute_clv_for_bet(bet, db)
    assert closing is None and clv is None


@pytest.mark.parametrize("kw", [
    dict(fixture_id=None),
    dict(market_type=""),
    dict(odds=0.0),
])
async def test_guard_clauses_return_none(db, kw):
    fx = _fixture(3006)
    db.add(fx)
    await db.flush()
    bet = _bet(fx)
    for key, value in kw.items():
        setattr(bet, key, value)
    closing, clv = await compute_clv_for_bet(bet, db)
    assert closing is None and clv is None


# ---------------------------------------------------------------------------
# Market scoping + selection remapping
# ---------------------------------------------------------------------------

async def test_team_total_snapshot_never_prices_full_game_market(db):
    """'Over 1.5' full-game must not be priced from 'Total - Home' Over 1.5."""
    fx = _fixture(3101)
    db.add(fx)
    await db.flush()
    bet = _bet(fx, market_type="Over 1.5", selection_name="Over 1.5", odds=1.3)
    db.add_all([
        bet,
        # Team-total snapshot with juicy odds — must be excluded by scope.
        _snap(fx, market_type="Total - Home", selection="Over 1.5", odds=2.5),
        # Legit full-game snapshot.
        _snap(fx, market_type="Goals Over/Under", selection="Over 1.5", odds=1.35),
    ])
    await db.commit()

    closing, _ = await compute_clv_for_bet(bet, db)
    assert closing == pytest.approx(1.35)


async def test_btts_selection_remapping(db):
    fx = _fixture(3102)
    db.add(fx)
    await db.flush()
    bet = _bet(fx, market_type="BTTS Yes", selection_name="BTTS Yes", odds=1.8)
    db.add_all([
        bet,
        _snap(fx, market_type="Both Teams Score", selection="Yes", odds=1.9),
        _snap(fx, market_type="Both Teams Score", selection="No", odds=2.1),  # wrong side
    ])
    await db.commit()

    closing, clv = await compute_clv_for_bet(bet, db)
    assert closing == pytest.approx(1.9)
    assert clv == pytest.approx(round((1.9 / 1.8 - 1) * 100, 2))


async def test_win_to_nil_combined_market_remapping(db):
    fx = _fixture(3103)
    db.add(fx)
    await db.flush()
    bet = _bet(fx, market_type="Home Win to Nil", selection_name="Home Win to Nil", odds=3.0)
    db.add_all([
        bet,
        _snap(fx, market_type="Win To Nil", selection="Home", odds=3.4),
        _snap(fx, market_type="Win To Nil", selection="Away", odds=5.0),  # wrong side
    ])
    await db.commit()

    closing, _ = await compute_clv_for_bet(bet, db)
    assert closing == pytest.approx(3.4)


async def test_home_team_total_prefix_stripped(db):
    """'Home Over 0.5' bet maps to snapshot market 'Total - Home' selection 'Over 0.5'."""
    fx = _fixture(3104)
    db.add(fx)
    await db.flush()
    bet = _bet(fx, market_type="Home Over 0.5", selection_name="Home Over 0.5", odds=1.4)
    db.add_all([
        bet,
        _snap(fx, market_type="Total - Home", selection="Over 0.5", odds=1.45),
        # Away team total with same selection name — must be excluded by scope.
        _snap(fx, market_type="Total - Away", selection="Over 0.5", odds=1.9),
    ])
    await db.commit()

    closing, _ = await compute_clv_for_bet(bet, db)
    assert closing == pytest.approx(1.45)


# ---------------------------------------------------------------------------
# compute_clv_all — batch semantics
# ---------------------------------------------------------------------------

async def test_compute_clv_all_updates_and_skips(db):
    fx = _fixture(3201)
    db.add(fx)
    await db.flush()
    with_snap = _bet(fx, odds=2.0)
    no_snap = _bet(fx, market_type="Under 2.5", selection_name="Under 2.5", odds=1.9)
    already = _bet(fx, market_type="Over 3.5", selection_name="Over 3.5",
                   odds=2.5, closing_odds=2.6, clv_pct=4.0)
    db.add_all([with_snap, no_snap, already, _snap(fx, odds=2.2)])
    await db.commit()

    result = await compute_clv_all(db)

    assert result["updated"] == 1
    assert result["skipped_no_data"] == 1  # `already` filtered out by closing_odds IS NULL
    assert with_snap.closing_odds == pytest.approx(2.2)
    assert with_snap.clv_pct == pytest.approx(10.0)
    assert already.closing_odds == pytest.approx(2.6)  # untouched


async def test_compute_clv_all_force_recomputes(db):
    fx = _fixture(3202)
    db.add(fx)
    await db.flush()
    bet = _bet(fx, odds=2.0, closing_odds=9.9, clv_pct=99.0)  # stale values
    db.add_all([bet, _snap(fx, odds=2.1)])
    await db.commit()

    result = await compute_clv_all(db, force=True)

    assert result["updated"] == 1
    assert bet.closing_odds == pytest.approx(2.1)
    assert bet.clv_pct == pytest.approx(5.0)


async def test_compute_clv_all_scopes_to_user(db):
    fx = _fixture(3203)
    db.add(fx)
    await db.flush()
    mine = _bet(fx, user_id=1, odds=2.0)
    theirs = _bet(fx, user_id=2, market_type="Under 2.5", selection_name="Under 2.5", odds=1.9)
    db.add_all([
        mine, theirs,
        _snap(fx, odds=2.2),
        _snap(fx, selection="Under 2.5", odds=2.0),
    ])
    await db.commit()

    result = await compute_clv_all(db, user_id=1)

    assert result["updated"] == 1
    assert mine.closing_odds is not None
    assert theirs.closing_odds is None


# ---------------------------------------------------------------------------
# _best_odds_from_api_rows — closing-line capture parsing
# ---------------------------------------------------------------------------

def test_best_odds_from_api_rows_picks_max_in_scope():
    rows = [
        {"market_type": "Goals Over/Under", "selection_name": "Over 2.5", "odds": 1.9},
        {"market_type": "Goals Over/Under", "selection_name": "Over 2.5", "odds": 2.05},
        {"market_type": "Total - Home",     "selection_name": "Over 2.5", "odds": 3.5},  # out of scope
        {"market_type": "Goals Over/Under", "selection_name": "Under 2.5", "odds": 2.2},  # wrong selection
    ]
    assert _best_odds_from_api_rows(rows, "Over 2.5") == pytest.approx(2.05)


def test_best_odds_from_api_rows_remaps_btts_and_ignores_junk():
    rows = [
        {"market_type": "Both Teams Score", "selection_name": "Yes", "odds": 1.85},
        {"market_type": "Both Teams Score", "selection_name": "Yes", "odds": None},  # null odds
        {"market_type": "Both Teams Score", "selection_name": "Yes", "odds": 1.0},   # <= 1.0 junk
        {"market_type": "Both Teams Score", "selection_name": "No",  "odds": 2.0},
    ]
    assert _best_odds_from_api_rows(rows, "BTTS Yes") == pytest.approx(1.85)


def test_best_odds_from_api_rows_returns_none_when_no_match():
    rows = [{"market_type": "Goals Over/Under", "selection_name": "Over 2.5", "odds": 1.9}]
    assert _best_odds_from_api_rows(rows, "Under 3.5") is None
    assert _best_odds_from_api_rows([], "Over 2.5") is None

"""
Tests for app/services/settlement.py::settle_acca_bets — accumulator settlement.

Covers the standard acca rules:
  * All legs Won            → ticket Won,  P/L = stake × (combined_odds − 1)
  * Any leg Lost            → ticket Lost, P/L = −stake
  * Void leg(s)             → removed; adjusted odds = product of non-void legs
  * All legs Void           → ticket Void, P/L = 0
  * Any fixture not final   → ticket stays Pending (but leg results are
                              written back into notes as matches finish)
Plus edge handling:
  * Legs resolved by team-name lookup when fixture_id is absent
  * Missing fixture / unknown market leg → treated as void
  * Corrupt notes JSON / empty legs / missing event_date → skipped safely
  * settle_bets_for_date aggregates acca_settled into its totals
"""
import json
from datetime import date

import pytest

from app.models import Fixture, TrackedBet
from app.services.settlement import settle_acca_bets, settle_bets_for_date

EVENT_DATE = date(2026, 8, 26)


def _fixture(ext_id: int, **kw) -> Fixture:
    defaults = dict(
        external_fixture_id=ext_id, home_team=f"Home{ext_id}", away_team=f"Away{ext_id}",
        league="Test League", event_date=EVENT_DATE, status="FT",
        home_score=2, away_score=1,
    )
    defaults.update(kw)
    return Fixture(**defaults)


def _leg(fixture: Fixture | None = None, market: str = "Over 2.5", odd: float = 1.5, **kw) -> dict:
    leg = {"market": market, "odd": odd}
    if fixture is not None:
        leg["fixture_id"] = fixture.id
        leg["home_team"] = fixture.home_team
        leg["away_team"] = fixture.away_team
    leg.update(kw)
    return leg


def _acca(legs: list[dict], stake: float = 10.0, odds: float = 2.0, **kw) -> TrackedBet:
    defaults = dict(
        bookmaker="TestBook", match_name="Acca Ticket", market_type="Accumulator",
        selection_name="Acca", odds=odds, stake=stake, result_status="Pending",
        source_rule_key="acca_advisory", event_date=EVENT_DATE,
        notes=json.dumps({"legs": legs}),
    )
    defaults.update(kw)
    return TrackedBet(**defaults)


# ---------------------------------------------------------------------------
# Winning / losing tickets
# ---------------------------------------------------------------------------

async def test_all_legs_won_settles_ticket_won(db):
    fx1 = _fixture(1001, home_score=2, away_score=2)   # 4 goals — Over 2.5 wins
    fx2 = _fixture(1002, home_score=1, away_score=0)   # Home Win wins
    db.add_all([fx1, fx2])
    await db.flush()

    bet = _acca([_leg(fx1, "Over 2.5", 1.5), _leg(fx2, "Home Win", 2.0)], stake=10.0)
    db.add(bet)
    await db.commit()

    result = await settle_acca_bets(db)

    assert result["acca_settled"] == 1
    assert bet.result_status == "Won"
    # combined odds = 1.5 × 2.0 = 3.0 → P/L = 10 × (3.0 − 1) = 20
    assert bet.profit_loss == pytest.approx(20.0)
    assert bet.settled_at is not None

    legs = json.loads(bet.notes)["legs"]
    assert [leg["result"] for leg in legs] == ["won", "won"]
    assert legs[0]["score"] == "2-2"
    assert legs[1]["score"] == "1-0"


async def test_single_lost_leg_loses_whole_ticket(db):
    fx1 = _fixture(1101, home_score=3, away_score=1)   # Over 2.5 wins
    fx2 = _fixture(1102, home_score=0, away_score=0)   # BTTS Yes loses
    db.add_all([fx1, fx2])
    await db.flush()

    bet = _acca([_leg(fx1, "Over 2.5", 1.4), _leg(fx2, "BTTS Yes", 1.8)], stake=25.0)
    db.add(bet)
    await db.commit()

    await settle_acca_bets(db)

    assert bet.result_status == "Lost"
    assert bet.profit_loss == pytest.approx(-25.0)
    legs = json.loads(bet.notes)["legs"]
    assert [leg["result"] for leg in legs] == ["won", "lost"]


# ---------------------------------------------------------------------------
# Void handling
# ---------------------------------------------------------------------------

async def test_void_leg_removed_and_odds_adjusted(db):
    fx1 = _fixture(1201, home_score=2, away_score=1)                       # Over 2.5 wins @1.5
    fx2 = _fixture(1202, status="CANC", home_score=None, away_score=None)  # void leg @1.8
    fx3 = _fixture(1203, home_score=1, away_score=1)                       # Draw wins @3.0
    db.add_all([fx1, fx2, fx3])
    await db.flush()

    bet = _acca(
        [_leg(fx1, "Over 2.5", 1.5), _leg(fx2, "Over 2.5", 1.8), _leg(fx3, "Draw", 3.0)],
        stake=10.0,
    )
    db.add(bet)
    await db.commit()

    await settle_acca_bets(db)

    assert bet.result_status == "Won"
    # adjusted odds = 1.5 × 3.0 = 4.5 (void 1.8 leg excluded) → P/L = 10 × 3.5 = 35
    assert bet.profit_loss == pytest.approx(35.0)
    legs = json.loads(bet.notes)["legs"]
    assert [leg["result"] for leg in legs] == ["won", "void", "won"]
    assert legs[1]["score"] is None  # void legs never record a score


async def test_all_legs_void_voids_ticket(db):
    fx1 = _fixture(1301, status="CANC", home_score=None, away_score=None)
    fx2 = _fixture(1302, status="PST", home_score=None, away_score=None)
    db.add_all([fx1, fx2])
    await db.flush()

    bet = _acca([_leg(fx1, "Over 2.5", 1.5), _leg(fx2, "Home Win", 2.0)], stake=10.0)
    db.add(bet)
    await db.commit()

    await settle_acca_bets(db)

    assert bet.result_status == "Void"
    assert bet.profit_loss == 0.0


async def test_void_leg_does_not_rescue_lost_ticket(db):
    fx1 = _fixture(1401, status="CANC", home_score=None, away_score=None)  # void
    fx2 = _fixture(1402, home_score=0, away_score=2)                       # Home Win loses
    db.add_all([fx1, fx2])
    await db.flush()

    bet = _acca([_leg(fx1, "Over 2.5", 1.5), _leg(fx2, "Home Win", 2.0)], stake=10.0)
    db.add(bet)
    await db.commit()

    await settle_acca_bets(db)

    assert bet.result_status == "Lost"
    assert bet.profit_loss == pytest.approx(-10.0)


async def test_missing_fixture_and_unknown_market_legs_are_void(db):
    fx = _fixture(1501, home_score=2, away_score=1)
    db.add(fx)
    await db.flush()

    legs = [
        _leg(fx, "Over 2.5", 1.5),                                      # won
        {"market": "Over 2.5", "odd": 1.7,                              # no fixture anywhere → void
         "home_team": "Ghost FC", "away_team": "Phantom United"},
        _leg(fx, "Martian Handicap", 1.9),                              # unknown market → void
    ]
    bet = _acca(legs, stake=10.0)
    db.add(bet)
    await db.commit()

    await settle_acca_bets(db)

    assert bet.result_status == "Won"
    assert bet.profit_loss == pytest.approx(5.0)  # only the 1.5 leg counts
    parsed = json.loads(bet.notes)["legs"]
    assert [leg["result"] for leg in parsed] == ["won", "void", "void"]


# ---------------------------------------------------------------------------
# Pending behaviour + leg writeback
# ---------------------------------------------------------------------------

async def test_pending_leg_keeps_ticket_pending_but_writes_leg_results(db):
    fx_done = _fixture(1601, home_score=3, away_score=0)                     # final
    fx_live = _fixture(1602, status="1H", home_score=0, away_score=0)        # in play
    db.add_all([fx_done, fx_live])
    await db.flush()

    bet = _acca([_leg(fx_done, "Over 2.5", 1.5), _leg(fx_live, "Over 2.5", 1.6)], stake=10.0)
    db.add(bet)
    await db.commit()

    result = await settle_acca_bets(db)

    assert result["acca_settled"] == 0
    assert bet.result_status == "Pending"
    assert bet.settled_at is None
    # Leg results must still be visible mid-ticket for the Tracker page.
    legs = json.loads(bet.notes)["legs"]
    assert legs[0]["result"] == "won"
    assert legs[0]["score"] == "3-0"
    assert legs[1]["result"] == "pending"


async def test_leg_resolved_by_team_name_lookup_without_fixture_id(db):
    fx = _fixture(1701, home_team="Arsenal", away_team="Chelsea", home_score=2, away_score=1)
    db.add(fx)
    await db.flush()

    # No fixture_id on the leg — must fall back to (event_date, home, away) lookup.
    bet = _acca([{"home_team": "Arsenal", "away_team": "Chelsea", "market": "Home Win", "odd": 2.2}], stake=10.0)
    db.add(bet)
    await db.commit()

    await settle_acca_bets(db)

    assert bet.result_status == "Won"
    assert bet.profit_loss == pytest.approx(12.0)


# ---------------------------------------------------------------------------
# Robustness: malformed tickets are skipped, never crash the pass
# ---------------------------------------------------------------------------

async def test_corrupt_notes_and_empty_legs_are_skipped(db):
    fx = _fixture(1801, home_score=2, away_score=1)
    db.add(fx)
    await db.flush()

    corrupt = _acca([], notes="not-json{{{")
    no_legs = _acca([])
    no_date = _acca([_leg(fx, "Over 2.5", 1.5)], event_date=None)
    db.add_all([corrupt, no_legs, no_date])
    await db.commit()

    result = await settle_acca_bets(db)

    assert result["acca_settled"] == 0
    for bet in (corrupt, no_legs, no_date):
        assert bet.result_status == "Pending"


async def test_non_acca_pending_bets_are_ignored(db):
    fx = _fixture(1901, home_score=2, away_score=1)
    db.add(fx)
    await db.flush()

    single = TrackedBet(
        bookmaker="TestBook", match_name="A vs B", market_type="Over 2.5",
        selection_name="Over 2.5", odds=1.9, stake=10.0, result_status="Pending",
        fixture_id=fx.id, event_date=EVENT_DATE,
        notes=json.dumps({"legs": [_leg(fx, "Over 2.5", 1.5)]}),  # legs present but wrong source_rule_key
    )
    db.add(single)
    await db.commit()

    result = await settle_acca_bets(db)
    assert result["acca_settled"] == 0


async def test_system_acca_source_rule_key_is_settled(db):
    fx = _fixture(2001, home_score=2, away_score=2)
    db.add(fx)
    await db.flush()

    bet = _acca([_leg(fx, "Over 2.5", 1.5)], source_rule_key="system_acca")
    db.add(bet)
    await db.commit()

    result = await settle_acca_bets(db)
    assert result["acca_settled"] == 1
    assert bet.result_status == "Won"


# ---------------------------------------------------------------------------
# Integration: settle_bets_for_date includes acca settlements in its totals
# ---------------------------------------------------------------------------

async def test_settle_bets_for_date_includes_acca_totals(db):
    fx = _fixture(2101, home_score=2, away_score=2)
    db.add(fx)
    await db.flush()

    single = TrackedBet(
        bookmaker="TestBook", match_name="A vs B", market_type="Over 2.5",
        selection_name="Over 2.5", odds=1.9, stake=10.0, result_status="Pending",
        fixture_id=fx.id, event_date=EVENT_DATE,
    )
    acca = _acca([_leg(fx, "Over 2.5", 1.5), _leg(fx, "BTTS Yes", 1.7)], stake=10.0)
    db.add_all([single, acca])
    await db.commit()

    result = await settle_bets_for_date(db, run_date=EVENT_DATE)

    assert result["acca_settled"] == 1
    assert result["settled"] == 2  # 1 single + 1 acca
    assert single.result_status == "Won"
    assert acca.result_status == "Won"
    assert acca.profit_loss == pytest.approx(10.0 * (1.5 * 1.7 - 1.0))

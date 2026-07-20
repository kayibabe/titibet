"""
match_info.py — Per-fixture contextual intelligence.

Mines the local fixtures table to produce:
  - team_stats: form, win/draw/loss %, PPG, goals averages (last 10 games each team)
  - performance_highlights: notable statistical trends (e.g. Under 4.5 in 33/35)
  - h2h: last 7 head-to-head meetings
  - probabilities: market derived_probs from our Bayesian engine (already in signals)

All computed entirely from data we already hold — zero additional API calls.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fixture import Fixture
from app.models.signal import Signal

FINAL_STATUSES = {"FT", "AET", "PEN"}
FORM_WINDOW   = 10   # games for stats
H2H_WINDOW    = 7    # last N H2H meetings
HIGHLIGHT_N   = 35   # window for performance highlights


# ── Helpers ───────────────────────────────────────────────────────────────────

def _goals_total(fx: Fixture, team: str) -> int:
    if fx.home_team == team:
        return fx.home_score or 0
    return fx.away_score or 0


def _goals_conceded(fx: Fixture, team: str) -> int:
    if fx.home_team == team:
        return fx.away_score or 0
    return fx.home_score or 0


def _result(fx: Fixture, team: str) -> str:
    """Return W / D / L from team's perspective."""
    gs = _goals_total(fx, team)
    gc = _goals_conceded(fx, team)
    if gs > gc:   return "W"
    if gs == gc:  return "D"
    return "L"


def _team_stats(fixtures: list[Fixture], team: str) -> dict:
    """Compute win/draw/loss, goals, PPG, form string from a list of completed fixtures."""
    wins = draws = losses = gf = ga = 0
    form = []
    for fx in fixtures:
        r = _result(fx, team)
        form.append(r)
        gf += _goals_total(fx, team)
        ga += _goals_conceded(fx, team)
        if r == "W":   wins   += 1
        elif r == "D": draws  += 1
        else:          losses += 1

    n = len(fixtures)
    points = wins * 3 + draws
    return {
        "played":    n,
        "wins":      wins,
        "draws":     draws,
        "losses":    losses,
        "win_pct":   round(wins   / n * 100, 1) if n else 0,
        "draw_pct":  round(draws  / n * 100, 1) if n else 0,
        "loss_pct":  round(losses / n * 100, 1) if n else 0,
        "goals_for": gf,
        "goals_against": ga,
        "avg_goals_for":     round(gf / n, 2) if n else 0,
        "avg_goals_against": round(ga / n, 2) if n else 0,
        "goal_difference": gf - ga,
        "ppg": round(points / n, 2) if n else 0,
        "form": form[:5],   # last 5 for display
    }


def _highlights(fixtures: list[Fixture], team: str) -> list[str]:
    """Return notable trend strings for a team over the highlight window."""
    n = len(fixtures)
    if n == 0:
        return []

    out = []

    def _pct(count): return f"{count}/{n}"

    # Goals thresholds — evaluate Under and Over separately (no ambiguous if/else in genexpr)
    goal_totals = [(fx.home_score or 0) + (fx.away_score or 0) for fx in fixtures]

    for threshold, label in [(4.5, "Under 4.5 goals"), (3.5, "Under 3.5 goals"),
                              (2.5, "Over 2.5 goals"),  (1.5, "Over 1.5 goals")]:
        is_under = label.startswith("Under")
        if is_under:
            count = sum(1 for g in goal_totals if g < threshold)
        else:
            count = sum(1 for g in goal_totals if g > threshold)
        pct = count / n
        if pct >= 0.70:
            out.append(f"{team} have **{label}** in their last {_pct(count)} matches")

    # BTTS
    btts = sum(1 for fx in fixtures if (fx.home_score or 0) >= 1 and (fx.away_score or 0) >= 1)
    if btts / n >= 0.65:
        out.append(f"{team} have **Both Teams to Score** in their last {_pct(btts)} matches")
    elif btts / n <= 0.30:
        out.append(f"{team} have **BTTS No** (clean sheet or blanked) in their last {_pct(n - btts)} matches")

    # Goals scored by this team
    scored = sum(1 for fx in fixtures if _goals_total(fx, team) >= 1)
    if scored / n >= 0.80:
        out.append(f"{team} **scored** in their last {_pct(scored)} matches")

    failed_to_score = sum(1 for fx in fixtures if _goals_total(fx, team) == 0)
    if failed_to_score / n >= 0.35:
        out.append(f"{team} **failed to score** in {_pct(failed_to_score)} of their last {n} matches")

    # Clean sheets
    cs = sum(1 for fx in fixtures if _goals_conceded(fx, team) == 0)
    if cs / n >= 0.40:
        out.append(f"{team} kept a **clean sheet** in {_pct(cs)} of their last {n} matches")

    # Win streak tendency
    if len(fixtures) >= 5:
        last5 = [_result(fx, team) for fx in fixtures[:5]]
        if last5.count("W") >= 4:
            out.append(f"{team} won **{last5.count('W')} of their last 5** matches")
        if last5.count("L") >= 4:
            out.append(f"{team} lost **{last5.count('L')} of their last 5** matches")

    return out[:6]   # cap at 6 highlights per team


def _h2h_entry(fx: Fixture, home_team: str) -> dict:
    """Serialise a fixture as an H2H row."""
    return {
        "date":       fx.event_date.isoformat() if fx.event_date else None,
        "home_team":  fx.home_team,
        "away_team":  fx.away_team,
        "home_score": fx.home_score,
        "away_score": fx.away_score,
        "is_home":    fx.home_team == home_team,
    }


# ── Main service function ─────────────────────────────────────────────────────

async def get_match_info(
    db: AsyncSession,
    fixture_id: int,
) -> dict:
    """
    Return full contextual match intelligence for one fixture.
    """
    # Load the fixture itself
    fixture = await db.get(Fixture, fixture_id)
    if not fixture:
        return {}

    home = fixture.home_team
    away = fixture.away_team
    before = fixture.event_date or date.today()
    cutoff = before - timedelta(days=365)   # one season lookback

    # ── Fetch last N completed games for home team ────────────────────────
    home_q = (
        select(Fixture)
        .where(
            Fixture.event_date >= cutoff,
            Fixture.event_date <  before,
            Fixture.status.in_(FINAL_STATUSES),
            Fixture.home_score.is_not(None),
            or_(Fixture.home_team == home, Fixture.away_team == home),
        )
        .order_by(Fixture.event_date.desc())
        .limit(HIGHLIGHT_N)
    )
    home_fixtures = list((await db.execute(home_q)).scalars().all())

    # ── Fetch last N completed games for away team ────────────────────────
    away_q = (
        select(Fixture)
        .where(
            Fixture.event_date >= cutoff,
            Fixture.event_date <  before,
            Fixture.status.in_(FINAL_STATUSES),
            Fixture.home_score.is_not(None),
            or_(Fixture.home_team == away, Fixture.away_team == away),
        )
        .order_by(Fixture.event_date.desc())
        .limit(HIGHLIGHT_N)
    )
    away_fixtures = list((await db.execute(away_q)).scalars().all())

    # ── H2H — last 7 meetings between these two teams ────────────────────
    h2h_q = (
        select(Fixture)
        .where(
            Fixture.event_date < before,
            Fixture.status.in_(FINAL_STATUSES),
            Fixture.home_score.is_not(None),
            or_(
                and_(Fixture.home_team == home, Fixture.away_team == away),
                and_(Fixture.home_team == away, Fixture.away_team == home),
            ),
        )
        .order_by(Fixture.event_date.desc())
        .limit(H2H_WINDOW)
    )
    h2h_fixtures = list((await db.execute(h2h_q)).scalars().all())

    # ── Signals — probabilities from our engine ───────────────────────────
    sig_q = select(Signal).where(Signal.fixture_id == fixture_id)
    signals = list((await db.execute(sig_q)).scalars().all())

    probabilities = []
    for sig in sorted(signals, key=lambda s: -(s.dual_quality_score or 0)):
        if sig.bayesian_prob is not None:
            probabilities.append({
                "market":      sig.market,
                "prob":        round(sig.bayesian_prob * 100, 1),
                "confidence":  sig.dual_confidence,
                "agreement":   sig.dual_agreement,
                "best_odd":    sig.bayesian_best_odd,
                "bookmaker":   sig.bayesian_bookmaker,
                "quality":     sig.dual_quality_score,
                "is_value":    sig.bayesian_is_value,
            })

    # ── Compute stats using form window (last 10) ─────────────────────────
    home_stats = _team_stats(home_fixtures[:FORM_WINDOW], home)
    away_stats = _team_stats(away_fixtures[:FORM_WINDOW], away)

    # ── Performance highlights (full window) ──────────────────────────────
    home_highlights = _highlights(home_fixtures, home)
    away_highlights = _highlights(away_fixtures, away)

    return {
        "fixture": {
            "id":         fixture.id,
            "home_team":  home,
            "away_team":  away,
            "league":     fixture.league,
            "event_date": fixture.event_date.isoformat() if fixture.event_date else None,
            "kickoff_at": fixture.kickoff_at.isoformat() if fixture.kickoff_at else None,
            "status":     fixture.status,
            "home_score": fixture.home_score,
            "away_score": fixture.away_score,
            "league_tier": fixture.league_tier,
        },
        "home_stats":        home_stats,
        "away_stats":        away_stats,
        "home_highlights":   home_highlights,
        "away_highlights":   away_highlights,
        "h2h":               [_h2h_entry(fx, home) for fx in h2h_fixtures],
        "probabilities":     probabilities,
        "data_games_home":   len(home_fixtures),
        "data_games_away":   len(away_fixtures),
    }

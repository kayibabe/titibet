"""
form_service.py — Rolling team-form lambda computation.

Problem being solved
--------------------
The Poisson engine derives lambda entirely from CS odds ratios.  CS odds in
Tier-2/3 leagues lag on form shifts (fewer sharp bettors, thinner markets).
When a defence suddenly starts shipping goals — or a team's motivation changes
late in the season — the odds take longer to catch up than the results do.

Solution
--------
Query the last N *completed* fixtures for each team, compute an exponentially-
weighted average of goals scored per match, and return form-derived lambdas.
These are then blended with the CS-odds-derived lambdas in the Poisson engine
at a configurable weight (default 50/50).

Design decisions
----------------
- Exponential decay (most recent game = highest weight) rather than a flat
  rolling mean.  This is more reactive to genuine form shifts vs noise.
- We track goals scored (not conceded) per team.  Lambda_H = home team attack
  rate; Lambda_A = away team attack rate.  This maps cleanly to the Poisson
  model's independent home/away parameterisation.
- If either team has fewer than `form_min_games` completed matches we return
  an empty dict — the Poisson engine falls back to CS-only lambdas.  Returning
  partial data would skew the blend for one side only.
- We query across home AND away appearances so we capture a team's true recent
  output regardless of venue.  Venue splits are theoretically better but require
  more data; with only 6 games the noise outweighs the signal.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import POISSON_RULES
from app.models.fixture import Fixture

R = POISSON_RULES


def _exp_weighted_avg(values: list[float], decay: float = 0.85) -> float:
    """
    Exponentially weighted average, most-recent first.

    values[0] is the MOST recent observation.
    decay=0.85 means each older game is worth 85% of the next newer one.
    """
    if not values:
        return 0.0
    total_weight = 0.0
    total = 0.0
    w = 1.0
    for v in values:
        total += v * w
        total_weight += w
        w *= decay
    return total / total_weight if total_weight > 0 else 0.0


async def get_team_form_lambdas(
    db: AsyncSession,
    home_team: str,
    away_team: str,
    before_date: date,
    n: Optional[int] = None,
) -> dict:
    """
    Return form-derived lambdas for the upcoming fixture.

    Returns
    -------
    dict with keys lambda_h, lambda_a, lambda_total, games_h, games_a
    OR an empty dict {} if there is insufficient data for either team.

    Parameters
    ----------
    home_team : str   — home team name (must match Fixture.home_team / away_team)
    away_team : str   — away team name
    before_date : date — fixture date; we only look at matches strictly before this
    n : int           — window size; defaults to POISSON_RULES["rolling_form_games"]
    """
    n = n or int(R["rolling_form_games"])
    min_games = int(R["form_min_games"])

    home_goals = await _fetch_team_goals(db, home_team, before_date, n)
    away_goals = await _fetch_team_goals(db, away_team, before_date, n)

    if len(home_goals) < min_games or len(away_goals) < min_games:
        # Not enough data — caller will fall back to CS-only lambdas
        return {}

    lam_h = _exp_weighted_avg(home_goals)
    lam_a = _exp_weighted_avg(away_goals)

    # Guard against degenerate zero-lambdas (e.g. teams on scoring drought).
    # A lambda of 0 breaks Poisson CDF; floor at 0.10 (1 goal per 10 games).
    lam_h = max(lam_h, 0.10)
    lam_a = max(lam_a, 0.10)

    return {
        "lambda_h": lam_h,
        "lambda_a": lam_a,
        "lambda_total": lam_h + lam_a,
        "games_h": len(home_goals),
        "games_a": len(away_goals),
    }


async def _fetch_team_goals(
    db: AsyncSession,
    team: str,
    before_date: date,
    n: int,
) -> list[float]:
    """
    Fetch goals scored by *team* in its last n completed fixtures before before_date,
    constrained to within form_max_lookback_days (default 90) of before_date.

    The lookback window prevents previous-season results from polluting current-season
    form estimates — particularly important at the start of a new campaign where a
    team's squad, manager, or playing style may have changed significantly over summer.

    Returns a list of goal counts, most-recent first.
    """
    max_days = int(R.get("form_max_lookback_days", 90))
    cutoff_date = before_date - timedelta(days=max_days)

    stmt = (
        select(Fixture)
        .where(
            and_(
                Fixture.event_date < before_date,
                Fixture.event_date >= cutoff_date,
                Fixture.home_score.is_not(None),
                Fixture.away_score.is_not(None),
                or_(
                    Fixture.home_team == team,
                    Fixture.away_team == team,
                ),
            )
        )
        .order_by(Fixture.event_date.desc())
        .limit(n)
    )

    result = await db.execute(stmt)
    fixtures: list[Fixture] = list(result.scalars().all())

    goals: list[float] = []
    for fx in fixtures:
        if fx.home_team == team:
            goals.append(float(fx.home_score))  # type: ignore[arg-type]
        else:
            goals.append(float(fx.away_score))  # type: ignore[arg-type]

    return goals  # already ordered most-recent first by the query

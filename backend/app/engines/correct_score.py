"""
correct_score.py — Exact-score (Correct Score) probability engine.

Builds a full scoreline probability matrix from the same blended lambdas the
rest of the system trusts, applies the Dixon-Coles low-score correction
(independent Poisson systematically underprices 0-0 / 1-1 draws), and picks
the single highest-EV scoreline per fixture against the bookmaker CS board.

CS is a value market, not a confidence market: even the most likely scoreline
lands ~10-15% of the time, so picks are gated on exec-price EV rather than the
dual-engine confidence cascade.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from app.core.config import (
    CS_DC_RHO,
    CS_MARKET_PREFIX,
    CS_MAX_GOALS,
    CS_MIN_BOOKMAKERS,
    CS_MIN_EV,
    CS_MIN_MODEL_PROB,
    CS_ODDS_CEILING,
    exec_odd_from,
)
from app.engines.bayesian import _parse_scoreline


@dataclass
class CSOdds:
    """Best available price for one scoreline across the bookmaker CS board."""
    best_odds: float
    bookmaker: str
    n_books: int


@dataclass
class CSPick:
    """The single best-EV correct-score pick for a fixture."""
    home_goals: int
    away_goals: int
    model_prob: float      # DC-adjusted Poisson probability of this exact score
    best_odds: float       # displayed proxy price (best across books)
    exec_odds: float       # haircut execution price used for EV / Kelly
    ev: float              # model_prob * exec_odds - 1
    bookmaker: str
    n_books: int
    lambda_h: float
    lambda_a: float

    @property
    def market(self) -> str:
        return f"{CS_MARKET_PREFIX}{self.home_goals}-{self.away_goals}"


def _dc_tau(h: int, a: int, lam_h: float, lam_a: float, rho: float) -> float:
    """Dixon-Coles tau adjustment for the four low-score cells (clamped >= 0)."""
    if h == 0 and a == 0:
        return max(0.0, 1.0 - lam_h * lam_a * rho)
    if h == 1 and a == 0:
        return max(0.0, 1.0 + lam_a * rho)
    if h == 0 and a == 1:
        return max(0.0, 1.0 + lam_h * rho)
    if h == 1 and a == 1:
        return max(0.0, 1.0 - rho)
    return 1.0


def score_matrix(
    lam_h: float,
    lam_a: float,
    rho: float = CS_DC_RHO,
    max_goals: int = CS_MAX_GOALS,
) -> Optional[list[list[float]]]:
    """
    P(H=i, A=j) grid for i,j in 0..max_goals: independent Poisson with the
    Dixon-Coles tau correction, renormalized so the truncated grid sums to 1.
    """
    if not lam_h or not lam_a or lam_h <= 0 or lam_a <= 0:
        return None

    # Poisson PMF vectors via the same iterative scheme as poisson.poisson_cdf.
    def _pmf(lam: float) -> list[float]:
        pmf = [math.exp(-lam)]
        for k in range(1, max_goals + 1):
            pmf.append(pmf[-1] * lam / k)
        return pmf

    pmf_h = _pmf(lam_h)
    pmf_a = _pmf(lam_a)

    grid = [
        [pmf_h[i] * pmf_a[j] * _dc_tau(i, j, lam_h, lam_a, rho) for j in range(max_goals + 1)]
        for i in range(max_goals + 1)
    ]
    total = sum(sum(row) for row in grid)
    if total <= 0:
        return None
    return [[p / total for p in row] for row in grid]


def collect_cs_odds(cs_by_bookie: dict[str, list[dict]]) -> dict[tuple[int, int], CSOdds]:
    """
    Aggregate the raw CS board ({bookmaker: [{value: "1:0", odd: 6.5}, ...]})
    into best price + book count per scoreline.
    """
    best: dict[tuple[int, int], CSOdds] = {}
    seen_books: dict[tuple[int, int], set[str]] = {}
    for bookmaker, values in cs_by_bookie.items():
        for item in values:
            try:
                odd = float(item.get("odd", 0))
            except (ValueError, TypeError):
                continue
            if odd <= 1.0:
                continue
            parsed = _parse_scoreline(item.get("value", ""))
            if parsed is None:
                continue
            seen_books.setdefault(parsed, set()).add(bookmaker)
            current = best.get(parsed)
            if current is None or odd > current.best_odds:
                best[parsed] = CSOdds(best_odds=odd, bookmaker=bookmaker, n_books=0)
    for key, cs in best.items():
        cs.n_books = len(seen_books[key])
    return best


def best_cs_pick(
    matrix: list[list[float]],
    cs_odds: dict[tuple[int, int], CSOdds],
    lam_h: float,
    lam_a: float,
    *,
    min_ev: float = CS_MIN_EV,
    odds_ceiling: float = CS_ODDS_CEILING,
    min_bookmakers: int = CS_MIN_BOOKMAKERS,
    min_model_prob: float = CS_MIN_MODEL_PROB,
) -> Optional[CSPick]:
    """
    Evaluate every priced grid cell and return the single argmax-EV scoreline,
    or None if nothing clears the gates. EV is computed at the execution price
    (global haircut), consistent with how the rest of the system books P&L.
    """
    max_goals = len(matrix) - 1
    best: Optional[CSPick] = None
    for (h, a), quote in cs_odds.items():
        if h > max_goals or a > max_goals:
            continue
        if quote.best_odds > odds_ceiling:
            continue
        if quote.n_books < min_bookmakers:
            continue
        prob = matrix[h][a]
        if prob < min_model_prob:
            continue
        market = f"{CS_MARKET_PREFIX}{h}-{a}"
        exec_odds = exec_odd_from(quote.best_odds, market)
        if exec_odds <= 1.0:
            continue
        ev = prob * exec_odds - 1.0
        if ev < min_ev:
            continue
        if best is None or ev > best.ev:
            best = CSPick(
                home_goals=h, away_goals=a,
                model_prob=prob,
                best_odds=quote.best_odds, exec_odds=exec_odds,
                ev=ev,
                bookmaker=quote.bookmaker, n_books=quote.n_books,
                lambda_h=lam_h, lambda_a=lam_a,
            )
    return best


def parse_cs_market(market: str) -> Optional[tuple[int, int]]:
    """'Correct Score 2-1' -> (2, 1); None if not a CS market string."""
    if not market or not market.startswith(CS_MARKET_PREFIX):
        return None
    return _parse_scoreline(market[len(CS_MARKET_PREFIX):])

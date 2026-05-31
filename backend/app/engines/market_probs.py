"""
Market probability derivation from a ZINB/Poisson score matrix.
Ported from qsbip/models/goal_model/market_probs.py.

Covers: 1X2, Asian Handicap (full and quarter lines), Over/Under, BTTS.
Input is a (max_goals+1, max_goals+1) joint score probability matrix.

Usage:
    matrix = zinb_model.score_matrix(home_id, away_id)
    if matrix is not None:
        probs = all_market_probs(matrix, ah_handicap=-0.5, ou_line=2.5)
        # probs = {"1X2": {...}, "AH": {...}, "OU": {...}, "BTTS": {...}}
"""
from __future__ import annotations

from typing import Optional

try:
    import numpy as np
    _NP_OK = True
except ImportError:
    _NP_OK = False


def result_probs(matrix) -> dict[str, float]:
    """1X2 probabilities from a score probability matrix."""
    if not _NP_OK:
        return {}
    n = matrix.shape[0]
    home_win = float(sum(matrix[i, j] for i in range(n) for j in range(n) if i > j))
    draw = float(sum(matrix[i, i] for i in range(n)))
    away_win = float(sum(matrix[i, j] for i in range(n) for j in range(n) if j > i))
    total = home_win + draw + away_win
    if total <= 0:
        return {"home": 1/3, "draw": 1/3, "away": 1/3}
    return {
        "home": home_win / total,
        "draw": draw / total,
        "away": away_win / total,
    }


def over_under_prob(matrix, line: float = 2.5) -> dict[str, float]:
    """Over/Under total-goals probabilities for a given line."""
    if not _NP_OK:
        return {}
    n = matrix.shape[0]
    over = float(sum(matrix[i, j] for i in range(n) for j in range(n) if (i + j) > line))
    return {"over": over, "under": max(0.0, 1.0 - over)}


def btts_prob(matrix) -> dict[str, float]:
    """Both-Teams-To-Score probabilities."""
    if not _NP_OK:
        return {}
    n = matrix.shape[0]
    yes = float(sum(matrix[i, j] for i in range(1, n) for j in range(1, n)))
    return {"yes": yes, "no": max(0.0, 1.0 - yes)}


def asian_handicap_prob(matrix, handicap: float) -> dict[str, float]:
    """
    Asian Handicap probabilities (home perspective).
    Quarter-lines (±0.25, ±0.75) are split between two adjacent half-lines.
    """
    remainder = abs(handicap) % 1.0
    is_quarter = abs(remainder - 0.25) < 1e-9 or abs(remainder - 0.75) < 1e-9

    if is_quarter:
        lo = _ah_prob_simple(matrix, handicap - 0.25)
        hi = _ah_prob_simple(matrix, handicap + 0.25)
        return {
            "home": (lo["home"] + hi["home"]) / 2.0,
            "away": (lo["away"] + hi["away"]) / 2.0,
        }
    return _ah_prob_simple(matrix, handicap)


def _ah_prob_simple(matrix, handicap: float) -> dict[str, float]:
    if not _NP_OK:
        return {"home": 0.5, "away": 0.5}
    n = matrix.shape[0]
    home_win = away_win = push = 0.0
    for i in range(n):
        for j in range(n):
            margin = (i + handicap) - j
            if margin > 0:
                home_win += matrix[i, j]
            elif margin < 0:
                away_win += matrix[i, j]
            else:
                push += matrix[i, j]
    total = home_win + away_win
    if total <= 0:
        return {"home": 0.5, "away": 0.5}
    return {"home": home_win / total, "away": away_win / total}


def correct_score_prob(matrix, home: int, away: int) -> float:
    """Probability of an exact correct score."""
    if not _NP_OK or home >= matrix.shape[0] or away >= matrix.shape[1]:
        return 0.0
    return float(matrix[home, away])


def all_market_probs(
    matrix,
    ah_handicap: float = -0.5,
    ou_lines: Optional[list[float]] = None,
) -> dict:
    """
    Return a unified dict of probabilities for all standard markets.

    Keys: "1X2", "BTTS", "AH", "OU_2.5", "OU_1.5", "OU_3.5", etc.
    """
    if not _NP_OK or matrix is None:
        return {}

    ou_lines = ou_lines or [1.5, 2.5, 3.5, 4.5]
    out: dict = {
        "1X2": result_probs(matrix),
        "BTTS": btts_prob(matrix),
        "AH": asian_handicap_prob(matrix, ah_handicap),
    }
    for line in ou_lines:
        out[f"OU_{line}"] = over_under_prob(matrix, line)
    return out

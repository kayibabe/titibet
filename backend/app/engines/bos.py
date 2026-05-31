"""
BOS 2.0 — Match Stability Index.
Ported from betapp/backend/models/bos.py.

Produces a Stability Index (SI) that quantifies how "stable" (low-scoring,
defensively oriented) a fixture is expected to be. High SI → good candidate
for Under and BTTS-No markets. Used in titibet as a quality gate and signal
enrichment layer.

SI = D + B + H + M   (threshold ≥ 75 by default)

D — Defensive Score:   based on 0-0 CS bookmaker odds
B — Balance Score:     favourite/underdog odds ratio
H — Historical Score:  average total-goals rate of both teams
M — Stability Score:   first-half low-score historical rates
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ── Defaults (overridden by config.BOS_* constants at call sites) ────────────
_BOS_SI_THRESHOLD: float = 75.0
_BOS_O00_MAX: float = 7.0
_BOS_CMA_MAX: float = 4.0


@dataclass
class BOSResult:
    si: float
    d_score: float
    b_score: float
    h_score: float
    m_score: float
    cma: float
    mbr: float
    passed: bool
    reject_reason: Optional[str] = None
    details: dict = field(default_factory=dict)


def compute_si(
    o_00: float,
    f_odds: float,
    u_odds: float,
    atg_home: float,
    atg_away: float,
    ht_00_home: float = 0.25,
    ht_00_away: float = 0.25,
    ht_10_home: float = 0.30,
    ht_10_away: float = 0.30,
    cma_max: float = _BOS_CMA_MAX,
    threshold: float = _BOS_SI_THRESHOLD,
    o00_max: float = _BOS_O00_MAX,
) -> BOSResult:
    """
    Compute BOS 2.0 Stability Index for a single fixture.

    Parameters
    ----------
    o_00        : Bookmaker odds for 0-0 correct score
    f_odds      : Favourite's match odds (lower value)
    u_odds      : Underdog's match odds (higher value)
    atg_home    : Home team average total goals (last 5 matches)
    atg_away    : Away team average total goals (last 5 matches)
    ht_00_home  : Home team fraction of recent matches at 0-0 HT [0-1]
    ht_00_away  : Away team fraction of recent matches at 0-0 HT [0-1]
    ht_10_home  : Home team fraction of recent matches at 1-0/0-1 HT [0-1]
    ht_10_away  : Away team fraction of recent matches at 1-0/0-1 HT [0-1]
    cma_max     : Ceiling for H-score normalisation
    threshold   : SI threshold to mark passed=True
    o00_max     : Hard reject if 0-0 odds exceed this value

    Stage 1 — Defensive Score (D)
        D = max(0, 100 * (1 - O_00 / 7))
        Hard reject if O_00 > o00_max.

    Stage 2 — Market Balance Score (B)
        MBR = min(f, u) / max(f, u)   always in (0, 1]
        B   = 100 * MBR

    Stage 3 — Historical Behaviour Score (H)
        CMA = (atg_home + atg_away) / 2
        H   = max(0, 100 * (1 - CMA / cma_max))

    Stage 4 — First-Half Stability Score (M)
        FHS = ((ht_00_home + ht_00_away)/2) + ((ht_10_home + ht_10_away)/2)
        M   = 100 * FHS / 2    [normalised to [0, 100]]
    """
    # ── Stage 1: Defensive Score ──────────────────────────────────────────
    if o_00 > o00_max:
        return BOSResult(
            si=0.0, d_score=0.0, b_score=0.0, h_score=0.0, m_score=0.0,
            cma=0.0, mbr=0.0, passed=False,
            reject_reason=f"O_00={o_00:.2f} > {o00_max} — hard reject",
        )
    D = max(0.0, 100.0 * (1.0 - o_00 / 7.0))

    # ── Stage 2: Market Balance Score ────────────────────────────────────
    if f_odds <= 0 or u_odds <= 0:
        return BOSResult(
            si=0.0, d_score=D, b_score=0.0, h_score=0.0, m_score=0.0,
            cma=0.0, mbr=0.0, passed=False, reject_reason="Invalid match odds",
        )
    mbr = min(f_odds, u_odds) / max(f_odds, u_odds)
    B = 100.0 * mbr

    # ── Stage 3: Historical Behaviour Score ──────────────────────────────
    cma = (atg_home + atg_away) / 2.0
    H = max(0.0, 100.0 * (1.0 - cma / cma_max))

    # ── Stage 4: First-Half Stability Score ──────────────────────────────
    ht_00_avg = (ht_00_home + ht_00_away) / 2.0
    ht_10_avg = (ht_10_home + ht_10_away) / 2.0
    fhs = ht_00_avg + ht_10_avg               # [0, 2]
    M = 100.0 * fhs / 2.0                     # normalised to [0, 100]

    si = D + B + H + M
    passed = si >= threshold

    return BOSResult(
        si=round(si, 2),
        d_score=round(D, 2),
        b_score=round(B, 2),
        h_score=round(H, 2),
        m_score=round(M, 2),
        cma=round(cma, 3),
        mbr=round(mbr, 3),
        passed=passed,
        reject_reason=None if passed else f"SI={si:.1f} < {threshold}",
        details={
            "o_00": o_00, "f_odds": f_odds, "u_odds": u_odds,
            "atg_home": atg_home, "atg_away": atg_away,
            "ht_00_avg": round(ht_00_avg, 3),
            "ht_10_avg": round(ht_10_avg, 3),
        },
    )

"""
poisson.py — Poisson probability engine.

Ported from TiTiBet/src/utils/rules.js and probability.js.

The core innovation: lambda is derived from CS odds *ratios*, so the bookmaker's
overround factor cancels out:
    λH = odds_00 / odds_10   (home scoring rate)
    λA = odds_00 / odds_01   (away scoring rate)

This gives independent home/away Poisson parameters for accurate BTTS calculation
on asymmetric matches, rather than the naive equal-split approximation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from app.core.config import POISSON_RULES, MARKET_MIN_ODDS

R = POISSON_RULES  # shorthand

import logging as _logging
_log = _logging.getLogger(__name__)


def _form_lambda_weight() -> float:
    """Read form_lambda_weight from config with default and [0, 1] bounds clamp."""
    fw = float(R.get("form_lambda_weight", 0.35))
    if not (0.0 <= fw <= 1.0):
        _log.warning("form_lambda_weight %s is outside [0, 1]; clamping to 0.35", fw)
        fw = 0.35
    return fw


# ── Form-lambda blending ──────────────────────────────────────────────────────

def _blend_lam(
    cs_lam: Optional[float],
    form_lam: Optional[float],
    weight: float,
) -> Optional[float]:
    """
    Blend a CS-odds-derived lambda with a form-derived lambda.

    weight = 0.0  → pure CS (original behaviour)
    weight = 1.0  → pure form
    weight = 0.35 → production default (CS leads, form adjusts)

    Two guards applied after blending:

    1. Divergence guard — if form_lam is more than 80% above cs_lam the two
       sources are in strong disagreement.  The CS odds from sharp-market
       bookmakers are usually the more reliable signal, so we halve the form
       weight in that case rather than suppress form entirely.

    2. Lambda ceiling — after blending, cap the result at form_lambda_ceiling
       (default 3.0).  This prevents a team on a freak high-scoring run from
       inflating expected goals above realistic territory and wiping out all
       under-goals signals for that fixture.

    If either input is None/zero we fall back to whichever is available.
    """
    ceiling = float(R.get("form_lambda_ceiling", 3.0))

    cs_ok = cs_lam is not None and cs_lam > 0
    form_ok = form_lam is not None and form_lam > 0

    if cs_ok and form_ok:
        # Divergence guard: if form is wildly higher than CS, trust CS more.
        effective_weight = weight
        if form_lam > cs_lam * 1.8:  # type: ignore[operator]
            effective_weight = weight * 0.5
        blended = (1.0 - effective_weight) * cs_lam + effective_weight * form_lam  # type: ignore[operator]
        return min(blended, ceiling)

    if form_ok:
        return min(form_lam, ceiling)  # type: ignore[return-value]
    return cs_lam


# ── Maths helpers ─────────────────────────────────────────────────────────────

def poisson_cdf(lam: float, k: int) -> Optional[float]:
    """P(X ≤ k) where X ~ Poisson(lam). Returns None if lam invalid."""
    if not lam or lam <= 0 or k < 0:
        return None
    total = 0.0
    term = math.exp(-lam)
    for i in range(k + 1):
        total += term
        if i < k:
            term = term * lam / (i + 1)
    return min(1.0, total)


def lambda_from_cs00(odds_00: Optional[float], overround: float = 1.40) -> Optional[float]:
    """Derive total Poisson lambda from the 0-0 CS odds (with overround correction)."""
    if not odds_00 or odds_00 <= 1:
        return None
    true_p = (1.0 / odds_00) / overround
    if true_p <= 0 or true_p >= 1:
        return None
    return -math.log(true_p)


def derive_lambdas(
    odds_00: Optional[float],
    odds_10: Optional[float],
    odds_01: Optional[float],
) -> Optional[dict]:
    """Derive independent home/away lambdas. Overround cancels in ratio."""
    if not odds_00 or not odds_10 or not odds_01:
        return None
    if odds_00 <= 1 or odds_10 <= 1 or odds_01 <= 1:
        return None
    lh = odds_00 / odds_10
    la = odds_00 / odds_01
    return {"lambda_h": lh, "lambda_a": la, "lambda_total": lh + la}


def poisson_prob_for_market(bet_key: str, lam: float, lambdas: Optional[dict] = None) -> Optional[float]:
    if not lam or lam <= 0:
        return None
    if bet_key == "under2_5":
        return poisson_cdf(lam, 2)
    if bet_key == "under3_5":
        return poisson_cdf(lam, 3)
    if bet_key == "over1_5":
        v = poisson_cdf(lam, 1)
        return 1.0 - v if v is not None else None
    if bet_key == "over2_5":
        v = poisson_cdf(lam, 2)
        return 1.0 - v if v is not None else None
    if bet_key == "over0_5":
        v = poisson_cdf(lam, 0)
        return 1.0 - v if v is not None else None
    if bet_key == "over3_5":
        v = poisson_cdf(lam, 3)
        return 1.0 - v if v is not None else None
    if bet_key == "over0_5_fh":
        return 1.0 - math.exp(-lam)
    if bet_key == "btts_yes":
        lh = lambdas["lambda_h"] if lambdas else lam / 2
        la = lambdas["lambda_a"] if lambdas else lam / 2
        return (1.0 - math.exp(-lh)) * (1.0 - math.exp(-la))
    return None


def team_side_over_poisson_prob(lam: float, line: float) -> Optional[float]:
    """P(team goals > line) for half-lines 0.5 (≥1 goal) and 1.5 (≥2) under Poisson(lam)."""
    if lam is None or lam <= 0:
        return None
    need = int(line + 0.5)
    cdf_v = poisson_cdf(lam, need - 1)
    if cdf_v is None:
        return None
    return 1.0 - cdf_v


def compute_team_over_edge(
    lam: float,
    line: float,
    market_odds: Optional[float],
    min_edge_pct: float,
    bet_key: str,
) -> dict:
    if not market_odds or market_odds <= 1:
        return {"has_price": False, "has_lambda": lam is not None and lam > 0, "bet_key": bet_key}
    p = team_side_over_poisson_prob(lam, line)
    if p is None:
        return {"has_price": True, "has_lambda": False, "market_odds": market_odds, "bet_key": bet_key}
    breakeven = 1.0 / market_odds
    edge = p - breakeven
    edge_pct = edge * 100
    return {
        "has_price": True,
        "has_lambda": True,
        "bet_key": bet_key,
        "market_odds": market_odds,
        "poisson_prob": p,
        "breakeven": breakeven,
        "edge": edge,
        "edge_pct": edge_pct,
        "has_edge": edge_pct >= min_edge_pct,
        "min_edge_pct": min_edge_pct,
    }


def compute_edge(bet_key: str, lam: float, market_odds: Optional[float], lambdas: Optional[dict] = None, min_edge_pct: float = 3.0) -> dict:
    """Compute Poisson edge vs market breakeven."""
    if not market_odds or market_odds <= 1:
        return {"has_price": False, "has_lambda": bool(lam), "bet_key": bet_key}
    poisson_prob = poisson_prob_for_market(bet_key, lam, lambdas)
    if poisson_prob is None:
        return {"has_price": True, "has_lambda": False, "market_odds": market_odds, "bet_key": bet_key}
    breakeven = 1.0 / market_odds
    edge = poisson_prob - breakeven
    edge_pct = edge * 100
    return {
        "has_price": True, "has_lambda": True, "bet_key": bet_key,
        "market_odds": market_odds, "poisson_prob": poisson_prob,
        "breakeven": breakeven, "edge": edge,
        "edge_pct": edge_pct, "has_edge": edge_pct >= min_edge_pct,
        "min_edge_pct": min_edge_pct,
        "lambda_h": lambdas["lambda_h"] if lambdas else None,
        "lambda_a": lambdas["lambda_a"] if lambdas else None,
    }


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class PoissonResult:
    rule_key: str
    market: str          # the recommended market string
    rule_pass: bool
    rule_strong: bool
    poisson_prob: Optional[float]
    edge_pct: Optional[float]
    has_edge: bool
    grade: str           # A / B / C / N
    lambda_h: Optional[float]
    lambda_a: Optional[float]
    lambda_total: Optional[float]
    mixed_signals: list[str] = field(default_factory=list)
    missing_markets: list[str] = field(default_factory=list)
    # True when rolling form data was available and blended into lambda
    form_blended: bool = False


@dataclass
class PoissonFixtureResult:
    fixture_id: int
    results: list[PoissonResult]
    contradiction: bool
    mixed_signals: list[str]
    priority_score: float
    priority_label: str


# ── Rule evaluators (ported from rules.js) ────────────────────────────────────

def _grade(rule_pass: bool, rule_strong: bool) -> str:
    # Edge-vs-market no longer participates in grading (retired 2026-07-02):
    # A = rule passed with strong conviction, B = rule passed, N = no signal.
    if not rule_pass:
        return "N"
    return "A" if rule_strong else "B"


def _marginal_team_over_result(
    *,
    rule_key: str,
    market: str,
    side: str,
    line: float,
    odds_key: str,
    odds: dict,
    signal_odds: dict,
    form_lambdas: Optional[dict],
) -> PoissonResult:
    """
    Home/Away Over 0.5 or 1.5: independent Poisson on blended λ_home / λ_away
    from CS ratios, vs team 'Over x.x' book price.
    """
    d = derive_lambdas(odds.get("s00"), odds.get("s10"), odds.get("s01"))
    if not d:
        return PoissonResult(
            rule_key=rule_key, market=market,
            rule_pass=False, rule_strong=False,
            poisson_prob=None, edge_pct=None, has_edge=False,
            grade="N", lambda_h=None, lambda_a=None, lambda_total=None,
            missing_markets=["CS 0-0 / 1-0 / 0-1"],
        )
    lh0, la0 = d["lambda_h"], d["lambda_a"]
    fw = _form_lambda_weight()
    lh = _blend_lam(lh0, form_lambdas.get("lambda_h") if form_lambdas else None, fw)
    la = _blend_lam(la0, form_lambdas.get("lambda_a") if form_lambdas else None, fw)
    lam_side = lh if side == "h" else la
    mo = signal_odds.get(odds_key)
    min_odd = MARKET_MIN_ODDS.get(market)
    if mo is not None and min_odd is not None and mo < min_odd:
        return PoissonResult(
            rule_key=rule_key, market=market,
            rule_pass=False, rule_strong=False,
            poisson_prob=team_side_over_poisson_prob(lam_side, line),
            edge_pct=None, has_edge=False,
            grade="N", lambda_h=lh, lambda_a=la, lambda_total=d["lambda_total"],
            form_blended=bool(form_lambdas),
        )
    # edge_pct is still computed for diagnostics/display, but acceptance is
    # probability-based: the model itself must see the outcome as likely.
    er = compute_team_over_edge(lam_side, line, mo, 0.0, odds_key)
    p = er.get("poisson_prob")
    edge_pct_v = er.get("edge_pct")
    min_p = float(R.get("team_over_min_prob", 0.60))
    strong_p = float(R.get("team_over_strong_prob", 0.72))
    rule_pass = bool(er.get("has_price")) and p is not None and p >= min_p
    strong = rule_pass and p >= strong_p
    return PoissonResult(
        rule_key=rule_key, market=market,
        rule_pass=rule_pass, rule_strong=strong,
        poisson_prob=p,
        edge_pct=edge_pct_v, has_edge=bool(edge_pct_v is not None and edge_pct_v > 0),
        grade=_grade(rule_pass, strong),
        lambda_h=lh, lambda_a=la, lambda_total=d["lambda_total"],
        form_blended=bool(form_lambdas),
    )


def _evaluate_cs_cascade(odds: dict, signal_odds: dict, form_lambdas: Optional[dict] = None) -> list[PoissonResult]:
    """0-0 CS cascade rules mapping odds ranges to markets."""
    s00 = odds.get("s00")
    results = []
    if s00 is None:
        return results

    cs_lam = lambda_from_cs00(s00, R["cs_overround_factor"])
    fw = _form_lambda_weight()
    # Blend total lambda once; all cascade rules share the same total expected goals.
    lam = _blend_lam(cs_lam, form_lambdas.get("lambda_total") if form_lambdas else None, fw)

    cascade_rules = [
        ("cs00u25",    R["cs00_u25_min"],     R["cs00_u25_max"],     "Under 2.5", "under2_5"),
        ("cs00u35",    R["cs00_u35_min"],     R["cs00_u35_max"],     "Under 3.5", "under3_5"),
        ("cs00mid",    R["cs00_mid_min"],      R["cs00_mid_max"],     "Under 3.5", "under3_5"),
        ("cs00o15",    R["cs00_o15_min"],      R["cs00_o15_max"],     "Over 1.5",  "over1_5"),
        ("cs00extreme", R["cs00_extreme_min"], None,                  "Over 1.5",  "over1_5"),
    ]

    for rule_key, lo, hi, market, bet_key in cascade_rules:
        if hi is not None:
            pass_ = lo <= s00 <= hi
        else:
            pass_ = s00 >= lo

        # Don't fire if the available market price is below the minimum profitable odds.
        if pass_ and market in MARKET_MIN_ODDS:
            market_odds = signal_odds.get(bet_key)
            if market_odds is None or market_odds < MARKET_MIN_ODDS[market]:
                pass_ = False

        # edge_pct retained for diagnostics only — no longer gates or grades.
        edge_result = compute_edge(bet_key, lam, signal_odds.get(bet_key), min_edge_pct=0.0)
        has_edge = edge_result.get("has_edge", False)

        results.append(PoissonResult(
            rule_key=rule_key, market=market,
            rule_pass=pass_, rule_strong=False,
            poisson_prob=edge_result.get("poisson_prob"),
            edge_pct=edge_result.get("edge_pct"), has_edge=has_edge,
            grade=_grade(pass_, False),
            lambda_h=None, lambda_a=None, lambda_total=lam,
            form_blended=bool(form_lambdas),
        ))
    return results


def _evaluate_over15_signal(odds: dict, signal_odds: dict) -> PoissonResult:
    s10 = odds.get("s10"); s00 = odds.get("s00"); s01 = odds.get("s01")
    s11 = odds.get("s11"); s20 = odds.get("s20"); s02 = odds.get("s02")

    base_ok = (s10 is not None and s10 >= R["over15_min_10"] and
               s00 is not None and s00 >= R["over15_min_00"] and
               s01 is not None and s01 >= R["over15_min_01"])
    supports = [
        s11 is not None and s11 <= R["over15_support_max_11"],
        s20 is not None and s20 <= R["over15_support_max_20"],
        s02 is not None and s02 <= R["over15_support_max_02"],
    ]
    pass_ = base_ok and any(supports)
    lam = lambda_from_cs00(s00, R["cs_overround_factor"])
    edge_result = compute_edge("over1_5", lam, signal_odds.get("over1_5"), min_edge_pct=0.0)
    rule_strong15 = pass_ and sum(supports) >= 2
    has_edge15 = edge_result.get("has_edge", False)
    return PoissonResult(
        rule_key="over15", market="Over 1.5", rule_pass=pass_, rule_strong=rule_strong15,
        poisson_prob=edge_result.get("poisson_prob"),
        edge_pct=edge_result.get("edge_pct"), has_edge=has_edge15,
        grade=_grade(pass_, rule_strong15),
        lambda_h=None, lambda_a=None, lambda_total=lam,
    )


def _evaluate_over25_signal(odds: dict, signal_odds: dict) -> PoissonResult:
    s22 = odds.get("s22"); s00 = odds.get("s00")
    s10 = odds.get("s10"); s01 = odds.get("s01")
    s21 = odds.get("s21"); s12 = odds.get("s12")

    core_ok = (s22 is not None and s22 <= R["over25_max_22"] and
               s00 is not None and s00 >= R["over25_min_00"] and
               s10 is not None and s10 >= R["over25_min_10"] and
               s01 is not None and s01 >= R["over25_min_01"])
    support_ok = ((s21 is not None and s21 <= R["over25_support_max_21"]) or
                  (s12 is not None and s12 <= R["over25_support_max_12"]))
    pass_ = core_ok and support_ok
    lam = lambda_from_cs00(s00, R["cs_overround_factor"])
    edge_result = compute_edge("over2_5", lam, signal_odds.get("over2_5"), min_edge_pct=0.0)
    has_edge25 = edge_result.get("has_edge", False)
    return PoissonResult(
        rule_key="over25", market="Over 2.5", rule_pass=pass_, rule_strong=pass_,
        poisson_prob=edge_result.get("poisson_prob"),
        edge_pct=edge_result.get("edge_pct"), has_edge=has_edge25,
        grade=_grade(pass_, pass_),
        lambda_h=None, lambda_a=None, lambda_total=lam,
    )


# ── Contradiction detection ───────────────────────────────────────────────────

def detect_contradictions(results: dict[str, PoissonResult]) -> list[str]:
    mixed = []
    over25 = results.get("over25")
    cs00u25 = results.get("cs00u25")
    cs00mid = results.get("cs00mid")

    if over25 and over25.rule_pass and cs00u25 and cs00u25.rule_pass:
        mixed.append("O2.5 signal + U2.5 CS")
    if over25 and over25.rule_pass and cs00mid and cs00mid.rule_pass:
        mixed.append("O2.5 signal + U3.5 Mid")
    return mixed


# ── Full fixture analysis ─────────────────────────────────────────────────────

def analyse_fixture(
    fixture_id: int,
    odds: dict,                          # CS odds: s00, s10, s01, s11, s20, s02, s21, s12, s22, s31, s13
    signal_odds: dict,                   # market odds: over1_5, over2_5, under2_5
    form_lambdas: Optional[dict] = None, # rolling form: {lambda_h, lambda_a, lambda_total}; None = CS-only
) -> PoissonFixtureResult:
    home_o05_r = _marginal_team_over_result(
        rule_key="home_o05", market="Home Over 0.5", side="h", line=0.5, odds_key="home_o05",
        odds=odds, signal_odds=signal_odds, form_lambdas=form_lambdas,
    )
    cascade = _evaluate_cs_cascade(odds, signal_odds, form_lambdas)
    over15_r = _evaluate_over15_signal(odds, signal_odds)
    over25_r = _evaluate_over25_signal(odds, signal_odds)

    cascade_map = {r.rule_key: r for r in cascade}
    all_results = {
        "home_o05": home_o05_r,
        "over15": over15_r, "over25": over25_r,
        **cascade_map,
    }

    mixed = detect_contradictions(all_results)
    primary_keys = ["cs00u25", "cs00u35", "cs00o15", "cs00mid", "cs00extreme"]
    passed = [k for k in primary_keys if all_results.get(k) and all_results[k].rule_pass]
    strong = [k for k in primary_keys if all_results.get(k) and all_results[k].rule_strong]

    score = max(0.0, len(passed) + len(strong) * 0.5 - len(mixed) * 0.25)
    if mixed:
        label = "Mixed"
    elif score >= 2.5:
        label = "High"
    elif score >= 1.5:
        label = "Medium"
    elif passed:
        label = "Watch"
    else:
        label = "No signal"

    return PoissonFixtureResult(
        fixture_id=fixture_id,
        results=list(all_results.values()),
        contradiction=bool(mixed),
        mixed_signals=mixed,
        priority_score=score,
        priority_label=label,
    )

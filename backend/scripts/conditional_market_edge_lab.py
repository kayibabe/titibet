"""Conditional market-edge decomposition lab for TiTiBet.

Research-only. Reads settled fixtures and earliest stored market snapshots directly
from the application database, reproduces the existing ungated model probabilities,
and evaluates conditional regimes without changing production state.

The lab focuses on four markets:
  - Under 2.5
  - Under 3.5
  - Over 2.5
  - Away Under 0.5

It reports single-factor segments and selected interactions across odds, model
probability, league tier, and model agreement, followed by chronological
validation of research candidates.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from sqlalchemy import select

from app.core.config import MARKETS, POISSON_RULES, get_league_tier
from app.core.database import AsyncSessionLocal
from app.engines import bayesian as bay_engine
from app.engines import poisson as poi_engine
from app.models import Fixture, MarketSnapshot
from app.quant.probability import expected_value
from app.services.form_service import _exp_weighted_avg, _fetch_team_goals
from app.services.signal_engine import (
    _build_away_totals,
    _build_cs_by_bookie,
    _build_double_chance,
    _build_exact_goals,
    _build_goals_ou,
    _build_home_totals,
    _build_match_winner,
    _build_poisson_odds,
    _build_win_to_nil_away,
    _build_win_to_nil_home,
)
from app.quant.calibration import calibration_report

TARGET_MARKETS = (
    "Under 2.5",
    "Under 3.5",
    "Over 2.5",
    "Away Under 0.5",
)
ENGINES = ("bayesian", "poisson", "ensemble")

ODDS_BANDS = (
    ("<1.25", None, 1.25),
    ("1.25-1.49", 1.25, 1.50),
    ("1.50-1.69", 1.50, 1.70),
    ("1.70-2.09", 1.70, 2.10),
    ("2.10-2.49", 2.10, 2.50),
    ("2.50+", 2.50, None),
)
PROB_BANDS = (
    ("<0.55", None, 0.55),
    ("0.55-0.64", 0.55, 0.65),
    ("0.65-0.74", 0.65, 0.75),
    ("0.75-0.84", 0.75, 0.85),
    ("0.85+", 0.85, None),
)
RESEARCH_MIN_N = 30
RESEARCH_MIN_EV = 0.03
RESEARCH_MIN_ROI = 0.0
RESEARCH_MAX_CAL_ERROR = 0.10


@dataclass(frozen=True)
class Observation:
    fixture_date: date
    market: str
    engine: str
    odds: float | None
    probability: float
    outcome: int
    ev: float | None
    league: str
    country: str
    tier: int
    agreement: str
    bayesian_probability: float | None
    poisson_probability: float | None



def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def earliest_snapshots(snapshots: list[MarketSnapshot]) -> list[MarketSnapshot]:
    """Select the earliest observation for each bookmaker/market/selection."""
    earliest: dict[tuple[str, str, str], MarketSnapshot] = {}
    for snap in snapshots:
        key = (snap.bookmaker, snap.market_type, snap.selection_name)
        current = earliest.get(key)
        if current is None:
            earliest[key] = snap
            continue
        cur_ts = current.pulled_at or datetime.max
        snap_ts = snap.pulled_at or datetime.max
        if snap_ts < cur_ts or (snap_ts == cur_ts and (snap.id or 0) < (current.id or 0)):
            earliest[key] = snap
    return list(earliest.values())


POISSON_PRICE_KEYS = {
    "Over 1.5": "over1_5",
    "Over 2.5": "over2_5",
    "Under 2.5": "under2_5",
    "Under 3.5": "under3_5",
    "Home Over 0.5": "home_o05",
    "Away Over 0.5": "away_o05",
}


def outcome_for(market: str, fixture: Fixture) -> int | None:
    if fixture.home_score is None or fixture.away_score is None:
        return None
    fn = MARKETS.get(market)
    return int(bool(fn(fixture.home_score, fixture.away_score))) if fn else None


def best_price(market: str, bay_signal: Any, poi_signal_odds: dict[str, float]) -> float | None:
    exec_odd = getattr(bay_signal, "exec_odd", None) if bay_signal is not None else None
    if _finite(exec_odd) and float(exec_odd) > 1.0:
        return float(exec_odd)
    key = POISSON_PRICE_KEYS.get(market)
    odd = poi_signal_odds.get(key) if key else None
    if _finite(odd) and float(odd) > 1.0:
        return float(odd)
    return None


def agreement_label(bp: float | None, pp: float | None) -> str:
    if bp is None or pp is None:
        return "BAYESIAN_ONLY" if pp is None else "POISSON_ONLY"
    distance = abs(bp - pp)
    if distance > 0.10:
        return "DISAGREE"
    if bp >= 0.70 and pp >= 0.70:
        return "BOTH_HIGH"
    return "BOTH"


def band_name(value: float, bands: Iterable[tuple[str, float | None, float | None]]) -> str | None:
    for name, lo, hi in bands:
        if lo is not None and value < lo:
            continue
        if hi is not None and value >= hi:
            continue
        return name
    return None


def probability_for_engine(engine: str, bp: float | None, pp: float | None) -> float | None:
    if engine == "bayesian":
        return bp
    if engine == "poisson":
        return pp
    if bp is not None and pp is not None:
        return 0.6 * bp + 0.4 * pp
    return bp if bp is not None else pp


def aggregate(rows: list[Observation]) -> dict[str, Any] | None:
    if not rows:
        return None
    probs = [r.probability for r in rows]
    outcomes = [r.outcome for r in rows]
    priced = [r for r in rows if r.odds is not None and r.odds > 1.0]
    evs = [r.ev for r in priced if r.ev is not None]
    profit = sum((r.odds - 1.0) if r.outcome else -1.0 for r in priced)
    cal = calibration_report(probs, outcomes)
    wins = sum(outcomes)
    return {
        "n": len(rows),
        "priced_n": len(priced),
        "wins": wins,
        "hit_rate": round(wins / len(rows), 6),
        "mean_probability": round(sum(probs) / len(probs), 6),
        "mean_implied_probability": round(sum(1.0 / r.odds for r in priced) / len(priced), 6) if priced else None,
        "mean_ev": round(sum(evs) / len(evs), 6) if evs else None,
        "positive_ev_rate": round(sum(ev >= 0 for ev in evs) / len(evs), 6) if evs else None,
        "roi": round(profit / len(priced), 6) if priced else None,
        "brier": round(cal.brier, 6),
        "log_loss": round(cal.log_loss, 6),
        "calibration_error": round(cal.mean_absolute_calibration_error, 6),
    }


def is_research_candidate(metrics: dict[str, Any] | None) -> bool:
    if not metrics:
        return False
    return (
        metrics["n"] >= RESEARCH_MIN_N
        and metrics["mean_ev"] is not None
        and metrics["mean_ev"] >= RESEARCH_MIN_EV
        and metrics["roi"] is not None
        and metrics["roi"] >= RESEARCH_MIN_ROI
        and metrics["calibration_error"] <= RESEARCH_MAX_CAL_ERROR
    )


def segment_records(rows: list[Observation], dimensions: tuple[str, ...]) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], list[Observation]] = defaultdict(list)
    for row in rows:
        values: list[Any] = []
        for dim in dimensions:
            if dim == "odds_band":
                values.append(band_name(row.odds, ODDS_BANDS) if row.odds is not None else "UNPRICED")
            elif dim == "probability_band":
                values.append(band_name(row.probability, PROB_BANDS))
            elif dim == "tier":
                values.append(f"T{row.tier}")
            elif dim == "agreement":
                values.append(row.agreement)
            else:
                raise ValueError(f"Unknown dimension: {dim}")
        buckets[tuple(values)].append(row)

    output: list[dict[str, Any]] = []
    for key, bucket in buckets.items():
        metrics = aggregate(bucket)
        if metrics is None:
            continue
        item = {dim: val for dim, val in zip(dimensions, key)}
        item.update(metrics)
        item["research_candidate"] = is_research_candidate(metrics)
        output.append(item)

    output.sort(key=lambda x: (not x["research_candidate"], -(x["roi"] if x["roi"] is not None else -999), -x["n"]))
    return output


def candidate_key(item: dict[str, Any], dimensions: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(item.get(dim) for dim in dimensions)


def matches_candidate(row: Observation, item: dict[str, Any], dimensions: tuple[str, ...]) -> bool:
    values = {
        "odds_band": band_name(row.odds, ODDS_BANDS) if row.odds is not None else "UNPRICED",
        "probability_band": band_name(row.probability, PROB_BANDS),
        "tier": f"T{row.tier}",
        "agreement": row.agreement,
    }
    return all(values[dim] == item.get(dim) for dim in dimensions)


async def build_observations(date_from: date, date_to: date, markets: set[str]) -> tuple[list[Observation], int]:
    observations: list[Observation] = []
    fixtures_seen = 0
    async with AsyncSessionLocal() as db:
        query = select(Fixture).order_by(Fixture.event_date, Fixture.id)
        query = query.where(Fixture.event_date >= date_from, Fixture.event_date <= date_to)
        query = query.where(Fixture.status.in_(["FT", "AET", "PEN"]))
        query = query.where(Fixture.home_score.is_not(None), Fixture.away_score.is_not(None))
        fixtures = list((await db.execute(query)).scalars().all())

        for fixture in fixtures:
            fixtures_seen += 1
            snaps = list((await db.execute(
                select(MarketSnapshot).where(MarketSnapshot.fixture_id == fixture.id)
            )).scalars().all())
            if not snaps:
                continue
            snaps = earliest_snapshots(snaps)

            cs = _build_cs_by_bookie(snaps)
            goals = _build_goals_ou(snaps)
            mw = _build_match_winner(snaps)
            dc = _build_double_chance(snaps)
            ht = _build_home_totals(snaps)
            at = _build_away_totals(snaps)
            wtn_h = _build_win_to_nil_home(snaps)
            wtn_a = _build_win_to_nil_away(snaps)
            eg = _build_exact_goals(snaps)
            poi_odds, poi_signal_odds = _build_poisson_odds(snaps)

            bay_result = bay_engine.analyse_fixture(
                fixture_id=fixture.id,
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                league=fixture.league or "",
                country=fixture.country or "",
                cs_by_bookie=cs,
                goals_ou=goals,
                btts={},
                match_winner=mw,
                double_chance=dc,
                home_totals=ht,
                away_totals=at,
                win_to_nil_home=wtn_h,
                win_to_nil_away=wtn_a,
                exact_goals=eg,
                all_markets=True,
            )

            form_lambdas = None
            fd = fixture.event_date
            if fd is not None:
                hg = await _fetch_team_goals(db, fixture.home_team, fd, int(POISSON_RULES["rolling_form_games"]))
                ag = await _fetch_team_goals(db, fixture.away_team, fd, int(POISSON_RULES["rolling_form_games"]))
                min_g = int(POISSON_RULES["form_min_games"])
                if len(hg) >= min_g and len(ag) >= min_g:
                    lh = max(_exp_weighted_avg(hg), 0.10)
                    la = max(_exp_weighted_avg(ag), 0.10)
                    form_lambdas = {"lambda_h": lh, "lambda_a": la, "lambda_total": lh + la,
                                    "games_h": len(hg), "games_a": len(ag)}

            poi_result = poi_engine.analyse_fixture(
                fixture_id=fixture.id,
                odds=poi_odds,
                signal_odds=poi_signal_odds,
                form_lambdas=form_lambdas,
            )

            bays = {r.market: r for r in bay_result.market_results}
            pois = {r.market: r for r in poi_result.results if r.poisson_prob is not None}
            league = fixture.league or ""
            country = fixture.country or ""
            tier = get_league_tier(league, country)

            for market in markets:
                y = outcome_for(market, fixture)
                if y is None:
                    continue
                b = bays.get(market)
                p = pois.get(market)
                bp = getattr(b, "derived_prob", None) if b is not None else None
                pp = getattr(p, "poisson_prob", None) if p is not None else None
                if bp is None and pp is None:
                    continue
                price = best_price(market, b, poi_signal_odds)
                agreement = agreement_label(
                    float(bp) if _finite(bp) else None,
                    float(pp) if _finite(pp) else None,
                )
                for engine in ENGINES:
                    prob = probability_for_engine(
                        engine,
                        float(bp) if _finite(bp) else None,
                        float(pp) if _finite(pp) else None,
                    )
                    if prob is None or not 0 <= prob <= 1:
                        continue
                    ev = expected_value(prob, price) if price and price > 1 else None
                    observations.append(Observation(
                        fixture_date=fixture.event_date,
                        market=market,
                        engine=engine,
                        odds=price,
                        probability=prob,
                        outcome=int(y),
                        ev=float(ev) if _finite(ev) else None,
                        league=league,
                        country=country,
                        tier=tier,
                        agreement=agreement,
                        bayesian_probability=float(bp) if _finite(bp) else None,
                        poisson_probability=float(pp) if _finite(pp) else None,
                    ))
    return observations, fixtures_seen


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from", dest="date_from", type=date.fromisoformat, default=date(2026, 1, 1))
    p.add_argument("--to", dest="date_to", type=date.fromisoformat, default=date(2026, 6, 30))
    p.add_argument("--validation-from", type=date.fromisoformat, default=date(2026, 5, 1))
    p.add_argument("--validation-to", type=date.fromisoformat, default=date(2026, 6, 30))
    p.add_argument("--min-n", type=int, default=RESEARCH_MIN_N)
    p.add_argument("--min-ev", type=float, default=RESEARCH_MIN_EV)
    p.add_argument("--min-roi", type=float, default=RESEARCH_MIN_ROI)
    p.add_argument("--max-calibration-error", type=float, default=RESEARCH_MAX_CAL_ERROR)
    p.add_argument("--output", default="conditional_market_edge_lab.json")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    globals()["RESEARCH_MIN_N"] = args.min_n
    globals()["RESEARCH_MIN_EV"] = args.min_ev
    globals()["RESEARCH_MIN_ROI"] = args.min_roi
    globals()["RESEARCH_MAX_CAL_ERROR"] = args.max_calibration_error

    all_rows, fixtures_seen = await build_observations(args.date_from, args.to, set(TARGET_MARKETS))
    discovery_rows = [r for r in all_rows if r.fixture_date < args.validation_from]
    validation_rows = [r for r in all_rows if args.validation_from <= r.fixture_date <= args.validation_to]

    dimensions_list = [
        ("odds_band",),
        ("probability_band",),
        ("tier",),
        ("agreement",),
        ("odds_band", "tier"),
        ("odds_band", "agreement"),
        ("probability_band", "agreement"),
    ]

    by_market: dict[str, Any] = {}
    candidates: list[dict[str, Any]] = []
    for market in TARGET_MARKETS:
        market_discovery = [r for r in discovery_rows if r.market == market]
        market_validation = [r for r in validation_rows if r.market == market]
        market_payload: dict[str, Any] = {}
        for engine in ENGINES:
            drows = [r for r in market_discovery if r.engine == engine]
            vrows = [r for r in market_validation if r.engine == engine]
            engine_payload = {
                "overall_discovery": aggregate(drows),
                "overall_validation": aggregate(vrows),
                "segments": {},
            }
            for dims in dimensions_list:
                segs = segment_records(drows, dims)
                engine_payload["segments"]["×".join(dims)] = segs
                for seg in segs:
                    if seg["research_candidate"]:
                        validation_match = [r for r in vrows if matches_candidate(r, seg, dims)]
                        vm = aggregate(validation_match)
                        candidates.append({
                            "market": market,
                            "engine": engine,
                            "dimensions": dims,
                            "regime": {d: seg[d] for d in dims},
                            "discovery": {k: seg[k] for k in ("n", "hit_rate", "mean_ev", "roi", "calibration_error")},
                            "validation": ({k: vm[k] for k in ("n", "hit_rate", "mean_ev", "roi", "calibration_error")} if vm else None),
                            "validation_survives": bool(vm and is_research_candidate(vm)),
                        })
            market_payload[engine] = engine_payload
        by_market[market] = market_payload

    # Deduplicate equivalent candidates produced by overlapping dimension sets only
    # by keeping the exact regime signature. This is still research evidence, not a rule.
    candidates.sort(key=lambda x: (not x["validation_survives"], -(x["discovery"]["roi"] or -999), -x["discovery"]["n"]))
    survivors = [c for c in candidates if c["validation_survives"]]

    result = {
        "report_type": "conditional_market_edge_lab_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "scope": {
            "from": args.date_from.isoformat(),
            "to": args.to.isoformat(),
            "validation_from": args.validation_from.isoformat(),
            "validation_to": args.validation_to.isoformat(),
            "markets": list(TARGET_MARKETS),
        },
        "fixtures_seen": fixtures_seen,
        "parameters": {
            "research_min_n": RESEARCH_MIN_N,
            "research_min_ev": RESEARCH_MIN_EV,
            "research_min_roi": RESEARCH_MIN_ROI,
            "research_max_calibration_error": RESEARCH_MAX_CAL_ERROR,
            "odds_bands": [x[0] for x in ODDS_BANDS],
            "probability_bands": [x[0] for x in PROB_BANDS],
            "agreement_definition": "BOTH_HIGH if both engines >=0.70 and within 10pp; BOTH if within 10pp; DISAGREE if >10pp apart; single-engine otherwise",
        },
        "production_rules_changed": False,
        "methodology": {
            "point_in_time_form": True,
            "odds": "earliest stored snapshot; Bayesian execution odd where available, otherwise Poisson market price",
            "ensemble": "60% Bayesian + 40% Poisson when both exist",
            "research_candidate": "N >= configured floor, mean EV >= configured floor, ROI >= configured floor, calibration error <= configured ceiling",
            "temporal_validation": "candidate discovery before validation-from; validation period evaluated separately",
        },
        "by_market": by_market,
        "research_candidates": candidates,
        "validation_survivors": survivors,
        "summary": {
            "candidate_count": len(candidates),
            "validation_survivor_count": len(survivors),
        },
    }

    output = Path(args.output)
    if not output.is_absolute():
        output = Path.cwd() / output
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nSaved conditional market-edge report to: {output.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

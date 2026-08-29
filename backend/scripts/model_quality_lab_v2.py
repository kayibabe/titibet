"""Ungated model-quality benchmark for TiTiBet.

Measures Bayesian, Poisson and a simple weighted ensemble independently of live
signal gates. Missing engine output for a fixture is treated as missing data,
not an execution failure.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from sqlalchemy import select
from app.core.config import MARKETS, POISSON_RULES
from app.core.database import AsyncSessionLocal
from app.engines import bayesian as bay_engine
from app.engines import poisson as poi_engine
from app.models import Fixture, MarketSnapshot
from app.quant.calibration import calibration_report, wilson_interval
from app.quant.probability import expected_value
from app.services.form_service import _fetch_team_goals, _exp_weighted_avg
from app.services.signal_engine import (
    _build_away_totals, _build_cs_by_bookie, _build_double_chance,
    _build_exact_goals, _build_goals_ou, _build_home_totals,
    _build_match_winner, _build_poisson_odds,
    _build_win_to_nil_away, _build_win_to_nil_home,
)

POISSON_PRICE_KEYS = {
    "Over 1.5": "over1_5", "Over 2.5": "over2_5", "Under 2.5": "under2_5",
    "Under 3.5": "under3_5", "Home Over 0.5": "home_o05",
    "Away Over 0.5": "away_o05", "Over 0.5 1H": "over05fh",
}


def earliest_snapshots(snapshots: list[MarketSnapshot]) -> list[MarketSnapshot]:
    from datetime import datetime
    out: dict[tuple[str, str, str], MarketSnapshot] = {}
    for snap in snapshots:
        key = (snap.bookmaker, snap.market_type, snap.selection_name)
        cur = out.get(key)
        if cur is None:
            out[key] = snap
            continue
        cur_ts = cur.pulled_at or datetime.max
        new_ts = snap.pulled_at or datetime.max
        if new_ts < cur_ts or (new_ts == cur_ts and (snap.id or 0) < (cur.id or 0)):
            out[key] = snap
    return list(out.values())


def outcome_for(market: str, fixture: Fixture) -> int | None:
    if fixture.home_score is None or fixture.away_score is None:
        return None
    fn = MARKETS.get(market)
    return int(bool(fn(fixture.home_score, fixture.away_score))) if fn else None


def best_price(market: str, bay, poisson_signal_odds: dict) -> float | None:
    odd = getattr(bay, "exec_odd", None) if bay is not None else None
    if odd and odd > 1:
        return float(odd)
    key = POISSON_PRICE_KEYS.get(market)
    odd = poisson_signal_odds.get(key) if key else None
    return float(odd) if odd and odd > 1 else None


async def run_lab(
    date_from: date | None,
    date_to: date | None,
    market: str | None,
    *,
    include_observations: bool = False,
) -> dict:
    observations: dict[str, list[dict]] = defaultdict(list)
    fixtures_seen = 0

    async with AsyncSessionLocal() as db:
        query = select(Fixture).order_by(Fixture.event_date, Fixture.id)
        if date_from:
            query = query.where(Fixture.event_date >= date_from)
        if date_to:
            query = query.where(Fixture.event_date <= date_to)
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

            bay_result = bay_engine.analyse_fixture(
                fixture_id=fixture.id,
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                league=fixture.league or "",
                country=fixture.country or "",
                cs_by_bookie=_build_cs_by_bookie(snaps),
                goals_ou=_build_goals_ou(snaps),
                btts={},
                match_winner=_build_match_winner(snaps),
                double_chance=_build_double_chance(snaps),
                home_totals=_build_home_totals(snaps),
                away_totals=_build_away_totals(snaps),
                win_to_nil_home=_build_win_to_nil_home(snaps),
                win_to_nil_away=_build_win_to_nil_away(snaps),
                exact_goals=_build_exact_goals(snaps),
                all_markets=True,
            )

            form_lambdas = None
            if fixture.event_date is not None:
                n = int(POISSON_RULES["rolling_form_games"])
                min_games = int(POISSON_RULES["form_min_games"])
                hg = await _fetch_team_goals(db, fixture.home_team, fixture.event_date, n)
                ag = await _fetch_team_goals(db, fixture.away_team, fixture.event_date, n)
                if len(hg) >= min_games and len(ag) >= min_games:
                    lh = max(_exp_weighted_avg(hg), 0.10)
                    la = max(_exp_weighted_avg(ag), 0.10)
                    form_lambdas = {"lambda_h": lh, "lambda_a": la, "lambda_total": lh + la,
                                    "games_h": len(hg), "games_a": len(ag)}

            poi_odds, poi_signal_odds = _build_poisson_odds(snaps)
            poi_result = poi_engine.analyse_fixture(
                fixture_id=fixture.id,
                odds=poi_odds,
                signal_odds=poi_signal_odds,
                form_lambdas=form_lambdas,
            )

            bays = {r.market: r for r in (bay_result.market_results if bay_result else [])}
            pois = {r.market: r for r in (poi_result.results if poi_result else []) if r.poisson_prob is not None}
            all_markets = set(bays) | set(pois)
            if market:
                all_markets &= {market}

            for mkt in all_markets:
                y = outcome_for(mkt, fixture)
                if y is None:
                    continue
                b, p = bays.get(mkt), pois.get(mkt)
                bp, pp = getattr(b, "derived_prob", None), getattr(p, "poisson_prob", None)
                if bp is None and pp is None:
                    continue
                ensemble = 0.6 * float(bp) + 0.4 * float(pp) if bp is not None and pp is not None else float(bp if bp is not None else pp)
                price = best_price(mkt, b, poi_signal_odds)
                for name, prob in (("bayesian", bp), ("poisson", pp), ("ensemble", ensemble)):
                    if prob is None or not 0 <= float(prob) <= 1:
                        continue
                    ev = expected_value(float(prob), price) if price and price > 1 else None
                    observations[mkt].append({
                        "engine": name,
                        "prob": float(prob),
                        "outcome": y,
                        "odds": price,
                        "ev": ev,
                        "fixture_id": fixture.id,
                        "event_date": fixture.event_date.isoformat() if fixture.event_date else None,
                    })

    markets: dict[str, dict] = {}
    for mkt, rows in sorted(observations.items()):
        markets[mkt] = {}
        for name in ("bayesian", "poisson", "ensemble"):
            rs = [r for r in rows if r["engine"] == name]
            if not rs:
                continue
            probs = [r["prob"] for r in rs]
            outcomes = [r["outcome"] for r in rs]
            hits = sum(outcomes)
            cal = calibration_report(probs, outcomes)
            priced = [r for r in rs if r["odds"] and r["odds"] > 1]
            evs = [r["ev"] for r in priced if r["ev"] is not None]
            profit = sum((r["odds"] - 1.0) if r["outcome"] else -1.0 for r in priced)
            markets[mkt][name] = {
                "n": len(rs), "wins": hits, "hit_rate": round(hits / len(rs), 6),
                "hit_rate_ci": tuple(round(x, 6) for x in wilson_interval(hits, len(rs))),
                "brier": round(cal.brier, 6), "log_loss": round(cal.log_loss, 6),
                "calibration_error": round(cal.mean_absolute_calibration_error, 6),
                "mean_probability": round(sum(probs) / len(probs), 6),
                "mean_implied_probability": round(sum(1 / r["odds"] for r in priced) / len(priced), 6) if priced else None,
                "mean_ev": round(sum(evs) / len(evs), 6) if evs else None,
                "positive_ev_rate": round(sum(ev >= 0 for ev in evs) / len(evs), 6) if evs else None,
                "roi": round(profit / len(priced), 6) if priced else None,
            }

    report = {
        "scope": {"date_from": date_from.isoformat() if date_from else None,
                  "date_to": date_to.isoformat() if date_to else None, "market": market},
        "fixtures_seen": fixtures_seen,
        "markets": markets,
        "methodology": {
            "ungated_model_quality": True,
            "production_rules_applied": False,
            "adaptive_suppression_applied": False,
            "point_in_time_form": True,
            "odds": "earliest stored snapshot; execution price where Bayesian execution odd exists",
            "ensemble": "60% Bayesian + 40% Poisson when both are available; otherwise the available engine probability",
            "missing_bayesian_output": "treated as missing observation",
        },
    }
    if include_observations:
        # Preserve chronological order for walk-forward calibration while keeping
        # the normal report compact unless the caller explicitly requests data.
        report["observations"] = {
            mkt: sorted(rows, key=lambda r: (r["event_date"] or "", r["fixture_id"] or 0, r["engine"]))
            for mkt, rows in sorted(observations.items())
        }
    return report


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="date_from", type=date.fromisoformat)
    p.add_argument("--to", dest="date_to", type=date.fromisoformat)
    p.add_argument("--market")
    p.add_argument("--output", default="model_quality_lab.json")
    p.add_argument("--observations-output", help="Optional JSON file containing raw point-in-time observations")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    report = await run_lab(
        args.date_from, args.date_to, args.market,
        include_observations=bool(args.observations_output),
    )
    output = Path(args.output)
    if not output.is_absolute():
        output = Path.cwd() / output
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.observations_output:
        obs_output = Path(args.observations_output)
        if not obs_output.is_absolute():
            obs_output = Path.cwd() / obs_output
        obs_output.write_text(json.dumps(report.pop("observations", {}), indent=2), encoding="utf-8")
        # Re-write the compact report after removing the raw observation payload.
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Saved point-in-time observations to: {obs_output.resolve()}")
    print(json.dumps(report, indent=2))
    print(f"\nSaved model-quality report to: {output.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

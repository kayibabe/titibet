"""Ungated model-quality benchmark for TiTiBet.

This research-only runner evaluates model probabilities independently of live
signal gates. It uses the earliest stored odds snapshot per bookmaker/market,
point-in-time team form, and actual fixture outcomes. Nothing is written to the
signals table and no production rules are changed.
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
from app.quant.probability import expected_value, implied_probability
from app.services.form_service import _fetch_team_goals, _exp_weighted_avg
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
    _earliest_snapshots if False else _latest_snapshots,
)

# Import the local earliest-snapshot helper without relying on private backtester state.
def earliest_snapshots(snapshots: list[MarketSnapshot]) -> list[MarketSnapshot]:
    from datetime import datetime
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
    "Over 0.5 1H": "over05fh",
}


def outcome_for(market: str, fixture: Fixture) -> int | None:
    if fixture.home_score is None or fixture.away_score is None:
        return None
    fn = MARKETS.get(market)
    if fn is None:
        return None
    return int(bool(fn(fixture.home_score, fixture.away_score)))


def best_price(market: str, bay, poi_signal_odds: dict) -> float | None:
    if bay is not None and getattr(bay, "exec_odd", None) and bay.exec_odd > 1:
        return float(bay.exec_odd)
    key = POISSON_PRICE_KEYS.get(market)
    if key and poi_signal_odds.get(key, 0) > 1:
        return float(poi_signal_odds[key])
    return None


async def run_lab(date_from: date | None, date_to: date | None, market: str | None) -> dict:
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

            # Point-in-time form only: strictly before the fixture date.
            form_lambdas = None
            fd = fixture.event_date
            if fd is not None:
                hg = await _fetch_team_goals(db, fixture.home_team, fd, int(POISSON_RULES["rolling_form_games"]))
                ag = await _fetch_team_goals(db, fixture.away_team, fd, int(POISSON_RULES["rolling_form_games"]))
                min_g = int(POISSON_RULES["form_min_games"])
                if len(hg) >= min_g and len(ag) >= min_g:
                    lh = max(_exp_weighted_avg(hg), 0.10)
                    la = max(_exp_weighted_avg(ag), 0.10)
                    form_lambdas = {"lambda_h": lh, "lambda_a": la,
                                    "lambda_total": lh + la, "games_h": len(hg), "games_a": len(ag)}

            poi_result = poi_engine.analyse_fixture(
                fixture_id=fixture.id,
                odds=poi_odds,
                signal_odds=poi_signal_odds,
                form_lambdas=form_lambdas,
            )

            bays = {r.market: r for r in bay_result.market_results}
            pois = {r.market: r for r in poi_result.results if r.poisson_prob is not None}
            all_markets = set(bays) | set(pois)
            if market:
                all_markets &= {market}

            for mkt in all_markets:
                y = outcome_for(mkt, fixture)
                if y is None:
                    continue
                b = bays.get(mkt)
                p = pois.get(mkt)
                bp = getattr(b, "derived_prob", None)
                pp = getattr(p, "poisson_prob", None)
                if bp is None and pp is None:
                    continue

                price = best_price(mkt, b, poi_signal_odds)
                ensemble = None
                if bp is not None and pp is not None:
                    ensemble = 0.6 * float(bp) + 0.4 * float(pp)
                else:
                    ensemble = float(bp if bp is not None else pp)

                for engine_name, prob in (("bayesian", bp), ("poisson", pp), ("ensemble", ensemble)):
                    if prob is None or not 0 <= float(prob) <= 1:
                        continue
                    ev = expected_value(float(prob), price) if price and price > 1 else None
                    observations[mkt].append({
                        "engine": engine_name,
                        "prob": float(prob),
                        "outcome": y,
                        "odds": price,
                        "ev": ev,
                    })

    result: dict[str, dict] = {}
    for mkt, rows in sorted(observations.items()):
        result[mkt] = {}
        for engine_name in ("bayesian", "poisson", "ensemble"):
            rs = [r for r in rows if r["engine"] == engine_name]
            if not rs:
                continue
            probs = [r["prob"] for r in rs]
            outcomes = [r["outcome"] for r in rs]
            hits = sum(outcomes)
            cal = calibration_report(probs, outcomes)
            valid_prices = [r for r in rs if r["odds"] and r["odds"] > 1]
            evs = [r["ev"] for r in valid_prices if r["ev"] is not None]
            profit = 0.0
            stake = 0.0
            for r in valid_prices:
                stake += 1.0
                profit += (r["odds"] - 1.0) if r["outcome"] else -1.0
            result[mkt][engine_name] = {
                "n": len(rs),
                "wins": hits,
                "hit_rate": round(hits / len(rs), 6),
                "hit_rate_ci": tuple(round(x, 6) for x in wilson_interval(hits, len(rs))),
                "brier": round(cal.brier, 6),
                "log_loss": round(cal.log_loss, 6),
                "calibration_error": round(cal.mean_absolute_calibration_error, 6),
                "mean_probability": round(sum(probs) / len(probs), 6),
                "mean_implied_probability": round(sum(1 / r["odds"] for r in valid_prices) / len(valid_prices), 6) if valid_prices else None,
                "mean_ev": round(sum(evs) / len(evs), 6) if evs else None,
                "positive_ev_rate": round(sum(ev >= 0 for ev in evs) / len(evs), 6) if evs else None,
                "roi": round(profit / stake, 6) if stake else None,
            }

    return {
        "scope": {"date_from": date_from.isoformat() if date_from else None,
                  "date_to": date_to.isoformat() if date_to else None,
                  "market": market},
        "fixtures_seen": fixtures_seen,
        "markets": result,
        "methodology": {
            "ungated_model_quality": True,
            "production_rules_applied": False,
            "adaptive_suppression_applied": False,
            "point_in_time_form": True,
            "odds": "earliest stored snapshot; execution price where Bayesian execution odd exists",
            "ensemble": "60% Bayesian + 40% Poisson when both are available; otherwise available engine probability",
        },
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="date_from", type=date.fromisoformat)
    p.add_argument("--to", dest="date_to", type=date.fromisoformat)
    p.add_argument("--market")
    p.add_argument("--output", default="model_quality_lab.json")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    report = await run_lab(args.date_from, args.date_to, args.market)
    output = Path(args.output)
    if not output.is_absolute():
        output = Path.cwd() / output
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nSaved model-quality report to: {output.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

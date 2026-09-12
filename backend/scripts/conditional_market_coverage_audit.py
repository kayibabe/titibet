from __future__ import annotations

"""Research-only audit of historical coverage for conditional market-edge analysis."""
import argparse
import asyncio
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
import sys
import os

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models import Fixture, MarketSnapshot
from app.engines import bayesian as bay_engine
from app.engines import poisson as poi_engine
from app.services.form_service import _exp_weighted_avg, _fetch_team_goals
from app.services.signal_engine import (
    _build_away_totals, _build_cs_by_bookie, _build_double_chance, _build_exact_goals,
    _build_goals_ou, _build_home_totals, _build_match_winner, _build_poisson_odds,
    _build_win_to_nil_away, _build_win_to_nil_home,
)
from app.core.config import POISSON_RULES

TARGET_MARKETS = ("Under 2.5", "Under 3.5", "Over 2.5", "Away Under 0.5")


def earliest_snapshots(snaps):
    out = {}
    for s in snaps:
        key = (s.bookmaker, s.market_type, s.selection_name)
        cur = out.get(key)
        if cur is None or (s.pulled_at or datetime.max, s.id or 0) < (cur.pulled_at or datetime.max, cur.id or 0):
            out[key] = s
    return list(out.values())


async def audit(date_from: date, date_to: date, validation_from: date, validation_to: date):
    months = defaultdict(lambda: Counter())
    failures = Counter()
    market_coverage = defaultdict(Counter)
    async with AsyncSessionLocal() as db:
        q = select(Fixture).where(
            Fixture.event_date >= date_from,
            Fixture.event_date <= date_to,
            Fixture.status.in_(["FT", "AET", "PEN"]),
            Fixture.home_score.is_not(None), Fixture.away_score.is_not(None),
        ).order_by(Fixture.event_date, Fixture.id)
        fixtures = list((await db.execute(q)).scalars().all())
        for f in fixtures:
            period = "discovery" if f.event_date < validation_from else "validation" if f.event_date <= validation_to else "outside"
            key = f.event_date.strftime("%Y-%m")
            months[key]["settled_fixtures"] += 1
            snaps = list((await db.execute(select(MarketSnapshot).where(MarketSnapshot.fixture_id == f.id))).scalars().all())
            if not snaps:
                months[key]["no_snapshots"] += 1
                failures[(key, "no_snapshots")] += 1
                continue
            months[key]["with_snapshots"] += 1
            snaps = earliest_snapshots(snaps)
            months[key]["earliest_snapshot_rows"] += len(snaps)
            cs = _build_cs_by_bookie(snaps); goals = _build_goals_ou(snaps); mw = _build_match_winner(snaps)
            dc = _build_double_chance(snaps); ht = _build_home_totals(snaps); at = _build_away_totals(snaps)
            wth = _build_win_to_nil_home(snaps); wta = _build_win_to_nil_away(snaps); eg = _build_exact_goals(snaps)
            poi_odds, poi_signal_odds = _build_poisson_odds(snaps)
            months[key]["fixtures_with_cs"] += int(bool(cs))
            months[key]["fixtures_with_goals_ou"] += int(bool(goals))
            months[key]["fixtures_with_poisson_inputs"] += int(bool(poi_odds))
            bay = bay_engine.analyse_fixture(
                fixture_id=f.id, home_team=f.home_team, away_team=f.away_team,
                league=f.league or "", country=f.country or "", cs_by_bookie=cs, goals_ou=goals,
                btts={}, match_winner=mw, double_chance=dc, home_totals=ht, away_totals=at,
                win_to_nil_home=wth, win_to_nil_away=wta, exact_goals=eg, all_markets=True,
            )
            if bay is None:
                months[key]["bayesian_none"] += 1
                failures[(key, "bayesian_none")] += 1
                bay_markets = set()
            else:
                bay_markets = {r.market for r in bay.market_results}
                months[key]["bayesian_scored"] += 1
            form_lambdas = None
            hg = await _fetch_team_goals(db, f.home_team, f.event_date, int(POISSON_RULES["rolling_form_games"]))
            ag = await _fetch_team_goals(db, f.away_team, f.event_date, int(POISSON_RULES["rolling_form_games"]))
            if len(hg) >= int(POISSON_RULES["form_min_games"]) and len(ag) >= int(POISSON_RULES["form_min_games"]):
                lh, la = max(_exp_weighted_avg(hg), 0.10), max(_exp_weighted_avg(ag), 0.10)
                form_lambdas = {"lambda_h": lh, "lambda_a": la, "lambda_total": lh + la}
            poi = poi_engine.analyse_fixture(fixture_id=f.id, odds=poi_odds, signal_odds=poi_signal_odds, form_lambdas=form_lambdas)
            months[key]["poisson_scored"] += int(poi is not None)
            poi_markets = {r.market for r in getattr(poi, "results", []) if getattr(r, "poisson_prob", None) is not None} if poi else set()
            for m in TARGET_MARKETS:
                market_coverage[(key, m)]["bayesian_market"] += int(m in bay_markets)
                market_coverage[(key, m)]["poisson_market"] += int(m in poi_markets)
                if period in {"discovery", "validation"}:
                    market_coverage[(key, m)][period] += 1
            months[key][period + "_fixtures"] += 1
    return months, market_coverage, failures


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="date_from", type=date.fromisoformat, default=date(2026,1,1))
    p.add_argument("--to", dest="date_to", type=date.fromisoformat, default=date(2026,6,30))
    p.add_argument("--validation-from", type=date.fromisoformat, default=date(2026,5,1))
    p.add_argument("--validation-to", type=date.fromisoformat, default=date(2026,6,30))
    p.add_argument("--output", default="conditional_market_coverage_audit.json")
    return p.parse_args()


async def main():
    args = parse_args()
    months, coverage, failures = await audit(args.date_from,args.date_to,args.validation_from,args.validation_to)
    month_rows=[]
    for month in sorted(months):
        row={"month":month, **dict(months[month])}
        month_rows.append(row)
    market_rows=[]
    for (month, market), counts in sorted(coverage.items()):
        market_rows.append({"month":month,"market":market,**dict(counts)})
    result={
        "report_type":"conditional_market_coverage_audit",
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "source_scope":{"date_from":args.date_from.isoformat(),"date_to":args.date_to.isoformat(),"validation_from":args.validation_from.isoformat(),"validation_to":args.validation_to.isoformat()},
        "monthly":month_rows,
        "market_coverage":market_rows,
        "failure_counts":[{"month":m,"reason":r,"count":c} for (m,r),c in sorted(failures.items())],
        "decision":"NEEDS_DISCOVERY_WINDOW_SELECTION" if not any(r.get("discovery_fixtures",0)>0 and r.get("with_snapshots",0)>0 for r in month_rows) else "COVERAGE_AVAILABLE",
        "research_only":True,
    }
    out=Path(args.output)
    if not out.is_absolute(): out=Path.cwd()/out
    out.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))
    print(f"\nSaved coverage audit to: {out.resolve()}")

if __name__ == "__main__":
    asyncio.run(main())

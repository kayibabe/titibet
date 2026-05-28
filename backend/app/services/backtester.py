"""
backtester.py — Historical backtest service.

Replays dual engine against stored market_snapshots for historical fixtures.
Produces BacktestResult rows with per-market win/loss outcomes.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    BACKTEST_FLAT_STAKE,
    DISABLED_LEAGUES,
    DISABLED_MARKETS,
    MARKETS,
    OVER_GOALS_SUPPRESSED_LEAGUES,
    POISSON_RULES,
    UNDER_GOALS_SUPPRESSED_LEAGUES,
    WOMEN_LEAGUE_KEYWORDS,
    get_settings,
)
from app.engines import bayesian as bay_engine
from app.engines import poisson as poi_engine
from app.engines import dual_engine
from app.models import Fixture, MarketSnapshot, BacktestResult
from app.services.performance_intelligence import PerformanceWeights, compute_performance_weights
from app.services.signal_engine import (
    _CONFIDENCE_DOWNGRADE,
    _build_cs_by_bookie, _build_goals_ou, _build_btts,
    _build_match_winner, _build_double_chance, _build_poisson_odds,
    _build_home_totals, _build_away_totals,
    _build_win_to_nil_home, _build_win_to_nil_away, _build_exact_goals,
    MARKET_TO_POISSON_KEY, _get_underperforming_leagues, _latest_snapshots,
    _team_total_context_penalty,
    _is_end_of_northern_season, _OVER_GOALS_MARKETS,
)
from app.services.form_service import get_team_form_lambdas
from app.services.staking import kelly_stake_pct

settings = get_settings()


async def run_backtest(
    db: AsyncSession,
    market: Optional[str] = None,
    league_id: Optional[int] = None,
    league_name: Optional[str] = None,
    min_edge: float = 0.05,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    engine: str = "dual",    # "bayesian" / "poisson" / "dual"
    confidence_filter: Optional[str] = None,
) -> dict:
    """
    Run backtest. Clears existing results for the same scope, then writes new BacktestResult rows.
    Returns summary statistics.
    """
    query = select(Fixture)
    if date_from:
        query = query.where(Fixture.event_date >= date_from)
    if date_to:
        query = query.where(Fixture.event_date <= date_to)
    if league_id:
        query = query.where(Fixture.league_id == league_id)
    if league_name:
        query = query.where(Fixture.league.ilike(f"%{league_name}%"))

    # Only include finished fixtures
    query = query.where(Fixture.status.in_(["FT", "AET", "PEN"]))
    query = query.where(Fixture.home_score.isnot(None))

    fixture_result = await db.execute(query)
    fixtures: list[Fixture] = list(fixture_result.scalars().all())
    allowed_confidence = None
    if confidence_filter:
        allowed_confidence = {
            item.strip()
            for item in str(confidence_filter).split(",")
            if item.strip()
        }
    try:
        perf_weights: Optional[PerformanceWeights] = await compute_performance_weights(db)
    except Exception:
        perf_weights = None
    try:
        underperforming_leagues: frozenset[str] = await _get_underperforming_leagues(db, min_roi_pct=60.0)
    except Exception:
        underperforming_leagues = frozenset()
    all_suppressed_leagues = underperforming_leagues | DISABLED_LEAGUES

    # Clear old results for this scope
    del_q = delete(BacktestResult)
    if date_from:
        del_q = del_q.where(BacktestResult.fixture_date >= date_from)
    if date_to:
        del_q = del_q.where(BacktestResult.fixture_date <= date_to)
    if market:
        del_q = del_q.where(BacktestResult.market == market)
    await db.execute(del_q)
    await db.commit()

    results: list[BacktestResult] = []

    for fixture in fixtures:
        if fixture.home_score is None or fixture.away_score is None:
            continue
        if all_suppressed_leagues and (fixture.league or "").lower().strip() in all_suppressed_leagues:
            continue

        snap_result = await db.execute(
            select(MarketSnapshot).where(MarketSnapshot.fixture_id == fixture.id)
        )
        snapshots_raw: list[MarketSnapshot] = list(snap_result.scalars().all())
        if not snapshots_raw:
            continue
        snapshots = _latest_snapshots(snapshots_raw)

        cs_by_bookie = _build_cs_by_bookie(snapshots)
        goals_ou = _build_goals_ou(snapshots)
        btts_dict = _build_btts(snapshots)
        match_winner = _build_match_winner(snapshots)
        double_chance = _build_double_chance(snapshots)
        home_totals = _build_home_totals(snapshots)
        away_totals = _build_away_totals(snapshots)
        wtn_home = _build_win_to_nil_home(snapshots)
        wtn_away = _build_win_to_nil_away(snapshots)
        exact_goals = _build_exact_goals(snapshots)
        poi_odds, poi_signal_odds = _build_poisson_odds(snapshots)

        bay_result = bay_engine.analyse_fixture(
            fixture_id=fixture.id,
            home_team=fixture.home_team, away_team=fixture.away_team,
            league=fixture.league or "", country=fixture.country or "",
            cs_by_bookie=cs_by_bookie, goals_ou=goals_ou,
            btts=btts_dict, match_winner=match_winner,
            double_chance=double_chance,
            home_totals=home_totals,
            away_totals=away_totals,
            win_to_nil_home=wtn_home,
            win_to_nil_away=wtn_away,
            exact_goals=exact_goals,
            all_markets=True,
        ) if engine in ("bayesian", "dual") else None

        # Use the same form-lambda blending as the live signal engine so that
        # backtest ROI figures are representative of what the system actually bets.
        form_lambdas = None
        if engine in ("poisson", "dual"):
            form_lambdas = await get_team_form_lambdas(
                db=db,
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                before_date=fixture.event_date or date_from or date.today(),
            )

        poi_result = poi_engine.analyse_fixture(
            fixture_id=fixture.id, odds=poi_odds, signal_odds=poi_signal_odds,
            form_lambdas=form_lambdas or None,
        ) if engine in ("poisson", "dual") else None

        bay_by_market: dict[str, bay_engine.BayesianResult] = {}
        if bay_result:
            for mr in bay_result.market_results:
                bay_by_market[mr.market] = mr

        poi_by_key = {}
        if poi_result:
            poi_by_key = {r.rule_key: r for r in poi_result.results}
        poi_by_market = {}
        if poi_result:
            poi_by_market = {r.market: r for r in poi_result.results if r.rule_pass}

        all_markets = set(bay_by_market.keys()) | set(poi_by_market.keys())
        fixture_league = (fixture.league or "").strip()

        fixture_date = fixture.event_date or date_from or date.today()

        for mkt in all_markets:
            if mkt not in MARKETS:
                continue
            if market and mkt != market:
                continue
            if mkt in DISABLED_MARKETS:
                continue

            # Mirror new signal_engine gates so backtest ROI is representative
            if mkt in {"Home Over 1.5", "Away Over 1.5"} and (fixture.league_tier or 3) >= 3:
                continue

            if perf_weights is not None and perf_weights.should_suppress_league_market(fixture_league, mkt):
                continue

            condition = MARKETS[mkt]

            b = bay_by_market.get(mkt)
            if b and (not b.is_value or (b.edge or 0.0) < min_edge):
                b = None
            p_key = MARKET_TO_POISSON_KEY.get(mkt)
            p = poi_by_market.get(mkt)
            if p_key and p_key in poi_by_key:
                p = poi_by_key.get(p_key)

            if engine == "bayesian" and not b:
                continue
            if engine == "poisson" and (not p or not p.rule_pass):
                continue
            if engine == "dual" and not b and (not p or not p.rule_pass):
                continue

            ds = dual_engine.fuse(
                fixture_id=fixture.id, market=mkt,
                bayesian=b, poisson=p,
                mixed_signals=poi_result.mixed_signals if poi_result else [],
            )

            if mkt == "Under 2.5":
                under25_cap = float(POISSON_RULES.get("under25_max_odds", 2.20))
                best_u25_odd = b.best_actual_odd if b else None
                if best_u25_odd is not None and best_u25_odd > under25_cap:
                    continue

            if mkt == "Under 3.5":
                under35_cap = float(POISSON_RULES.get("under35_max_odds", 1.85))
                best_u35_odd = b.best_actual_odd if b else None
                if best_u35_odd is not None and best_u35_odd > under35_cap:
                    continue

            if mkt in ("Under 2.5", "Under 3.5"):
                league_lower = (fixture.league or "").lower()
                if any(k in league_lower for k in UNDER_GOALS_SUPPRESSED_LEAGUES):
                    continue

            over_markets = {
                "Over 0.5", "Over 1.5", "Over 2.5", "Over 3.5",
                "Home Over 0.5", "Home Over 1.5",
                "Away Over 0.5", "Away Over 1.5",
            }
            if mkt in over_markets:
                league_lower = (fixture.league or "").lower()
                if any(k in league_lower for k in OVER_GOALS_SUPPRESSED_LEAGUES):
                    continue

            # Women's league over-goals odds ceiling (mirror of signal_engine gate)
            if mkt in {"Away Over 0.5", "Away Over 1.5", "Home Over 0.5", "Home Over 1.5"}:
                league_lower = (fixture.league or "").lower()
                if any(kw in league_lower for kw in WOMEN_LEAGUE_KEYWORDS):
                    _wo = b.best_actual_odd if b else None
                    _women_ceil = 2.30 if mkt.startswith("Away") else 2.50
                    if _wo is not None and _wo > _women_ceil:
                        continue

            # Tier 3 away-over high-odds ceiling (mirror of signal_engine gate)
            if mkt == "Away Over 0.5" and (fixture.league_tier or 3) >= 3:
                _wo = b.best_actual_odd if b else None
                if _wo is not None and _wo > 2.50:
                    continue

            final_confidence = ds.confidence
            if (
                perf_weights is not None
                and ds.confidence not in ("None", "Low")
                and perf_weights.confidence_needs_downgrade(mkt, fixture.league_tier)
            ):
                final_confidence = _CONFIDENCE_DOWNGRADE.get(ds.confidence, ds.confidence)

            _team_penalty, severe_team_total_flag = _team_total_context_penalty(
                market=mkt,
                league_tier=fixture.league_tier,
                form_lambdas=form_lambdas or None,
                best_odd=b.best_actual_odd if b else None,
                bookmaker_count=b.bookmaker_count if b else None,
            )
            if severe_team_total_flag and final_confidence in ("High", "Medium"):
                final_confidence = _CONFIDENCE_DOWNGRADE.get(final_confidence, final_confidence)

            if final_confidence == "Low" and perf_weights is not None:
                lm_factor = perf_weights.factor_for_league_market(fixture_league, mkt)
                if lm_factor < 0.85:
                    continue

            if allowed_confidence and final_confidence not in allowed_confidence:
                continue
            if final_confidence == "None":
                continue

            # Determine bet outcome
            won = condition(fixture.home_score, fixture.away_score)
            best_odd = b.best_actual_odd if b else 0.0
            flat_stake = BACKTEST_FLAT_STAKE
            ks = kelly_stake_pct(b.derived_prob, best_odd) * 100 if b and best_odd > 1 else 0.0

            profit = flat_stake * (best_odd - 1.0) if won else -flat_stake

            result = BacktestResult(
                fixture_id=fixture.id,
                fixture_date=fixture.event_date,
                league_id=fixture.league_id,
                league_name=fixture.league,
                league_tier=fixture.league_tier,
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                home_score=fixture.home_score,
                away_score=fixture.away_score,
                market=mkt,
                source_engine=engine,
                derived_prob=b.derived_prob if b else None,
                actual_odd=best_odd if best_odd > 1 else None,
                edge=b.edge if b else None,
                dual_confidence=final_confidence,
                bet_result=1 if won else 0,
                profit_loss=round(profit, 2),
                flat_stake=flat_stake,
                kelly_stake=round(ks, 2) if ks else None,
            )
            results.append(result)
            db.add(result)

    await db.commit()

    # Compute summary
    return _summarise(results)


def _summarise(results: list[BacktestResult]) -> dict:
    if not results:
        return {
            "total": 0,
            "total_bets": 0,
            "wins": 0,
            "losses": 0,
            "hit_rate": 0.0,
            "roi": 0.0,
            "total_profit": 0.0,
            "total_stake": 0.0,
            "avg_odds": None,
            "by_market": [],
            "bankroll_curve": [],
        }

    total = len(results)
    wins = sum(1 for r in results if r.bet_result == 1)
    losses = total - wins
    total_profit = sum(r.profit_loss for r in results)
    total_stake = total * BACKTEST_FLAT_STAKE
    roi = (total_profit / total_stake * 100) if total_stake else 0.0
    hit_rate = (wins / total * 100) if total else 0.0
    odds_values = [r.actual_odd for r in results if r.actual_odd]
    avg_odds = (sum(odds_values) / len(odds_values)) if odds_values else None

    by_market: dict[str, dict] = {}
    for r in results:
        m = r.market
        if m not in by_market:
            by_market[m] = {"total": 0, "wins": 0, "profit": 0.0, "odds": []}
        by_market[m]["total"] += 1
        by_market[m]["wins"] += r.bet_result or 0
        by_market[m]["profit"] += r.profit_loss
        if r.actual_odd:
            by_market[m]["odds"].append(r.actual_odd)

    market_stats = []
    for m, d in sorted(by_market.items()):
        t = d["total"]
        w = d["wins"]
        p = d["profit"]
        s = t * BACKTEST_FLAT_STAKE
        market_odds = d["odds"]
        market_stats.append({
            "market": m, "total": t, "count": t, "wins": w, "losses": t - w,
            "hit_rate": round(w / t * 100, 1) if t else 0.0,
            "roi": round(p / s * 100, 1) if s else 0.0,
            "profit": round(p, 2),
            "avg_odds": round(sum(market_odds) / len(market_odds), 2) if market_odds else None,
        })

    # Bankroll curve (sorted by date)
    bankroll = 100.0
    curve = []
    for r in sorted(results, key=lambda x: (x.fixture_date or date.min, x.id)):
        bankroll += r.profit_loss
        curve.append({
            "date": r.fixture_date.isoformat() if r.fixture_date else None,
            "bankroll": round(bankroll, 2),
            "won": r.bet_result == 1,
        })

    return {
        "total": total,
        "total_bets": total,
        "wins": wins,
        "losses": losses,
        "hit_rate": round(hit_rate, 1),
        "roi": round(roi, 1),
        "total_profit": round(total_profit, 2),
        "total_stake": round(total_stake, 2),
        "avg_odds": round(avg_odds, 2) if avg_odds is not None else None,
        "by_market": market_stats,
        "bankroll_curve": curve,
    }

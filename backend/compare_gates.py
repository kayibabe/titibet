"""
compare_gates.py — Before/after gate comparison with per-gate attribution.
Usage:  cd backend && python compare_gates.py
"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from app.core.database import AsyncSessionLocal
from app.services.backtester import run_backtest
from app.core.config import POISSON_RULES
from app.services.signal_engine import _is_end_of_northern_season, _OVER_GOALS_MARKETS, _CONFIDENCE_DOWNGRADE
from app.engines import dual_engine
from app.services.backtester import (
    _build_cs_by_bookie, _build_goals_ou, _build_match_winner,
    _build_double_chance, _build_home_totals, _build_away_totals,
    _build_win_to_nil_home, _build_win_to_nil_away, _build_exact_goals,
    _build_poisson_odds, _latest_snapshots, MARKET_TO_POISSON_KEY,
    _get_underperforming_leagues, _team_total_context_penalty,
)
from app.core.config import (
    MARKETS, DISABLED_MARKETS, DISABLED_LEAGUES, BACKTEST_FLAT_STAKE,
    MARKET_MAX_ODDS, POISSON_ONLY_MAX_ODDS, UNDER_GOALS_SUPPRESSED_LEAGUES,
    OVER_GOALS_SUPPRESSED_LEAGUES, YOUTH_LEAGUE_KEYWORDS, WOMEN_LEAGUE_KEYWORDS,
    exec_odd_from, get_settings,
)
from app.engines import bayesian as bay_engine, poisson as poi_engine
from app.models import Fixture, MarketSnapshot
from app.services.performance_intelligence import PerformanceWeights, compute_performance_weights
from app.services.form_service import get_team_form_lambdas
from app.services.staking import kelly_stake_pct
from sqlalchemy import select
from datetime import date


async def _count_signals(db, gate_ev: bool, gate_eos: bool) -> dict:
    """Run backtester logic in-process with selective gate toggles."""
    query = (
        select(Fixture)
        .where(Fixture.status.in_(["FT", "AET", "PEN"]))
        .where(Fixture.home_score.isnot(None))
    )
    fixtures = list((await db.execute(query)).scalars().all())

    try:
        perf_weights = await compute_performance_weights(db)
    except Exception:
        perf_weights = None
    try:
        underperforming = await _get_underperforming_leagues(db, min_roi_pct=60.0)
    except Exception:
        underperforming = frozenset()
    all_suppressed = underperforming | DISABLED_LEAGUES

    total = wins = 0
    by_mkt: dict[str, dict] = {}

    for fixture in fixtures:
        if fixture.home_score is None or fixture.away_score is None:
            continue
        if (fixture.league or "").lower().strip() in all_suppressed:
            continue
        if any(kw in (fixture.league or "").lower() for kw in YOUTH_LEAGUE_KEYWORDS):
            continue

        snaps_raw = list((await db.execute(
            select(MarketSnapshot).where(MarketSnapshot.fixture_id == fixture.id)
        )).scalars().all())
        if not snaps_raw:
            continue
        snaps = _latest_snapshots(snaps_raw)

        cs_by_bookie = _build_cs_by_bookie(snaps)
        goals_ou     = _build_goals_ou(snaps)
        match_winner = _build_match_winner(snaps)
        double_chance = _build_double_chance(snaps)
        home_totals  = _build_home_totals(snaps)
        away_totals  = _build_away_totals(snaps)
        wtn_home     = _build_win_to_nil_home(snaps)
        wtn_away     = _build_win_to_nil_away(snaps)
        exact_goals  = _build_exact_goals(snaps)
        poi_odds, poi_signal_odds = _build_poisson_odds(snaps)

        bay_result = bay_engine.analyse_fixture(
            fixture_id=fixture.id,
            home_team=fixture.home_team, away_team=fixture.away_team,
            league=fixture.league or "", country=fixture.country or "",
            cs_by_bookie=cs_by_bookie, goals_ou=goals_ou,
            btts={}, match_winner=match_winner, double_chance=double_chance,
            home_totals=home_totals, away_totals=away_totals,
            win_to_nil_home=wtn_home, win_to_nil_away=wtn_away,
            exact_goals=exact_goals, all_markets=True,
        )

        form_lambdas = await get_team_form_lambdas(
            db=db, home_team=fixture.home_team, away_team=fixture.away_team,
            before_date=fixture.event_date or date.today(),
        )

        poi_result = poi_engine.analyse_fixture(
            fixture_id=fixture.id, odds=poi_odds,
            signal_odds=poi_signal_odds, form_lambdas=form_lambdas or None,
        )

        bay_by_market = {mr.market: mr for mr in (bay_result.market_results if bay_result else [])}
        poi_by_key = {r.rule_key: r for r in poi_result.results}
        poi_by_market = {r.market: r for r in poi_result.results if r.rule_pass}

        all_markets = set(bay_by_market) | set(poi_by_market)
        fixture_league = (fixture.league or "").strip()
        fixture_date = fixture.event_date or date.today()

        for mkt in all_markets:
            if mkt not in MARKETS or mkt in DISABLED_MARKETS:
                continue
            if perf_weights and perf_weights.should_suppress_league_market(fixture_league, mkt):
                continue

            condition = MARKETS[mkt]
            b = bay_by_market.get(mkt)
            if b and (not b.is_value or (b.edge or 0.0) < 0.05):
                b = None
            p_key = MARKET_TO_POISSON_KEY.get(mkt)
            p = poi_by_market.get(mkt)
            if p_key and p_key in poi_by_key:
                p = poi_by_key.get(p_key)

            if not b and (not p or not p.rule_pass):
                continue

            if mkt == "Under 2.5":
                best_u25 = b.best_actual_odd if b else None
                cap = float(POISSON_RULES.get("under25_max_odds", 2.20))
                if best_u25 is not None and best_u25 > cap:
                    continue
                if any(k in (fixture.league or "").lower() for k in UNDER_GOALS_SUPPRESSED_LEAGUES):
                    continue

            if mkt in {"Over 1.5", "Over 2.5", "Home Over 0.5"}:
                if any(k in (fixture.league or "").lower() for k in OVER_GOALS_SUPPRESSED_LEAGUES):
                    continue

            _max_odd = MARKET_MAX_ODDS.get(mkt)
            if _max_odd and b and b.best_actual_odd and b.best_actual_odd > _max_odd:
                continue

            ds = dual_engine.fuse(
                fixture_id=fixture.id, market=mkt, bayesian=b, poisson=p,
                mixed_signals=poi_result.mixed_signals,
            )

            final_confidence = ds.confidence
            if perf_weights and ds.confidence not in ("None", "Low"):
                if perf_weights.confidence_needs_downgrade(mkt, fixture.league_tier):
                    final_confidence = _CONFIDENCE_DOWNGRADE.get(ds.confidence, ds.confidence)

            _tp, severe = _team_total_context_penalty(
                market=mkt, league_tier=fixture.league_tier,
                form_lambdas=form_lambdas or None,
                best_odd=b.best_actual_odd if b else None,
                bookmaker_count=b.bookmaker_count if b else None,
            )
            if severe and final_confidence in ("High", "Medium"):
                final_confidence = _CONFIDENCE_DOWNGRADE.get(final_confidence, final_confidence)

            if final_confidence == "Low" and perf_weights:
                if perf_weights.factor_for_league_market(fixture_league, mkt) < 0.85:
                    continue
            if final_confidence == "Low" and (fixture.league_tier or 3) >= 3:
                continue
            if final_confidence == "None":
                continue

            _poi_bt_odd = float(poi_signal_odds.get(p_key or "") or 0.0)
            _poi_bt_max = POISSON_ONLY_MAX_ODDS.get(mkt)
            is_dual_bt = final_confidence == "High" and ds.agreement == "Both"
            is_poisson_bt = (
                mkt == "Home Over 0.5"
                and ds.agreement == "Poisson Only"
                and p is not None and getattr(p, "rule_strong", False)
                and _poi_bt_odd > 1.0
                and (_poi_bt_max is None or _poi_bt_odd < _poi_bt_max)
            )
            if not is_dual_bt and not is_poisson_bt:
                continue

            # ── Gate 1: ev_score ─────────────────────────────────────────────
            if gate_ev and b is not None and not is_poisson_bt:
                _st = float(POISSON_RULES.get("prob_shrink_threshold", 0.75))
                _sf = float(POISSON_RULES.get("prob_shrink_factor", 0.88))
                _shi = float(POISSON_RULES.get("prob_shrink_threshold_hi", 0.80))
                _sfi = float(POISSON_RULES.get("prob_shrink_factor_hi", 0.35))
                _rp = b.derived_prob
                if _rp and _rp > _st:
                    _rp = _st + (_rp - _st) * _sf
                    if _rp > _shi:
                        _rp = _shi + (_rp - _shi) * _sfi
                _ex = exec_odd_from(b.best_actual_odd, mkt) if b.best_actual_odd else 0.0
                if _rp and _ex > 1.0 and (_rp * _ex - 1.0) < 0:
                    continue

            # ── Gate 2: end-of-season ────────────────────────────────────────
            if gate_eos and _is_end_of_northern_season(fixture_date):
                _tier = fixture.league_tier or 3
                if _tier >= 2 and mkt in _OVER_GOALS_MARKETS:
                    continue
                if _tier >= 3 and final_confidence in ("High", "Medium"):
                    final_confidence = _CONFIDENCE_DOWNGRADE.get(final_confidence, final_confidence)
                    is_dual_bt = final_confidence == "High" and ds.agreement == "Both"
                    if not is_dual_bt and not is_poisson_bt:
                        continue

            # ── Outcome ──────────────────────────────────────────────────────
            won = condition(fixture.home_score, fixture.away_score)
            best_odd = (b.best_actual_odd if b else 0.0) or float(poi_signal_odds.get(p_key or "") or 0.0)
            if best_odd <= 1:
                continue
            settle_odd = exec_odd_from(best_odd, mkt)
            if settle_odd <= 1:
                continue

            total += 1
            wins += int(won)
            profit = BACKTEST_FLAT_STAKE * (settle_odd - 1.0) if won else -BACKTEST_FLAT_STAKE
            if mkt not in by_mkt:
                by_mkt[mkt] = {"n": 0, "wins": 0, "profit": 0.0}
            by_mkt[mkt]["n"] += 1
            by_mkt[mkt]["wins"] += int(won)
            by_mkt[mkt]["profit"] += profit

    total_stake = total * BACKTEST_FLAT_STAKE
    return {
        "n": total, "wins": wins,
        "hit_rate": round(wins / total * 100, 1) if total else 0.0,
        "roi": round((sum(by_mkt[m]["profit"] for m in by_mkt) / total_stake * 100) if total_stake else 0.0, 1),
        "by_mkt": {m: {
            "n": d["n"], "wins": d["wins"],
            "hit_rate": round(d["wins"] / d["n"] * 100, 1) if d["n"] else 0.0,
            "roi": round(d["profit"] / (d["n"] * BACKTEST_FLAT_STAKE) * 100, 1) if d["n"] else 0.0,
        } for m, d in by_mkt.items()},
    }


async def _audit_all_markets(db) -> list[dict]:
    """
    For every signal that reaches dual_engine.fuse() (before the is_dual_bt gate),
    record market × (confidence, agreement) tier with outcome stats.
    Returns rows sorted by market, then confidence desc, then agreement.
    """
    query = (
        select(Fixture)
        .where(Fixture.status.in_(["FT", "AET", "PEN"]))
        .where(Fixture.home_score.isnot(None))
    )
    fixtures = list((await db.execute(query)).scalars().all())

    try:
        perf_weights = await compute_performance_weights(db)
    except Exception:
        perf_weights = None
    try:
        underperforming = await _get_underperforming_leagues(db, min_roi_pct=60.0)
    except Exception:
        underperforming = frozenset()
    all_suppressed = underperforming | DISABLED_LEAGUES

    # key: (market, confidence, agreement) -> {n, wins, profit}
    buckets: dict[tuple, dict] = {}

    for fixture in fixtures:
        if fixture.home_score is None or fixture.away_score is None:
            continue
        if (fixture.league or "").lower().strip() in all_suppressed:
            continue
        if any(kw in (fixture.league or "").lower() for kw in YOUTH_LEAGUE_KEYWORDS):
            continue

        snaps_raw = list((await db.execute(
            select(MarketSnapshot).where(MarketSnapshot.fixture_id == fixture.id)
        )).scalars().all())
        if not snaps_raw:
            continue
        snaps = _latest_snapshots(snaps_raw)

        cs_by_bookie = _build_cs_by_bookie(snaps)
        goals_ou     = _build_goals_ou(snaps)
        match_winner = _build_match_winner(snaps)
        double_chance = _build_double_chance(snaps)
        home_totals  = _build_home_totals(snaps)
        away_totals  = _build_away_totals(snaps)
        wtn_home     = _build_win_to_nil_home(snaps)
        wtn_away     = _build_win_to_nil_away(snaps)
        exact_goals  = _build_exact_goals(snaps)
        poi_odds, poi_signal_odds = _build_poisson_odds(snaps)

        bay_result = bay_engine.analyse_fixture(
            fixture_id=fixture.id,
            home_team=fixture.home_team, away_team=fixture.away_team,
            league=fixture.league or "", country=fixture.country or "",
            cs_by_bookie=cs_by_bookie, goals_ou=goals_ou,
            btts={}, match_winner=match_winner, double_chance=double_chance,
            home_totals=home_totals, away_totals=away_totals,
            win_to_nil_home=wtn_home, win_to_nil_away=wtn_away,
            exact_goals=exact_goals, all_markets=True,
        )

        form_lambdas = await get_team_form_lambdas(
            db=db, home_team=fixture.home_team, away_team=fixture.away_team,
            before_date=fixture.event_date or date.today(),
        )

        poi_result = poi_engine.analyse_fixture(
            fixture_id=fixture.id, odds=poi_odds,
            signal_odds=poi_signal_odds, form_lambdas=form_lambdas or None,
        )

        bay_by_market = {mr.market: mr for mr in (bay_result.market_results if bay_result else [])}
        poi_by_key = {r.rule_key: r for r in poi_result.results}
        poi_by_market = {r.market: r for r in poi_result.results if r.rule_pass}

        all_markets = set(bay_by_market) | set(poi_by_market)
        fixture_league = (fixture.league or "").strip()
        fixture_date = fixture.event_date or date.today()

        for mkt in all_markets:
            if mkt not in MARKETS or mkt in DISABLED_MARKETS:
                continue
            if perf_weights and perf_weights.should_suppress_league_market(fixture_league, mkt):
                continue

            condition = MARKETS[mkt]
            b = bay_by_market.get(mkt)
            if b and (not b.is_value or (b.edge or 0.0) < 0.05):
                b = None
            p_key = MARKET_TO_POISSON_KEY.get(mkt)
            p = poi_by_market.get(mkt)
            if p_key and p_key in poi_by_key:
                p = poi_by_key.get(p_key)

            if not b and (not p or not p.rule_pass):
                continue

            if mkt == "Under 2.5":
                best_u25 = b.best_actual_odd if b else None
                cap = float(POISSON_RULES.get("under25_max_odds", 2.20))
                if best_u25 is not None and best_u25 > cap:
                    continue
                if any(k in (fixture.league or "").lower() for k in UNDER_GOALS_SUPPRESSED_LEAGUES):
                    continue

            if mkt in {"Over 1.5", "Over 2.5", "Home Over 0.5"}:
                if any(k in (fixture.league or "").lower() for k in OVER_GOALS_SUPPRESSED_LEAGUES):
                    continue

            _max_odd = MARKET_MAX_ODDS.get(mkt)
            if _max_odd and b and b.best_actual_odd and b.best_actual_odd > _max_odd:
                continue

            ds = dual_engine.fuse(
                fixture_id=fixture.id, market=mkt, bayesian=b, poisson=p,
                mixed_signals=poi_result.mixed_signals,
            )

            final_confidence = ds.confidence
            if perf_weights and ds.confidence not in ("None", "Low"):
                if perf_weights.confidence_needs_downgrade(mkt, fixture.league_tier):
                    final_confidence = _CONFIDENCE_DOWNGRADE.get(ds.confidence, ds.confidence)

            _tp, severe = _team_total_context_penalty(
                market=mkt, league_tier=fixture.league_tier,
                form_lambdas=form_lambdas or None,
                best_odd=b.best_actual_odd if b else None,
                bookmaker_count=b.bookmaker_count if b else None,
            )
            if severe and final_confidence in ("High", "Medium"):
                final_confidence = _CONFIDENCE_DOWNGRADE.get(final_confidence, final_confidence)

            if final_confidence in ("None",):
                continue

            won = condition(fixture.home_score, fixture.away_score)
            best_odd = (b.best_actual_odd if b else 0.0) or float(poi_signal_odds.get(p_key or "") or 0.0)
            if best_odd <= 1:
                continue
            settle_odd = exec_odd_from(best_odd, mkt)
            if settle_odd <= 1:
                continue

            key = (mkt, final_confidence, ds.agreement)
            if key not in buckets:
                buckets[key] = {"n": 0, "wins": 0, "profit": 0.0}
            buckets[key]["n"] += 1
            buckets[key]["wins"] += int(won)
            buckets[key]["profit"] += BACKTEST_FLAT_STAKE * (settle_odd - 1.0) if won else -BACKTEST_FLAT_STAKE

    rows = []
    conf_order = {"High": 0, "Medium": 1, "Low": 2}
    agr_order  = {"Both": 0, "Bayesian Only": 1, "Poisson Only": 2, "Contradiction": 3}
    for (mkt, conf, agr), d in sorted(
        buckets.items(),
        key=lambda x: (x[0][0], conf_order.get(x[0][1], 9), agr_order.get(x[0][2], 9)),
    ):
        n = d["n"]
        stake_total = n * BACKTEST_FLAT_STAKE
        rows.append({
            "market": mkt,
            "confidence": conf,
            "agreement": agr,
            "n": n,
            "wins": d["wins"],
            "hit_rate": round(d["wins"] / n * 100, 1) if n else 0.0,
            "roi": round(d["profit"] / stake_total * 100, 1) if stake_total else 0.0,
            "is_dual": conf == "High" and agr == "Both",
        })
    return rows


async def main():
    async with AsyncSessionLocal() as db:
        print("Baseline (no new gates)...")
        r_none = await _count_signals(db, gate_ev=False, gate_eos=False)
        print("EV gate only...")
        r_ev   = await _count_signals(db, gate_ev=True,  gate_eos=False)
        print("End-of-season gate only...")
        r_eos  = await _count_signals(db, gate_ev=False, gate_eos=True)
        print("Both gates (final)...")
        r_both = await _count_signals(db, gate_ev=True,  gate_eos=True)
        print("Market audit (all tiers)...")
        audit  = await _audit_all_markets(db)

    results = {
        "baseline": r_none,
        "ev_only":  r_ev,
        "eos_only": r_eos,
        "both":     r_both,
    }
    print(json.dumps(results, indent=2))

    # Print audit table
    print("\n" + "="*90)
    print(f"{'MARKET':<28} {'CONF':<8} {'AGREEMENT':<16} {'N':>5} {'HR%':>6} {'ROI%':>7}  DUAL?")
    print("="*90)
    for r in audit:
        dual_marker = " <-- QUALIFIES" if r["is_dual"] else ""
        print(
            f"{r['market']:<28} {r['confidence']:<8} {r['agreement']:<16}"
            f" {r['n']:>5} {r['hit_rate']:>6.1f} {r['roi']:>7.1f}{dual_marker}"
        )


if __name__ == "__main__":
    asyncio.run(main())

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
    AWAY_GOALS_SUPPRESSED_LEAGUES,
    BACKTEST_FLAT_STAKE,
    DISABLED_LEAGUES,
    DISABLED_MARKETS,
    MARKET_MAX_ODDS,
    MARKETS,
    OVER_GOALS_SUPPRESSED_LEAGUES,
    POISSON_ONLY_MAX_ODDS,
    POISSON_RULES,
    UNDER_GOALS_SUPPRESSED_LEAGUES,
    WOMEN_LEAGUE_KEYWORDS,
    YOUTH_LEAGUE_KEYWORDS,
    exec_odd_from,
    get_settings,
)
from app.engines import bayesian as bay_engine
from app.engines import poisson as poi_engine
from app.engines import dual_engine
from app.models import Fixture, MarketSnapshot, BacktestResult
from app.services.performance_intelligence import PerformanceWeights, compute_performance_weights
from app.services.signal_engine import (
    _CONFIDENCE_DOWNGRADE,
    _build_cs_by_bookie, _build_goals_ou,
    _build_match_winner, _build_double_chance, _build_poisson_odds,
    _build_home_totals, _build_away_totals,
    _build_win_to_nil_home, _build_win_to_nil_away, _build_exact_goals,
    MARKET_TO_POISSON_KEY, _get_underperforming_leagues, _latest_snapshots,
    _team_total_context_penalty,
    _is_end_of_northern_season, _OVER_GOALS_MARKETS,
)
from app.services.form_service import get_team_form_lambdas

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
    settle_at_exec: bool = True,
    apply_new_gates: bool = True,
) -> dict:
    """
    Run backtest. Clears existing results for the same scope, then writes new BacktestResult rows.
    Returns summary statistics.

    settle_at_exec (default True): book P&L and Kelly at the realistic EXECUTION
    price — the proxy odd haircut down to what the user actually gets at their
    book (betPawa / 888bets / Betway) — instead of the longer displayed proxy odd.
    This makes ROI reflect reality rather than the inflated sharp/WH line.
    Pass False to reproduce the legacy proxy-price ROI for comparison.
    The haircut comes from config (exec_odds_haircut / EXEC_HAIRCUT_BY_MARKET);
    with a 0% haircut exec settlement is identical to proxy settlement.
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

    # Only include finished fixtures that have at least one market snapshot.
    # Fixtures backfilled without odds (no market_snapshots rows) are useless
    # for signal replay and were causing O(N) empty snapshot queries that pushed
    # 6-month backtests past the 60-second Fly.io proxy timeout.
    query = query.where(Fixture.status.in_(["FT", "AET", "PEN"]))
    query = query.where(Fixture.home_score.isnot(None))
    query = query.where(
        Fixture.id.in_(select(MarketSnapshot.fixture_id).distinct())
    )

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
        underperforming_leagues: frozenset[str] = await _get_underperforming_leagues(db, min_roi_pct=-20.0)
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
        _bt_league_lower = (fixture.league or "").lower().strip()
        if all_suppressed_leagues and (
            _bt_league_lower in all_suppressed_leagues
            or "friendlies" in _bt_league_lower
        ):
            continue

        _league_lower_bt = (fixture.league or "").lower()
        if any(kw in _league_lower_bt for kw in YOUTH_LEAGUE_KEYWORDS):
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
        btts_dict = {}
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
            # is_value is now the probability floor only; the min_edge parameter
            # is retained for API compatibility but no longer filters (EV gating
            # retired 2026-07-02).
            if b and not b.is_value:
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

            if mkt == "Under 2.5":
                league_lower = (fixture.league or "").lower()
                if any(k in league_lower for k in UNDER_GOALS_SUPPRESSED_LEAGUES):
                    continue

            over_markets = {"Over 1.5", "Over 2.5", "Home Over 0.5"}
            if mkt in over_markets:
                league_lower = (fixture.league or "").lower()
                if any(k in league_lower for k in OVER_GOALS_SUPPRESSED_LEAGUES):
                    continue

            # Women's league over-goals odds ceiling (mirror of signal_engine gate)
            if mkt == "Home Over 0.5":
                league_lower = (fixture.league or "").lower()
                if any(kw in league_lower for kw in WOMEN_LEAGUE_KEYWORDS):
                    _wo = b.best_actual_odd if b else None
                    if _wo is not None and _wo > 2.50:
                        continue

            # Market maximum odds cap (mirror of signal_engine gate)
            _max_odd = MARKET_MAX_ODDS.get(mkt)
            if _max_odd:
                _best_for_cap = b.best_actual_odd if b else None
                if _best_for_cap is not None and _best_for_cap > _max_odd:
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

            # Low confidence in Tier 3 = near-zero edge in highest-variance context
            if final_confidence == "Low" and (fixture.league_tier or 3) >= 3:
                continue

            if allowed_confidence and final_confidence not in allowed_confidence:
                continue
            if final_confidence == "None":
                continue

            # Mirror live signal_engine tier gates.
            # Tier 1: Dual Signal — Both agreement + High confidence.
            # Tier 2: Poisson Signal — Poisson-only + rule_strong + odds < POISSON_ONLY_MAX_ODDS.
            _poi_bt_odd = float(poi_signal_odds.get(p_key or "") or 0.0)
            _poi_bt_max = POISSON_ONLY_MAX_ODDS.get(mkt)
            is_dual_bt   = final_confidence == "High" and ds.agreement == "Both"
            is_poisson_bt = (
                mkt == "Home Over 0.5"
                and ds.agreement == "Poisson Only"
                and p is not None and getattr(p, "rule_strong", False)
                and _poi_bt_odd > 1.0
                and (_poi_bt_max is None or _poi_bt_odd < _poi_bt_max)
            )
            if not is_dual_bt and not is_poisson_bt:
                continue

            # ── New gates (mirroring signal_engine) ──────────────────────────
            # (The negative-EV hard gate was removed 2026-07-02, mirroring the
            # live signal engine — EV never rejects a bet.)
            if apply_new_gates:
                # Gate 2: end-of-northern-season suppression.
                # Tier 2+ Over-goals signals dropped May 10 – June 30.
                # Tier 3 remaining signals confidence-downgraded.
                if _is_end_of_northern_season(fixture_date):
                    _bt_tier = fixture.league_tier or 3
                    if _bt_tier >= 2 and mkt in _OVER_GOALS_MARKETS:
                        continue
                    if _bt_tier >= 3 and final_confidence in ("High", "Medium"):
                        final_confidence = _CONFIDENCE_DOWNGRADE.get(final_confidence, final_confidence)
                        is_dual_bt = final_confidence == "High" and ds.agreement == "Both"
                        if not is_dual_bt and not is_poisson_bt:
                            continue

            # Determine bet outcome
            won = condition(fixture.home_score, fixture.away_score)
            best_odd = b.best_actual_odd if b else 0.0
            # For Poisson-only signals (b is None or b has no Bayesian odds),
            # fall back to the actual bookmaker odds captured in poi_signal_odds.
            # Without this, every Poisson-only win still books as a loss because
            # profit = stake * (0 - 1) = -stake.
            if best_odd <= 1 and p_key:
                best_odd = float(poi_signal_odds.get(p_key) or 0.0)
            if best_odd <= 1:
                # No valid bookmaker odds available — skip this signal from P&L
                continue
            # best_odd is the displayed PROXY price. Settle at the EXECUTION price
            # (proxy haircut to the user's real book) so ROI is honest. The
            # odds-cap gates above deliberately still use the observed proxy price.
            settle_odd = exec_odd_from(best_odd, mkt) if settle_at_exec else best_odd
            if settle_odd <= 1:
                continue
            flat_stake = BACKTEST_FLAT_STAKE
            # Probability-scaled flat stake (Kelly retired with EV gating).
            ks = settings.max_kelly_pct * b.derived_prob * 100 if b and b.derived_prob else 0.0

            profit = flat_stake * (settle_odd - 1.0) if won else -flat_stake

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
                # Record the settlement odd actually used for P&L (exec price when
                # settle_at_exec) so summary ROI / avg_odds reflect real returns.
                actual_odd=round(settle_odd, 3) if settle_odd > 1 else None,
                edge=b.edge if b else None,
                dual_confidence=final_confidence,
                dual_agreement=ds.agreement,
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


# ── Correct Score backtest ────────────────────────────────────────────────────
#
# CS picks are EV-gated, not confidence-gated, so they don't flow through
# run_backtest. Data collection is split from parameter evaluation so the
# calibration CLI can sweep (min_ev × odds_ceiling × rho) in memory without
# re-reading snapshots per combination.

from dataclasses import dataclass as _dataclass

from app.engines import correct_score as cs_engine


@_dataclass
class CSSample:
    """Everything needed to replay CS picks for one finished fixture."""
    fixture_id: int
    event_date: date
    league: str
    lambda_h: float
    lambda_a: float
    cs_odds: dict[tuple[int, int], cs_engine.CSOdds]
    home_score: int
    away_score: int


async def collect_cs_backtest_data(
    db: AsyncSession,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[CSSample]:
    """
    Load per-fixture CS backtest samples: blended lambdas (same CS-ratio +
    form blend the live engine uses, form window strictly before the fixture
    date — no lookahead) plus the full CS board and the final score.

    FT fixtures only — CS settles on the 90-minute score and stored scores
    for AET/PEN fixtures may include extra time.
    """
    query = select(Fixture).where(
        Fixture.status == "FT",
        Fixture.home_score.isnot(None),
        Fixture.away_score.isnot(None),
        Fixture.id.in_(select(MarketSnapshot.fixture_id).distinct()),
    )
    if date_from:
        query = query.where(Fixture.event_date >= date_from)
    if date_to:
        query = query.where(Fixture.event_date <= date_to)
    fixtures: list[Fixture] = list((await db.execute(query)).scalars().all())

    fw = poi_engine._form_lambda_weight()
    samples: list[CSSample] = []

    for fixture in fixtures:
        league_lower = (fixture.league or "").lower().strip()
        if league_lower in DISABLED_LEAGUES:
            continue
        if any(kw in league_lower for kw in YOUTH_LEAGUE_KEYWORDS):
            continue

        snap_result = await db.execute(
            select(MarketSnapshot).where(MarketSnapshot.fixture_id == fixture.id)
        )
        snapshots = _latest_snapshots(list(snap_result.scalars().all()))
        if not snapshots:
            continue

        cs_by_bookie = _build_cs_by_bookie(snapshots)
        if not cs_by_bookie:
            continue
        poi_odds, _ = _build_poisson_odds(snapshots)
        d = poi_engine.derive_lambdas(poi_odds.get("s00"), poi_odds.get("s10"), poi_odds.get("s01"))
        if not d:
            continue

        form_lambdas = await get_team_form_lambdas(
            db=db,
            home_team=fixture.home_team,
            away_team=fixture.away_team,
            before_date=fixture.event_date or date.today(),
        )
        lam_h = poi_engine._blend_lam(d["lambda_h"], form_lambdas.get("lambda_h") if form_lambdas else None, fw)
        lam_a = poi_engine._blend_lam(d["lambda_a"], form_lambdas.get("lambda_a") if form_lambdas else None, fw)
        if not lam_h or not lam_a or lam_h <= 0 or lam_a <= 0:
            continue

        cs_odds = cs_engine.collect_cs_odds(cs_by_bookie)
        if not cs_odds:
            continue

        samples.append(CSSample(
            fixture_id=fixture.id,
            event_date=fixture.event_date or date.today(),
            league=fixture.league or "",
            lambda_h=lam_h,
            lambda_a=lam_a,
            cs_odds=cs_odds,
            home_score=fixture.home_score,
            away_score=fixture.away_score,
        ))

    samples.sort(key=lambda s: (s.event_date, s.fixture_id))
    return samples


def evaluate_cs_params(
    samples: list[CSSample],
    *,
    min_ev: float,
    odds_ceiling: float,
    rho: float,
    min_bookmakers: int = 2,
    min_model_prob: float = 0.06,
    flat_stake: float = BACKTEST_FLAT_STAKE,
    matrices: Optional[dict[int, list[list[float]]]] = None,
) -> dict:
    """
    Replay one parameter combination over pre-collected samples.
    `matrices` optionally caches score matrices keyed by fixture_id for this rho.
    """
    picks = []
    for s in samples:
        matrix = matrices.get(s.fixture_id) if matrices is not None else None
        if matrix is None:
            matrix = cs_engine.score_matrix(s.lambda_h, s.lambda_a, rho=rho)
            if matrix is None:
                continue
            if matrices is not None:
                matrices[s.fixture_id] = matrix
        pick = cs_engine.best_cs_pick(
            matrix, s.cs_odds, s.lambda_h, s.lambda_a,
            odds_ceiling=odds_ceiling,
            min_bookmakers=min_bookmakers, min_model_prob=min_model_prob,
        )
        if pick is None:
            continue
        if pick.ev < min_ev:
            continue
        won = s.home_score == pick.home_goals and s.away_score == pick.away_goals
        profit = flat_stake * (pick.exec_odds - 1.0) if won else -flat_stake
        picks.append({
            "fixture_id": s.fixture_id,
            "event_date": s.event_date,
            "league": s.league,
            "scoreline": f"{pick.home_goals}-{pick.away_goals}",
            "model_prob": pick.model_prob,
            "exec_odds": pick.exec_odds,
            "ev": pick.ev,
            "won": won,
            "profit": profit,
        })

    n = len(picks)
    wins = sum(1 for p in picks if p["won"])
    total_profit = sum(p["profit"] for p in picks)
    total_stake = n * flat_stake
    return {
        "min_ev": min_ev,
        "odds_ceiling": odds_ceiling,
        "rho": rho,
        "n": n,
        "wins": wins,
        "hit_rate": round(wins / n * 100, 1) if n else 0.0,
        "roi": round(total_profit / total_stake * 100, 1) if total_stake else 0.0,
        "profit": round(total_profit, 2),
        "avg_odds": round(sum(p["exec_odds"] for p in picks) / n, 2) if n else None,
        "avg_ev": round(sum(p["ev"] for p in picks) / n, 3) if n else None,
        "picks": picks,
    }


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
            "by_confidence": [],
            "by_agreement": [],
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

    def _bucket_stats(buckets: dict[str, dict], key_name: str) -> list[dict]:
        stats = []
        for k, d in sorted(buckets.items()):
            t = d["total"]
            w = d["wins"]
            p = d["profit"]
            s = t * BACKTEST_FLAT_STAKE
            odds_list = d["odds"]
            stats.append({
                key_name: k, "total": t, "count": t, "wins": w, "losses": t - w,
                "hit_rate": round(w / t * 100, 1) if t else 0.0,
                "roi": round(p / s * 100, 1) if s else 0.0,
                "profit": round(p, 2),
                "avg_odds": round(sum(odds_list) / len(odds_list), 2) if odds_list else None,
            })
        return stats

    by_market: dict[str, dict] = {}
    by_confidence: dict[str, dict] = {}
    by_agreement: dict[str, dict] = {}

    for r in results:
        for bucket, key in [
            (by_market, r.market),
            (by_confidence, r.dual_confidence or "Unknown"),
            (by_agreement, getattr(r, "dual_agreement", None) or "Unknown"),
        ]:
            if key not in bucket:
                bucket[key] = {"total": 0, "wins": 0, "profit": 0.0, "odds": []}
            bucket[key]["total"] += 1
            bucket[key]["wins"] += r.bet_result or 0
            bucket[key]["profit"] += r.profit_loss
            if r.actual_odd:
                bucket[key]["odds"].append(r.actual_odd)

    market_stats = _bucket_stats(by_market, "market")
    confidence_stats = _bucket_stats(by_confidence, "confidence")
    agreement_stats = _bucket_stats(by_agreement, "agreement")

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
        "by_confidence": confidence_stats,
        "by_agreement": agreement_stats,
        "bankroll_curve": curve,
    }

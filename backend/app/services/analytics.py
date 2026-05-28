"""
analytics.py — ROI, hit rate, streak, breakdown, and accumulator analytics.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional


def _ticket_source_label(ticket) -> str:
    name = (getattr(ticket, "name", "") or "").strip().lower()
    if "best 5" in name:
        return "Best 5"
    if "top 10" in name:
        return "Top 10"
    if "mini" in name:
        return "Mini"
    if "safe" in name:
        return "Safe"
    if "value" in name:
        return "Value"
    if "bold" in name:
        return "Bold"
    if "auto acca" in name:
        return "Auto"
    return "Manual"


def _bet_source_label(bet) -> str:
    label = (getattr(bet, "source_rule_label", "") or "").strip()
    if label:
        return label
    if getattr(bet, "source_rule_key", None):
        return "Signals Rule"
    return "Manual"


def build_analytics(bets: list) -> dict:
    """Build comprehensive analytics from a list of TrackedBet-like objects."""
    settled = [b for b in bets if b.result_status in ("Won", "Lost")]
    pending = [b for b in bets if b.result_status == "Pending"]

    total_bets = len(bets)
    total_stake = sum(b.stake for b in bets)
    wins = [b for b in settled if b.result_status == "Won"]
    losses = [b for b in settled if b.result_status == "Lost"]

    total_profit_loss = sum(b.profit_loss for b in settled)
    total_stake_settled = sum(b.stake for b in settled)
    total_odds = sum(b.odds for b in settled)

    win_rate = (len(wins) / len(settled) * 100) if settled else 0.0
    roi = (total_profit_loss / total_stake_settled * 100) if total_stake_settled else 0.0
    total_return = total_stake_settled + total_profit_loss
    avg_odds = (total_odds / len(settled)) if settled else 0.0

    # ── Streaks ──────────────────────────────────────────────────────────────
    sorted_settled = sorted(settled, key=lambda b: (b.settled_at or b.created_at, b.id))
    longest_win = longest_loss = 0
    run = 0
    current_type = None
    for b in sorted_settled:
        s = b.result_status
        if s == current_type:
            run += 1
        else:
            run = 1
            current_type = s
        if s == "Won":
            longest_win = max(longest_win, run)
        elif s == "Lost":
            longest_loss = max(longest_loss, run)
    current_streak_type = current_type
    current_streak_len = run

    # ── Daily trend ──────────────────────────────────────────────────────────
    # ── CLV summary ──────────────────────────────────────────────────────────
    clv_bets = [b for b in settled if getattr(b, "clv_pct", None) is not None]
    avg_clv = round(sum(b.clv_pct for b in clv_bets) / len(clv_bets), 2) if clv_bets else None
    clv_coverage_pct = round(len(clv_bets) / len(settled) * 100, 1) if settled else 0.0
    positive_clv_pct = (
        round(sum(1 for b in clv_bets if b.clv_pct > 0) / len(clv_bets) * 100, 1)
        if clv_bets else None
    )

    # ── Daily trend ──────────────────────────────────────────────────────────
    daily: dict[str, dict] = {}
    for b in settled:
        d = (b.settled_at or b.event_date or b.created_at)
        d_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        if d_str not in daily:
            daily[d_str] = {
                "profit_loss": 0.0, "stake": 0.0, "wins": 0, "bets": 0,
                "clv_sum": 0.0, "clv_count": 0,
            }
        daily[d_str]["profit_loss"] += b.profit_loss
        daily[d_str]["stake"] += b.stake
        daily[d_str]["bets"] += 1
        if b.result_status == "Won":
            daily[d_str]["wins"] += 1
        if getattr(b, "clv_pct", None) is not None:
            daily[d_str]["clv_sum"] += b.clv_pct
            daily[d_str]["clv_count"] += 1

    cumulative = 0.0
    daily_trend = []
    for d_str in sorted(daily.keys()):
        row = daily[d_str]
        cumulative += row["profit_loss"]
        daily_trend.append({
            "date": d_str,
            "profit_loss": round(row["profit_loss"], 2),
            "cumulative": round(cumulative, 2),
            "stake": round(row["stake"], 2),
            "wins": row["wins"],
            "bets": row["bets"],
            "avg_clv": round(row["clv_sum"] / row["clv_count"], 2) if row["clv_count"] else None,
        })

    # ── By market ────────────────────────────────────────────────────────────
    by_market: dict[str, dict] = defaultdict(
        lambda: {"bets": 0, "wins": 0, "losses": 0, "settled": 0,
                 "profit_loss": 0.0, "stake": 0.0, "odds_sum": 0.0}
    )
    for b in bets:
        m = b.market_type or "Unknown"
        by_market[m]["bets"] += 1
        by_market[m]["stake"] += b.stake
        by_market[m]["odds_sum"] += b.odds
        if b.result_status in ("Won", "Lost"):
            by_market[m]["settled"] += 1
            by_market[m]["profit_loss"] += b.profit_loss
        if b.result_status == "Won":
            by_market[m]["wins"] += 1
        elif b.result_status == "Lost":
            by_market[m]["losses"] += 1

    market_breakdown = []
    for market, d in sorted(by_market.items()):
        s = d["settled"]
        stake_s = sum(
            b.stake for b in bets
            if b.market_type == market and b.result_status in ("Won", "Lost")
        )
        market_breakdown.append({
            "market": market,
            "bets": d["bets"],
            "wins": d["wins"],
            "losses": d["losses"],
            "win_rate": round(d["wins"] / s * 100, 1) if s else 0.0,
            "roi": round(d["profit_loss"] / stake_s * 100, 1) if stake_s else 0.0,
            "profit_loss": round(d["profit_loss"], 2),
            "avg_odds": round(d["odds_sum"] / d["bets"], 2) if d["bets"] else 0.0,
        })

    # ── By league ────────────────────────────────────────────────────────────
    by_league: dict[str, dict] = defaultdict(
        lambda: {"bets": 0, "wins": 0, "losses": 0, "settled": 0,
                 "profit_loss": 0.0, "stake": 0.0, "odds_sum": 0.0}
    )
    for b in bets:
        lg = b.league or "Unknown"
        by_league[lg]["bets"] += 1
        by_league[lg]["stake"] += b.stake
        by_league[lg]["odds_sum"] += b.odds
        if b.result_status in ("Won", "Lost"):
            by_league[lg]["settled"] += 1
            by_league[lg]["profit_loss"] += b.profit_loss
        if b.result_status == "Won":
            by_league[lg]["wins"] += 1
        elif b.result_status == "Lost":
            by_league[lg]["losses"] += 1

    league_breakdown = []
    for lg, d in sorted(by_league.items()):
        s = d["settled"]
        stake_s = sum(
            b.stake for b in bets
            if (b.league or "Unknown") == lg and b.result_status in ("Won", "Lost")
        )
        league_breakdown.append({
            "league": lg,
            "bets": d["bets"],
            "wins": d["wins"],
            "losses": d["losses"],
            "win_rate": round(d["wins"] / s * 100, 1) if s else 0.0,
            "roi": round(d["profit_loss"] / stake_s * 100, 1) if stake_s else 0.0,
            "profit_loss": round(d["profit_loss"], 2),
            "avg_odds": round(d["odds_sum"] / d["bets"], 2) if d["bets"] else 0.0,
        })

    # ── By rule ──────────────────────────────────────────────────────────────
    by_rule: dict[str, dict] = defaultdict(
        lambda: {"bets": 0, "wins": 0, "losses": 0, "settled": 0,
                 "profit_loss": 0.0, "stake": 0.0}
    )
    for b in bets:
        rk = b.source_rule_key or "manual"
        by_rule[rk]["bets"] += 1
        by_rule[rk]["stake"] += b.stake
        if b.result_status in ("Won", "Lost"):
            by_rule[rk]["settled"] += 1
            by_rule[rk]["profit_loss"] += b.profit_loss
        if b.result_status == "Won":
            by_rule[rk]["wins"] += 1
        elif b.result_status == "Lost":
            by_rule[rk]["losses"] += 1

    rule_breakdown = []
    for rk, d in sorted(by_rule.items()):
        s = d["settled"]
        stake_s = sum(
            b.stake for b in bets
            if (b.source_rule_key or "manual") == rk and b.result_status in ("Won", "Lost")
        )
        rule_breakdown.append({
            "rule_key": rk,
            "bets": d["bets"],
            "wins": d["wins"],
            "losses": d["losses"],
            "win_rate": round(d["wins"] / s * 100, 1) if s else 0.0,
            "roi": round(d["profit_loss"] / stake_s * 100, 1) if stake_s else 0.0,
            "profit_loss": round(d["profit_loss"], 2),
        })

    # ── By signal confidence ──────────────────────────────────────────────────
    # Reveals how well each confidence tier (High/Medium/Low) actually performs.
    # This is the core feedback signal for the self-learning system.
    CONF_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Unknown": 3}
    by_conf: dict[str, dict] = defaultdict(
        lambda: {"bets": 0, "wins": 0, "losses": 0, "settled": 0,
                 "profit_loss": 0.0, "stake": 0.0, "odds_sum": 0.0}
    )
    for b in bets:
        c = b.dual_confidence or "Unknown"
        by_conf[c]["bets"] += 1
        by_conf[c]["stake"] += b.stake
        by_conf[c]["odds_sum"] += b.odds
        if b.result_status in ("Won", "Lost"):
            by_conf[c]["settled"] += 1
            by_conf[c]["profit_loss"] += b.profit_loss
        if b.result_status == "Won":
            by_conf[c]["wins"] += 1
        elif b.result_status == "Lost":
            by_conf[c]["losses"] += 1

    confidence_breakdown = []
    for conf, d in sorted(by_conf.items(), key=lambda x: CONF_ORDER.get(x[0], 99)):
        s = d["settled"]
        stake_s = sum(
            b.stake for b in bets
            if (b.dual_confidence or "Unknown") == conf and b.result_status in ("Won", "Lost")
        )
        confidence_breakdown.append({
            "confidence": conf,
            "bets": d["bets"],
            "wins": d["wins"],
            "losses": d["losses"],
            "win_rate": round(d["wins"] / s * 100, 1) if s else 0.0,
            "roi": round(d["profit_loss"] / stake_s * 100, 1) if stake_s else 0.0,
            "profit_loss": round(d["profit_loss"], 2),
            "avg_odds": round(d["odds_sum"] / d["bets"], 2) if d["bets"] else 0.0,
        })

    # ── By engine agreement ───────────────────────────────────────────────────
    # Shows which agreement types (Both/Bayesian Only/Poisson Only/Contradiction)
    # actually hit vs. how many bets carry each label.  Feeds the analytics page
    # Agreement Breakdown panel and the self-learning pipeline's min_prob_by_agreement rule.
    AGREE_ORDER = {"Both": 0, "Bayesian Only": 1, "Poisson Only": 2, "Contradiction": 3, "Unknown": 4}
    by_agree: dict[str, dict] = defaultdict(
        lambda: {"bets": 0, "wins": 0, "losses": 0, "settled": 0,
                 "profit_loss": 0.0, "stake": 0.0, "odds_sum": 0.0}
    )
    for b in bets:
        ag = getattr(b, "dual_agreement", None) or "Unknown"
        by_agree[ag]["bets"] += 1
        by_agree[ag]["stake"] += b.stake
        by_agree[ag]["odds_sum"] += b.odds
        if b.result_status in ("Won", "Lost"):
            by_agree[ag]["settled"] += 1
            by_agree[ag]["profit_loss"] += b.profit_loss
        if b.result_status == "Won":
            by_agree[ag]["wins"] += 1
        elif b.result_status == "Lost":
            by_agree[ag]["losses"] += 1

    agreement_breakdown = []
    for ag, d in sorted(by_agree.items(), key=lambda x: AGREE_ORDER.get(x[0], 99)):
        s = d["settled"]
        stake_s = sum(
            b.stake for b in bets
            if (getattr(b, "dual_agreement", None) or "Unknown") == ag
            and b.result_status in ("Won", "Lost")
        )
        agreement_breakdown.append({
            "agreement": ag,
            "bets": d["bets"],
            "wins": d["wins"],
            "losses": d["losses"],
            "win_rate": round(d["wins"] / s * 100, 1) if s else 0.0,
            "roi": round(d["profit_loss"] / stake_s * 100, 1) if stake_s else 0.0,
            "profit_loss": round(d["profit_loss"], 2),
            "avg_odds": round(d["odds_sum"] / d["bets"], 2) if d["bets"] else 0.0,
        })

    by_source: dict[str, dict] = defaultdict(
        lambda: {"bets": 0, "wins": 0, "losses": 0, "settled": 0, "profit_loss": 0.0, "stake": 0.0, "odds_sum": 0.0}
    )
    for b in bets:
        source = _bet_source_label(b)
        by_source[source]["bets"] += 1
        by_source[source]["stake"] += b.stake
        by_source[source]["odds_sum"] += b.odds
        if b.result_status in ("Won", "Lost"):
            by_source[source]["settled"] += 1
            by_source[source]["profit_loss"] += b.profit_loss
        if b.result_status == "Won":
            by_source[source]["wins"] += 1
        elif b.result_status == "Lost":
            by_source[source]["losses"] += 1

    SOURCE_ORDER = {
        "Top 10": 0,
        "Next 5": 1,
        "Signals Board": 2,
        "Quality View": 3,
        "EV View": 4,
        "Probability View": 5,
        "Stake View": 6,
        "Deep Dive": 7,
        "Signals Rule": 8,
        "Manual": 9,
    }
    source_breakdown = []
    for source, d in sorted(by_source.items(), key=lambda x: SOURCE_ORDER.get(x[0], 99)):
        settled_s = d["settled"]
        stake_s = sum(
            b.stake for b in bets
            if _bet_source_label(b) == source and b.result_status in ("Won", "Lost")
        )
        source_breakdown.append({
            "source": source,
            "bets": d["bets"],
            "wins": d["wins"],
            "losses": d["losses"],
            "win_rate": round(d["wins"] / settled_s * 100, 1) if settled_s else 0.0,
            "roi": round(d["profit_loss"] / stake_s * 100, 1) if stake_s else 0.0,
            "profit_loss": round(d["profit_loss"], 2),
            "avg_odds": round(d["odds_sum"] / d["bets"], 2) if d["bets"] else 0.0,
        })

    return {
        "total_bets": total_bets,
        "settled_bets": len(settled),
        "pending_bets": len(pending),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "roi": round(roi, 1),
        "avg_odds": round(avg_odds, 2),
        "total_profit_loss": round(total_profit_loss, 2),
        "total_stake": round(total_stake, 2),
        "total_stake_settled": round(total_stake_settled, 2),
        "total_return": round(total_return, 2),
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "current_streak_type": current_streak_type,
        "current_streak_len": current_streak_len,
        # ── Closing Line Value ───────────────────────────────────────────────
        # avg_clv > 0 means bets were placed at better prices than closing odds
        # (consistent positive CLV is the strongest long-run edge indicator).
        "avg_clv": avg_clv,
        "clv_coverage_pct": clv_coverage_pct,
        "positive_clv_pct": positive_clv_pct,
        # ────────────────────────────────────────────────────────────────────
        "daily_trend": daily_trend,
        "by_market": market_breakdown,
        "by_league": league_breakdown,
        "by_rule": rule_breakdown,
        "by_confidence": confidence_breakdown,
        "by_agreement": agreement_breakdown,
        "by_source": source_breakdown,
    }


def compute_parameter_status(bets: list) -> dict:
    """
    Classify every market and league as active / suspended / monitoring
    based on the user's full settled bet history.

    Thresholds (exposed in the response so the UI can show them):
      Active    — ≥ ACTIVE_MIN_BETS settled, ROI ≥ +5 %, hit rate ≥ 50 %
      Suspended — ≥ SUSPEND_MIN_BETS settled, ROI ≤ −10 %
      Monitoring — everything else (insufficient data or neutral performance)

    Both suspended and monitoring parameters continue to generate signals —
    they are just deprioritised / flagged so the user can choose to focus
    on the active ones.
    """
    ACTIVE_MIN_BETS   = 8
    ACTIVE_MIN_ROI    = 5.0    # %
    ACTIVE_MIN_HIT    = 50.0   # %
    SUSPEND_MIN_BETS  = 8
    SUSPEND_MAX_ROI   = -10.0  # %

    settled = [b for b in bets if b.result_status in ("Won", "Lost")]

    # ── Aggregate per market ──────────────────────────────────────────────────
    from collections import defaultdict
    mkt: dict[str, dict] = defaultdict(
        lambda: {"bets": 0, "wins": 0, "losses": 0, "settled": 0,
                 "profit_loss": 0.0, "stake": 0.0}
    )
    for b in bets:
        m = b.market_type or "Unknown"
        mkt[m]["bets"] += 1
        if b.result_status in ("Won", "Lost"):
            mkt[m]["settled"] += 1
            mkt[m]["profit_loss"] += b.profit_loss
            mkt[m]["stake"] += b.stake
        if b.result_status == "Won":
            mkt[m]["wins"] += 1
        elif b.result_status == "Lost":
            mkt[m]["losses"] += 1

    # ── Aggregate per league ──────────────────────────────────────────────────
    lge: dict[str, dict] = defaultdict(
        lambda: {"bets": 0, "wins": 0, "losses": 0, "settled": 0,
                 "profit_loss": 0.0, "stake": 0.0}
    )
    for b in bets:
        lg = b.league or "Unknown"
        lge[lg]["bets"] += 1
        if b.result_status in ("Won", "Lost"):
            lge[lg]["settled"] += 1
            lge[lg]["profit_loss"] += b.profit_loss
            lge[lg]["stake"] += b.stake
        if b.result_status == "Won":
            lge[lg]["wins"] += 1
        elif b.result_status == "Lost":
            lge[lg]["losses"] += 1

    def _classify(d: dict, name: str) -> dict:
        s = d["settled"]
        roi = round(d["profit_loss"] / d["stake"] * 100, 1) if d["stake"] else 0.0
        win_rate = round(d["wins"] / s * 100, 1) if s else 0.0
        pl = round(d["profit_loss"], 2)

        if s >= ACTIVE_MIN_BETS and roi >= ACTIVE_MIN_ROI and win_rate >= ACTIVE_MIN_HIT:
            status = "active"
            reason = f"{win_rate:.0f}% hit rate · +{roi:.1f}% ROI over {s} bets"
        elif s >= SUSPEND_MIN_BETS and roi <= SUSPEND_MAX_ROI:
            status = "suspended"
            reason = f"{win_rate:.0f}% hit rate · {roi:.1f}% ROI over {s} bets"
        elif s < ACTIVE_MIN_BETS:
            status = "monitoring"
            reason = f"Building data — {s}/{ACTIVE_MIN_BETS} settled bets"
        else:
            status = "monitoring"
            reason = f"Neutral — {win_rate:.0f}% hit, {roi:+.1f}% ROI over {s} bets"

        return {
            "parameter": name,
            "status": status,
            "bets": d["bets"],
            "settled": s,
            "wins": d["wins"],
            "losses": d["losses"],
            "win_rate": win_rate,
            "roi": roi,
            "profit_loss": pl,
            "reason": reason,
        }

    def _sort_key(row: dict) -> tuple:
        # active first, then monitoring, then suspended; within each group sort by ROI desc
        order = {"active": 0, "monitoring": 1, "suspended": 2}
        return (order.get(row["status"], 9), -row["roi"])

    markets = sorted([_classify(d, m) for m, d in mkt.items()], key=_sort_key)
    leagues = sorted([_classify(d, lg) for lg, d in lge.items()], key=_sort_key)

    return {
        "markets": markets,
        "leagues": leagues,
        "thresholds": {
            "active_min_bets":     ACTIVE_MIN_BETS,
            "active_min_roi":      ACTIVE_MIN_ROI,
            "active_min_hit_rate": ACTIVE_MIN_HIT,
            "suspend_min_bets":    SUSPEND_MIN_BETS,
            "suspend_max_roi":     SUSPEND_MAX_ROI,
        },
        "summary": {
            "active_markets":     sum(1 for r in markets if r["status"] == "active"),
            "suspended_markets":  sum(1 for r in markets if r["status"] == "suspended"),
            "active_leagues":     sum(1 for r in leagues if r["status"] == "active"),
            "suspended_leagues":  sum(1 for r in leagues if r["status"] == "suspended"),
        },
    }


def build_accumulator_analytics(tickets: list) -> dict:
    """
    Compute performance analytics for accumulator tickets.

    Expects objects with: result_status, combined_odds, stake, profit_loss,
    and legs (list with len()).
    """
    settled = [t for t in tickets if t.result_status in ("Won", "Lost")]
    pending = [t for t in tickets if t.result_status == "Pending"]

    total_tickets = len(tickets)
    total_stake = sum(t.stake for t in tickets if t.stake)
    total_pl = sum(t.profit_loss for t in settled if t.profit_loss is not None)
    total_stake_settled = sum(t.stake for t in settled if t.stake)
    wins = [t for t in settled if t.result_status == "Won"]

    hit_rate = (len(wins) / len(settled) * 100) if settled else 0.0
    roi = (total_pl / total_stake_settled * 100) if total_stake_settled else 0.0

    # ── By leg count ─────────────────────────────────────────────────────────
    by_legs: dict[int, dict] = defaultdict(
        lambda: {"tickets": 0, "wins": 0, "losses": 0,
                 "profit_loss": 0.0, "stake": 0.0}
    )
    for t in tickets:
        n = len(t.legs) if hasattr(t, "legs") and t.legs else 0
        by_legs[n]["tickets"] += 1
        if t.stake:
            by_legs[n]["stake"] += t.stake
        if t.result_status in ("Won", "Lost"):
            if t.profit_loss is not None:
                by_legs[n]["profit_loss"] += t.profit_loss
            if t.result_status == "Won":
                by_legs[n]["wins"] += 1
            else:
                by_legs[n]["losses"] += 1

    leg_breakdown = []
    for n in sorted(by_legs.keys()):
        d = by_legs[n]
        settled_n = d["wins"] + d["losses"]
        stake_s = d["stake"]
        leg_breakdown.append({
            "legs": n,
            "tickets": d["tickets"],
            "wins": d["wins"],
            "losses": d["losses"],
            "hit_rate": round(d["wins"] / settled_n * 100, 1) if settled_n else 0.0,
            "roi": round(d["profit_loss"] / stake_s * 100, 1) if stake_s else 0.0,
            "profit_loss": round(d["profit_loss"], 2),
        })

    # ── By combined odds band ─────────────────────────────────────────────────
    def _odds_band(odds: float) -> str:
        if odds <= 15:
            return "≤15 (safe)"
        if odds <= 30:
            return "16–30"
        if odds <= 60:
            return "31–60 (value)"
        return "61–100 (bold)"

    by_band: dict[str, dict] = defaultdict(
        lambda: {"tickets": 0, "wins": 0, "losses": 0,
                 "profit_loss": 0.0, "stake": 0.0}
    )
    for t in tickets:
        band = _odds_band(t.combined_odds or 0)
        by_band[band]["tickets"] += 1
        if t.stake:
            by_band[band]["stake"] += t.stake
        if t.result_status in ("Won", "Lost"):
            if t.profit_loss is not None:
                by_band[band]["profit_loss"] += t.profit_loss
            if t.result_status == "Won":
                by_band[band]["wins"] += 1
            else:
                by_band[band]["losses"] += 1

    BAND_ORDER = ["≤15 (safe)", "16–30", "31–60 (value)", "61–100 (bold)"]
    odds_breakdown = []
    for band in BAND_ORDER:
        if band not in by_band:
            continue
        d = by_band[band]
        settled_b = d["wins"] + d["losses"]
        stake_s = d["stake"]
        odds_breakdown.append({
            "band": band,
            "tickets": d["tickets"],
            "wins": d["wins"],
            "losses": d["losses"],
            "hit_rate": round(d["wins"] / settled_b * 100, 1) if settled_b else 0.0,
            "roi": round(d["profit_loss"] / stake_s * 100, 1) if stake_s else 0.0,
            "profit_loss": round(d["profit_loss"], 2),
        })

    # ── Market combo analysis ─────────────────────────────────────────────────
    # Requires tickets to have a leg_markets list attached by the router.
    # Canonical combo key = sorted, deduplicated market names joined by " + ".
    market_combos: dict[str, dict] = defaultdict(
        lambda: {"tickets": 0, "wins": 0, "losses": 0, "profit_loss": 0.0, "stake": 0.0}
    )
    for t in tickets:
        leg_markets = getattr(t, "leg_markets", None)
        if not leg_markets:
            continue
        combo_key = " + ".join(sorted(set(m for m in leg_markets if m)))
        if not combo_key:
            continue
        market_combos[combo_key]["tickets"] += 1
        if t.stake:
            market_combos[combo_key]["stake"] += t.stake
        if t.result_status == "Won":
            market_combos[combo_key]["wins"] += 1
            if t.profit_loss is not None:
                market_combos[combo_key]["profit_loss"] += t.profit_loss
        elif t.result_status == "Lost":
            market_combos[combo_key]["losses"] += 1
            if t.profit_loss is not None:
                market_combos[combo_key]["profit_loss"] += t.profit_loss

    combo_breakdown = []
    for combo, d in sorted(market_combos.items(), key=lambda x: -x[1]["tickets"]):
        settled_c = d["wins"] + d["losses"]
        stake_c = d["stake"]
        combo_breakdown.append({
            "markets": combo,
            "tickets": d["tickets"],
            "wins": d["wins"],
            "losses": d["losses"],
            "hit_rate": round(d["wins"] / settled_c * 100, 1) if settled_c else 0.0,
            "roi": round(d["profit_loss"] / stake_c * 100, 1) if stake_c else 0.0,
            "profit_loss": round(d["profit_loss"], 2),
        })

    by_source: dict[str, dict] = defaultdict(
        lambda: {"tickets": 0, "wins": 0, "losses": 0, "profit_loss": 0.0, "stake": 0.0}
    )
    for t in tickets:
        source = _ticket_source_label(t)
        by_source[source]["tickets"] += 1
        if t.stake:
            by_source[source]["stake"] += t.stake
        if t.result_status == "Won":
            by_source[source]["wins"] += 1
            if t.profit_loss is not None:
                by_source[source]["profit_loss"] += t.profit_loss
        elif t.result_status == "Lost":
            by_source[source]["losses"] += 1
            if t.profit_loss is not None:
                by_source[source]["profit_loss"] += t.profit_loss

    SOURCE_ORDER = {
        "Top 10": 0,
        "Best 5": 1,
        "Next 5": 2,
        "Remaining": 3,
        "Mini": 4,
        "Safe": 5,
        "Value": 6,
        "Bold": 7,
        "Auto": 8,
        "Manual": 9,
    }
    source_breakdown = []
    for source, d in sorted(by_source.items(), key=lambda x: SOURCE_ORDER.get(x[0], 99)):
        settled_s = d["wins"] + d["losses"]
        source_breakdown.append({
            "source": source,
            "tickets": d["tickets"],
            "wins": d["wins"],
            "losses": d["losses"],
            "hit_rate": round(d["wins"] / settled_s * 100, 1) if settled_s else 0.0,
            "roi": round(d["profit_loss"] / d["stake"] * 100, 1) if d["stake"] else 0.0,
            "profit_loss": round(d["profit_loss"], 2),
        })

    return {
        "total_tickets": total_tickets,
        "settled_tickets": len(settled),
        "pending_tickets": len(pending),
        "wins": len(wins),
        "losses": len(settled) - len(wins),
        "hit_rate": round(hit_rate, 1),
        "roi": round(roi, 1),
        "total_profit_loss": round(total_pl, 2),
        "total_stake": round(total_stake, 2),
        "by_legs": leg_breakdown,
        "by_odds_band": odds_breakdown,
        "by_source": source_breakdown,
        "by_market_combo": combo_breakdown,
    }

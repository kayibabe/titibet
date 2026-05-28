"""
league_watch_guard.py — Automated league performance monitoring.

Checks leagues in LEAGUE_WATCHLIST against BacktestResult and TrackedBet history.
When a league's ROI drops below its configured threshold (with enough sample size),
writes a LearningProposal(change_type="league_suppression") to the DB. The signal
engine and accumulator generator both read active league_suppression proposals, so
the suppression takes effect on the next signal generation cycle without a restart.

When a league's performance recovers above the recovery threshold, the proposal is
deactivated automatically so the league re-enters the signal pool.

State machine per watched league:
  OK       → ROI above warn threshold, or not enough bets yet
  WARNING  → ROI below warn threshold but above suppress threshold, OR not enough bets
  SUPPRESSED → ROI below suppress threshold AND min_bets_act reached → LP written
  RECOVERED  → previously suppressed, now above recovery threshold → LP deactivated
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import LEAGUE_WATCHLIST
from app.models.learning_proposal import LearningProposal

logger = logging.getLogger("titibet.watch_guard")

_CHANGE_TYPE = "league_suppression"


@dataclass
class LeagueStatus:
    keyword: str
    note: str
    total_bets: int
    wins: int
    roi_pct: float
    state: str          # "OK" | "WARNING" | "SUPPRESSED" | "RECOVERED"
    action_taken: str   # "none" | "suppressed" | "reactivated"
    proposal_id: Optional[int] = None
    message: str = ""


async def _get_active_proposal(db: AsyncSession, keyword: str) -> Optional[LearningProposal]:
    result = await db.execute(
        select(LearningProposal)
        .where(LearningProposal.change_type == _CHANGE_TYPE)
        .where(LearningProposal.target == keyword)
        .where(LearningProposal.is_active == True)  # noqa: E712
    )
    return result.scalar_one_or_none()


async def _query_league_stats(db: AsyncSession, keyword: str) -> tuple[int, int, float]:
    """
    Returns (total_bets, wins, roi_pct) combining BacktestResult + TrackedBet data.
    Uses substring match on league_name / league (lowercase).
    """
    kw = f"%{keyword}%"

    # BacktestResult — model replay data
    bt = await db.execute(text("""
        SELECT
            COUNT(*)           AS n,
            SUM(bet_result)    AS wins,
            SUM(profit_loss)   AS pnl,
            SUM(flat_stake)    AS staked
        FROM backtest_results
        WHERE LOWER(league_name) LIKE :kw
    """), {"kw": kw})
    bt_row = bt.fetchone()

    # TrackedBet — real settled bets
    tb = await db.execute(text("""
        SELECT
            COUNT(*)                                                           AS n,
            SUM(CASE WHEN result_status='Won' THEN 1 ELSE 0 END)              AS wins,
            SUM(profit_loss)                                                   AS pnl,
            SUM(stake)                                                         AS staked
        FROM tracked_bets
        WHERE result_status IN ('Won','Lost')
          AND LOWER(league) LIKE :kw
    """), {"kw": kw})
    tb_row = tb.fetchone()

    total_n    = (bt_row.n or 0) + (tb_row.n or 0)
    total_wins = (bt_row.wins or 0) + (tb_row.wins or 0)
    total_pnl  = (bt_row.pnl or 0.0) + (tb_row.pnl or 0.0)
    total_stk  = (bt_row.staked or 0.0) + (tb_row.staked or 0.0)

    roi = (total_pnl / total_stk * 100) if total_stk else 0.0
    return int(total_n), int(total_wins), round(roi, 1)


async def run_league_watch_guard(db: AsyncSession) -> list[LeagueStatus]:
    """
    Main entry point. Iterates over all entries in LEAGUE_WATCHLIST, evaluates
    current performance, and creates/deactivates LearningProposals as needed.

    Called after each settlement cycle in the scheduler.
    Returns one LeagueStatus per watched league for logging and admin visibility.
    """
    statuses: list[LeagueStatus] = []

    for keyword, cfg in LEAGUE_WATCHLIST.items():
        min_warn   = cfg.get("min_bets_warn", 5)
        min_act    = cfg.get("min_bets_act", 10)
        warn_roi   = cfg.get("warn_roi_pct", -10.0)
        supp_roi   = cfg.get("suppress_roi_pct", -20.0)
        recover_roi = cfg.get("recover_roi_pct", supp_roi + 15.0)  # must recover 15pp above suppress
        note       = cfg.get("note", "")

        total_bets, wins, roi = await _query_league_stats(db, keyword)
        active_proposal = await _get_active_proposal(db, keyword)

        state       = "OK"
        action      = "none"
        proposal_id = active_proposal.id if active_proposal else None
        msg_parts   = [f"bets={total_bets}  wins={wins}  ROI={roi:+.1f}%"]

        # ── Recovery check: previously suppressed, now improving ──────────────
        if active_proposal and total_bets >= min_act and roi >= recover_roi:
            active_proposal.is_active = False
            await db.commit()
            state  = "RECOVERED"
            action = "reactivated"
            proposal_id = active_proposal.id
            msg_parts.append(f"RECOVERED above {recover_roi:+.1f}% → proposal deactivated")
            logger.info(
                "Watch guard RECOVERED: '%s'  ROI=%+.1f%%  bets=%d  (proposal #%d deactivated)",
                keyword, roi, total_bets, active_proposal.id,
            )

        # ── Active proposal still valid: already suppressed ───────────────────
        elif active_proposal:
            state = "SUPPRESSED"
            msg_parts.append(f"still suppressed (LP #{active_proposal.id}), ROI {roi:+.1f}% < {recover_roi:+.1f}%")
            logger.debug("Watch guard SUPPRESSED (ongoing): '%s'  ROI=%+.1f%%", keyword, roi)

        # ── Threshold crossed: trigger suppression ────────────────────────────
        elif total_bets >= min_act and roi <= supp_roi:
            proposal = LearningProposal(
                change_type=_CHANGE_TYPE,
                target=keyword,
                proposed_value=round(roi, 1),
                rationale=(
                    f"Watch guard auto-suppression: '{keyword}' has {total_bets} bets "
                    f"with ROI={roi:+.1f}% (threshold={supp_roi:+.1f}%). {note}"
                ),
                confidence="Medium",
                backtest_note=(
                    f"{wins}/{total_bets} wins ({wins/total_bets*100:.0f}% WR)  "
                    f"ROI={roi:+.1f}%  threshold={supp_roi:+.1f}%"
                ),
                is_active=True,
            )
            db.add(proposal)
            await db.flush()   # get the id
            await db.commit()
            state       = "SUPPRESSED"
            action      = "suppressed"
            proposal_id = proposal.id
            msg_parts.append(
                f"*** AUTO-SUPPRESSED *** ROI={roi:+.1f}% < {supp_roi:+.1f}% "
                f"with {total_bets} bets → LP #{proposal.id} written"
            )
            logger.warning(
                "Watch guard SUPPRESSED: '%s'  ROI=%+.1f%%  bets=%d  WR=%.0f%%  LP #%d written",
                keyword, roi, total_bets, wins / total_bets * 100 if total_bets else 0, proposal.id,
            )

        # ── Warning zone: not enough data or ROI bad but below act threshold ──
        elif total_bets >= min_warn and roi <= warn_roi:
            state = "WARNING"
            msg_parts.append(
                f"WARNING: ROI={roi:+.1f}% < {warn_roi:+.1f}%  "
                f"({total_bets}/{min_act} bets for suppression)"
            )
            logger.warning(
                "Watch guard WARNING: '%s'  ROI=%+.1f%%  bets=%d/%d",
                keyword, roi, total_bets, min_act,
            )

        else:
            msg_parts.append(f"OK (warn threshold {warn_roi:+.1f}%)")
            logger.debug("Watch guard OK: '%s'  ROI=%+.1f%%  bets=%d", keyword, roi, total_bets)

        statuses.append(LeagueStatus(
            keyword=keyword,
            note=note,
            total_bets=total_bets,
            wins=wins,
            roi_pct=roi,
            state=state,
            action_taken=action,
            proposal_id=proposal_id,
            message=" | ".join(msg_parts),
        ))

    return statuses


async def get_watchlist_status(db: AsyncSession) -> list[dict]:
    """
    Public read-only view of the current watchlist state.
    Used by the admin API endpoint.
    """
    statuses = []
    for keyword, cfg in LEAGUE_WATCHLIST.items():
        min_warn   = cfg.get("min_bets_warn", 5)
        min_act    = cfg.get("min_bets_act", 10)
        warn_roi   = cfg.get("warn_roi_pct", -10.0)
        supp_roi   = cfg.get("suppress_roi_pct", -20.0)
        recover_roi = cfg.get("recover_roi_pct", supp_roi + 15.0)

        total_bets, wins, roi = await _query_league_stats(db, keyword)
        active_proposal = await _get_active_proposal(db, keyword)

        if active_proposal:
            state = "SUPPRESSED"
        elif total_bets >= min_warn and roi <= warn_roi:
            state = "WARNING"
        else:
            state = "OK"

        statuses.append({
            "keyword":          keyword,
            "note":             cfg.get("note", ""),
            "state":            state,
            "total_bets":       total_bets,
            "wins":             wins,
            "win_rate_pct":     round(wins / total_bets * 100, 1) if total_bets else 0.0,
            "roi_pct":          roi,
            "warn_roi_pct":     warn_roi,
            "suppress_roi_pct": supp_roi,
            "recover_roi_pct":  recover_roi,
            "min_bets_warn":    min_warn,
            "min_bets_act":     min_act,
            "active_proposal_id": active_proposal.id if active_proposal else None,
            "suppressed_since": active_proposal.created_at.isoformat() if active_proposal else None,
        })
    return statuses

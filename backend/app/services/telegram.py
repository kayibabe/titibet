"""
telegram.py — Telegram Bot integration for TiTiBet signal alerts.

Pushes signal digests to three named channels after every sync:

  TiTiBet General  — all signal matches for the day
  TiTiBet Free     — 3 deterministic daily picks
  TiTiBet Pro      — High Conf ACCA, Goals ACCA, Safe Ticket, Best Singles

Setup
-----
1. Create a bot via @BotFather → copy the token.
2. Add the bot to each group as admin ("Post Messages" rights).
3. Set in backend/.env:

   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_GENERAL_CHAT_ID=<chat id>
   TELEGRAM_FREE_CHAT_ID=<chat id>
   TELEGRAM_PRO_CHAT_ID=<chat id>
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import html
import httpx
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    get_settings,
    DISABLED_MARKETS,
    DISABLED_LEAGUES,
    OVER_GOALS_SUPPRESSED_LEAGUES,
)
from app.models import Signal, Fixture
from app.services.signal_engine import _get_underperforming_leagues

logger   = logging.getLogger("titibet.telegram")
settings = get_settings()

TELEGRAM_API = "https://api.telegram.org"
_MAX_CHARS   = 4000   # Telegram limit is 4096; leave buffer for safety


# ── Ranking helpers (mirrors app.routers.signals to avoid circular import) ───

def _system_rank(sig: Signal, fixture: Fixture | None = None) -> tuple:
    bayes_prob   = sig.bayesian_prob or 0.0
    poisson_prob = sig.poisson_prob or 0.0
    primary_prob = max(bayes_prob, poisson_prob)
    avg_prob     = (
        (bayes_prob + poisson_prob) / 2.0
        if bayes_prob and poisson_prob else primary_prob
    )
    books   = sig.bayesian_bookmaker_count or 0
    quality = sig.dual_quality_score or 0.0

    confidence_rank = {"High": 3, "Medium": 2, "Low": 1}.get(
        sig.dual_confidence or "", 0
    )
    agreement_rank = {
        "Both": 3, "Bayesian Only": 2, "Poisson Only": 1, "Contradiction": 0,
    }.get(sig.dual_agreement or "", 0)

    return (
        confidence_rank,
        agreement_rank,
        round(quality, 6),
        1 if primary_prob >= 0.70 else 0,
        round(primary_prob, 6),
        2 if books >= 3 else 1 if books == 2 else 0,
        1 if bayes_prob >= 0.65 and poisson_prob >= 0.65 else 0,
        1 if (fixture and fixture.league_tier == 1) else 0,
        round(avg_prob, 6),
        round(sig.poisson_lambda_total or 0.0, 6),
    )


def _best_per_fixture(
    rows: list[tuple[Signal, Fixture]],
) -> list[tuple[Signal, Fixture]]:
    best: dict[int, tuple[Signal, Fixture]] = {}
    for sig, fix in rows:
        cur = best.get(sig.fixture_id)
        if cur is None or _system_rank(sig, fix) > _system_rank(cur[0], cur[1]):
            best[sig.fixture_id] = (sig, fix)
    return list(best.values())


# ── Formatting helpers ────────────────────────────────────────────────────────

def _conf_header(confidence: str) -> str:
    return {
        "High":   "🔥 <b>HIGH CONFIDENCE</b>",
        "Medium": "📊 <b>MEDIUM CONFIDENCE</b>",
        "Low":    "📉 <b>LOW CONFIDENCE</b>",
    }.get(confidence, f"• <b>{confidence.upper()}</b>")


def _agreement_tag(agreement: str | None) -> str:
    return {
        "Both":          "✅ Both engines agree",
        "Bayesian Only": "ðŸ“ Bayesian",
        "Poisson Only":  "ðŸ“ Poisson",
        "Contradiction": "âš ï¸ Contradiction",
    }.get(agreement or "", "")


def _kickoff_str(kickoff_at: Any) -> str:
    if kickoff_at is None:
        return ""
    if getattr(kickoff_at, "tzinfo", None) is None:
        kickoff_at = kickoff_at.replace(tzinfo=timezone.utc)
    return kickoff_at.strftime("%H:%M UTC")


def _pct(prob: float | None) -> str:
    return f"{prob * 100:.0f}%" if prob is not None else "?"


def _odds(v: float | None) -> str:
    return f"{v:.2f}" if v is not None else "?"





def _esc(text: str | None) -> str:
    """HTML-escape dynamic content so team/league names with <>&'" don't break Telegram."""
    return html.escape(str(text or ""))


# Human-readable market labels for Telegram subscribers who aren't familiar
# with the terse internal naming.  Unlisted markets fall back to their raw name.
_MARKET_LABELS: dict[str, str] = {
    # ── Total goals ──────────────────────────────────────────────────────────
    "Over 0.5":  "Over 0.5 Goals",
    "Over 1.5":  "Over 1.5 Goals",
    "Over 2.5":  "Over 2.5 Goals",
    "Over 3.5":  "Over 3.5 Goals",
    "Over 4.5":  "Over 4.5 Goals",
    "Under 1.5": "Under 1.5 Goals",
    "Under 2.5": "Under 2.5 Goals",
    "Under 3.5": "Under 3.5 Goals",
    "Under 4.5": "Under 4.5 Goals",
    # ── Home team goals ──────────────────────────────────────────────────────
    "Home Over 0.5":  "Home Team Over 0.5 Goals",
    "Home Over 1.5":  "Home Team Over 1.5 Goals",
    "Home Under 0.5": "Home Team Under 0.5 Goals",
    "Home Under 1.5": "Home Team Under 1.5 Goals",
    # ── Away team goals ──────────────────────────────────────────────────────
    "Away Over 0.5":  "Away Team Over 0.5 Goals",
    "Away Over 1.5":  "Away Team Over 1.5 Goals",
    "Away Under 0.5": "Away Team Under 0.5 Goals",
    "Away Under 1.5": "Away Team Under 1.5 Goals",
    # ── Both teams to score ──────────────────────────────────────────────────
    "BTTS Yes": "Both Teams to Score",
    "BTTS No":  "Both Teams NOT to Score",
    # ── Match result ─────────────────────────────────────────────────────────
    "Home Win": "Home Win",
    "Draw":     "Draw",
    "Away Win": "Away Win",
    # ── Double-chance ────────────────────────────────────────────────────────
    "1X (Home or Draw)": "Home Win or Draw",
    "X2 (Draw or Away)": "Draw or Away Win",
    "12 (Home or Away)": "Either Team Wins (No Draw)",
    # ── Win to nil ───────────────────────────────────────────────────────────
    "Home Win to Nil": "Home Win to Nil",
    "Away Win to Nil": "Away Win to Nil",
    # ── Exact goals ──────────────────────────────────────────────────────────
    "Exactly 1 Goal":  "Exactly 1 Goal",
    "Exactly 2 Goals": "Exactly 2 Goals",
    "Exactly 3 Goals": "Exactly 3 Goals",
}


def _verbose_market(market: str | None) -> str:
    """Return a human-readable market label, falling back to the raw name."""
    m = (market or "").strip()
    return _MARKET_LABELS.get(m, m)


def _ko_from_iso(ko_raw: str | None) -> str:
    """
    Parse an ISO datetime string from a ticket leg dict and return ' · HH:MM UTC'.
    Returns empty string when absent or unparseable.
    Used by build_free_message and build_pro_message to avoid duplicating the
    fromisoformat dance in both functions.
    """
    if not ko_raw:
        return ""
    try:
        ko_dt = datetime.fromisoformat(str(ko_raw).replace("Z", "+00:00"))
        if ko_dt.tzinfo is None:
            ko_dt = ko_dt.replace(tzinfo=timezone.utc)
        return " · " + ko_dt.strftime("%H:%M UTC")
    except Exception:
        return ""


# ── Message splitter ─────────────────────────────────────────────────────────

def _split_message(text: str, limit: int = _MAX_CHARS) -> list[str]:
    """
    Split a Telegram message into chunks that each fit within `limit` characters.
    Splits on paragraph boundaries (\n\n) so legs and sections aren't cut mid-line.
    """
    if len(text) <= limit:
        return [text]

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        # +2 for the \n\n separator we'd join back
        needed = len(para) + (2 if current else 0)
        if current and current_len + needed > limit:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += needed

    if current:
        chunks.append("\n\n".join(current))

    return chunks or [text[:limit]]


# ── Transport ─────────────────────────────────────────────────────────────────

async def _send_to(chat_id: str, text: str) -> bool:
    """POST one message to a specific Telegram chat. Returns True on success."""
    token = settings.telegram_bot_token
    if not token or not chat_id:
        return False

    url     = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload = {
        "chat_id":                  chat_id,
        "text":                     text,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Telegram HTTP %s for chat %s: %s",
                exc.response.status_code, chat_id, exc.response.text[:200],
            )
        except Exception as exc:
            logger.warning("Telegram send to %s failed: %s", chat_id, exc)
    return False


# ── Pre-kickoff alert state ───────────────────────────────────────────────────
# Module-level set of fixture_ids that have already received a kickoff alert
# this process lifetime. Resets on restart (acceptable — alerts fire again after
# a restart but that's better than missing them).
_alerted_fixture_ids: set[int] = set()


def build_kickoff_alert(signals: list[tuple[Signal, Fixture]]) -> str:
    """
    Build a compact pre-kickoff alert message for High+Both signals kicking off soon.
    Sent ~30 min before kickoff so subscribers can still place bets.
    """
    parts = ["⏰ <b>KICKING OFF SOON — Top Picks</b>\n<i>High confidence picks</i>"]
    for sig, fix in signals:
        ko = _kickoff_str(fix.kickoff_at)
        league_line = f"{_esc(fix.country)} · {_esc(fix.league)}" if fix.country else _esc(fix.league or "")
        b_prob   = sig.bayesian_prob
        p_prob   = sig.poisson_prob
        primary  = max((v for v in [b_prob, p_prob] if v is not None), default=None)
        parts.append(
            f"\n🔥 <b>{_esc(fix.home_team)} vs {_esc(fix.away_team)}</b> — {ko}\n"
            f"   🏆 {league_line}\n"
            f"   📌 {_esc(_verbose_market(sig.market))} · {_pct(primary)}"
        )
    parts.append(f"\n<a href=\"{settings.app_url}\">{settings.app_url}</a>")
    return "\n".join(parts)


async def push_kickoff_alerts(db: AsyncSession) -> int:
    """
    Send pre-kickoff alerts for High+Both signals whose fixture kicks off within
    the next 90 minutes and haven't been alerted yet this session.

    Called every 30 min by the scheduler — returns number of alerts sent.
    No-op when Telegram is not configured.
    """
    if not settings.telegram_bot_token:
        return 0
    targets = _configured_titibet_channels()
    if not targets:
        return 0

    now = datetime.now(tz=timezone.utc)
    window_end = now + timedelta(minutes=90)

    # Fetch High+Both signals with kickoffs in the 90-min window
    query = (
        select(Signal, Fixture)
        .join(Fixture, Signal.fixture_id == Fixture.id)
        .where(Fixture.event_date == now.date())
        .where(Signal.dual_confidence == "High")
        .where(Signal.dual_agreement == "Both")
    )
    rows = list((await db.execute(query)).all())

    # Filter to the kickoff window and unseen fixtures
    upcoming: list[tuple[Signal, Fixture]] = []
    for sig, fix in rows:
        if fix.id in _alerted_fixture_ids:
            continue
        ko = fix.kickoff_at
        if ko is None:
            continue
        if getattr(ko, "tzinfo", None) is None:
            ko = ko.replace(tzinfo=timezone.utc)
        if now <= ko <= window_end:
            upcoming.append((sig, fix))

    if not upcoming:
        return 0

    # Deduplicate to best signal per fixture
    upcoming = _best_per_fixture(upcoming)
    upcoming.sort(key=lambda r: _system_rank(r[0], r[1]), reverse=True)

    text = build_kickoff_alert(upcoming)
    # Truncate to Telegram limit
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS - 50] + "\n... (truncated)"

    sent = 0
    for chat_id, _profile in targets:
        try:
            ok = await _send_to(chat_id, text)
        except Exception as _exc:
            logger.warning('TiTiBet Telegram [%s] send failed: %s', channel_type, _exc)
            ok = False
        if ok:
            sent += 1

    if sent > 0:
        newly_alerted = {fix.id for _sig, fix in upcoming}
        _alerted_fixture_ids.update(newly_alerted)
        logger.info(
            "Kickoff alerts sent to %d channel(s) for %d upcoming fixture(s): %s",
            sent, len(upcoming),
            ", ".join(f"{fix.home_team} vs {fix.away_team}" for _s, fix in upcoming),
        )

    return sent


async def _query_all_rows(db: AsyncSession, run_date: date) -> list[tuple[Signal, Fixture]]:
    """Fetch all un-deduplicated signal rows for run_date, with suppression applied."""
    bad_leagues    = await _get_underperforming_leagues(db, min_roi_pct=60.0)
    all_suppressed = bad_leagues | DISABLED_LEAGUES

    query = (
        select(Signal, Fixture)
        .join(Fixture, Signal.fixture_id == Fixture.id)
        .where(Fixture.event_date == run_date)
    )
    if all_suppressed:
        query = query.where(
            func.lower(func.trim(Fixture.league)).notin_(all_suppressed)
        )
    if DISABLED_MARKETS:
        query = query.where(Signal.market.notin_(list(DISABLED_MARKETS)))
    if OVER_GOALS_SUPPRESSED_LEAGUES:
        _over_list = [
            "Over 0.5", "Over 1.5", "Over 2.5", "Over 3.5",
            "Home Over 0.5", "Home Over 1.5",
            "Away Over 0.5", "Away Over 1.5",
        ]
        for _key in OVER_GOALS_SUPPRESSED_LEAGUES:
            query = query.where(
                ~(
                    func.lower(func.trim(Fixture.league)).contains(_key)
                    & Signal.market.in_(_over_list)
                )
            )
    return list((await db.execute(query)).all())


def _configured_titibet_channels() -> list[tuple[str, str]]:
    """Return (chat_id, channel_type) pairs for the three named TiTiBet channels."""
    channels: list[tuple[str, str]] = []
    if settings.telegram_general_chat_id:
        channels.append((settings.telegram_general_chat_id, "general"))
    if settings.telegram_free_chat_id:
        channels.append((settings.telegram_free_chat_id, "free"))
    if settings.telegram_pro_chat_id:
        channels.append((settings.telegram_pro_chat_id, "pro"))
    return channels


def build_general_message(signals: list[tuple], run_date: date) -> str:
    """Full signal digest for the TiTiBet General channel — all matches today."""
    date_label = run_date.strftime("%a %d %b %Y")
    parts = [
        f"📋 <b>TiTiBet General — {date_label}</b>",
        f"<i>All signal matches for today · {len(signals)} picks</i>",
    ]
    buckets: dict[str, list] = {"High": [], "Medium": [], "Low": []}
    for sig, fix in signals:
        bucket = sig.dual_confidence if sig.dual_confidence in buckets else "Low"
        buckets[bucket].append((sig, fix))
    idx = 1
    for conf in ("High", "Medium", "Low"):
        if not buckets[conf]:
            continue
        parts.append("")
        parts.append(_conf_header(conf))
        for sig, fix in buckets[conf]:
            primary = max((v for v in [sig.bayesian_prob, sig.poisson_prob] if v), default=None)
            ko = _kickoff_str(fix.kickoff_at)
            league_line = f"{_esc(fix.country)} · {_esc(fix.league)}" if fix.country else _esc(fix.league or "")
            parts.append(
                f"\n<b>{idx}. {_esc(fix.home_team)} vs {_esc(fix.away_team)}</b>\n"
                f"   🏆 {league_line}{(' · ' + ko) if ko else ''}\n"
                f"   📌 {_esc(_verbose_market(sig.market))} · {_pct(primary)}"
            )
            idx += 1
    parts.append(f"\n<a href=\"{settings.app_url}\">{settings.app_url}</a>")
    return "\n".join(parts)


def build_free_message(free_ticket: dict, run_date: date) -> str:
    """Top 3 EV-ranked picks for the TiTiBet Free channel."""
    date_label = run_date.strftime("%a %d %b %Y")
    selected = free_ticket.get("selected_legs", [])
    combined = free_ticket.get("combined_odds")
    win_prob = free_ticket.get("win_probability_estimate")
    kelly    = free_ticket.get("kelly_stake_pct")

    # Build stats line: combined odds · win prob · Kelly stake
    stats_parts = []
    if combined:
        stats_parts.append(f"Combined {combined:.2f}x")
    if win_prob is not None:
        stats_parts.append(f"Win {_pct(win_prob)}")
    if kelly and kelly > 0:
        stats_parts.append(f"Kelly {kelly * 100:.1f}%")
    stats_line = f"<i>{' · '.join(stats_parts)}</i>" if stats_parts else ""

    parts = [
        f"🎯 <b>TiTiBet Free — {date_label}</b>",
        f"<i>Today's top 3 value picks</i>",
    ]
    if stats_line:
        parts.append(stats_line)

    for i, leg in enumerate(selected, 1):
        ko_str = _ko_from_iso(leg.get("kickoff_at"))
        parts.append(
            f"\n<b>{i}. {_esc(leg.get('match_name', ''))}</b>\n"
            f"   🏆 {_esc(leg.get('league', ''))}{ko_str}\n"
            f"   📌 {_esc(_verbose_market(leg.get('market', '')))} · {_pct(leg.get('probability'))}"
        )

    other = free_ticket.get("other_legs", [])
    if other:
        names = " · ".join(
            f"{_esc(l.get('home_team',''))} vs {_esc(l.get('away_team',''))}"
            for l in other[:8]
        )
        parts.append(f"\n<i>Also today: {names}</i>")
    parts.append(f"\n🔒 Full details: <a href=\"{settings.app_url}\">{settings.app_url}</a>")
    return "\n".join(parts)


def build_pro_message(pro_ticket: dict, run_date: date) -> str:
    """5 Pro sub-tickets for the TiTiBet Pro channel, formatted like the Free ticket."""
    date_label = run_date.strftime("%a %d %b %Y")
    parts = [
        f"💎 <b>TiTiBet Pro — {date_label}</b>",
        f"<i>High Conf ACCA · Goals ACCA · Safe Ticket · Best Singles · Sharp Moves</i>",
    ]
    sub_emojis = {
        "high_conf_acca": "🔥",
        "goals_acca":     "⚽",
        "safe_ticket":    "🛡",
        "best_singles":   "⭐",
        "sharp_moves":    "📈",
    }
    for sub in pro_ticket.get("sub_tickets", []):
        legs = sub.get("legs", [])
        if not legs:
            continue

        emoji      = sub_emojis.get(sub["key"], "•")
        is_singles = sub.get("is_singles", False)

        # Sub-ticket stats: combined odds + Kelly (accas only)
        stats_parts = []
        combined = sub.get("combined_odds")
        kelly    = sub.get("kelly_stake_pct")
        if combined and not is_singles:
            stats_parts.append(f"{combined:.2f}x")
        if kelly and kelly > 0 and not is_singles:
            stats_parts.append(f"Kelly {kelly * 100:.1f}%")
        stats_suffix = f"  <i>· {' · '.join(stats_parts)}</i>" if stats_parts else ""

        parts.append(f"\n{emoji} <b>{sub['label']}</b>{stats_suffix}")

        for i, leg in enumerate(legs, 1):
            ko_str   = _ko_from_iso(leg.get("kickoff_at"))
            league   = _esc(leg.get("league") or "")
            prob     = leg.get("probability")
            prob_str = f" · {_pct(prob)}" if prob is not None else ""
            parts.append(
                f"\n<b>{i}. {_esc(leg.get('match_name', ''))}</b>\n"
                f"   🏆 {league}{ko_str}\n"
                f"   📌 {_esc(_verbose_market(leg.get('market', '')))}{prob_str}"
            )

    parts.append(f"\n<a href=\"{settings.app_url}\">{settings.app_url}</a>")
    return "\n".join(parts)


async def push_titibet_tickets(db: AsyncSession, run_date: date) -> bool:
    """
    Send the three TiTiBet named tickets to their respective Telegram channels.
    Called after every sync + compute cycle (same trigger as push_titibet_tickets).
    Safe to call when Telegram is not configured — returns False silently.
    """
    if not settings.telegram_bot_token:
        return False
    channels = _configured_titibet_channels()
    if not channels:
        return False

    from app.services.recommended_tickets import load_titibet_tickets
    tickets = await load_titibet_tickets(db, run_date)
    free_ticket = tickets.get("free", {})
    pro_ticket  = tickets.get("pro", {})

    # Build the general signal list (same query as existing push_titibet_tickets)
    all_rows = await _query_all_rows(db, run_date)
    general_rows = _best_per_fixture(all_rows)
    general_rows.sort(key=lambda r: _system_rank(r[0], r[1]), reverse=True)

    any_sent = False
    for chat_id, channel_type in channels:
        if channel_type == "general":
            msg = build_general_message(general_rows, run_date)
        elif channel_type == "free":
            msg = build_free_message(free_ticket, run_date)
        elif channel_type == "pro":
            msg = build_pro_message(pro_ticket, run_date)
        else:
            continue

        chunks = _split_message(msg)
        ok = False
        for chunk in chunks:
            try:
                ok = await _send_to(chat_id, chunk)
            except Exception as _exc:
                logger.warning('TiTiBet Telegram [%s] send failed: %s', channel_type, _exc)
                ok = False
        if ok:
            logger.info("TiTiBet Telegram [%s → %s]: sent %d chunk(s) for %s",
                        channel_type, chat_id, len(chunks), run_date)
            any_sent = True
        else:
            logger.warning('TiTiBet Telegram [%s → %s]: send failed for %s', channel_type, chat_id, run_date)

    return any_sent


# ─────────────────────────────────────────────────────────────────────────────
# Results reporting
# ─────────────────────────────────────────────────────────────────────────────

# Terminal statuses mirrored from settlement.py (no circular import).
_FINAL_STATUSES: frozenset[str] = frozenset({"FT", "AET", "PEN"})
_VOID_STATUSES:  frozenset[str] = frozenset({"CANC", "ABD", "AWD", "WO", "TBD", "PST", "INT", "SUSP"})

# Score-based settlement conditions — kept in sync with settlement.SCORE_SETTLEABLE_MARKETS.
_RESULT_CONDITIONS: dict[str, Callable[[int, int], bool]] = {
    "BTTS Yes":   lambda h, a: h >= 1 and a >= 1,
    "BTTS No":    lambda h, a: h == 0 or a == 0,
    "Over 0.5":   lambda h, a: (h + a) >= 1,
    "Over 1.5":   lambda h, a: (h + a) >= 2,
    "Over 2.5":   lambda h, a: (h + a) >= 3,
    "Over 3.5":   lambda h, a: (h + a) >= 4,
    "Over 4.5":   lambda h, a: (h + a) >= 5,
    "Under 1.5":  lambda h, a: (h + a) <= 1,
    "Under 2.5":  lambda h, a: (h + a) <= 2,
    "Under 3.5":  lambda h, a: (h + a) <= 3,
    "Under 4.5":  lambda h, a: (h + a) <= 4,
    "Home Win":   lambda h, a: h > a,
    "Draw":       lambda h, a: h == a,
    "Away Win":   lambda h, a: h < a,
    "1X (Home or Draw)":  lambda h, a: h >= a,
    "X2 (Draw or Away)":  lambda h, a: h <= a,
    "12 (Home or Away)":  lambda h, a: h != a,
    "Home Over 0.5":  lambda h, a: h >= 1,
    "Home Under 0.5": lambda h, a: h == 0,
    "Home Over 1.5":  lambda h, a: h >= 2,
    "Home Under 1.5": lambda h, a: h <= 1,
    "Away Over 0.5":  lambda h, a: a >= 1,
    "Away Under 0.5": lambda h, a: a == 0,
    "Away Over 1.5":  lambda h, a: a >= 2,
    "Away Under 1.5": lambda h, a: a <= 1,
    "Home Win to Nil": lambda h, a: h > a and a == 0,
    "Away Win to Nil": lambda h, a: a > h and h == 0,
    "Exactly 1 Goal":  lambda h, a: (h + a) == 1,
    "Exactly 2 Goals": lambda h, a: (h + a) == 2,
    "Exactly 3 Goals": lambda h, a: (h + a) == 3,
}


def _compute_result_from_market(market: str, fix: Fixture) -> str:
    """
    Determine the outcome of a market prediction from the final fixture score.
    Returns: 'won' | 'lost' | 'void' | 'pending' | 'unknown'
    """
    status = (fix.status or "").strip().upper()
    if status in _VOID_STATUSES:
        return "void"
    if status not in _FINAL_STATUSES:
        return "pending"
    h = fix.home_score
    a = fix.away_score
    if h is None or a is None:
        return "unknown"
    condition = _RESULT_CONDITIONS.get(market)
    if condition is None:
        return "unknown"
    return "won" if condition(int(h), int(a)) else "lost"


def _compute_result(sig: Signal, fix: Fixture) -> str:
    """Compute result for a Signal row against its Fixture."""
    return _compute_result_from_market(sig.market, fix)


def _result_emoji(result: str) -> str:
    return {"won": "✅", "lost": "❌", "void": "⚪", "pending": "⏳", "unknown": "❓"}.get(result, "❓")


def _score_str(fix: Fixture) -> str:
    """Return a compact score/status string like '2-1' or 'PST'."""
    status = (fix.status or "").strip().upper()
    if status in _VOID_STATUSES:
        return status
    if fix.home_score is not None and fix.away_score is not None:
        return f"{fix.home_score}-{fix.away_score}"
    return "?"


# ── Results push log helpers ──────────────────────────────────────────────────

async def _check_results_sent(db: AsyncSession, push_date: date, channel_type: str) -> bool:
    """Return True if a results message has already been sent for this date+channel."""
    row = await db.execute(
        text(
            "SELECT 1 FROM telegram_push_log "
            "WHERE push_date = :d AND channel_type = :ct AND push_type = 'results' "
            "LIMIT 1"
        ),
        {"d": push_date.isoformat(), "ct": channel_type},
    )
    return row.scalar() is not None


async def _log_results_sent(db: AsyncSession, push_date: date, channel_type: str) -> None:
    """Record that a results message was sent. Safe to call multiple times (INSERT OR IGNORE)."""
    await db.execute(
        text(
            "INSERT OR IGNORE INTO telegram_push_log (push_date, channel_type, push_type) "
            "VALUES (:d, :ct, 'results')"
        ),
        {"d": push_date.isoformat(), "ct": channel_type},
    )
    await db.commit()


# ── Results message builders ──────────────────────────────────────────────────

def build_general_results_message(
    signals: list[tuple[Signal, Fixture]],
    run_date: date,
) -> str:
    """Results digest for the General channel — all picks with outcomes and a summary."""
    date_label = run_date.strftime("%a %d %b %Y")

    rows_with_result: list[tuple[Signal, Fixture, str]] = []
    counts: dict[str, int] = {"won": 0, "lost": 0, "void": 0}
    for sig, fix in signals:
        r = _compute_result(sig, fix)
        counts[r] = counts.get(r, 0) + 1
        rows_with_result.append((sig, fix, r))

    won, lost, void_ = counts["won"], counts["lost"], counts["void"]
    total = len(signals)
    hit_rate = round(won / (won + lost) * 100) if (won + lost) > 0 else 0

    parts = [
        f"📊 <b>TiTiBet General — Results</b>",
        f"<i>{date_label} · {total} picks · Hit rate: {hit_rate}%</i>",
    ]

    buckets: dict[str, list[tuple[Signal, Fixture, str]]] = {"High": [], "Medium": [], "Low": []}
    for sig, fix, result in rows_with_result:
        bucket = sig.dual_confidence if sig.dual_confidence in buckets else "Low"
        buckets[bucket].append((sig, fix, result))

    idx = 1
    for conf in ("High", "Medium", "Low"):
        if not buckets[conf]:
            continue
        parts.append("")
        parts.append(_conf_header(conf))
        for sig, fix, result in buckets[conf]:
            score    = _score_str(fix)
            primary  = max((v for v in [sig.bayesian_prob, sig.poisson_prob] if v), default=None)
            r_emoji  = _result_emoji(result)
            ko       = _kickoff_str(fix.kickoff_at)
            league_line = (
                f"{_esc(fix.country)} · {_esc(fix.league)}" if fix.country else _esc(fix.league or "")
            )
            parts.append(
                f"\n{r_emoji} <b>{idx}. {_esc(fix.home_team)} vs {_esc(fix.away_team)}</b> ({score})"
                f"{(' · ' + ko) if ko else ''}\n"
                f"   🏆 {league_line}\n"
                f"   📌 {_esc(_verbose_market(sig.market))} · {_pct(primary)}"
            )
            idx += 1

    parts.append("")
    summary_pieces = [f"{won} Won", f"{lost} Lost"]
    if void_:
        summary_pieces.append(f"{void_} Void")
    if (won + lost) > 0:
        summary_pieces.append(f"Hit rate: {hit_rate}%")
    parts.append(f"📈 <b>Summary: {' · '.join(summary_pieces)}</b>")
    parts.append(f"\n<a href=\"{settings.app_url}\">{settings.app_url}</a>")
    return "\n".join(parts)


def build_free_results_message(
    free_ticket: dict,
    fix_map: dict[int, "Fixture"],
    run_date: date,
) -> str:
    """Results for the Free channel — 3 picks with individual outcomes and an overall verdict."""
    date_label = run_date.strftime("%a %d %b %Y")
    legs = free_ticket.get("selected_legs", [])

    parts = [
        f"🎯 <b>TiTiBet Free — Results</b>",
        f"<i>{date_label} · {len(legs)} picks</i>",
    ]

    won_count = lost_count = void_count = 0
    for i, leg in enumerate(legs, 1):
        fix_id = leg.get("fixture_id")
        fix    = fix_map.get(fix_id) if fix_id else None
        if fix:
            result = _compute_result_from_market(leg.get("market", ""), fix)
            score  = _score_str(fix)
        else:
            result = "unknown"
            score  = "?"

        r_emoji = _result_emoji(result)
        if result == "won":    won_count  += 1
        elif result == "lost": lost_count += 1
        elif result == "void": void_count += 1

        ko_str   = _ko_from_iso(leg.get("kickoff_at"))
        prob     = leg.get("probability")
        prob_str = f" · {_pct(prob)}" if prob is not None else ""

        parts.append(
            f"\n{r_emoji} <b>{i}. {_esc(leg.get('match_name', ''))}</b> ({score}){ko_str}\n"
            f"   🏆 {_esc(leg.get('league', ''))}\n"
            f"   📌 {_esc(_verbose_market(leg.get('market', '')))}{prob_str}"
        )

    total      = won_count + lost_count
    all_void   = (void_count == len(legs)) and len(legs) > 0
    ticket_won = (lost_count == 0) and (won_count > 0)
    verdict    = "⚪ Void" if all_void else ("✅ Won" if ticket_won else "❌ Lost")
    hit_rate   = round(won_count / total * 100) if total > 0 else 0

    parts.append(f"\n📈 {won_count}/{len(legs)} Won · Ticket: {verdict}")
    if total > 0:
        parts.append(f"<i>Hit rate: {hit_rate}%</i>")
    parts.append(f"\n<a href=\"{settings.app_url}\">{settings.app_url}</a>")
    return "\n".join(parts)


def build_pro_results_message(
    pro_ticket: dict,
    fix_map: dict[int, "Fixture"],
    run_date: date,
) -> str:
    """Results for the Pro channel — each sub-ticket with leg outcomes in General format."""
    date_label = run_date.strftime("%a %d %b %Y")
    parts = [
        f"💎 <b>TiTiBet Pro — Results</b>",
        f"<i>{date_label}</i>",
    ]

    sub_emojis = {
        "high_conf_acca": "🔥",
        "goals_acca":     "⚽",
        "safe_ticket":    "🛡",
        "best_singles":   "⭐",
        "sharp_moves":    "📈",
    }

    for sub in pro_ticket.get("sub_tickets", []):
        legs = sub.get("legs", [])
        if not legs:
            continue

        emoji      = sub_emojis.get(sub.get("key", ""), "•")
        is_singles = sub.get("is_singles", False)
        won_legs = lost_legs = void_legs = 0

        # Collect rendered leg lines first so we can compute the verdict for the header
        leg_parts: list[str] = []
        for i, leg in enumerate(legs, 1):
            fix_id = leg.get("fixture_id")
            fix    = fix_map.get(fix_id) if fix_id else None
            if fix:
                result = _compute_result_from_market(leg.get("market", ""), fix)
                score  = _score_str(fix)
            else:
                result = "unknown"
                score  = "?"

            r_emoji = _result_emoji(result)
            if result == "won":    won_legs  += 1
            elif result == "lost": lost_legs += 1
            elif result == "void": void_legs += 1

            ko_str   = _ko_from_iso(leg.get("kickoff_at"))
            prob     = leg.get("probability")
            prob_str = f" · {_pct(prob)}" if prob is not None else ""

            leg_parts.append(
                f"\n{r_emoji} <b>{i}. {_esc(leg.get('match_name', ''))}</b> ({score}){ko_str}\n"
                f"   🏆 {_esc(leg.get('league', ''))}\n"
                f"   📌 {_esc(_verbose_market(leg.get('market', '')))}{prob_str}"
            )

        all_void   = (void_legs == len(legs)) and len(legs) > 0
        ticket_won = (lost_legs == 0) and (won_legs > 0)
        verdict    = "⚪ Void" if all_void else ("✅ Won" if ticket_won else "❌ Lost")

        # Sub-ticket header with verdict
        parts.append("")
        if is_singles:
            parts.append(f"{emoji} <b>{_esc(sub.get('label', ''))}</b>")
        else:
            parts.append(f"{emoji} <b>{_esc(sub.get('label', ''))}</b> · {verdict}")

        parts.extend(leg_parts)

        if not is_singles:
            parts.append(f"<i>({won_legs}/{len(legs)} legs won)</i>")

    parts.append(f"\n<a href=\"{settings.app_url}\">{settings.app_url}</a>")
    return "\n".join(parts)


# ── Main results push entry point ─────────────────────────────────────────────

async def push_results_report(
    db: AsyncSession,
    run_date: date,
    force: bool = False,
) -> bool:
    """
    Send a results digest for run_date to every configured Telegram channel.

    Fires only when ALL fixtures in that day's signal list have reached a
    terminal state (FT / AET / PEN / CANC / PST / …).  If any fixture is
    still in-progress the function returns False silently — call it again
    after the next settlement cycle.

    Uses telegram_push_log to prevent duplicate sends. Pass force=True to
    re-send even if the log shows it was already sent (admin override).

    Returns True if at least one channel received the message.
    """
    if not settings.telegram_bot_token:
        return False
    channels = _configured_titibet_channels()
    if not channels:
        return False

    # ── 1. Query signals + fixtures for the date ──────────────────────────────
    all_rows = await _query_all_rows(db, run_date)
    if not all_rows:
        logger.debug("Results report: no signals for %s — skipping", run_date)
        return False

    signal_rows = _best_per_fixture(all_rows)
    signal_rows.sort(key=lambda r: _system_rank(r[0], r[1]), reverse=True)

    # ── 2. Check all fixtures are in a terminal state ─────────────────────────
    pending = [
        fix for _, fix in signal_rows
        if (fix.status or "").strip().upper() not in (_FINAL_STATUSES | _VOID_STATUSES)
    ]
    if pending and not force:
        logger.debug(
            "Results report: %d fixture(s) still pending for %s (%s) — skipping",
            len(pending), run_date,
            ", ".join(f.home_team + " vs " + f.away_team for f in pending[:3]),
        )
        return False

    # ── 3. Load ticket data (for Free + Pro) ─────────────────────────────────
    # include_finished=True bypasses the "no finished fixtures" filter in
    # _load_candidates so the same picks that were originally sent are
    # recovered after games end.
    from app.services.recommended_tickets import load_titibet_tickets
    tickets = await load_titibet_tickets(db, run_date, include_finished=True)

    # Build fix_map from ALL fixtures for the date — not just the subset that
    # passes _query_all_rows suppression filters.  Pro/Free legs may belong to
    # leagues/markets that were suppressed after the original push; querying
    # fixtures directly ensures those legs can still be resolved to a score.
    all_fixtures_result = await db.execute(
        select(Fixture).where(Fixture.event_date == run_date)
    )
    fix_map: dict[int, Fixture] = {
        f.id: f for f in all_fixtures_result.scalars().all()
    }

    # ── 4. Send to each channel ───────────────────────────────────────────────
    any_sent = False
    for chat_id, channel_type in channels:
        if not force:
            already = await _check_results_sent(db, run_date, channel_type)
            if already:
                logger.debug(
                    "Results report: already sent for %s/%s — skipping (use force=True to override)",
                    run_date, channel_type,
                )
                continue

        if channel_type == "general":
            msg = build_general_results_message(signal_rows, run_date)
        elif channel_type == "free":
            msg = build_free_results_message(tickets.get("free", {}), fix_map, run_date)
        elif channel_type == "pro":
            msg = build_pro_results_message(tickets.get("pro", {}), fix_map, run_date)
        else:
            continue

        chunks = _split_message(msg)
        ok = False
        for chunk in chunks:
            try:
                ok = await _send_to(chat_id, chunk)
            except Exception as exc:
                logger.warning(
                    "Results report [%s → %s] send failed: %s", channel_type, chat_id, exc
                )
                ok = False

        if ok:
            await _log_results_sent(db, run_date, channel_type)
            logger.info(
                "Results report [%s → %s]: sent %d chunk(s) for %s",
                channel_type, chat_id, len(chunks), run_date,
            )
            any_sent = True
        else:
            logger.warning(
                "Results report [%s → %s]: send FAILED for %s",
                channel_type, chat_id, run_date,
            )

    return any_sent


async def check_and_push_pending_results(db: AsyncSession) -> int:
    """
    Sweep the last 3 days and push results for any date that is fully settled
    but hasn't been reported yet. Called after every settlement cycle.

    Returns the number of dates for which results were pushed.
    """
    pushed = 0
    today  = date.today()
    for delta in range(3):
        target = today - timedelta(days=delta)
        try:
            sent = await push_results_report(db, target)
            if sent:
                pushed += 1
        except Exception:
            logger.exception(
                "check_and_push_pending_results: error pushing results for %s", target
            )
    return pushed



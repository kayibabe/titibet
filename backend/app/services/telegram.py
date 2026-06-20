"""
telegram.py — Telegram Bot integration for TiTiBet signal alerts.

Pushes signal digests to three named channels after every sync:

  TiTiBet General  — all signal matches for the day
  TiTiBet Free     — 3 deterministic daily picks
  TiTiBet Pro      — top-ranked signals (same digest as General)

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


# ─────────────────────────────────────────────────────────────────────────────
# Nightly "tonight + overnight" digest
#
# Broadcast in the evening (20:30 CAT) so subscribers can see — and bet on —
# matches that kick off after midnight Malawi time BEFORE going to bed. Those
# after-midnight (CAT) matches fall on the *next* UTC date, so this digest spans
# both today and tomorrow (UTC) and only lists fixtures that haven't kicked off.
# Times are shown in CAT (UTC+2, Malawi) since that's the subscriber base.
# ─────────────────────────────────────────────────────────────────────────────

# Malawi / Central Africa Time — fixed UTC+2, no daylight saving.
_CAT_OFFSET = timedelta(hours=2)
_CAT_LABEL = "CAT"


def _ko_aware(kickoff_at: Any) -> datetime | None:
    """Return a tz-aware (UTC) datetime for a fixture kickoff, or None."""
    if kickoff_at is None:
        return None
    if getattr(kickoff_at, "tzinfo", None) is None:
        return kickoff_at.replace(tzinfo=timezone.utc)
    return kickoff_at


def _kickoff_str_cat(kickoff_at: Any) -> str:
    """Format a kickoff time in Malawi local time, e.g. '02:30 CAT'."""
    ko = _ko_aware(kickoff_at)
    if ko is None:
        return ""
    return (ko + _CAT_OFFSET).strftime("%H:%M ") + _CAT_LABEL


def build_signal_digest(
    rows: list[tuple[Signal, Fixture]],
    channel_type: str = "general",
    limit: int | None = None,
    total: int | None = None,
) -> str:
    """
    Build a 'tonight + overnight' digest. `rows` is already deduped and ordered
    by the caller (chronological for general/pro, top-ranked for free). `total`
    is the full count before any `limit` was applied (for the "+N more" teaser).
    """
    shown = rows[:limit] if limit else rows
    total = total if total is not None else len(rows)
    title = {
        "free": "TiTiBet Free — Tonight's Top Picks",
        "pro":  "TiTiBet Pro — Tonight &amp; Overnight",
        "general": "TiTiBet — Tonight &amp; Overnight",
    }.get(channel_type, "TiTiBet — Tonight &amp; Overnight")

    parts = [
        f"🌙 <b>{title}</b>",
        f"<i>Upcoming matches incl. after-midnight kickoffs · {len(shown)} picks · times in CAT</i>",
    ]
    for i, (sig, fix) in enumerate(shown, 1):
        primary = max((v for v in [sig.bayesian_prob, sig.poisson_prob] if v), default=None)
        ko = _kickoff_str_cat(fix.kickoff_at)
        league_line = f"{_esc(fix.country)} · {_esc(fix.league)}" if fix.country else _esc(fix.league or "")
        conf_tag = {"High": "🔥", "Medium": "📊", "Low": "📉"}.get(sig.dual_confidence or "", "•")
        parts.append(
            f"\n<b>{i}. {_esc(fix.home_team)} vs {_esc(fix.away_team)}</b>{(' · ' + ko) if ko else ''}\n"
            f"   🏆 {league_line}\n"
            f"   📌 {_esc(_verbose_market(sig.market))} · {_pct(primary)} {conf_tag}"
        )
    if limit and total > limit:
        extra = total - limit
        parts.append(
            f"\n<i>➕ {extra} more pick{'s' if extra != 1 else ''} on the app — "
            f"upgrade for the full list.</i>"
        )
    parts.append(f"\n<a href=\"{settings.app_url}\">{settings.app_url}</a>")
    return "\n".join(parts)


async def push_signal_digest(db: AsyncSession, free_limit: int = 3) -> int:
    """
    Broadcast the 'tonight + overnight' digest to all configured channels.
    General/Pro get every upcoming match (chronological); Free gets the top
    `free_limit` by model rank. Spans today + tomorrow (UTC) so after-midnight
    (CAT) matches are included. Returns the number of channels sent to.
    No-op when Telegram is not configured or there are no upcoming matches.
    """
    if not settings.telegram_bot_token:
        return 0
    targets = _configured_titibet_channels()
    if not targets:
        return 0

    now = datetime.now(tz=timezone.utc)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    rows = await _query_all_rows(db, today)
    rows += await _query_all_rows(db, tomorrow)
    deduped = _best_per_fixture(rows)

    # Keep only fixtures that haven't kicked off yet.
    upcoming = [(s, f) for s, f in deduped if (_ko_aware(f.kickoff_at) or now) >= now and f.kickoff_at]
    if not upcoming:
        logger.info("Signal digest: no upcoming matches — nothing to send")
        return 0

    chronological = sorted(upcoming, key=lambda r: _ko_aware(r[1].kickoff_at))
    by_rank = sorted(upcoming, key=lambda r: _system_rank(r[0], r[1]), reverse=True)

    sent = 0
    for chat_id, channel_type in targets:
        if channel_type == "free":
            text = build_signal_digest(by_rank, channel_type="free", limit=free_limit, total=len(upcoming))
        else:
            text = build_signal_digest(chronological, channel_type=channel_type, total=len(upcoming))
        ok = False
        for chunk in _split_message(text):
            ok = await _send_to(chat_id, chunk)
        if ok:
            sent += 1

    if sent:
        logger.info(
            "Signal digest sent to %d channel(s) — %d upcoming match(es) tonight + overnight",
            sent, len(upcoming),
        )
    return sent


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

    # ── 3. Send to each channel ───────────────────────────────────────────────
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

        msg = build_general_results_message(signal_rows, run_date)

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



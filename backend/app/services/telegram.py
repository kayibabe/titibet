"""
telegram.py — Telegram Bot integration for TiTiBet signal alerts.

Pushes signal digests to two named channels after every sync:

  TiTiBet Free     — limited/blurred teaser of the day's picks
  TiTiBet Pro      — top-ranked signals, full detail

Setup
-----
1. Create a bot via @BotFather → copy the token.
2. Add the bot to each group as admin ("Post Messages" rights).
3. Set in backend/.env:

   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_FREE_CHAT_ID=<chat id>
   TELEGRAM_PRO_CHAT_ID=<chat id>
"""
from __future__ import annotations

import logging
import random
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
    DUAL_HIGH_ODDS_CEILING,
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

    # Poisson + Medium takes top priority — mirrors app.routers.signals._system_rank.
    poisson_medium_flag = 1 if (sig.dual_agreement == "Poisson Only" and sig.dual_confidence == "Medium") else 0

    return (
        poisson_medium_flag,
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
    for chat_id, channel_type in targets:
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
        .where(Signal.is_candidate == False)  # noqa: E712 — mirrors signals router
    )
    if all_suppressed:
        query = query.where(
            func.lower(func.trim(Fixture.league)).notin_(all_suppressed)
        )
        query = query.where(~func.lower(func.trim(Fixture.league)).contains("friendlies"))
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
    rows = list((await db.execute(query)).all())

    # Mirror the router's Both+High odds ceiling — don't push picks to subscribers
    # that the signal list would suppress.
    if DUAL_HIGH_ODDS_CEILING:
        rows = [
            (sig, fix) for sig, fix in rows
            if not (
                sig.dual_confidence == "High"
                and sig.dual_agreement == "Both"
                and sig.market in DUAL_HIGH_ODDS_CEILING
                and (sig.bayesian_best_odd or 0.0) >= DUAL_HIGH_ODDS_CEILING[sig.market]
            )
        ]

    return rows


def _configured_titibet_channels() -> list[tuple[str, str]]:
    """Return (chat_id, channel_type) pairs for the two named TiTiBet channels."""
    channels: list[tuple[str, str]] = []
    if settings.telegram_free_chat_id:
        channels.append((settings.telegram_free_chat_id, "free"))
    if settings.telegram_pro_chat_id:
        channels.append((settings.telegram_pro_chat_id, "pro"))
    return channels


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

# How far ahead the digest reaches. The evening send (20:30 CAT) should cover
# tonight + the after-midnight (overnight) window, NOT all of tomorrow — otherwise
# tomorrow's afternoon/evening fixtures show up and look like today's. ~12h from
# 20:30 CAT reaches ~08:30 CAT, covering the overnight slate only.
import os as _os
DIGEST_HORIZON_HOURS = int(_os.getenv("DIGEST_HORIZON_HOURS", "12"))


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


def _kickoff_label_cat(kickoff_at: Any, now_utc: datetime) -> str:
    """Kickoff in CAT with a day prefix relative to now, e.g. 'Today 20:00 CAT',
    'Tomorrow 02:30 CAT', or a weekday for anything further out. Prevents tomorrow's
    matches from being mistaken for today's in the digest."""
    ko = _ko_aware(kickoff_at)
    if ko is None:
        return ""
    ko_cat = ko + _CAT_OFFSET
    now_cat = now_utc + _CAT_OFFSET
    day_diff = (ko_cat.date() - now_cat.date()).days
    if day_diff == 0:
        prefix = "Today "
    elif day_diff == 1:
        prefix = "Tomorrow "
    else:
        prefix = ko_cat.strftime("%a ")
    return prefix + ko_cat.strftime("%H:%M ") + _CAT_LABEL


# Number of matches randomly revealed in clear text on the Free channel;
# everything else is replaced with a non-revealable placeholder. Kept as a
# module-level default so every Free push uses the same count unless overridden.
FREE_REVEAL_COUNT = 2

# Placeholder used for hidden match names in the Free channel. Unlike
# <tg-spoiler>, this cannot be revealed by tapping — the real names simply
# aren't in the message.
_FREE_HIDDEN_MATCH = "▒▒▒▒▒ vs ▒▒▒▒▒"
_FREE_HIDDEN_ACCA  = "▒▒▒▒▒ vs ▒▒▒▒▒"

# Shown at the end of every Free-channel message.
FREE_UPGRADE_CTA = (
    "\n<i>🔒 Some matches are hidden — upgrade to Pro to see every pick "
    "instantly, in full, with no restrictions.</i>"
)


def _pick_reveal_fixture_ids(rows: list[tuple[Signal, Fixture]], count: int) -> set[int]:
    """
    Choose `count` fixtures to reveal in clear text on the Free channel.

    Biased away from the strongest picks (High confidence) so the free preview
    never accidentally hands out the best pick by chance — the reveal pool
    prefers Medium/Low confidence signals, falling back to the full set only
    when there aren't enough non-High signals to fill the quota.
    """
    if count <= 0 or not rows:
        return set()
    pool = [r for r in rows if r[0].dual_confidence != "High"]
    if len(pool) < count:
        pool = list(rows)
    count = min(count, len(pool))
    return {fix.id for _sig, fix in random.sample(pool, count)}


def build_signal_digest(
    rows: list[tuple[Signal, Fixture]],
    channel_type: str = "pro",
    now: datetime | None = None,
    reveal_fixture_ids: set[int] | None = None,
    acca: dict | None = None,
) -> str:
    """
    Build a 'tonight + overnight' digest. `rows` is already deduped and ordered
    by the caller (chronological). `now` is used to label each kickoff
    Today/Tomorrow in CAT.

    channel_type="pro"  — every match shown in clear text, acca included.
    channel_type="free" — only fixtures in `reveal_fixture_ids` show real team
    names; hidden matches and all acca legs use a non-revealable placeholder
    so content cannot be exposed by tapping.
    """
    is_free = channel_type == "free"
    reveal_fixture_ids = reveal_fixture_ids or set()
    now = now or datetime.now(tz=timezone.utc)
    title = "TiTiBet Free — Tonight &amp; Overnight" if is_free else "TiTiBet Pro — Tonight &amp; Overnight"

    parts = [
        f"🌙 <b>{title}</b>",
        f"<i>Tonight &amp; after-midnight kickoffs · {len(rows)} picks · times in CAT</i>",
    ]
    for i, (sig, fix) in enumerate(rows, 1):
        primary = max((v for v in [sig.bayesian_prob, sig.poisson_prob] if v), default=None)
        ko = _kickoff_label_cat(fix.kickoff_at, now)
        league_line = f"{_esc(fix.country)} · {_esc(fix.league)}" if fix.country else _esc(fix.league or "")
        conf_tag = {"High": "🔥", "Medium": "📊", "Low": "📉"}.get(sig.dual_confidence or "", "•")
        match_name = (
            _FREE_HIDDEN_MATCH
            if is_free and fix.id not in reveal_fixture_ids
            else f"{_esc(fix.home_team)} vs {_esc(fix.away_team)}"
        )
        parts.append(
            f"\n<b>{i}. {match_name}</b>{(' · ' + ko) if ko else ''}\n"
            f"   🏆 {league_line}\n"
            f"   📌 {_esc(_verbose_market(sig.market))} · {_pct(primary)} {conf_tag}"
        )

    legs = (acca or {}).get("legs") or []
    combined_odds = (acca or {}).get("combined_odds")
    if legs and combined_odds:
        parts.append("\n" + "─" * 24)
        parts.append(f"\n🎟️ <b>AI Acca of the Day</b> — combined @ {combined_odds}")
        for i, leg in enumerate(legs, 1):
            match = (
                _FREE_HIDDEN_ACCA if is_free
                else (
                    f"{_esc(leg.get('home_team'))} vs {_esc(leg.get('away_team'))}"
                    if leg.get("home_team") and leg.get("away_team") else "—"
                )
            )
            odd = leg.get("odd")
            odd_str = f"{float(odd):.2f}" if odd is not None else "?"
            parts.append(
                f"\n   {i}. <b>{match}</b> — {_esc(leg.get('market'))} @ {odd_str}"
            )
        rationale = (acca or {}).get("rationale")
        if rationale and not is_free:
            parts.append(f"\n<i>{_esc(rationale)}</i>")

    if is_free:
        parts.append(FREE_UPGRADE_CTA)
    parts.append(f"\n<a href=\"{settings.app_url}\">{settings.app_url}</a>")
    return "\n".join(parts)


async def push_signal_digest(db: AsyncSession, free_reveal_count: int = FREE_REVEAL_COUNT) -> int:
    """
    Broadcast the 'tonight + overnight' digest to all configured channels.
    Both channels get the full upcoming match list (chronological); Free
    spoiler-blurs all but `free_reveal_count` randomly-revealed matches
    (biased away from High confidence — see `_pick_reveal_fixture_ids`).
    Spans today + tomorrow (UTC) so after-midnight (CAT) matches are included.
    Returns the number of channels sent to.
    No-op when Telegram is not configured or there are no upcoming matches.
    """
    if not settings.telegram_bot_token:
        return 0
    targets = _configured_titibet_channels()
    if not targets:
        return 0

    now = datetime.now(tz=timezone.utc)
    cutoff = now + timedelta(hours=DIGEST_HORIZON_HOURS)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    rows = await _query_all_rows(db, today)
    rows += await _query_all_rows(db, tomorrow)
    deduped = _best_per_fixture(rows)

    # Keep only fixtures kicking off between now and the horizon (tonight + the
    # after-midnight window). This excludes tomorrow's afternoon/evening fixtures,
    # which otherwise show up and get mistaken for today's.
    upcoming = []
    for s, f in deduped:
        ko = _ko_aware(f.kickoff_at)
        if ko is not None and now <= ko <= cutoff:
            upcoming.append((s, f))
    if not upcoming:
        logger.info("Signal digest: no upcoming matches in the next %dh — nothing to send", DIGEST_HORIZON_HOURS)
        return 0

    chronological = sorted(upcoming, key=lambda r: _ko_aware(r[1].kickoff_at))
    reveal_fixture_ids = _pick_reveal_fixture_ids(upcoming, free_reveal_count)

    sent = 0
    for chat_id, channel_type in targets:
        if channel_type == "free":
            text = build_signal_digest(chronological, channel_type="free", now=now, reveal_fixture_ids=reveal_fixture_ids)
        else:
            text = build_signal_digest(chronological, channel_type=channel_type, now=now)
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
# Tomorrow digest — singles + AI Advisory accumulator ticket
#
# Sent at 18:00 UTC (8pm Malawi local) right after the tomorrow pre-sync, so
# subscribers get tomorrow's full single-match slate AND the AI Advisory's
# Acca-of-the-Day in one message — early enough to place bets tonight.
#
# Pro gets everything in clear text. Free gets identical content, but only
# `FREE_REVEAL_COUNT` randomly chosen matches (biased away from High
# confidence — see `_pick_reveal_fixture_ids`) are shown in clear — the rest
# have their team names wrapped in a Telegram spoiler (tap-to-reveal blur),
# and every leg of the Acca ticket is spoiler-blurred regardless, since the
# accumulator is the headline Pro perk.
# ─────────────────────────────────────────────────────────────────────────────

async def _check_push_sent(db: AsyncSession, push_date: date, channel_type: str, push_type: str) -> bool:
    """Return True if a message of this push_type has already been sent for this date+channel."""
    row = await db.execute(
        text(
            "SELECT 1 FROM telegram_push_log "
            "WHERE push_date = :d AND channel_type = :ct AND push_type = :pt "
            "LIMIT 1"
        ),
        {"d": push_date.isoformat(), "ct": channel_type, "pt": push_type},
    )
    return row.scalar() is not None


async def _log_push_sent(db: AsyncSession, push_date: date, channel_type: str, push_type: str) -> None:
    """Record that a message was sent. Safe to call multiple times (INSERT OR IGNORE)."""
    await db.execute(
        text(
            "INSERT OR IGNORE INTO telegram_push_log (push_date, channel_type, push_type) "
            "VALUES (:d, :ct, :pt)"
        ),
        {"d": push_date.isoformat(), "ct": channel_type, "pt": push_type},
    )
    await db.commit()


def build_tomorrow_message(
    rows: list[tuple[Signal, Fixture]],
    run_date: date,
    acca: dict | None,
    channel_type: str = "pro",
    reveal_fixture_ids: set[int] | None = None,
) -> str:
    """
    Build the 'tomorrow' message: full single-match slate (chronological)
    followed by the AI Advisory's Acca-of-the-Day ticket.

    channel_type="pro"  — every match and every acca leg shown in clear text.
    channel_type="free" — only fixtures in `reveal_fixture_ids` show real team
    names; everything else (market, odds, confidence, league) stays visible,
    just the team names are spoiler-blurred. Acca legs are always blurred.
    """
    is_free = channel_type == "free"
    reveal_fixture_ids = reveal_fixture_ids or set()
    title = "TiTiBet Free" if is_free else "TiTiBet Pro"
    date_label = run_date.strftime("%a %d %b %Y")
    parts = [
        f"🌅 <b>{title} — Tomorrow, {date_label}</b>",
        f"<i>Full slate for tomorrow · {len(rows)} pick{'s' if len(rows) != 1 else ''} · place your bets tonight</i>",
    ]
    for i, (sig, fix) in enumerate(rows, 1):
        primary = max((v for v in [sig.bayesian_prob, sig.poisson_prob] if v), default=None)
        ko = _kickoff_str_cat(fix.kickoff_at)
        league_line = f"{_esc(fix.country)} · {_esc(fix.league)}" if fix.country else _esc(fix.league or "")
        conf_tag = {"High": "🔥", "Medium": "📊", "Low": "📉"}.get(sig.dual_confidence or "", "•")
        match_name = (
            _FREE_HIDDEN_MATCH
            if is_free and fix.id not in reveal_fixture_ids
            else f"{_esc(fix.home_team)} vs {_esc(fix.away_team)}"
        )
        parts.append(
            f"\n<b>{i}. {match_name}</b>{(' · ' + ko) if ko else ''}\n"
            f"   🏆 {league_line}\n"
            f"   📌 {_esc(_verbose_market(sig.market))} · {_pct(primary)} {conf_tag}"
        )

    legs = (acca or {}).get("legs") or []
    combined_odds = (acca or {}).get("combined_odds")
    if legs and combined_odds:
        parts.append("\n" + "─" * 24)
        parts.append(f"\n🎟️ <b>AI Acca of the Day</b> — combined @ {combined_odds}")
        for i, leg in enumerate(legs, 1):
            match = (
                _FREE_HIDDEN_ACCA if is_free
                else (
                    f"{_esc(leg.get('home_team'))} vs {_esc(leg.get('away_team'))}"
                    if leg.get("home_team") and leg.get("away_team") else "—"
                )
            )
            odd = leg.get("odd")
            odd_str = f"{float(odd):.2f}" if odd is not None else "?"
            parts.append(
                f"\n   {i}. <b>{match}</b> — {_esc(leg.get('market'))} @ {odd_str}"
            )
        rationale = (acca or {}).get("rationale")
        if rationale and not is_free:
            parts.append(f"\n<i>{_esc(rationale)}</i>")

    if is_free:
        parts.append(FREE_UPGRADE_CTA)
    parts.append(f"\n<a href=\"{settings.app_url}\">{settings.app_url}</a>")
    return "\n".join(parts)


async def push_tomorrow_digest(db: AsyncSession, run_date: date | None = None) -> int:
    """
    Broadcast tomorrow's full single-match slate plus the AI Advisory's Acca-of-
    the-Day to TiTiBet Free and Pro. Pro sees everything in clear; Free sees
    `FREE_REVEAL_COUNT` randomly chosen matches revealed and the rest (plus the
    whole Acca ticket) spoiler-blurred. Called by the 18:00 UTC tomorrow
    pre-sync job, after fixtures/odds/signals are synced and the advisory cache
    is warmed for tomorrow. Idempotent per date+channel via telegram_push_log.
    Returns the number of channels sent to.
    """
    if not settings.telegram_bot_token:
        return 0
    targets = [(cid, ct) for cid, ct in _configured_titibet_channels() if ct in ("free", "pro")]
    if not targets:
        return 0

    run_date = run_date or (date.today() + timedelta(days=1))

    rows = await _query_all_rows(db, run_date)
    deduped = _best_per_fixture(rows)
    if not deduped:
        logger.info("Tomorrow digest: no signals for %s — nothing to send", run_date)
        return 0

    chronological = sorted(deduped, key=lambda r: _ko_aware(r[1].kickoff_at) or datetime.max.replace(tzinfo=timezone.utc))

    acca = None
    try:
        from app.services.advisor_service import get_advisor_insights
        insights = await get_advisor_insights(db, run_date, current_user=None, force=False)
        acca = insights.get("accumulator")
    except Exception:
        logger.exception("Tomorrow digest: failed to fetch AI Advisory acca — sending singles only")

    reveal_fixture_ids = _pick_reveal_fixture_ids(chronological, FREE_REVEAL_COUNT)

    sent = 0
    for chat_id, channel_type in targets:
        if await _check_push_sent(db, run_date, channel_type, "tomorrow"):
            logger.info("Tomorrow digest: already sent for %s/%s — skipping", run_date, channel_type)
            continue

        text_msg = build_tomorrow_message(
            chronological, run_date, acca,
            channel_type=channel_type,
            reveal_fixture_ids=reveal_fixture_ids if channel_type == "free" else None,
        )

        ok = False
        for chunk in _split_message(text_msg):
            ok = await _send_to(chat_id, chunk)

        if ok:
            await _log_push_sent(db, run_date, channel_type, "tomorrow")
            sent += 1

    if sent:
        logger.info(
            "Tomorrow digest sent to %d channel(s) — %d single(s), acca=%s",
            sent, len(deduped), "yes" if acca and acca.get("legs") else "no",
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

def build_results_message(
    signals: list[tuple[Signal, Fixture]],
    run_date: date,
) -> str:
    """Results digest shared by all channels — every pick with outcome and a summary."""
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
        f"📊 <b>TiTiBet — Results</b>",
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

        msg = build_results_message(signal_rows, run_date)

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


async def push_morning_digest(db: AsyncSession, free_reveal_count: int = FREE_REVEAL_COUNT) -> int:
    """
    Broadcast today's full signal list as a morning digest (all-day picks).
    Called after the 06:00 UTC sync so subscribers see the day's picks at wake-up.
    Both channels get the full list (rank order); Free spoiler-blurs all but
    `free_reveal_count` randomly-revealed matches (biased away from High
    confidence — see `_pick_reveal_fixture_ids`).
    Returns the number of channels sent to.
    """
    if not settings.telegram_bot_token:
        return 0
    targets = _configured_titibet_channels()
    if not targets:
        return 0

    today = date.today()
    now = datetime.now(tz=timezone.utc)

    rows = await _query_all_rows(db, today)
    deduped = _best_per_fixture(rows)
    if not deduped:
        logger.info("Morning digest: no signals for %s — skipping", today)
        return 0

    # Rank order (best first) so subscribers see top picks immediately.
    by_rank = sorted(deduped, key=lambda r: _system_rank(r[0], r[1]), reverse=True)
    reveal_fixture_ids = _pick_reveal_fixture_ids(by_rank, free_reveal_count)

    acca = None
    try:
        from app.services.advisor_service import get_advisor_insights
        insights = await get_advisor_insights(db, today, current_user=None, force=False)
        acca = insights.get("accumulator")
    except Exception:
        logger.exception("Morning digest: failed to fetch AI Advisory acca — sending singles only")

    sent = 0
    for chat_id, channel_type in targets:
        if channel_type == "free":
            text = build_signal_digest(by_rank, channel_type="free", now=now, reveal_fixture_ids=reveal_fixture_ids, acca=acca)
        else:
            text = build_signal_digest(by_rank, channel_type=channel_type, now=now, acca=acca)
        # Override title line to say "Today's Picks" instead of "Tonight & Overnight"
        text = text.replace("Tonight &amp; Overnight", "Today's Picks")
        text = text.replace("Tonight &amp; after-midnight kickoffs", "Today's signal picks")
        ok = False
        for chunk in _split_message(text):
            ok = await _send_to(chat_id, chunk)
        if ok:
            sent += 1

    if sent:
        logger.info("Morning digest sent to %d channel(s) — %d picks for %s", sent, len(deduped), today)
    return sent


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


async def push_ingestion_alert(
    db: AsyncSession,
    run_date: date,
    status: str,
    error_message: str | None = None,
) -> None:
    """
    Send a brief ops alert to the Pro channel when an ingestion run fails.
    Fail-silent — never raises so it can't block the sync loop.
    Sends only to the Pro channel (ops audience), not the Free channel.
    """
    if not settings.telegram_bot_token or not settings.telegram_pro_chat_id:
        return
    try:
        date_str = run_date.isoformat()
        msg_parts = [
            f"⚠️ <b>Ingestion alert — {_esc(date_str)}</b>",
            f"Status: <code>{_esc(status)}</code>",
        ]
        if error_message:
            msg_parts.append(f"Error: {_esc(error_message[:300])}")
        await _send_to(settings.telegram_pro_chat_id, "\n".join(msg_parts))
        logger.info("Ingestion alert sent for %s (status=%s)", date_str, status)
    except Exception as exc:
        logger.warning("push_ingestion_alert failed (non-fatal): %s", exc)



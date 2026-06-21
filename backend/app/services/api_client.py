"""
api_client.py — API-Football HTTP client.

Merged from FootBet api_client.py and TiTiBet api_football.py.
Fetches fixtures + all market types (CS, goals O/U, BTTS, 1X2, FH CS, corners).
All market snapshot rows are stored flat for later engine processing.

API call budget
---------------
Previous design: 2 bookmaker loops × up to 3 pages = up to 6 calls per sync.
Current design:  1 call per page (no bookmaker filter) — API returns all bookmakers
                 in each response entry under the "bookmakers" key.  We filter to our
                 target bookmaker IDs client-side after parsing.

This halves the odds call count:  up to 3 calls per sync instead of 6.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger("titibet.api_client")

from app.core.config import get_settings

settings = get_settings()

BASE_URL = "https://v3.football.api-sports.io"

# Bookmakers fetched from API-Football odds endpoint.
# The API returns ALL bookmakers in one response; we filter client-side.
#
# Verified IDs from GET /odds/bookmakers (confirmed by scanning live cache 2026-05-22):
#   4  = Pinnacle      — sharpest market in the world, lowest overround (~2-3%)
#                        Used as the reference price for EV/edge calculation.
#   8  = Bet365        — high CS coverage, sharp 1X2/totals; also a reference price source
#   11 = 1xBet         — wide CS coverage, sometimes generous on totals
#   7  = William Hill  — European soft book (~8-12% overround); best proxy for
#                        regional soft books (Betpawa MW, 888bets, Premier Bet MW, Moors Bet)
#                        since those bookmakers are not in API-Football's coverage at all.
#                        Odds are stored and displayed but NOT used as EV reference.
#
# Gotchas in API-Football's bookmaker registry — easy to confuse, verify before edits:
#   id 1  = 10Bet  (NOT Bet365)
#   id 6  = Bwin   (NOT Betway — similar prefix; this was the prior bug)
#   id 16 = Unibet
#   id 24 = Betway — in registry but API returns 0 rows for our fixtures; removed.
BOOKMAKER_IDS: set[int] = {4, 8, 11, 7}   # Pinnacle, Bet365, 1xBet, William Hill
BOOKMAKER_NAMES: dict[int, str] = {4: "Pinnacle", 8: "Bet365", 11: "1xBet", 7: "William Hill"}

# Sharp (low-margin) bookmakers used as the reference price for edge/EV calculation.
# When any of these have odds for a market, their price is used instead of
# the naive "best available" — preventing inflated EV from soft-book outliers.
SHARP_BOOKMAKER_NAMES: frozenset = frozenset({"Pinnacle", "Bet365"})

# Target bookmakers — where the user actually places bets.
# Their odds are shown in the UI instead of the sharp-book price, so the user
# sees exactly what they'll get at their bookmaker.  EV/edge math is unaffected
# (still anchored to SHARP_BOOKMAKER_NAMES).  If no target bookmaker has odds
# for a given market, the display falls back to the best sharp price.
#
# William Hill is the closest available proxy to the African regional bookmakers
# the user actually uses (Betpawa MW, 888bets MW, Premier Bet MW, Moors Bet).
# Those are not covered by API-Football.  The odds discount setting in the UI
# lets the user apply an additional % haircut on top of what William Hill offers.
TARGET_BOOKMAKER_NAMES: frozenset = frozenset({"William Hill"})

# Hard cap on odds pages fetched per sync. Each page ≈ 10 fixtures, 1 API request.
# Default 3 (~30 fixtures) is far too low for busy days (400+ fixtures), which left
# most fixtures — including the ones we actually want — with no odds and therefore no
# signals. Tunable via MAX_ODDS_PAGES env so coverage can be raised against quota.
MAX_ODDS_PAGES = int(os.getenv("MAX_ODDS_PAGES", "3"))

# Cache location. Defaults to backend/.cache for local dev, but on Fly it MUST point
# at the persistent /data volume (set API_CACHE_DIR) — otherwise every redeploy wipes
# the cache and forces a full re-pull (and on quota-limited days, partial odds).
_CACHE_DIR = Path(os.getenv("API_CACHE_DIR") or str(Path(__file__).resolve().parents[2] / ".cache" / "api_football"))
_HISTORICAL_TTL = timedelta(days=30)
# 8h keeps the file cache valid through two sync cycles (max gap is ~8h:
# 22:01→04:00 = 5h59m; 04:00→08:00 = 4h) so odds data survives even when
# a live re-fetch fails (free-plan season restriction, quota, network error).
# Must exceed STALE_MIN_AGE_HOURS (default 4h) so the cache is still readable
# when DB snapshots are classified stale and live re-fetch is attempted.
_TODAY_TTL = timedelta(hours=8)
_FIXTURE_TTL = timedelta(minutes=20)

# In-memory quota snapshot — updated after every live API call.
# API-Football resets at midnight UTC.
_quota: dict[str, int | None] = {"limit": None, "remaining": None}


def get_quota_info() -> dict[str, int | None]:
    """Return the last-seen API-Football quota (limit / remaining)."""
    return dict(_quota)


def _update_quota(headers: "httpx.Headers") -> None:
    """Extract quota counters from response headers and cache them."""
    try:
        limit     = headers.get("x-ratelimit-requests-limit")
        remaining = headers.get("x-ratelimit-requests-remaining")
        if limit is not None:
            _quota["limit"]     = int(limit)
        if remaining is not None:
            _quota["remaining"] = int(remaining)
            logger.debug("API-Football quota: %s / %s remaining", remaining, limit)
            if _quota["remaining"] is not None and _quota["remaining"] <= 5:
                logger.warning(
                    "API-Football quota critically low: %d requests remaining",
                    _quota["remaining"],
                )
    except Exception:
        pass


def _headers() -> dict[str, str]:
    if not settings.api_football_key:
        raise RuntimeError(
            "API_FOOTBALL_KEY not set. Copy backend/.env.example to backend/.env and add your key."
        )
    return {"x-apisports-key": settings.api_football_key}


def _cache_key(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def _cache_file(path: str) -> Path:
    return _CACHE_DIR / f"{_cache_key(path)}.json"


def _cache_ttl_for_path(path: str) -> timedelta:
    parsed = urlparse(path)
    params = parse_qs(parsed.query)

    if "fixture" in params:
        return _FIXTURE_TTL

    date_values = params.get("date")
    if date_values:
        try:
            req_date = date.fromisoformat(date_values[0])
            return _TODAY_TTL if req_date >= date.today() else _HISTORICAL_TTL
        except ValueError:
            pass
    return _TODAY_TTL


def _read_cache(path: str, *, allow_stale: bool = False) -> dict[str, Any] | None:
    cache_path = _cache_file(path)
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(payload["cached_at"])
        age = datetime.now(timezone.utc) - cached_at
        if allow_stale or age <= _cache_ttl_for_path(path):
            return payload["payload"]
    except Exception:
        return None
    return None


def _write_cache(path: str, payload: dict[str, Any]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = _cache_file(path)
        cache_path.write_text(
            json.dumps(
                {
                    "path": path,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "payload": payload,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        # Cache writes should never break live pulls.
        return


_MAX_RETRIES = 3
_RETRY_DELAYS = [2, 5, 10]  # seconds between attempts on 429


async def _get(path: str, *, force: bool = False) -> dict[str, Any]:
    if not force:
        cached_payload = _read_cache(path)
        if cached_payload is not None:
            return cached_payload

    # Quota guard — refuse to make a live call when we know we're at zero.
    if _quota["remaining"] is not None and _quota["remaining"] <= 0:
        logger.error(
            "API-Football quota exhausted (%d remaining) — skipping live call for %s",
            _quota["remaining"], path,
        )
        stale = _read_cache(path, allow_stale=True)
        if stale is not None:
            return stale
        raise RuntimeError("API quota exhausted and no stale cache available")

    for attempt in range(_MAX_RETRIES):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
            try:
                resp = await client.get(path, headers=_headers())
                _update_quota(resp.headers)
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("errors"):
                    errs = payload["errors"]
                    if isinstance(errs, dict) and errs:
                        raise RuntimeError(str(errs))
                _write_cache(path, payload)
                return payload
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    if attempt < _MAX_RETRIES - 1:
                        delay = _RETRY_DELAYS[attempt]
                        logger.warning(
                            "API-Football 429 (rate limit) on %s — retrying in %ds (attempt %d/%d)",
                            path, delay, attempt + 1, _MAX_RETRIES,
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.warning(
                            "API-Football 429 (rate limit) on %s — all retries exhausted, "
                            "falling back to stale cache",
                            path,
                        )
                        stale = _read_cache(path, allow_stale=True)
                        if stale is not None:
                            return stale
                        raise
                # Non-429 HTTP error — fall through to generic handler
                logger.warning("_get: request failed for %s: %s", path, exc)
                stale_payload = _read_cache(path, allow_stale=True)
                if stale_payload is not None:
                    return stale_payload
                raise
            except Exception as exc:
                logger.warning("_get: request failed for %s: %s", path, exc)
                stale_payload = _read_cache(path, allow_stale=True)
                if stale_payload is not None:
                    return stale_payload
                raise


async def fetch_fixtures(date_str: str, *, force: bool = False) -> list[dict[str, Any]]:
    """Fetch all fixtures for a date. Returns normalised dicts.  1 API call.

    force=True bypasses the file cache entirely — use this when re-syncing a past
    date whose fixtures may have been cached with stale NS/1H/2H statuses.
    """
    payload = await _get(f"/fixtures?date={date_str}", force=force)
    rows: list[dict[str, Any]] = []
    for item in payload.get("response", []):
        row = _parse_fixture_row(item)
        # Add date-level fields not needed in the single-fixture path
        fixture = item.get("fixture", {})
        league  = item.get("league", {})
        dt = row.get("kickoff_at")
        row["event_date"] = dt.date() if dt else None
        row["league_id"]  = league.get("id")
        row["season"]     = league.get("season")
        row.setdefault("home_team", "Home")
        row.setdefault("away_team", "Away")
        rows.append(row)
    return rows


def _parse_bookmakers(entry: dict, now: datetime) -> list[dict[str, Any]]:
    """
    Extract flat market-snapshot rows from one response entry.

    The /odds endpoint (without a bookmaker filter) embeds ALL bookmakers in each
    fixture entry under the "bookmakers" key:
        {"id": 1, "name": "Bet365", "bets": [...]}

    We filter to BOOKMAKER_IDS and use the API-provided name — no hardcoded mapping
    needed, so adding a new bookmaker is just adding its ID to BOOKMAKER_IDS.
    """
    external_id = entry.get("fixture", {}).get("id")
    rows: list[dict[str, Any]] = []

    found_bookie_ids = {bk.get("id") for bk in entry.get("bookmakers", [])}
    missing = BOOKMAKER_IDS - found_bookie_ids
    if missing:
        logger.debug(
            "Expected bookmakers missing from API response for fixture %s: %s",
            external_id, missing,
        )

    for bk in entry.get("bookmakers", []):
        bk_id   = bk.get("id")
        bk_name = bk.get("name", "Unknown")

        if bk_id not in BOOKMAKER_IDS:
            continue  # skip bookmakers we don't track

        for bet in bk.get("bets", []):
            market_type = (bet.get("name") or "Unknown").strip()
            for option in bet.get("values", []):
                try:
                    odds = float(option.get("odd"))
                except (TypeError, ValueError):
                    continue
                if odds <= 1.0:
                    continue
                rows.append({
                    "external_fixture_id": external_id,
                    "bookmaker":       bk_name,
                    "market_type":     market_type,
                    "selection_name":  str(option.get("value") or "").strip(),
                    "odds":            odds,
                    "pulled_at":       now,
                })
    return rows


async def fetch_markets(date_str: str) -> list[dict[str, Any]]:
    """
    Fetch all market odds for all fixtures on a date.  Up to MAX_ODDS_PAGES API calls
    (previously up to 2× that due to the per-bookmaker loop).

    Returns flat list of market_snapshot rows:
      {external_fixture_id, bookmaker, market_type, selection_name, odds, pulled_at}
    """
    now   = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []

    # Single call for page 1 — tells us the total page count.
    first = await _get(f"/odds?date={date_str}&page=1")
    total_pages = min(first.get("paging", {}).get("total", 1), MAX_ODDS_PAGES)

    payloads = [first]
    for page in range(2, total_pages + 1):
        payloads.append(await _get(f"/odds?date={date_str}&page={page}"))

    for payload in payloads:
        for entry in payload.get("response", []):
            rows.extend(_parse_bookmakers(entry, now))

    return rows


async def fetch_markets_by_leagues(
    date_str: str,
    league_seasons: list[tuple[int, int]],
) -> list[dict[str, Any]]:
    """
    Fetch market odds for specific leagues, one API call per (league_id, season) pair.

    This is the quota-efficient path for free-plan API keys: instead of fetching
    /odds?date=X&page=N (capped at 3 pages = ~30 random fixtures), we fetch
    /odds?league=L&season=S&date=X for each league we care about. Each league's
    data fits on page 1 (1–10 fixtures/day), so the free-plan page cap never bites.

    Returns flat list of market_snapshot rows identical to fetch_markets().
    """
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for league_id, season in league_seasons:
        path = f"/odds?league={league_id}&season={season}&date={date_str}"
        try:
            payload = await _get(path)
        except Exception as exc:
            logger.warning(
                "fetch_markets_by_leagues: league=%s season=%s date=%s failed: %s",
                league_id, season, date_str, exc,
            )
            continue
        for entry in payload.get("response", []):
            rows.extend(_parse_bookmakers(entry, now))
    return rows


async def fetch_fixture_by_id(ext_id: int, *, force: bool = False) -> dict[str, Any] | None:
    """
    Fetch a single fixture by its API-Football ID and return a normalised dict
    with current status and score.

    force=True bypasses the file cache entirely — essential when a fixture was
    cached as NS/2H/HT and we need its actual FT result.  The fresh response is
    written back to cache so subsequent reads get the correct data.
    """
    path = f"/fixtures?id={ext_id}"
    if not force:
        cached = _read_cache(path)
        if cached is not None:
            items = cached.get("response", [])
            if items:
                return _parse_fixture_row(items[0])
            return None

    # Bypass cache: fetch live, then overwrite the cache file with fresh data.
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
        try:
            resp = await client.get(path, headers=_headers())
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("errors"):
                errs = payload["errors"]
                if isinstance(errs, dict) and errs:
                    raise RuntimeError(str(errs))
            _write_cache(path, payload)   # overwrite stale cache
            items = payload.get("response", [])
            if not items:
                return None
            return _parse_fixture_row(items[0])
        except Exception as exc:
            logger.warning("fetch_fixture_by_id: ext_id=%s failed: %s", ext_id, exc)
            # Fall back to stale cache rather than crash.
            stale = _read_cache(path, allow_stale=True)
            if stale:
                items = stale.get("response", [])
                if items:
                    return _parse_fixture_row(items[0])
            return None


def _parse_fixture_row(item: dict[str, Any]) -> dict[str, Any]:
    """Normalise one /fixtures response entry into a flat dict."""
    fixture  = item.get("fixture", {})
    goals    = item.get("goals", {})
    teams    = item.get("teams", {})
    league   = item.get("league", {})
    score    = item.get("score", {})
    halftime = score.get("halftime", {})
    try:
        dt = datetime.fromisoformat(fixture["date"].replace("Z", "+00:00"))
    except Exception:
        dt = None
    return {
        "external_fixture_id": fixture.get("id"),
        "status":        fixture.get("status", {}).get("short"),
        "home_score":    goals.get("home"),
        "away_score":    goals.get("away"),
        "home_score_ht": halftime.get("home"),   # None until half-time
        "away_score_ht": halftime.get("away"),
        "home_team":     teams.get("home", {}).get("name"),
        "away_team":     teams.get("away", {}).get("name"),
        "country":       league.get("country"),
        "league":        league.get("name"),
        "kickoff_at":    dt,
    }


async def fetch_fixture_odds(fixture_id: int) -> list[dict[str, Any]]:
    """
    Fetch all odds for a specific fixture (used in backtester / deep-dive refresh).
    Single API call — all bookmakers returned together, filtered client-side.
    """
    now  = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    try:
        payload = await _get(f"/odds?fixture={fixture_id}")
    except Exception:
        return rows
    for entry in payload.get("response", []):
        rows.extend(_parse_bookmakers(entry, now))
    return rows


async def fetch_fixture_statistics_corners(ext_fixture_id: int) -> dict | None:
    """
    Fetch corner kick counts for a completed fixture from /fixtures/statistics.
    Returns {"home_corners": N, "away_corners": M} or None if unavailable.
    Responses are file-cached (same mechanism as other endpoints) so re-fetching
    a settled fixture costs zero API calls after the first successful pull.
    """
    try:
        payload = await _get(f"/fixtures/statistics?fixture={ext_fixture_id}")
    except Exception:
        return None

    teams_data = payload.get("response", [])
    if len(teams_data) < 2:
        return None

    def _extract_corners(team_entry: dict) -> int | None:
        for stat in team_entry.get("statistics", []):
            if str(stat.get("type", "")).lower() in ("corner kicks", "corners"):
                val = stat.get("value")
                try:
                    return int(val) if val is not None else None
                except (TypeError, ValueError):
                    return None
        return None

    home_c = _extract_corners(teams_data[0])
    away_c = _extract_corners(teams_data[1])
    if home_c is None or away_c is None:
        return None
    return {"home_corners": home_c, "away_corners": away_c}

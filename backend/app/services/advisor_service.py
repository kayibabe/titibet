"""
advisor_service.py — Multi-model AI advisory council with provider chain fallback.

Provider priority per advisor (first configured key with available quota wins):
  1. Anthropic Claude  — TITIBET_CLAUDE_KEY      — highest quality, paid
  2. Google Gemini     — GEMINI_API_KEY         — free, no card required (aistudio.google.com)
  3. Cerebras          — CEREBRAS_API_KEY       — free, very fast Llama inference
  4. Groq              — GROQ_API_KEY           — free Llama/Mixtral, daily limits
  5. Mistral           — MISTRAL_API_KEY        — free open-mistral-nemo

Each provider returns None on billing/quota exhaustion so the next is tried
transparently. Rate-limit errors are returned as soft errors (shown in UI).
Configure as many keys as you like — more keys = more resilience.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date
from typing import Any

import anthropic
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Signal, Fixture
from app.services.performance_intelligence import PerformanceWeights, compute_performance_weights

logger = logging.getLogger(__name__)

# ── Provider endpoints ────────────────────────────────────────────────────────

GROQ_URL      = "https://api.groq.com/openai/v1/chat/completions"
CEREBRAS_URL  = "https://api.cerebras.ai/v1/chat/completions"
MISTRAL_URL   = "https://api.mistral.ai/v1/chat/completions"
GEMINI_URL    = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# ── Advisor definitions ───────────────────────────────────────────────────────
# Each advisor lists a model per provider. Providers are tried in chain order.

ADVISORS: list[dict] = [
    {
        "id":    "scout",
        "name":  "The Scout",
        "role":  "Signal validation & match context",
        "emoji": "🔭",
        "models": {
            # Scout does the most complex per-match statistical reasoning — use the
            # same quality tier as Strategist/Skeptic.
            "claude":   "claude-sonnet-4-6",
            "gemini":   "gemini-2.0-flash",
            "cerebras": "llama3.3-70b",
            "groq":     "llama-3.3-70b-versatile",
            "mistral":  "mistral-small-latest",
        },
        "system": (
            "You are a professional football betting analyst specialising in statistical signal validation. "
            "You receive signals from a dual-engine system (Bayesian consensus + Poisson probability model) "
            "alongside historical match data (form, head-to-head, team stats). "
            "Your job: for each match, assess whether the contextual data SUPPORTS or UNDERMINES the signal. "
            "Do not just restate the numbers — add genuine analytical insight. "
            "Focus on 1-2 sentences of real observation per match. "
            "Identify the strongest 2-3 picks overall. "
            "Always respond with valid JSON only. No markdown, no prose outside the JSON."
        ),
        "task": (
            "Analyse each signal. Return JSON with this exact shape:\n"
            '{"verdict":"Strong"|"Mixed"|"Caution",'
            '"top_picks":[{"home_team":"...","away_team":"...","market":"...","reason":"..."},...],'
            '"warnings":["warning",...],'
            '"summary":"2-3 sentence paragraph"}'
        ),
    },
    {
        "id":    "strategist",
        "name":  "The Strategist",
        "role":  "Portfolio construction & value ranking",
        "emoji": "♟️",
        "models": {
            "claude":   "claude-sonnet-4-6",
            "gemini":   "gemini-2.0-flash",            # stronger reasoning
            "cerebras": "llama3.3-70b",
            "groq":     "llama-3.3-70b-versatile",
            "mistral":  "mistral-small-latest",
        },
        "system": (
            "You are a senior football betting portfolio analyst. "
            "You receive a batch of signals from an AI dual-engine system and related match data. "
            "Your job is to assess the PORTFOLIO — not each pick in isolation. "
            "Look for: correlated outcomes (e.g. multiple Over 2.5 bets in same league), "
            "concentration risk, best risk-adjusted combinations, and which signals have the "
            "highest expected value relative to their risk. "
            "Think in terms of a 1-5 unit staking model. "
            "Always respond with valid JSON only. No markdown, no prose outside the JSON."
        ),
        "task": (
            "Assess the full portfolio. Return JSON with this exact shape:\n"
            '{"verdict":"Strong"|"Mixed"|"Caution",'
            '"top_picks":[{"home_team":"...","away_team":"...","market":"...","reason":"..."},...],'
            '"warnings":["correlation/concentration note",...],'
            '"summary":"2-3 sentence paragraph on the day\'s overall opportunity"}'
        ),
    },
    {
        "id":    "skeptic",
        "name":  "The Skeptic",
        "role":  "Contrarian risk & red-flag analysis",
        "emoji": "🧐",
        "models": {
            "claude":   "claude-sonnet-4-6",
            "gemini":   "gemini-2.0-flash",
            "cerebras": "llama3.3-70b",
            "groq":     "llama-3.1-8b-instant",       # 8B model = separate rate-limit pool from 70B
            "mistral":  "mistral-small-latest",
        },
        "system": (
            "You are a contrarian football betting analyst — your job is to find reasons NOT to bet. "
            "You receive signals from an AI model system and related match data. "
            "Look for: thin bookmaker coverage suggesting model may be fitting noise, "
            "team motivational factors (dead rubbers, rotation risk, fixture congestion), "
            "H2H patterns that contradict the signal, misleading form (e.g. wins against weak opposition), "
            "markets with historically low model accuracy. "
            "You are the last line of defence before money goes down. Be sceptical but fair — "
            "if a signal genuinely looks solid, say so. "
            "Always respond with valid JSON only. No markdown, no prose outside the JSON."
        ),
        "task": (
            "Find risks and red flags. Return JSON with this exact shape:\n"
            '{"verdict":"Strong"|"Mixed"|"Caution",'
            '"top_picks":[{"home_team":"...","away_team":"...","market":"...","reason":"..."},...],'
            '"warnings":["specific red flag",...],'
            '"summary":"2-3 sentence contrarian assessment"}'
        ),
    },
]

# Provider chain order — tried in this sequence, skipped if key is absent
PROVIDER_CHAIN = ["claude", "gemini", "cerebras", "groq", "mistral"]

# Billing/quota error phrases that trigger fallback to the next provider
_QUOTA_PHRASES = (
    "credit balance is too low",
    "insufficient credits",
    "quota exceeded",
    "exceeded your current quota",
    "billing",
    "payment required",
    "out of tokens",
)


# ── Context builder ───────────────────────────────────────────────────────────

def _build_context(rows: list, match_infos: dict, perf_weights: "PerformanceWeights | None" = None) -> str:
    lines: list[str] = [f"=== TODAY'S SIGNALS ({len(rows)} matches) ===\n"]
    for i, (sig, fix) in enumerate(rows, 1):
        info = match_infos.get(sig.fixture_id, {})
        hs   = info.get("home_stats",      {})
        as_  = info.get("away_stats",      {})
        h2h  = info.get("h2h",             [])
        hh   = info.get("home_highlights", [])[:2]
        ah   = info.get("away_highlights", [])[:2]

        ev_pct = None
        if sig.bayesian_prob and sig.bayesian_best_odd:
            ev_pct = round((sig.bayesian_prob * sig.bayesian_best_odd - 1.0) * 100, 1)

        line = (
            f"[{i}] {fix.home_team} vs {fix.away_team}"
            f" | {fix.league or 'Unknown League'} | Tier {fix.league_tier or '?'}\n"
            f"Signal: {sig.market} | Confidence: {sig.dual_confidence}"
            f" | Agreement: {sig.dual_agreement} | Quality: {sig.dual_quality_score}\n"
        )
        if sig.bayesian_prob is not None:
            line += (
                f"Bayesian: Prob={round(sig.bayesian_prob * 100, 1)}%"
                f" | Best Odd: {sig.bayesian_best_odd} ({sig.bayesian_bookmaker})"
                f" | EV: {ev_pct}% | Books: {sig.bayesian_bookmaker_count}\n"
            )
        if sig.poisson_prob is not None:
            line += (
                f"Poisson: lH={sig.poisson_lambda_h} lA={sig.poisson_lambda_a}"
                f" | Grade: {sig.poisson_grade} | Strong: {sig.poisson_rule_strong}\n"
            )
        if hs:
            hform = " ".join(hs.get("form", []))
            aform = " ".join(as_.get("form", []))
            line += (
                f"Form: {fix.home_team} [{hform}] | {fix.away_team} [{aform}]\n"
                f"Stats: {fix.home_team} PPG={hs.get('ppg')} AvgGF={hs.get('avg_goals_for')}"
                f" AvgGA={hs.get('avg_goals_against')}"
                f" | {fix.away_team} PPG={as_.get('ppg')} AvgGF={as_.get('avg_goals_for')}"
                f" AvgGA={as_.get('avg_goals_against')}\n"
            )
        if h2h:
            n     = len(h2h)
            hw    = sum(
                1 for m in h2h
                if (m["home_team"] == fix.home_team and m["home_score"] > m["away_score"])
                or (m["away_team"] == fix.home_team and (m.get("away_score") or 0) > (m.get("home_score") or 0))
            )
            draws = sum(1 for m in h2h if m["home_score"] == m["away_score"])
            line += f"H2H (last {n}): {fix.home_team} {hw}W {draws}D {n-hw-draws}L\n"
        highlights = [h.replace("**", "") for h in (hh + ah) if h]
        if highlights:
            line += "Trends: " + " | ".join(highlights[:3]) + "\n"
        lines.append(line)

    if perf_weights and perf_weights.by_confidence_market:
        slices = [
            (k, v) for k, v in perf_weights.by_confidence_market.items()
            if v.samples >= 15
        ]
        if slices:
            slices.sort(key=lambda kv: kv[1].win_rate, reverse=True)
            best = slices[:5]
            worst = slices[-5:][::-1]
            perf_lines = ["\n=== HISTORICAL PERFORMANCE CONTEXT ==="]
            perf_lines.append("Top 5 performing (confidence, market) slices:")
            for (conf, mkt), sl in best:
                perf_lines.append(
                    f"  {conf} · {mkt}: {round(sl.win_rate * 100, 1)}% win rate"
                    f" | ROI {round(sl.roi * 100, 1)}% | n={sl.samples}"
                )
            perf_lines.append("Bottom 5 performing slices:")
            for (conf, mkt), sl in worst:
                perf_lines.append(
                    f"  {conf} · {mkt}: {round(sl.win_rate * 100, 1)}% win rate"
                    f" | ROI {round(sl.roi * 100, 1)}% | n={sl.samples}"
                )
            lines.append("\n".join(perf_lines))

    return "\n".join(lines)


def _build_skeptic_extras(rows: list) -> str:
    """
    AI-3: Extra context block tailored for the Skeptic advisor.
    Surfaces market-vs-model divergence, thin coverage, odds drift, and other
    red flags that a contrarian analyst should interrogate first.
    """
    lines = ["\n=== SKEPTIC FOCUS: DIVERGENCE & RISK FLAGS ==="]
    found_any = False

    for sig, fix in rows:
        flags: list[str] = []

        # Short-odds favourites flagged High confidence — thin margin for error
        if sig.dual_confidence == "High" and sig.bayesian_best_odd is not None:
            if sig.bayesian_best_odd < 1.50:
                flags.append(
                    f"SHORT ODDS ({sig.bayesian_best_odd:.2f}) — 'High' confidence on a heavy favourite; "
                    f"any model calibration error could flip this to negative EV"
                )

        # Odds lengthened since open = market moving against this pick
        if sig.odds_drift_pct is not None and sig.odds_drift_pct > 4.0:
            flags.append(
                f"ODDS DRIFTED +{sig.odds_drift_pct:.1f}% since open — market is moving AGAINST this signal; "
                f"sharp money disagrees"
            )

        # Thin bookmaker coverage = may be fitting noise
        bc = sig.bayesian_bookmaker_count
        if bc is not None and bc <= 1:
            flags.append(
                "THIN COVERAGE (1 book) — signal rests on a single bookmaker's price; "
                "more likely to be noise than a genuine edge"
            )

        # Large model-to-market probability gap
        if sig.bayesian_prob is not None and sig.bayesian_best_odd is not None:
            implied = 1.0 / sig.bayesian_best_odd
            gap = sig.bayesian_prob - implied
            if gap > 0.15:
                flags.append(
                    f"MODEL OVERCONFIDENT +{gap:.1%} vs market — our model assigns considerably higher "
                    f"probability than the bookmaker; verify this isn't a systematic bias"
                )
            elif gap < -0.10:
                flags.append(
                    f"MARKET OVERCONFIDENT {gap:.1%} vs model — bookmaker is more bullish than our model; "
                    f"model may be correctly cautious or structurally miscalibrated for this market"
                )

        # Engine contradiction despite overall label
        if sig.contradiction:
            flags.append(
                "ENGINE CONTRADICTION — Bayesian and Poisson engines disagree; "
                "one of them is wrong and we don't know which"
            )

        # Single-engine signals: no cross-validation
        if sig.dual_agreement == "Bayesian Only":
            flags.append(
                "BAYESIAN ONLY — Poisson goal model does not confirm; "
                "check whether team-level scoring patterns support this market"
            )
        elif sig.dual_agreement == "Poisson Only":
            flags.append(
                "POISSON ONLY — bookmaker markets do not reflect model probability; "
                "either the market knows something the model doesn't, or there's a pricing anomaly"
            )

        if flags:
            found_any = True
            lines.append(f"\n{fix.home_team} vs {fix.away_team} | {sig.market}:")
            for f in flags:
                lines.append(f"  ⚠  {f}")

    if not found_any:
        lines.append(
            "No major divergence flags detected. Signals appear broadly consistent with "
            "market pricing. Focus your analysis on motivation, fixture congestion, and "
            "team news factors not captured in the model data."
        )

    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    # First attempt: parse as-is
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # Second attempt: strip markdown code fences then parse
    stripped = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped.strip())
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "Failed to parse LLM JSON response (first 200 chars): %s", text[:200]
        )
        return {
            "error": "parse_error",
            "verdict": "Mixed",
            "top_picks": [],
            "warnings": [],
            "summary": "Advisor returned a malformed response.",
        }


def _err(code: str, summary: str) -> dict[str, Any]:
    return {"error": code, "verdict": "Mixed", "top_picks": [], "warnings": [], "summary": summary}


def _is_quota_error(message: str) -> bool:
    msg = message.lower()
    return any(p in msg for p in _QUOTA_PHRASES)


# ── Provider callers ──────────────────────────────────────────────────────────
# Each returns:
#   dict  — success or soft error (rate limit / bad response) — stop the chain
#   None  — billing/quota exhausted — try the next provider


async def _call_claude(advisor: dict, context: str, api_key: str) -> dict | None:
    try:
        client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url="https://api.anthropic.com",  # ignore ANTHROPIC_BASE_URL from Claude Code env
        )
        msg = await client.messages.create(
            model=advisor["models"]["claude"],
            max_tokens=1024,
            system=advisor["system"],
            messages=[
                {"role": "user",      "content": f"{advisor['task']}\n\n{context}"},
                {"role": "assistant", "content": "{"},   # prefill → guaranteed JSON
            ],
        )
        return _extract_json("{" + msg.content[0].text)
    except anthropic.APIError as exc:
        body_msg = ""
        if isinstance(exc.body, dict):
            body_msg = exc.body.get("error", {}).get("message", "")
        if _is_quota_error(body_msg):
            logger.info("Claude quota — falling back (advisor=%s)", advisor["id"])
            return None
        logger.warning("Claude error for %s: HTTP %s — %s", advisor["id"], exc.status_code, body_msg)
        return _err(f"claude_{exc.status_code}", f"Claude error: {body_msg[:120] or str(exc)[:120]}")
    except anthropic.AuthenticationError:
        return _err("claude_auth", "Anthropic API key is invalid.")
    except anthropic.RateLimitError:
        return _err("claude_429", "Claude rate limit — retry shortly.")
    except json.JSONDecodeError:
        return _err("claude_json", "Claude returned malformed JSON.")
    except Exception as exc:
        logger.warning("Claude failed for %s: %s", advisor["id"], exc)
        return _err(type(exc).__name__, "Claude advisor request failed.")


async def _call_gemini(advisor: dict, context: str, api_key: str) -> dict | None:
    """
    Google Gemini via REST — no extra SDK needed beyond httpx.
    Uses responseMimeType=application/json for clean structured output.
    Free tier: 15 RPM, 1 million TPD on gemini-2.0-flash-lite.
    Get key: aistudio.google.com/apikey
    """
    model = advisor["models"]["gemini"]
    url   = GEMINI_URL.format(model=model)
    payload = {
        "system_instruction": {"parts": [{"text": advisor["system"]}]},
        "contents": [{"role": "user", "parts": [{"text": f"{advisor['task']}\n\n{context}"}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature":      0.25,
            "maxOutputTokens":  1024,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload, params={"key": api_key})
            if resp.status_code == 429:
                body = resp.text.lower()
                logger.info("Gemini 429 for %s: %s", advisor["id"], resp.text[:200])
                if _is_quota_error(body) or "quota" in body:
                    return None
                return _err("gemini_429", "Gemini rate limit — retry shortly.")
            if resp.status_code in (400, 403):
                try:
                    body = resp.json()
                except Exception:
                    body = {}
                msg = body.get("error", {}).get("message", resp.text[:200])
                logger.info("Gemini %s for %s: %s", resp.status_code, advisor["id"], msg[:200])
                if _is_quota_error(msg) or resp.status_code == 403:
                    return None
                return _err(f"gemini_{resp.status_code}", f"Gemini error: {msg[:120]}")
            resp.raise_for_status()
            data    = resp.json()
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            return _extract_json(content)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status >= 500:
            logger.info("Gemini server error %s for %s — falling back", status, advisor["id"])
            return None
        logger.warning("Gemini HTTP %s for %s: %s", status, advisor["id"], exc.response.text[:200])
        return _err(f"gemini_{status}", f"Gemini API error {status}.")
    except json.JSONDecodeError:
        return _err("gemini_json", "Gemini returned malformed JSON.")
    except Exception as exc:
        logger.warning("Gemini failed for %s: %s", advisor["id"], exc)
        return _err(type(exc).__name__, "Gemini advisor request failed.")


async def _call_openai_compat(
    advisor:  dict,
    context:  str,
    api_key:  str,
    base_url: str,
    provider: str,          # "groq" | "cerebras" | "mistral"
) -> dict | None:
    """
    Generic caller for OpenAI-compatible chat completions endpoints.
    Covers Groq, Cerebras, and Mistral (all support response_format json_object).
    """
    model = advisor["models"][provider]
    payload: dict = {
        "model":           model,
        "messages": [
            {"role": "system", "content": advisor["system"]},
            {"role": "user",   "content": f"{advisor['task']}\n\n{context}"},
        ],
        "temperature":     0.25,
        "max_tokens":      1024,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                base_url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 429:
                body = resp.text.lower()
                if _is_quota_error(body) or "tokens per day" in body or "tpd" in body:
                    logger.info("%s daily quota — falling back (advisor=%s)", provider, advisor["id"])
                    return None
                return _err(f"{provider}_429", f"{provider.title()} rate limit — retry shortly.")
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return _extract_json(content)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        body   = exc.response.text.lower()
        if status >= 500 or status in (404, 400):
            # 400 from Groq/Cerebras typically means the model name is invalid
            # (deprecated or renamed) — fall back to the next provider rather than
            # hard-failing so a single stale model name doesn't break the chain.
            logger.info("%s %s for %s — falling back (%s)", provider, status, advisor["id"], exc.response.text[:120])
            return None
        if _is_quota_error(body):
            return None
        logger.warning("%s HTTP %s for %s: %s", provider, status, advisor["id"], exc.response.text[:200])
        return _err(f"{provider}_{status}", f"{provider.title()} API error {status}.")
    except json.JSONDecodeError:
        return _err(f"{provider}_json", f"{provider.title()} returned malformed JSON.")
    except Exception as exc:
        logger.warning("%s failed for %s: %s", provider, advisor["id"], exc)
        return _err(type(exc).__name__, f"{provider.title()} advisor request failed.")


# ── Per-advisor provider chain ────────────────────────────────────────────────

async def _call_advisor(
    advisor: dict,
    context: str,
    settings,
    extra_context: str = "",
) -> tuple[str, dict[str, Any]]:
    """
    Try each configured provider in PROVIDER_CHAIN order.
    Returns (model_label, result_dict).
    ``extra_context`` is appended to the base context string (used by the Skeptic).
    """
    full_context = context + extra_context if extra_context else context
    keys = {
        "claude":   settings.titibet_claude_key,
        "gemini":   settings.gemini_api_key,
        "cerebras": settings.cerebras_api_key,
        "groq":     settings.groq_api_key,
        "mistral":  settings.mistral_api_key,
    }
    compat_urls = {
        "groq":     GROQ_URL,
        "cerebras": CEREBRAS_URL,
        "mistral":  MISTRAL_URL,
    }

    for provider in PROVIDER_CHAIN:
        key = keys.get(provider, "")
        if not key:
            continue

        model_label = advisor["models"][provider]
        logger.debug("Trying %s/%s for %s", provider, model_label, advisor["id"])

        if provider == "claude":
            result = await _call_claude(advisor, full_context, key)
        elif provider == "gemini":
            result = await _call_gemini(advisor, full_context, key)
        else:
            result = await _call_openai_compat(advisor, full_context, key, compat_urls[provider], provider)

        if result is not None:
            return model_label, result
        # None = quota exhausted → try next provider

    return "none", _err("no_provider", "All configured AI providers are at quota. Add more keys to .env.")


# ── Public entry point ────────────────────────────────────────────────────────

async def get_advisor_insights(
    db:           AsyncSession,
    target_date:  date,
    fixture_ids:  list[int] | None = None,
    current_user: Any | None = None,
) -> dict:
    """
    Orchestrate the AI advisory council for a given date.

    1. Load up to 12 High/Medium signals for the date.
    2. Fetch match info for up to 8 fixtures.
    3. Build a compact context string.
    4. Fire all three advisors concurrently — each runs its own provider chain.
    5. Return structured insights + metadata.
    """
    # Belt-and-suspenders subscription gate — the router also enforces this, but
    # a service-layer check ensures any future caller (e.g. internal tools) can't
    # bypass it accidentally.
    if current_user is not None:
        tier   = getattr(current_user, "tier", None)
        status = getattr(current_user, "subscription_status", None)
        if tier not in ("pro", "elite") or status != "active":
            return {
                "error":   "subscription_required",
                "message": "AI Advisor requires an active Pro or Elite subscription.",
            }

    settings = get_settings()

    configured_keys = [
        settings.titibet_claude_key,
        settings.gemini_api_key,
        settings.cerebras_api_key,
        settings.groq_api_key,
        settings.mistral_api_key,
    ]
    if not any(configured_keys):
        return {
            "configured": False,
            "message": (
                "AI advisors are disabled — no provider keys configured.\n"
                "Add at least one to backend/.env:\n"
                "  TITIBET_CLAUDE_KEY=sk-ant-...      (console.anthropic.com)\n"
                "  GEMINI_API_KEY=AIza...           (aistudio.google.com/apikey — free)\n"
                "  CEREBRAS_API_KEY=csk-...         (inference.cerebras.ai — free)\n"
                "  GROQ_API_KEY=gsk_...             (console.groq.com — free)\n"
                "  MISTRAL_API_KEY=...              (console.mistral.ai — free tier)"
            ),
        }

    # ── Load signals ──────────────────────────────────────────────────────────
    q = (
        select(Signal, Fixture)
        .join(Fixture, Signal.fixture_id == Fixture.id)
        .where(Fixture.event_date == target_date)
        .where(Signal.dual_confidence.in_(["High", "Medium"]))
        .where(Signal.dual_agreement.in_(["Both", "Bayesian Only", "Poisson Only"]))
        .order_by(Signal.dual_quality_score.desc().nullslast())
        .limit(12)
    )
    if fixture_ids:
        q = q.where(Signal.fixture_id.in_(fixture_ids))

    rows = (await db.execute(q)).all()
    if not rows:
        return {
            "configured": True,
            "insights":   [],
            "message":    "No High or Medium confidence signals found for this date.",
        }

    # ── Fetch match info (parallel, capped at 8 fixtures) ────────────────────
    from app.services.match_info import get_match_info

    unique_fixture_ids = list({fix.id for _, fix in rows})[:8]
    match_info_results = await asyncio.gather(
        *[get_match_info(db, fid) for fid in unique_fixture_ids],
        return_exceptions=True,
    )
    match_infos: dict[int, dict] = {}
    for fid, result in zip(unique_fixture_ids, match_info_results):
        if isinstance(result, dict):
            match_infos[fid] = result

    # ── Build context & call all advisors concurrently ────────────────────────
    try:
        perf_weights = await compute_performance_weights(db)
    except Exception:
        perf_weights = None
    context = _build_context(rows, match_infos, perf_weights)

    # AI-3: Build Skeptic-specific divergence extras (market vs model, thin coverage, drift)
    skeptic_extras = _build_skeptic_extras(rows)

    advisor_outputs = await asyncio.gather(
        *[
            _call_advisor(
                adv, context, settings,
                extra_context=skeptic_extras if adv["id"] == "skeptic" else "",
            )
            for adv in ADVISORS
        ]
    )

    # AI-3: Consensus verdict — aggregate across all three advisors
    _verdict_score = {"Strong": 2, "Mixed": 1, "Caution": 0}
    advisor_verdicts = [
        result.get("verdict", "Mixed")
        for _, result in advisor_outputs
        if isinstance(result, dict) and "verdict" in result
    ]
    if advisor_verdicts:
        avg_score = sum(_verdict_score.get(v, 1) for v in advisor_verdicts) / len(advisor_verdicts)
        consensus_verdict = "Strong" if avg_score >= 1.5 else ("Caution" if avg_score < 0.7 else "Mixed")
    else:
        consensus_verdict = "Mixed"

    return {
        "configured":       True,
        "date":             target_date.isoformat(),
        "matches_analysed": len(rows),
        "consensus_verdict": consensus_verdict,
        "advisors": [
            {
                "id":     adv["id"],
                "name":   adv["name"],
                "role":   adv["role"],
                "model":  model_label,
                "emoji":  adv["emoji"],
                "result": result,
            }
            for adv, (model_label, result) in zip(ADVISORS, advisor_outputs)
        ],
    }

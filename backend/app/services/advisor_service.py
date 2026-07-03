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
from datetime import date, datetime, timezone
from typing import Any

import anthropic
import httpx
from sqlalchemy import select, text
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

ACCA_BUILDER: dict = {
    "id":    "acca_builder",
    "name":  "Acca Builder",
    "role":  "Daily accumulator recommendation",
    "emoji": "🎟️",
    "models": {
        "claude":   "claude-sonnet-5",
        "gemini":   "gemini-2.0-flash",
        "cerebras": "llama3.3-70b",
        "groq":     "llama-3.3-70b-versatile",
        "mistral":  "mistral-small-latest",
    },
    "system": (
        "You are a specialist football accumulator analyst. "
        "You receive a pool of pre-screened signals ranked by dual-engine probability agreement. "
        "Your role is to select the best 3-5 legs. Optimise for: "
        "(1) Prefer legs where both Bayesian and Poisson engines agree (dual_agreement=Both) "
        "with probability ≥0.60. Fall back to single-engine signals only when the dual pool is thin. "
        "(2) League diversity — no more than 2 legs from the same league. "
        "(3) Combined decimal odds in the 3.0–20.0 range — enough reward without being a lottery. "
        "(4) Market diversity — avoid stacking the same market type (e.g. all Over 2.5). "
        "Avoid: the same team appearing more than once, any decimal leg odd above 3.5, "
        "and any signal where the contextual data raises red flags. "
        "For each leg use the Bayesian best_odd from the context; if missing, estimate from the probability. "
        "Set confidence to 'High' when you select ≥3 legs where both engines agree at ≥0.60 probability. "
        "Set confidence to 'Medium' for mixed pools. "
        "Always respond with valid JSON only — no markdown, no prose outside the JSON."
    ),
    "task": (
        "Select the best 3-5 legs for today's accumulator. "
        "Return JSON with this EXACT shape — no extra fields:\n"
        '{"legs":[{"home_team":"...","away_team":"...","market":"...","odd":1.75,"reason":"1-sentence justification"}],'
        '"rationale":"2-3 sentence explanation of why these legs combine well and what makes this acca compelling today",'
        '"confidence":"High"|"Medium"|"Low"}'
    ),
}

ADVISORS: list[dict] = [
    {
        "id":    "scout",
        "name":  "The Scout",
        "role":  "Signal validation & match context",
        "emoji": "🔭",
        "models": {
            # Scout does the most complex per-match statistical reasoning — use the
            # same quality tier as Strategist/Skeptic.
            "claude":   "claude-sonnet-5",
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
            "claude":   "claude-sonnet-5",
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
            "claude":   "claude-sonnet-5",
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
                f" | Books: {sig.bayesian_bookmaker_count}\n"
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
        # No assistant prefill — the Sonnet 4.6/5 family rejects it with a 400.
        # Thinking stays off: this is a fast structured-selection task and
        # thinking tokens would eat into max_tokens.
        msg = await client.messages.create(
            model=advisor["models"]["claude"],
            max_tokens=2048,
            thinking={"type": "disabled"},
            system=advisor["system"],
            messages=[
                {"role": "user", "content": f"{advisor['task']}\n\n{context}"},
            ],
        )
        text = next((b.text for b in msg.content if b.type == "text"), "")
        return _extract_json(text)
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


# ── Acca tracking helpers ─────────────────────────────────────────────────────

async def _is_acca_tracked(db: AsyncSession, target_date: date, uid: int) -> bool:
    """True when this user already has the day's AI acca in their tracker."""
    from app.models.bet import TrackedBet

    row = await db.scalar(
        select(TrackedBet.id).where(
            TrackedBet.source_rule_key == "acca_advisory",
            TrackedBet.event_date == target_date,
            TrackedBet.user_id == uid,
        )
    )
    return row is not None


async def _create_acca_bet(
    db:           AsyncSession,
    acca:         dict,
    target_date:  date,
    current_user: Any | None,
) -> bool:
    """
    Persist the AI acca as a single TrackedBet row (idempotent per user+date).
    Only called from the explicit track endpoint — viewing the advisory tab
    never creates a bet. Returns True if newly created, False if a row existed.
    """
    from sqlalchemy import select, or_
    from sqlalchemy.exc import IntegrityError
    from app.models.bet import TrackedBet

    legs = acca.get("legs", [])
    combined_odds = acca.get("combined_odds")
    if not legs or not combined_odds or combined_odds <= 1.0:
        return False

    uid: int | None = getattr(current_user, "id", None) if current_user else None

    # Tracking requires a logged-in user (the scheduled cache-warming job has none)
    if uid is None:
        return False

    # Dedup: one acca_advisory row per (user_id, event_date) — check this user only
    dup_q = select(TrackedBet).where(
        TrackedBet.source_rule_key == "acca_advisory",
        TrackedBet.event_date == target_date,
        TrackedBet.user_id == uid,
    )
    if await db.scalar(dup_q):
        return False  # already tracked

    leg_summary = "\n".join(
        f"{i+1}. {leg.get('home_team','')} vs {leg.get('away_team','')} · "
        f"{leg.get('market','')} @ {float(leg.get('odd') or 0):.2f}"
        for i, leg in enumerate(legs)
    )
    notes = json.dumps({"legs": legs, "leg_summary": leg_summary})

    bet = TrackedBet(
        user_id=uid,
        fixture_id=None,
        bookmaker="AI Acca",
        event_date=target_date,
        match_name=f"AI Acca · {len(legs)} leg{'s' if len(legs) != 1 else ''}",
        league=None,
        market_type="Accumulator",
        selection_name="Accumulator",
        odds=combined_odds,
        stake=50_000.0,
        source_rule_key="acca_advisory",
        source_rule_label="AI Acca of the Day",
        dual_confidence=acca.get("confidence"),
        notes=notes,
    )
    db.add(bet)
    try:
        await db.commit()
        return True
    except IntegrityError:
        await db.rollback()
        return False


async def auto_track_acca_legs(
    db:          AsyncSession,
    acca:        dict,
    target_date: date,
) -> int:
    """
    Create system-level TrackedBet rows (user_id=None) for each acca leg and
    one combined accumulator row.  Idempotent: skips any leg already tracked for
    this date (keyed on fixture_id + market_type).  Returns count of new rows.
    Called from the pre-sync and advisory-cache scheduler jobs.
    """
    from app.models.bet import TrackedBet

    legs = acca.get("legs", [])
    combined_odds = acca.get("combined_odds")
    if not legs or not combined_odds or combined_odds <= 1.0:
        return 0

    # Load existing system acca rows for this date to dedup
    existing_rows = list(
        (await db.execute(
            select(TrackedBet.fixture_id, TrackedBet.market_type)
            .where(
                TrackedBet.event_date == target_date,
                TrackedBet.source_rule_key.in_(["acca_leg_system", "acca_advisory_system"]),
            )
        )).all()
    )
    existing_keys: set[tuple] = {(r.fixture_id, r.market_type) for r in existing_rows}

    inserted = 0
    for leg in legs:
        fid = leg.get("fixture_id")
        market = leg.get("market", "")
        if not fid or not market:
            continue
        key = (fid, market)
        if key in existing_keys:
            continue
        odd = float(leg.get("odd") or 0)
        if odd <= 1.0:
            continue
        match_name = (
            f"{leg.get('home_team', '')} vs {leg.get('away_team', '')}"
            if leg.get("home_team") else "Unknown"
        )
        bet = TrackedBet(
            user_id=None,
            fixture_id=fid,
            bookmaker="AI Acca",
            event_date=target_date,
            match_name=match_name,
            league=None,
            market_type=market,
            selection_name=market,
            odds=odd,
            stake=50_000.0,
            source_rule_key="acca_leg_system",
            source_rule_label="AI Acca Leg (Auto)",
            dual_confidence=acca.get("confidence"),
            result_status="Pending",
        )
        db.add(bet)
        existing_keys.add(key)
        inserted += 1

    # Combined accumulator row — dedup on market_type="Accumulator"
    combo_key = (None, "Accumulator")
    if combo_key not in existing_keys:
        # Check by source_rule_key + date since fixture_id is NULL for the combo row
        combo_exists = await db.scalar(
            select(TrackedBet.id).where(
                TrackedBet.source_rule_key == "acca_advisory_system",
                TrackedBet.event_date == target_date,
                TrackedBet.user_id.is_(None),
            )
        )
        if not combo_exists:
            leg_summary = "\n".join(
                f"{i+1}. {leg.get('home_team','')} vs {leg.get('away_team','')} · "
                f"{leg.get('market','')} @ {float(leg.get('odd') or 0):.2f}"
                for i, leg in enumerate(legs)
            )
            combo = TrackedBet(
                user_id=None,
                fixture_id=None,
                bookmaker="AI Acca",
                event_date=target_date,
                match_name=f"AI Acca · {len(legs)} leg{'s' if len(legs) != 1 else ''}",
                league=None,
                market_type="Accumulator",
                selection_name="Accumulator",
                odds=combined_odds,
                stake=50_000.0,
                source_rule_key="acca_advisory_system",
                source_rule_label="AI Acca of the Day (System)",
                dual_confidence=acca.get("confidence"),
                notes=json.dumps({"legs": legs, "leg_summary": leg_summary}),
                result_status="Pending",
            )
            db.add(combo)
            inserted += 1

    if inserted:
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            logger.warning("auto_track_acca_legs: commit failed for %s", target_date, exc_info=True)
            return 0

    return inserted


# ── Acca leg results (computed live from current Fixture rows) ───────────────
# Not persisted into the cache — recomputed on every call so results always
# reflect the latest fixture score/status rather than a stale snapshot.

async def _attach_leg_results(db: AsyncSession, legs: list[dict], target_date: date) -> None:
    """
    Mutate each leg in place, adding `result` ("won" | "lost" | "void" | "pending"),
    `score` (e.g. "2-1", or None while pending/void), and backfilling `kickoff_at`
    when missing. Looks up the fixture by `fixture_id` (new legs); falls back to a
    team-name + date match for legacy legs generated before fixture_id was attached.

    Runs on every call (cache hit or live) — `kickoff_at` is only set once at
    generation time in the live path, so a cached acca served on a later request
    would otherwise never get it. Backfilling here covers both paths uniformly.
    """
    from app.services.settlement import (
        FINAL_STATUSES, VOID_STATUSES, _score_condition,
    )

    if not legs:
        return

    fixture_ids = [leg["fixture_id"] for leg in legs if leg.get("fixture_id")]
    fixtures_by_id: dict[int, Fixture] = {}
    if fixture_ids:
        rows = (await db.execute(select(Fixture).where(Fixture.id.in_(fixture_ids)))).scalars().all()
        fixtures_by_id = {f.id: f for f in rows}

    for leg in legs:
        fixture = fixtures_by_id.get(leg.get("fixture_id"))
        if fixture is None:
            home = (leg.get("home_team") or "").strip()
            away = (leg.get("away_team") or "").strip()
            if home and away:
                fixture = await db.scalar(
                    select(Fixture).where(
                        Fixture.event_date == target_date,
                        Fixture.home_team == home,
                        Fixture.away_team == away,
                    )
                )

        if fixture is None:
            leg["result"] = "pending"
            leg["score"] = None
            continue

        if not leg.get("kickoff_at"):
            leg["kickoff_at"] = fixture.kickoff_at.isoformat() if fixture.kickoff_at else None
        leg.setdefault("fixture_id", fixture.id)

        status = (fixture.status or "").strip().upper()
        if status in VOID_STATUSES:
            leg["result"] = "void"
            leg["score"] = None
            continue

        if status not in FINAL_STATUSES or fixture.home_score is None or fixture.away_score is None:
            leg["result"] = "pending"
            leg["score"] = None
            continue

        score = f"{fixture.home_score}-{fixture.away_score}"
        condition = _score_condition(leg.get("market"))
        if condition is None:
            leg["result"] = "void"
            leg["score"] = score
        else:
            leg["result"] = "won" if condition(fixture.home_score, fixture.away_score) else "lost"
            leg["score"] = score


# ── Advisory cache (system_settings) ─────────────────────────────────────────
# Stores the AI output once per day so users see instant results.
# Only the AI-generated content is cached; per-user acca tracking is always
# evaluated fresh so each subscriber gets their own TrackedBet row.

_ADVISORY_CACHE_PREFIX = "advisory_cache_"


async def _get_advisory_cache(db: AsyncSession, target_date: date) -> dict | None:
    key = f"{_ADVISORY_CACHE_PREFIX}{target_date.isoformat()}"
    row = await db.execute(
        text("SELECT value FROM system_settings WHERE key = :k"), {"k": key}
    )
    val = row.scalar()
    if val:
        try:
            return json.loads(val)
        except Exception:
            return None
    return None


async def invalidate_advisory_cache(db: AsyncSession, target_date: date) -> None:
    """Drop the cached advisory payload for a date.

    Called by compute_signals_for_date: the cached acca pins leg odds from the
    signal rows it was built on, so once those rows are deleted/recomputed the
    cached payload is definitionally stale (2026-07-02: a pre-fix acca kept
    serving contaminated 1st-half prices after the signals were corrected).
    """
    key = f"{_ADVISORY_CACHE_PREFIX}{target_date.isoformat()}"
    try:
        await db.execute(
            text("DELETE FROM system_settings WHERE key = :k"), {"k": key}
        )
        await db.commit()
    except Exception:
        logger.warning("Failed to invalidate advisory cache for %s", target_date, exc_info=True)


async def _set_advisory_cache(db: AsyncSession, target_date: date, data: dict) -> None:
    key = f"{_ADVISORY_CACHE_PREFIX}{target_date.isoformat()}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    await db.execute(text("""
        INSERT INTO system_settings (key, value, updated_at) VALUES (:k, :v, :t)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
    """), {"k": key, "v": json.dumps(data), "t": now})
    await db.commit()


# ── Public entry point ────────────────────────────────────────────────────────

async def get_advisor_insights(
    db:           AsyncSession,
    target_date:  date,
    fixture_ids:  list[int] | None = None,
    current_user: Any | None = None,
    force:        bool = False,
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

    # ── Serve from cache when available (skip on force=True or fixture filter) ─
    if not force and fixture_ids is None:
        cached = await _get_advisory_cache(db, target_date)
        if cached and cached.get("matches_analysed", 0) > 0:
            # Cache hit — report whether this user already tracked the acca
            # (tracking itself only happens via the explicit track endpoint),
            # then return immediately.
            acca_data = cached.get("accumulator", {})
            tracked = False
            if acca_data.get("legs") and not acca_data.get("error"):
                uid = getattr(current_user, "id", None) if current_user else None
                if uid is not None:
                    try:
                        tracked = await _is_acca_tracked(db, target_date, uid)
                    except Exception as exc:
                        logger.warning("_is_acca_tracked (cache hit) failed: %s", exc)
                try:
                    await _attach_leg_results(db, acca_data["legs"], target_date)
                except Exception as exc:
                    logger.warning("_attach_leg_results (cache hit) failed: %s", exc)
            cached["accumulator"] = {**acca_data, "tracked": tracked, "from_cache": True}
            return cached

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

    # ── Acca builder gets its own signal pool (tiered fallbacks) ─────────────
    # The pool drives both what the AI sees AND the hallucination-guard validation
    # (legs not in the pool are dropped).  All four tiers keep the same pool for
    # both purposes so the AI can never propose legs it wasn't given.

    def _primary_prob(sig: Signal) -> float:
        return max(sig.bayesian_prob or 0.0, sig.poisson_prob or 0.0)

    # Tier 1: best — High+Both+prob≥0.70
    acca_t1 = [
        (sig, fix) for sig, fix in rows
        if sig.dual_confidence == "High"
        and sig.dual_agreement == "Both"
        and _primary_prob(sig) >= 0.70
    ]
    # Tier 2: High+Both, no prob floor
    acca_t2 = [
        (sig, fix) for sig, fix in rows
        if sig.dual_confidence == "High"
        and sig.dual_agreement == "Both"
    ]
    # Tier 3: Both agreement + either engine ≥ 0.60
    acca_t3 = [
        (sig, fix) for sig, fix in rows
        if sig.dual_agreement == "Both"
        and _primary_prob(sig) >= 0.60
    ]
    # Tier 4: any signal with max prob ≥ 0.60
    acca_t4 = [
        (sig, fix) for sig, fix in rows
        if _primary_prob(sig) >= 0.60
    ]

    if len(acca_t1) >= 3:
        acca_pool = acca_t1
    elif len(acca_t2) >= 3:
        acca_pool = acca_t2
    elif len(acca_t3) >= 3:
        acca_pool = acca_t3
    elif len(acca_t4) >= 3:
        acca_pool = acca_t4
    else:
        acca_pool = list(rows)  # last resort: all signals

    acca_context = _build_context(acca_pool, match_infos, perf_weights)

    all_advisor_coros = [
        _call_advisor(
            adv, context, settings,
            extra_context=skeptic_extras if adv["id"] == "skeptic" else "",
        )
        for adv in ADVISORS
    ]
    # Run all 4 advisors (3 council + acca builder) concurrently
    all_advisor_coros.append(_call_advisor(ACCA_BUILDER, acca_context, settings))

    all_outputs = await asyncio.gather(*all_advisor_coros)
    advisor_outputs = all_outputs[:3]
    acca_model_label, acca_result = all_outputs[3]

    # AI-3: Consensus verdict — aggregate across the advisors that actually
    # answered. Errored advisors carry a placeholder "Mixed" verdict which
    # would dilute the consensus, so they're excluded.
    _verdict_score = {"Strong": 2, "Mixed": 1, "Caution": 0}
    advisor_verdicts = [
        result.get("verdict", "Mixed")
        for _, result in advisor_outputs
        if isinstance(result, dict) and "verdict" in result and not result.get("error")
    ]
    if advisor_verdicts:
        avg_score = sum(_verdict_score.get(v, 1) for v in advisor_verdicts) / len(advisor_verdicts)
        consensus_verdict = "Strong" if avg_score >= 1.5 else ("Caution" if avg_score < 0.7 else "Mixed")
    else:
        consensus_verdict = "Mixed"

    # Validate acca legs against the pool the AI was given. Legs that match no
    # pool fixture are hallucinations and get dropped — they could never settle.
    # Matched legs are enriched with fixture_id + kickoff_at, and the AI-reported
    # odd is replaced with the server-side bayesian_best_odd (when the leg's
    # market matches the pool signal) so tracked stakes never rest on an
    # LLM-estimated price.
    raw_legs = acca_result.get("legs", []) if isinstance(acca_result, dict) else []

    def _norm(s: str | None) -> str:
        return (s or "").strip().lower()

    _pool_by_names: dict[tuple[str, str], tuple[Signal, Fixture]] = {
        (_norm(fix.home_team), _norm(fix.away_team)): (sig, fix)
        for sig, fix in acca_pool
    }
    acca_legs: list[dict] = []
    for leg in raw_legs:
        match = _pool_by_names.get((_norm(leg.get("home_team")), _norm(leg.get("away_team"))))
        if match is None:
            logger.warning(
                "Acca leg dropped — no matching pool fixture: %s vs %s (%s)",
                leg.get("home_team"), leg.get("away_team"), leg.get("market"),
            )
            continue
        sig, fix = match
        leg["fixture_id"] = fix.id
        leg["kickoff_at"] = fix.kickoff_at.isoformat() if fix.kickoff_at else None
        if _norm(leg.get("market")) == _norm(sig.market) and sig.bayesian_best_odd and sig.bayesian_best_odd > 1.0:
            leg["odd"] = sig.bayesian_best_odd
        acca_legs.append(leg)

    # Combined odds = product over ALL displayed legs — if any leg lacks a real
    # odd we can't quote a truthful combined price, so leave it unset rather
    # than silently multiplying a subset.
    combined_odds: float | None = None
    if acca_legs:
        try:
            leg_odds = [float(leg.get("odd") or 0) for leg in acca_legs]
            if all(o > 1.0 for o in leg_odds):
                product = 1.0
                for o in leg_odds:
                    product *= o
                combined_odds = round(product, 2)
        except (TypeError, ValueError):
            combined_odds = None

    # Enforce "High" badge when the acca was built from the elite pool and
    # has ≥3 legs with a sensible combined odd (≥3.0).  The AI may sometimes
    # self-report "Medium" conservatively — we override it here because the
    # input signals are already pre-screened as High+Both+≥70%.
    ai_confidence = acca_result.get("confidence", "Medium") if isinstance(acca_result, dict) else "Medium"
    if (
        len(acca_legs) >= 3
        and combined_odds is not None
        and combined_odds >= 3.0
        and len(acca_pool) >= 3
    ):
        resolved_confidence = "High"
    else:
        resolved_confidence = ai_confidence

    accumulator = {
        "model":         acca_model_label,
        "legs":          acca_legs,
        "combined_odds": combined_odds,
        "rationale":     acca_result.get("rationale", "") if isinstance(acca_result, dict) else "",
        "confidence":    resolved_confidence,
        "error":         acca_result.get("error") if isinstance(acca_result, dict) else None,
    }

    # Report whether this user already tracked the acca — tracking itself is
    # opt-in via the explicit track endpoint, never a side effect of viewing.
    tracked = False
    if acca_legs and not accumulator.get("error"):
        uid = getattr(current_user, "id", None) if current_user else None
        if uid is not None:
            try:
                tracked = await _is_acca_tracked(db, target_date, uid)
            except Exception as exc:
                logger.warning("_is_acca_tracked failed: %s", exc)
        try:
            await _attach_leg_results(db, acca_legs, target_date)
        except Exception as exc:
            logger.warning("_attach_leg_results failed: %s", exc)

    accumulator["tracked"] = tracked

    result_payload = {
        "configured":        True,
        "date":              target_date.isoformat(),
        "matches_analysed":  len(rows),
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
        "accumulator": accumulator,
    }

    # Persist AI output to cache (omit the per-user tracked flag).
    # A run with any errored advisor (or a failed acca) is NOT cached — caching
    # it would pin the failure for the whole day, since cache hits skip the AI
    # entirely. Leaving the cache empty lets the next request retry live.
    has_errors = accumulator.get("error") is not None or any(
        isinstance(res, dict) and res.get("error") for _, res in advisor_outputs
    )
    if fixture_ids is None and not has_errors:
        try:
            cacheable = {
                **result_payload,
                "accumulator": {k: v for k, v in accumulator.items() if k != "tracked"},
            }
            await _set_advisory_cache(db, target_date, cacheable)
        except Exception as exc:
            logger.warning("Failed to write advisory cache: %s", exc)
    elif fixture_ids is None:
        logger.info("Advisory result not cached — advisor/acca errors present; next request retries live")

    return result_payload


async def track_acca_for_user(
    db:           AsyncSession,
    target_date:  date,
    current_user: Any,
) -> dict:
    """
    Explicitly add the day's AI acca to this user's tracker (one row per
    user+date). Resolves the acca via get_advisor_insights — normally a cache
    hit — so the tracked odds are always the server-validated ones.
    """
    insights = await get_advisor_insights(db, target_date, current_user=current_user)
    if insights.get("error"):
        return {"tracked": False, "error": insights["error"], "message": insights.get("message", "")}

    acca = insights.get("accumulator") or {}
    if not acca.get("legs") or acca.get("error") or not acca.get("combined_odds"):
        return {
            "tracked": False,
            "error":   "no_acca",
            "message": "No accumulator available to track for this date.",
        }

    created = await _create_acca_bet(db, acca, target_date, current_user)
    return {"tracked": True, "created": created, "combined_odds": acca.get("combined_odds")}

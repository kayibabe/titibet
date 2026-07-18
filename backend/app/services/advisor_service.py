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
import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import anthropic
import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings, DUAL_HIGH_ODDS_CEILING, OVER_GOALS_SUPPRESSED_LEAGUES, DISABLED_LEAGUES
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
        "cerebras": "llama3.1-70b",
        "groq":     "llama-3.3-70b-versatile",
        "mistral":  "mistral-small-latest",
    },
    "system": (
        "You are a specialist football accumulator analyst. "
        "You receive a pool of pre-screened signals ranked by dual-engine probability agreement. "
        "Your role is to build as many non-overlapping accumulator tickets as possible from the pool. "
        "Each ticket: 3–4 legs. No fixture may appear in more than one ticket. "
        "Keep building tickets until fewer than 3 signals remain unused. "
        "Per ticket, optimise for: "
        "(1) Prefer legs where both Bayesian and Poisson engines agree (dual_agreement=Both) "
        "with probability ≥0.60. Fall back to single-engine signals only when the dual pool is thin. "
        "(2) League diversity — no more than 2 legs from the same league within a ticket. "
        "(3) Combined decimal odds in the 3.5–6.0 range — achievable at sustainable win rates (≥30% ticket probability). "
        "(4) Market diversity — avoid stacking the same market type (e.g. all Over 2.5). "
        "Avoid per ticket: the same team more than once, any decimal leg odd above 3.5, "
        "any signal where contextual data raises red flags. "
        "Only select legs from the exact fixture_ids present in the signal pool — do not invent or guess ids. "
        "For each leg use the Bayesian best_odd from the context; never invent an odd not shown. "
        "In each leg's 'reason' field, write a specific 1-sentence justification that names "
        "the actual probability (e.g. 'Bayesian 72%'), agreement status (Both engines / Bayesian only), "
        "and one supporting context factor (e.g. 'home team unbeaten in last 6, 8-book consensus'). "
        "Generic reasons like 'Strong signal' or 'Model indicates value' are not acceptable. "
        "Set confidence to 'High' when ≥3 legs in a ticket have both engines agreeing at ≥0.60 probability. "
        "Set confidence to 'Medium' for mixed pools. "
        "Order your tickets from strongest to weakest: rank first by confidence (High > Medium > Low), "
        "then by how many legs have dual_agreement=Both, then by combined odds proximity to the 6-20x sweet spot. "
        "Always respond with valid JSON only — no markdown, no prose outside the JSON."
    ),
    "task": (
        "Build all possible non-overlapping accumulator tickets from the signals above, "
        "ranked strongest first. "
        "Each leg MUST use the fixture_id shown in parentheses (id:NNN) at the start of its context block. "
        "Return JSON with this EXACT shape — no extra fields:\n"
        '{"tickets":['
        '{"legs":[{"fixture_id":123,"home_team":"...","away_team":"...","market":"...","odd":1.75,'
        '"dual_agreement":"Both"|"Bayesian Only"|"Poisson Only",'
        '"reason":"Bayesian 68%, both engines agree; home side unbeaten in 7, 6-book consensus"}],'
        '"rationale":"2-3 sentence explanation of why these legs combine well",'
        '"confidence":"High"|"Medium"|"Low"}'
        "]}"
    ),
}

# ── Advisor roster ───────────────────────────────────────────────────────────
# 2026-07-11 audit: Scout and Skeptic retired (paper WR 42.9% / 50%).
# 2026-07-17: Restored — paper sample was too small (5/8 picks) to justify
# retirement; all three advisors run as paper-trade shadow picks going forward.

ADVISORS: list[dict] = [
    {
        "id":    "scout",
        "name":  "The Scout",
        "role":  "Signal validation & match context",
        "emoji": "🔭",
        "models": {
            "claude":   "claude-sonnet-5",
            "gemini":   "gemini-2.0-flash",
            "cerebras": "llama3.1-70b",
            "groq":     "llama-3.3-70b-versatile",
            "mistral":  "mistral-small-latest",
        },
        "system": (
            "You are a football match analyst. "
            "You receive a batch of model-generated betting signals alongside match context: "
            "recent form, head-to-head records, goals scored/conceded, and team stats. "
            "Your job is to validate each signal against the ACTUAL match evidence — "
            "confirm when the numbers tell a coherent story, flag when they don't. "
            "A strong signal is one where the model probability, the bookmaker odds, "
            "and the contextual match data all point in the same direction. "
            "Always respond with valid JSON only. No markdown, no prose outside the JSON."
        ),
        "task": (
            "Review each signal against its match context. Return JSON with this exact shape:\n"
            '{"verdict":"Strong"|"Mixed"|"Caution",'
            '"top_picks":[{"home_team":"...","away_team":"...","market":"...","reason":"..."},...],'
            '"warnings":["any match-context concern that weakens a signal",...],'
            '"summary":"2-3 sentence paragraph on how well the match context supports today\'s signals"}'
        ),
    },
    {
        "id":    "strategist",
        "name":  "The Strategist",
        "role":  "Portfolio construction & value ranking",
        "emoji": "♟️",
        "models": {
            "claude":   "claude-sonnet-5",
            "gemini":   "gemini-2.0-flash",
            "cerebras": "llama3.1-70b",
            "groq":     "llama-3.1-8b-instant",
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
            "cerebras": "llama3.1-70b",
            "groq":     "llama-3.3-70b-versatile",
            "mistral":  "mistral-small-latest",
        },
        "system": (
            "You are a contrarian football betting analyst — your job is to find reasons NOT to bet. "
            "You receive the same signals as the other advisors, plus an extra section "
            "highlighting market-vs-model divergences, thin bookmaker coverage, odds drift, "
            "and engine contradictions. "
            "Your role: interrogate each signal for hidden risks, market inefficiencies, "
            "and model blind spots. Identify which picks the smart money is fading and why. "
            "A signal that survives your scrutiny is genuinely worth considering; "
            "one that doesn't should be removed from consideration entirely. "
            "Always respond with valid JSON only. No markdown, no prose outside the JSON."
        ),
        "task": (
            "Scrutinise each signal for risks and red flags. Return JSON with this exact shape:\n"
            '{"verdict":"Strong"|"Mixed"|"Caution",'
            '"top_picks":[{"home_team":"...","away_team":"...","market":"...","reason":"..."},...],'
            '"warnings":["specific risk or red flag for a signal",...],'
            '"summary":"2-3 sentence contrarian assessment of today\'s signal quality"}'
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

        ko = (
            fix.kickoff_at.strftime("%H:%M CAT") if fix.kickoff_at else "TBD"
        )
        line = (
            f"[{i}] (id:{fix.id}) {fix.home_team} vs {fix.away_team}"
            f" | {fix.league or 'Unknown League'} | Tier {fix.league_tier or '?'}"
            f" | KO: {ko}\n"
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
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    # Second attempt: strip markdown code fences then parse
    stripped = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped.strip())
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
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
            max_tokens=3000,
            system=advisor["system"],
            messages=[
                {"role": "user", "content": f"{advisor['task']}\n\n{context}"},
            ],
        )
        text = next((b.text for b in msg.content if b.type == "text"), "")
        if not text:
            # Empty response — treat as quota/overload and let the chain fall back.
            logger.info("Claude returned empty text for %s — falling back", advisor["id"])
            return None
        return _extract_json(text)
    except anthropic.APITimeoutError:
        logger.info("Claude timeout — falling back (advisor=%s)", advisor["id"])
        return None
    except anthropic.AuthenticationError:
        return _err("claude_auth", "Anthropic API key is invalid.")
    except anthropic.RateLimitError:
        return _err("claude_429", "Claude rate limit — retry shortly.")
    except anthropic.APIStatusError as exc:
        body_msg = ""
        if isinstance(exc.body, dict):
            body_msg = exc.body.get("error", {}).get("message", "")
        if _is_quota_error(body_msg):
            logger.info("Claude quota (HTTP %s) — falling back (advisor=%s)", exc.status_code, advisor["id"])
            return None
        logger.warning("Claude error for %s: HTTP %s — %s", advisor["id"], exc.status_code, body_msg)
        return _err(f"claude_{exc.status_code}", f"Claude error: {body_msg[:120] or str(exc)[:120]}")
    except anthropic.APIError as exc:
        logger.warning("Claude API error for %s: %s", advisor["id"], exc)
        return None
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
            "maxOutputTokens":  2048,
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
        "max_tokens":      2048,
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
        if status >= 500 or status in (404, 400, 401, 403):
            # 400 — invalid model name (deprecated/renamed); fall through to next provider
            # 401/403 — key missing or invalid; fall through silently rather than
            #            surfacing a raw auth error to the user
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


# ── Advisory pick tracking ────────────────────────────────────────────────────

_ADVISOR_RULE_KEYS: dict[str, tuple[str, str]] = {
    "scout":      ("scout_pick",      "The Scout"),
    "strategist": ("strategist_pick", "The Strategist"),
    "skeptic":    ("skeptic_pick",    "The Skeptic"),
}
_ALL_ADVISORY_KEYS = [rk for rk, _ in _ADVISOR_RULE_KEYS.values()]


async def auto_track_advisor_picks(
    db:              AsyncSession,
    advisor_outputs: list[tuple[str, dict]],
    advisor_defs:    list[dict],
    rows:            list,
    target_date:     date,
) -> int:
    """
    Create zero-stake shadow TrackedBet rows for each advisor's top_picks so their
    performance can be tracked over time.  Idempotent: skips picks already present
    for this date.  Returns the number of new rows inserted.

    rows — list of (Signal, Fixture) tuples from the current advisory signal pool.
    These are used to resolve team names → fixture_id + odds without any LLM calls.
    """
    from app.models.bet import TrackedBet

    def _norm(s: str | None) -> str:
        return (s or "").strip().lower()

    pool_by_names: dict[tuple[str, str], tuple] = {
        (_norm(fix.home_team), _norm(fix.away_team)): (sig, fix)
        for sig, fix in rows
    }

    existing_rows_q = (await db.execute(
        select(TrackedBet.fixture_id, TrackedBet.market_type, TrackedBet.source_rule_key)
        .where(
            TrackedBet.event_date == target_date,
            TrackedBet.user_id.is_(None),
            TrackedBet.source_rule_key.in_(_ALL_ADVISORY_KEYS),
        )
    )).all()
    existing_keys: set[tuple] = {
        (r.fixture_id, r.market_type, r.source_rule_key) for r in existing_rows_q
    }

    inserted = 0
    for adv_def, (model_label, result) in zip(advisor_defs, advisor_outputs):
        adv_id = adv_def["id"]
        if adv_id not in _ADVISOR_RULE_KEYS or result.get("error"):
            continue
        rule_key, rule_label = _ADVISOR_RULE_KEYS[adv_id]

        for pick in result.get("top_picks", []):
            home   = _norm(pick.get("home_team"))
            away   = _norm(pick.get("away_team"))
            market = (pick.get("market") or "").strip()
            # Normalize LLM-generated labels to canonical market names (case-insensitive suffix)
            if market.lower().endswith(" goals") and not market.lower().startswith("exactly"):
                market = market[: -len(" goals")].strip()
            market = market.replace("Home Team Over ", "Home Over ").replace("Home Team Under ", "Home Under ")
            market = market.replace("Away Team Over ", "Away Over ").replace("Away Team Under ", "Away Under ")
            if not home or not away or not market:
                continue

            pair = pool_by_names.get((home, away))
            if pair is None:
                logger.debug(
                    "auto_track_advisor_picks: no signal for %s vs %s (%s) — skipping",
                    pick.get("home_team"), pick.get("away_team"), adv_id,
                )
                continue

            sig, fix = pair

            # Skip picks from suppressed leagues — mirrors auto_tracker suppression so
            # zero-stake advisory rows don't inflate loss counts for disabled markets.
            _league_lower = (fix.league or "").lower().strip()
            if _league_lower in DISABLED_LEAGUES or "friendlies" in _league_lower:
                continue
            _OVER_ADV = {"Home Over 0.5", "Away Over 0.5", "Over 1.5", "Over 2.5",
                         "Home Over 1.5", "Away Over 1.5"}
            if market in _OVER_ADV and any(k in _league_lower for k in OVER_GOALS_SUPPRESSED_LEAGUES):
                continue

            odds = sig.bayesian_best_odd
            if not odds or odds <= 1.0:
                prob = sig.bayesian_prob or sig.poisson_prob
                if prob and 0.0 < prob < 1.0:
                    odds = round(1.0 / prob, 3)
                else:
                    continue

            # Over 1.5 quality gate: Bayesian-only Over 1.5 at 1.30–1.36 in early
            # European qualifying has ~43% WR — far below the ~75% break-even at
            # those odds. Reject entirely below 1.40; require Both-engine agreement
            # when odds are 1.40–1.50.
            if market == "Over 1.5":
                if odds < 1.40:
                    logger.debug(
                        "auto_track_advisor_picks: Over 1.5 @ %.2f below 1.40 floor — skip (%s vs %s)",
                        odds, fix.home_team, fix.away_team,
                    )
                    continue
                if odds < 1.50 and sig.dual_agreement != "Both":
                    logger.debug(
                        "auto_track_advisor_picks: Over 1.5 @ %.2f needs Both agreement, got %s — skip (%s vs %s)",
                        odds, sig.dual_agreement, fix.home_team, fix.away_team,
                    )
                    continue

            key = (fix.id, market, rule_key)
            if key in existing_keys:
                continue

            advisor_stake = 0.0  # all advisors are paper-trade only
            db.add(TrackedBet(
                user_id=None,
                fixture_id=fix.id,
                bookmaker="AI Advisory",
                event_date=target_date,
                match_name=f"{fix.home_team} vs {fix.away_team}",
                league=fix.league,
                market_type=market,
                selection_name=market,
                odds=odds,
                stake=advisor_stake,
                source_rule_key=rule_key,
                source_rule_label=rule_label,
                dual_confidence=sig.dual_confidence,
                dual_agreement=sig.dual_agreement,
                result_status="Pending",
                notes=json.dumps({"reason": pick.get("reason", ""), "model": model_label}),
            ))
            existing_keys.add(key)
            inserted += 1

    if inserted:
        try:
            await db.commit()
            logger.info(
                "auto_track_advisor_picks: %d pick rows for %s", inserted, target_date,
            )
        except Exception:
            await db.rollback()
            logger.warning(
                "auto_track_advisor_picks: commit failed for %s", target_date, exc_info=True,
            )
            return 0

    return inserted


# ── Acca tracking helpers ─────────────────────────────────────────────────────

def _acca_fingerprint(legs: list[dict]) -> str:
    """
    Stable 12-char hex hash of an acca's legs.

    Uses fixture_id when available (exact, immune to name variation).
    Falls back to sorted home:away:market strings for legs without fixture_id
    so different accas always produce different hashes even without fixture_ids.
    Stored as "Accumulator|<fp>" in selection_name for per-acca dedup without
    a schema migration.
    """
    parts = []
    for leg in legs:
        fid = leg.get("fixture_id")
        mkt = (leg.get("market") or "").strip().lower()
        if fid:
            parts.append(f"{fid}:{mkt}")
        else:
            home = (leg.get("home_team") or "").strip().lower()
            away = (leg.get("away_team") or "").strip().lower()
            parts.append(f"{home}:{away}:{mkt}")
    parts.sort()
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:12]


async def _is_acca_tracked(
    db:          AsyncSession,
    target_date: date,
    uid:         int,
    fp:          str | None = None,
) -> bool:
    """
    True when this user already has this specific acca in their tracker.

    When fp (fingerprint) is supplied the check is exact — only this acca's
    legs count as "tracked".  Falls back to a legacy date-level check for rows
    written before fingerprinting was introduced (selection_name='Accumulator').
    """
    from app.models.bet import TrackedBet

    conditions = [
        TrackedBet.source_rule_key == "acca_advisory",
        TrackedBet.event_date == target_date,
        TrackedBet.user_id == uid,
    ]
    if fp:
        conditions.append(TrackedBet.selection_name == f"Accumulator|{fp}")

    row = await db.scalar(select(TrackedBet.id).where(*conditions))
    return row is not None


async def _create_acca_bet(
    db:           AsyncSession,
    acca:         dict,
    target_date:  date,
    current_user: Any | None,
) -> bool:
    """
    Persist the AI acca as a single TrackedBet row.

    Multiple distinct accas (different legs) may be tracked on the same day —
    each is distinguished by its content fingerprint stored in selection_name as
    "Accumulator|<12-char-hex>".  A duplicate is only rejected when the exact
    same set of legs has already been tracked by this user.
    """
    from sqlalchemy.exc import IntegrityError
    from app.models.bet import TrackedBet

    legs = acca.get("legs", [])
    combined_odds = acca.get("combined_odds")
    if not legs or not combined_odds or combined_odds <= 1.0:
        return False

    uid: int | None = getattr(current_user, "id", None) if current_user else None

    if uid is None:
        return False

    fp = _acca_fingerprint(legs)
    fp_tag = f"Accumulator|{fp}"

    # Dedup: same fingerprint already tracked by this user → skip
    dup_q = select(TrackedBet.id).where(
        TrackedBet.source_rule_key == "acca_advisory",
        TrackedBet.event_date == target_date,
        TrackedBet.user_id == uid,
        TrackedBet.selection_name == fp_tag,
    )
    if await db.scalar(dup_q):
        return False

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
        selection_name=fp_tag,
        odds=combined_odds,
        stake=50_000.0,
        source_rule_key="acca_advisory",
        source_rule_label=acca.get("rank_label") or "AI Acca of the Day",
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
    acca:        dict | list,
    target_date: date,
    replace:     bool = False,
) -> int:
    """
    Create ONE system TrackedBet row per ACCA ticket at K50,000 flat stake.

    Each ticket becomes a single combined bet (market_type='Accumulator') with
    legs stored in notes JSON.  Settlement via settle_acca_bets() which handles
    source_rule_key='system_acca' using the standard all-or-nothing ACCA rules.

    Accepts either a single acca dict (backward compat) or a list of ticket dicts.
    Idempotent by fingerprint: skips tickets whose fixture-ID set is already tracked.
    replace=True wipes all system_acca rows for the date first (emergency reset).

    Returns count of new ticket rows inserted.
    """
    import hashlib as _hl
    import json as _json
    from app.models.bet import TrackedBet

    # Normalise to list of tickets
    tickets: list[dict] = acca if isinstance(acca, list) else [acca]
    tickets = [t for t in tickets if t.get("legs") and float(t.get("combined_odds") or 0) > 1.0]
    if not tickets:
        return 0

    if replace:
        await db.execute(
            text(
                "DELETE FROM tracked_bets "
                "WHERE event_date = :d AND user_id IS NULL "
                "AND source_rule_key = 'system_acca'"
            ),
            {"d": target_date.isoformat()},
        )
        existing_fps: set[str] = set()
    else:
        existing_fps = set(
            (await db.execute(
                select(TrackedBet.selection_name)
                .where(
                    TrackedBet.event_date == target_date,
                    TrackedBet.source_rule_key == "system_acca",
                    TrackedBet.user_id.is_(None),
                )
            )).scalars().all()
        )

    inserted = 0
    inserted_legs_by_fp: dict[str, list] = {}

    for ticket in tickets:
        legs = ticket.get("legs", [])
        combined_odds = float(ticket.get("combined_odds") or 0)
        leg_count = len(legs)
        if leg_count < 2 or combined_odds <= 1.0:
            continue

        # Skip tickets where any leg has already kicked off (within 30 min).
        skip = False
        for leg in legs:
            kickoff_str = leg.get("kickoff_at")
            if kickoff_str:
                try:
                    ko = datetime.fromisoformat(kickoff_str)
                    if ko.tzinfo is None:
                        ko = ko.replace(tzinfo=timezone.utc)
                    if ko < datetime.now(timezone.utc) + timedelta(minutes=30):
                        skip = True
                        break
                except (ValueError, TypeError):
                    pass
        if skip:
            continue

        # Fingerprint: sorted fixture IDs — dedup across re-runs.
        fid_str = ",".join(sorted(str(leg.get("fixture_id", "")) for leg in legs if leg.get("fixture_id")))
        fingerprint = f"system_acca|{fid_str}"
        if fingerprint in existing_fps:
            continue

        leg_summary = "\n".join(
            f"{i+1}. {leg.get('home_team','')} vs {leg.get('away_team','')} · "
            f"{leg.get('market','')} @ {float(leg.get('odd') or 0):.2f}"
            for i, leg in enumerate(legs)
        )

        db.add(TrackedBet(
            user_id=None,
            fixture_id=None,
            bookmaker="AI Acca",
            event_date=target_date,
            match_name=f"AI Acca · {leg_count} leg{'s' if leg_count != 1 else ''}",
            league=None,
            market_type="Accumulator",
            selection_name=fingerprint,
            odds=combined_odds,
            stake=50_000.0,
            source_rule_key="system_acca",
            source_rule_label="AI Acca Ticket",
            dual_confidence=ticket.get("confidence"),
            result_status="Pending",
            acca_ticket_id=fingerprint,
            notes=_json.dumps({"legs": legs, "leg_summary": leg_summary}),
        ))
        existing_fps.add(fingerprint)
        inserted_legs_by_fp[fingerprint] = legs
        inserted += 1

    if inserted:
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            logger.warning("auto_track_acca_legs: commit failed for %s", target_date, exc_info=True)
            return 0

        # Stamp acca_ticket_id on each leg's corresponding single-bet row so the
        # self-learning pipeline can correlate ACCA losses with individual leg types.
        # Uses INSERT-OR-IGNORE semantics (NULL guard) so only the first ACCA ticket
        # to claim a fixture+market wins — no double-stamping across tickets.
        stamped = False
        for fp, fp_legs in inserted_legs_by_fp.items():
            for leg in fp_legs:
                fid = leg.get("fixture_id")
                mkt = leg.get("market")
                if fid and mkt:
                    await db.execute(
                        text(
                            "UPDATE tracked_bets SET acca_ticket_id = :tid "
                            "WHERE fixture_id = :fid AND market_type = :mkt "
                            "AND user_id IS NULL AND acca_ticket_id IS NULL"
                        ),
                        {"tid": fp, "fid": int(fid), "mkt": mkt},
                    )
                    stamped = True
        if stamped:
            await db.commit()

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
        if tier != "pro" or status != "active":
            return {
                "error":   "subscription_required",
                "message": "AI Advisor requires an active Pro subscription.",
            }

    settings = get_settings()

    # ── Serve from cache when available (skip on force=True or fixture filter) ─
    if not force and fixture_ids is None:
        cached = await _get_advisory_cache(db, target_date)
        if cached and cached.get("matches_analysed", 0) > 0:
            # Cache hit — report per-ticket tracked status for this user.
            uid_cache = getattr(current_user, "id", None) if current_user else None

            # Normalise: old caches have only "accumulator"; new caches have
            # "accumulators" list.  Upgrade the old format on the fly.
            if "accumulators" not in cached or not cached["accumulators"]:
                acca_data = cached.get("accumulator", {})
                cached["accumulators"] = [acca_data] if acca_data.get("legs") else []

            enriched_tickets: list[dict] = []
            for ticket in cached["accumulators"]:
                ticket_legs = ticket.get("legs", [])
                ticket_tracked = False
                if ticket_legs and not ticket.get("error") and uid_cache is not None:
                    try:
                        fp = _acca_fingerprint(ticket_legs)
                        ticket_tracked = await _is_acca_tracked(db, target_date, uid_cache, fp=fp)
                    except Exception as exc:
                        logger.warning("_is_acca_tracked (cache hit) failed: %s", exc)
                try:
                    await _attach_leg_results(db, ticket_legs, target_date)
                except Exception as exc:
                    logger.warning("_attach_leg_results (cache hit) failed: %s", exc)
                enriched_tickets.append({**ticket, "tracked": ticket_tracked, "from_cache": True})

            cached["accumulators"] = enriched_tickets
            # Keep singular backward-compat field pointing at the first ticket
            cached["accumulator"] = enriched_tickets[0] if enriched_tickets else cached.get("accumulator", {})
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
        .where(Signal.is_candidate == False)  # noqa: E712 — exclude data-collection-only signals
        .where(Signal.dual_confidence.in_(["High", "Medium"]))
        .where(Signal.dual_agreement.in_(["Both", "Bayesian Only", "Poisson Only"]))
        .order_by(Signal.dual_quality_score.desc().nullslast())
        .limit(15)
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

    # Skeptic divergence extras are kept for the Acca Builder context even though
    # the Skeptic advisor was retired — the ACCA pool's skeptic-veto filter still uses it.
    skeptic_extras = _build_skeptic_extras(rows)

    # ── Acca builder gets its own signal pool (tiered fallbacks) ─────────────
    # The pool drives both what the AI sees AND the hallucination-guard validation
    # (legs not in the pool are dropped).  All four tiers keep the same pool for
    # both purposes so the AI can never propose legs it wasn't given.

    def _primary_prob(sig: Signal) -> float:
        return max(sig.bayesian_prob or 0.0, sig.poisson_prob or 0.0)

    # Acca pool — High confidence + Both engines only.
    # 2026-07-18 simulation audit: T2 (High+Both) delivered 66.7% ticket win rate;
    # T3 (Medium+Both) and single-engine legs compound variance and lose money.
    # No fallback to lower tiers — skip the day rather than build a weak ticket.
    #
    # Two sub-tiers select the pool but share the same High+Both requirement:
    # T1: High+Both+prob≥0.72 (preferred — tightest quality, starts here)
    # T2: High+Both, no prob floor (fallback if T1 < 3 legs)
    # If T2 < 3 legs → no acca today.

    acca_t1 = [
        (sig, fix) for sig, fix in rows
        if sig.dual_confidence == "High"
        and sig.dual_agreement == "Both"
        and _primary_prob(sig) >= 0.72
    ]
    acca_t2 = [
        (sig, fix) for sig, fix in rows
        if sig.dual_confidence == "High"
        and sig.dual_agreement == "Both"
    ]

    if len(acca_t1) >= 3:
        acca_pool = acca_t1
    elif len(acca_t2) >= 3:
        acca_pool = acca_t2
    else:
        acca_pool = []  # insufficient High+Both legs — no acca today

    # ACCA ceiling: enforce DUAL_HIGH_ODDS_CEILING for ANY Both-agreement signal,
    # not just High+Both (which is what the main list endpoint gates).
    # In ACCA context per-leg errors compound, so the stricter standard applies.
    acca_pool = [
        (sig, fix) for sig, fix in acca_pool
        if not (
            sig.dual_agreement == "Both"
            and sig.market in DUAL_HIGH_ODDS_CEILING
            and (sig.bayesian_best_odd or 0.0) >= DUAL_HIGH_ODDS_CEILING[sig.market]
        )
    ]

    # HO0.5 Tier 3 ACCA gate: mirrors build_acca_candidates gate.
    # Home-team scoring rates are structurally unreliable in data-sparse lower leagues;
    # the model is overconfident and every system ACCA loss in July 2026 traced back
    # to a Tier 3 HO0.5 leg (Al Hikma/Lebanon, Argentino/Argentina).
    acca_pool = [
        (sig, fix) for sig, fix in acca_pool
        if not (
            sig.market == "Home Over 0.5"
            and (fix.league_tier or 3) >= 3
        )
    ]

    # Data-poor Both+High HO0.5 gate: mirrors build_acca_candidates gate.
    from app.core.config import HO05_DATA_POOR_COUNTRIES
    acca_pool = [
        (sig, fix) for sig, fix in acca_pool
        if not (
            sig.market == "Home Over 0.5"
            and sig.dual_confidence == "High"
            and sig.dual_agreement == "Both"
            and (fix.league_tier or 3) >= 3
            and (fix.country or "").lower() in HO05_DATA_POOR_COUNTRIES
        )
    ]

    # Rank 5: Remove (confidence, market) slices with a confirmed poor track record.
    # Only fires once a slice accumulates ≥25 samples so we're not gate-keeping on noise.
    _PERF_GATE_MIN_SAMPLES = 25
    _PERF_GATE_WIN_RATE    = 0.35
    if perf_weights and perf_weights.by_confidence_market:
        perf_filtered = [
            (sig, fix) for sig, fix in acca_pool
            if (
                (sig.dual_confidence, sig.market)
                not in perf_weights.by_confidence_market
                or perf_weights.by_confidence_market[
                    (sig.dual_confidence, sig.market)
                ].samples < _PERF_GATE_MIN_SAMPLES
                or perf_weights.by_confidence_market[
                    (sig.dual_confidence, sig.market)
                ].win_rate >= _PERF_GATE_WIN_RATE
            )
        ]
        if len(perf_filtered) >= 3:
            acca_pool = perf_filtered

    # Rank 4: Skeptic veto — remove thin-bookmaker-coverage and engine-contradiction
    # signals from the acca pool.  Zero latency cost: this is pure Python.
    skeptic_vetoed = [
        (sig, fix) for sig, fix in acca_pool
        if not (
            sig.contradiction is True
            or (
                sig.bayesian_bookmaker_count is not None
                and sig.bayesian_bookmaker_count <= 1
            )
        )
    ]
    if len(skeptic_vetoed) >= 3:
        acca_pool = skeptic_vetoed

    acca_context = _build_context(acca_pool, match_infos, perf_weights)

    n_advisors = len(ADVISORS)
    all_advisor_coros = [
        _call_advisor(
            adv, context, settings,
            extra_context=skeptic_extras if adv["id"] == "skeptic" else "",
        )
        for adv in ADVISORS
    ]
    # Run advisor(s) + acca builder concurrently
    all_advisor_coros.append(_call_advisor(ACCA_BUILDER, acca_context, settings))

    all_outputs = await asyncio.gather(*all_advisor_coros)
    advisor_outputs = all_outputs[:n_advisors]
    acca_model_label, acca_result = all_outputs[n_advisors]

    # Consensus verdict — pass through the single advisor's verdict directly
    # (averaging only makes sense with multiple advisors).
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

    # ── Validate and process acca tickets ────────────────────────────────────
    # The LLM returns {"tickets": [{legs, rationale, confidence}, ...]} for
    # multi-ticket mode, or the legacy {"legs": [...]} single-ticket shape.
    # Both are normalised into raw_tickets here then processed uniformly.
    def _norm(s: str | None) -> str:
        return (s or "").strip().lower()

    if isinstance(acca_result, dict):
        if "tickets" in acca_result:
            raw_tickets: list[dict] = acca_result.get("tickets") or []
        elif "legs" in acca_result:
            raw_tickets = [acca_result]  # legacy single-ticket
        else:
            raw_tickets = []
    else:
        raw_tickets = []

    _pool_by_fid: dict[int, tuple[Signal, Fixture]] = {
        fix.id: (sig, fix) for sig, fix in acca_pool
    }
    _pool_by_names: dict[tuple[str, str], tuple[Signal, Fixture]] = {
        (_norm(fix.home_team), _norm(fix.away_team)): (sig, fix)
        for sig, fix in acca_pool
    }

    _COMBINED_MAX  = 20.0
    _MIN_ACCA_LEGS = 3
    _MAX_ACCA_LEGS = 4

    def _validate_ticket_legs(
        raw_legs: list[dict],
        used_fids: set[int],
    ) -> list[dict]:
        """Hallucination-guard + server-side odd substitution for one ticket's legs."""
        validated: list[dict] = []
        for leg in raw_legs:
            match: tuple[Signal, Fixture] | None = None
            fid_raw = leg.get("fixture_id")
            if fid_raw is not None:
                try:
                    fid = int(fid_raw)
                    if fid not in used_fids:
                        match = _pool_by_fid.get(fid)
                except (TypeError, ValueError):
                    pass
            if match is None:
                candidate = _pool_by_names.get(
                    (_norm(leg.get("home_team")), _norm(leg.get("away_team")))
                )
                if candidate and candidate[1].id not in used_fids:
                    match = candidate
            if match is None:
                logger.warning(
                    "Acca leg dropped — no matching pool fixture: %s vs %s "
                    "(fixture_id=%s, market=%s)",
                    leg.get("home_team"), leg.get("away_team"),
                    fid_raw, leg.get("market"),
                )
                continue
            sig, fix = match

            # Reject legs where the LLM returned a market that doesn't match the
            # signal — would pair wrong odds with wrong outcome.
            if _norm(leg.get("market")) != _norm(sig.market):
                logger.warning(
                    "Acca leg dropped — market mismatch: LLM=%r signal=%r (%s vs %s)",
                    leg.get("market"), sig.market,
                    fix.home_team, fix.away_team,
                )
                continue

            leg["fixture_id"] = fix.id
            leg["kickoff_at"] = fix.kickoff_at.isoformat() if fix.kickoff_at else None

            # Server-side odd substitution — use canonical Bayesian price.
            if sig.bayesian_best_odd and sig.bayesian_best_odd > 1.0:
                leg["odd"] = sig.bayesian_best_odd

            # Explicit odds validation — drop legs with missing or invalid odds.
            try:
                odd_val = float(leg.get("odd") or 0)
                if odd_val <= 1.0:
                    logger.warning(
                        "Acca leg dropped — odd ≤1.0 (%.2f): %s vs %s %s",
                        odd_val, fix.home_team, fix.away_team, sig.market,
                    )
                    continue
            except (TypeError, ValueError):
                logger.warning(
                    "Acca leg dropped — non-numeric odd %r: %s vs %s %s",
                    leg.get("odd"), fix.home_team, fix.away_team, sig.market,
                )
                continue

            # Enforce per-leg odd ceiling (prompt says ≤3.5).
            if odd_val > 3.5:
                logger.warning(
                    "Acca leg dropped — odd %.2f exceeds 3.5 ceiling: %s vs %s %s",
                    odd_val, fix.home_team, fix.away_team, sig.market,
                )
                continue

            leg["odd"] = odd_val
            validated.append(leg)
        return validated

    def _compute_combined_odds(legs: list[dict]) -> float | None:
        try:
            odds = [float(leg.get("odd") or 0) for leg in legs]
            if all(o > 1.0 for o in odds):
                p = 1.0
                for o in odds:
                    p *= o
                return round(p, 2)
        except (TypeError, ValueError):
            pass
        return None

    def _apply_ceiling(legs: list[dict]) -> tuple[list[dict], float | None]:
        legs = list(legs)
        combined = _compute_combined_odds(legs)
        while combined is not None and combined > _COMBINED_MAX and len(legs) > _MIN_ACCA_LEGS:
            worst = max(range(len(legs)), key=lambda i: float(legs[i].get("odd") or 0))
            legs.pop(worst)
            combined = _compute_combined_odds(legs)
        return legs, combined

    uid = getattr(current_user, "id", None) if current_user else None
    used_fixture_ids: set[int] = set()
    processed_tickets: list[dict] = []

    for raw_ticket in raw_tickets:
        if not isinstance(raw_ticket, dict) or "legs" not in raw_ticket:
            logger.warning("Acca: malformed ticket JSON — expected {legs, rationale, confidence}; skipping")
            continue
        raw_legs = raw_ticket.get("legs", [])
        # Enforce hard max before validation so we never exceed 4 legs even if the
        # LLM ignores the prompt constraint.
        raw_legs = raw_legs[:_MAX_ACCA_LEGS]
        ticket_legs = _validate_ticket_legs(raw_legs, used_fixture_ids)
        if len(ticket_legs) < _MIN_ACCA_LEGS:
            continue

        ticket_legs, combined_odds = _apply_ceiling(ticket_legs)
        if len(ticket_legs) < _MIN_ACCA_LEGS:
            continue

        # Mark these fixtures as used so later tickets can't reuse them
        for leg in ticket_legs:
            used_fixture_ids.add(leg["fixture_id"])

        ai_conf = raw_ticket.get("confidence", "Medium")
        resolved_conf = (
            "High"
            if len(ticket_legs) >= 3 and combined_odds is not None and combined_odds >= 3.0
            else ai_conf
        )

        # Track status for this specific ticket
        ticket_tracked = False
        if uid is not None:
            try:
                fp = _acca_fingerprint(ticket_legs)
                ticket_tracked = await _is_acca_tracked(db, target_date, uid, fp=fp)
            except Exception as exc:
                logger.warning("_is_acca_tracked failed: %s", exc)

        try:
            await _attach_leg_results(db, ticket_legs, target_date)
        except Exception as exc:
            logger.warning("_attach_leg_results failed: %s", exc)

        processed_tickets.append({
            "model":         acca_model_label,
            "legs":          ticket_legs,
            "combined_odds": combined_odds,
            "rationale":     raw_ticket.get("rationale", ""),
            "confidence":    resolved_conf,
            "tracked":       ticket_tracked,
            "error":         None,
        })

    # ── Server-side ranking (authoritative — overrides LLM ordering) ─────────
    # Rank: High confidence > Medium > Low; then count of Both-agreement legs;
    # then in-range flag; then distance from the 6-20x sweet-spot midpoint.
    def _ticket_rank(t: dict) -> tuple:
        conf = {"High": 3, "Medium": 2, "Low": 1}.get(t.get("confidence") or "Low", 0)
        dual_both = sum(
            1 for leg in t.get("legs", [])
            if (leg.get("dual_agreement") or "").strip().lower() == "both"
        )
        odds = float(t.get("combined_odds") or 0)
        in_range = 1 if 6.0 <= odds <= 20.0 else 0
        # Distance from sweet-spot midpoint (13×) — smaller is better
        dist = abs(odds - 13.0) if odds > 0 else 999
        return (-conf, -dual_both, -in_range, dist)

    processed_tickets.sort(key=_ticket_rank)

    # ── Auto-retry on zero tickets ────────────────────────────────────────────
    # When all LLM tickets failed validation (bad fixture_ids, odds > 3.5, etc.)
    # try once more with: the full advisory pool (no tier filtering), the ceiling
    # relaxed to 25× so borderline leg combinations survive, and all fixtures
    # available as candidates.  Only fires when the first attempt returned a
    # non-error response (i.e. the LLM answered but all tickets were rejected
    # by server-side guards) and there are enough signals to build at least one
    # ticket.
    if not processed_tickets and isinstance(acca_result, dict) and not acca_result.get("error") and len(rows) >= 3:
        logger.info("Acca: zero tickets after validation — retrying with full pool and relaxed ceiling")
        _pool_by_fid = {fix.id: (sig, fix) for sig, fix in rows}
        _pool_by_names = {
            (_norm(fix.home_team), _norm(fix.away_team)): (sig, fix)
            for sig, fix in rows
        }
        _COMBINED_MAX = 25.0
        used_fixture_ids = set()

        retry_context = _build_context(list(rows), match_infos, perf_weights)
        retry_model_label, retry_raw = await _call_advisor(ACCA_BUILDER, retry_context, settings)

        retry_raw_tickets: list[dict] = []
        if isinstance(retry_raw, dict):
            if "tickets" in retry_raw:
                retry_raw_tickets = retry_raw.get("tickets") or []
            elif "legs" in retry_raw:
                retry_raw_tickets = [retry_raw]

        for raw_ticket in retry_raw_tickets:
            if not isinstance(raw_ticket, dict) or "legs" not in raw_ticket:
                continue
            raw_legs = (raw_ticket.get("legs") or [])[:_MAX_ACCA_LEGS]
            ticket_legs = _validate_ticket_legs(raw_legs, used_fixture_ids)
            if len(ticket_legs) < _MIN_ACCA_LEGS:
                continue
            ticket_legs, combined_odds = _apply_ceiling(ticket_legs)
            if len(ticket_legs) < _MIN_ACCA_LEGS:
                continue
            for leg in ticket_legs:
                used_fixture_ids.add(leg["fixture_id"])
            ai_conf = raw_ticket.get("confidence", "Medium")
            resolved_conf = (
                "High"
                if len(ticket_legs) >= 3 and combined_odds is not None and combined_odds >= 3.0
                else ai_conf
            )
            ticket_tracked = False
            if uid is not None:
                try:
                    fp = _acca_fingerprint(ticket_legs)
                    ticket_tracked = await _is_acca_tracked(db, target_date, uid, fp=fp)
                except Exception as exc:
                    logger.warning("_is_acca_tracked failed on acca retry: %s", exc)
            try:
                await _attach_leg_results(db, ticket_legs, target_date)
            except Exception as exc:
                logger.warning("_attach_leg_results failed on acca retry: %s", exc)
            processed_tickets.append({
                "model":         retry_model_label,
                "legs":          ticket_legs,
                "combined_odds": combined_odds,
                "rationale":     raw_ticket.get("rationale", ""),
                "confidence":    resolved_conf,
                "tracked":       ticket_tracked,
                "error":         None,
            })

        if processed_tickets:
            processed_tickets.sort(key=_ticket_rank)
            acca_model_label = retry_model_label

    # Assign rank labels after sorting so every TrackedBet row carries the
    # position (Top Pick / Alt Pick 1 / Alt Pick 2 …) in source_rule_label.
    # This lets the analytics layer slice acca performance by ticket rank.
    for i, ticket in enumerate(processed_tickets):
        if len(processed_tickets) == 1:
            ticket["rank_label"] = "AI Acca of the Day"
        elif i == 0:
            ticket["rank_label"] = "Top Pick"
        else:
            ticket["rank_label"] = f"Alt Pick {i}"

    # For backward compat keep `accumulator` (singular) as the first ticket,
    # or an error shell when no valid tickets were produced.
    acca_error = acca_result.get("error") if isinstance(acca_result, dict) else "acca_failed"
    if processed_tickets:
        accumulator = processed_tickets[0]
    else:
        accumulator = {
            "model":         acca_model_label,
            "legs":          [],
            "combined_odds": None,
            "rationale":     "",
            "confidence":    "Medium",
            "tracked":       False,
            "error":         acca_error or "no_valid_legs",
        }

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
        "accumulator":  accumulator,           # first ticket (backward compat)
        "accumulators": processed_tickets,     # all tickets
    }

    # Persist AI output to cache (omit per-user tracked flags).
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
                "accumulator":  {k: v for k, v in accumulator.items() if k != "tracked"},
                "accumulators": [
                    {k: v for k, v in t.items() if k != "tracked"}
                    for t in processed_tickets
                ],
            }
            await _set_advisory_cache(db, target_date, cacheable)
        except Exception as exc:
            logger.warning("Failed to write advisory cache: %s", exc)
        # Shadow-track each advisor's top_picks as zero-stake bets for performance measurement.
        try:
            n_picks = await auto_track_advisor_picks(
                db, list(advisor_outputs), ADVISORS, list(rows), target_date,
            )
            if n_picks:
                logger.info(
                    "Advisory picks tracked: %d rows for %s", n_picks, target_date,
                )
        except Exception:
            logger.exception("auto_track_advisor_picks failed for %s — continuing", target_date)
    elif fixture_ids is None:
        logger.info("Advisory result not cached — advisor/acca errors present; next request retries live")

    return result_payload


async def track_acca_for_user(
    db:             AsyncSession,
    target_date:    date,
    current_user:   Any,
    expected_odds:  float | None = None,
) -> dict:
    """
    Explicitly add the day's AI acca to this user's tracker.

    Fetches the advisory from cache (fast).  When expected_odds is supplied and
    the cached acca's combined_odds differ by more than 0.10, the cache is
    stale (e.g. user hit Refresh and the fresh result wasn't cached due to an
    error) — in that case a force-run re-generates the acca so the tracked row
    matches what the user actually sees.
    """
    insights = await get_advisor_insights(db, target_date, current_user=current_user)
    if insights.get("error"):
        return {"tracked": False, "error": insights["error"], "message": insights.get("message", "")}

    all_tickets: list[dict] = insights.get("accumulators") or []
    if not all_tickets:
        # Backward compat: old cache has only "accumulator" (singular)
        singular = insights.get("accumulator") or {}
        if singular.get("legs"):
            all_tickets = [singular]

    if not all_tickets:
        return {
            "tracked": False,
            "error":   "no_acca",
            "message": "No accumulator available to track for this date.",
        }

    # Find the specific ticket the user wants to track.
    # Primary: match by expected_odds (within 0.10 tolerance) so we track the
    # exact acca displayed to the user even when multiple tickets exist.
    # Fallback: first ticket.
    acca: dict | None = None
    if expected_odds is not None:
        for ticket in all_tickets:
            co = ticket.get("combined_odds")
            if co is not None and abs(float(co) - expected_odds) <= 0.10:
                acca = ticket
                break

    if acca is None:
        # No match — check if every ticket's odds are far from expected_odds
        # (cache mismatch from a Refresh that wasn't cached).
        if (
            expected_odds is not None
            and not any(
                ticket.get("combined_odds") is not None
                and abs(float(ticket["combined_odds"]) - expected_odds) <= 0.10
                for ticket in all_tickets
            )
        ):
            logger.info(
                "track_acca: no ticket with odds ≈ %.2f in cache — forcing fresh run",
                expected_odds,
            )
            insights = await get_advisor_insights(db, target_date, current_user=current_user, force=True)
            all_tickets = insights.get("accumulators") or []
            if not all_tickets:
                singular = insights.get("accumulator") or {}
                if singular.get("legs"):
                    all_tickets = [singular]
            # Try matching again after fresh run
            for ticket in all_tickets:
                co = ticket.get("combined_odds")
                if co is not None and abs(float(co) - expected_odds) <= 0.10:
                    acca = ticket
                    break

        if acca is None:
            acca = all_tickets[0]  # fall back to first ticket

    if not acca.get("legs") or acca.get("error") or not acca.get("combined_odds"):
        return {
            "tracked": False,
            "error":   "no_acca",
            "message": "No accumulator available to track for this date.",
        }

    created = await _create_acca_bet(db, acca, target_date, current_user)
    return {"tracked": True, "created": created, "combined_odds": acca.get("combined_odds")}


# ── Conversational chat ───────────────────────────────────────────────────────

_CHAT_SYSTEM = (
    "You are TiTiBet's AI football betting assistant. "
    "You help pro subscribers understand today's signals, evaluate picks, and think through betting decisions. "
    "Be concise (2-4 sentences unless detail is genuinely needed), analytical, and honest about uncertainty. "
    "You know the system uses Bayesian + Poisson dual-model signals. "
    "Never recommend stake sizes above 5% of bankroll. "
    "Do not guarantee outcomes — betting involves risk."
)

_CHAT_MODELS = {
    "claude":   "claude-haiku-4-5-20251001",
    "gemini":   "gemini-2.0-flash",
    "cerebras": "llama3.1-70b",
    "groq":     "llama-3.3-70b-versatile",
    "mistral":  "mistral-small-latest",
}


async def chat_with_advisor(
    question: str,
    history: list[dict],
    settings,
) -> str:
    """
    Single-turn chat call with conversation history.
    Returns the assistant's reply as a plain string.
    history items: [{"role": "user"|"assistant", "content": "..."}]
    """
    messages = [*history, {"role": "user", "content": question}]
    keys = {
        "claude":   settings.titibet_claude_key,
        "gemini":   settings.gemini_api_key,
        "cerebras": settings.cerebras_api_key,
        "groq":     settings.groq_api_key,
        "mistral":  settings.mistral_api_key,
    }

    for provider in PROVIDER_CHAIN:
        key = keys.get(provider, "")
        if not key:
            continue
        try:
            if provider == "claude":
                client = anthropic.AsyncAnthropic(
                    api_key=key,
                    base_url="https://api.anthropic.com",
                )
                msg = await client.messages.create(
                    model=_CHAT_MODELS["claude"],
                    max_tokens=600,
                    system=_CHAT_SYSTEM,
                    messages=messages,
                )
                return next((b.text for b in msg.content if b.type == "text"), "").strip()
            else:
                url = {"groq": GROQ_URL, "cerebras": CEREBRAS_URL, "mistral": MISTRAL_URL}.get(provider)
                if url is None and provider == "gemini":
                    # Gemini via REST
                    gemini_url = GEMINI_URL.format(model=_CHAT_MODELS["gemini"])
                    turns = []
                    for m in messages:
                        turns.append({"role": "model" if m["role"] == "assistant" else "user",
                                      "parts": [{"text": m["content"]}]})
                    async with httpx.AsyncClient(timeout=30.0) as c:
                        r = await c.post(gemini_url, json={
                            "system_instruction": {"parts": [{"text": _CHAT_SYSTEM}]},
                            "contents": turns,
                            "generationConfig": {"maxOutputTokens": 600, "temperature": 0.4},
                        }, params={"key": key})
                        if r.status_code == 200:
                            cands = r.json().get("candidates", [])
                            if cands:
                                return cands[0]["content"]["parts"][0]["text"].strip()
                    continue
                if url is None:
                    continue
                headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                payload = {
                    "model": _CHAT_MODELS[provider],
                    "messages": [{"role": "system", "content": _CHAT_SYSTEM}, *messages],
                    "max_tokens": 600,
                    "temperature": 0.4,
                }
                async with httpx.AsyncClient(timeout=30.0) as c:
                    r = await c.post(url, json=payload, headers=headers)
                    if r.status_code == 200:
                        return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.info("chat_with_advisor: %s failed — %s", provider, exc)
            continue

    return "All AI providers are currently at quota. Please try again shortly."


async def explain_system_picks(bets: list[dict], settings) -> dict:
    """
    Single LLM call that generates a short 2-sentence analysis for each
    system-tracked bet. Returns {"explanations": {"<key>": "text", ...}}.
    """
    if not bets:
        return {"explanations": {}}

    lines = []
    for i, bet in enumerate(bets[:20], 1):
        key    = str(bet.get("key", i))
        match  = bet.get("match", "Unknown match")
        market = bet.get("market", "Unknown market")
        prob   = bet.get("prob")
        odds   = bet.get("odds")
        result = bet.get("result", "Pending")
        score  = bet.get("score")

        parts = [f"[{key}] {match} · {market}"]
        if prob:  parts.append(f"prob {prob}")
        if odds:  parts.append(f"odds {odds}")
        parts.append(f"result: {result}")
        if score: parts.append(f"score {score}")
        lines.append("  " + " · ".join(parts))

    bets_text = "\n".join(lines)
    full_prompt = (
        "You are TiTiBet's AI football betting assistant. "
        "For EACH bet below write a 3-4 sentence PRE-MATCH predictive analysis. "
        "Cover: (1) what the Bayesian and Poisson model signals indicate and why this market was selected, "
        "(2) any relevant team form, head-to-head, or tactical factors you know about these teams, "
        "(3) a clear recommendation (use **bold** for the pick name). "
        "Write in present/predictive tense — treat every pick as upcoming. "
        "For bets already Won or Lost, briefly mention the outcome at the end. "
        "Return ONLY valid JSON — no markdown fences, no prose outside the JSON object:\n"
        '{"explanations":{"KEY":"analysis text",...}}\n\n'
        f"Bets:\n{bets_text}"
    )

    pseudo = {
        "id":     "explainer",
        "system": _CHAT_SYSTEM + " Always respond with valid JSON only — no markdown fences, no prose outside the JSON.",
        "task":   full_prompt,
        "models": _CHAT_MODELS,
    }

    _, result = await _call_advisor(pseudo, "", settings)
    if isinstance(result, dict) and not result.get("error"):
        raw = result.get("explanations", {})
        return {"explanations": {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}}
    return {"explanations": {}}

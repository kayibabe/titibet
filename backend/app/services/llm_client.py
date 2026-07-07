"""
llm_client.py — Shared multi-provider LLM client for pipeline agents.

Provider cascade for analysis pipelines (loss analysis + strategy):
  1. Groq        — GROQ_API_KEY           — primary free fast Llama inference
  2. Claude      — TITIBET_CLAUDE_KEY      — highest quality, paid
  3. Gemini      — GEMINI_API_KEY          — free, no card required
  4. Cerebras    — CEREBRAS_API_KEY        — free, very fast Llama
  5. Mistral     — MISTRAL_API_KEY         — free open-mistral-nemo

Each provider returns None when billing/quota is exhausted so the next is
tried transparently.  Callers receive the parsed JSON dict or None if all
providers fail.

Usage:
    from app.services.llm_client import call_llm

    result = await call_llm(
        system="You are ...",
        user="Analyse ...",
        model_tier="fast",     # "fast" | "smart"
        response_format="json",
        timeout=30.0,
    )
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Provider endpoint constants (mirror advisor_service.py)
GROQ_URL      = "https://api.groq.com/openai/v1/chat/completions"
CEREBRAS_URL  = "https://api.cerebras.ai/v1/chat/completions"
MISTRAL_URL   = "https://api.mistral.ai/v1/chat/completions"
GEMINI_URL    = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Model selection per provider.  "fast" = smaller/cheaper, "smart" = best available.
_MODELS: dict[str, dict[str, str]] = {
    "groq": {
        "fast":  "llama-3.1-8b-instant",
        "smart": "llama-3.3-70b-versatile",
    },
    "claude": {
        "fast":  "claude-haiku-4-5-20251001",
        "smart": "claude-sonnet-5",
    },
    "gemini": {
        "fast":  "gemini-2.0-flash",
        "smart": "gemini-2.0-flash",
    },
    "cerebras": {
        "fast":  "llama3.3-70b",
        "smart": "llama3.3-70b",
    },
    "mistral": {
        "fast":  "mistral-small-latest",
        "smart": "mistral-small-latest",
    },
}

# Cascade order: Groq first (fast, free), then paid/fallback providers
_CASCADE = ["groq", "claude", "gemini", "cerebras", "mistral"]

MAX_RETRIES = 2
RETRY_DELAYS = [2, 5]


async def call_llm(
    system: str,
    user: str,
    model_tier: str = "smart",
    response_format: str = "json",
    timeout: float = 40.0,
    max_tokens: int = 800,
) -> Optional[dict]:
    """
    Try each configured provider in cascade order.
    Returns parsed JSON dict on success, None if all providers fail.

    model_tier: "fast" uses smaller/cheaper models; "smart" uses best available.
    response_format: "json" enforces JSON output; "text" for free-form.
    """
    from app.core.config import get_settings
    settings = get_settings()

    keys = {
        "groq":     settings.groq_api_key,
        "claude":   settings.titibet_claude_key,
        "gemini":   settings.gemini_api_key,
        "cerebras": settings.cerebras_api_key,
        "mistral":  settings.mistral_api_key,
    }

    for provider in _CASCADE:
        api_key = keys.get(provider, "")
        if not api_key:
            continue

        model = _MODELS[provider][model_tier]
        logger.debug("llm_client: trying %s/%s", provider, model)

        result = await _try_provider(
            provider=provider,
            model=model,
            api_key=api_key,
            system=system,
            user=user,
            response_format=response_format,
            timeout=timeout,
            max_tokens=max_tokens,
        )

        if result is not None:
            logger.debug("llm_client: got response from %s/%s", provider, model)
            return result

        logger.info("llm_client: %s/%s failed/exhausted — trying next provider", provider, model)

    logger.warning("llm_client: all providers failed for call (system=%s...)", system[:60])
    return None


async def _try_provider(
    provider: str,
    model: str,
    api_key: str,
    system: str,
    user: str,
    response_format: str,
    timeout: float,
    max_tokens: int,
) -> Optional[dict]:
    """Call one provider with retry.  Returns None to signal quota exhaustion / permanent failure."""
    if provider == "claude":
        return await _call_claude(model, api_key, system, user, max_tokens, timeout)
    elif provider == "gemini":
        return await _call_gemini(model, api_key, system, user, max_tokens, timeout)
    else:
        # groq, cerebras, mistral — all OpenAI-compatible
        base_urls = {
            "groq":     GROQ_URL,
            "cerebras": CEREBRAS_URL,
            "mistral":  MISTRAL_URL,
        }
        return await _call_openai_compat(
            provider, model, api_key, base_urls[provider],
            system, user, response_format, timeout, max_tokens,
        )


async def _call_claude(
    model: str,
    api_key: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: float,
) -> Optional[dict]:
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url="https://api.anthropic.com",
        )
        msg = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = next((b.text for b in msg.content if b.type == "text"), "")
        if not text:
            return None
        return _parse_json(text)
    except Exception as exc:
        exc_str = str(exc).lower()
        if any(p in exc_str for p in ("credit", "billing", "quota", "insufficient")):
            return None
        logger.warning("llm_client Claude error: %s", exc)
        return None


async def _call_gemini(
    model: str,
    api_key: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: float,
) -> Optional[dict]:
    url = GEMINI_URL.format(model=model)
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature":      0.2,
            "maxOutputTokens":  max_tokens,
        },
    }
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, params={"key": api_key})
                if resp.status_code in (429, 403):
                    return None
                resp.raise_for_status()
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return _parse_json(text)
        except Exception as exc:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            logger.warning("llm_client Gemini error: %s", exc)
            return None
    return None


async def _call_openai_compat(
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    system: str,
    user: str,
    response_format: str,
    timeout: float,
    max_tokens: int,
) -> Optional[dict]:
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "temperature": 0.2,
        "max_tokens":  max_tokens,
    }
    if response_format == "json":
        payload["response_format"] = {"type": "json_object"}

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    base_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                )
                if resp.status_code == 429:
                    body = resp.text.lower()
                    if any(p in body for p in ("tokens per day", "tpd", "quota", "billing")):
                        return None
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_DELAYS[attempt])
                        continue
                    return None
                if resp.status_code in (400, 401, 403, 404, 500, 502, 503):
                    logger.info("llm_client %s %s — skipping", provider, resp.status_code)
                    return None
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return _parse_json(content)
        except Exception as exc:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            logger.warning("llm_client %s error: %s", provider, exc)
            return None
    return None


def _parse_json(text: str) -> Optional[dict]:
    """Parse JSON from LLM response text, stripping markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("llm_client: unparseable JSON response (first 200): %s", text[:200])
        return None

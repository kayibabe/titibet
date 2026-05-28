"""
paystack.py — Paystack API wrapper.

Handles transaction initialization, verification, and webhook signature
validation. All amounts are in the smallest currency unit (kobo/pesewas/cents).
For MWK (Malawian Kwacha) Paystack uses 100 tambala = 1 Kwacha, so multiply
the display amount by 100 before sending.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

PAYSTACK_BASE = "https://api.paystack.co"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_settings().paystack_secret_key}",
        "Content-Type": "application/json",
    }


# ── Plans ─────────────────────────────────────────────────────────────────────

PLANS: list[dict] = [
    {
        "id": "pro_monthly",
        "tier": "pro",
        "label": "Pro",
        "interval": "monthly",
        "price_mwk": 20000,
        "features": [
            "All value signals (19 markets)",
            "Accumulator builder — Safe & Value tiers",
            "Bet tracker with CLV tracking",
            "Analytics & backtest",
        ],
    },
    {
        "id": "pro_yearly",
        "tier": "pro",
        "label": "Pro",
        "interval": "yearly",
        "price_mwk": 240000,
        "features": [
            "Everything in Pro Monthly",
            "2 months free (vs monthly)",
        ],
    },
    {
        "id": "elite_monthly",
        "tier": "elite",
        "label": "Elite",
        "interval": "monthly",
        "price_mwk": 30000,
        "features": [
            "Everything in Pro",
            "Bold accumulator tier (60–100× odds)",
            "AI betting advisor (Groq-powered)",
            "Admin user panel",
            "Priority signal alerts",
        ],
    },
    {
        "id": "elite_yearly",
        "tier": "elite",
        "label": "Elite",
        "interval": "yearly",
        "price_mwk": 360000,
        "features": [
            "Everything in Elite Monthly",
            "2 months free (vs monthly)",
        ],
    },
]

# Map plan id → Paystack plan code (filled from settings at runtime)
def _plan_code(plan_id: str) -> str:
    s = get_settings()
    return {
        "pro_monthly":    s.paystack_plan_pro_monthly,
        "pro_yearly":     s.paystack_plan_pro_yearly,
        "elite_monthly":  s.paystack_plan_elite_monthly,
        "elite_yearly":   s.paystack_plan_elite_yearly,
    }.get(plan_id, "")


def get_plan_by_id(plan_id: str) -> dict | None:
    return next((p for p in PLANS if p["id"] == plan_id), None)


# ── Transaction init ──────────────────────────────────────────────────────────

async def initialize_transaction(
    email: str,
    plan_id: str,
    user_id: int,
) -> dict[str, Any]:
    """
    Initialize a Paystack transaction / subscription.
    Returns the Paystack response body (contains authorization_url).
    """
    plan = get_plan_by_id(plan_id)
    if not plan:
        raise ValueError(f"Unknown plan: {plan_id}")

    plan_code = _plan_code(plan_id)
    s = get_settings()
    amount_tambala = plan["price_mwk"] * 100  # smallest unit

    payload: dict[str, Any] = {
        "email": email,
        "amount": amount_tambala,
        "currency": s.paystack_currency,
        "callback_url": s.paystack_callback_url,
        "metadata": {
            "user_id": user_id,
            "plan_id": plan_id,
            "tier": plan["tier"],
            "cancel_action": s.paystack_callback_url,
        },
    }
    if plan_code:
        payload["plan"] = plan_code

    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.post(
            f"{PAYSTACK_BASE}/transaction/initialize",
            json=payload,
            headers=_headers(),
        )
        data = res.json()

    if not data.get("status"):
        log.error("Paystack init failed: %s", data)
        raise RuntimeError(data.get("message", "Paystack error"))

    return data["data"]  # { authorization_url, access_code, reference }


# ── Verify ────────────────────────────────────────────────────────────────────

async def verify_transaction(reference: str) -> dict[str, Any]:
    """
    Verify a completed transaction by reference.
    Returns the full transaction object from Paystack.
    Raises RuntimeError if verification fails or payment not successful.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(
            f"{PAYSTACK_BASE}/transaction/verify/{reference}",
            headers=_headers(),
        )
        data = res.json()

    if not data.get("status"):
        raise RuntimeError(data.get("message", "Verification failed"))

    tx = data["data"]
    if tx.get("status") != "success":
        raise RuntimeError(f"Payment not successful: {tx.get('status')}")

    return tx


# ── Webhook validation ────────────────────────────────────────────────────────

def validate_webhook(payload_bytes: bytes, signature: str) -> bool:
    """
    Validate Paystack webhook HMAC-SHA512 signature.
    Paystack sends X-Paystack-Signature: <hex digest>.
    """
    secret = get_settings().paystack_secret_key
    if not secret:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Tier resolution from webhook event ───────────────────────────────────────

def tier_from_event(event: dict) -> tuple[str, str] | None:
    """
    Extract (tier, plan_id) from a Paystack charge.success or
    subscription.create event. Returns None if metadata is missing.
    """
    data = event.get("data", {})
    meta = data.get("metadata") or {}
    tier = meta.get("tier")
    plan_id = meta.get("plan_id")
    if tier and plan_id:
        return tier, plan_id
    return None

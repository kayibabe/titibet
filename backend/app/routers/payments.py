"""
payments.py — Paystack payment endpoints.

POST /api/payments/plans          — list available plans (public)
POST /api/payments/initialize     — start a checkout session (auth required)
GET  /api/payments/verify         — verify a completed payment by reference
POST /api/payments/webhook        — Paystack server-to-server callback (no auth)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services.email import send_payment_confirmation
from app.services.paystack import (
    PLANS,
    get_plan_by_id,
    initialize_transaction,
    validate_webhook,
    verify_transaction,
    tier_from_event,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/payments", tags=["payments"])


# ── Plans ─────────────────────────────────────────────────────────────────────

@router.get("/plans")
async def list_plans():
    """Return available subscription plans (no auth required)."""
    return PLANS


# ── Initialize ────────────────────────────────────────────────────────────────

class InitRequest(BaseModel):
    plan_id: str


@router.post("/initialize")
async def initialize_payment(
    body: InitRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """
    Create a Paystack checkout session.
    Returns { authorization_url, reference } — frontend redirects to authorization_url.
    """
    plan = get_plan_by_id(body.plan_id)
    if not plan:
        raise HTTPException(400, f"Unknown plan: {body.plan_id}")

    try:
        data = await initialize_transaction(
            email=current_user.email,
            plan_id=body.plan_id,
            user_id=current_user.id,
        )
    except (ValueError, RuntimeError) as e:
        raise HTTPException(502, str(e))

    return {
        "authorization_url": data["authorization_url"],
        "reference": data["reference"],
        "plan": plan,
    }


# ── Verify (frontend callback) ────────────────────────────────────────────────

@router.get("/verify")
async def verify_payment(
    background_tasks: BackgroundTasks,
    reference: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Called by the frontend after Paystack redirects back.
    Verifies the payment and upgrades the user's tier.
    """
    try:
        tx = await verify_transaction(reference)
    except RuntimeError as e:
        raise HTTPException(402, str(e))

    meta = tx.get("metadata") or {}
    plan_id = meta.get("plan_id")
    tier = meta.get("tier")

    if not tier or not plan_id:
        raise HTTPException(400, "Missing metadata in transaction")

    plan = get_plan_by_id(plan_id)
    if plan:
        interval = plan.get("interval", "monthly")
        days = 365 if interval == "yearly" else 31
        current_user.tier = tier
        current_user.subscription_status = "active"
        current_user.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=days)
        current_user.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(current_user)
        expires_str = current_user.subscription_expires_at.strftime("%d %b %Y") if current_user.subscription_expires_at else ""
        background_tasks.add_task(send_payment_confirmation, current_user.email, current_user.name or "", tier, expires_str)

    return {
        "status": "success",
        "tier": current_user.tier,
        "subscription_status": current_user.subscription_status,
        "expires_at": current_user.subscription_expires_at,
    }


# ── Webhook ───────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def paystack_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_paystack_signature: str = Header(default=""),
):
    """
    Paystack server-to-server webhook.
    Handles charge.success and subscription.disable events.
    Must return 200 quickly — Paystack retries on non-2xx.
    """
    body_bytes = await request.body()

    if not validate_webhook(body_bytes, x_paystack_signature):
        log.warning("Paystack webhook: invalid signature")
        raise HTTPException(401, "Invalid webhook signature")

    try:
        event = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON")

    event_type = event.get("event", "")
    log.info("Paystack webhook: %s", event_type)

    if event_type in ("charge.success", "subscription.create"):
        await _handle_charge_success(event, db)
    elif event_type == "subscription.disable":
        await _handle_subscription_disable(event, db)

    return {"received": True}


async def _handle_charge_success(event: dict, db: AsyncSession) -> None:
    result = tier_from_event(event)
    if not result:
        return
    tier, plan_id = result

    data = event.get("data", {})
    meta = data.get("metadata") or {}
    user_id = meta.get("user_id")
    if not user_id:
        return

    user = await db.get(User, int(user_id))
    if not user:
        log.warning("Paystack webhook: user %s not found", user_id)
        return

    plan = get_plan_by_id(plan_id)
    interval = plan.get("interval", "monthly") if plan else "monthly"
    days = 365 if interval == "yearly" else 31

    user.tier = tier
    user.subscription_status = "active"
    user.subscription_expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    user.updated_at = datetime.now(timezone.utc)

    # Store Paystack customer code if present
    customer = data.get("customer") or {}
    if customer.get("customer_code"):
        user.payment_customer_id = customer["customer_code"]

    await db.commit()
    log.info("Upgraded user %s to %s via webhook", user_id, tier)
    expires_str = user.subscription_expires_at.strftime("%d %b %Y") if user.subscription_expires_at else ""
    try:
        await send_payment_confirmation(user.email, user.name or "", tier, expires_str)
    except Exception:
        pass


async def _handle_subscription_disable(event: dict, db: AsyncSession) -> None:
    data = event.get("data", {})
    customer = data.get("customer") or {}
    customer_code = customer.get("customer_code")
    if not customer_code:
        return

    from sqlalchemy import select
    result = await db.execute(
        select(User).where(User.payment_customer_id == customer_code)
    )
    user = result.scalar_one_or_none()
    if not user:
        return

    user.subscription_status = "cancelled"
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    log.info("Subscription cancelled for user %s", user.id)

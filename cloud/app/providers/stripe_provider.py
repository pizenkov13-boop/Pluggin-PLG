"""Stripe checkout + webhook stub (EU/US — after 200 users)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request
from supabase import Client

from cloud.app.billing import activate_subscription, claim_idempotency, release_claim_on_failure, enter_grace_period
from cloud.app.config import BILLING_RETURN_URL, STRIPE_PRICE_ID_INTL, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET

logger = logging.getLogger("plg.billing.stripe")


def create_checkout(user_id: str, price_tier: str = "other") -> dict[str, Any]:
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID_INTL:
        raise HTTPException(503, "Stripe not configured (STRIPE_SECRET_KEY / STRIPE_PRICE_ID_INTL).")

    try:
        import stripe
    except ImportError as exc:
        raise HTTPException(503, "stripe package not installed.") from exc

    stripe.api_key = STRIPE_SECRET_KEY
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_ID_INTL, "quantity": 1}],
        success_url=BILLING_RETURN_URL + "?provider=stripe",
        cancel_url=BILLING_RETURN_URL + "?cancelled=1",
        client_reference_id=user_id,
        metadata={"user_id": user_id, "price_tier": price_tier},
    )
    return {
        "provider": "stripe",
        "session_id": session.id,
        "confirmation_url": session.url,
        "price_tier": price_tier,
    }


async def handle_webhook(client: Client, request: Request) -> dict[str, str]:
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(503, "STRIPE_WEBHOOK_SECRET not configured.")

    try:
        import stripe
    except ImportError as exc:
        raise HTTPException(503, "stripe package not installed.") from exc

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        raise HTTPException(400, "Invalid Stripe signature.") from exc

    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    data_obj = (event.get("data") or {}).get("object") or {}

    idem_key = event_id or f"{event_type}:{data_obj.get('id')}"
    if claim_idempotency(
        client,
        provider="stripe",
        idempotency_key=idem_key,
        event_type=event_type,
        external_id=str(data_obj.get("id") or ""),
        payload=dict(event),
    ):
        return {"ok": "duplicate"}

    with release_claim_on_failure(client, provider="stripe", idempotency_key=idem_key):
        user_id = str(
            data_obj.get("client_reference_id")
            or (data_obj.get("metadata") or {}).get("user_id")
            or ""
        )
        price_tier = str((data_obj.get("metadata") or {}).get("price_tier") or "other")

        if event_type == "checkout.session.completed" and user_id:
            sub_id = str(data_obj.get("subscription") or data_obj.get("id") or "")
            activate_subscription(
                client,
                user_id,
                provider="stripe",
                external_id=sub_id,
                price_tier=price_tier,
            )
            return {"ok": "activated"}

        if event_type in ("invoice.payment_failed", "customer.subscription.deleted") and user_id:
            enter_grace_period(client, user_id)
            return {"ok": "grace"}

        return {"ok": "ignored"}

"""Paddle webhook stub (global VAT — enable after CIS launch)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import HTTPException, Request
from supabase import Client

from cloud.app.billing import activate_subscription, claim_idempotency, release_claim_on_failure, enter_grace_period, cancel_subscription
from cloud.app.config import PADDLE_WEBHOOK_SECRET

logger = logging.getLogger("plg.billing.paddle")


def _verify_signature(payload: bytes, signature: str) -> bool:
    if not PADDLE_WEBHOOK_SECRET:
        return False
    expected = hmac.new(
        PADDLE_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_webhook(client: Client, request: Request) -> dict[str, str]:
    if not PADDLE_WEBHOOK_SECRET:
        raise HTTPException(503, "PADDLE_WEBHOOK_SECRET not configured.")

    payload = await request.body()
    sig = request.headers.get("paddle-signature", "")
    if not _verify_signature(payload, sig):
        raise HTTPException(400, "Invalid Paddle signature.")

    body: dict[str, Any] = json.loads(payload)
    event_type = str(body.get("event_type") or body.get("alert_name") or "")
    event_id = str(body.get("event_id") or body.get("alert_id") or event_type)
    data = body.get("data") or body
    custom = data.get("custom_data") or {}

    user_id = str(custom.get("user_id") or data.get("passthrough") or "")
    price_tier = str(custom.get("price_tier") or "other")

    if claim_idempotency(
        client,
        provider="paddle",
        idempotency_key=event_id,
        event_type=event_type,
        user_id=user_id or None,
        external_id=str(data.get("subscription_id") or data.get("id") or ""),
        payload=body,
    ):
        return {"ok": "duplicate"}

    with release_claim_on_failure(client, provider="paddle", idempotency_key=event_id):
        if event_type in ("subscription.created", "subscription.activated", "subscription_payment_succeeded"):
            if user_id:
                sub_id = str(data.get("subscription_id") or data.get("id") or event_id)
                activate_subscription(
                    client,
                    user_id,
                    provider="paddle",
                    external_id=sub_id,
                    price_tier=price_tier,
                )
                return {"ok": "activated"}

        if event_type in ("subscription.payment_failed", "subscription.past_due"):
            if user_id:
                enter_grace_period(client, user_id)
            return {"ok": "grace"}

        if event_type in ("subscription.cancelled", "subscription.canceled"):
            if user_id:
                cancel_subscription(client, user_id)
            return {"ok": "cancelled"}

        return {"ok": "ignored"}

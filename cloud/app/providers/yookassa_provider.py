"""ЮKassa checkout + webhook (domestic RUB — 499 ₽, or 299 ₽ on the launch promo)."""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from supabase import Client

from cloud.app.billing import activate_subscription, claim_idempotency, release_claim_on_failure, enter_grace_period, expire_subscription
from cloud.app.config import (
    BILLING_RETURN_URL,
    YOOKASSA_SECRET_KEY,
    YOOKASSA_SHOP_ID,
    YOOKASSA_VAT_CODE,
)
from cloud.app.legal import _user_email
from cloud.app.pricing import is_domestic, normalize_tier, price_entry

logger = logging.getLogger("plg.billing.yookassa")


def _configured() -> bool:
    return bool(YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY)


def create_checkout(
    client: Client, user_id: str, price_tier: str = "ru", referral_code: str | None = None
) -> dict[str, Any]:
    if not _configured():
        raise HTTPException(503, "ЮKassa credentials missing (YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY).")
    tier = normalize_tier(price_tier)
    if not is_domestic(tier):
        raise HTTPException(400, "ЮKassa serves the domestic (RUB) tier only. Use Prodamus for international.")

    try:
        from yookassa import Configuration, Payment
    except ImportError as exc:
        raise HTTPException(503, "yookassa package not installed.") from exc

    Configuration.configure(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
    idempotence_key = str(uuid.uuid4())
    amount_rub = price_entry(tier)["amount_minor"] / 100
    amount = f"{amount_rub:.2f}"
    customer_email = _user_email(client, user_id)
    if not customer_email:
        raise HTTPException(400, "A verified account email is required to issue the payment receipt.")

    metadata: dict[str, Any] = {"user_id": user_id, "price_tier": tier}
    if referral_code:
        metadata["referral_code"] = referral_code

    payment = Payment.create(
        {
            "amount": {"value": amount, "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": BILLING_RETURN_URL},
            "capture": True,
            "description": "Пакет PLG — 30 дней доступа",
            "metadata": metadata,
            "receipt": {
                "customer": {"email": customer_email},
                "items": [
                    {
                        "description": "Пакет PLG — 30 дней доступа",
                        "quantity": "1.00",
                        "amount": {"value": amount, "currency": "RUB"},
                        "vat_code": YOOKASSA_VAT_CODE,
                        "payment_mode": "full_payment",
                        "payment_subject": "service",
                    }
                ],
                "internet": True,
            },
        },
        idempotence_key,
    )

    confirmation = payment.confirmation
    url = getattr(confirmation, "confirmation_url", None) if confirmation else None
    if not url:
        raise HTTPException(502, "ЮKassa did not return a payment URL.")

    return {
        "provider": "yookassa",
        "payment_id": payment.id,
        "confirmation_url": url,
        "amount_rub": int(amount_rub) if amount_rub == int(amount_rub) else amount_rub,
        "price_tier": tier,
    }


def _fetch_payment(payment_id: str) -> Any:
    from yookassa import Configuration, Payment

    Configuration.configure(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
    return Payment.find_one(payment_id)


def _payment_amount(payment: Any) -> tuple[Decimal, str]:
    """Read the authoritative amount returned by YooKassa without floats."""
    amount = getattr(payment, "amount", None)
    value = amount.get("value") if isinstance(amount, dict) else getattr(amount, "value", None)
    currency = amount.get("currency") if isinstance(amount, dict) else getattr(amount, "currency", None)
    try:
        return Decimal(str(value)), str(currency or "")
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(400, "Payment has an invalid amount.") from exc


def handle_webhook(client: Client, body: dict[str, Any]) -> dict[str, str]:
    if not _configured():
        raise HTTPException(503, "ЮKassa not configured.")

    event = str(body.get("event") or "")
    obj = body.get("object") or {}
    object_id = str(obj.get("id") or "")
    if not object_id:
        raise HTTPException(400, "Missing object id in webhook.")

    # A refund webhook contains the refund id in ``object.id``.  That id is
    # not a payment id and Payment.find_one(refund_id) returns YooKassa 404.
    # Use the linked original payment to recover our metadata, while keeping
    # the refund id as the idempotency/external id for this event.
    if event == "refund.succeeded":
        payment_id = str(obj.get("payment_id") or "")
        if not payment_id:
            raise HTTPException(400, "Refund missing original payment id.")
        external_id = object_id
    else:
        payment_id = object_id
        external_id = payment_id

    payment = _fetch_payment(payment_id)
    status = str(getattr(payment, "status", "") or "")
    metadata = getattr(payment, "metadata", None) or {}
    user_id = str(metadata.get("user_id") or "")
    price_tier = normalize_tier(metadata.get("price_tier"))
    referral_code = str(metadata.get("referral_code") or "") or None

    amount_rub: Decimal | None = None
    currency = ""
    if event == "payment.succeeded" and status == "succeeded":
        amount_rub, currency = _payment_amount(payment)
        # The amount is validated against the tier the payment was created on
        # (metadata.price_tier) — `ru` -> 499 ₽, `ru_promo` -> 299 ₽. Both are
        # legitimate RUB amounts; the frozen tier says which one this payment
        # owes.
        expected_amount = Decimal(str(price_entry(price_tier)["amount_minor"])) / 100
        if currency != "RUB" or amount_rub != expected_amount:
            logger.error(
                "yookassa payment %s rejected: amount=%s %s expected=%s RUB (tier=%s)",
                payment_id,
                amount_rub,
                currency,
                expected_amount,
                price_tier,
            )
            raise HTTPException(400, "Payment amount does not match the subscription price.")

    idem_key = f"{event}:{external_id}"
    if claim_idempotency(
        client,
        provider="yookassa",
        idempotency_key=idem_key,
        event_type=event,
        user_id=user_id or None,
        amount_cents=(int(amount_rub * 100) if amount_rub is not None else None),
        currency=currency or None,
        external_id=external_id,
        price_tier=price_tier,
        referral_code=referral_code,
        payload=body,
    ):
        return {"ok": "duplicate"}

    with release_claim_on_failure(client, provider="yookassa", idempotency_key=idem_key):
        if event == "payment.succeeded" and status == "succeeded":
            if not user_id:
                logger.error("yookassa payment %s missing user_id metadata", payment_id)
                raise HTTPException(400, "Payment missing user_id metadata.")
            activate_subscription(
                client,
                user_id,
                provider="yookassa",
                external_id=payment_id,
                price_tier=price_tier,
            )
            try:
                from cloud.app.analytics_ops import track_event
                from cloud.app.email import notify_payment_success
                from cloud.app.legal import _user_email

                track_event(client, "payment_completed", user_id=user_id)
                email = _user_email(client, user_id)
                if email:
                    notify_payment_success(client, user_id, email)
            except Exception:  # noqa: BLE001
                logger.exception("post-payment notify failed")
            return {"ok": "activated"}

        if event in ("payment.canceled", "payment.waiting_for_capture") and status == "canceled":
            if user_id:
                profile = client.table("profiles").select("status").eq("id", user_id).maybe_single().execute()
                if profile.data and profile.data.get("status") == "active":
                    enter_grace_period(client, user_id)
            return {"ok": "grace"}

        if event == "refund.succeeded":
            if user_id:
                expire_subscription(client, user_id)
            return {"ok": "refunded"}

        logger.info("yookassa webhook ignored event=%s status=%s", event, status)
        return {"ok": "ignored"}

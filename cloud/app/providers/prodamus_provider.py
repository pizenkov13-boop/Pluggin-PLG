"""Prodamus checkout + webhook — every price-book tier (``ru``, ``ru_promo``,
``eu``, ``other``, ``by``).

**2026-09-29 update:** Prodamus is now the *preferred* provider for the
domestic ``ru``/``ru_promo`` tiers too — ``pick_checkout_provider`` in
billing.py checks ``PRODAMUS_FORM_URL``/``PRODAMUS_SECRET_KEY`` before
YooKassa's. YooKassa is the fallback when Prodamus isn't configured, not the
other way around; its code (``yookassa_provider.py``) is unchanged and still
live. This reverses the earlier "ru stays on YooKassa permanently" decision
in docs/PRODAMUS_INTL_BILLING_PLAN.md — see that file and the project issue log (not included)
§0ai for the history. The admin-only test-checkout route
(``create_checkout(..., allow_any_tier=True)``) still exists for routing a
payment through Prodamus on a tier the price book doesn't map to it, but is
no longer needed for ``ru`` specifically since that's now a normal
Prodamus-served tier.

Prodamus is a signed payform, not a resource REST API: ``create_checkout``
does one GET with ``do=link`` and the response body is the payment URL;
``handle_webhook`` verifies the HMAC in the ``Sign`` header and routes on
``payment_status``. The exact non-success status vocabulary (failed /
cancelled / refund) is not confirmed in public docs (see plan §8.5) — until
Prodamus confirms it, anything that isn't ``success`` is treated as a
failure/grace signal, mirroring the plan's documented fallback.

**2026-09-29 incident, part 1:** ``create_checkout`` briefly requested
``callbackType=json`` (commit 4c78b94), on the unverified assumption that
Prodamus's JSON notification uses the same flat field names as the
documented form-urlencoded one. Reverted (commit 834a8d2): ``create_checkout``
no longer sends ``callbackType``. ``handle_webhook`` still parses a JSON body
defensively if one arrives, but nothing requests it anymore.

**2026-09-29 incident, part 2 — the real bug.** After the revert above, a
real ``ru_promo`` payment (order 49304825, 299 RUB via SBP) *still* 400'd on
"Payment has an invalid amount", proving the JSON theory was never the
actual cause. Sentry captured the real form-urlencoded webhook's field
names: ``sum``, ``currency``, ``commission``, ``commission_sum``,
``products[0][sum]``, ... — **there is no ``currency_sum`` field at all.**
The amount-check had been reading ``currency_sum`` since the very first
version of this module (commit 0293dcd), based on
docs/PRODAMUS_INTL_BILLING_PLAN.md §5's field list, which was written before
any real payload was ever seen and was simply wrong. The real field is
``sum`` (the payment total, in ``currency``). Fixed: amount-check now reads
``sum``. Unverified for the still-flag-gated ``eu``/``other``/``by``
(foreign-currency) tiers specifically — no real international payment has
gone through yet (``intl_billing`` is off) — but ``sum``+``currency`` is the
only field pair Prodamus's real notification actually contains, so it's the
correct pair to check regardless of currency. See the project issue log (not included) §0ai.

Fiscal receipts for every currency here (EUR/USD/BYN, and a ru test payment)
are issued by Prodamus itself, always in RUB at that day's CBR rate — this
module never computes or sends a receipt/tax breakdown (contrast
``yookassa_provider``'s inline ``receipt`` block, required because YooKassa
bills in RUB directly). ``products[0][price]``/``currency`` below are in the
selling currency's own units; the RUB conversion is entirely Prodamus-side
and this code doesn't need to know or check it (plan §5 amount-check compares
the selling-currency pair, never the RUB receipt amount).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from fastapi import HTTPException, Request
from supabase import Client

from cloud.app.billing import activate_subscription, claim_idempotency, release_claim_on_failure, enter_grace_period, expire_subscription
from cloud.app.config import BILLING_RETURN_URL, PRODAMUS_FORM_URL, PRODAMUS_SECRET_KEY
from cloud.app.legal import _user_email
from cloud.app.pricing import normalize_tier, price_entry

logger = logging.getLogger("plg.billing.prodamus")

# Confirmed refund status values are unknown (plan §8.5, open question to
# Prodamus). Nothing maps here yet — a refund currently lands in the
# catch-all "not success" branch, same as a failed/cancelled payment.
_REFUND_STATUSES: frozenset[str] = frozenset()


def _configured() -> bool:
    return bool(PRODAMUS_FORM_URL and PRODAMUS_SECRET_KEY)


def _normalize_for_sign(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalize_for_sign(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_normalize_for_sign(v) for v in value]
    return "" if value is None else str(value)


def _sign(payload: dict[str, Any], secret: str) -> str:
    """HMAC-SHA256 over the recursively key-sorted, all-string-cast payload.

    Mirrors Prodamus's PHP ``class-hmac.php`` normalization (plan §8.2):
    cast every value to string, ``ksort`` every level (including nested
    arrays), compact JSON with unescaped unicode, then HMAC-SHA256 hex.
    PHP's ``json_encode`` escapes ``/`` as ``\\/`` by default (no
    ``JSON_UNESCAPED_SLASHES``); confirmed against a real Prodamus payment
    2026-09-28 — without this the signature doesn't match theirs whenever a
    value contains a slash (e.g. ``urlSuccess``/``urlReturn``).
    """
    body = json.dumps(_normalize_for_sign(payload), ensure_ascii=False, separators=(",", ":")).replace(
        "/", "\\/"
    )
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def _flatten(payload: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """PHP-style bracket-notation query params: ``products[0][name]=...``."""
    items: list[tuple[str, str]] = []
    for key, value in payload.items():
        full_key = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            items.extend(_flatten(value, full_key))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                item_key = f"{full_key}[{i}]"
                if isinstance(item, dict):
                    items.extend(_flatten(item, item_key))
                else:
                    items.append((item_key, str(item)))
        else:
            items.append((full_key, str(value)))
    return items


def _parse_bracket_key(raw_key: str) -> list[str]:
    parts: list[str] = []
    part = ""
    for ch in raw_key:
        if ch == "[":
            if part:
                parts.append(part)
                part = ""
        elif ch == "]":
            if part:
                parts.append(part)
                part = ""
        else:
            part += ch
    if part:
        parts.append(part)
    return parts


def _listify(node: Any) -> Any:
    if isinstance(node, dict):
        node = {k: _listify(v) for k, v in node.items()}
        if node and all(k.isdigit() for k in node):
            return [node[k] for k in sorted(node, key=int)]
        return node
    return node


def _nest_form(items: list[tuple[str, str]]) -> dict[str, Any]:
    """Reconstruct the nested dict/list structure PHP's ``$_POST`` would build
    from bracket-notation form keys, for HMAC verification."""
    root: dict[str, Any] = {}
    for raw_key, value in items:
        parts = _parse_bracket_key(raw_key)
        if not parts:
            continue
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return _listify(root)


def create_checkout(
    client: Client,
    user_id: str,
    price_tier: str,
    referral_code: str | None = None,
    *,
    allow_any_tier: bool = False,
) -> dict[str, Any]:
    """``allow_any_tier`` is for the admin-only one-off test checkout
    (``POST /v1/admin/billing/prodamus-test-checkout``) that proves the
    webhook/HMAC against a real payment *before* ``ru`` is ever routed to
    Prodamus by default. The regular checkout path (``pick_checkout_provider``
    -> this function) never passes it, so customer ``ru`` traffic still goes
    to YooKassa exactly as before — this flag doesn't change that routing."""
    if not _configured():
        raise HTTPException(503, "Prodamus credentials missing (PRODAMUS_FORM_URL / PRODAMUS_SECRET_KEY).")
    tier = normalize_tier(price_tier)
    entry = price_entry(tier)
    if not allow_any_tier and entry["provider"] != "prodamus":
        raise HTTPException(400, f"Prodamus does not serve tier {tier!r}.")

    customer_email = _user_email(client, user_id)
    if not customer_email:
        raise HTTPException(400, "A verified account email is required to issue the payment receipt.")

    order_id = str(uuid.uuid4())
    amount = Decimal(str(entry["amount_minor"])) / 100
    currency = str(entry["currency"]).lower()

    payload: dict[str, Any] = {
        "do": "link",
        # Prodamus-side source label, approved for our account 2026-09-28.
        "sys": "pluggin",
        "order_id": order_id,
        "currency": currency,
        "customer_email": customer_email,
        "products": [
            {
                "name": "Пакет PLG — 30 дней доступа",
                "price": f"{amount:.2f}",
                "quantity": "1",
                "tax": "none",
            }
        ],
        "urlReturn": BILLING_RETURN_URL,
        "urlSuccess": BILLING_RETURN_URL,
        "_param_user_id": user_id,
        "_param_price_tier": tier,
    }
    if referral_code:
        payload["_param_referral_code"] = referral_code

    payload["signature"] = _sign(payload, PRODAMUS_SECRET_KEY)

    try:
        response = httpx.get(
            PRODAMUS_FORM_URL, params=_flatten(payload), timeout=15.0, follow_redirects=True
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Prodamus checkout request failed.") from exc

    confirmation_url = response.text.strip()
    if not confirmation_url.startswith("http"):
        raise HTTPException(502, "Prodamus did not return a payment URL.")

    return {
        "provider": "prodamus",
        "order_id": order_id,
        "confirmation_url": confirmation_url,
        "amount": float(amount),
        "currency": entry["currency"],
        "price_tier": tier,
    }


async def handle_webhook(client: Client, request: Request) -> dict[str, str]:
    if not _configured():
        raise HTTPException(503, "Prodamus not configured.")

    # create_checkout no longer requests callbackType=json (2026-09-29
    # incident, see module docstring), so Prodamus should send its default
    # form-urlencoded body — but branch on Content-Type anyway in case a
    # checkout link created before the revert is still pending, or Prodamus
    # ever defaults to JSON on their end.
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            nested: dict[str, Any] = dict(await request.json())
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, "Invalid Prodamus webhook JSON body.") from exc
        flat: dict[str, Any] = nested
    else:
        form_items = list((await request.form()).multi_items())
        flat = {k: v for k, v in form_items}
        nested = _nest_form(form_items)

    # Always logged, before any validation can reject the request — the only
    # record of what Prodamus actually sent if a field name/shape assumption
    # turns out wrong again (2026-09-29: a bad assumption here cost us a real
    # payment's activation with no way to see why from logs alone).
    logger.info("prodamus webhook received: content_type=%s keys=%s", content_type, sorted(flat.keys()))

    sign_header = request.headers.get("Sign", "")
    if not sign_header:
        raise HTTPException(400, "Missing Prodamus signature.")
    verify_payload = {
        k: v for k, v in nested.items() if k not in ("signature", "sign", "_payform_sign")
    }
    expected = _sign(verify_payload, PRODAMUS_SECRET_KEY)
    if not hmac.compare_digest(expected.lower(), str(sign_header).lower()):
        raise HTTPException(400, "Invalid Prodamus signature.")

    order_id = str(flat.get("order_id") or "")
    if not order_id:
        raise HTTPException(400, "Missing order_id in webhook.")
    payment_status = str(flat.get("payment_status") or "")
    user_id = str(flat.get("_param_user_id") or "")
    price_tier = normalize_tier(flat.get("_param_price_tier"))
    referral_code = str(flat.get("_param_referral_code") or "") or None

    currency = str(flat.get("currency") or "").lower()
    paid_amount: Decimal | None = None
    if payment_status == "success":
        entry = price_entry(price_tier)
        try:
            paid_amount = Decimal(str(flat.get("sum")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            logger.error(
                "prodamus payment %s: unparseable sum=%r (raw keys=%s)",
                order_id,
                flat.get("sum"),
                sorted(flat.keys()),
            )
            raise HTTPException(400, "Payment has an invalid amount.") from exc
        expected_amount = Decimal(str(entry["amount_minor"])) / 100
        if currency != str(entry["currency"]).lower() or paid_amount != expected_amount:
            logger.error(
                "prodamus payment %s rejected: amount=%s %s expected=%s %s (tier=%s)",
                order_id,
                paid_amount,
                currency,
                expected_amount,
                entry["currency"],
                price_tier,
            )
            raise HTTPException(400, "Payment amount does not match the subscription price.")

    idem_key = f"{payment_status}:{order_id}"
    # Stored event_type must match yookassa_provider's "payment.succeeded" for
    # a success, not Prodamus's own "success" literal — billing.py
    # (activate_subscription's current_payment_event_id lookup), promo.py
    # (promo_paid_count) and analytics_ops.py (admin_metrics,
    # admin_referral_report) all filter payment_events on that one literal,
    # provider-agnostically. Getting this wrong doesn't error — it silently
    # undercounts revenue/referrals and leaves current_payment_event_id NULL.
    # payment_status itself (not this) still drives idem_key/refund routing.
    event_type = "payment.succeeded" if payment_status == "success" else (payment_status or "unknown")
    if claim_idempotency(
        client,
        provider="prodamus",
        idempotency_key=idem_key,
        event_type=event_type,
        user_id=user_id or None,
        amount_cents=(int(paid_amount * 100) if paid_amount is not None else None),
        currency=currency or None,
        external_id=order_id,
        price_tier=price_tier,
        referral_code=referral_code,
        payload=flat,
    ):
        return {"ok": "duplicate"}

    with release_claim_on_failure(client, provider="prodamus", idempotency_key=idem_key):
        if payment_status == "success":
            if not user_id:
                logger.error("prodamus payment %s missing _param_user_id", order_id)
                raise HTTPException(400, "Payment missing user_id metadata.")
            activate_subscription(
                client,
                user_id,
                provider="prodamus",
                external_id=order_id,
                price_tier=price_tier,
            )
            try:
                from cloud.app.analytics_ops import track_event
                from cloud.app.email import notify_payment_success
                from cloud.app.legal import _user_email as _lookup_email

                track_event(client, "payment_completed", user_id=user_id)
                email = _lookup_email(client, user_id)
                if email:
                    notify_payment_success(client, user_id, email)
            except Exception:  # noqa: BLE001
                logger.exception("post-payment notify failed")
            return {"ok": "activated"}

        if payment_status in _REFUND_STATUSES:
            if user_id:
                expire_subscription(client, user_id)
            return {"ok": "refunded"}

        # Not a confirmed success or refund status — failed/cancelled/expired
        # (or an unmapped refund, see module docstring). If the subscriber was
        # active, drop them to grace like a failed renewal; otherwise no-op.
        if user_id:
            profile = client.table("profiles").select("status").eq("id", user_id).maybe_single().execute()
            if profile.data and profile.data.get("status") == "active":
                enter_grace_period(client, user_id)
        logger.info("prodamus webhook status=%s order=%s treated as non-success", payment_status, order_id)
        return {"ok": "grace"}

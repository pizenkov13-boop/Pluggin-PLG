"""Short-lived, one-use links from the desktop app to the web checkout."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from supabase import Client

from cloud.app.config import BILLING_CHECKOUT_LINK_TTL_SECONDS
from cloud.app.pricing import normalize_tier, price_label


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _not_available() -> HTTPException:
    # Deliberately use the same response for missing, expired and used links.
    return HTTPException(404, "Эта ссылка на оплату больше не доступна. Вернитесь в приложение и создайте новую.")


def create_checkout_link(client: Client, *, user_id: str, price_tier: str) -> str:
    now = _utc_now()
    token = secrets.token_urlsafe(32)
    client.table("billing_checkout_links").insert(
        {
            "token_hash": _token_hash(token),
            "user_id": user_id,
            "price_tier": price_tier,
            "expires_at": (now + timedelta(seconds=BILLING_CHECKOUT_LINK_TTL_SECONDS)).isoformat(),
        }
    ).execute()
    return token


def read_checkout_link(client: Client, token: str) -> dict[str, Any]:
    if not token or len(token) > 256:
        raise _not_available()
    result = (
        client.table("billing_checkout_links")
        .select("token_hash,user_id,price_tier,price_locked,expires_at,consumed_at")
        .eq("token_hash", _token_hash(token))
        .maybe_single()
        .execute()
    )
    row = result.data or {}
    expires_at = str(row.get("expires_at") or "")
    if not row or row.get("consumed_at") or not expires_at:
        raise _not_available()
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _not_available() from exc
    if expires <= _utc_now():
        raise _not_available()
    return row


def consume_checkout_link(client: Client, token: str) -> dict[str, Any]:
    """Atomically claim a link before issuing a provider payment."""
    row = read_checkout_link(client, token)
    result = (
        client.table("billing_checkout_links")
        .update({"consumed_at": _utc_now().isoformat()})
        .eq("token_hash", _token_hash(token))
        .is_("consumed_at", "null")
        .execute()
    )
    if not result.data:
        raise _not_available()
    return row


def lock_checkout_link_price(
    client: Client,
    token: str,
    row: dict[str, Any],
    *,
    country: str | None,
    requested_region: str | None = None,
) -> str:
    """Resolve the price-book key for this link from the visitor's geo and
    freeze it onto the row — once. The first view of a link wins; later views
    (page reload, changed IP) get back the frozen tier, so the amount shown on
    checkout.html can't drift before the pay click.

    ``row`` is updated in place so the caller sees the frozen value.
    """
    if row.get("price_locked"):
        return normalize_tier(row.get("price_tier"))

    from cloud.app.billing import resolve_web_offer_tier

    # This call commits the tier onto the link (price_locked=True right below),
    # which is what promo_taken_count() counts — so, unlike a plain price
    # lookup, it must respect the checkout-link reservation buffer too.
    tier = resolve_web_offer_tier(
        country, requested_region=requested_region, client=client, enforce_link_budget=True
    )
    (
        client.table("billing_checkout_links")
        .update({"price_tier": tier, "price_locked": True})
        .eq("token_hash", _token_hash(token))
        .eq("price_locked", False)
        .execute()
    )
    row["price_tier"] = tier
    row["price_locked"] = True
    return tier


def public_checkout_offer(row: dict[str, Any], *, tier: str | None = None) -> dict[str, str]:
    key = normalize_tier(tier if tier is not None else row.get("price_tier"))
    return {"price_tier": key, "price_label": price_label(key)}


# Paid-placement referral attribution (see cloud/app/analytics_ops.py's
# admin_referral_report). Codes are hand-issued strings (partner names), never
# self-serve — a plain allowlist charset keeps a garbled/malicious cookie
# value from reaching YooKassa metadata or the payment_events row.
_REFERRAL_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def clean_referral_code(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    return value if _REFERRAL_CODE_RE.fullmatch(value) else None

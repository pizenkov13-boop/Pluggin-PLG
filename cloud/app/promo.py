"""Launch promo — the first ``PROMO_MAX_PAID`` buyers pay ``ru_promo`` (299 ₽).

The promo rides on the domestic price path: ``resolve_web_offer_tier`` offers
``ru_promo`` instead of ``ru`` while :func:`promo_paid_available` (plain price
display) or :func:`promo_tier_available` (about to freeze/commit a slot) is
true. Two independent bounds feed those:

* **``promo_paid``** — distinct users who have a successful ``payment.succeeded``
  recorded on the ``ru_promo`` tier (``payment_events.price_tier``). This is the
  honest ``X / cap`` shown on the site. It matches on the *frozen tier*, not a
  ruble amount — a smoke test at a fake price, or a later price tweak, can't
  move the count. Refunds do not decrement it (a slot, once used, stays used).
* **``promo_taken``** — checkout links already *frozen* to ``ru_promo`` that are
  either consumed or still within their TTL. ``price_locked`` makes a link's
  tier a write-once fact, so "≤ N links carry ``ru_promo``" ⇒ "≤ N promo sales".
  This is the hard ceiling on overshoot from links frozen at 299 during a
  traffic spike that haven't been paid yet; expired-unpaid links drop out of
  the count on their own, so the budget self-heals with no sweep job.

Every lookup here is a live query — no cache. A cached count would only widen
the overshoot it exists to bound, and the queries run once per checkout view,
not per request.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from supabase import Client

from cloud.app.config import (
    PROMO_ENABLED,
    PROMO_LINK_BUDGET,
    PROMO_MAX_PAID,
)
from cloud.app.pricing import normalize_tier, price_label

logger = logging.getLogger("plg.billing.promo")

PROMO_TIER = "ru_promo"


def promo_paid_count(client: Client) -> int:
    """Distinct users with a successful payment recorded on the promo tier.

    Matches on ``payment_events.price_tier`` (the frozen offer key), never on a
    ruble amount — so a test at a fake price or a later price change can't
    retroactively move ``X / cap``. Deliberately provider-agnostic (no
    ``.eq("provider", ...)`` filter): ``ru_promo`` is domestic-only by
    construction (see pricing.py), and since 2026-09-29 Prodamus is the
    preferred provider for it too, not just YooKassa — a provider filter here
    would have silently stopped counting Prodamus-routed promo sales, letting
    the launch promo overrun its cap. Every provider normalizes its "the
    payment succeeded" event to ``event_type="payment.succeeded"`` in
    payment_events (see prodamus_provider.handle_webhook) specifically so this
    filter stays provider-agnostic.
    """
    res = (
        client.table("payment_events")
        .select("user_id")
        .eq("event_type", "payment.succeeded")
        .eq("price_tier", PROMO_TIER)
        .limit(1000)
        .execute()
    )
    rows = res.data or []
    return len({r.get("user_id") for r in rows if r.get("user_id")})


def _count(query) -> int:
    return int(getattr(query.limit(1).execute(), "count", 0) or 0)


def promo_taken_count(client: Client) -> int:
    """Checkout links frozen to ``ru_promo`` that still hold a slot — consumed,
    or unconsumed but not yet expired. Expired-unpaid links free their slot.

    Two plain queries rather than one ``or=(...)``: a ``+00:00`` offset in an
    or-filter value is fragile to round-trip through the query string, and a
    failed count here would silently disable the promo.
    """
    now_z = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    base = (
        client.table("billing_checkout_links")
        .select("token_hash", count="exact")
        .eq("price_tier", PROMO_TIER)
        .eq("price_locked", True)
    )
    consumed = _count(base.not_.is_("consumed_at", "null"))
    live_unconsumed = _count(
        client.table("billing_checkout_links")
        .select("token_hash", count="exact")
        .eq("price_tier", PROMO_TIER)
        .eq("price_locked", True)
        .is_("consumed_at", "null")
        .gt("expires_at", now_z)
    )
    return consumed + live_unconsumed


def promo_paid_available(client: Client) -> bool:
    """True while the promo should be *shown* to a visitor, based purely on
    confirmed sales — ignores the checkout-link reservation buffer.

    Use this for anything that only displays a price (the public price
    endpoint, the account billing screen) and never commits to a slot. The
    link budget in :func:`promo_tier_available` exists to bound a different,
    narrower risk — overshoot from checkout links already frozen at 299 that
    haven't been paid yet — and must stay scoped to the path that actually
    freezes a link's price (``lock_checkout_link_price``). Otherwise a burst
    of started-but-abandoned checkouts fills that buffer for up to
    ``BILLING_CHECKOUT_LINK_TTL_SECONDS`` and flips the price shown to every
    other visitor, even though real paid sales are nowhere near the cap.
    """
    if not PROMO_ENABLED:
        return False
    return promo_paid_count(client) < PROMO_MAX_PAID


def promo_tier_available(client: Client) -> bool:
    """True while a *new checkout link* may still be frozen to the promo
    price. Both bounds must hold: fewer than ``PROMO_MAX_PAID`` paid users AND
    fewer than ``PROMO_LINK_BUDGET`` links already frozen at 299.

    Scoped to link creation/freezing (``lock_checkout_link_price``) and direct
    checkout (``clamp_promo_tier``) — anything that is about to commit a slot.
    For plain price display use :func:`promo_paid_available` instead.
    """
    if not promo_paid_available(client):
        return False
    if promo_taken_count(client) >= PROMO_LINK_BUDGET:
        return False
    return True


def clamp_promo_tier(tier: str | None, client: Client) -> str:
    """Downgrade a client-supplied / un-frozen ``ru_promo`` to ``ru`` once the
    promo is no longer available. A tier frozen onto a checkout link bypasses
    this — an already-issued 299 link keeps its price until it expires."""
    if normalize_tier(tier) != PROMO_TIER:
        return normalize_tier(tier)
    try:
        return PROMO_TIER if promo_tier_available(client) else "ru"
    except Exception:  # noqa: BLE001 — never let a promo count break checkout
        logger.warning("promo clamp lookup failed; using ru", exc_info=True)
        return "ru"


def promo_status(client: Client) -> dict[str, object]:
    """Public promo state for the site — the strikethrough price and ``X / cap``
    counter. ``active`` follows the paid count only: the UI keeps showing the
    promo until 100 are actually sold, even if the link budget is briefly full.
    """
    try:
        paid = promo_paid_count(client)
    except Exception:  # noqa: BLE001
        logger.warning("promo_status count failed", exc_info=True)
        return {"active": False, "paid": 0, "cap": PROMO_MAX_PAID}
    return {
        "active": bool(PROMO_ENABLED) and paid < PROMO_MAX_PAID,
        "paid": min(paid, PROMO_MAX_PAID),
        "cap": PROMO_MAX_PAID,
        "price_label": price_label(PROMO_TIER),
        "regular_price_label": price_label("ru"),
    }

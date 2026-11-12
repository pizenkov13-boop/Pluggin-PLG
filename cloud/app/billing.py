"""Subscription billing — activation, grace, idempotency, geo prices."""

from __future__ import annotations

from contextlib import contextmanager

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from supabase import Client

from cloud.app.config import (
    BEAT_LIMIT,
    GRACE_DAYS,
    PERIOD_DAYS,
    TRIAL_BEATS,
)
# price_label is re-exported here so existing importers
# (`from cloud.app.billing import price_label`) keep working.
from cloud.app.pricing import is_domestic, normalize_tier, price_label, price_provider

logger = logging.getLogger("plg.billing")

ACTIVE_STATUSES = frozenset({"trial", "active", "grace"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def resolve_price_tier(explicit: str | None, locale_header: str | None = None) -> str:
    """Resolve a price-book key. Price follows an explicit choice (user
    selection / stored link), never the UI language. ``locale_header`` is kept
    in the signature for call-site compatibility and is ignored."""
    return normalize_tier(explicit)


# Query-string override used only for support ("?region=ru" on the checkout or
# landing URL). Limited to the keys we actually serve so it can't smuggle in a
# reserved-but-dead tier.
_REGION_OVERRIDES = frozenset({"ru", "eu", "other", "by"})


def resolve_web_offer_tier(
    country: str | None,
    *,
    requested_region: str | None = None,
    client: Client | None = None,
    enforce_link_budget: bool = False,
) -> str:
    """The price-book key a web/desktop visitor should be *offered*, from their
    IP country. This is the single geo-pricing decision — checkout, the account
    screen and the public landing price all call it.

    While the ``intl_billing`` flag is off it always returns ``"ru"`` (499 ₽ for
    everyone, today's behaviour). With the flag on it honours an explicit
    ``?region=`` override, otherwise maps the country: ``RU`` -> ``ru``,
    EU/EEA -> ``eu``, everything else -> ``other``. An unknown country (private
    IP, DB file missing, unlisted range) falls back to ``"ru"`` — the safe
    domestic direction.

    ``enforce_link_budget`` is forwarded to :func:`_domestic_offer_tier` — pass
    it only when the result is about to be frozen onto a checkout link, never
    for a plain price lookup (see there for why).
    """
    from cloud.app.feature_flags import flag_enabled
    from cloud.app.pricing import key_for_country

    if client is None or not flag_enabled(client, "intl_billing", default=False):
        return _domestic_offer_tier(client, enforce_link_budget=enforce_link_budget)
    if requested_region:
        override = requested_region.strip().lower()
        if override in _REGION_OVERRIDES:
            return _domestic_offer_tier(client, enforce_link_budget=enforce_link_budget) if override == "ru" else override
    if not country:
        return _domestic_offer_tier(client, enforce_link_budget=enforce_link_budget)
    tier = key_for_country(country)
    return _domestic_offer_tier(client, enforce_link_budget=enforce_link_budget) if tier == "ru" else tier


def _domestic_offer_tier(client: Client | None, *, enforce_link_budget: bool = False) -> str:
    """The domestic tier to offer — ``ru_promo`` (299 ₽) while the launch promo
    still has budget, otherwise ``ru`` (499 ₽). Any lookup failure falls back to
    ``ru``: the safe direction, and it never blocks checkout.

    ``enforce_link_budget=False`` (the default) checks only confirmed paid
    sales (``promo_paid_available``) — the right choice for anything that just
    *displays* a price. Pass ``True`` only where the result is about to be
    committed by freezing a checkout link's price
    (``checkout_links.lock_checkout_link_price``): that path additionally
    respects the checkout-link reservation buffer (``promo_tier_available``),
    which exists to bound overshoot from links frozen at 299 that haven't been
    paid yet. Mixing the two up is what let a burst of abandoned checkouts
    flip the publicly displayed price to 499 while real paid sales were still
    well under the 100 cap.
    """
    if client is None:
        return "ru"
    try:
        from cloud.app.promo import PROMO_TIER, promo_paid_available, promo_tier_available

        available = promo_tier_available(client) if enforce_link_budget else promo_paid_available(client)
        return PROMO_TIER if available else "ru"
    except Exception:  # noqa: BLE001 — a promo count must never break pricing
        logger.warning("promo tier lookup failed; offering ru", exc_info=True)
        return "ru"


def trial_remaining(row: dict[str, Any]) -> int:
    used = int(row.get("trial_beats_used") or 0)
    return max(0, TRIAL_BEATS - used)


def apply_grace_expiry(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "expired")
    subscription_ends = _parse_ts(row.get("subscription_ends_at"))
    if status == "active" and subscription_ends and _utc_now() >= subscription_ends:
        row["status"] = "expired"
        status = "expired"
    if status != "grace":
        return row
    grace_until = _parse_ts(row.get("grace_until"))
    if grace_until and _utc_now() > grace_until:
        row["status"] = "expired"
        row["grace_until"] = None
    return row


def billing_snapshot(row: dict[str, Any], *, offer_tier: str | None = None) -> dict[str, Any]:
    row = apply_grace_expiry(dict(row))
    status = str(row.get("status") or "expired")
    stored_tier = normalize_tier(row.get("price_tier"))
    ends_at = _parse_ts(row.get("subscription_ends_at"))
    grace_until = _parse_ts(row.get("grace_until"))

    grace_days_left = 0
    if status == "grace" and grace_until:
        grace_days_left = max(0, (grace_until.date() - _utc_now().date()).days)

    days_until_renewal = 0
    if ends_at and status in ("active", "grace"):
        days_until_renewal = max(0, (ends_at.date() - _utc_now().date()).days)

    # An active/grace subscriber who has burned all 30 monthly beats can't
    # generate until the period rolls over, so the client must show the same
    # paywall as an expired account: any payment starts a fresh full period
    # (30 beats, ends_at = now + 30 days — see activate_subscription). (Caller
    # rolls the period, so beats_used is current.)
    monthly_remaining = max(0, BEAT_LIMIT - int(row.get("beats_used") or 0))
    quota_spent = status in ("active", "grace") and monthly_remaining <= 0

    # The displayed price is a *prospective* purchase price, so for someone who
    # isn't paying yet it follows their region (offer_tier, from the request's
    # geo). An active subscriber — including one who has spent the pack — keeps
    # the tier they actually subscribed on, in its own currency/provider.
    tier = stored_tier
    if offer_tier and status not in ("active", "grace"):
        tier = normalize_tier(offer_tier)

    # On the launch promo the client shows the regular price struck through next
    # to the 299 ₽ one. Present only while `ru_promo` is the offer; once the
    # promo closes the tier is plain `ru` and this key is gone -> just "499 ₽".
    regular_price_label = price_label("ru") if tier == "ru_promo" else None

    return {
        "status": status,
        "price_tier": tier,
        "price_label": price_label(tier),
        "regular_price_label": regular_price_label,
        "trial_beats": TRIAL_BEATS,
        "trial_remaining": trial_remaining(row) if status == "trial" else 0,
        "grace_days_left": grace_days_left,
        "subscription_ends_at": ends_at.isoformat() if ends_at else None,
        "days_until_renewal": days_until_renewal,
        "billing_provider": row.get("billing_provider"),
        "monthly_remaining": monthly_remaining,
        "can_subscribe": status in ("trial", "expired", "cancelled", "grace"),
        "needs_payment": status in ("expired", "cancelled")
        or (status == "trial" and trial_remaining(row) <= 0)
        or quota_spent,
        # Why this account can't generate right now — so the client can show
        # honest copy instead of "subscription ended" for someone who never
        # subscribed. Only meaningful when needs_payment is true.
        "lapsed_reason": _lapsed_reason(row, status),
    }


def _lapsed_reason(row: dict[str, Any], status: str) -> str | None:
    """Classify a non-active account: churned payer vs. spent free beat vs.
    free beat blocked because this device already claimed one."""
    if status in ("active", "grace"):
        return None
    if status == "trial":
        return "trial" if trial_remaining(row) > 0 else "trial_spent"
    # expired / cancelled
    ever_paid = bool(row.get("subscription_ends_at") or row.get("billing_provider"))
    if ever_paid:
        return "subscription_ended"
    ban_reason = str(row.get("ban_reason") or "")
    if "device" in ban_reason.lower():
        return "device_trial_used"
    if int(row.get("trial_beats_used") or 0) >= TRIAL_BEATS:
        return "trial_spent"
    # Never subscribed, trial not spent, no device block recorded — an account
    # that predates the trial grant, or one demoted by an admin. Treat as a
    # spent trial for copy purposes (the paywall is the same).
    return "trial_spent"


def claim_idempotency(
    client: Client,
    *,
    provider: str,
    idempotency_key: str,
    event_type: str,
    user_id: str | None = None,
    amount_cents: int | None = None,
    currency: str | None = None,
    external_id: str | None = None,
    price_tier: str | None = None,
    referral_code: str | None = None,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Return True if this event was already processed (duplicate webhook).

    ``price_tier`` records the price-book key the payment was created on
    (``ru`` / ``ru_promo`` / …). It's the authoritative "which offer did this
    buyer take" signal — the promo counter matches on it, not on a ruble
    amount that a test or a price tweak could move.
    """
    existing = (
        client.table("payment_events")
        .select("id")
        .eq("provider", provider)
        .eq("idempotency_key", idempotency_key)
        .maybe_single()
        .execute()
    )
    # Some postgrest-py versions return ``None`` for an empty maybe_single()
    # response. Treat that as "not seen yet"; duplicates are still caught
    # by the unique provider/idempotency_key constraint on the insert.
    if existing is not None and existing.data:
        logger.info("duplicate payment event provider=%s key=%s", provider, idempotency_key)
        return True

    client.table("payment_events").insert(
        {
            "provider": provider,
            "idempotency_key": idempotency_key,
            "event_type": event_type,
            "user_id": user_id,
            "amount_cents": amount_cents,
            "currency": currency,
            "external_id": external_id,
            "price_tier": price_tier,
            "referral_code": referral_code,
            "payload": payload or {},
        }
    ).execute()
    return False


def release_idempotency(client: Client, *, provider: str, idempotency_key: str) -> None:
    """Forget a claimed event so the provider's retry is processed again."""
    client.table("payment_events").delete().eq("provider", provider).eq(
        "idempotency_key", idempotency_key
    ).execute()


@contextmanager
def release_claim_on_failure(client: Client, *, provider: str, idempotency_key: str):
    """Undo ``claim_idempotency`` if handling the event then fails.

    PostgREST has no multi-statement transaction, so the claim row and the
    subscription update cannot commit together. Without this, a failure after
    the claim (DB blip in ``activate_subscription``) left the event marked
    processed: the provider's retry came back as "duplicate" and a paid user
    was never activated. The claim still goes first, so two concurrent
    deliveries of one event cannot both run.
    """
    try:
        yield
    except BaseException:
        try:
            release_idempotency(client, provider=provider, idempotency_key=idempotency_key)
        except Exception:  # noqa: BLE001 — never mask the original failure
            logger.exception(
                "could not release idempotency claim provider=%s key=%s", provider, idempotency_key
            )
        raise


def activate_subscription(
    client: Client,
    user_id: str,
    *,
    provider: str,
    external_id: str,
    price_tier: str,
    period_days: int = PERIOD_DAYS,
) -> dict[str, Any]:
    now = _utc_now()
    ends = now + timedelta(days=period_days)

    # Every payment — first, renewal, or a re-buy while still active — starts a
    # fresh full period: 30 monthly beats (beats_used=0) and ends_at = now + 30
    # days. Legacy daily fields are non-blocking activity statistics and are reset
    # the same way every time.

    # The payment_events row this activation is FOR — cached on the profile so
    # /v1/generate can stamp generation_logs.payment_event_id from the already-
    # loaded profile row (no extra query per generation). Ties every future
    # generation to this specific purchase, not just "the user's account", so
    # a refund lookup can tell paid-generations-under-purchase-A apart from
    # paid-generations-under-purchase-B after a repeat purchase. None if the
    # payment_events row hasn't landed yet (webhook race) — a generation would
    # then stamp NULL, which refund lookups must treat as unproven, not as 0.
    payment_row = (
        client.table("payment_events")
        .select("id")
        .eq("provider", provider)
        .eq("external_id", external_id)
        .eq("event_type", "payment.succeeded")
        .order("processed_at", desc=True)
        .limit(1)
        .maybe_single()
        .execute()
    )
    current_payment_event_id = (payment_row.data or {}).get("id")

    patch = {
        "status": "active",
        "billing_provider": provider,
        "price_tier": price_tier,
        "external_subscription_id": external_id,
        "subscription_ends_at": ends.isoformat(),
        "grace_until": None,
        "period_start": now.isoformat(),
        "beats_used": 0,
        "current_payment_event_id": current_payment_event_id,
        "updated_at": now.isoformat(),
    }
    patch["beats_today"] = 0
    patch["daily_reset"] = now.date().isoformat()
    client.table("profiles").update(patch).eq("id", user_id).execute()
    logger.info(
        "subscription activated user=%s provider=%s tier=%s",
        user_id,
        provider,
        price_tier,
    )
    result = client.table("profiles").select("*").eq("id", user_id).single().execute()
    return result.data or patch


def enter_grace_period(client: Client, user_id: str) -> None:
    now = _utc_now()
    grace_until = now + timedelta(days=GRACE_DAYS)
    client.table("profiles").update(
        {
            "status": "grace",
            "grace_until": grace_until.isoformat(),
            "updated_at": now.isoformat(),
        }
    ).eq("id", user_id).execute()
    logger.info("grace period user=%s until=%s", user_id, grace_until.isoformat())


def expire_subscription(client: Client, user_id: str) -> None:
    now = _utc_now()
    client.table("profiles").update(
        {
            "status": "expired",
            "grace_until": None,
            "updated_at": now.isoformat(),
        }
    ).eq("id", user_id).execute()
    logger.info("subscription expired user=%s", user_id)


def cancel_subscription(client: Client, user_id: str) -> None:
    now = _utc_now()
    client.table("profiles").update(
        {
            "status": "cancelled",
            "updated_at": now.isoformat(),
        }
    ).eq("id", user_id).execute()
    logger.info("subscription cancelled user=%s", user_id)


def pick_checkout_provider(price_tier: str, *, client: Client | None = None) -> str:
    from cloud.app.config import (
        PADDLE_API_KEY,
        PRODAMUS_FORM_URL,
        PRODAMUS_SECRET_KEY,
        STRIPE_PRICE_ID_INTL,
        STRIPE_SECRET_KEY,
        YOOKASSA_SECRET_KEY,
        YOOKASSA_SHOP_ID,
    )

    if is_domestic(price_tier):
        # Prodamus preferred since 2026-09-29 (verified against a real ru
        # payment — the project issue log (not included) §0ai); YooKassa is the fallback when
        # Prodamus isn't configured, kept live for that reason, not removed.
        if PRODAMUS_FORM_URL and PRODAMUS_SECRET_KEY:
            return "prodamus"
        if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
            return "yookassa"
        raise HTTPException(503, "No domestic payment provider configured.")

    from cloud.app.feature_flags import flag_enabled

    if client is None or not flag_enabled(client, "intl_billing", default=False):
        raise HTTPException(
            503,
            "International billing is not available yet. CIS subscribers: use ₽ pricing.",
        )

    want = price_provider(price_tier)
    if want == "prodamus" and PRODAMUS_FORM_URL and PRODAMUS_SECRET_KEY:
        return "prodamus"
    if want == "stripe" and STRIPE_SECRET_KEY and STRIPE_PRICE_ID_INTL:
        return "stripe"
    if want == "paddle" and PADDLE_API_KEY:
        return "paddle"
    # Fallbacks so an env configured with only Stripe/Paddle still resolves.
    if STRIPE_SECRET_KEY and STRIPE_PRICE_ID_INTL:
        return "stripe"
    if PADDLE_API_KEY:
        return "paddle"
    raise HTTPException(503, "International billing not configured yet (Prodamus/Stripe/Paddle).")

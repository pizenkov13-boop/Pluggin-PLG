"""Single source of truth for per-region price, currency and provider.

Live keys: ``ru``, ``ru_promo`` (RUB), ``eu`` (EUR), ``other`` (USD) and
``by`` (BYN) — all five rows are ``"provider": "prodamus"``, no per-country
FX maths, each row a fixed retail price. Prodamus confirmed it can bill
USD/EUR/BYN as foreign currency and nothing else. The remaining reserved
keys (``kz``/``us``/``ca``/``gb``) stay in the DB CHECK constraint
(migration 014) but are not served: Prodamus confirmed it cannot bill
KZT/GBP/CAD. See ``docs/PRODAMUS_INTL_BILLING_PLAN.md``.

**``ru``/``ru_promo`` provider field (2026-09-29):** this is the *preferred*
provider, not a routing guarantee — ``billing.pick_checkout_provider`` picks
Prodamus when configured and falls back to YooKassa otherwise (YooKassa is
never removed, just no longer the default). The actual provider a payment
went through is recorded separately, per-payment, on
``profiles.billing_provider`` / ``payment_events.provider`` — that's the
authoritative source for "who actually processed this one", not this field.

Price is chosen explicitly (user selection, or the tier stored on a checkout
link) — never from the UI language. ``_COUNTRY_TO_KEY`` only ever pre-selects a
choice in the web checkout; it is not authoritative.
"""

from __future__ import annotations

from cloud.app.config import (
    PRICE_BYN_CENTS,
    PRICE_CIS_RUB,
    PRICE_CIS_RUB_PROMO,
    PRICE_EUR_CENTS,
    PRICE_INTL_USD_CENTS,
)

# Legacy tier values still on disk (profiles.price_tier) and on unexpired
# checkout links. Every read path folds them to a current key.
_LEGACY_ALIASES: dict[str, str] = {"cis": "ru", "intl": "other"}

# Fallback when a tier is missing or unknown. Domestic is the safe direction:
# an unknown key can never route to an international provider that isn't built.
DEFAULT_TIER = "ru"

# Full key vocabulary accepted by the DB CHECK constraint (migrations 014 + 016).
# Only the keys present in ``_price_book()`` are actually served today.
KNOWN_TIERS: frozenset[str] = frozenset(
    {"ru", "ru_promo", "kz", "by", "us", "ca", "gb", "eu", "other"}
)

# ISO-3166-alpha2 (from CF-IPCountry) -> price-book key. Anything absent -> other.
# RU, BY and the eurozone/EEA get their own row; everyone else collapses to
# ``other`` (USD). This map only *pre-selects* an option in the web checkout —
# it is never the source of the price (see docs/PRODAMUS_INTL_BILLING_PLAN.md §4).
_EU_COUNTRIES = (
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK "
    "SI ES SE IS LI NO"
).split()
_COUNTRY_TO_KEY: dict[str, str] = {
    "RU": "ru",
    "BY": "by",
    **{c: "eu" for c in _EU_COUNTRIES},
}


def _price_book() -> dict[str, dict[str, object]]:
    """Built per call so tests that monkeypatch config amounts take effect."""
    usd = PRICE_INTL_USD_CENTS / 100
    eur = PRICE_EUR_CENTS / 100
    byn = PRICE_BYN_CENTS / 100
    return {
        "ru": {
            "currency": "RUB",
            "amount_minor": PRICE_CIS_RUB * 100,
            "provider": "prodamus",
            "label": f"{PRICE_CIS_RUB} ₽",
        },
        # Launch promo — same RUB rail as `ru`, lower price. Offered in
        # place of `ru` by resolve_web_offer_tier while the promo has budget
        # (cloud/app/promo.py). Kept a distinct key so the frozen checkout-link
        # price, the webhook amount check and profiles.price_tier all record
        # which price the buyer actually paid.
        "ru_promo": {
            "currency": "RUB",
            "amount_minor": PRICE_CIS_RUB_PROMO * 100,
            "provider": "prodamus",
            "label": f"{PRICE_CIS_RUB_PROMO} ₽",
        },
        "eu": {
            "currency": "EUR",
            "amount_minor": PRICE_EUR_CENTS,
            "provider": "prodamus",
            "label": f"€{eur:.2f}",
        },
        "other": {
            "currency": "USD",
            "amount_minor": PRICE_INTL_USD_CENTS,
            "provider": "prodamus",
            "label": f"${usd:.2f}",
        },
        "by": {
            "currency": "BYN",
            "amount_minor": PRICE_BYN_CENTS,
            "provider": "prodamus",
            "label": f"{byn:.2f} BYN",
        },
    }


def normalize_tier(tier: str | None) -> str:
    """Map any stored/legacy/blank tier to a currently-served price-book key."""
    key = (tier or "").strip().lower()
    key = _LEGACY_ALIASES.get(key, key)
    return key if key in _price_book() else DEFAULT_TIER


def price_entry(tier: str | None) -> dict[str, object]:
    return _price_book()[normalize_tier(tier)]


def price_label(tier: str | None) -> str:
    return str(price_entry(tier)["label"])


def price_provider(tier: str | None) -> str:
    return str(price_entry(tier)["provider"])


def is_domestic(tier: str | None) -> bool:
    return normalize_tier(tier) in ("ru", "ru_promo")


def key_for_country(country: str | None) -> str:
    """Pre-selection hint for the web checkout — not a source of truth."""
    return _COUNTRY_TO_KEY.get((country or "").strip().upper(), "other")

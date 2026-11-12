"""Tests for billing logic (trial, grace, geo prices)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from unittest.mock import MagicMock

import pytest

from cloud.app.billing import (
    apply_grace_expiry,
    billing_snapshot,
    pick_checkout_provider,
    price_label,
    resolve_price_tier,
    trial_remaining,
)
from cloud.app.pricing import key_for_country, normalize_tier
from cloud.app.quota import ensure_can_generate, quota_snapshot, roll_profile
from fastapi import HTTPException


def test_checkout_requires_payment_terms_acceptance():
    from cloud.app.legal import require_payment_terms_acceptance

    with pytest.raises(HTTPException) as exc:
        require_payment_terms_acceptance(False)
    assert exc.value.status_code == 400

    require_payment_terms_acceptance(True)


def _row(**kwargs) -> dict:
    now = datetime.now(timezone.utc)
    base = {
        "plan": "base",
        "status": "active",
        "period_start": now.isoformat(),
        "beats_used": 0,
        "beats_today": 0,
        "daily_reset": now.date().isoformat(),
        "trial_beats_used": 0,
        "price_tier": "ru",
    }
    base.update(kwargs)
    return base


def test_expired_subscription_closes_access_even_with_unused_beats():
    row = _row(
        subscription_ends_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )
    rolled = apply_grace_expiry(row)
    assert rolled["status"] == "expired"
    with pytest.raises(HTTPException) as exc:
        ensure_can_generate(rolled)
    assert exc.value.status_code == 402


def test_price_label_domestic():
    assert price_label("ru") == "499 ₽"
    assert price_label("cis") == "499 ₽"  # legacy alias


def test_price_label_other():
    assert price_label("other") == "$13.99"
    assert price_label("intl") == "$13.99"  # legacy alias


def test_price_label_eu():
    assert price_label("eu") == "€12.99"


def test_price_label_by():
    assert price_label("by") == "43.00 BYN"


def test_normalize_tier_aliases_and_fallback():
    assert normalize_tier("cis") == "ru"
    assert normalize_tier("intl") == "other"
    assert normalize_tier("ru") == "ru"
    assert normalize_tier("other") == "other"
    assert normalize_tier("eu") == "eu"
    assert normalize_tier("by") == "by"
    assert normalize_tier(None) == "ru"
    assert normalize_tier("") == "ru"
    assert normalize_tier("  CIS ") == "ru"
    # Reserved-but-unserved keys fold to the safe domestic default.
    assert normalize_tier("gb") == "ru"
    assert normalize_tier("garbage") == "ru"


def test_key_for_country():
    assert key_for_country("RU") == "ru"
    assert key_for_country("ru") == "ru"
    assert key_for_country("BY") == "by"
    assert key_for_country("DE") == "eu"
    assert key_for_country("FR") == "eu"
    assert key_for_country("US") == "other"
    assert key_for_country(None) == "other"


def test_resolve_price_tier_ignores_language():
    # Price follows an explicit choice, never the UI language.
    assert resolve_price_tier(None, "ru-RU") == "ru"
    assert resolve_price_tier(None, "en-US") == "ru"
    assert resolve_price_tier("other", "ru-RU") == "other"
    assert resolve_price_tier("cis", "en-US") == "ru"


# --- geo price routing (resolve_web_offer_tier) -------------------------------

def _flag(monkeypatch, on: bool):
    monkeypatch.setattr("cloud.app.feature_flags.flag_enabled", lambda *a, **k: on)


def test_web_offer_tier_flag_off_is_always_domestic(monkeypatch):
    from cloud.app.billing import resolve_web_offer_tier

    _flag(monkeypatch, False)
    for country in ("DE", "US", "RU", None, "XX"):
        assert resolve_web_offer_tier(country, client=object()) == "ru"
    # No client at all -> also domestic.
    assert resolve_web_offer_tier("DE") == "ru"


def test_web_offer_tier_flag_on_maps_country(monkeypatch):
    from cloud.app.billing import resolve_web_offer_tier

    _flag(monkeypatch, True)
    assert resolve_web_offer_tier("RU", client=object()) == "ru"
    assert resolve_web_offer_tier("BY", client=object()) == "by"
    assert resolve_web_offer_tier("DE", client=object()) == "eu"
    assert resolve_web_offer_tier("FR", client=object()) == "eu"
    assert resolve_web_offer_tier("NO", client=object()) == "eu"  # EEA
    assert resolve_web_offer_tier("US", client=object()) == "other"
    assert resolve_web_offer_tier("GB", client=object()) == "other"  # Prodamus has no GBP
    # Unknown country (private IP, DB gap) -> safe domestic fallback.
    assert resolve_web_offer_tier(None, client=object()) == "ru"


def test_web_offer_tier_region_override(monkeypatch):
    from cloud.app.billing import resolve_web_offer_tier

    _flag(monkeypatch, True)
    assert resolve_web_offer_tier("DE", requested_region="ru", client=object()) == "ru"
    assert resolve_web_offer_tier("RU", requested_region="eu", client=object()) == "eu"
    assert resolve_web_offer_tier("RU", requested_region="by", client=object()) == "by"
    assert resolve_web_offer_tier("RU", requested_region="ZZ", client=object()) == "ru"  # junk ignored
    # Override is dead while the flag is off.
    _flag(monkeypatch, False)
    assert resolve_web_offer_tier("RU", requested_region="eu", client=object()) == "ru"


def test_billing_snapshot_offer_tier_only_for_prospects():
    from cloud.app.billing import billing_snapshot

    # Prospect (expired) sees the geo price.
    snap = billing_snapshot(_row(status="expired"), offer_tier="eu")
    assert snap["price_tier"] == "eu"
    assert snap["price_label"] == "€12.99"

    # Active subscriber keeps the tier they actually bought on.
    snap = billing_snapshot(_row(status="active", price_tier="ru"), offer_tier="eu")
    assert snap["price_label"] == "499 ₽"

    # Active-but-depleted (quota spent) also keeps their tier.
    snap = billing_snapshot(_row(status="active", beats_used=30, price_tier="ru"), offer_tier="other")
    assert snap["needs_payment"] is True
    assert snap["price_label"] == "499 ₽"

    # No offer_tier -> unchanged behaviour.
    snap = billing_snapshot(_row(status="expired", price_tier="ru"))
    assert snap["price_label"] == "499 ₽"


def test_billing_snapshot_regular_price_label_only_on_promo():
    from cloud.app.billing import billing_snapshot

    # Prospect offered the launch promo -> struck-through regular price for the UI.
    snap = billing_snapshot(_row(status="expired"), offer_tier="ru_promo")
    assert snap["price_tier"] == "ru_promo"
    assert snap["price_label"] == "299 ₽"
    assert snap["regular_price_label"] == "499 ₽"

    # Plain ru (promo closed / not offered) -> no regular price key value.
    snap = billing_snapshot(_row(status="expired"), offer_tier="ru")
    assert snap["price_label"] == "499 ₽"
    assert snap["regular_price_label"] is None

    # Active subscriber who bought on the promo keeps ru_promo + the struck price.
    snap = billing_snapshot(_row(status="active", price_tier="ru_promo"))
    assert snap["regular_price_label"] == "499 ₽"


def test_lock_checkout_link_price_freezes_once(monkeypatch):
    from cloud.app import checkout_links

    _flag(monkeypatch, True)
    updates: list[dict] = []

    class _Chain:
        def update(self, data):
            updates.append(data)
            return self

        def eq(self, *a, **k):
            return self

        def execute(self):
            return MagicMock()

    class _Client:
        def table(self, _name):
            return _Chain()

    row = {"price_tier": "ru", "price_locked": False}
    first = checkout_links.lock_checkout_link_price(_Client(), "tok", row, country="DE")
    assert first == "eu"
    assert row["price_locked"] is True and row["price_tier"] == "eu"
    assert updates == [{"price_tier": "eu", "price_locked": True}]

    # Second view from a different country returns the frozen tier, no new write.
    again = checkout_links.lock_checkout_link_price(_Client(), "tok", row, country="US")
    assert again == "eu"
    assert len(updates) == 1


def test_billing_price_endpoint_is_geo_and_public(monkeypatch):
    import json as _json

    from cloud.app import main

    monkeypatch.setattr(main, "service_client", lambda: object())
    monkeypatch.setattr(main, "client_country", lambda _req: "DE")
    _flag(monkeypatch, True)

    resp = main.billing_price(object(), region=None)
    body = _json.loads(resp.body)
    assert body["price_tier"] == "eu"
    assert body["price_label"] == "€12.99"
    assert body["currency"] == "EUR"
    assert body["amount"] == 12.99
    assert resp.headers["cache-control"] == "no-store"

    # Flag off -> domestic for everyone, regardless of country.
    _flag(monkeypatch, False)
    body = _json.loads(main.billing_price(object(), region=None).body)
    assert body["price_label"] == "499 ₽"


def test_trial_remaining():
    from cloud.app.config import TRIAL_BEATS

    assert trial_remaining(_row(status="trial", trial_beats_used=0)) == TRIAL_BEATS
    assert trial_remaining(_row(status="trial", trial_beats_used=TRIAL_BEATS)) == 0


def test_grace_expires():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    row = _row(status="grace", grace_until=past.isoformat())
    out = apply_grace_expiry(row)
    assert out["status"] == "expired"


def test_trial_blocks_when_exhausted():
    from cloud.app.config import TRIAL_BEATS

    row = _row(status="trial", trial_beats_used=TRIAL_BEATS)
    with pytest.raises(HTTPException) as exc:
        ensure_can_generate(row)
    assert exc.value.status_code == 402


def test_billing_snapshot_trial():
    from cloud.app.config import TRIAL_BEATS

    snap = billing_snapshot(_row(status="trial", trial_beats_used=0))
    assert snap["trial_remaining"] == TRIAL_BEATS
    assert snap["can_subscribe"] is True


def test_quota_trial_uses_trial_limit():
    from cloud.app.config import TRIAL_BEATS

    snap = quota_snapshot(_row(status="trial", trial_beats_used=0))
    assert snap["limit"] == TRIAL_BEATS
    assert snap["remaining"] == TRIAL_BEATS


def test_intl_checkout_blocked_without_flag():
    client = MagicMock()
    for tier in ("intl", "other"):
        with pytest.raises(HTTPException) as exc:
            pick_checkout_provider(tier, client=client)
        assert exc.value.status_code == 503


def test_domestic_checkout_falls_back_to_yookassa_without_prodamus(monkeypatch):
    """ЮKassa is the fallback for ru/ru_promo when Prodamus isn't configured
    — not the default. See test_domestic_checkout_prefers_prodamus below for
    the 2026-09-29 default (Prodamus first when both are configured)."""
    monkeypatch.setattr("cloud.app.config.YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setattr("cloud.app.config.YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.config.PRODAMUS_FORM_URL", "")
    monkeypatch.setattr("cloud.app.config.PRODAMUS_SECRET_KEY", "")
    assert pick_checkout_provider("ru") == "yookassa"
    assert pick_checkout_provider("cis") == "yookassa"  # legacy alias


def test_domestic_checkout_prefers_prodamus_when_configured(monkeypatch):
    """As of 2026-09-29, pick_checkout_provider checks Prodamus before
    ЮKassa for domestic tiers too — Prodamus wins whenever both are
    configured, ЮKassa is now the fallback, not the default."""
    monkeypatch.setattr("cloud.app.config.YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setattr("cloud.app.config.YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.config.PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr("cloud.app.config.PRODAMUS_SECRET_KEY", "secret")
    assert pick_checkout_provider("ru") == "prodamus"
    assert pick_checkout_provider("ru_promo") == "prodamus"


# --- active/grace with the 30 monthly beats spent = same paywall as expired ----


def test_spent_quota_needs_payment_like_expired():
    # 3 beats/day for 10 days: all 30 monthly beats gone, subscription not expired.
    for status in ("active", "grace"):
        snap = billing_snapshot(_row(status=status, beats_used=30))
        assert snap["monthly_remaining"] == 0
        assert snap["needs_payment"] is True
        assert "can_topup" not in snap  # the separate top-up mechanic is gone


def test_active_with_generations_left_has_no_paywall():
    snap = billing_snapshot(_row(status="active", beats_used=12))
    assert snap["monthly_remaining"] == 18
    assert snap["needs_payment"] is False


# --- lapsed_reason: why a non-active account can't generate -------------------


def test_lapsed_reason_active_is_none():
    assert billing_snapshot(_row(status="active"))["lapsed_reason"] is None


def test_lapsed_reason_trial_with_free_beat():
    snap = billing_snapshot(_row(status="trial", trial_beats_used=0))
    assert snap["lapsed_reason"] == "trial"


def test_lapsed_reason_free_beat_spent():
    # Backend has already flipped status trial -> expired at this point.
    snap = billing_snapshot(_row(status="expired", trial_beats_used=1))
    assert snap["lapsed_reason"] == "trial_spent"
    assert snap["needs_payment"] is True


def test_lapsed_reason_device_trial_blocked():
    snap = billing_snapshot(
        _row(
            status="expired",
            trial_beats_used=0,
            ban_reason="Trial already used on this device.",
        )
    )
    assert snap["lapsed_reason"] == "device_trial_used"


def test_lapsed_reason_churned_subscriber():
    snap = billing_snapshot(
        _row(
            status="expired",
            billing_provider="yookassa",
            subscription_ends_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        )
    )
    assert snap["lapsed_reason"] == "subscription_ended"


def test_quota_snapshot_inactive_reports_zero_not_phantom_thirty():
    # A never-subscribed account that just spent its trial beat must not look
    # like a paid subscriber with 30 beats left.
    snap = quota_snapshot(_row(status="expired", beats_used=0, trial_beats_used=1))
    assert snap["limit"] == 0
    assert snap["remaining"] == 0
    assert snap["days_until_reset"] == 0


class _FakeClient:
    """Captures the profiles update patch and serves the current status.

    Also answers the payment_events lookup activate_subscription() does to
    stamp current_payment_event_id: pass payment_event_ids as a
    {external_id: payment_events.id} map so different purchases resolve to
    different ids (the "repeat purchase" tests below rely on this). An
    external_id with no entry resolves to None, simulating a not-yet-landed
    webhook row.
    """

    def __init__(self, current_status: str, payment_event_ids: dict[str, str] | None = None):
        self._status = current_status
        self._payment_event_ids = payment_event_ids or {}
        self.patch: dict | None = None
        self._table = None
        self._mode = "select"
        self._eq: dict[str, object] = {}

    def table(self, name):
        self._table = name
        self._eq = {}
        return self

    def select(self, *_a, **_k):
        self._mode = "select"
        return self

    def update(self, patch):
        self._mode = "update"
        self.patch = patch
        return self

    def eq(self, field, value):
        self._eq[field] = value
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def maybe_single(self):
        return self

    def single(self):
        return self

    def execute(self):
        if self._mode == "select":
            if self._table == "payment_events":
                external_id = self._eq.get("external_id")
                payment_id = self._payment_event_ids.get(external_id)
                return type("R", (), {"data": {"id": payment_id} if payment_id else None})()
            return type("R", (), {"data": {"status": self._status}})()
        return type("R", (), {"data": {**(self.patch or {})}})()


def test_repurchase_while_spent_starts_a_full_new_period():
    from cloud.app.billing import activate_subscription
    from cloud.app.config import BEAT_LIMIT, PERIOD_DAYS

    client = _FakeClient(current_status="active")  # pays again while still "active"
    before = datetime.now(timezone.utc)
    activate_subscription(
        client, "u1", provider="yookassa", external_id="pay_1", price_tier="ru"
    )
    after = datetime.now(timezone.utc)

    patch = client.patch
    assert patch["status"] == "active"
    assert patch["beats_used"] == 0                   # fresh 30 monthly beats
    assert patch["beats_today"] == 0                  # activity stat resets like a normal activation
    assert "daily_reset" in patch
    ends = datetime.fromisoformat(patch["subscription_ends_at"])
    assert before + timedelta(days=PERIOD_DAYS) <= ends <= after + timedelta(days=PERIOD_DAYS)

    # The account now reads 30/30 and is no longer sent to the paywall.
    paid = {**_row(status="active", beats_used=BEAT_LIMIT), **patch}
    assert billing_snapshot(paid)["needs_payment"] is False
    assert quota_snapshot(paid)["remaining"] == BEAT_LIMIT


def test_first_subscription_resets_daily_activity_stat():
    from cloud.app.billing import activate_subscription

    client = _FakeClient(current_status="trial")  # first paid subscription
    activate_subscription(
        client, "u2", provider="yookassa", external_id="pay_2", price_tier="ru"
    )
    assert client.patch["beats_used"] == 0
    assert client.patch["beats_today"] == 0


class _IdemClient:
    """Captures the payment_events insert; reports 'not seen yet' on lookup."""

    def __init__(self):
        self.inserted: dict | None = None
        self._mode = "select"

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        self._mode = "select"
        return self

    def insert(self, row):
        self._mode = "insert"
        self.inserted = row
        return self

    def eq(self, *_a, **_k):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        if self._mode == "select":
            return type("R", (), {"data": None})()
        return type("R", (), {"data": [self.inserted]})()


def test_claim_idempotency_records_price_tier():
    from cloud.app.billing import claim_idempotency

    client = _IdemClient()
    seen = claim_idempotency(
        client,
        provider="yookassa",
        idempotency_key="payment.succeeded:pay_9",
        event_type="payment.succeeded",
        user_id="u9",
        amount_cents=29900,
        currency="RUB",
        external_id="pay_9",
        price_tier="ru_promo",
        payload={"event": "payment.succeeded"},
    )
    assert seen is False
    assert client.inserted["price_tier"] == "ru_promo"
    assert client.inserted["amount_cents"] == 29900


def test_claim_idempotency_price_tier_defaults_to_none():
    from cloud.app.billing import claim_idempotency

    client = _IdemClient()
    claim_idempotency(
        client,
        provider="stripe",
        idempotency_key="k1",
        event_type="invoice.paid",
        user_id="u1",
    )
    assert client.inserted["price_tier"] is None

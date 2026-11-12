"""Payment payload / webhook checks for the Prodamus provider.

As of 2026-09-29, Prodamus is the preferred provider for every price-book
tier (``ru``/``ru_promo``/``eu``/``other``/``by``) whenever
``PRODAMUS_FORM_URL``/``PRODAMUS_SECRET_KEY`` are configured —
``pick_checkout_provider`` in billing.py checks Prodamus before ЮKassa for
the domestic tiers too. ЮKassa is the fallback when Prodamus isn't
configured, not the other way around. See docs/PRODAMUS_INTL_BILLING_PLAN.md
and the project issue log (not included) §0ai for the history of this decision."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


def _fake_user_email(monkeypatch, provider, email="buyer@example.com"):
    monkeypatch.setattr(provider, "_user_email", lambda _client, _user_id: email)


def test_checkout_serves_domestic_ru_tier_directly(monkeypatch):
    """Since 2026-09-29 `ru` is a normal Prodamus-served tier (provider field
    in pricing.py's price book) — no allow_any_tier bypass needed."""
    from cloud.app.providers import prodamus_provider

    captured: dict = {}

    def fake_get(url, params=None, timeout=None, follow_redirects=None):
        captured["params"] = dict(params)
        return SimpleNamespace(raise_for_status=lambda: None, text="https://pay.example.com/pay/ru1")

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_CIS_RUB", 499)
    monkeypatch.setattr(prodamus_provider.httpx, "get", fake_get)
    _fake_user_email(monkeypatch, prodamus_provider)

    result = prodamus_provider.create_checkout(SimpleNamespace(), "user-1", "ru")

    assert result["confirmation_url"] == "https://pay.example.com/pay/ru1"
    assert result["currency"] == "RUB"
    assert captured["params"]["currency"] == "rub"
    assert captured["params"]["products[0][price]"] == "499.00"


def test_checkout_rejects_a_tier_not_in_the_price_book(monkeypatch):
    """allow_any_tier=False still rejects a key outside the price book
    entirely (normalize_tier folds unknown/reserved keys to "ru", so this
    exercises the guard via a tier normalize_tier would NOT fold away)."""
    from cloud.app.providers import prodamus_provider

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr(
        prodamus_provider,
        "price_entry",
        lambda _tier: {"currency": "RUB", "amount_minor": 100, "provider": "stripe", "label": "x"},
    )

    with pytest.raises(HTTPException) as exc:
        prodamus_provider.create_checkout(SimpleNamespace(), "user-1", "ru")
    assert exc.value.status_code == 400


def test_checkout_allow_any_tier_bypasses_the_provider_guard(monkeypatch):
    """allow_any_tier=True (the admin test-checkout path) still works even
    for a tier the price book doesn't route to Prodamus."""
    from cloud.app.providers import prodamus_provider

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr(
        prodamus_provider,
        "price_entry",
        lambda _tier: {"currency": "RUB", "amount_minor": 49900, "provider": "stripe", "label": "x"},
    )
    monkeypatch.setattr(
        prodamus_provider.httpx,
        "get",
        lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, text="https://pay.example.com/pay/x"),
    )
    _fake_user_email(monkeypatch, prodamus_provider)

    result = prodamus_provider.create_checkout(
        SimpleNamespace(), "user-1", "ru", allow_any_tier=True
    )
    assert result["confirmation_url"] == "https://pay.example.com/pay/x"


def test_checkout_requires_configuration(monkeypatch):
    from cloud.app.providers import prodamus_provider

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "")

    with pytest.raises(HTTPException) as exc:
        prodamus_provider.create_checkout(SimpleNamespace(), "user-1", "eu")
    assert exc.value.status_code == 503


def test_checkout_requires_email(monkeypatch):
    from cloud.app.providers import prodamus_provider

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    _fake_user_email(monkeypatch, prodamus_provider, email="")

    with pytest.raises(HTTPException) as exc:
        prodamus_provider.create_checkout(SimpleNamespace(), "user-1", "eu")
    assert exc.value.status_code == 400


def test_checkout_builds_signed_payload_and_returns_link(monkeypatch):
    from cloud.app.providers import prodamus_provider

    captured: dict = {}

    def fake_get(url, params=None, timeout=None, follow_redirects=None):
        captured["url"] = url
        captured["params"] = dict(params)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            text="https://pay.example.com/pay/abc123",
        )

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_EUR_CENTS", 1299)
    monkeypatch.setattr(prodamus_provider.httpx, "get", fake_get)
    _fake_user_email(monkeypatch, prodamus_provider)

    result = prodamus_provider.create_checkout(SimpleNamespace(), "user-1", "eu", referral_code="ref-1")

    assert result["confirmation_url"] == "https://pay.example.com/pay/abc123"
    assert result["price_tier"] == "eu"
    assert result["currency"] == "EUR"
    params = captured["params"]
    assert params["currency"] == "eur"
    assert params["customer_email"] == "buyer@example.com"
    assert params["products[0][price]"] == "12.99"
    assert params["_param_user_id"] == "user-1"
    assert params["_param_referral_code"] == "ref-1"
    assert "callbackType" not in params
    assert "signature" in params


def test_checkout_rejects_non_url_response(monkeypatch):
    from cloud.app.providers import prodamus_provider

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr(
        prodamus_provider.httpx,
        "get",
        lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, text="error: bad shop"),
    )
    _fake_user_email(monkeypatch, prodamus_provider)

    with pytest.raises(HTTPException) as exc:
        prodamus_provider.create_checkout(SimpleNamespace(), "user-1", "eu")
    assert exc.value.status_code == 502


def test_sign_is_deterministic_and_key_order_independent():
    from cloud.app.providers import prodamus_provider

    a = prodamus_provider._sign({"b": "2", "a": "1"}, "secret")
    b = prodamus_provider._sign({"a": "1", "b": "2"}, "secret")
    assert a == b
    assert a == prodamus_provider._sign({"a": "1", "b": "2"}, "secret")


def _webhook_form(**overrides) -> dict[str, str]:
    # Field names match the real Prodamus notification body (order 49304825,
    # captured via Sentry 2026-09-29) — notably `sum`, not `currency_sum`,
    # which isn't a real field at all. See module docstring.
    base = {
        "order_id": "order-1",
        "payment_status": "success",
        "currency": "eur",
        "sum": "12.99",
        "_param_user_id": "user-1",
        "_param_price_tier": "eu",
    }
    base.update(overrides)
    return base


def _signed_headers(form: dict[str, str], secret: str) -> dict[str, str]:
    from cloud.app.providers import prodamus_provider

    sig = prodamus_provider._sign(dict(form), secret)
    return {"Sign": sig}


def test_webhook_rejects_missing_signature(monkeypatch):
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")

    class FakeRequest:
        headers: dict[str, str] = {}

        async def form(self):
            from starlette.datastructures import FormData

            return FormData(_webhook_form().items())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(prodamus_provider.handle_webhook(SimpleNamespace(), FakeRequest()))
    assert exc.value.status_code == 400


def test_webhook_rejects_invalid_signature(monkeypatch):
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")

    class FakeRequest:
        headers = {"Sign": "not-the-right-signature"}

        async def form(self):
            from starlette.datastructures import FormData

            return FormData(_webhook_form().items())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(prodamus_provider.handle_webhook(SimpleNamespace(), FakeRequest()))
    assert exc.value.status_code == 400


def _make_request(form: dict[str, str], secret: str):
    from starlette.datastructures import FormData

    headers = _signed_headers(form, secret)

    class FakeRequest:
        def __init__(self):
            self.headers = headers

        async def form(self):
            return FormData(form.items())

    return FakeRequest()


def _make_json_request(body: dict[str, object], secret: str):
    """handle_webhook still parses a JSON body if Content-Type says so, even
    though create_checkout no longer requests callbackType=json (reverted
    2026-09-29 — see module docstring). Same Sign header/HMAC either way."""
    from cloud.app.providers import prodamus_provider

    sig = prodamus_provider._sign(dict(body), secret)
    headers = {"Sign": sig, "content-type": "application/json"}

    class FakeRequest:
        def __init__(self):
            self.headers = headers

        async def json(self):
            return body

    return FakeRequest()


def test_webhook_activates_on_success(monkeypatch):
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_EUR_CENTS", 1299)

    activated: list[dict] = []
    monkeypatch.setattr(prodamus_provider, "claim_idempotency", lambda *a, **k: False)
    monkeypatch.setattr(
        prodamus_provider,
        "activate_subscription",
        lambda _client, user_id, **kw: activated.append({"user_id": user_id, **kw}),
    )

    request = _make_request(_webhook_form(), "secret")
    result = asyncio.run(prodamus_provider.handle_webhook(SimpleNamespace(), request))

    assert result == {"ok": "activated"}
    assert activated and activated[0] == {
        "user_id": "user-1",
        "provider": "prodamus",
        "external_id": "order-1",
        "price_tier": "eu",
    }


def test_webhook_activates_ru_promo_matching_real_prodamus_payload(monkeypatch):
    """Regression for the 2026-09-29 incident: real order 49304825 (299 RUB,
    ru_promo, via SBP) 400'd because handle_webhook read a `currency_sum`
    field that Prodamus's real notification doesn't send. This mirrors the
    actual field set Sentry captured for that order (minus PII) — no
    `currency_sum` anywhere, `sum` is the amount."""
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_CIS_RUB_PROMO", 299)

    activated: list[dict] = []
    monkeypatch.setattr(prodamus_provider, "claim_idempotency", lambda *a, **k: False)
    monkeypatch.setattr(
        prodamus_provider,
        "activate_subscription",
        lambda _client, user_id, **kw: activated.append({"user_id": user_id, **kw}),
    )

    real_shape_form = {
        "date": "2026-09-29 12:00:00",
        "order_id": "49304825",
        "order_num": "",
        "domain": "pay.example.com",
        "sum": "299.00",
        "currency": "rub",
        "customer_email": "buyer@example.com",
        "customer_phone": "",
        "customer_extra": "",
        "payment_type": "СБП",
        "commission": "3.5",
        "commission_sum": "10.47",
        "attempt": "1",
        "payment_status": "success",
        "payment_status_description": "Успешная оплата",
        "payment_init": "1",
        # products[0][...] (bracket-notation) fields are omitted here — the
        # existing _make_request/_signed_headers test helpers sign the flat
        # dict directly, not the _nest_form() reconstruction handle_webhook
        # verifies against, so bracket keys would need real nesting to sign
        # correctly. Irrelevant to this regression (it's about `sum`, not
        # products[]); see the JSON-body tests for nested-payload coverage.
        "sys": "pluggin",
        "_param_user_id": "aea89418-1818-46e6-9f25-7afe0c9091d1",
        "_param_price_tier": "ru_promo",
        "_param_referral_code": "",
    }
    request = _make_request(real_shape_form, "secret")
    result = asyncio.run(prodamus_provider.handle_webhook(SimpleNamespace(), request))

    assert result == {"ok": "activated"}
    assert activated and activated[0]["price_tier"] == "ru_promo"
    assert activated[0]["external_id"] == "49304825"


def test_webhook_success_records_canonical_event_type(monkeypatch):
    """payment_events.event_type must be "payment.succeeded" for a Prodamus
    success, matching yookassa_provider's literal — promo.py, billing.py
    (current_payment_event_id) and analytics_ops.py all filter on it,
    provider-agnostically. Storing Prodamus's raw "success" instead would
    silently drop Prodamus payments from revenue/referral/promo counting."""
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_EUR_CENTS", 1299)

    claimed: list[dict] = []
    monkeypatch.setattr(
        prodamus_provider,
        "claim_idempotency",
        lambda _client, **kw: (claimed.append(kw) or False),
    )
    monkeypatch.setattr(prodamus_provider, "activate_subscription", lambda *a, **k: None)

    request = _make_request(_webhook_form(), "secret")
    asyncio.run(prodamus_provider.handle_webhook(SimpleNamespace(), request))

    assert claimed and claimed[0]["event_type"] == "payment.succeeded"


def test_webhook_non_success_keeps_raw_payment_status_as_event_type(monkeypatch):
    """Non-success statuses aren't normalized — nothing downstream filters on
    a specific Prodamus failure/cancel/refund literal (see module docstring:
    exact vocabulary unconfirmed, plan §8.5), only on the success one."""
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")

    claimed: list[dict] = []
    monkeypatch.setattr(
        prodamus_provider,
        "claim_idempotency",
        lambda _client, **kw: (claimed.append(kw) or False),
    )
    monkeypatch.setattr(prodamus_provider, "enter_grace_period", lambda *a, **k: None)

    fake_client = SimpleNamespace(
        table=lambda _name: SimpleNamespace(
            select=lambda *_a: SimpleNamespace(
                eq=lambda *_a: SimpleNamespace(
                    maybe_single=lambda: SimpleNamespace(
                        execute=lambda: SimpleNamespace(data={"status": "expired"})
                    )
                )
            )
        )
    )

    request = _make_request(_webhook_form(payment_status="failed"), "secret")
    asyncio.run(prodamus_provider.handle_webhook(fake_client, request))

    assert claimed and claimed[0]["event_type"] == "failed"


def test_webhook_activates_on_success_json_body(monkeypatch):
    """Defensive coverage: handle_webhook still accepts a JSON body if one
    arrives, even though create_checkout no longer asks for it (reverted
    2026-09-29 — real Prodamus JSON field names were never confirmed)."""
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_EUR_CENTS", 1299)

    activated: list[dict] = []
    monkeypatch.setattr(prodamus_provider, "claim_idempotency", lambda *a, **k: False)
    monkeypatch.setattr(
        prodamus_provider,
        "activate_subscription",
        lambda _client, user_id, **kw: activated.append({"user_id": user_id, **kw}),
    )

    request = _make_json_request(_webhook_form(), "secret")
    result = asyncio.run(prodamus_provider.handle_webhook(SimpleNamespace(), request))

    assert result == {"ok": "activated"}
    assert activated and activated[0]["external_id"] == "order-1"


def test_webhook_json_body_rejects_invalid_signature(monkeypatch):
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")

    class FakeRequest:
        headers = {"Sign": "wrong", "content-type": "application/json"}

        async def json(self):
            return _webhook_form()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(prodamus_provider.handle_webhook(SimpleNamespace(), FakeRequest()))
    assert exc.value.status_code == 400


def test_webhook_json_body_rejects_malformed_body(monkeypatch):
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")

    class FakeRequest:
        headers = {"Sign": "whatever", "content-type": "application/json"}

        async def json(self):
            raise ValueError("not json")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(prodamus_provider.handle_webhook(SimpleNamespace(), FakeRequest()))
    assert exc.value.status_code == 400


def test_webhook_rejects_wrong_amount(monkeypatch):
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_EUR_CENTS", 1299)

    claimed: list[bool] = []
    monkeypatch.setattr(prodamus_provider, "claim_idempotency", lambda *a, **k: claimed.append(True))

    request = _make_request(_webhook_form(sum="1.00"), "secret")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(prodamus_provider.handle_webhook(SimpleNamespace(), request))
    assert exc.value.status_code == 400
    assert claimed == []


def test_webhook_duplicate_does_not_activate_twice(monkeypatch):
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_EUR_CENTS", 1299)

    activated: list[str] = []
    monkeypatch.setattr(prodamus_provider, "claim_idempotency", lambda *a, **k: True)
    monkeypatch.setattr(
        prodamus_provider, "activate_subscription", lambda *a, **k: activated.append("called")
    )

    request = _make_request(_webhook_form(), "secret")
    result = asyncio.run(prodamus_provider.handle_webhook(SimpleNamespace(), request))

    assert result == {"ok": "duplicate"}
    assert activated == []


def test_webhook_non_success_enters_grace_for_active_subscriber(monkeypatch):
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")

    graced: list[str] = []
    monkeypatch.setattr(prodamus_provider, "claim_idempotency", lambda *a, **k: False)
    monkeypatch.setattr(prodamus_provider, "enter_grace_period", lambda _client, user_id: graced.append(user_id))

    fake_client = SimpleNamespace(
        table=lambda _name: SimpleNamespace(
            select=lambda *_a: SimpleNamespace(
                eq=lambda *_a: SimpleNamespace(
                    maybe_single=lambda: SimpleNamespace(
                        execute=lambda: SimpleNamespace(data={"status": "active"})
                    )
                )
            )
        )
    )

    request = _make_request(_webhook_form(payment_status="failed"), "secret")
    result = asyncio.run(prodamus_provider.handle_webhook(fake_client, request))

    assert result == {"ok": "grace"}
    assert graced == ["user-1"]


def test_webhook_missing_order_id_rejected(monkeypatch):
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")

    request = _make_request(_webhook_form(order_id=""), "secret")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(prodamus_provider.handle_webhook(SimpleNamespace(), request))
    assert exc.value.status_code == 400


def test_prodamus_webhook_route_dispatches(monkeypatch):
    from cloud.app import main

    called: list[bool] = []

    async def fake_handle(_client, _request):
        called.append(True)
        return {"ok": "activated"}

    monkeypatch.setattr(main, "service_client", lambda: object())
    monkeypatch.setattr(main.prodamus_provider, "handle_webhook", fake_handle)

    response = TestClient(main.app).post(
        "/v1/billing/webhooks/prodamus",
        data={"order_id": "order-1", "payment_status": "success"},
        headers={"Sign": "whatever"},
    )

    assert response.status_code == 200
    assert called == [True]


def test_pick_checkout_provider_routes_eu_to_prodamus_when_flag_on(monkeypatch):
    from cloud.app.billing import pick_checkout_provider

    monkeypatch.setattr("cloud.app.config.PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr("cloud.app.config.PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.feature_flags.flag_enabled", lambda *a, **k: True)

    assert pick_checkout_provider("eu", client=SimpleNamespace()) == "prodamus"
    assert pick_checkout_provider("other", client=SimpleNamespace()) == "prodamus"
    assert pick_checkout_provider("by", client=SimpleNamespace()) == "prodamus"


def test_checkout_builds_byn_payload_without_bypass(monkeypatch):
    """``by`` is a normal Prodamus-served tier like eu/other — no
    allow_any_tier needed, unlike the ru admin test-checkout path."""
    from cloud.app.providers import prodamus_provider

    captured: dict = {}

    def fake_get(url, params=None, timeout=None, follow_redirects=None):
        captured["params"] = dict(params)
        return SimpleNamespace(raise_for_status=lambda: None, text="https://pay.example.com/pay/by1")

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_BYN_CENTS", 4300)
    monkeypatch.setattr(prodamus_provider.httpx, "get", fake_get)
    _fake_user_email(monkeypatch, prodamus_provider)

    result = prodamus_provider.create_checkout(SimpleNamespace(), "user-1", "by")

    assert result["confirmation_url"] == "https://pay.example.com/pay/by1"
    assert result["currency"] == "BYN"
    assert captured["params"]["currency"] == "byn"
    assert captured["params"]["products[0][price]"] == "43.00"


def test_webhook_activates_on_success_byn(monkeypatch):
    from cloud.app.providers import prodamus_provider
    import asyncio

    monkeypatch.setattr(prodamus_provider, "PRODAMUS_FORM_URL", "https://pay.example.com")
    monkeypatch.setattr(prodamus_provider, "PRODAMUS_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_BYN_CENTS", 4300)

    activated: list[dict] = []
    monkeypatch.setattr(prodamus_provider, "claim_idempotency", lambda *a, **k: False)
    monkeypatch.setattr(
        prodamus_provider,
        "activate_subscription",
        lambda _client, user_id, **kw: activated.append({"user_id": user_id, **kw}),
    )

    form = _webhook_form(currency="byn", sum="43.00", _param_price_tier="by")
    request = _make_request(form, "secret")
    result = asyncio.run(prodamus_provider.handle_webhook(SimpleNamespace(), request))

    assert result == {"ok": "activated"}
    assert activated and activated[0]["price_tier"] == "by"


def test_admin_prodamus_test_checkout_requires_admin_key(monkeypatch):
    from cloud.app import main

    monkeypatch.setattr("cloud.app.config.ADMIN_SECRET", "topsecret")
    monkeypatch.setattr("cloud.app.admin.ADMIN_SECRET", "topsecret")

    response = TestClient(main.app).post(
        "/v1/admin/billing/prodamus-test-checkout",
        json={"user_id": "user-1"},
    )
    assert response.status_code == 401


def test_admin_prodamus_test_checkout_routes_ru_through_prodamus(monkeypatch):
    """The regular checkout endpoints are untouched — only this admin-gated
    route can put a ru payment through Prodamus, for the one controlled test
    payment needed to verify the webhook/HMAC before any default changes."""
    from cloud.app import main

    monkeypatch.setattr("cloud.app.config.ADMIN_SECRET", "topsecret")
    monkeypatch.setattr("cloud.app.admin.ADMIN_SECRET", "topsecret")
    monkeypatch.setattr(main, "service_client", lambda: object())

    captured: dict = {}

    def fake_create_checkout(_client, user_id, tier, referral_code=None, *, allow_any_tier=False):
        captured.update(user_id=user_id, tier=tier, allow_any_tier=allow_any_tier)
        return {"provider": "prodamus", "confirmation_url": "https://pay.example.com/pay/ru1"}

    monkeypatch.setattr(main.prodamus_provider, "create_checkout", fake_create_checkout)

    response = TestClient(main.app).post(
        "/v1/admin/billing/prodamus-test-checkout",
        json={"user_id": "user-1", "price_tier": "ru"},
        headers={"X-PLG-Admin-Key": "topsecret"},
    )

    assert response.status_code == 200
    assert response.json()["confirmation_url"] == "https://pay.example.com/pay/ru1"
    assert captured == {"user_id": "user-1", "tier": "ru", "allow_any_tier": True}


def test_admin_manual_refund_requires_admin_key(monkeypatch):
    from cloud.app import main

    monkeypatch.setattr("cloud.app.config.ADMIN_SECRET", "topsecret")
    monkeypatch.setattr("cloud.app.admin.ADMIN_SECRET", "topsecret")

    response = TestClient(main.app).post(
        "/v1/admin/billing/manual-refund",
        json={"user_id": "user-1"},
    )
    assert response.status_code == 401


def test_admin_manual_refund_calls_expire_subscription(monkeypatch):
    """Uses the real billing.expire_subscription — not a raw profiles patch —
    so it stays consistent with every other refund path (matches what
    yookassa_provider's refund.succeeded webhook does)."""
    from cloud.app import main

    monkeypatch.setattr("cloud.app.config.ADMIN_SECRET", "topsecret")
    monkeypatch.setattr("cloud.app.admin.ADMIN_SECRET", "topsecret")
    monkeypatch.setattr(main, "service_client", lambda: "fake-client")

    expired: list[tuple] = []
    monkeypatch.setattr(
        main, "expire_subscription", lambda client, user_id: expired.append((client, user_id))
    )

    response = TestClient(main.app).post(
        "/v1/admin/billing/manual-refund",
        json={"user_id": "user-1", "reason": "refunded in Prodamus panel"},
        headers={"X-PLG-Admin-Key": "topsecret"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "user_id": "user-1", "status": "expired"}
    assert expired == [("fake-client", "user-1")]


def test_admin_manual_refund_works_without_a_reason(monkeypatch):
    from cloud.app import main

    monkeypatch.setattr("cloud.app.config.ADMIN_SECRET", "topsecret")
    monkeypatch.setattr("cloud.app.admin.ADMIN_SECRET", "topsecret")
    monkeypatch.setattr(main, "service_client", lambda: "fake-client")
    monkeypatch.setattr(main, "expire_subscription", lambda client, user_id: None)

    response = TestClient(main.app).post(
        "/v1/admin/billing/manual-refund",
        json={"user_id": "user-1"},
        headers={"X-PLG-Admin-Key": "topsecret"},
    )
    assert response.status_code == 200


def test_admin_manual_activate_requires_admin_key(monkeypatch):
    from cloud.app import main

    monkeypatch.setattr("cloud.app.config.ADMIN_SECRET", "topsecret")
    monkeypatch.setattr("cloud.app.admin.ADMIN_SECRET", "topsecret")

    response = TestClient(main.app).post(
        "/v1/admin/billing/manual-activate",
        json={"user_id": "user-1", "order_id": "49304825", "price_tier": "ru_promo"},
    )
    assert response.status_code == 401


def test_admin_manual_activate_uses_price_book_amount_not_caller_input(monkeypatch):
    """Regression recovery path for the 2026-09-29 incident (order 49304825).
    Amount/currency come from price_entry(tier), never from the request body —
    a caller can't grant a bigger entitlement than the tier's real price."""
    from cloud.app import main

    monkeypatch.setattr("cloud.app.config.ADMIN_SECRET", "topsecret")
    monkeypatch.setattr("cloud.app.admin.ADMIN_SECRET", "topsecret")
    monkeypatch.setattr(main, "service_client", lambda: "fake-client")
    monkeypatch.setattr("cloud.app.pricing.PRICE_CIS_RUB_PROMO", 299)

    claimed: list[dict] = []
    monkeypatch.setattr(
        main,
        "claim_idempotency",
        lambda client, **kw: claimed.append(kw) or False,
    )
    activated: list[dict] = []
    monkeypatch.setattr(
        main,
        "activate_subscription",
        lambda client, user_id, **kw: activated.append({"user_id": user_id, **kw}),
    )

    response = TestClient(main.app).post(
        "/v1/admin/billing/manual-activate",
        json={
            "user_id": "aea89418-1818-46e6-9f25-7afe0c9091d1",
            "order_id": "49304825",
            "price_tier": "ru_promo",
            "reason": "webhook 400 bug, payment confirmed in Prodamus panel",
        },
        headers={"X-PLG-Admin-Key": "topsecret"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "activated"
    assert claimed[0]["idempotency_key"] == "success:49304825"
    assert claimed[0]["amount_cents"] == 29900
    assert claimed[0]["currency"] == "rub"
    assert activated[0] == {
        "user_id": "aea89418-1818-46e6-9f25-7afe0c9091d1",
        "provider": "prodamus",
        "external_id": "49304825",
        "price_tier": "ru_promo",
    }


def test_admin_manual_activate_is_idempotent(monkeypatch):
    """A genuine Prodamus retry for the same order (or a repeat admin click)
    must not double-activate — claim_idempotency already guards this via the
    same f"success:{order_id}" key the real webhook uses."""
    from cloud.app import main

    monkeypatch.setattr("cloud.app.config.ADMIN_SECRET", "topsecret")
    monkeypatch.setattr("cloud.app.admin.ADMIN_SECRET", "topsecret")
    monkeypatch.setattr(main, "service_client", lambda: "fake-client")
    monkeypatch.setattr(main, "claim_idempotency", lambda client, **kw: True)

    activated: list[dict] = []
    monkeypatch.setattr(
        main,
        "activate_subscription",
        lambda client, user_id, **kw: activated.append({"user_id": user_id, **kw}),
    )

    response = TestClient(main.app).post(
        "/v1/admin/billing/manual-activate",
        json={"user_id": "user-1", "order_id": "49304825", "price_tier": "ru_promo"},
        headers={"X-PLG-Admin-Key": "topsecret"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": "duplicate"}
    assert activated == []

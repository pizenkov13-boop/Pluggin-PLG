"""Payment payload checks for the live ЮKassa provider."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def test_checkout_sends_fiscal_receipt(monkeypatch):
    from cloud.app.providers import yookassa_provider

    captured: dict = {}

    class Payment:
        @staticmethod
        def create(payload, idempotence_key):
            captured["payload"] = payload
            captured["idempotence_key"] = idempotence_key
            return SimpleNamespace(
                id="payment-1",
                confirmation=SimpleNamespace(confirmation_url="https://pay.example/1"),
            )

    class Configuration:
        @staticmethod
        def configure(shop_id, secret_key):
            captured["credentials"] = (shop_id, secret_key)

    fake_client = SimpleNamespace(
        auth=SimpleNamespace(
            admin=SimpleNamespace(
                get_user_by_id=lambda _user_id: SimpleNamespace(
                    user=SimpleNamespace(email="buyer@example.com")
                )
            )
        )
    )
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_CIS_RUB", 899)
    monkeypatch.setitem(sys.modules, "yookassa", SimpleNamespace(Configuration=Configuration, Payment=Payment))

    result = yookassa_provider.create_checkout(fake_client, "user-1")

    assert result["confirmation_url"] == "https://pay.example/1"
    receipt = captured["payload"]["receipt"]
    assert receipt["customer"]["email"] == "buyer@example.com"
    assert receipt["internet"] is True
    assert receipt["items"] == [
        {
            "description": "Пакет PLG — 30 дней доступа",
            "quantity": "1.00",
            "amount": {"value": "899.00", "currency": "RUB"},
            "vat_code": 1,
            "payment_mode": "full_payment",
            "payment_subject": "service",
        }
    ]


def test_checkout_uses_promo_amount_for_ru_promo(monkeypatch):
    from cloud.app.providers import yookassa_provider

    captured: dict = {}

    class Payment:
        @staticmethod
        def create(payload, idempotence_key):
            captured["payload"] = payload
            return SimpleNamespace(
                id="payment-1",
                confirmation=SimpleNamespace(confirmation_url="https://pay.example/1"),
            )

    class Configuration:
        @staticmethod
        def configure(shop_id, secret_key):
            pass

    fake_client = SimpleNamespace(
        auth=SimpleNamespace(
            admin=SimpleNamespace(
                get_user_by_id=lambda _user_id: SimpleNamespace(
                    user=SimpleNamespace(email="buyer@example.com")
                )
            )
        )
    )
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_CIS_RUB_PROMO", 299)
    monkeypatch.setitem(sys.modules, "yookassa", SimpleNamespace(Configuration=Configuration, Payment=Payment))

    result = yookassa_provider.create_checkout(fake_client, "user-1", "ru_promo")

    assert result["price_tier"] == "ru_promo"
    assert result["amount_rub"] == 299
    payload = captured["payload"]
    assert payload["amount"] == {"value": "299.00", "currency": "RUB"}
    assert payload["metadata"]["price_tier"] == "ru_promo"
    assert payload["receipt"]["items"][0]["amount"] == {"value": "299.00", "currency": "RUB"}


def test_payment_webhook_accepts_promo_amount(monkeypatch):
    from cloud.app.providers import yookassa_provider

    activated: list[dict] = []
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_CIS_RUB_PROMO", 299)
    monkeypatch.setattr(
        yookassa_provider,
        "_fetch_payment",
        lambda _payment_id: SimpleNamespace(
            status="succeeded",
            metadata={"user_id": "user-1", "price_tier": "ru_promo"},
            amount=SimpleNamespace(value="299.00", currency="RUB"),
        ),
    )
    claimed: list[dict] = []
    monkeypatch.setattr(
        yookassa_provider,
        "claim_idempotency",
        lambda *a, **k: (claimed.append(k) or False),
    )
    monkeypatch.setattr(
        yookassa_provider,
        "activate_subscription",
        lambda _client, user_id, **kw: activated.append({"user_id": user_id, **kw}),
    )

    result = yookassa_provider.handle_webhook(
        SimpleNamespace(),
        {"event": "payment.succeeded", "object": {"id": "payment-1"}},
    )

    assert result == {"ok": "activated"}
    assert activated and activated[0]["price_tier"] == "ru_promo"
    # the payment_events row records the frozen tier (drives promo_paid_count)
    assert claimed and claimed[0]["price_tier"] == "ru_promo"


def test_payment_webhook_rejects_full_price_on_a_promo_payment(monkeypatch):
    """A `ru_promo` payment that somehow carries 499 ₽ is rejected — the frozen
    tier says it owes 299."""
    from cloud.app.providers import yookassa_provider
    from fastapi import HTTPException

    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_CIS_RUB", 499)
    monkeypatch.setattr("cloud.app.pricing.PRICE_CIS_RUB_PROMO", 299)
    monkeypatch.setattr(
        yookassa_provider,
        "_fetch_payment",
        lambda _payment_id: SimpleNamespace(
            status="succeeded",
            metadata={"user_id": "user-1", "price_tier": "ru_promo"},
            amount=SimpleNamespace(value="499.00", currency="RUB"),
        ),
    )
    monkeypatch.setattr(yookassa_provider, "claim_idempotency", lambda *a, **k: (_ for _ in ()).throw(AssertionError("claimed")))

    with pytest.raises(HTTPException) as exc:
        yookassa_provider.handle_webhook(
            SimpleNamespace(),
            {"event": "payment.succeeded", "object": {"id": "payment-1"}},
        )
    assert exc.value.status_code == 400


def test_refund_webhook_fetches_original_payment(monkeypatch):
    from cloud.app.providers import yookassa_provider

    calls: list[tuple[str, str]] = []
    original_payment = SimpleNamespace(
        status="succeeded",
        metadata={"user_id": "user-1", "price_tier": "cis"},
    )
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setattr(
        yookassa_provider,
        "_fetch_payment",
        lambda payment_id: (calls.append(("fetch", payment_id)) or original_payment),
    )
    monkeypatch.setattr(yookassa_provider, "claim_idempotency", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        yookassa_provider,
        "expire_subscription",
        lambda _client, user_id: calls.append(("expire", user_id)),
    )

    result = yookassa_provider.handle_webhook(
        SimpleNamespace(),
        {
            "event": "refund.succeeded",
            "object": {"id": "refund-1", "payment_id": "payment-1"},
        },
    )

    assert result == {"ok": "refunded"}
    assert calls == [("fetch", "payment-1"), ("expire", "user-1")]


def test_duplicate_payment_webhook_does_not_activate_twice(monkeypatch):
    from cloud.app.providers import yookassa_provider

    activated: list[str] = []
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_CIS_RUB", 899)
    monkeypatch.setattr(
        yookassa_provider,
        "_fetch_payment",
        lambda _payment_id: SimpleNamespace(
            status="succeeded",
            metadata={"user_id": "user-1", "price_tier": "cis"},
            amount=SimpleNamespace(value="899.00", currency="RUB"),
        ),
    )
    monkeypatch.setattr(yookassa_provider, "claim_idempotency", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        yookassa_provider,
        "activate_subscription",
        lambda *_args, **_kwargs: activated.append("called"),
    )

    result = yookassa_provider.handle_webhook(
        SimpleNamespace(),
        {"event": "payment.succeeded", "object": {"id": "payment-1"}},
    )

    assert result == {"ok": "duplicate"}
    assert activated == []


def test_payment_webhook_rejects_wrong_amount(monkeypatch):
    from cloud.app.providers import yookassa_provider
    from fastapi import HTTPException

    claimed: list[bool] = []
    activated: list[bool] = []
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_CIS_RUB", 899)
    monkeypatch.setattr(
        yookassa_provider,
        "_fetch_payment",
        lambda _payment_id: SimpleNamespace(
            status="succeeded",
            metadata={"user_id": "user-1", "price_tier": "cis"},
            amount=SimpleNamespace(value="10.00", currency="RUB"),
        ),
    )
    monkeypatch.setattr(
        yookassa_provider,
        "claim_idempotency",
        lambda *args, **kwargs: claimed.append(True),
    )
    monkeypatch.setattr(
        yookassa_provider,
        "activate_subscription",
        lambda *_args, **_kwargs: activated.append(True),
    )

    try:
        yookassa_provider.handle_webhook(
            SimpleNamespace(),
            {"event": "payment.succeeded", "object": {"id": "payment-1"}},
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Payment amount does not match the subscription price."
    else:
        raise AssertionError("wrong payment amount was accepted")

    assert claimed == []
    assert activated == []


def test_yookassa_webhook_rejects_untrusted_source_ip(monkeypatch):
    from cloud.app import main

    called: list[bool] = []
    monkeypatch.setattr(main, "YOOKASSA_VERIFY_IP", True)
    monkeypatch.setattr(main, "YOOKASSA_WEBHOOK_IPS", ["203.0.113.0/27"])
    monkeypatch.setattr(main, "client_ip", lambda _request: "8.8.8.8")
    monkeypatch.setattr(main.yookassa_provider, "handle_webhook", lambda *_args: called.append(True))

    response = TestClient(main.app).post(
        "/v1/billing/webhooks/yookassa",
        json={"event": "payment.succeeded", "object": {"id": "payment-1"}},
    )

    assert response.status_code == 403
    assert called == []


def _stub_paid_payment(monkeypatch, yookassa_provider, released, activate):
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SHOP_ID", "shop")
    monkeypatch.setattr(yookassa_provider, "YOOKASSA_SECRET_KEY", "secret")
    monkeypatch.setattr("cloud.app.pricing.PRICE_CIS_RUB", 499)
    monkeypatch.setattr(
        yookassa_provider,
        "_fetch_payment",
        lambda _payment_id: SimpleNamespace(
            status="succeeded",
            metadata={"user_id": "user-1", "price_tier": "ru"},
            amount=SimpleNamespace(value="499.00", currency="RUB"),
        ),
    )
    monkeypatch.setattr(yookassa_provider, "claim_idempotency", lambda *a, **k: False)
    monkeypatch.setattr(yookassa_provider, "activate_subscription", activate)
    monkeypatch.setattr(
        "cloud.app.billing.release_idempotency",
        lambda _client, *, provider, idempotency_key: released.append((provider, idempotency_key)),
    )


def test_failed_activation_releases_the_claim_so_the_retry_is_processed(monkeypatch):
    """A paid webhook whose activation fails must not stay marked as processed:
    the provider's retry would otherwise come back as "duplicate" and the paying
    user would never get access."""
    from cloud.app.providers import yookassa_provider

    released: list[tuple[str, str]] = []

    def boom(*_a, **_k):
        raise RuntimeError("db unavailable")

    _stub_paid_payment(monkeypatch, yookassa_provider, released, boom)

    with pytest.raises(RuntimeError):
        yookassa_provider.handle_webhook(
            SimpleNamespace(),
            {"event": "payment.succeeded", "object": {"id": "payment-1"}},
        )
    assert released == [("yookassa", "payment.succeeded:payment-1")]


def test_successful_activation_keeps_the_claim(monkeypatch):
    from cloud.app.providers import yookassa_provider

    released: list[tuple[str, str]] = []
    _stub_paid_payment(monkeypatch, yookassa_provider, released, lambda *_a, **_k: None)

    result = yookassa_provider.handle_webhook(
        SimpleNamespace(),
        {"event": "payment.succeeded", "object": {"id": "payment-1"}},
    )
    assert result == {"ok": "activated"}
    assert released == []


def test_release_failure_does_not_mask_the_original_error(monkeypatch):
    from cloud.app.billing import release_claim_on_failure

    def bad_release(*_a, **_k):
        raise OSError("release failed too")

    monkeypatch.setattr("cloud.app.billing.release_idempotency", bad_release)
    with pytest.raises(ValueError, match="original"):
        with release_claim_on_failure(SimpleNamespace(), provider="p", idempotency_key="k"):
            raise ValueError("original")

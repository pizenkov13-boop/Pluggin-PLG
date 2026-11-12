"""Launch promo — `ru_promo` (299 ₽) gate and public status (cloud/app/promo.py)."""

from __future__ import annotations

import pytest

from cloud.app import promo as promo_mod
from cloud.app.pricing import is_domestic, price_label


class _Resp:
    def __init__(self, *, data=None, count=None):
        self.data = data
        self.count = count


class _RowQuery:
    """Applies the recorded `.eq()` filters to a row list on execute() — so
    a test can prove promo_paid_count matches on price_tier, not on amount."""

    def __init__(self, rows, eq_log):
        self._rows = rows
        self._eq = eq_log

    def select(self, *a, **k):
        return self

    def eq(self, field, value):
        self._eq.append((field, value))
        self._rows = [r for r in self._rows if r.get(field) == value]
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return _Resp(data=list(self._rows))


class _CountQuery:
    """billing_checkout_links count query — returns a preset .count."""

    def __init__(self, count):
        self._count = count

    @property
    def not_(self):
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def is_(self, *a, **k):
        return self

    def gt(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return _Resp(count=self._count)


_PROMO_ROW = {
    "provider": "yookassa",
    "event_type": "payment.succeeded",
    "price_tier": "ru_promo",
}


class _FakeClient:
    """Answers the queries promo.py makes.

    `paid_user_ids` — shorthand: each id becomes one successful ru_promo
    payment row. `payment_rows` — full control over payment_events rows.
    `taken` — total live promo links; promo_taken_count splits it across two
    billing_checkout_links queries, so the fake returns the whole count on the
    first of each pair and 0 on the second.
    """

    def __init__(self, *, paid_user_ids=(), payment_rows=None, taken=0):
        if payment_rows is not None:
            self._rows = list(payment_rows)
        else:
            self._rows = [{**_PROMO_ROW, "user_id": u} for u in paid_user_ids]
        self._taken = taken
        self._link_calls = 0
        self.eq_log: list[tuple[str, object]] = []

    def table(self, name):
        if name == "payment_events":
            return _RowQuery(list(self._rows), self.eq_log)
        if name == "billing_checkout_links":
            count = self._taken if self._link_calls % 2 == 0 else 0
            self._link_calls += 1
            return _CountQuery(count)
        raise AssertionError(f"unexpected table {name}")


@pytest.fixture(autouse=True)
def _promo_defaults(monkeypatch):
    monkeypatch.setattr(promo_mod, "PROMO_ENABLED", True)
    monkeypatch.setattr(promo_mod, "PROMO_MAX_PAID", 100)
    monkeypatch.setattr(promo_mod, "PROMO_LINK_BUDGET", 108)


def test_price_book_has_promo_tier():
    assert price_label("ru_promo") == "299 ₽"
    assert is_domestic("ru_promo") is True


def test_promo_paid_count_dedups_users():
    client = _FakeClient(paid_user_ids=["a", "a", "b", "c", None])
    assert promo_mod.promo_paid_count(client) == 3


def test_promo_paid_counts_by_frozen_tier_not_by_amount():
    # A ru_promo purchase counts at ANY amount (a 10 ₽ smoke test still is a
    # ru_promo purchase — the fix is to test off the promo tier, see below).
    client = _FakeClient(
        payment_rows=[
            {**_PROMO_ROW, "user_id": "a", "amount_cents": 29900},
            {**_PROMO_ROW, "user_id": "b", "amount_cents": 1000},
            {**_PROMO_ROW, "user_id": "b", "amount_cents": 1000},  # dup user
        ]
    )
    assert promo_mod.promo_paid_count(client) == 2


def test_promo_paid_counts_prodamus_rows_too():
    """Regression: promo_paid_count must not filter on provider — Prodamus is
    the preferred provider for ru_promo since 2026-09-29 (the project issue log (not included)
    §0ai), and prodamus_provider.handle_webhook normalizes its success event
    to event_type="payment.succeeded" specifically so this stays
    provider-agnostic. A provider="yookassa" filter here would silently stop
    counting Prodamus sales and let the promo overrun its cap."""
    client = _FakeClient(
        payment_rows=[
            {"provider": "yookassa", "event_type": "payment.succeeded", "price_tier": "ru_promo", "user_id": "a"},
            {"provider": "prodamus", "event_type": "payment.succeeded", "price_tier": "ru_promo", "user_id": "b"},
        ]
    )
    assert promo_mod.promo_paid_count(client) == 2


def test_promo_paid_ignores_regular_tier_payments():
    client = _FakeClient(
        payment_rows=[
            {**_PROMO_ROW, "user_id": "promo1"},
            {"provider": "yookassa", "event_type": "payment.succeeded",
             "price_tier": "ru", "user_id": "regular1", "amount_cents": 29900},
            {"provider": "yookassa", "event_type": "payment.succeeded",
             "price_tier": None, "user_id": "legacy1"},
        ]
    )
    assert promo_mod.promo_paid_count(client) == 1


def test_promo_paid_ignores_non_succeeded_events():
    client = _FakeClient(
        payment_rows=[
            {**_PROMO_ROW, "user_id": "paid1"},
            {**_PROMO_ROW, "event_type": "payment.canceled", "user_id": "cancelled1"},
            {**_PROMO_ROW, "event_type": "refund.succeeded", "user_id": "refunded1"},
        ]
    )
    assert promo_mod.promo_paid_count(client) == 1


def test_promo_paid_query_filters_on_tier_never_on_amount():
    client = _FakeClient(paid_user_ids=["a"])
    promo_mod.promo_paid_count(client)
    fields = [f for f, _ in client.eq_log]
    assert ("price_tier", "ru_promo") in client.eq_log
    assert ("event_type", "payment.succeeded") in client.eq_log
    assert "amount_cents" not in fields


def test_promo_taken_count_reads_exact_count():
    assert promo_mod.promo_taken_count(_FakeClient(taken=42)) == 42


def test_promo_available_while_both_bounds_hold():
    client = _FakeClient(paid_user_ids=[f"u{i}" for i in range(37)], taken=40)
    assert promo_mod.promo_tier_available(client) is True


def test_promo_closes_when_100_paid():
    client = _FakeClient(paid_user_ids=[f"u{i}" for i in range(100)], taken=0)
    assert promo_mod.promo_tier_available(client) is False


def test_promo_closes_when_link_budget_full():
    client = _FakeClient(paid_user_ids=["u1"], taken=108)
    assert promo_mod.promo_tier_available(client) is False


def test_promo_disabled_by_flag(monkeypatch):
    monkeypatch.setattr(promo_mod, "PROMO_ENABLED", False)
    client = _FakeClient(paid_user_ids=["u1"], taken=0)
    assert promo_mod.promo_tier_available(client) is False


def test_clamp_downgrades_promo_when_unavailable():
    full = _FakeClient(paid_user_ids=[f"u{i}" for i in range(100)], taken=0)
    assert promo_mod.clamp_promo_tier("ru_promo", full) == "ru"

    live = _FakeClient(paid_user_ids=["u1"], taken=1)
    assert promo_mod.clamp_promo_tier("ru_promo", live) == "ru_promo"


def test_clamp_passes_through_non_promo_tiers():
    client = _FakeClient()
    assert promo_mod.clamp_promo_tier("eu", client) == "eu"
    assert promo_mod.clamp_promo_tier("cis", client) == "ru"  # legacy alias


def test_clamp_falls_back_to_ru_on_lookup_error():
    class _Broken:
        def table(self, _name):
            raise RuntimeError("db down")

    assert promo_mod.clamp_promo_tier("ru_promo", _Broken()) == "ru"


def test_promo_status_shape_and_counter():
    client = _FakeClient(paid_user_ids=[f"u{i}" for i in range(37)], taken=5)
    status = promo_mod.promo_status(client)
    assert status == {
        "active": True,
        "paid": 37,
        "cap": 100,
        "price_label": "299 ₽",
        "regular_price_label": "499 ₽",
    }


def test_promo_status_inactive_and_capped_once_sold_out():
    client = _FakeClient(paid_user_ids=[f"u{i}" for i in range(140)], taken=0)
    status = promo_mod.promo_status(client)
    assert status["active"] is False
    assert status["paid"] == 100  # never reports past the cap


def test_promo_status_survives_lookup_error():
    class _Broken:
        def table(self, _name):
            raise RuntimeError("db down")

    assert promo_mod.promo_status(_Broken()) == {"active": False, "paid": 0, "cap": 100}


# --- resolve_web_offer_tier now offers ru_promo on the domestic path ----------
#
# Plain lookups (default, enforce_link_budget=False) check promo_paid_available
# only. enforce_link_budget=True — the checkout-link freeze / real-payment
# path — additionally checks promo_tier_available (paid + link budget).


def test_web_offer_tier_offers_promo_when_available(monkeypatch):
    from cloud.app import billing

    monkeypatch.setattr("cloud.app.feature_flags.flag_enabled", lambda *a, **k: False)
    monkeypatch.setattr("cloud.app.promo.promo_paid_available", lambda _c: True)
    assert billing.resolve_web_offer_tier("RU", client=object()) == "ru_promo"
    assert billing.resolve_web_offer_tier(None, client=object()) == "ru_promo"


def test_web_offer_tier_plain_ru_when_promo_closed(monkeypatch):
    from cloud.app import billing

    monkeypatch.setattr("cloud.app.feature_flags.flag_enabled", lambda *a, **k: False)
    monkeypatch.setattr("cloud.app.promo.promo_paid_available", lambda _c: False)
    assert billing.resolve_web_offer_tier("RU", client=object()) == "ru"


def test_web_offer_tier_promo_never_overrides_international(monkeypatch):
    from cloud.app import billing

    monkeypatch.setattr("cloud.app.feature_flags.flag_enabled", lambda *a, **k: True)
    monkeypatch.setattr("cloud.app.promo.promo_paid_available", lambda _c: True)
    assert billing.resolve_web_offer_tier("DE", client=object()) == "eu"
    assert billing.resolve_web_offer_tier("US", client=object()) == "other"
    # ...but a RU visitor with the intl flag on still gets the promo.
    assert billing.resolve_web_offer_tier("RU", client=object()) == "ru_promo"


def test_web_offer_tier_promo_lookup_error_is_safe(monkeypatch):
    from cloud.app import billing

    monkeypatch.setattr("cloud.app.feature_flags.flag_enabled", lambda *a, **k: False)

    def _boom(_c):
        raise RuntimeError("db down")

    monkeypatch.setattr("cloud.app.promo.promo_paid_available", _boom)
    assert billing.resolve_web_offer_tier("RU", client=object()) == "ru"


def test_web_offer_tier_display_ignores_link_budget(monkeypatch):
    """Regression: a plain price lookup must not flip to 499 just because
    abandoned checkouts have temporarily filled the link reservation buffer —
    only confirmed paid sales should move the displayed price."""
    from cloud.app import billing

    monkeypatch.setattr("cloud.app.feature_flags.flag_enabled", lambda *a, **k: False)
    monkeypatch.setattr("cloud.app.promo.promo_paid_available", lambda _c: True)
    monkeypatch.setattr("cloud.app.promo.promo_tier_available", lambda _c: False)
    assert billing.resolve_web_offer_tier("RU", client=object()) == "ru_promo"


def test_web_offer_tier_link_budget_enforced_when_committing(monkeypatch):
    """A checkout-link freeze (enforce_link_budget=True) must still respect
    the link reservation buffer, even while paid sales are under the cap."""
    from cloud.app import billing

    monkeypatch.setattr("cloud.app.feature_flags.flag_enabled", lambda *a, **k: False)
    monkeypatch.setattr("cloud.app.promo.promo_paid_available", lambda _c: True)
    monkeypatch.setattr("cloud.app.promo.promo_tier_available", lambda _c: False)
    assert billing.resolve_web_offer_tier("RU", client=object(), enforce_link_budget=True) == "ru"


def test_promo_paid_available_ignores_link_budget():
    # 1 paid user, link budget already exhausted — display path stays on promo.
    client = _FakeClient(paid_user_ids=["u1"], taken=108)
    assert promo_mod.promo_paid_available(client) is True


def test_promo_paid_available_closes_when_100_paid():
    client = _FakeClient(paid_user_ids=[f"u{i}" for i in range(100)], taken=0)
    assert promo_mod.promo_paid_available(client) is False


def test_promo_paid_available_disabled_by_flag(monkeypatch):
    monkeypatch.setattr(promo_mod, "PROMO_ENABLED", False)
    client = _FakeClient(paid_user_ids=["u1"], taken=0)
    assert promo_mod.promo_paid_available(client) is False

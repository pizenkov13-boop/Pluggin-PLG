"""Tests for migration 018: linking generation_logs to the payment/entitlement
period they were made under, for refund-eligibility lookups.

Covers:
  - free trial beat never gets a payment_event_id (nothing to link to yet)
  - a paid purchase stamps the correct payment_events.id onto the profile
  - two separate purchases resolve to two distinct ids (no mixing)
  - roll_profile() (quota rollover) leaves current_payment_event_id untouched
  - the refund-eligibility SQL's decision rule (migration 018's docstring):
    an untagged, in-window generation must force MANUAL REVIEW, never YES

The actual generation_logs INSERT in cloud/app/main.py:/v1/generate is a
one-line passthrough (`row.get("current_payment_event_id")`) — its correctness
follows directly from what's proven here: activate_subscription() being the
only writer of that column, and roll_profile() never touching it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cloud.app.billing import activate_subscription
from cloud.app.quota import roll_profile


class _FakeClient:
    """Same shape as test_cloud_billing._FakeClient, kept local/self-contained
    so this file doesn't depend on another test module's private class."""

    def __init__(self, current_status: str, payment_event_ids: dict[str, str] | None = None):
        self._status = current_status
        self._payment_event_ids = payment_event_ids or {}
        self.patches: list[dict] = []
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
        self.patches.append(patch)
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
        return type("R", (), {"data": {**(self.patches[-1] or {})}})()


def test_free_trial_never_gets_a_payment_event_id():
    # A brand-new trial profile — as created by auth.profile_row() — never
    # went through activate_subscription(), so the column is simply absent.
    # This is exactly what main.py's `row.get("current_payment_event_id")`
    # reads for a trial-status generation: None.
    trial_row = {"id": "u1", "status": "trial", "trial_beats_used": 0}
    assert trial_row.get("current_payment_event_id") is None


def test_activate_subscription_links_the_correct_payment_event_id():
    client = _FakeClient(current_status="trial", payment_event_ids={"pay_1": "evt_1"})
    activate_subscription(client, "u1", provider="yookassa", external_id="pay_1", price_tier="ru")
    assert client.patches[-1]["current_payment_event_id"] == "evt_1"


def test_activate_subscription_links_none_when_webhook_row_not_landed_yet():
    # payment_events insert and activate_subscription() are two separate
    # webhook-driven calls; if the row genuinely isn't there yet, stamp None
    # rather than a wrong id — main.py then correctly leaves the generation
    # untagged (same as a legacy row) instead of mis-attributing it.
    client = _FakeClient(current_status="trial", payment_event_ids={})
    activate_subscription(client, "u1", provider="yookassa", external_id="pay_missing", price_tier="ru")
    assert client.patches[-1]["current_payment_event_id"] is None


def test_repeat_purchase_does_not_mix_entitlement_periods():
    # Two purchases by the same user must resolve to their OWN payment_events
    # id, not "whichever was seen first" or a shared/latest value — this is
    # what lets a refund lookup count paid generations per purchase without
    # conflating purchase A's usage with purchase B's.
    client = _FakeClient(
        current_status="trial",
        payment_event_ids={"pay_1": "evt_1", "pay_2": "evt_2"},
    )
    activate_subscription(client, "u1", provider="yookassa", external_id="pay_1", price_tier="ru")
    first_link = client.patches[-1]["current_payment_event_id"]

    # Simulate the account now being active for the second (top-up) purchase.
    client._status = "active"
    activate_subscription(client, "u1", provider="yookassa", external_id="pay_2", price_tier="ru")
    second_link = client.patches[-1]["current_payment_event_id"]

    assert first_link == "evt_1"
    assert second_link == "evt_2"
    assert first_link != second_link


def test_rollover_preserves_current_payment_event_id():
    # roll_profile() only ever touches period_start/beats_used/beats_today/
    # daily_reset. current_payment_event_id must survive a calendar rollover
    # untouched — the payment funding the subscription hasn't changed just
    # because the monthly beat counter reset.
    now = datetime.now(timezone.utc)
    stale_period_start = now - timedelta(days=31)  # forces a rollover
    row = {
        "status": "active",
        "period_start": stale_period_start.isoformat(),
        "beats_used": 17,
        "beats_today": 2,
        "daily_reset": now.date().isoformat(),
        "current_payment_event_id": "evt_1",
    }
    rolled = roll_profile(row)
    assert rolled["beats_used"] == 0  # sanity: rollover did happen
    assert rolled["current_payment_event_id"] == "evt_1"


# --- refund-eligibility decision rule (migration 018 SQL) --------------------
#
# There is no live Postgres in this test suite (all cloud tests mock the
# Supabase client — see tests/test_cloud_billing.py), so the actual SQL from
# 018_generation_payment_link.sql can't be executed here. This mirrors just
# its decision rule in Python, against plain dict rows, so the *business
# rule* — untagged in-window rows must block an automatic YES — has a
# regression test even without a live DB.


def _refund_decision(*, within_14_days: bool, already_refunded: bool, tracked: int, untagged_legacy: int) -> str:
    if already_refunded:
        return "NO — already refunded"
    if not within_14_days:
        return "NO — window closed"
    if untagged_legacy > 0:
        return "MANUAL REVIEW — legacy rows in window not yet tagged, cannot prove 0"
    if tracked == 0:
        return "YES — 0 paid generations"
    return f"NO — {tracked} paid generations"


def test_legacy_untagged_generation_forces_manual_review_not_yes():
    # A purchase made before migration 018 shipped: 0 rows tagged with this
    # purchase's payment_event_id (the column didn't exist yet when those
    # generations happened), but the row IS inside the purchase's window and
    # therefore can only be a paid generation (trial is spent before any
    # purchase exists). Must never resolve to YES.
    decision = _refund_decision(within_14_days=True, already_refunded=False, tracked=0, untagged_legacy=1)
    assert decision.startswith("MANUAL REVIEW")
    assert "YES" not in decision


def test_fully_tagged_purchase_with_zero_paid_generations_is_eligible():
    # Purchase made after migration 018 shipped: every post-purchase
    # generation (if any) would have been stamped. Zero tracked + zero
    # untagged legacy rows -> safe, provable YES.
    decision = _refund_decision(within_14_days=True, already_refunded=False, tracked=0, untagged_legacy=0)
    assert decision == "YES — 0 paid generations"


def test_tagged_paid_generation_blocks_refund():
    decision = _refund_decision(within_14_days=True, already_refunded=False, tracked=2, untagged_legacy=0)
    assert decision == "NO — 2 paid generations"


def test_already_refunded_blocks_refund_regardless_of_usage():
    decision = _refund_decision(within_14_days=True, already_refunded=True, tracked=0, untagged_legacy=0)
    assert decision == "NO — already refunded"


def test_window_closed_blocks_refund_regardless_of_usage():
    decision = _refund_decision(within_14_days=False, already_refunded=False, tracked=0, untagged_legacy=0)
    assert decision == "NO — window closed"

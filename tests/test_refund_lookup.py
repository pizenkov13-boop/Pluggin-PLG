from datetime import datetime, timedelta, timezone

from tools.refund_lookup import build_refund_lookup, render


NOW = datetime(2026, 9, 14, tzinfo=timezone.utc)


def _lookup(*, age=3, paid=(), free=(), untagged=()):
    purchased = NOW - timedelta(days=age)
    generations = [
        {"payment_event_id": "evt-current", "created_at": (purchased + timedelta(hours=1)).isoformat()}
        for _ in paid
    ]
    generations.extend(
        {"payment_event_id": None, "created_at": (NOW - timedelta(days=30)).isoformat()}
        for _ in free
    )
    generations.extend(
        {"payment_event_id": None, "created_at": (purchased + timedelta(hours=2)).isoformat()}
        for _ in untagged
    )
    return build_refund_lookup(
        account="customer@example.com",
        user_id="user-1",
        profile={
            "status": "active",
            "plan": "premium",
            "current_payment_event_id": "evt-current",
        },
        payments=[
            {
                "id": "evt-current",
                "event_type": "payment.succeeded",
                "amount_cents": 29900,
                "currency": "RUB",
                "processed_at": purchased.isoformat(),
            }
        ],
        generations=generations,
        now=NOW,
    )


def test_zero_paid_generations_is_yes_and_free_is_not_paid():
    result = _lookup(free=[1])
    assert result.decision == "YES"
    assert result.paid_generations_used == 0
    assert result.free_generations_used == 1


def test_paid_generation_is_no():
    result = _lookup(paid=[1])
    assert result.decision == "NO"
    assert "paid generation" in result.reason


def test_closed_window_is_no():
    result = _lookup(age=15)
    assert result.decision == "NO"
    assert "window is closed" in result.reason


def test_untagged_generation_after_purchase_is_manual_review():
    result = _lookup(untagged=[1])
    assert result.decision == "MANUAL REVIEW"
    assert "payment_event_id" in result.reason


def test_render_contains_support_fields_and_never_raw_generation_data():
    output = render(_lookup())
    assert "Account: customer@example.com" in output
    assert "Payment ID: evt-current" in output
    assert "REFUND ELIGIBLE: YES" in output
    assert "Traceback" not in output

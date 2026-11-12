"""Read-only support lookup for voluntary refund eligibility.

Usage::

    python tools/refund_lookup.py customer@example.com

The command only reads Supabase through the server-side service client. It does
not create refunds, update rows, or call any payment-provider API.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


REFUND_WINDOW_DAYS = 14
PAYMENT_COLUMNS = (
    "id,provider,event_type,amount_cents,currency,external_id,price_tier,processed_at"
)


@dataclass(frozen=True)
class RefundLookup:
    account: str
    user_id: str
    payment_id: str
    amount: str
    payment_status: str
    purchased_at: str
    entitlement: str
    paid_generations_used: int
    free_generations_used: int
    days_since_purchase: int | None
    refund_window: str
    decision: str
    reason: str


def _value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _rows(result: Any) -> list[dict[str, Any]]:
    data = _value(result, "data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _iso(value: Any) -> str:
    return str(value) if value else "—"


def _amount(payment: dict[str, Any]) -> str:
    cents = payment.get("amount_cents")
    if cents is None:
        return "—"
    try:
        amount = int(cents) / 100
    except (TypeError, ValueError):
        return "—"
    currency = str(payment.get("currency") or "").upper()
    return f"{amount:.2f} {currency}".strip()


def _entitlement(profile: dict[str, Any]) -> str:
    status = str(profile.get("status") or "unknown")
    plan = str(profile.get("plan") or "base")
    ends = profile.get("subscription_ends_at")
    suffix = f", through {ends}" if ends else ""
    return f"{status} ({plan}{suffix})"


def _is_reversed(event_type: str) -> bool:
    lowered = event_type.lower()
    return "refund" in lowered or "cancel" in lowered or "chargeback" in lowered


def build_refund_lookup(
    *,
    account: str,
    user_id: str,
    profile: dict[str, Any],
    payments: Iterable[dict[str, Any]],
    generations: Iterable[dict[str, Any]],
    now: datetime | None = None,
) -> RefundLookup:
    """Build the support decision from already-read rows without mutations."""

    payment_rows = list(payments)
    generation_rows = list(generations)
    payment_id = str(profile.get("current_payment_event_id") or "")
    payment = next((row for row in payment_rows if str(row.get("id") or "") == payment_id), None)
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    purchased_at = _parse_timestamp(payment.get("processed_at")) if payment else None
    age = max(0, (current_time - purchased_at).days) if purchased_at else None
    event_type = str(payment.get("event_type") or "unknown") if payment else "not found"

    paid_used = sum(
        1 for row in generation_rows if str(row.get("payment_event_id") or "") == payment_id and payment_id
    )

    successful_purchases = [
        _parse_timestamp(row.get("processed_at"))
        for row in payment_rows
        if row.get("processed_at") and not _is_reversed(str(row.get("event_type") or ""))
    ]
    first_purchase = min((value for value in successful_purchases if value), default=None)
    free_used = 0
    legacy_after_purchase = 0
    for row in generation_rows:
        if row.get("payment_event_id"):
            continue
        created_at = _parse_timestamp(row.get("created_at"))
        if created_at is None:
            legacy_after_purchase += 1
        elif first_purchase is None or created_at < first_purchase:
            free_used += 1
        elif purchased_at is not None and created_at >= purchased_at:
            legacy_after_purchase += 1

    refund_window = f"{age} days / {REFUND_WINDOW_DAYS} days" if age is not None else "unknown"
    decision = "MANUAL REVIEW"
    reason = "Payment linkage is missing or the payment event cannot be read."
    if payment and purchased_at:
        if _is_reversed(event_type):
            decision = "NO"
            reason = f"Payment event is already reversed: {event_type}."
        elif legacy_after_purchase:
            reason = (
                f"{legacy_after_purchase} generation(s) after purchase have no payment_event_id; "
                "paid/free usage cannot be proven."
            )
        elif age is not None and age > REFUND_WINDOW_DAYS:
            decision = "NO"
            reason = f"Purchase is {age} days old; the {REFUND_WINDOW_DAYS}-day window is closed."
        elif paid_used:
            decision = "NO"
            reason = f"{paid_used} paid generation(s) used under this payment."
        else:
            decision = "YES"
            reason = "Within 14 days and 0 paid generations used."

    return RefundLookup(
        account=account,
        user_id=user_id,
        payment_id=payment_id or "—",
        amount=_amount(payment or {}),
        payment_status=event_type,
        purchased_at=_iso(payment.get("processed_at") if payment else None),
        entitlement=_entitlement(profile),
        paid_generations_used=paid_used,
        free_generations_used=free_used,
        days_since_purchase=age,
        refund_window=refund_window,
        decision=decision,
        reason=reason,
    )


def _admin_users(client: Any) -> list[Any]:
    response = client.auth.admin.list_users(page=1, per_page=1000)
    users = _value(response, "users")
    if isinstance(users, list):
        return users
    if isinstance(response, list):
        return response
    return []


def lookup_account(client: Any, email: str, *, now: datetime | None = None) -> RefundLookup:
    normalized_email = email.strip().casefold()
    user = next(
        (
            candidate
            for candidate in _admin_users(client)
            if str(_value(candidate, "email") or "").strip().casefold() == normalized_email
        ),
        None,
    )
    if user is None:
        raise LookupError(f"Account not found: {email}")
    user_id = str(_value(user, "id") or "")
    profile_result = (
        client.table("profiles")
        .select("id,status,plan,subscription_ends_at,current_payment_event_id")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    profile_rows = _rows(profile_result)
    if not profile_rows:
        raise LookupError(f"Profile not found for account: {email}")
    profile = profile_rows[0]
    payments = _rows(
        client.table("payment_events")
        .select(PAYMENT_COLUMNS)
        .eq("user_id", user_id)
        .order("processed_at", desc=False)
        .execute()
    )
    generations = _rows(
        client.table("generation_logs")
        .select("id,payment_event_id,created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=False)
        .execute()
    )
    return build_refund_lookup(
        account=str(_value(user, "email") or email),
        user_id=user_id,
        profile=profile,
        payments=payments,
        generations=generations,
        now=now,
    )


def render(result: RefundLookup) -> str:
    values = (
        ("Account", result.account),
        ("User ID", result.user_id),
        ("Payment ID", result.payment_id),
        ("Amount", result.amount),
        ("Payment status", result.payment_status),
        ("Purchased at", result.purchased_at),
        ("Entitlement", result.entitlement),
        ("Paid generations used", str(result.paid_generations_used)),
        ("Free generations used", str(result.free_generations_used)),
        ("Days since purchase", str(result.days_since_purchase) if result.days_since_purchase is not None else "—"),
        ("Refund window", result.refund_window),
        ("REFUND ELIGIBLE", result.decision),
        ("Reason", result.reason),
    )
    return "\n".join(f"{label}: {value}" for label, value in values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only voluntary refund eligibility lookup.")
    parser.add_argument("email", help="account email")
    args = parser.parse_args(argv)
    try:
        from cloud.app.auth import service_client

        result = lookup_account(service_client(), args.email)
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Refund lookup failed: {exc}", file=sys.stderr)
        return 1
    print(render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

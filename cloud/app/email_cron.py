"""Scheduled email notifications — subscription expiry reminders."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import Client

from cloud.app.email import notify_subscription_expiring
from cloud.app.legal import _user_email

logger = logging.getLogger("plg.email_cron")

_WARN_DAYS = (3, 1)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _already_sent(client: Client, *, user_id: str, template: str, days: int) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    try:
        result = (
            client.table("notification_log")
            .select("id")
            .eq("user_id", user_id)
            .eq("template", template)
            .eq("status", "sent")
            .gte("created_at", since)
            .contains("metadata", {"days": days})
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception:  # noqa: BLE001
        return False


def run_subscription_expiry_reminders(
    client: Client,
    *,
    warn_days: tuple[int, ...] = _WARN_DAYS,
) -> dict[str, Any]:
    """Email active subscribers whose plan ends in N days (default 3 and 1)."""
    now = datetime.now(timezone.utc)
    sent = 0
    skipped = 0

    for days in warn_days:
        target_start = (now + timedelta(days=days - 1)).date().isoformat()
        target_end = (now + timedelta(days=days)).date().isoformat()
        result = (
            client.table("profiles")
            .select("id, status, subscription_ends_at")
            .in_("status", ["active", "grace"])
            .gte("subscription_ends_at", f"{target_start}T00:00:00+00:00")
            .lt("subscription_ends_at", f"{target_end}T00:00:00+00:00")
            .execute()
        )
        for row in result.data or []:
            user_id = str(row["id"])
            ends = _parse_ts(row.get("subscription_ends_at"))
            if not ends:
                skipped += 1
                continue
            actual_days = max(0, (ends.date() - now.date()).days)
            if actual_days not in warn_days:
                skipped += 1
                continue
            if _already_sent(client, user_id=user_id, template="subscription_expiring", days=actual_days):
                skipped += 1
                continue
            email = _user_email(client, user_id)
            if not email:
                skipped += 1
                continue
            if notify_subscription_expiring(client, user_id, email, days=actual_days):
                sent += 1
            else:
                skipped += 1

    logger.info("subscription expiry reminders sent=%s skipped=%s", sent, skipped)
    return {"ok": True, "sent": sent, "skipped": skipped}


def run_all(client: Client) -> dict[str, Any]:
    return run_subscription_expiry_reminders(client)

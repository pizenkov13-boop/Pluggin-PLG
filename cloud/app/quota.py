"""Server-side beat quota — 30 generations / 30 days + trial + grace."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from supabase import Client

from cloud.app.billing import apply_grace_expiry, billing_snapshot, trial_remaining
from cloud.app.config import BEAT_LIMIT, DAILY_BEAT_LIMIT, PERIOD_DAYS, TRIAL_BEATS
from cloud.app.pricing import price_label
from cloud.app.privacy import exception_kind


def _subscribe_cta() -> str:
    return f"Subscribe for {price_label('ru')}/month to continue."


def _skip_limit() -> bool:
    import os

    return os.getenv("PLG_SKIP_BEAT_LIMIT", "").strip().lower() in ("1", "true", "yes")


logger = logging.getLogger("plg.quota")


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def roll_profile(row: dict[str, Any]) -> dict[str, Any]:
    row = apply_grace_expiry(dict(row))
    today = _utc_today()
    period_start = _parse_ts(str(row["period_start"]))
    daily_reset = date.fromisoformat(str(row["daily_reset"])[:10])

    beats_used = int(row.get("beats_used") or 0)
    beats_today = int(row.get("beats_today") or 0)

    if today > daily_reset:
        beats_today = 0
        daily_reset = today

    status = str(row.get("status") or "expired")
    if status in ("active", "grace"):
        while datetime.now(timezone.utc) >= period_start + timedelta(days=PERIOD_DAYS):
            period_start = period_start + timedelta(days=PERIOD_DAYS)
            beats_used = 0

    row["period_start"] = period_start.isoformat()
    row["beats_used"] = beats_used
    row["beats_today"] = beats_today
    row["daily_reset"] = daily_reset.isoformat()
    return row


def quota_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    row = roll_profile(dict(row))
    status = str(row.get("status") or "expired")
    period_start = _parse_ts(str(row["period_start"]))
    period_end = period_start + timedelta(days=PERIOD_DAYS)
    days_until = max(0, (period_end.date() - _utc_today()).days)
    used = int(row["beats_used"])
    today_used = int(row["beats_today"])

    if status == "trial":
        remaining = trial_remaining(row)
        limit = TRIAL_BEATS
    elif status in ("active", "grace"):
        remaining = max(0, BEAT_LIMIT - used)
        limit = BEAT_LIMIT
    else:
        # expired / cancelled: no allowance. Reporting the full BEAT_LIMIT here
        # made a never-subscribed account that just spent its one trial beat
        # look like a paid subscriber with 30 beats left — the desktop
        # `remaining <= 0` gate then never fired and only the best-effort
        # billing preflight stopped a doomed generation.
        remaining = 0
        limit = 0
        days_until = 0

    snap = {
        "used": used if status != "trial" else int(row.get("trial_beats_used") or 0),
        "limit": limit,
        "remaining": remaining,
        "beats_today": today_used,
        "days_until_reset": days_until,
        "period_days": PERIOD_DAYS,
        "plan": row.get("plan", "base"),
        "status": status,
        "billing": billing_snapshot(row),
    }
    return snap


def ensure_can_generate(row: dict[str, Any]) -> dict[str, Any]:
    if _skip_limit():
        return roll_profile(dict(row))
    row = roll_profile(dict(row))
    status = str(row.get("status") or "expired")
    snap = quota_snapshot(row)

    if status == "trial":
        if snap["remaining"] <= 0:
            raise HTTPException(402, _subscribe_cta())
        return row

    if status not in ("active", "grace"):
        raise HTTPException(402, "Subscription inactive. Renew to generate beats.")

    if snap["remaining"] <= 0:
        raise HTTPException(
            429,
            f"Monthly limit reached ({BEAT_LIMIT} beats / {PERIOD_DAYS} days). "
            f"Resets in {snap['days_until_reset']} days.",
        )
    return row


def consume_beat(client: Client, user_id: str, row: dict[str, Any]) -> dict[str, Any]:
    before = quota_snapshot(row)
    row = ensure_can_generate(row)
    status = str(row.get("status") or "expired")
    now = datetime.now(timezone.utc).isoformat()

    if status == "trial":
        row["trial_beats_used"] = int(row.get("trial_beats_used") or 0) + 1
        row["beats_today"] = int(row.get("beats_today") or 0) + 1
        if int(row["trial_beats_used"]) >= TRIAL_BEATS:
            row["status"] = "expired"
    else:
        row["beats_used"] = int(row["beats_used"]) + 1
        row["beats_today"] = int(row["beats_today"]) + 1

    row["updated_at"] = now
    update = {
        "period_start": row["period_start"],
        "beats_used": row["beats_used"],
        "beats_today": row["beats_today"],
        "daily_reset": row["daily_reset"],
        "trial_beats_used": row.get("trial_beats_used", 0),
        "status": row.get("status"),
        "updated_at": now,
    }
    client.table("profiles").update(update).eq("id", user_id).execute()
    snap = quota_snapshot(row)
    _maybe_notify_quota_limit(client, user_id, before, snap)
    return snap


_RPC_AVAILABLE = True


def _reject(reason: str) -> HTTPException:
    if reason == "trial_ended":
        return HTTPException(402, _subscribe_cta())
    if reason == "monthly":
        return HTTPException(
            429, f"Monthly limit reached ({BEAT_LIMIT} beats / {PERIOD_DAYS} days). Renew or wait for reset."
        )
    return HTTPException(402, "Subscription inactive. Renew to generate beats.")


def reserve_beat(client: Client, user_id: str, row: dict[str, Any]) -> dict[str, Any]:
    """Atomically reserve ONE beat credit *before* the expensive LLM call.

    Uses the row-locked `plg_consume_beat` RPC so concurrent requests on the same
    account can't each pass the check and over-generate / over-spend (the read-
    check-write race in the old flow). Falls back to the legacy check-then-consume
    path when the migration (008) isn't applied yet, so code can ship first.

    Returns {"atomic": bool, "was_trial": bool}. Raises HTTPException if over limit.
    """
    global _RPC_AVAILABLE
    was_trial = str(row.get("status") or "") == "trial"

    if _RPC_AVAILABLE:
        try:
            resp = client.rpc(
                "plg_consume_beat",
                {
                    "p_user": user_id,
                    "p_beat_limit": BEAT_LIMIT,
                    "p_daily_limit": DAILY_BEAT_LIMIT,
                    "p_trial_beats": TRIAL_BEATS,
                    "p_period_days": PERIOD_DAYS,
                },
            ).execute()
            data = resp.data
            if isinstance(data, list):
                data = data[0] if data else None
            if isinstance(data, dict):
                if data.get("allowed"):
                    return {"atomic": True, "was_trial": bool(data.get("consumed_trial"))}
                raise _reject(str(data.get("reason") or ""))
            logger.warning("plg_consume_beat returned unexpected shape: %r", data)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if any(s in msg for s in ("plg_consume_beat", "does not exist", "pgrst202", "could not find the function")):
                _RPC_AVAILABLE = False
                logger.warning(
                    "plg_consume_beat RPC missing — using legacy quota path. "
                    "Apply migration 008_atomic_quota.sql to close the concurrency race."
                )
            else:
                logger.warning("plg_consume_beat RPC error, using legacy path type=%s", exception_kind(exc))

    # Legacy fallback: pre-check now; caller calls consume_beat() after generation.
    ensure_can_generate(row)
    return {"atomic": False, "was_trial": was_trial}


def refund_beat(client: Client, user_id: str, *, was_trial: bool) -> None:
    """Return a reserved credit if generation fails after reservation."""
    try:
        client.rpc("plg_refund_beat", {"p_user": user_id, "p_was_trial": was_trial}).execute()
    except Exception:  # noqa: BLE001
        logger.exception("refund_beat failed user=%s", user_id)


def _maybe_notify_quota_limit(
    client: Client,
    user_id: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    try:
        from cloud.app.email import notify_quota_limit
        from cloud.app.legal import _user_email

        email = _user_email(client, user_id)
        if not email:
            return
        status = str(after.get("status") or "")
        if (
            status in ("active", "grace")
            and int(before.get("remaining") or 0) > 0
            and int(after.get("remaining") or 0) == 0
        ):
            notify_quota_limit(
                client,
                user_id,
                email,
                days=int(after.get("days_until_reset") or 0),
            )
    except Exception:  # noqa: BLE001
        logger.exception("quota limit email failed user=%s", user_id)


def save_profile(client: Client, user_id: str, row: dict[str, Any]) -> None:
    client.table("profiles").update(
        {
            "period_start": row["period_start"],
            "beats_used": row["beats_used"],
            "beats_today": row["beats_today"],
            "daily_reset": row["daily_reset"],
            "status": row.get("status"),
            "trial_beats_used": row.get("trial_beats_used", 0),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", user_id).execute()

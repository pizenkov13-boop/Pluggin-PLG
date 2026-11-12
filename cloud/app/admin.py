"""Admin API — manual bans, alert triage."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import Header, HTTPException
from supabase import Client

from cloud.app.abuse import list_alerts
from cloud.app.config import ADMIN_SECRET

logger = logging.getLogger("plg.admin")


def require_admin(x_plg_admin: str | None = Header(default=None, alias="X-PLG-Admin-Key")) -> None:
    if not ADMIN_SECRET:
        raise HTTPException(503, "Admin API not configured (PLG_ADMIN_SECRET).")
    if not x_plg_admin or x_plg_admin != ADMIN_SECRET:
        raise HTTPException(401, "Invalid admin key.")


def ban_entity(
    client: Client,
    *,
    ban_type: str,
    ban_value: str,
    reason: str | None = None,
    banned_by: str = "admin",
    expires_at: str | None = None,
) -> dict[str, Any]:
    if ban_type not in ("user", "device", "ip", "fingerprint"):
        raise HTTPException(400, "Invalid ban_type.")

    ban_value = ban_value.strip()
    if not ban_value:
        raise HTTPException(400, "ban_value required.")

    client.table("security_bans").upsert(
        {
            "ban_type": ban_type,
            "ban_value": ban_value,
            "reason": reason,
            "banned_by": banned_by,
            "expires_at": expires_at,
        },
        on_conflict="ban_type,ban_value",
    ).execute()

    if ban_type == "user":
        client.table("profiles").update(
            {
                "banned": True,
                "ban_reason": reason or "Banned by admin.",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", ban_value).execute()

    logger.info("ban %s=%s reason=%s", ban_type, ban_value, reason)
    return {"ok": True, "ban_type": ban_type, "ban_value": ban_value}


def unban_entity(client: Client, *, ban_type: str, ban_value: str) -> dict[str, Any]:
    client.table("security_bans").delete().eq("ban_type", ban_type).eq("ban_value", ban_value).execute()
    if ban_type == "user":
        client.table("profiles").update(
            {"banned": False, "ban_reason": None, "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", ban_value).execute()
    return {"ok": True}


def ack_alert(client: Client, alert_id: str) -> dict[str, Any]:
    client.table("abuse_alerts").update({"acknowledged": True}).eq("id", alert_id).execute()
    return {"ok": True}


def admin_dashboard(client: Client) -> dict[str, Any]:
    alerts = list_alerts(client, limit=20)
    bans = client.table("security_bans").select("*").order("created_at", desc=True).limit(20).execute()
    return {"ok": True, "alerts": alerts, "recent_bans": bans.data or []}

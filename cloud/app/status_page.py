"""Public status page payload (status.example.com / JSON)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from supabase import Client

from cloud.app.config import (
    APP_VERSION,
    PLG_ENV,
    STATUS_PAGE_URL,
    SUPPORT_EMAIL,
    SUPPORT_SLA_HOURS,
    SUPPORT_TELEGRAM,
    SUPPORT_UPDATES_URL,
)
from cloud.app.redis_store import redis_available


def _component_status(client: Client, component: str) -> str:
    if component == "redis":
        return "operational" if redis_available() else "degraded"

    if component == "generation":
        try:
            row = client.table("kill_switch").select("enabled,today_spend_usd,daily_spend_cap_usd").eq("id", 1).single().execute()
            data = row.data or {}
            if not data.get("enabled"):
                return "outage"
            spend = float(data.get("today_spend_usd") or 0)
            cap = float(data.get("daily_spend_cap_usd") or 500)
            if spend >= cap * 0.9:
                return "degraded"
        except Exception:  # noqa: BLE001
            return "degraded"
        return "operational"

    return "operational"


def status_payload(client: Client) -> dict[str, Any]:
    components = [
        {"id": "api", "name": "Cloud API", "status": "operational"},
        {"id": "auth", "name": "Authentication", "status": "operational"},
        {"id": "payments", "name": "Billing", "status": "operational"},
        {"id": "generation", "name": "AI Generation", "status": _component_status(client, "generation")},
        {"id": "redis", "name": "Queue / Rate limits", "status": _component_status(client, "redis")},
    ]

    open_incidents = (
        client.table("status_incidents")
        .select("*")
        .is_("resolved_at", "null")
        .order("started_at", desc=True)
        .limit(10)
        .execute()
    )

    statuses = [c["status"] for c in components]
    if "outage" in statuses:
        overall = "outage"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "operational"

    return {
        "ok": True,
        "overall": overall,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "version": APP_VERSION,
        "env": PLG_ENV,
        "components": components,
        "incidents": open_incidents.data or [],
        "support": {
            "email": SUPPORT_EMAIL,
            "telegram": SUPPORT_TELEGRAM,
            "sla_hours": SUPPORT_SLA_HOURS,
            "updates_url": SUPPORT_UPDATES_URL,
            "status_url": STATUS_PAGE_URL,
        },
    }

"""Extended admin — users, quota, payments, spend."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from supabase import Client

from cloud.app.analytics_ops import admin_metrics


def list_users(client: Client, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    result = (
        client.table("profiles")
        .select("id,status,plan,beats_used,banned,created_at,utm_source,ramp_tier")
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return {"ok": True, "users": result.data or [], "limit": limit, "offset": offset}


def get_user_detail(client: Client, user_id: str) -> dict[str, Any]:
    prof = client.table("profiles").select("*").eq("id", user_id).maybe_single().execute()
    if not prof.data:
        raise HTTPException(404, "User not found.")
    devices = client.table("user_devices").select("*").eq("user_id", user_id).execute()
    payments = (
        client.table("payment_events")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    gens = (
        client.table("generation_logs")
        .select("id,model,cost_usd,created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    spend = sum(float(g.get("cost_usd") or 0) for g in (gens.data or []))
    return {
        "ok": True,
        "profile": prof.data,
        "devices": devices.data or [],
        "payments": payments.data or [],
        "recent_generations": gens.data or [],
        "api_spend_usd": round(spend, 4),
    }


def patch_user_quota(
    client: Client,
    user_id: str,
    *,
    beats_used: int | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    patch: dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if beats_used is not None:
        patch["beats_used"] = beats_used
    if status is not None:
        patch["status"] = status
    client.table("profiles").update(patch).eq("id", user_id).execute()
    return {"ok": True, "user_id": user_id, **patch}


def ops_dashboard(client: Client) -> dict[str, Any]:
    metrics = admin_metrics(client, days=30)
    users_active = (
        client.table("profiles")
        .select("id", count="exact")
        .eq("status", "active")
        .execute()
    )
    feedback = (
        client.table("feedback_submissions")
        .select("id", count="exact")
        .execute()
    )
    return {
        "ok": True,
        "active_subscribers": users_active.count or 0,
        "open_feedback": feedback.count or 0,
        "metrics_30d": metrics,
    }

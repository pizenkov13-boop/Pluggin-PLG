"""Device binding — max N PCs per account."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from supabase import Client

from cloud.app.config import MAX_DEVICES


def register_device(
    client: Client,
    user_id: str,
    device_id: str,
    device_name: str | None = None,
) -> dict:
    device_id = (device_id or "").strip()
    if not device_id or len(device_id) > 128:
        raise HTTPException(400, "Invalid device_id.")

    result = (
        client.table("user_devices")
        .select("*")
        .eq("user_id", user_id)
        .eq("device_id", device_id)
        .maybe_single()
        .execute()
    )
    row = result.data if result else None
    now = datetime.now(timezone.utc).isoformat()
    if row:
        client.table("user_devices").update({"last_seen": now, "device_name": device_name}).eq(
            "id", row["id"]
        ).execute()
        return {"ok": True, "registered": True, "device_id": device_id}

    all_devices = client.table("user_devices").select("id").eq("user_id", user_id).execute()
    count = len(all_devices.data or [])
    if count >= MAX_DEVICES:
        raise HTTPException(
            403,
            f"Device limit reached ({MAX_DEVICES}). Remove an old device or contact support.",
        )

    client.table("user_devices").insert(
        {
            "user_id": user_id,
            "device_id": device_id,
            "device_name": device_name,
            "last_seen": now,
        }
    ).execute()
    return {"ok": True, "registered": True, "device_id": device_id}


def touch_device(client: Client, user_id: str, device_id: str) -> None:
    if not device_id:
        return
    client.table("user_devices").update({"last_seen": datetime.now(timezone.utc).isoformat()}).eq(
        "user_id", user_id
    ).eq("device_id", device_id).execute()

"""Waitlist + invite-only ramp (200 → 2k → 20k → 100k)."""

from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from supabase import Client

from cloud.app.config import INVITE_ONLY, WAITLIST_MODE
from cloud.app.feature_flags import flag_enabled

logger = logging.getLogger("plg.waitlist")

_CODE_ALPHABET = string.ascii_uppercase + string.digits


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def invite_required(client: Client) -> bool:
    if INVITE_ONLY or WAITLIST_MODE:
        return True
    return flag_enabled(client, "waitlist_gate", default=False)


def join_waitlist(client: Client, email: str, ramp_tier: str = "wave_200") -> dict[str, Any]:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email.")

    existing = (
        client.table("waitlist_entries")
        .select("*")
        .eq("email", email)
        .maybe_single()
        .execute()
    )
    if existing.data:
        return {"ok": True, "status": existing.data.get("status"), "already": True}

    client.table("waitlist_entries").insert(
        {"email": email, "ramp_tier": ramp_tier, "status": "pending"}
    ).execute()
    return {"ok": True, "status": "pending", "already": False}


def _code_valid(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    expires = row.get("expires_at")
    if expires:
        exp = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > exp:
            return False
    max_uses = row.get("max_uses")
    uses = int(row.get("uses") or 0)
    if max_uses is not None and uses >= int(max_uses):
        return False
    return True


def validate_invite_code(client: Client, code: str | None) -> dict[str, Any]:
    if not invite_required(client):
        return {"ok": True, "skipped": True}

    code = (code or "").strip().upper()
    if not code:
        raise HTTPException(403, "Invite code required.")

    result = client.table("invite_codes").select("*").eq("code", code).maybe_single().execute()
    if not _code_valid(result.data):
        raise HTTPException(403, "Invalid or expired invite code.")

    return {"ok": True, "ramp_tier": result.data.get("ramp_tier"), "code": code}


def consume_invite_code(client: Client, code: str, user_id: str) -> None:
    code = code.strip().upper()
    row = client.table("invite_codes").select("*").eq("code", code).maybe_single().execute()
    if not row.data:
        return

    uses = int(row.data.get("uses") or 0) + 1
    client.table("invite_codes").update({"uses": uses}).eq("code", code).execute()

    client.table("profiles").update(
        {
            "ramp_tier": row.data.get("ramp_tier"),
            "invite_code": code,
            "updated_at": _utc_now(),
        }
    ).eq("id", user_id).execute()

    client.table("waitlist_entries").update(
        {"status": "activated", "invite_code": code}
    ).eq("invite_code", code).execute()


def generate_invite_codes(
    client: Client,
    *,
    count: int,
    ramp_tier: str,
    max_uses: int | None = 1,
) -> list[str]:
    codes: list[str] = []
    for _ in range(max(1, min(count, 500))):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        client.table("invite_codes").insert(
            {
                "code": code,
                "ramp_tier": ramp_tier,
                "max_uses": max_uses,
            }
        ).execute()
        codes.append(code)
    return codes

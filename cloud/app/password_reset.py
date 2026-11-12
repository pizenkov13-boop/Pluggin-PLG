"""Password reset codes owned by PLG and delivered through Unisender."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from supabase import Client

from cloud.app.config import (
    EMAIL_VERIFICATION_MAX_ATTEMPTS,
    EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS,
    EMAIL_VERIFICATION_SECRET,
    EMAIL_VERIFICATION_TTL_MINUTES,
)
from cloud.app.email import send_password_reset_code


def _hash(user_id: str, code: str) -> str:
    value = f"password-reset:{user_id}:{code}".encode("utf-8")
    return hmac.new(EMAIL_VERIFICATION_SECRET.encode("utf-8"), value, hashlib.sha256).hexdigest()


def _user_id(client: Client, email: str) -> str | None:
    response = client.rpc("auth_user_id_by_email", {"p_email": email.strip().lower()}).execute()
    return str(getattr(response, "data", None) or "").strip() or None


def issue_reset_code(client: Client, *, email: str) -> None:
    user_id = _user_id(client, email)
    # Keep the public response identical for unknown addresses.
    if not user_id:
        return
    row_response = client.table("password_reset_codes").select("last_sent_at").eq("user_id", user_id).maybe_single().execute()
    row = getattr(row_response, "data", None) if row_response else None
    if row:
        last_sent = datetime.fromisoformat(str(row["last_sent_at"]).replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - last_sent).total_seconds() < EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS:
            raise HTTPException(429, "Wait before requesting another reset code.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.now(timezone.utc)
    client.table("password_reset_codes").upsert(
        {"user_id": user_id, "code_hash": _hash(user_id, code), "expires_at": (now + timedelta(minutes=EMAIL_VERIFICATION_TTL_MINUTES)).isoformat(), "attempts": 0, "last_sent_at": now.isoformat()},
        on_conflict="user_id",
    ).execute()
    if not send_password_reset_code(client, to=email.strip().lower(), code=code, user_id=user_id):
        client.table("password_reset_codes").delete().eq("user_id", user_id).execute()
        raise HTTPException(503, "Could not send reset email. Try again shortly.")


def complete_reset(client: Client, *, email: str, code: str, new_password: str) -> dict[str, Any]:
    cleaned = "".join(ch for ch in code if ch.isdigit())
    if len(cleaned) != 6 or len(new_password) < 8:
        raise HTTPException(400, "Enter a 6-digit code and a password of at least 8 characters.")
    user_id = _user_id(client, email)
    if not user_id:
        raise HTTPException(400, "Invalid or expired reset code.")
    response = client.table("password_reset_codes").select("*").eq("user_id", user_id).maybe_single().execute()
    row = getattr(response, "data", None) if response else None
    if not row or datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        raise HTTPException(400, "Invalid or expired reset code.")
    attempts = int(row.get("attempts") or 0)
    if attempts >= EMAIL_VERIFICATION_MAX_ATTEMPTS:
        raise HTTPException(429, "Too many incorrect codes. Request a new one.")
    if not hmac.compare_digest(str(row["code_hash"]), _hash(user_id, cleaned)):
        client.table("password_reset_codes").update({"attempts": attempts + 1}).eq("user_id", user_id).execute()
        raise HTTPException(400, "Invalid or expired reset code.")
    try:
        client.auth.admin.update_user_by_id(user_id, {"password": new_password})
    except Exception as exc:  # noqa: BLE001
        if "different from the old password" in str(exc).lower():
            raise HTTPException(422, "New password must be different from the old password.") from exc
        raise HTTPException(500, "Could not update password.") from exc
    client.table("password_reset_codes").delete().eq("user_id", user_id).execute()
    return {"ok": True, "message": "Password changed. Sign in with your new password."}

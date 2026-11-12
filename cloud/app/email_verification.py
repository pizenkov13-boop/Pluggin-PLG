"""Email verification codes for desktop account creation."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from collections.abc import Callable
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
from cloud.app.email import send_verification_code

logger = logging.getLogger("plg.email_verification")


def _code_hash(user_id: str, code: str) -> str:
    if not EMAIL_VERIFICATION_SECRET:
        raise HTTPException(503, "Email verification is not configured.")
    value = f"{user_id}:{code}".encode("utf-8")
    return hmac.new(EMAIL_VERIFICATION_SECRET.encode("utf-8"), value, hashlib.sha256).hexdigest()


def _user_email(client: Client, user_id: str) -> str:
    try:
        response = client.auth.admin.get_user_by_id(user_id)
        user: Any = getattr(response, "user", None) or (
            response.get("user") if isinstance(response, dict) else None
        )
        email = getattr(user, "email", None) if user else None
        if not email and isinstance(user, dict):
            email = user.get("email")
        return str(email or "").strip().lower()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "Account not found.") from exc


def issue_code(
    client: Client,
    *,
    user_id: str,
    allow_cooldown: bool = False,
    send: bool = True,
) -> Callable[[], None] | None:
    """Write a fresh verification code row. With ``send=True`` (default) the
    email goes out now and a failure raises 503. With ``send=False`` only the
    row is written (fast, local) and a zero-arg sender is returned for the
    caller to run off the request path (e.g. FastAPI BackgroundTasks) — so
    signup doesn't block on the transactional-email API."""
    email = _user_email(client, user_id)
    if not email:
        raise HTTPException(404, "Account not found.")

    existing_response = client.table("email_verification_codes").select("last_sent_at").eq(
        "user_id", user_id
    ).maybe_single().execute()
    existing = getattr(existing_response, "data", None) if existing_response else None
    if existing and not allow_cooldown:
        last_sent = datetime.fromisoformat(str(existing["last_sent_at"]).replace("Z", "+00:00"))
        seconds = (datetime.now(timezone.utc) - last_sent).total_seconds()
        if seconds < EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS:
            wait = max(1, int(EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS - seconds))
            raise HTTPException(429, f"Wait {wait}s before requesting another code.")

    code = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.now(timezone.utc)
    client.table("email_verification_codes").upsert(
        {
            "user_id": user_id,
            "code_hash": _code_hash(user_id, code),
            "expires_at": (now + timedelta(minutes=EMAIL_VERIFICATION_TTL_MINUTES)).isoformat(),
            "attempts": 0,
            "last_sent_at": now.isoformat(),
        },
        on_conflict="user_id",
    ).execute()
    def _deliver() -> None:
        if not send_verification_code(client, to=email, code=code, user_id=user_id):
            logger.warning("verification email send failed user=%s (client can resend)", user_id)

    if not send:
        return _deliver

    if not send_verification_code(client, to=email, code=code, user_id=user_id):
        client.table("email_verification_codes").delete().eq("user_id", user_id).execute()
        raise HTTPException(503, "Could not send verification email. Try again shortly.")
    return None


def verify_code(client: Client, *, user_id: str, code: str) -> dict[str, Any]:
    cleaned = "".join(ch for ch in code if ch.isdigit())
    if len(cleaned) != 6:
        raise HTTPException(400, "Enter the 6-digit code.")
    row_response = client.table("email_verification_codes").select("*").eq(
        "user_id", user_id
    ).maybe_single().execute()
    row = getattr(row_response, "data", None) if row_response else None
    if not row:
        raise HTTPException(400, "Request a new verification code.")
    expires_at = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    if expires_at <= datetime.now(timezone.utc):
        client.table("email_verification_codes").delete().eq("user_id", user_id).execute()
        raise HTTPException(400, "This code expired. Request a new one.")
    attempts = int(row.get("attempts") or 0)
    if attempts >= EMAIL_VERIFICATION_MAX_ATTEMPTS:
        raise HTTPException(429, "Too many incorrect codes. Request a new one.")
    if not hmac.compare_digest(str(row["code_hash"]), _code_hash(user_id, cleaned)):
        client.table("email_verification_codes").update({"attempts": attempts + 1}).eq(
            "user_id", user_id
        ).execute()
        raise HTTPException(400, "Incorrect verification code.")
    try:
        client.auth.admin.update_user_by_id(user_id, {"email_confirm": True})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, "Could not verify email.") from exc
    client.table("email_verification_codes").delete().eq("user_id", user_id).execute()
    return {"ok": True, "message": "Email verified."}

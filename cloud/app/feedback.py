"""In-app feedback + optional log excerpt."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from supabase import Client

from cloud.app.config import APP_VERSION, SUPPORT_EMAIL
from cloud.app.email import send_email
from cloud.app.privacy import redact_sensitive_text

logger = logging.getLogger("plg.feedback")

MAX_MESSAGE = 4000
MAX_LOG = 12000


def submit_feedback(
    client: Client,
    *,
    user_id: str | None,
    email: str | None,
    category: str,
    message: str,
    app_version: str | None = None,
    platform: str | None = None,
    log_excerpt: str | None = None,
) -> dict[str, Any]:
    text = (message or "").strip()
    if not text:
        raise HTTPException(400, "Message required.")
    if len(text) > MAX_MESSAGE:
        raise HTTPException(400, f"Message too long (max {MAX_MESSAGE}).")

    log = redact_sensitive_text(log_excerpt, max_chars=MAX_LOG) if log_excerpt else None

    client.table("feedback_submissions").insert(
        {
            "user_id": user_id,
            "email": email,
            "category": (category or "general")[:64],
            "message": text,
            "app_version": app_version or APP_VERSION,
            "platform": platform,
            "log_excerpt": log,
        }
    ).execute()

    if SUPPORT_EMAIL:
        body = f"Category: {category}\nUser: {user_id or 'anon'}\nEmail: {email or 'n/a'}\n\n{text}"
        if log:
            body += f"\n\n--- log ---\n{log[:4000]}"
        send_email(
            client,
            to=SUPPORT_EMAIL,
            subject=f"[PLG Feedback] {category}",
            body=body,
            template="feedback",
            user_id=user_id,
        )

    logger.info("feedback user=%s category=%s", user_id, category)
    return {"ok": True, "message": "Thanks — we reply within 24–48 hours."}

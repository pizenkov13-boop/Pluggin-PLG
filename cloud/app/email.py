"""Transactional email — Unisender Go API + notification log."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from supabase import Client

from cloud.app.config import (
    EMAIL_FROM,
    UNISENDER_API_KEY,
    UNISENDER_PASSWORD_RESET_TEMPLATE_ID,
    UNISENDER_VERIFICATION_TEMPLATE_ID,
    SUPPORT_EMAIL,
)

logger = logging.getLogger("plg.email")

UNISENDER_EMAIL_URL = "https://goapi.unisender.ru/ru/transactional/api/v1/email/send.json"

TEMPLATES: dict[str, str] = {
    "payment_success": "Pluggin' — payment received. Your subscription is active for 30 days.",
    "subscription_expiring": "Pluggin' — subscription ends in {days} days. Renew in Settings to unlock more generations.",
    "quota_monthly_limit": "Pluggin' — monthly generation limit reached. Resets in {days} days or upgrade.",
    "welcome": "Welcome to Pluggin' — describe a beat and open FL Studio.",
}


def _sender_parts() -> tuple[str, str]:
    """Return Unisender's separate sender name and address fields."""
    if "<" in EMAIL_FROM and EMAIL_FROM.endswith(">"):
        name, address = EMAIL_FROM.rsplit("<", 1)
        return name.strip(), address[:-1].strip()
    return "Pluggin'", EMAIL_FROM.strip()


def _send_unisender(
    *,
    to: str,
    subject: str,
    text: str,
    template_id: str | None = None,
    substitutions: dict[str, str] | None = None,
) -> httpx.Response:
    from_name, from_email = _sender_parts()
    message: dict[str, Any] = {
        "recipients": [{"email": to}],
        "subject": subject,
        "from_email": from_email,
        "from_name": from_name,
        "body": {"plaintext": text},
        "template_engine": "simple",
        "global_language": "en",
    }
    if template_id:
        message["template_id"] = template_id
        message["global_substitutions"] = substitutions or {}
    return httpx.post(
        UNISENDER_EMAIL_URL,
        headers={
            "X-API-KEY": UNISENDER_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={"message": message},
        timeout=20.0,
    )


def _log_notification(
    client: Client,
    *,
    user_id: str | None,
    template: str,
    recipient: str,
    status: str,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        client.table("notification_log").insert(
            {
                "user_id": user_id,
                "channel": "email",
                "template": template,
                "recipient": recipient,
                "status": status,
                "error": error,
                "metadata": metadata or {},
            }
        ).execute()
    except Exception:  # noqa: BLE001
        logger.warning("notification_log insert failed")


def send_email(
    client: Client,
    *,
    to: str,
    subject: str,
    body: str,
    template: str,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    if not to or "@" not in to:
        return False

    if not UNISENDER_API_KEY:
        logger.info("email skipped (no UNISENDER_API_KEY) to=%s template=%s", to, template)
        _log_notification(
            client,
            user_id=user_id,
            template=template,
            recipient=to,
            status="queued",
            metadata=metadata,
        )
        return False

    try:
        resp = _send_unisender(to=to, subject=subject, text=body)
        if resp.status_code >= 400:
            _log_notification(
                client,
                user_id=user_id,
                template=template,
                recipient=to,
                status="failed",
                error=resp.text,
                metadata=metadata,
            )
            return False
        _log_notification(
            client,
            user_id=user_id,
            template=template,
            recipient=to,
            status="sent",
            metadata=metadata,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        _log_notification(
            client,
            user_id=user_id,
            template=template,
            recipient=to,
            status="failed",
            error=str(exc),
            metadata=metadata,
        )
        return False


def send_verification_code(client: Client, *, to: str, code: str, user_id: str) -> bool:
    """Send a code through the published Unisender template; never log the code."""
    if not UNISENDER_API_KEY or not UNISENDER_VERIFICATION_TEMPLATE_ID:
        logger.error("verification email is not configured")
        return False
    try:
        resp = _send_unisender(
            to=to,
            subject="Your PLG verification code",
            text="Your PLG verification code is {{code}}.",
            template_id=UNISENDER_VERIFICATION_TEMPLATE_ID,
            substitutions={"code": code},
        )
        if resp.status_code >= 400:
            _log_notification(client, user_id=user_id, template="email_verification", recipient=to, status="failed", error=resp.text)
            return False
        _log_notification(client, user_id=user_id, template="email_verification", recipient=to, status="sent")
        return True
    except Exception as exc:  # noqa: BLE001
        _log_notification(client, user_id=user_id, template="email_verification", recipient=to, status="failed", error=str(exc))
        return False


def send_password_reset_code(client: Client, *, to: str, code: str, user_id: str) -> bool:
    """Send a reset OTP through the dedicated Unisender transactional template."""
    if not UNISENDER_API_KEY or not UNISENDER_PASSWORD_RESET_TEMPLATE_ID:
        logger.error("password-reset email is not configured")
        return False
    try:
        resp = _send_unisender(
            to=to,
            subject="PLG Password Reset",
            text="Enter this 6-digit code in the PLG app: {{code}}",
            template_id=UNISENDER_PASSWORD_RESET_TEMPLATE_ID,
            substitutions={"code": code},
        )
        if resp.status_code >= 400:
            _log_notification(client, user_id=user_id, template="password_reset", recipient=to, status="failed", error=resp.text)
            return False
        _log_notification(client, user_id=user_id, template="password_reset", recipient=to, status="sent")
        return True
    except Exception as exc:  # noqa: BLE001
        _log_notification(client, user_id=user_id, template="password_reset", recipient=to, status="failed", error=str(exc))
        return False

def notify_payment_success(client: Client, user_id: str, email: str) -> None:
    send_email(
        client,
        to=email,
        subject="Pluggin' — subscription active",
        body=TEMPLATES["payment_success"],
        template="payment_success",
        user_id=user_id,
    )


def notify_subscription_expiring(client: Client, user_id: str, email: str, *, days: int) -> bool:
    body = TEMPLATES["subscription_expiring"].format(days=days)
    return send_email(
        client,
        to=email,
        subject="Pluggin' — subscription ending soon",
        body=body,
        template="subscription_expiring",
        user_id=user_id,
        metadata={"days": days},
    )


def notify_quota_limit(client: Client, user_id: str, email: str, *, days: int = 0) -> bool:
    body = TEMPLATES["quota_monthly_limit"].format(days=days)
    return send_email(
        client,
        to=email,
        subject="Pluggin' — generation limit reached",
        body=body,
        template="quota_monthly_limit",
        user_id=user_id,
    )

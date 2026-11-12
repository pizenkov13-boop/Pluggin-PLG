"""Legal constants, GDPR deletion, log retention."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from supabase import Client

from cloud.app.config import (
    BEAT_LIMIT,
    LEGAL_AGE_MIN,
    LEGAL_PRIVACY_VERSION,
    LEGAL_TERMS_VERSION,
    PAYMENT_LOG_RETENTION_DAYS,
    PERIOD_DAYS,
    PRICE_CIS_RUB,
    PROMPT_LOG_RETENTION_DAYS,
    DATA_REGION,
    SUPPORT_EMAIL,
    TRIAL_BEATS,
)

logger = logging.getLogger("plg.legal")

LEGAL_DOCS = {
    "terms_version": LEGAL_TERMS_VERSION,
    "privacy_version": LEGAL_PRIVACY_VERSION,
    "min_age": LEGAL_AGE_MIN,
    "data_region": DATA_REGION,
    "support_email": SUPPORT_EMAIL,
    "prompt_log_retention_days": PROMPT_LOG_RETENTION_DAYS,
    "payment_log_retention_days": PAYMENT_LOG_RETENTION_DAYS,
}


def legal_snapshot() -> dict[str, Any]:
    return {
        "ok": True,
        **LEGAL_DOCS,
        "documents": {
            "terms": "legal/TERMS_RU.md",
            "privacy": "legal/PRIVACY_RU.md",
            "payment": "legal/PAYMENT_TERMS_RU.md",
        },
        "disclaimers": {
            "ai_generated": (
                "Beats are AI-assisted. Pluggin' is a tool, not a co-author. "
                "You are responsible for how you use and release outputs."
            ),
            "sample_licenses": (
                "Imported kits and samples remain your responsibility. "
                "Ensure you have rights to use sounds in your projects."
            ),
            "beat_ownership": (
                "You own all beats and session files generated for your account. "
                "Pluggin' claims no royalties, revenue share, or co-authorship. "
                "Subject to third-party sample licenses you import."
            ),
            "similarity": (
                "AI output may resemble existing tracks. Pluggin' does not guarantee "
                "uniqueness and is not liable for similarity claims, takedowns, or "
                "platform enforcement. Clear releases yourself before commercial use."
            ),
            "data_processing": (
                "We store sanitized beat metadata (patterns without disk paths, ratings, "
                "prompt hash — not full prompt text) to personalize and improve the service. "
                "Audio and .flp files stay on your device. See Privacy Policy."
            ),
        },
        "subscription": {
            "cancel_anytime": True,
            "refund_via_support": True,
            "auto_refund": False,
            "auto_renewal": False,
            "trial_beats": TRIAL_BEATS,
            "beat_limit": BEAT_LIMIT,
            "period_days": PERIOD_DAYS,
            "price_cis_rub": PRICE_CIS_RUB,
        },
    }


def record_acceptance(
    client: Client,
    user_id: str,
    *,
    terms_version: str,
    privacy_version: str,
    ip: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    client.table("profiles").update(
        {
            "terms_version": terms_version,
            "terms_accepted_at": now,
            "privacy_version": privacy_version,
            "age_confirmed_at": now,
            "updated_at": now,
        }
    ).eq("id", user_id).execute()

    rows = [
        {"user_id": user_id, "doc_type": "terms", "doc_version": terms_version, "ip_address": ip},
        {"user_id": user_id, "doc_type": "privacy", "doc_version": privacy_version, "ip_address": ip},
        {"user_id": user_id, "doc_type": "age", "doc_version": str(LEGAL_AGE_MIN), "ip_address": ip},
    ]
    client.table("legal_acceptances").insert(rows).execute()


def require_terms_acceptance(accept_terms: bool, confirm_age: bool) -> None:
    if not accept_terms:
        raise HTTPException(400, "You must accept the Terms of Service and Privacy Policy.")
    if not confirm_age:
        raise HTTPException(400, f"You must confirm you are at least {LEGAL_AGE_MIN} years old.")


def require_payment_terms_acceptance(accepted: bool) -> None:
    """Keep checkout consent enforced on the server, not only in the desktop UI."""
    if not accepted:
        raise HTTPException(400, "Подтвердите согласие с Офертой, Политикой конфиденциальности и Условиями оплаты.")


def delete_user_account(client: Client, user_id: str) -> dict[str, Any]:
    """GDPR erasure — delete PII; anonymize payment records (kept 5 years, see purge_payment_events)."""
    email = _user_email(client, user_id)
    now = datetime.now(timezone.utc).isoformat()
    client.table("profiles").update({"deletion_requested_at": now}).eq("id", user_id).execute()

    client.table("generation_logs").delete().eq("user_id", user_id).execute()
    for table in (
        "beat_events",
        "beat_ratings",
        "beat_patterns",
        "beats",
        "feedback_submissions",
        "analytics_events",
        "notification_log",
        "email_verification_codes",
        "password_reset_codes",
    ):
        try:
            client.table(table).delete().eq("user_id", user_id).execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("account delete: %s cleanup skipped (%s)", table, exc)
    client.table("user_devices").delete().eq("user_id", user_id).execute()
    client.table("legal_acceptances").delete().eq("user_id", user_id).execute()
    # Keep only an aggregate abuse record; IP/device details are personal data.
    client.table("abuse_alerts").update(
        {"user_id": None, "ip_address": None, "details": {"account_deleted": True}}
    ).eq("user_id", user_id).execute()
    # Must run before auth delete_user: the FK is ON DELETE SET NULL, so once
    # the auth row is gone these rows can no longer be found by user_id.
    _anonymize_payment_events(client, user_id, email, now)

    if email:
        client.table("waitlist_entries").delete().eq("email", email.lower()).execute()

    client.table("profiles").delete().eq("id", user_id).execute()

    try:
        client.auth.admin.delete_user(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("auth delete failed type=%s", type(exc).__name__)
        raise HTTPException(500, "Account deletion failed. Contact support.") from exc

    logger.info("account deleted user=%s", user_id)
    return {"ok": True, "message": "Account and personal data deleted."}


def _anonymize_payment_events(client: Client, user_id: str, email: str, now: str) -> None:
    """Keep payment rows for accounting, minus anything that points at the person.

    The stored webhook body carries the user UUID in object.metadata.user_id;
    amount, status, dates, payment ids and price_tier stay untouched.
    anonymized_at starts the retention clock used by purge_payment_events().
    """
    rows = client.table("payment_events").select("id,payload").eq("user_id", user_id).execute()
    identifiers = {user_id}
    if email:
        identifiers.update({email, email.lower()})
    for row in rows.data or []:
        client.table("payment_events").update(
            {
                "user_id": None,
                "payload": _scrub_identifiers(row.get("payload") or {}, identifiers),
                "anonymized_at": now,
            }
        ).eq("id", row["id"]).execute()


def _scrub_identifiers(value: Any, identifiers: set[str]) -> Any:
    """Drop metadata.user_id and blank any string equal to the user's UUID/email."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "user_id" and item in identifiers:
                continue
            out[key] = _scrub_identifiers(item, identifiers)
        return out
    if isinstance(value, list):
        return [_scrub_identifiers(item, identifiers) for item in value]
    if isinstance(value, str) and value in identifiers:
        return None
    return value


def _user_email(client: Client, user_id: str) -> str:
    """Return the receipt email when PostgREST is used as the service URL.

    In production the API talks to ``supabase-rest`` directly.  That endpoint
    intentionally does not proxy GoTrue, so ``client.auth.admin`` cannot reach
    ``/auth/v1`` there.  Fall back to the internal GoTrue container instead.
    """
    try:
        resp = client.auth.admin.get_user_by_id(user_id)
        user = getattr(resp, "user", None) or (resp.get("user") if isinstance(resp, dict) else None)
        if user:
            return str(getattr(user, "email", None) or user.get("email") or "")
    except Exception:  # noqa: BLE001
        pass

    try:
        import httpx

        from cloud.app.config import SUPABASE_SERVICE_KEY

        response = httpx.get(
            f"http://supabase-auth:9999/admin/users/{user_id}",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            },
            timeout=8.0,
        )
        data = response.json() if response.is_success else {}
        return str(data.get("email") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def purge_generation_logs(client: Client) -> dict[str, Any]:
    """Delete generation metadata older than retention window (no prompt text stored)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=PROMPT_LOG_RETENTION_DAYS)
    cutoff_iso = cutoff.isoformat()

    old = client.table("generation_logs").select("id").lt("created_at", cutoff_iso).execute()
    ids = [row["id"] for row in (old.data or [])]
    if not ids:
        return {"ok": True, "purged": 0}

    for batch_start in range(0, len(ids), 100):
        batch = ids[batch_start : batch_start + 100]
        client.table("generation_logs").delete().in_("id", batch).execute()

    logger.info("purged %s generation_logs older than %s days", len(ids), PROMPT_LOG_RETENTION_DAYS)
    return {"ok": True, "purged": len(ids), "retention_days": PROMPT_LOG_RETENTION_DAYS}


def purge_payment_events(client: Client, *, now: datetime | None = None) -> dict[str, Any]:
    """Destroy payment rows of deleted accounts once the retention term is over.

    privacy.html §2: payment data is kept for 5 years after the account is
    deleted. Both clocks must have run out: anonymized_at (account deletion)
    and the payment's own reporting year, so a payment made just before
    deletion still outlives its accounting year by the full term. Rows of
    active accounts (anonymized_at NULL) are never touched.
    """
    now = now or datetime.now(timezone.utc)
    deleted_before = now - timedelta(days=PAYMENT_LOG_RETENTION_DAYS)
    retention_years = PAYMENT_LOG_RETENTION_DAYS // 365
    paid_before = datetime(now.year - retention_years, 1, 1, tzinfo=timezone.utc)

    old = (
        client.table("payment_events")
        .select("id")
        .lt("anonymized_at", deleted_before.isoformat())
        .lt("processed_at", paid_before.isoformat())
        .execute()
    )
    ids = [row["id"] for row in (old.data or [])]
    if not ids:
        return {"ok": True, "purged": 0, "retention_days": PAYMENT_LOG_RETENTION_DAYS}

    purged = 0
    for batch_start in range(0, len(ids), 100):
        batch = ids[batch_start : batch_start + 100]
        try:
            client.table("payment_events").delete().in_("id", batch).execute()
            purged += len(batch)
        except Exception as exc:  # noqa: BLE001
            # A leftover FK (generation_logs / profiles.current_payment_event_id)
            # blocks the batch; retry tomorrow rather than abort the whole run.
            logger.warning("payment_events purge batch skipped (%s)", type(exc).__name__)

    logger.info(
        "purged %s payment_events of accounts deleted over %s days ago",
        purged,
        PAYMENT_LOG_RETENTION_DAYS,
    )
    return {"ok": True, "purged": purged, "retention_days": PAYMENT_LOG_RETENTION_DAYS}

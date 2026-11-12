"""Server-proxied signup with CAPTCHA + honeypot + device trial claim."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from supabase import Client

from cloud.app.captcha import verify_captcha
from cloud.app.email_verification import issue_code
from cloud.app.security import block_trial_farming, check_honeypot, enforce_not_banned

logger = logging.getLogger("plg.signup")


async def signup_user(
    client: Client,
    *,
    email: str,
    password: str,
    device_id: str | None,
    captcha_token: str | None,
    honeypot: str | None,
    remote_ip: str | None,
    background_tasks: BackgroundTasks | None = None,
    accept_terms: bool = False,
    confirm_age: bool = False,
    utm_source: str | None = None,
    utm_medium: str | None = None,
    utm_campaign: str | None = None,
    utm_content: str | None = None,
    referrer: str | None = None,
) -> dict[str, Any]:
    check_honeypot(honeypot)
    await verify_captcha(captcha_token, remote_ip)

    from cloud.app.legal import require_terms_acceptance

    require_terms_acceptance(accept_terms, confirm_age)

    email = email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email.")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    if device_id:
        enforce_not_banned(client, user_id="", device_id=device_id, ip=remote_ip)

    try:
        result = client.auth.admin.create_user(
            {
                "email": email,
                "password": password,
                "email_confirm": False,
            }
        )
    except Exception as exc:
        msg = str(exc)
        if "already" in msg.lower():
            raise HTTPException(409, "Email already registered.") from exc
        logger.error("signup failed type=%s", type(exc).__name__)
        raise HTTPException(500, "Signup failed.") from exc

    user = result.user
    if not user or not user.id:
        raise HTTPException(500, "Signup failed — no user returned.")

    user_id = str(user.id)

    # Everything past this point provisions a real auth user (invite burn, trial
    # claim, consent record, verification code). If ANY of it fails we must not
    # leave a half-provisioned orphan behind — an account the user can't verify,
    # plus a possibly half-claimed device — so tear the user down and surface a
    # clean error. This is the cleanup the mail-delivery path already did,
    # widened to the whole block after three uncaught 500s left orphans.
    try:
        block_trial_farming(client, user_id, device_id)

        from cloud.app.legal import record_acceptance
        from cloud.app.config import LEGAL_PRIVACY_VERSION, LEGAL_TERMS_VERSION

        record_acceptance(
            client,
            user_id,
            terms_version=LEGAL_TERMS_VERSION,
            privacy_version=LEGAL_PRIVACY_VERSION,
            ip=remote_ip,
        )

        # The code row is written now (fast, local) so the response can truthfully
        # say "code sent"; the transactional-email API call and analytics writes
        # run after the response instead of blocking signup on them.
        deliver_code = issue_code(client, user_id=user_id, allow_cooldown=True, send=False)

        def _post_response() -> None:
            from cloud.app.analytics_ops import save_attribution, track_event

            try:
                save_attribution(
                    client,
                    user_id,
                    utm_source=utm_source,
                    utm_medium=utm_medium,
                    utm_campaign=utm_campaign,
                    utm_content=utm_content,
                    referrer=referrer,
                )
                track_event(
                    client,
                    "signup",
                    user_id=user_id,
                    properties={"utm_source": utm_source, "utm_campaign": utm_campaign},
                )
            except Exception:  # noqa: BLE001
                logger.warning("signup analytics failed user=%s", user_id)
            if deliver_code is not None:
                deliver_code()

        if background_tasks is not None:
            background_tasks.add_task(_post_response)
        else:
            _post_response()
    except Exception as exc:  # noqa: BLE001
        try:
            client.auth.admin.delete_user(user_id)
        except Exception:  # noqa: BLE001
            logger.error(
                "failed to remove half-provisioned account type=%s",
                type(exc).__name__,
            )
        if isinstance(exc, HTTPException):
            raise
        logger.error("signup provisioning failed type=%s", type(exc).__name__)
        raise HTTPException(500, "Signup failed.") from exc

    return {
        "ok": True,
        "message": "Verification code sent.",
        "verification_required": True,
        "verification_user_id": user_id,
    }

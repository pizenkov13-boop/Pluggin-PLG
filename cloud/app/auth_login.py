"""Server-proxied password login with per-account + per-IP lockout.

Login used to go straight from the client to GoTrue
(`{SUPABASE_URL}/auth/v1/token?grant_type=password`), so PLG had no seam to
rate-limit password guessing — `rate_limit.py` never saw a login. This routes
it through the API instead: check the lockout, call GoTrue server-side, count
the failure (or clear the account counter on success).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import HTTPException

from cloud.app.config import SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL
from cloud.app.rate_limit import (
    check_login_limit,
    clear_login_failures,
    register_login_failure,
)

logger = logging.getLogger("plg.auth_login")

# GoTrue returns the same 400 for "wrong password" and "no such user"; the UI
# localizes this single string either way. Kept deliberately vague — telling a
# guesser which half was wrong is a free oracle.
_GENERIC = "Invalid email or password."


def _gotrue_apikey() -> str:
    # Either key is accepted by GoTrue as the `apikey` header and neither
    # weakens the password check. Prefer the anon key when the operator set it;
    # the service key is always present as a fallback.
    return SUPABASE_ANON_KEY or SUPABASE_SERVICE_KEY


def login_user(*, email: str, password: str, remote_ip: str | None) -> dict[str, Any]:
    email = (email or "").strip().lower()
    if not email or "@" not in email or not password:
        raise HTTPException(400, "Email and password are required.")
    if not SUPABASE_URL:
        raise HTTPException(500, "Auth is not configured.")

    # Lockout is checked BEFORE spending an upstream call.
    check_login_limit(email=email, ip=remote_ip)

    apikey = _gotrue_apikey()
    try:
        with httpx.Client(timeout=20.0, trust_env=False) as client:
            resp = client.post(
                f"{SUPABASE_URL}/auth/v1/token",
                params={"grant_type": "password"},
                headers={
                    "apikey": apikey,
                    "Authorization": f"Bearer {apikey}",
                    "Content-Type": "application/json",
                },
                json={"email": email, "password": password},
            )
    except httpx.HTTPError as exc:
        # Upstream unreachable is not a bad guess — don't count it toward lockout.
        logger.warning("login upstream unreachable type=%s", type(exc).__name__)
        raise HTTPException(502, "Auth service is unreachable. Try again.") from exc

    if resp.status_code == 200:
        clear_login_failures(email=email)
        return {"ok": True, "session": resp.json()}

    # "email not confirmed" is a real state the user must act on, not a wrong
    # guess — surface it distinctly and don't count it toward the lockout.
    error_code = ""
    try:
        error_code = str(resp.json().get("error_code") or "")
    except Exception:  # noqa: BLE001
        pass
    # Login gates on the password only — there is no email-confirmation step on
    # the sign-in screen. With GoTrue email-autoconfirm on (Supabase stack .env:
    # ENABLE_EMAIL_AUTOCONFIRM=true) this branch is never hit; if autoconfirm is
    # off, GoTrue only emits email_not_confirmed *after* a correct password, so
    # don't count it as a failed guess and don't show a code-entry prompt.
    if error_code == "email_not_confirmed":
        raise HTTPException(401, _GENERIC)

    if resp.status_code in (400, 401):
        register_login_failure(email=email, ip=remote_ip)
        raise HTTPException(401, _GENERIC)

    logger.warning("login upstream status=%s", resp.status_code)
    raise HTTPException(502, "Auth service error. Try again.")

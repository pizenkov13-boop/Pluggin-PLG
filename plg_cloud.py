"""Desktop client for PLG Cloud API + Supabase Auth."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from app_config import app_version, offline_dev
from plg_device import get_device_id, get_device_name
from plg_net import make_client
from plg_session_store import clear_session, load_session, save_session

logger = logging.getLogger("plg.cloud")

_SESSION_SKEW_S = 90
_device_registered = False
_region_cache: dict[str, str] | None = None
_cloud_probe_failures = 0
_cloud_probe_last_failure_at = 0.0
_CLOUD_PROBE_ATTEMPTS = 2  # UI connectivity check — 2 quick tries, not 3
_CLOUD_PROBE_FAILURES_BEFORE_OFFLINE = 3
# Failed probe rounds only add up while they are recent: a blip an hour ago plus
# one now is not "three failures in a row".
_CLOUD_PROBE_FAILURE_WINDOW_S = 300.0
# Establishing the connection is bounded tightly; the read side keeps the long
# limit because the server may legitimately take a while to answer a generation.
_CLOUD_GENERATE_CONNECT_TIMEOUT_S = 10.0
_CLOUD_GENERATE_READ_TIMEOUT_S = 180.0


def _region_auto_enabled() -> bool:
    raw = (os.getenv("PLG_REGION_AUTO") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def bootstrap_region(*, force: bool = False) -> dict[str, Any]:
    """Resolve CIS vs intl endpoints from PLG_REGION_BOOTSTRAP_URL (/v1/region)."""
    global _region_cache
    if not _region_auto_enabled():
        return {"ok": True, "skipped": True}
    if _region_cache and not force:
        return {"ok": True, "region": _region_cache.get("region"), "cached": True}

    bootstrap = (
        os.getenv("PLG_REGION_BOOTSTRAP_URL")
        or os.getenv("PLG_CLOUD_URL")
        or "http://127.0.0.1:8787"
    ).rstrip("/")
    try:
        import locale as _locale

        lang = os.getenv("PLG_UI_LOCALE") or _locale.getdefaultlocale()[0] or "en-US"
        with make_client(timeout=6.0) as client:
            resp = client.get(
                f"{bootstrap}/v1/region",
                headers={"Accept-Language": lang},
            )
        if resp.status_code >= 400:
            return {"ok": False, "error": f"Region bootstrap HTTP {resp.status_code}"}
        data = resp.json()
        if not data.get("ok"):
            return {"ok": False, "error": data.get("error") or "Region bootstrap failed."}
        _region_cache = {
            "region": str(data.get("region") or "cis"),
            "supabase_url": str(data.get("supabase_url") or "").rstrip("/"),
            "supabase_anon_key": str(data.get("supabase_anon_key") or "").strip(),
            "cloud_url": str(data.get("cloud_url") or "").rstrip("/"),
        }
        stored_region = str(load_session().get("region") or "")
        if stored_region and stored_region != _region_cache["region"]:
            clear_session()
            _device_registered = False
        return {"ok": True, "region": _region_cache["region"]}
    except Exception as exc:  # noqa: BLE001
        logger.debug("region bootstrap skipped: %s", exc)
        return {"ok": False, "error": str(exc)}


def is_cloud_mode() -> bool:
    if offline_dev():  # PLG_OFFLINE_DEV: local mode, never touches the server
        return False
    raw = os.getenv("PLG_CLOUD_MODE", "").strip().lower()
    return raw in ("1", "true", "yes")


def cloud_api_url() -> str:
    # A user/release configuration is explicit and must not be silently
    # replaced by a stale value returned from region bootstrap.
    configured = (os.getenv("PLG_CLOUD_URL") or "").rstrip("/")
    if configured:
        return configured
    bootstrap_region()
    if _region_cache and _region_cache.get("cloud_url"):
        return _region_cache["cloud_url"]
    return "http://127.0.0.1:8787"


def _cloud_probe_url() -> str:
    """Return a configured endpoint without making region bootstrap part of a health probe."""
    configured = (os.getenv("PLG_CLOUD_URL") or "").rstrip("/")
    if configured:
        return configured
    if _region_cache and _region_cache.get("cloud_url"):
        return _region_cache["cloud_url"]
    return "http://127.0.0.1:8787"


def _format_cloud_http_error(exc: Exception, *, path: str = "") -> dict[str, Any]:
    """Turn httpx/network failures into actionable UI messages."""
    url = cloud_api_url()
    target = f"{url}{path}" if path else url
    text = str(exc).strip() or exc.__class__.__name__
    lower = text.lower()

    if "getaddrinfo failed" in lower or "name or service not known" in lower:
        hint = (
            f"Cannot reach PLG Cloud at {url} (DNS lookup failed).\n\n"
            "For local dev:\n"
            "1. Terminal: python cloud\\run.py\n"
            "2. In .env set PLG_CLOUD_URL=http://127.0.0.1:8787\n"
            "3. Restart PLG\n\n"
            "For release: deploy the cloud API first, then point PLG_CLOUD_URL "
            "to your live host."
        )
        return {"ok": False, "error": hint, "error_type": "network"}

    if "connection refused" in lower or "actively refused" in lower:
        return {
            "ok": False,
            "error": (
                f"PLG Cloud is not running at {url}.\n\n"
                "Start it: python cloud\\run.py\n"
                "Then click Create beat again."
            ),
            "error_type": "network",
        }

    if "timed out" in lower or "timeout" in lower:
        return {
            "ok": False,
            "error": (
                "PLG Cloud did not respond in time.\n\n"
                f"Server: {target}\n\n"
                "Gemini can take 1–3 minutes when busy. Keep cloud\\run.py open "
                "and try again."
            ),
            "error_type": "network",
        }

    # Every caller passes an exception raised while sending/receiving the
    # request (never an HTTP status), so the server did not answer at all:
    # that is a network failure, distinct from "cloud" = the server answered
    # with an error.
    return {"ok": False, "error": f"Cloud request failed ({target}): {text}", "error_type": "network"}


def supabase_url() -> str:
    bootstrap_region()
    if _region_cache and _region_cache.get("supabase_url"):
        return _region_cache["supabase_url"]
    return (os.getenv("SUPABASE_URL") or "").rstrip("/")


def supabase_anon_key() -> str:
    bootstrap_region()
    if _region_cache and _region_cache.get("supabase_anon_key"):
        return _region_cache["supabase_anon_key"]
    return (
        os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY") or ""
    ).strip()


def _auth_headers() -> dict[str, str]:
    session = load_session()
    token = str(session.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Not signed in.")
    return {
        "Authorization": f"Bearer {token}",
        "X-PLG-Version": app_version(),
        "X-PLG-Device": get_device_id(),
    }


def _api_headers() -> dict[str, str]:
    return {**_auth_headers(), "Content-Type": "application/json"}


def _supabase_headers() -> dict[str, str]:
    key = supabase_anon_key()
    if not key:
        raise RuntimeError("SUPABASE_ANON_KEY not configured.")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _store_auth_response(data: dict[str, Any]) -> dict[str, Any]:
    expires_in = data.get("expires_in")
    expires_at: float | None = None
    if expires_in is not None:
        try:
            expires_at = time.time() + float(expires_in)
        except (TypeError, ValueError):
            expires_at = None
    # A token-refresh grant response is not guaranteed to include a "user"
    # object the way a login/signup response does — GoTrue configs vary. If
    # this call is a refresh and omits it, fall back to what was already on
    # disk instead of overwriting a known identity with None: that used to
    # make Account silently lose the signed-in user's email (still fully
    # authenticated, session still valid) until the next full login.
    previous = load_session()
    user = data.get("user") or previous.get("user") or {}
    email = (data.get("user") or {}).get("email") or previous.get("email") or user.get("email")
    session = {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in"),
        "expires_at": expires_at,
        "user": user,
        "email": email,
        "region": (_region_cache or {}).get("region"),
    }
    save_session(session)
    return session


def _needs_refresh(session: dict[str, Any]) -> bool:
    token = str(session.get("access_token") or "").strip()
    refresh = str(session.get("refresh_token") or "").strip()
    if not token:
        return bool(refresh)
    expires_at = session.get("expires_at")
    if expires_at is None:
        return False
    try:
        return time.time() >= float(expires_at) - _SESSION_SKEW_S
    except (TypeError, ValueError):
        return False


def ensure_session(*, validate: bool = False) -> dict[str, Any]:
    """Restore session from disk; refresh expired tokens; optionally validate with /v1/me."""
    global _device_registered

    session = load_session()
    if not session.get("access_token") and not session.get("refresh_token"):
        return {"ok": True, "signed_in": False}

    did_refresh = False
    if _needs_refresh(session):
        refreshed = refresh_session()
        did_refresh = bool(refreshed.get("ok"))
        if not did_refresh:
            if refreshed.get("error_type") in ("network", "cloud"):
                # The token could not be renewed because the server could not be
                # reached, not because it was refused: stay signed in, offline.
                return {"ok": True, "signed_in": True, "offline": True}
            return {"ok": True, "signed_in": False}
        session = load_session()

    token = str(session.get("access_token") or "").strip()
    if not token:
        return {"ok": True, "signed_in": False}

    if not validate:
        return {"ok": True, "signed_in": True}

    me = fetch_me()
    if me.get("error_type") == "network":
        return {"ok": True, "signed_in": True, "offline": True}
    if me.get("ok"):
        if did_refresh or not _device_registered:
            reg = register_device()
            if reg.get("ok"):
                _device_registered = True
            else:
                logger.warning("device register on session restore: %s", reg.get("error"))
        return {"ok": True, "signed_in": True}

    if me.get("error_type") == "auth":
        clear_session()
        _device_registered = False
        return {"ok": True, "signed_in": False}

    return {"ok": True, "signed_in": True}


def signup(
    email: str,
    password: str,
    captcha_token: str | None = None,
    accept_terms: bool = False,
    confirm_age: bool = False,
) -> dict[str, Any]:
    global _device_registered
    base = cloud_api_url()
    if not base:
        return {"ok": False, "error": "PLG_CLOUD_URL not configured.", "error_type": "config"}

    with make_client(timeout=30.0) as client:
        resp = client.post(
            f"{base}/v1/auth/signup",
            headers={"X-PLG-Version": app_version(), "Content-Type": "application/json"},
            json={
                "email": email.strip(),
                "password": password,
                "device_id": get_device_id(),
                "captcha_token": captcha_token,
                "website": "",
                "accept_terms": accept_terms,
                "confirm_age": confirm_age,
            },
        )
        data = resp.json()
        if resp.status_code >= 400:
            detail = data.get("detail") if isinstance(data.get("detail"), str) else resp.text
            # 409 = email already registered, 429 = signup rate-limited by IP
            # (see cloud/app/rate_limit.check_signup_limit) → stable codes the
            # UI localizes (client shows a translated string, not this raw
            # English detail).
            if resp.status_code == 409:
                err_type = "email_taken"
            elif resp.status_code == 429:
                err_type = "rate_limit"
            else:
                err_type = "auth"
            return {"ok": False, "error": detail, "error_type": err_type}

        if data.get("verification_required"):
            # A previous user can still have a valid session on this device.
            # Starting a new signup must not leave that session active, otherwise
            # the UI treats the response as an authenticated login and skips the
            # verification-code step.
            clear_session()
            _device_registered = False
            return {
                "ok": True,
                "message": data.get("message", "Verification code sent."),
                "verification_required": True,
                "verification_user_id": data.get("verification_user_id"),
            }

        session = data.get("session") or {}
        if session.get("access_token"):
            _store_auth_response(
                {
                    "access_token": session["access_token"],
                    "refresh_token": session.get("refresh_token"),
                    "expires_in": session.get("expires_in"),
                    "user": session.get("user"),
                }
            )
            register_device()
        else:
            login_result = login(email, password)
            if not login_result.get("ok"):
                return {
                    "ok": True,
                    "message": data.get("message", "Account created."),
                    "session": session,
                    "needs_login": True,
                }
        return {"ok": True, "message": data.get("message", "Account created."), "session": load_session()}


def verify_email(user_id: str, code: str) -> dict[str, Any]:
    return _cloud_post(
        "/v1/auth/verify-email",
        {"user_id": user_id, "code": code},
        require_auth=False,
        error_type_for_status={429: "rate_limit"},
    )


def resend_verification(user_id: str) -> dict[str, Any]:
    return _cloud_post(
        "/v1/auth/resend-verification",
        {"user_id": user_id},
        require_auth=False,
        error_type_for_status={429: "rate_limit"},
    )


def login(email: str, password: str) -> dict[str, Any]:
    # Login is proxied through the PLG API (`/v1/auth/login`), not sent straight
    # to GoTrue, so the server can rate-limit password guessing per account and
    # per IP. The API returns the raw GoTrue token payload under "session"; the
    # error_type strings below match what this function returned before, so the
    # UI's error handling is unchanged (invalid_credentials / email_not_confirmed
    # / rate_limit).
    result = _cloud_post(
        "/v1/auth/login",
        {"email": email.strip(), "password": password},
        require_auth=False,
        error_type_for_status={
            401: "invalid_credentials",
            403: "email_not_confirmed",
            429: "rate_limit",
        },
    )
    if not result.get("ok"):
        return result

    token_payload = result.get("session") or {}
    if not token_payload.get("access_token"):
        return {"ok": False, "error": "Malformed login response.", "error_type": "auth"}

    _store_auth_response(token_payload)
    global _device_registered
    _device_registered = False
    reg = register_device()
    if reg.get("ok"):
        _device_registered = True
    else:
        logger.warning("device register on login failed: %s", reg.get("error"))
    return {"ok": True, "session": load_session()}


def request_password_reset(email: str) -> dict[str, Any]:
    return _cloud_post(
        "/v1/auth/request-password-reset",
        {"email": email.strip()},
        require_auth=False,
        error_type_for_status={429: "rate_limit"},
    )


def complete_password_reset(email: str, code: str, new_password: str) -> dict[str, Any]:
    return _cloud_post(
        "/v1/auth/complete-password-reset",
        {"email": email.strip(), "code": code, "new_password": new_password},
        require_auth=False,
        # 422 = new password rejected for matching the old one → stable code
        # the UI localizes (auth.passwordMustDiffer); other statuses (wrong/
        # expired code, too many attempts) stay their own mapped types below.
        error_type_for_status={422: "same_password", 429: "rate_limit"},
    )


def refresh_session() -> dict[str, Any]:
    global _device_registered
    session = load_session()
    refresh = str(session.get("refresh_token") or "").strip()
    base = supabase_url()
    if not refresh or not base:
        return {"ok": False, "error": "No refresh token.", "error_type": "auth"}

    try:
        with make_client(timeout=30.0) as client:
            resp = client.post(
                f"{base}/auth/v1/token?grant_type=refresh_token",
                headers=_supabase_headers(),
                json={"refresh_token": refresh},
            )
            if resp.status_code in (400, 401, 403):
                # The server looked at the refresh token and refused it: only
                # then is the stored session dead.
                clear_session()
                _device_registered = False
                return {"ok": False, "error": "Session expired. Sign in again.", "error_type": "auth"}
            if resp.status_code >= 400:
                # A server-side hiccup (5xx, rate limit) says nothing about the
                # token: keep the session, the user is not signed out for it.
                return {"ok": False, "error": f"Session refresh failed ({resp.status_code}).", "error_type": "cloud"}
            _store_auth_response(resp.json())
            return {"ok": True, "session": load_session()}
    except httpx.HTTPError as exc:
        # No network: the session on disk is still the user's. Never raise out
        # of here, get_status() calls this on every start.
        logger.warning("session refresh failed type=%s", type(exc).__name__)
        return {"ok": False, "error": str(exc), "error_type": "network"}


def logout() -> dict[str, Any]:
    global _device_registered
    try:
        with make_client(timeout=15.0) as client:
            client.post(f"{cloud_api_url()}/v1/auth/logout", headers=_api_headers())
    except Exception:  # noqa: BLE001
        pass
    clear_session()
    _device_registered = False
    return {"ok": True}


def is_signed_in() -> bool:
    session = load_session()
    if not session.get("access_token"):
        return False
    if _needs_refresh(session):
        return bool(ensure_session().get("signed_in"))
    return True


def session_snapshot() -> dict[str, Any]:
    session = load_session()
    user = session.get("user") or {}
    email = session.get("email") or user.get("email")
    return {
        "signed_in": bool(session.get("access_token")),
        "email": email,
    }


def register_device() -> dict[str, Any]:
    with make_client(timeout=10.0) as client:
        resp = client.post(
            f"{cloud_api_url()}/v1/devices/register",
            headers=_api_headers(),
            json={"device_id": get_device_id(), "device_name": get_device_name()},
        )
        if resp.status_code == 401:
            refreshed = refresh_session()
            if not refreshed.get("ok"):
                return refreshed
            resp = client.post(
                f"{cloud_api_url()}/v1/devices/register",
                headers=_api_headers(),
                json={"device_id": get_device_id(), "device_name": get_device_name()},
            )
        try:
            data = resp.json() if resp.content else {}
        except Exception:  # noqa: BLE001
            data = {}
        if resp.status_code >= 400:
            detail = data.get("detail") if isinstance(data.get("detail"), str) else resp.text
            return {"ok": False, "error": detail or f"HTTP {resp.status_code}", "error_type": "device"}
        return {"ok": True, **data}


def fetch_me() -> dict[str, Any]:
    try:
        with make_client(timeout=12.0) as client:
            resp = client.get(f"{cloud_api_url()}/v1/me", headers=_api_headers())
            if resp.status_code == 401:
                refreshed = refresh_session()
                if not refreshed.get("ok"):
                    return refreshed
                resp = client.get(f"{cloud_api_url()}/v1/me", headers=_api_headers())
            data = resp.json()
            if resp.status_code >= 400:
                detail = str(data.get("detail") or resp.text)
                err_type = "rate_limit" if resp.status_code == 429 and "ip" in detail.lower() else "cloud"
                return {"ok": False, "error": detail, "error_type": err_type}
            return {"ok": True, **data}
    except httpx.HTTPError as exc:
        logger.warning("fetch_me failed type=%s", type(exc).__name__)
        return {"ok": False, "error": str(exc), "error_type": "network"}


def billing_status() -> dict[str, Any]:
    return _cloud_get("/v1/billing/status")


def billing_checkout(
    price_tier: str | None = None, *, accept_payment_terms: bool = False
) -> dict[str, Any]:
    payload: dict[str, Any] = {"accept_payment_terms": bool(accept_payment_terms)}
    if price_tier:
        payload["price_tier"] = price_tier
    return _cloud_post("/v1/billing/checkout", payload)


def billing_checkout_link() -> dict[str, Any]:
    """Create a short-lived web offer link; payment happens on the website."""
    return _cloud_post("/v1/billing/checkout-link", {})


def _cloud_get(path: str) -> dict[str, Any]:
    # Mirror _cloud_post: a network failure (DNS, refused, timeout) must come
    # back as a dict, never propagate. An uncaught ConnectTimeout here used to
    # bubble through plg_api.cloud_billing_status() into the pywebview bridge as
    # a raw exception, and the Account screen then rendered with billing=null —
    # which looks identical to a lapsed subscription ("Подписка закончилась")
    # even for a healthy trial account.
    try:
        with make_client(timeout=30.0) as client:
            resp = client.get(f"{cloud_api_url()}{path}", headers=_api_headers())
            if resp.status_code == 401:
                refreshed = refresh_session()
                if not refreshed.get("ok"):
                    return refreshed
                resp = client.get(f"{cloud_api_url()}{path}", headers=_api_headers())
            data = resp.json()
            if resp.status_code >= 400:
                detail = data.get("detail") if isinstance(data.get("detail"), str) else resp.text
                return {"ok": False, "error": detail, "error_type": "billing"}
            return {"ok": True, **data}
    except httpx.HTTPError as exc:
        return _format_cloud_http_error(exc, path=path)


def _cloud_post(
    path: str,
    payload: dict[str, Any],
    *,
    require_auth: bool = True,
    error_type_for_status: dict[int, str] | None = None,
) -> dict[str, Any]:
    """``error_type_for_status`` maps a response status code to a stable
    error_type the UI localizes (same idea as login()/signup()'s inline
    ternaries, generalized here since every _cloud_post caller shares this
    same HTTP-call/401-retry machinery). Any status not listed falls back to
    the generic "auth" type, same as before this parameter existed."""
    headers = (
        _api_headers()
        if require_auth
        else {"X-PLG-Version": app_version(), "Content-Type": "application/json"}
    )
    try:
        # Billing-link creation must never leave the desktop button spinning
        # indefinitely when the Cloud endpoint is unreachable.
        timeout = httpx.Timeout(12.0, connect=4.0)
        with make_client(timeout=timeout) as client:
            resp = client.post(f"{cloud_api_url()}{path}", headers=headers, json=payload)
            if require_auth and resp.status_code == 401:
                refreshed = refresh_session()
                if not refreshed.get("ok"):
                    return refreshed
                resp = client.post(f"{cloud_api_url()}{path}", headers=_api_headers(), json=payload)
            data = resp.json()
            if resp.status_code >= 400:
                detail = data.get("detail") if isinstance(data.get("detail"), str) else resp.text
                err_type = (error_type_for_status or {}).get(resp.status_code, "auth")
                return {"ok": False, "error": detail, "error_type": err_type}
            return {"ok": True, **data}
    except httpx.HTTPError as exc:
        return _format_cloud_http_error(exc, path=path)


def cloud_generate(
    prompt: str,
    *,
    catalog: dict[str, Any] | None = None,
    user_profile: dict[str, Any] | None = None,
    locale: str | None = None,
    smart_plan: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prompt": prompt,
        "device_id": get_device_id(),
    }
    if catalog is not None:
        payload["catalog"] = catalog
    if user_profile is not None:
        payload["user_profile"] = user_profile
    if locale:
        payload["locale"] = locale
    if smart_plan is not None:
        payload["smart_plan"] = smart_plan

    device_recovery_error: str | None = None
    try:
        timeout = httpx.Timeout(
            _CLOUD_GENERATE_READ_TIMEOUT_S, connect=_CLOUD_GENERATE_CONNECT_TIMEOUT_S
        )
        with make_client(timeout=timeout) as client:
            resp = client.post(
                f"{cloud_api_url()}/v1/generate",
                headers=_api_headers(),
                json=payload,
            )
            if resp.status_code == 401:
                refreshed = refresh_session()
                if not refreshed.get("ok"):
                    return refreshed
                resp = client.post(
                    f"{cloud_api_url()}/v1/generate",
                    headers=_api_headers(),
                    json=payload,
                )
            if _is_unknown_device(resp):
                # The server does not know this PC (registration never ran or
                # failed at launch). Register it once and retry the same
                # request before the user sees anything.
                registered = _reregister_device()
                if registered.get("ok"):
                    resp = client.post(
                        f"{cloud_api_url()}/v1/generate",
                        headers=_api_headers(),
                        json=payload,
                    )
                elif registered.get("error_type") in ("network", "auth"):
                    return registered
                else:
                    # e.g. the account's device limit: say that, not "unknown device".
                    device_recovery_error = str(registered.get("error") or "") or None
            try:
                data = resp.json()
            except ValueError:
                # A proxy / gateway error page is not JSON. The server did
                # answer, so this is a "cloud" error, not a lost connection.
                data = {}
            if not isinstance(data, dict):
                data = {}
    except httpx.HTTPError as exc:
        logger.exception("cloud_generate request failed")
        return _format_cloud_http_error(exc, path="/v1/generate")
    except OSError as exc:
        logger.exception("cloud_generate network failed")
        return _format_cloud_http_error(exc, path="/v1/generate")

    if resp.status_code < 400 and "pattern" not in data:
        logger.warning("cloud_generate: HTTP %s without a pattern", resp.status_code)
        return {
            "ok": False,
            "error": f"PLG Cloud answered HTTP {resp.status_code} without a beat.",
            "error_type": "cloud",
        }
    if resp.status_code >= 400:
        detail = data.get("detail") if isinstance(data.get("detail"), str) else data.get("detail")
        if isinstance(detail, list):
            detail = detail[0].get("msg") if detail else resp.text
        detail_text = str(detail or resp.text)[:500]
        err_type = "cloud"
        if resp.status_code == 429:
            err_type = (
                "rate_limit"
                if "ip" in detail_text.lower()
                else "quota"
            )
        elif resp.status_code == 402:
            err_type = "subscription"
        elif _is_unknown_device(resp):
            # Still unknown after the re-registration attempt: a device
            # problem, not a server hiccup — retrying later will not help.
            err_type = "device"
            if device_recovery_error:
                detail_text = device_recovery_error[:500]
        return {"ok": False, "error": detail_text, "error_type": err_type, "quota": data.get("quota")}
    return {"ok": True, **data}


def _is_unknown_device(resp: httpx.Response) -> bool:
    """True for the server's 403 "Unknown device" (cloud/app/security.py)."""
    if resp.status_code != 403:
        return False
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return "unknown device" in str(detail or resp.text).lower()


def _reregister_device() -> dict[str, Any]:
    """Register this PC with the server now; never raises."""
    global _device_registered
    try:
        registered = register_device()
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("device re-registration failed type=%s", type(exc).__name__)
        return _format_cloud_http_error(exc, path="/v1/devices/register")
    if registered.get("ok"):
        _device_registered = True
        logger.info("device re-registered after 'Unknown device' from /v1/generate")
    else:
        logger.warning("device re-registration refused: %s", registered.get("error"))
    return registered


def fetch_feature_flags() -> dict[str, Any]:
    try:
        with make_client(timeout=15.0) as client:
            if is_signed_in():
                resp = client.get(f"{cloud_api_url()}/v1/flags/me", headers=_auth_headers())
            else:
                resp = client.get(f"{cloud_api_url()}/v1/flags")
            if resp.status_code >= 400:
                return {"ok": False}
            return {"ok": True, **resp.json()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("flags fetch failed type=%s", type(exc).__name__)
        return {"ok": False}


def fetch_auth_config() -> dict[str, Any]:
    try:
        with make_client(timeout=15.0) as client:
            resp = client.get(f"{cloud_api_url()}/v1/auth/config")
            if resp.status_code >= 400:
                return {"ok": False}
            return {"ok": True, **resp.json()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth config fetch failed type=%s", type(exc).__name__)
        return {"ok": False}


def delete_account() -> dict[str, Any]:
    global _device_registered
    with make_client(timeout=30.0) as client:
        resp = client.delete(f"{cloud_api_url()}/v1/account", headers=_api_headers())
        if resp.status_code == 401:
            refreshed = refresh_session()
            if not refreshed.get("ok"):
                return refreshed
            resp = client.delete(f"{cloud_api_url()}/v1/account", headers=_api_headers())
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            detail = data.get("detail") if isinstance(data.get("detail"), str) else resp.text
            return {"ok": False, "error": detail, "error_type": "account"}
    clear_session()
    _device_registered = False
    return {"ok": True, "message": data.get("message", "Account deleted.")}


def ping_cloud() -> dict[str, Any]:
    """Quick connectivity check for the offline UI.

    ``/v1/status`` intentionally checks database-backed operational details and
    may be slow while a dependency recovers.  It must not decide whether a
    customer can use PLG.  ``/health`` is served without those checks.
    """
    global _cloud_probe_failures, _cloud_probe_last_failure_at
    base = _cloud_probe_url()
    last_error = ""
    for _ in range(_CLOUD_PROBE_ATTEMPTS):
        try:
            timeout = httpx.Timeout(3.0, connect=2.0)
            with make_client(timeout=timeout) as client:
                resp = client.get(f"{base}/health", headers={"X-PLG-Version": app_version()})
            if resp.status_code >= 400:
                # Something answered, so the network is fine — a gateway error
                # page or a maintenance 503 is not "no internet". Generation
                # reports its own error if the service really cannot serve it.
                _cloud_probe_failures = 0
                return {
                    "ok": True,
                    "cloud_url": base,
                    "probe_degraded": True,
                    "http_status": resp.status_code,
                }
            _cloud_probe_failures = 0
            return {"ok": True, "cloud_url": base}
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)

    now = time.monotonic()
    # Stale blips expire, but once the client is offline it stays offline until
    # a probe succeeds (a lone failed re-check must not flip the banner off).
    if (
        _cloud_probe_failures < _CLOUD_PROBE_FAILURES_BEFORE_OFFLINE
        and now - _cloud_probe_last_failure_at > _CLOUD_PROBE_FAILURE_WINDOW_S
    ):
        _cloud_probe_failures = 0
    _cloud_probe_last_failure_at = now
    _cloud_probe_failures += 1
    if _cloud_probe_failures < _CLOUD_PROBE_FAILURES_BEFORE_OFFLINE:
        # Do not lock a customer's app after one transient network blip. The
        # actual cloud action still reports a failure if the service is down.
        return {
            "ok": True,
            "cloud_url": base,
            "probe_degraded": True,
            "last_probe_error": last_error,
        }
    return {"ok": False, "cloud_url": base, "error": last_error, "error_type": "network"}


def fetch_status() -> dict[str, Any]:
    base = cloud_api_url()
    if not base:
        return {"ok": False, "error": "PLG_CLOUD_URL not configured.", "error_type": "config"}
    try:
        with make_client(timeout=15.0) as client:
            resp = client.get(f"{base}/v1/status", headers={"X-PLG-Version": app_version()})
            if resp.status_code >= 400:
                return {"ok": False, "error": resp.text, "error_type": "cloud"}
            return {"ok": True, **resp.json()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "error_type": "network"}


def submit_feedback(
    *,
    category: str,
    message: str,
    attach_log: bool = False,
    log_excerpt: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "category": category,
        "message": message,
        "attach_log": attach_log,
    }
    if log_excerpt:
        payload["log_excerpt"] = log_excerpt
    return _cloud_post("/v1/feedback", payload)


def sync_beat(payload: dict[str, Any]) -> dict[str, Any]:
    """Upload sanitized beat metadata/pattern for cloud learning."""
    return _cloud_post("/v1/beats", payload)


def sync_beat_event(
    beat_id: str,
    event_type: str,
    *,
    properties: dict[str, Any] | None = None,
    source: str = "desktop",
) -> dict[str, Any]:
    """Upload one immutable user action for a beat."""
    return _cloud_post(
        f"/v1/beats/{beat_id}/events",
        {
            "event_type": event_type,
            "source": source,
            "properties": properties or {},
        },
    )


def resolve_genre(prompt: str) -> dict[str, Any]:
    """Server-side genre brain: the gateway key never leaves the cloud."""
    return _cloud_post("/v1/genre/resolve", {"prompt": prompt})


def cloud_reward_status(genre: str | None = None) -> dict[str, Any]:
    from urllib.parse import quote

    suffix = f"?genre={quote(genre)}" if genre else ""
    return _cloud_get(f"/v1/beats/reward-status{suffix}")

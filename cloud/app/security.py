"""IP/fingerprint extraction, bans, honeypot, device binding."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request
from supabase import Client

from cloud.app.config import REQUIRE_DEVICE_BINDING, TRUSTED_PROXY

logger = logging.getLogger("plg.security")


def _peer_ip(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


def client_ip(request: Request) -> str:
    """Resolve the real client IP without trusting spoofable headers.

    `X-Forwarded-For` / `CF-Connecting-IP` are attacker-controllable unless the
    request actually arrives through our proxy. Trusting them blindly lets an
    attacker dodge IP rate limits/bans and frame other IPs. Only honour the
    header that our configured edge (PLG_TRUSTED_PROXY) is known to set.
    """
    proxy = TRUSTED_PROXY
    if proxy == "cloudflare":
        # Cloudflare overwrites CF-Connecting-IP with the true client IP. Raw
        # X-Forwarded-For[0] is still client-controlled, so we ignore it.
        cf = request.headers.get("CF-Connecting-IP", "").strip()
        return cf or _peer_ip(request)
    if proxy in ("xff", "proxy", "nginx", "alb", "ingress"):
        forwarded = request.headers.get("X-Forwarded-For", "").strip()
        return forwarded.split(",")[0].strip() if forwarded else _peer_ip(request)
    # Direct exposure (PLG_TRUSTED_PROXY=none/unknown): never trust headers.
    return _peer_ip(request)


def client_country(request: Request) -> str | None:
    """ISO-3166 alpha-2 (upper) for the request's client, or ``None``.

    Resolved entirely on this server: the real IP (see ``client_ip``) is looked
    up in a local DB-IP database — nothing is sent to any third party. A
    client-supplied ``X-App-Country`` / ``X-Country-Code`` hint is honoured only
    as a fallback when the IP can't be placed (e.g. the DB file isn't installed
    yet). Junk sentinels are dropped.
    """
    from cloud.app.geoip import country_for_ip

    code = country_for_ip(client_ip(request))
    if code:
        return code
    for header in ("X-App-Country", "X-Country-Code"):
        value = (request.headers.get(header) or "").strip().upper()
        if value and value not in {"XX", "T1", "ZZ"}:
            return value
    return None


def fingerprint(device_id: str | None, ip: str) -> str:
    raw = f"{(device_id or '').strip()}|{ip.strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def ip_in_allowlist(ip: str, cidrs: list[str]) -> bool:
    """True if `ip` falls in any of the given CIDRs / single addresses."""
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if addr.version == net.version and addr in net:
            return True
    return False


def check_honeypot(value: str | None) -> None:
    if value and str(value).strip():
        raise HTTPException(400, "Request rejected.")


def _ban_active(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    expires = row.get("expires_at")
    if expires:
        exp = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > exp:
            return False
    return True


def is_banned(
    client: Client,
    *,
    user_id: str | None = None,
    device_id: str | None = None,
    ip: str | None = None,
    fp: str | None = None,
) -> str | None:
    if user_id:
        prof = client.table("profiles").select("banned,ban_reason").eq("id", user_id).maybe_single().execute()
        pdata = prof.data if prof else None
        if pdata and pdata.get("banned"):
            return str(pdata.get("ban_reason") or "Account banned.")

    checks: list[tuple[str, str]] = []
    if user_id:
        checks.append(("user", user_id))
    if device_id:
        checks.append(("device", device_id))
    if ip:
        checks.append(("ip", ip))
    if fp:
        checks.append(("fingerprint", fp))

    for ban_type, ban_value in checks:
        result = (
            client.table("security_bans")
            .select("*")
            .eq("ban_type", ban_type)
            .eq("ban_value", ban_value)
            .maybe_single()
            .execute()
        )
        row = result.data if result else None
        if _ban_active(row):
            return str((row or {}).get("reason") or f"Banned ({ban_type}).")
    return None


def enforce_not_banned(
    client: Client,
    *,
    user_id: str,
    device_id: str | None = None,
    ip: str | None = None,
) -> None:
    fp = fingerprint(device_id, ip or "")
    reason = is_banned(client, user_id=user_id, device_id=device_id, ip=ip, fp=fp)
    if reason:
        raise HTTPException(403, reason)


def require_registered_device(client: Client, user_id: str, device_id: str | None) -> str:
    device_id = (device_id or "").strip()
    if not device_id:
        if REQUIRE_DEVICE_BINDING:
            raise HTTPException(400, "Missing device_id. Update the desktop app.")
        return ""

    existing = (
        client.table("user_devices")
        .select("id")
        .eq("user_id", user_id)
        .eq("device_id", device_id)
        .maybe_single()
        .execute()
    )
    if not (existing and existing.data):
        raise HTTPException(
            403,
            "Unknown device. Sign out and sign in again to bind this PC.",
        )
    return device_id


def _trial_farm_exempt_devices() -> set[str]:
    """Device IDs that may claim the trial repeatedly — dev/QA machines only.

    Set PLG_TRIAL_FARM_EXEMPT_DEVICE_IDS to a comma-separated list on a staging
    or test deployment so the end-to-end signup flow can be exercised from a
    machine that has already signed up before. Empty in production.
    """
    import os

    raw = os.getenv("PLG_TRIAL_FARM_EXEMPT_DEVICE_IDS", "")
    return {d.strip() for d in raw.split(",") if d.strip()}


def _claim_aged_out(claimed_at: Any) -> bool:
    """True if a device_trial_claims row is older than PLG_TRIAL_CLAIM_TTL_DAYS."""
    from cloud.app.config import TRIAL_CLAIM_TTL_DAYS

    if TRIAL_CLAIM_TTL_DAYS <= 0:
        return False
    if not claimed_at:
        return False
    try:
        ts = datetime.fromisoformat(str(claimed_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    from datetime import timedelta

    return datetime.now(timezone.utc) - ts > timedelta(days=TRIAL_CLAIM_TTL_DAYS)


def claim_device_trial(client: Client, device_id: str, user_id: str) -> bool:
    """Return True if this device may use the trial.

    One free beat per device, but the claim is not permanent: a claim older than
    PLG_TRIAL_CLAIM_TTL_DAYS ages out and the device may claim a fresh trial
    (re-stamped to the new account, so the next claimant waits another full
    window). Fully automatic — no support step. A farmer would have to wait the
    whole window per free account, which isn't worth it.
    """
    device_id = (device_id or "").strip()
    if not device_id:
        return True
    if device_id in _trial_farm_exempt_devices():
        logger.info("trial farming check skipped for exempt device=%s", device_id)
        return True

    existing = (
        client.table("device_trial_claims")
        .select("*")
        .eq("device_id", device_id)
        .maybe_single()
        .execute()
    )
    row = existing.data if existing else None
    if row:
        if str(row.get("first_user_id")) == user_id:
            return True
        if _claim_aged_out(row.get("claimed_at")):
            client.table("device_trial_claims").update(
                {
                    "first_user_id": user_id,
                    "claimed_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("device_id", device_id).execute()
            logger.info("stale device trial claim re-issued device=%s", device_id)
            return True
        return False

    client.table("device_trial_claims").insert(
        {"device_id": device_id, "first_user_id": user_id}
    ).execute()
    return True


def block_trial_farming(client: Client, user_id: str, device_id: str | None) -> None:
    if not claim_device_trial(client, device_id or "", user_id):
        client.table("profiles").update(
            {
                "status": "expired",
                "ban_reason": "Trial already used on this device.",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", user_id).execute()
        logger.warning("trial farming blocked user=%s device=%s", user_id, device_id)

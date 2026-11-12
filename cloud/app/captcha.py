"""CAPTCHA verification — Cloudflare Turnstile / hCaptcha."""

from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException

from cloud.app.config import (
    CAPTCHA_PROVIDER,
    HCAPTCHA_SECRET_KEY,
    TURNSTILE_SECRET_KEY,
    YANDEX_CAPTCHA_SECRET_KEY,
)

logger = logging.getLogger("plg.captcha")


def captcha_required() -> bool:
    if CAPTCHA_PROVIDER == "none":
        return False
    if CAPTCHA_PROVIDER == "turnstile":
        return bool(TURNSTILE_SECRET_KEY)
    if CAPTCHA_PROVIDER == "hcaptcha":
        return bool(HCAPTCHA_SECRET_KEY)
    if CAPTCHA_PROVIDER == "yandex":
        return bool(YANDEX_CAPTCHA_SECRET_KEY)
    return False


async def verify_captcha(token: str | None, remote_ip: str | None = None) -> None:
    if not captcha_required():
        return

    if not token or not str(token).strip():
        raise HTTPException(400, "CAPTCHA required. Complete the challenge and try again.")

    if CAPTCHA_PROVIDER == "turnstile":
        await _verify_turnstile(token, remote_ip)
    elif CAPTCHA_PROVIDER == "hcaptcha":
        await _verify_hcaptcha(token, remote_ip)
    elif CAPTCHA_PROVIDER == "yandex":
        await _verify_yandex(token, remote_ip)


async def _verify_turnstile(token: str, remote_ip: str | None) -> None:
    payload = {"secret": TURNSTILE_SECRET_KEY, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=payload,
        )
        data = resp.json()

    if not data.get("success"):
        logger.warning("turnstile failed: %s", data.get("error-codes"))
        raise HTTPException(400, "CAPTCHA verification failed.")


async def _verify_hcaptcha(token: str, remote_ip: str | None) -> None:
    payload = {"secret": HCAPTCHA_SECRET_KEY, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post("https://hcaptcha.com/siteverify", data=payload)
        data = resp.json()

    if not data.get("success"):
        logger.warning("hcaptcha failed: %s", data.get("error-codes"))
        raise HTTPException(400, "CAPTCHA verification failed.")


async def _verify_yandex(token: str, remote_ip: str | None) -> None:
    params = {"secret": YANDEX_CAPTCHA_SECRET_KEY, "token": token}
    if remote_ip:
        params["ip"] = remote_ip

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://smartcaptcha.yandexcloud.net/validate",
            params=params,
        )
        data = resp.json()

    if data.get("status") != "ok":
        logger.warning("yandex smartcaptcha failed: %s", data.get("message"))
        raise HTTPException(400, "CAPTCHA verification failed.")

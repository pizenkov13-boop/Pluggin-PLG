"""PostHog product analytics (desktop + optional server)."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from app_config import app_version

logger = logging.getLogger("plg.analytics")

_distinct_id: str | None = None


def _distinct_id_value() -> str:
    global _distinct_id
    if _distinct_id:
        return _distinct_id
    from plg_device import get_device_id

    _distinct_id = get_device_id() or str(uuid.uuid4())
    return _distinct_id


def is_enabled() -> bool:
    return bool((os.getenv("POSTHOG_API_KEY") or os.getenv("PLG_POSTHOG_KEY") or "").strip())


def track(event: str, properties: dict[str, Any] | None = None) -> None:
    key = (os.getenv("POSTHOG_API_KEY") or os.getenv("PLG_POSTHOG_KEY") or "").strip()
    host = (os.getenv("POSTHOG_HOST") or os.getenv("PLG_POSTHOG_HOST") or "https://eu.i.posthog.com").rstrip("/")
    if not key:
        return

    try:
        import httpx

        payload = {
            "api_key": key,
            "event": event,
            "distinct_id": _distinct_id_value(),
            "properties": {
                **(properties or {}),
                "app_version": app_version(),
                "platform": "desktop",
            },
        }
        httpx.post(f"{host}/capture/", json=payload, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("posthog track failed: %s", exc)


def identify_user(user_id: str, traits: dict[str, Any] | None = None) -> None:
    global _distinct_id
    _distinct_id = user_id
    track("$identify", {"$set": traits or {}})

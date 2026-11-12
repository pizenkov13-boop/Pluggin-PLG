"""Extended health / readiness for load balancers."""

from __future__ import annotations

from typing import Any

from cloud.app.config import APP_VERSION, MIN_CLIENT_VERSION, PLG_ENV
from cloud.app.redis_store import redis_available


def health_payload() -> dict[str, Any]:
    return {
        "ok": "true",
        "version": APP_VERSION,
        "min_client": MIN_CLIENT_VERSION,
        "env": PLG_ENV,
        "redis": redis_available(),
    }

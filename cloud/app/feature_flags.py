"""Remote feature flags — rollout without new .exe."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from supabase import Client
from cloud.app.privacy import exception_kind

logger = logging.getLogger("plg.flags")

_cache: dict[str, Any] = {"at": 0.0, "rows": []}
_CACHE_TTL = 60.0


def _load_flags(client: Client) -> list[dict[str, Any]]:
    now = time.time()
    if now - float(_cache["at"]) < _CACHE_TTL and _cache["rows"]:
        return list(_cache["rows"])
    try:
        result = client.table("feature_flags").select("*").execute()
        rows = list(result.data or [])
        _cache["at"] = now
        _cache["rows"] = rows
        return rows
    except Exception as exc:  # noqa: BLE001
        logger.warning("feature_flags load failed type=%s", exception_kind(exc))
        return list(_cache["rows"])


def _bucket(user_id: str | None, key: str) -> int:
    if not user_id:
        return 0
    digest = hashlib.sha256(f"{key}:{user_id}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def flag_enabled(
    client: Client,
    key: str,
    *,
    user_id: str | None = None,
    default: bool = False,
) -> bool:
    for row in _load_flags(client):
        if row.get("key") != key:
            continue
        if not row.get("enabled"):
            return False
        pct = int(row.get("rollout_pct") or 100)
        if pct >= 100:
            return True
        return _bucket(user_id, key) < pct
    return default


def flags_snapshot(client: Client, user_id: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in _load_flags(client):
        key = str(row.get("key") or "")
        if not key:
            continue
        out[key] = flag_enabled(client, key, user_id=user_id, default=bool(row.get("enabled")))
    return out


def invalidate_cache() -> None:
    _cache["at"] = 0.0

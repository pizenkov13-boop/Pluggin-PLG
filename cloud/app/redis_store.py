"""Upstash Redis — distributed rate limits + LLM queue slots."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from cloud.app.config import PLG_ENV, REDIS_URL
from cloud.app.privacy import exception_kind

logger = logging.getLogger("plg.redis")

_client: Any = None
_last_attempt = 0.0
_RECONNECT_COOLDOWN = 30.0  # don't reconnect-storm, but don't give up forever


def redis_client() -> Any | None:
    """Connected Redis client, or None (callers fall back to in-memory).

    Unlike a one-shot check, this retries every ~30s after a failure so a brief
    Redis blip doesn't permanently demote distributed limits to per-process for
    the whole process lifetime. In production a missing/broken Redis is logged at
    ERROR (per-process limits effectively multiply by worker count).
    """
    global _client, _last_attempt
    if _client is not None:
        return _client

    now = time.monotonic()
    if now - _last_attempt < _RECONNECT_COOLDOWN:
        return None
    _last_attempt = now

    if not REDIS_URL:
        if PLG_ENV == "production":
            logger.error("REDIS_URL not set in production — rate limits are per-process only.")
        return None

    try:
        import redis

        client = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2.0)
        client.ping()
        _client = client
        logger.info("Redis connected (Upstash)")
        return _client
    except Exception as exc:  # noqa: BLE001
        if PLG_ENV == "production":
            logger.error("Redis unavailable in production — limits degraded type=%s", exception_kind(exc))
        else:
            logger.warning("Redis unavailable, using in-memory fallback type=%s", exception_kind(exc))
        return None


def redis_available() -> bool:
    return redis_client() is not None


def sliding_allow(key: str, limit: int, window_sec: float) -> bool:
    """Return True if request is allowed under sliding window limit."""
    client = redis_client()
    if not client:
        return True  # caller uses in-memory fallback

    now = time.time()
    pipe = client.pipeline()
    pipe.zremrangebyscore(key, 0, now - window_sec)
    pipe.zadd(key, {str(now): now})
    pipe.zcard(key)
    pipe.expire(key, int(window_sec) + 10)
    _, _, count, _ = pipe.execute()
    if count > limit:
        client.zrem(key, str(now))
        return False
    return True


def sliding_wait_sec(key: str, limit: int, window_sec: float) -> float:
    client = redis_client()
    if not client:
        return 0.0
    now = time.time()
    oldest = client.zrange(key, 0, 0, withscores=True)
    if not oldest or len(client.zrange(key, 0, -1)) < limit:
        return 0.0
    return max(0.0, oldest[0][1] + window_sec - now)


def window_add(key: str, window_sec: float) -> int:
    """Record one event in a sliding window; return the count now in-window.

    Separate from ``sliding_allow`` (which records *and* decides): the login
    guard records only on a *failed* attempt and checks separately before the
    attempt. Returns -1 with no Redis so the caller keeps its in-memory count.
    """
    client = redis_client()
    if not client:
        return -1
    now = time.time()
    member = f"{now}:{os.urandom(6).hex()}"  # unique — two fails in one ms must not collide
    pipe = client.pipeline()
    pipe.zremrangebyscore(key, 0, now - window_sec)
    pipe.zadd(key, {member: now})
    pipe.zcard(key)
    pipe.expire(key, int(window_sec) + 10)
    results = pipe.execute()
    return int(results[2])


def window_count(key: str, window_sec: float) -> int:
    """Events currently in the sliding window (0 with no Redis)."""
    client = redis_client()
    if not client:
        return 0
    now = time.time()
    pipe = client.pipeline()
    pipe.zremrangebyscore(key, 0, now - window_sec)
    pipe.zcard(key)
    results = pipe.execute()
    return int(results[1])


def window_clear(key: str) -> None:
    client = redis_client()
    if client:
        client.delete(key)


def incr_slot(key: str, max_slots: int, ttl_sec: int = 300) -> bool:
    """Acquire distributed slot. Return False if at capacity."""
    client = redis_client()
    if not client:
        return True
    count = int(client.incr(key))
    if count == 1:
        client.expire(key, ttl_sec)
    if count > max_slots:
        client.decr(key)
        return False
    return True


def decr_slot(key: str) -> None:
    client = redis_client()
    if not client:
        return
    try:
        val = int(client.decr(key))
        if val < 0:
            client.set(key, 0)
    except Exception:  # noqa: BLE001
        pass

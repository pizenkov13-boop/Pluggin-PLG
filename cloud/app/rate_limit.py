"""Rate limits — in-memory locally, Redis (Upstash) in production."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict

from fastapi import HTTPException

from cloud.app.config import (
    GEN_COOLDOWN_SEC,
    GEN_HOURLY_LIMIT,
    IP_HOURLY_LIMIT,
    LOGIN_LOCKOUT_SEC,
    LOGIN_MAX_FAILURES,
    SIGNUP_IP_HOURLY_LIMIT,
)
from cloud.app import redis_store


class SlidingWindow:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_sec: float) -> bool:
        if redis_store.redis_available():
            return redis_store.sliding_allow(key, limit, window_sec)

        now = time.monotonic()
        cutoff = now - window_sec
        with self._lock:
            hits = [t for t in self._hits[key] if t > cutoff]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def seconds_until_allowed(self, key: str, limit: int, window_sec: float) -> float:
        if redis_store.redis_available():
            return redis_store.sliding_wait_sec(key, limit, window_sec)

        now = time.monotonic()
        cutoff = now - window_sec
        with self._lock:
            hits = sorted(t for t in self._hits[key] if t > cutoff)
        if len(hits) < limit:
            return 0.0
        return max(0.0, hits[0] + window_sec - now)


class FailureCounter:
    """Sliding count of recent failures per key, with separate record / check.

    ``SlidingWindow.allow`` records *and* decides in one call — wrong shape for
    login, where a failed attempt is recorded but the lockout is checked
    *before* the next attempt runs. Redis-backed in production (shared across
    workers); per-process in-memory otherwise, same trade-off as SlidingWindow.
    """

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def _mem_prune(self, key: str, window_sec: float) -> list[float]:
        cutoff = time.monotonic() - window_sec
        hits = [t for t in self._hits[key] if t > cutoff]
        self._hits[key] = hits
        return hits

    def count(self, key: str, window_sec: float) -> int:
        if redis_store.redis_available():
            return redis_store.window_count(key, window_sec)
        with self._lock:
            return len(self._mem_prune(key, window_sec))

    def record(self, key: str, window_sec: float) -> int:
        if redis_store.redis_available():
            return redis_store.window_add(key, window_sec)
        with self._lock:
            hits = self._mem_prune(key, window_sec)
            hits.append(time.monotonic())
            self._hits[key] = hits
            return len(hits)

    def clear(self, key: str) -> None:
        if redis_store.redis_available():
            redis_store.window_clear(key)
        with self._lock:
            self._hits.pop(key, None)


_ip_window = SlidingWindow()
_user_gen_window = SlidingWindow()
_user_cooldown = SlidingWindow()
_signup_window = SlidingWindow()
_login_failures = FailureCounter()


def _login_acct_key(email: str) -> str:
    # Never key Redis on a raw email — hash it, same reasoning as security.fingerprint.
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]
    return f"rl:login:acct:{digest}"


def _login_ip_key(ip: str) -> str:
    return f"rl:login:ip:{ip}"


_LOGIN_LOCKED_MSG = (
    "Too many failed sign-in attempts. Wait a few minutes and try again."
)


def check_login_limit(*, email: str, ip: str | None) -> None:
    """Raise 429 if this account, or this IP, is currently locked out."""
    if _login_failures.count(_login_acct_key(email), float(LOGIN_LOCKOUT_SEC)) >= LOGIN_MAX_FAILURES:
        raise HTTPException(429, _LOGIN_LOCKED_MSG)
    if ip and _login_failures.count(_login_ip_key(ip), float(LOGIN_LOCKOUT_SEC)) >= LOGIN_MAX_FAILURES:
        raise HTTPException(429, _LOGIN_LOCKED_MSG)


def register_login_failure(*, email: str, ip: str | None) -> None:
    """Count one failed attempt against both the account and the IP."""
    _login_failures.record(_login_acct_key(email), float(LOGIN_LOCKOUT_SEC))
    if ip:
        _login_failures.record(_login_ip_key(ip), float(LOGIN_LOCKOUT_SEC))


def clear_login_failures(*, email: str) -> None:
    """Wipe the account's failure count on a successful sign-in.

    The IP counter is deliberately left to age out on its own: clearing it on
    any success would let an attacker on a shared/NAT IP reset the IP lockout
    just by signing into one account they do control.
    """
    _login_failures.clear(_login_acct_key(email))


def check_ip_limit(ip: str) -> None:
    if not ip:
        return
    if not _ip_window.allow(f"rl:ip:{ip}", IP_HOURLY_LIMIT, 3600.0):
        raise HTTPException(429, f"Too many requests from this IP (max {IP_HOURLY_LIMIT}/hour).")


def check_signup_limit(ip: str) -> None:
    """Signup is exempt from the global IP middleware — gate it separately so a
    single IP can't farm accounts (trial-beat farming) even if CAPTCHA is off."""
    if not ip:
        return
    if not _signup_window.allow(f"rl:signup:{ip}", SIGNUP_IP_HOURLY_LIMIT, 3600.0):
        raise HTTPException(
            429, "Too many sign-up attempts from this network. Try again later."
        )


def check_generate_limits(user_id: str) -> None:
    cooldown_key = f"rl:gen_cd:{user_id}"
    if not _user_cooldown.allow(cooldown_key, 1, float(GEN_COOLDOWN_SEC)):
        wait = int(_user_cooldown.seconds_until_allowed(cooldown_key, 1, float(GEN_COOLDOWN_SEC))) + 1
        raise HTTPException(429, f"Wait {wait}s before generating again.")

    hourly_key = f"rl:gen_hr:{user_id}"
    if not _user_gen_window.allow(hourly_key, GEN_HOURLY_LIMIT, 3600.0):
        raise HTTPException(429, f"Hourly generation limit reached ({GEN_HOURLY_LIMIT}/hour).")

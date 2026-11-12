"""Brute-force lockout for the proxied password login (cloud/app/auth_login.py).

Login used to bypass the PLG API entirely (client -> GoTrue), so none of this
could be enforced. These tests pin the per-account + per-IP lockout that the
proxy adds.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

import cloud.app.auth_login as auth_login
import cloud.app.rate_limit as rate_limit
from cloud.app import redis_store


class _Resp:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Context-manager stand-in for httpx.Client; counts .post() calls."""

    def __init__(self, resp: _Resp | Exception, calls: list) -> None:
        self._resp = resp
        self._calls = calls

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *_a: object) -> None:
        return None

    def post(self, url: str, **kwargs: object) -> _Resp:
        self._calls.append(url)
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp


@pytest.fixture(autouse=True)
def _isolate_login_counters(monkeypatch):
    # No Redis in tests -> in-memory path; wipe the module singleton so counts
    # don't bleed across tests.
    monkeypatch.setattr(redis_store, "redis_available", lambda: False)
    rate_limit._login_failures._hits.clear()
    monkeypatch.setattr(auth_login, "SUPABASE_URL", "https://api.example.test")
    monkeypatch.setattr(auth_login, "SUPABASE_ANON_KEY", "anon-test")
    yield
    rate_limit._login_failures._hits.clear()


def _mock_upstream(monkeypatch, resp: _Resp | Exception) -> list:
    calls: list = []
    monkeypatch.setattr(auth_login.httpx, "Client", lambda **_k: _FakeClient(resp, calls))
    return calls


def test_locks_account_after_max_failures(monkeypatch):
    calls = _mock_upstream(monkeypatch, _Resp(400, {"error_code": "invalid_grant"}))

    for _ in range(rate_limit.LOGIN_MAX_FAILURES):
        with pytest.raises(HTTPException) as exc:
            auth_login.login_user(email="a@example.test", password="x", remote_ip="1.1.1.1")
        assert exc.value.status_code == 401

    # Next attempt is refused before it ever reaches the upstream.
    calls_before = len(calls)
    with pytest.raises(HTTPException) as exc:
        auth_login.login_user(email="a@example.test", password="x", remote_ip="1.1.1.1")
    assert exc.value.status_code == 429
    assert len(calls) == calls_before  # no upstream call spent on a locked account


def test_success_clears_account_lockout(monkeypatch):
    _mock_upstream(monkeypatch, _Resp(400, {}))
    for _ in range(rate_limit.LOGIN_MAX_FAILURES - 1):
        with pytest.raises(HTTPException):
            auth_login.login_user(email="b@example.test", password="x", remote_ip="2.2.2.2")

    _mock_upstream(monkeypatch, _Resp(200, {"access_token": "tok", "refresh_token": "r"}))
    ok = auth_login.login_user(email="b@example.test", password="right", remote_ip="2.2.2.2")
    assert ok == {"ok": True, "session": {"access_token": "tok", "refresh_token": "r"}}

    # Counter was reset by the success — a fresh run of failures is allowed again.
    _mock_upstream(monkeypatch, _Resp(400, {}))
    with pytest.raises(HTTPException) as exc:
        auth_login.login_user(email="b@example.test", password="x", remote_ip="2.2.2.2")
    assert exc.value.status_code == 401  # 401, not 429


def test_ip_lockout_is_independent_of_the_account(monkeypatch):
    _mock_upstream(monkeypatch, _Resp(400, {}))
    for _ in range(rate_limit.LOGIN_MAX_FAILURES):
        with pytest.raises(HTTPException):
            auth_login.login_user(email="victim@example.test", password="x", remote_ip="9.9.9.9")

    # A different account from the SAME IP is now blocked by the IP counter.
    with pytest.raises(HTTPException) as exc:
        auth_login.login_user(email="other@example.test", password="x", remote_ip="9.9.9.9")
    assert exc.value.status_code == 429

    # The original account from a DIFFERENT IP is still blocked by its own counter.
    with pytest.raises(HTTPException) as exc:
        auth_login.login_user(email="victim@example.test", password="x", remote_ip="8.8.8.8")
    assert exc.value.status_code == 429


def test_email_not_confirmed_never_counts_toward_lockout(monkeypatch):
    # Login gates on the password only — there is no email-confirmation step on
    # the sign-in screen. GoTrue only emits email_not_confirmed after a correct
    # password, so it must not count toward the lockout or escalate to 429. It
    # is surfaced as a generic 401 (no code-entry prompt on the client).
    _mock_upstream(monkeypatch, _Resp(400, {"error_code": "email_not_confirmed"}))
    for _ in range(rate_limit.LOGIN_MAX_FAILURES + 3):
        with pytest.raises(HTTPException) as exc:
            auth_login.login_user(email="c@example.test", password="x", remote_ip="3.3.3.3")
        assert exc.value.status_code == 401  # never escalates to 429


def test_upstream_unreachable_maps_502_and_never_counts(monkeypatch):
    _mock_upstream(monkeypatch, httpx.ConnectError("down"))
    for _ in range(rate_limit.LOGIN_MAX_FAILURES + 3):
        with pytest.raises(HTTPException) as exc:
            auth_login.login_user(email="d@example.test", password="x", remote_ip="4.4.4.4")
        assert exc.value.status_code == 502

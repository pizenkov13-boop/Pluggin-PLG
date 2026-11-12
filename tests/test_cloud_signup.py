"""signup_user provisioning must never leave an orphaned auth user behind."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import cloud.app.signup as signup_mod


class _FakeAdmin:
    def __init__(self) -> None:
        self.created_id = "user-xyz"
        self.deleted: list[str] = []

    def create_user(self, _payload):
        return type("R", (), {"user": type("U", (), {"id": self.created_id})()})()

    def delete_user(self, uid):
        self.deleted.append(uid)


class _FakeClient:
    def __init__(self) -> None:
        self.auth = type("A", (), {"admin": _FakeAdmin()})()


def _run_signup(client, **overrides):
    kwargs = dict(
        email="new@example.com",
        password="password-123",
        device_id=None,
        captcha_token=None,
        honeypot=None,
        remote_ip="1.2.3.4",
        accept_terms=True,
        confirm_age=True,
    )
    kwargs.update(overrides)
    return asyncio.run(signup_mod.signup_user(client, **kwargs))


def _patch_happy_path(monkeypatch):
    monkeypatch.setattr(signup_mod, "block_trial_farming", lambda *a, **k: None)
    import cloud.app.legal as legal_mod
    import cloud.app.analytics_ops as analytics_mod

    monkeypatch.setattr(legal_mod, "record_acceptance", lambda *a, **k: None)
    monkeypatch.setattr(analytics_mod, "save_attribution", lambda *a, **k: None)
    monkeypatch.setattr(analytics_mod, "track_event", lambda *a, **k: None)


def test_provisioning_error_deletes_orphan_and_raises_500(monkeypatch):
    client = _FakeClient()
    _patch_happy_path(monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("device_trial_claims write failed")

    monkeypatch.setattr(signup_mod, "block_trial_farming", boom)

    with pytest.raises(HTTPException) as exc:
        _run_signup(client)

    assert exc.value.status_code == 500
    assert client.auth.admin.deleted == ["user-xyz"]  # no orphan left behind


def test_httpexception_from_issue_code_is_preserved_not_masked(monkeypatch):
    client = _FakeClient()
    _patch_happy_path(monkeypatch)

    def mail_down(*_a, **_k):
        raise HTTPException(503, "mail unavailable")

    monkeypatch.setattr(signup_mod, "issue_code", mail_down)

    with pytest.raises(HTTPException) as exc:
        _run_signup(client)

    assert exc.value.status_code == 503  # original status kept, not wrapped as 500
    assert client.auth.admin.deleted == ["user-xyz"]


def test_happy_path_keeps_the_account(monkeypatch):
    client = _FakeClient()
    _patch_happy_path(monkeypatch)
    monkeypatch.setattr(signup_mod, "issue_code", lambda *a, **k: None)

    out = _run_signup(client)

    assert out["verification_required"] is True
    assert out["verification_user_id"] == "user-xyz"
    assert client.auth.admin.deleted == []

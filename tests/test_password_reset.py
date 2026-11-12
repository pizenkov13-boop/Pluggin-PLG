from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from cloud.app import email
from cloud.app import password_reset


class _ResetTable:
    def __init__(self, client: "_FakeClient") -> None:
        self.client = client
        self.operation = "select"
        self.payload: dict[str, object] | None = None

    def select(self, *_args: object) -> "_ResetTable":
        self.operation = "select"
        return self

    def eq(self, *_args: object) -> "_ResetTable":
        return self

    def maybe_single(self) -> "_ResetTable":
        return self

    def upsert(self, payload: dict[str, object], **_kwargs: object) -> "_ResetTable":
        self.operation = "upsert"
        self.payload = payload
        return self

    def update(self, payload: dict[str, object]) -> "_ResetTable":
        self.operation = "update"
        self.payload = payload
        return self

    def delete(self) -> "_ResetTable":
        self.operation = "delete"
        return self

    def execute(self) -> SimpleNamespace:
        if self.operation == "select":
            return SimpleNamespace(data=self.client.row)
        if self.operation == "upsert":
            self.client.row = dict(self.payload or {})
        elif self.operation == "update":
            assert self.client.row is not None
            self.client.row.update(self.payload or {})
        elif self.operation == "delete":
            self.client.row = None
            self.client.deleted = True
        return SimpleNamespace(data=self.client.row)


class _FakeAdmin:
    def __init__(self) -> None:
        self.password_updates: list[tuple[str, dict[str, str]]] = []
        self.error: Exception | None = None

    def update_user_by_id(self, user_id: str, payload: dict[str, str]) -> None:
        if self.error:
            raise self.error
        self.password_updates.append((user_id, payload))


class _FakeClient:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self.row = row
        self.deleted = False
        self.admin = _FakeAdmin()
        self.auth = SimpleNamespace(admin=self.admin)

    def table(self, _name: str) -> _ResetTable:
        return _ResetTable(self)


@pytest.fixture(autouse=True)
def _reset_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(password_reset, "EMAIL_VERIFICATION_SECRET", "test-secret")


def _row(*, code: str = "123456", attempts: int = 0, expired: bool = False) -> dict[str, object]:
    expiry = datetime.now(timezone.utc) + timedelta(minutes=-1 if expired else 5)
    return {
        "user_id": "user-1",
        "code_hash": password_reset._hash("user-1", code),
        "expires_at": expiry.isoformat(),
        "attempts": attempts,
    }


def test_issue_reset_code_stores_hashed_code_and_uses_unisender_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    sent: dict[str, str] = {}
    monkeypatch.setattr(password_reset, "_user_id", lambda *_args: "user-1")
    monkeypatch.setattr(
        password_reset,
        "send_password_reset_code",
        lambda _client, *, to, code, user_id: sent.update(to=to, code=code, user_id=user_id) or True,
    )

    password_reset.issue_reset_code(client, email=" Person@Example.com ")

    assert sent == {"to": "person@example.com", "code": sent["code"], "user_id": "user-1"}
    assert len(sent["code"]) == 6
    assert sent["code"].isdigit()
    assert client.row is not None
    assert client.row["code_hash"] == password_reset._hash("user-1", sent["code"])
    assert client.row["code_hash"] != sent["code"]


def test_reset_sender_uses_dedicated_unisender_template(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, object] = {}
    monkeypatch.setattr(email, "UNISENDER_API_KEY", "test-key")
    monkeypatch.setattr(email, "UNISENDER_PASSWORD_RESET_TEMPLATE_ID", "reset-template-id")
    monkeypatch.setattr(
        email,
        "_send_unisender",
        lambda **kwargs: sent.update(kwargs) or SimpleNamespace(status_code=200, text="ok"),
    )
    monkeypatch.setattr(email, "_log_notification", lambda *_args, **_kwargs: None)

    assert email.send_password_reset_code(_FakeClient(), to="person@example.com", code="123456", user_id="user-1")
    assert sent["template_id"] == "reset-template-id"
    assert sent["substitutions"] == {"code": "123456"}
    assert sent["to"] == "person@example.com"


def test_complete_reset_accepts_correct_code_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(_row())
    monkeypatch.setattr(password_reset, "_user_id", lambda *_args: "user-1")

    result = password_reset.complete_reset(
        client, email="person@example.com", code="123456", new_password="new-password-123"
    )

    assert result["ok"] is True
    assert client.admin.password_updates == [("user-1", {"password": "new-password-123"})]
    assert client.deleted is True


def test_complete_reset_rejects_short_password_before_updating(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(_row())
    monkeypatch.setattr(password_reset, "_user_id", lambda *_args: "user-1")

    with pytest.raises(HTTPException, match="at least 8 characters") as error:
        password_reset.complete_reset(client, email="person@example.com", code="123456", new_password="short")

    assert error.value.status_code == 400
    assert client.admin.password_updates == []
    assert client.deleted is False


def test_complete_reset_rejects_expired_code(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(_row(expired=True))
    monkeypatch.setattr(password_reset, "_user_id", lambda *_args: "user-1")

    with pytest.raises(HTTPException, match="Invalid or expired") as error:
        password_reset.complete_reset(client, email="person@example.com", code="123456", new_password="new-password-123")

    assert error.value.status_code == 400
    assert client.admin.password_updates == []


def test_complete_reset_locks_after_maximum_wrong_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(_row())
    monkeypatch.setattr(password_reset, "_user_id", lambda *_args: "user-1")

    for expected_attempt in range(1, password_reset.EMAIL_VERIFICATION_MAX_ATTEMPTS + 1):
        with pytest.raises(HTTPException, match="Invalid or expired") as error:
            password_reset.complete_reset(client, email="person@example.com", code="654321", new_password="new-password-123")
        assert error.value.status_code == 400
        assert client.row is not None
        assert client.row["attempts"] == expected_attempt

    with pytest.raises(HTTPException, match="Too many incorrect") as error:
        password_reset.complete_reset(client, email="person@example.com", code="123456", new_password="new-password-123")

    assert error.value.status_code == 429
    assert client.admin.password_updates == []


def test_complete_reset_keeps_code_when_new_password_matches_old(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(_row())
    client.admin.error = Exception("New password should be different from the old password.")
    monkeypatch.setattr(password_reset, "_user_id", lambda *_args: "user-1")

    with pytest.raises(HTTPException, match="must be different") as error:
        password_reset.complete_reset(client, email="person@example.com", code="123456", new_password="new-password-123")

    assert error.value.status_code == 422
    assert client.deleted is False

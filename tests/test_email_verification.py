from __future__ import annotations

from cloud.app import email_verification


def test_verification_code_hash_is_user_bound(monkeypatch) -> None:
    monkeypatch.setattr(email_verification, "EMAIL_VERIFICATION_SECRET", "test-secret")

    first = email_verification._code_hash("user-a", "123456")

    assert first == email_verification._code_hash("user-a", "123456")
    assert first != email_verification._code_hash("user-b", "123456")
    assert first != email_verification._code_hash("user-a", "654321")

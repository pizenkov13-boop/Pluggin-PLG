"""Encrypted-at-rest cloud session storage + legacy plaintext migration."""

from __future__ import annotations

import json

import plg_session_store as store


def _redirect(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "SESSION_FILE", tmp_path / "session.dat")
    monkeypatch.setattr(store, "_LEGACY_PLAINTEXT_FILE", tmp_path / "legacy.json")
    monkeypatch.delenv("PLG_CLOUD_SESSION_FILE", raising=False)


def test_load_session_returns_empty_when_nothing_stored(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    assert store.load_session() == {}


def test_save_then_load_round_trips_and_is_not_plaintext_on_disk(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    session = {
        "access_token": "abc",
        "refresh_token": "xyz",
        "email": "person@example.com",
    }

    store.save_session(session)

    assert store.load_session() == session
    raw = (tmp_path / "session.dat").read_bytes()
    assert b"xyz" not in raw
    assert b"person@example.com" not in raw


def test_corrupt_encrypted_file_is_treated_as_no_session(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    (tmp_path / "session.dat").write_bytes(b"not a real dpapi blob")

    assert store.load_session() == {}


def test_legacy_plaintext_session_is_migrated_and_removed(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps({"access_token": "old-token", "email": "person@example.com"}),
        encoding="utf-8",
    )

    result = store.load_session()

    assert result == {"access_token": "old-token", "email": "person@example.com"}
    assert not legacy.exists(), "legacy plaintext file must be cleaned up after migration"
    migrated = tmp_path / "session.dat"
    assert migrated.is_file()
    assert b"old-token" not in migrated.read_bytes(), "migrated copy must be encrypted, not copied verbatim"
    # A second load must come from the migrated encrypted file, not re-read
    # (now-deleted) legacy plaintext.
    assert store.load_session() == result


def test_clear_session_removes_both_current_and_legacy_files(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    store.save_session({"access_token": "abc"})
    (tmp_path / "legacy.json").write_text("{}", encoding="utf-8")

    store.clear_session()

    assert not (tmp_path / "session.dat").exists()
    assert not (tmp_path / "legacy.json").exists()
    assert store.load_session() == {}


def test_session_file_override_uses_plaintext_for_dev_debugging(tmp_path, monkeypatch):
    override = tmp_path / "custom-session.json"
    monkeypatch.setenv("PLG_CLOUD_SESSION_FILE", str(override))

    store.save_session({"access_token": "abc"})

    assert json.loads(override.read_text(encoding="utf-8")) == {"access_token": "abc"}
    assert store.load_session() == {"access_token": "abc"}

"""Tests for desktop auto-update helpers — incl. supply-chain signature guard."""

from __future__ import annotations

import base64
import hashlib

import pytest

import plg_updater
from plg_updater import _parse_version, _signed_message, verify_update_signature


def test_parse_version_order():
    assert _parse_version("1.0.0") < _parse_version("1.0.1")
    assert _parse_version("1.2.0") < _parse_version("2.0.0")


def test_parse_version_invalid():
    assert _parse_version("beta") == (0, 0, 0)


# --- update signature (Ed25519 supply-chain guard) -----------------------------


@pytest.fixture()
def keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode()
    return priv, pub_b64


def _sign(priv, version: str, sha: str) -> str:
    return base64.b64encode(priv.sign(_signed_message(version, sha))).decode()


def test_valid_signature_verifies(monkeypatch, keypair):
    priv, pub_b64 = keypair
    monkeypatch.setenv("PLG_UPDATE_PUBKEY", pub_b64)
    sha = hashlib.sha256(b"real binary").hexdigest()
    sig = _sign(priv, "1.2.0", sha)
    assert verify_update_signature("1.2.0", sha, sig) is True


def test_tampered_fields_fail(monkeypatch, keypair):
    priv, pub_b64 = keypair
    monkeypatch.setenv("PLG_UPDATE_PUBKEY", pub_b64)
    sha = hashlib.sha256(b"real binary").hexdigest()
    sig = _sign(priv, "1.2.0", sha)
    # attacker swaps the binary (different sha) but reuses the signature
    other = hashlib.sha256(b"malware").hexdigest()
    assert verify_update_signature("1.2.0", other, sig) is False
    # or bumps the version
    assert verify_update_signature("1.3.0", sha, sig) is False
    # or forges a signature
    assert verify_update_signature("1.2.0", sha, base64.b64encode(b"x" * 64).decode()) is False


def test_signature_from_wrong_key_fails(monkeypatch, keypair):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    _, pub_b64 = keypair
    monkeypatch.setenv("PLG_UPDATE_PUBKEY", pub_b64)  # trusted key
    attacker = Ed25519PrivateKey.generate()  # different key
    sha = hashlib.sha256(b"binary").hexdigest()
    sig = _sign(attacker, "1.2.0", sha)
    assert verify_update_signature("1.2.0", sha, sig) is False


def test_no_pubkey_means_no_verification(monkeypatch, keypair):
    priv, _ = keypair
    monkeypatch.delenv("PLG_UPDATE_PUBKEY", raising=False)
    sha = hashlib.sha256(b"binary").hexdigest()
    sig = _sign(priv, "1.2.0", sha)
    # verify fails closed without a configured key
    assert verify_update_signature("1.2.0", sha, sig) is False


def _fake_stream(monkeypatch, payload: bytes):
    class _Resp:
        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield payload

    class _Ctx:
        def __enter__(self):
            return _Resp()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(plg_updater.httpx, "stream", lambda *a, **k: _Ctx())


def test_download_rejects_missing_checksum(monkeypatch, tmp_path):
    payload = b"new binary"
    monkeypatch.setattr(
        plg_updater, "check_for_updates",
        lambda: {"ok": True, "update_available": True, "latest": "1.2.0",
                 "url": "https://x/PLG.exe", "sha256": "", "signature": ""},
    )
    _fake_stream(monkeypatch, payload)
    out = plg_updater.download_update(dest=tmp_path / "u.exe")
    assert out["ok"] is False and out["error_type"] == "validation"
    assert not (tmp_path / "u.exe").exists()  # rejected download is cleaned up


def test_download_rejects_bad_signature_when_key_set(monkeypatch, tmp_path, keypair):
    _, pub_b64 = keypair
    monkeypatch.setenv("PLG_UPDATE_PUBKEY", pub_b64)
    payload = b"new binary"
    sha = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(
        plg_updater, "check_for_updates",
        lambda: {"ok": True, "update_available": True, "latest": "1.2.0",
                 "url": "https://x/PLG.exe", "sha256": sha,
                 "signature": base64.b64encode(b"forged" * 11).decode()[:88]},
    )
    _fake_stream(monkeypatch, payload)
    out = plg_updater.download_update(dest=tmp_path / "u.exe")
    assert out["ok"] is False and out["error_type"] == "security"
    assert not (tmp_path / "u.exe").exists()


def test_download_accepts_valid_signed_update(monkeypatch, tmp_path, keypair):
    priv, pub_b64 = keypair
    monkeypatch.setenv("PLG_UPDATE_PUBKEY", pub_b64)
    payload = b"new binary v1.2.0"
    sha = hashlib.sha256(payload).hexdigest()
    sig = _sign(priv, "1.2.0", sha)
    monkeypatch.setattr(
        plg_updater, "check_for_updates",
        lambda: {"ok": True, "update_available": True, "latest": "1.2.0",
                 "url": "https://x/PLG.exe", "sha256": sha, "signature": sig},
    )
    _fake_stream(monkeypatch, payload)
    out = plg_updater.download_update(dest=tmp_path / "u.exe")
    assert out["ok"] is True and out["signed"] is True
    assert (tmp_path / "u.exe").read_bytes() == payload

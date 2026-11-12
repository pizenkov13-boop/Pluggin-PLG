"""Windows DPAPI wrapper used to encrypt the desktop client's cloud session."""

from __future__ import annotations

import pytest

import plg_dpapi


def test_available_on_this_platform():
    # This suite runs on Windows; the shipped build is Windows-only too.
    assert plg_dpapi.available() is True


def test_protect_unprotect_round_trip():
    secret = b'{"access_token":"abc","refresh_token":"xyz","email":"person@example.com"}'
    blob = plg_dpapi.protect(secret)

    assert blob != secret
    assert b"person@example.com" not in blob
    assert plg_dpapi.unprotect(blob) == secret


def test_empty_payload_round_trips():
    assert plg_dpapi.unprotect(plg_dpapi.protect(b"")) == b""


def test_unprotect_rejects_a_corrupt_blob():
    with pytest.raises(OSError):
        plg_dpapi.unprotect(b"not a real dpapi blob")


def test_unprotect_rejects_plaintext_json():
    """A pre-migration plaintext session file must not be silently accepted
    as if it were an encrypted blob."""
    with pytest.raises(OSError):
        plg_dpapi.unprotect(b'{"access_token": "abc"}')

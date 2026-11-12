"""Persist cloud auth session (access + refresh tokens, email) locally.

Encrypted at rest with Windows DPAPI (see ``plg_dpapi.py``), tied to the
signed-in Windows user — no separate password or key for PLG to manage. The
file lives in the per-user profile (``user_data_dir()``), not next to
PLG.exe, so a portable/shared/synced install folder never carries a live
session.

A one-time migration reads the old plaintext ``plg_cloud_session.json`` that
shipped before this file existed (next to the exe, unencrypted — access
token, refresh token and email all in the open), re-saves it through the new
encrypted path, and deletes the old copy. Existing signed-in users are not
logged out by the upgrade.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import plg_dpapi
from plg_paths import app_dir, user_data_dir

logger = logging.getLogger("plg.session_store")

SESSION_FILE = user_data_dir() / "plg_cloud_session.dat"
_LEGACY_PLAINTEXT_FILE = app_dir() / "plg_cloud_session.json"


def _override_path() -> Path | None:
    custom = os.getenv("PLG_CLOUD_SESSION_FILE", "").strip()
    return Path(custom) if custom else None


def load_session() -> dict[str, Any]:
    override = _override_path()
    if override is not None:
        return _read_plaintext(override)

    data = _read_encrypted(SESSION_FILE)
    if data:
        return data

    # Nothing at the new location yet — check for a pre-upgrade plaintext
    # session and migrate it in place so the user is not silently logged out.
    legacy = _read_plaintext(_LEGACY_PLAINTEXT_FILE)
    if legacy:
        save_session(legacy)
        try:
            _LEGACY_PLAINTEXT_FILE.unlink()
        except OSError:
            logger.warning("could not remove legacy plaintext session file")
        return legacy

    return {}


def save_session(data: dict[str, Any]) -> None:
    override = _override_path()
    path = override if override is not None else SESSION_FILE
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    if override is not None or not plg_dpapi.available():
        # Explicit dev/test override, or a non-Windows dev run of a
        # Windows-only shipped build: plaintext, not a real user's machine.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plg_dpapi.protect(payload))


def clear_session() -> None:
    for path in (_override_path() or SESSION_FILE, _LEGACY_PLAINTEXT_FILE):
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            logger.warning("could not remove session file %s", path)


def _read_plaintext(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_encrypted(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    if not plg_dpapi.available():
        # A dev run reading a real user's encrypted file: nothing to decrypt
        # with, and no way to prove it's plaintext either. Treat as absent.
        return {}
    try:
        payload = plg_dpapi.unprotect(raw)
        data = json.loads(payload.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        # Corrupt blob, protected by a different Windows user/machine, or a
        # stray plaintext file at the new path (e.g. from PLG_CLOUD_SESSION_FILE
        # testing) — never crash the app over a stored session, just treat it
        # as absent and let the user sign in again.
        return {}

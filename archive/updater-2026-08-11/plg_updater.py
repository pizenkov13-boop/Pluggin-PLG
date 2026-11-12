"""Desktop auto-update — check manifest, verify signature, download new PLG.exe.

Security model (supply-chain): the manifest is served over HTTPS, but TLS alone
only proves you reached *a* server — not that the payload is authentic if that
server (or its CDN / a release pipeline) is compromised. So every update is
cryptographically SIGNED with an Ed25519 private key held offline by the
developer; the matching PUBLIC key is baked into the client build
(``PLG_UPDATE_PUBKEY`` in .env.release — public keys are safe to embed). The
client refuses any update whose signature over ``version + sha256`` doesn't
verify against that key, so a spoofed/compromised manifest can't push malware.
The sha256 alone is self-referential (same manifest) — the signature is what
makes it trustworthy. Generate keys + sign releases with scripts/sign_release.py.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from app_config import app_version
from plg_paths import app_dir, is_frozen

logger = logging.getLogger("plg.updater")

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def _update_public_key():
    """Baked-in Ed25519 public key (base64, 32 raw bytes) or None if unset."""
    raw = os.getenv("PLG_UPDATE_PUBKEY", "").strip()
    if not raw:
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        return Ed25519PublicKey.from_public_bytes(base64.b64decode(raw))
    except Exception as exc:  # noqa: BLE001 — malformed key must not crash the app
        logger.error("PLG_UPDATE_PUBKEY malformed — cannot verify updates: %s", exc)
        return None


def _signed_message(version: str, sha256_hex: str) -> bytes:
    """The exact bytes the release signer signs — binds version to the binary."""
    return f"{version.strip()}\n{sha256_hex.strip().lower()}".encode("utf-8")


def verify_update_signature(version: str, sha256_hex: str, signature_b64: str) -> bool:
    """True only when the signature verifies against the baked-in public key."""
    pub = _update_public_key()
    if pub is None or not signature_b64:
        return False
    try:
        from cryptography.exceptions import InvalidSignature

        try:
            pub.verify(base64.b64decode(signature_b64), _signed_message(version, sha256_hex))
            return True
        except InvalidSignature:
            return False
    except Exception as exc:  # noqa: BLE001
        logger.error("Update signature check errored: %s", exc)
        return False


def _parse_version(text: str) -> tuple[int, int, int]:
    match = _VERSION_RE.match((text or "").strip())
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def manifest_url() -> str:
    return (
        os.getenv("PLG_UPDATE_MANIFEST_URL", "").strip()
        or "https://api.example.com/v1/release/manifest"
    )


def fetch_manifest() -> dict[str, Any]:
    url = manifest_url()
    try:
        resp = httpx.get(url, timeout=12.0, headers={"X-PLG-Version": app_version()})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            return {"ok": False, "error": "Invalid manifest.", "error_type": "network"}
        return {"ok": True, **data}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc), "error_type": "network"}
    except json.JSONDecodeError:
        return {"ok": False, "error": "Manifest is not JSON.", "error_type": "network"}


def check_for_updates() -> dict[str, Any]:
    current = app_version()
    manifest = fetch_manifest()
    if not manifest.get("ok"):
        return manifest

    latest = str(manifest.get("version") or "").strip()
    if not latest:
        return {"ok": True, "update_available": False, "current": current}

    newer = _parse_version(latest) > _parse_version(current)
    return {
        "ok": True,
        "update_available": newer,
        "current": current,
        "latest": latest,
        "url": manifest.get("url"),
        "notes": manifest.get("notes"),
        "sha256": manifest.get("sha256"),
        "signature": manifest.get("signature"),
        "mandatory": bool(manifest.get("mandatory")),
    }


def download_update(dest: Path | None = None) -> dict[str, Any]:
    info = check_for_updates()
    if not info.get("ok"):
        return info
    if not info.get("update_available"):
        return {"ok": True, "message": "Already on latest version.", "current": info.get("current")}

    url = str(info.get("url") or "").strip()
    if not url.startswith("https://"):
        return {"ok": False, "error": "Update URL missing.", "error_type": "config"}

    target = dest or (app_dir() / "PLG_update.exe")
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as resp:
            resp.raise_for_status()
            digest = hashlib.sha256()
            with target.open("wb") as handle:
                for chunk in resp.iter_bytes():
                    handle.write(chunk)
                    digest.update(chunk)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc), "error_type": "network"}

    got = digest.hexdigest().lower()
    expected = str(info.get("sha256") or "").strip().lower()
    version = str(info.get("latest") or "")
    signature = str(info.get("signature") or "")

    # Checksum is MANDATORY now — never apply a binary we can't verify at all
    # (an omitted checksum used to skip the check and accept any download).
    if not expected:
        target.unlink(missing_ok=True)
        return {"ok": False, "error": "Update is missing its checksum — refusing to apply.", "error_type": "validation"}
    if got != expected:
        target.unlink(missing_ok=True)
        return {"ok": False, "error": "Checksum mismatch — update rejected.", "error_type": "validation"}

    # Signature is REQUIRED whenever a public key is baked into this build. This
    # is the real supply-chain guard: a compromised manifest server controls the
    # url + sha256, but cannot forge a signature without the offline private key.
    pub_configured = _update_public_key() is not None
    if pub_configured:
        if not verify_update_signature(version, expected, signature):
            target.unlink(missing_ok=True)
            return {
                "ok": False,
                "error": "Update signature invalid — refusing to apply (possible tampering).",
                "error_type": "security",
            }
    else:
        logger.warning(
            "PLG_UPDATE_PUBKEY not set — update signature NOT verified. Set it in "
            ".env.release and sign releases (scripts/sign_release.py) before shipping."
        )

    return {
        "ok": True,
        "path": str(target),
        "version": info.get("latest"),
        "signed": pub_configured,
        "message": f"Downloaded v{info.get('latest')} — restart to apply.",
    }


def apply_downloaded_update(download_path: str | None = None) -> dict[str, Any]:
    """Stage a swap script — user restarts PLG.exe to finish."""
    if not is_frozen():
        return {"ok": False, "error": "Updates apply only to PLG.exe builds.", "error_type": "config"}

    src = Path(download_path or app_dir() / "PLG_update.exe")
    if not src.is_file():
        return {"ok": False, "error": "Download PLG_update.exe first.", "error_type": "not_found"}

    exe = Path(sys.executable).resolve()
    bat = app_dir() / "apply_plg_update.bat"
    bat.write_text(
        "\n".join(
            [
                "@echo off",
                "echo Applying Pluggin' update...",
                f'timeout /t 2 /nobreak >nul',
                f'copy /Y "{src}" "{exe}"',
                f'del "{src}"',
                f'start "" "{exe}"',
                "del \"%~f0\"",
            ]
        ),
        encoding="utf-8",
    )
    subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(app_dir()), close_fds=True)
    return {"ok": True, "message": "Restarting with new version…"}


def open_download_folder() -> dict[str, Any]:
    path = app_dir()
    try:
        os.startfile(path)  # noqa: S606
        return {"ok": True, "path": str(path)}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "error_type": "os_error"}

"""Windows DPAPI wrapper — encrypt small secrets to the current Windows user.

Uses ``CryptProtectData``/``CryptUnprotectData`` (part of Windows since XP),
via stdlib ``ctypes`` only — no extra dependency. A secret protected this way
can only be decrypted by the same Windows user account on the same machine;
there is no separate password or key for PLG to generate, store, or lose.

Only available on win32. Callers must check :func:`available` first and
decide their own fallback (PLG's shipped build is Windows-only, so callers
outside win32 are dev/CI runs, not real users).
"""

from __future__ import annotations

import sys

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


def available() -> bool:
    return sys.platform == "win32"


def protect(data: bytes) -> bytes:
    """Encrypt ``data`` for the current Windows user. Raises OSError on failure."""
    return _run(data, encrypt=True)


def unprotect(blob: bytes) -> bytes:
    """Decrypt a blob previously protected for the current Windows user.

    Raises OSError if the blob is corrupt, was protected by a different user
    account or machine, or DPAPI otherwise refuses it.
    """
    return _run(blob, encrypt=False)


def _run(data: bytes, *, encrypt: bool) -> bytes:
    if not available():
        raise OSError("Windows DPAPI is not available on this platform.")

    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(raw: bytes) -> tuple[_DATA_BLOB, ctypes.Array]:
        # Keep the backing buffer alive with the blob — pbData just points into it.
        buf = ctypes.create_string_buffer(raw, len(raw))
        return _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_blob, _keepalive = _blob(data)
    out_blob = _DATA_BLOB()

    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    ok = fn(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        verb = "protect" if encrypt else "unprotect"
        raise OSError(f"DPAPI {verb} failed (Windows error {ctypes.GetLastError()}).")

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(out_blob.pbData)

"""Small privacy helpers for data minimisation and safe operational logging."""

from __future__ import annotations

import re


_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[=:]\s*)[^\s,;]+"
)
_WINDOWS_USER_PATH = re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s]+")


def redact_sensitive_text(value: str | None, *, max_chars: int | None = None) -> str:
    """Remove common credentials/identifiers before support or app logging."""

    text = str(value or "")
    text = _EMAIL.sub("[email]", text)
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _JWT.sub("[token]", text)
    text = _SECRET_ASSIGNMENT.sub(r"\1[redacted]", text)
    text = _WINDOWS_USER_PATH.sub(r"[local-path]", text)
    return text[:max_chars] if max_chars is not None else text


def exception_kind(exc: BaseException) -> str:
    """Return a non-sensitive error label suitable for production logs."""

    return type(exc).__name__ or "Exception"

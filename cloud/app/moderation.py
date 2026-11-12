"""Prompt content moderation — illegal / NSFW / violence."""

from __future__ import annotations

import re

from fastapi import HTTPException

# Blocklist — extend via env file in production
_BLOCKED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(child\s*porn|cp\b|underage)",
        r"\b(terrorist|bomb\s*making|how\s+to\s+make\s+a\s+bomb)",
        r"\b(rape|molest)",
        r"\b(nazi\s+propaganda|holocaust\s+denial)",
        r"\b(snuff|bestiality|necrophil)",
    )
)

_WARN_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(nsfw|porn|xxx|hentai)",
        r"\b(cocaine|heroin|meth)\b",
    )
)


def moderate_prompt(text: str) -> str:
    """Block illegal content; log warnings for grey-area terms."""
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(text):
            raise HTTPException(
                400,
                "Prompt blocked by content policy. Describe music style only.",
            )

    for pattern in _WARN_PATTERNS:
        if pattern.search(text):
            # Soft warn — still allow beat prompts that mention genre slang
            # but strip obvious NSFW-only requests
            if re.search(r"\b(make|generate|create)\b.*\b(porn|xxx)\b", text, re.I):
                raise HTTPException(400, "Prompt blocked by content policy.")
    return text

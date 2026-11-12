"""Prompt sanitization + length limits + content moderation."""

from __future__ import annotations

import re

from fastapi import HTTPException

from cloud.app.config import MAX_PROMPT_CHARS
from cloud.app.moderation import moderate_prompt

_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "you are now",
    "disregard",
    "jailbreak",
    "developer mode",
)


def sanitize_prompt(prompt: str) -> str:
    text = (prompt or "").strip()
    if not text:
        raise HTTPException(400, "Prompt cannot be empty.")
    if len(text) > MAX_PROMPT_CHARS:
        raise HTTPException(400, f"Prompt too long (max {MAX_PROMPT_CHARS} characters).")

    lower = text.lower()
    for marker in _INJECTION_MARKERS:
        if marker in lower:
            raise HTTPException(400, "Prompt rejected by safety filter.")

    text = moderate_prompt(text)

    # Collapse excessive whitespace / control chars
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"\s{12,}", " ", text)
    return text

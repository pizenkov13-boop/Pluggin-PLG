"""Prompt locale bridge — native language → system tags → LLM-ready English."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from plg_paths import bundle_dir

VALID_LOCALES = frozenset({"en", "ru", "es", "pt", "zh", "ja", "fr", "de", "ar"})
_TAGS_FILE = bundle_dir() / "assets" / "prompt_tags.json"


@lru_cache(maxsize=1)
def _load_tags() -> dict[str, Any]:
    if not _TAGS_FILE.is_file():
        return {"system_tags": {}, "mood_words": {}}
    return json.loads(_TAGS_FILE.read_text(encoding="utf-8"))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _match_phrases(text: str, phrases: list[str]) -> bool:
    for phrase in phrases:
        p = phrase.strip().lower()
        if len(p) < 2:
            continue
        if p in text:
            return True
    return False


def detect_tags(prompt: str, locale: str | None = None) -> list[str]:
    """Return system tags (RAGE, PHONK, …) found in prompt across all languages."""
    data = _load_tags()
    text = _normalize_text(prompt)
    found: list[str] = []

    for tag, by_lang in (data.get("system_tags") or {}).items():
        if not isinstance(by_lang, dict):
            continue
        for words in by_lang.values():
            if isinstance(words, list) and _match_phrases(text, words):
                found.append(str(tag))
                break

    loc = (locale or "en").lower()
    if loc not in VALID_LOCALES:
        loc = "en"
    for mood, by_lang in (data.get("mood_words") or {}).items():
        words = by_lang.get(loc) or by_lang.get("en") or []
        if isinstance(words, list) and _match_phrases(text, words):
            found.append(f"MOOD_{str(mood).upper()}")

    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for tag in found:
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def prepare_prompt_for_llm(
    prompt: str,
    locale: str | None = None,
) -> dict[str, Any]:
    """Build LLM prompt + metadata. Original prompt preserved for UI."""
    original = (prompt or "").strip()
    loc = (locale or "en").lower()
    if loc not in VALID_LOCALES:
        loc = "en"

    tags = detect_tags(original, loc)
    tag_str = " ".join(tags) if tags else ""

    if tags:
        llm_prompt = (
            f"{original} | PLG tags: {tag_str} | "
            f"Produce trap/rage MIDI pattern matching tags. BPM from genre norms."
        )
    elif loc != "en":
        llm_prompt = (
            f"{original} | (user locale: {loc}) | "
            f"Interpret intent in English trap/rage production terms."
        )
    else:
        llm_prompt = original

    return {
        "user_prompt": original,
        "plg_locale": loc,
        "plg_style_tags": tags,
        "llm_prompt": llm_prompt,
    }


def apply_prompt_metadata(pattern: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Attach locale fields to pattern after generation."""
    pattern["user_prompt"] = meta.get("user_prompt") or pattern.get("user_prompt", "")
    pattern["plg_locale"] = meta.get("plg_locale", "en")
    tags = meta.get("plg_style_tags")
    if tags:
        pattern["plg_style_tags"] = tags
    return pattern

"""Smart prompt planning — detailed user brief → structured musical plan → Flash JSON.

When ``smart_plan`` is enabled (auto or manual), distils long producer prompts into a
compact JSON brief before the main ``generate_pattern`` call. Uses ``complete_json``
(Gemini / Claude today; swap via ``PLG_PLANNER_MODEL`` for DeepSeek-R1 later).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Literal

logger = logging.getLogger("plg.beat_planner")

DetailLevel = Literal["short", "medium", "detailed"]

_BEAT_BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "bpm": {"type": "number"},
        "key": {"type": "string"},
        "style": {"type": "string"},
        "mood": {"type": "array", "items": {"type": "string"}},
        "bars": {"type": "integer"},
        "melody": {
            "type": "object",
            "properties": {
                "register": {"type": "string"},
                "motion": {"type": "string"},
                "hook_bars": {"type": "integer"},
                "avoid": {"type": "string"},
            },
        },
        "drums": {
            "type": "object",
            "properties": {
                "kick": {"type": "string"},
                "hats": {"type": "string"},
                "sub_808": {"type": "string"},
                "drums_enter_bar": {"type": "integer"},
            },
        },
        "mix_vibe": {"type": "string"},
        "producer_note": {"type": "string"},
    },
    "required": ["bpm", "key", "style", "producer_note"],
}

_PLANNER_SYSTEM = """\
You are a Grammy-level dark trap / rage / opium producer brain — you plan records,
not loops. Read the user's beat request and output ONLY a compact JSON musical
brief for a MIDI pattern generator. Decide like a hit-maker: one idea rules the
beat, the hook must pass the hum test, the beat is built around a vocalist.

Rules:
- Pick ONE key (e.g. E minor, F# phrygian) and ONE BPM (130–165 for rage unless stated) — commit.
- Melody: memorable hook with an arc (start → peak → resolve), semitone motion,
  sustained notes, rests for the vocal — NOT random arpeggios.
- Drums: kick on 1+3 locking with the 808's bounce, backbeat snare, trap hats with
  rolls at phrase turns — drums may enter bar 9 for tension → release.
- sub_808: walks the changes, glides at phrase ends, mono — never a one-pitch drone.
- mix_vibe: space, reverb, dark LPF lead, gritty 808 — stadium trap aesthetic;
  drums in front, music behind, midrange open for the vocal.
- producer_note: one sentence the note-writer must follow — name the beat's ONE
  peak moment (drum cut, octave lift, stop-time).
- Never mention artist names in output — describe sound only.
"""

_DETAIL_SIGNALS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\b\d{2,3}\s*bpm\b", re.I), 18),
    (re.compile(r"\b(bpm|tempo)\b", re.I), 8),
    (re.compile(r"\b(minor|major|phrygian|dorian|key)\b", re.I), 14),
    (re.compile(r"\b(reverb|atmospheric|cinematic|psychedelic|spacey|dark)\b", re.I), 10),
    (re.compile(r"\b(melody|lead|hook|808|kick|snare|hat)\b", re.I), 6),
    (re.compile(r"\b(bar|bars|arrangement|verse|drop)\b", re.I), 8),
)


def prompt_detail_score(prompt: str) -> int:
    """0–100 — how «producer-detailed» the prompt is (for UI meter + auto plan)."""
    text = (prompt or "").strip()
    if not text:
        return 0
    words = re.findall(r"[^\s]+", text)
    score = min(36, len(words) * 2)
    if len(text) >= 120:
        score += 12
    if len(text) >= 220:
        score += 10
    for pattern, weight in _DETAIL_SIGNALS:
        if pattern.search(text):
            score += weight
    return min(100, score)


def prompt_detail_level(prompt: str) -> DetailLevel:
    score = prompt_detail_score(prompt)
    if score < 32:
        return "short"
    if score < 58:
        return "medium"
    return "detailed"


def should_use_smart_plan(
    prompt: str,
    smart_plan: bool | None,
) -> bool:
    """``None`` = auto (medium/detailed prompts). ``False`` / ``True`` = manual."""
    if smart_plan is False:
        return False
    if smart_plan is True:
        return True
    return prompt_detail_level(prompt) in ("medium", "detailed")


def smart_plan_hint(prompt: str) -> dict[str, Any]:
    """UI payload — detail meter + recommended checkbox state."""
    level = prompt_detail_level(prompt)
    score = prompt_detail_score(prompt)
    return {
        "detail_level": level,
        "detail_score": score,
        "recommended": level in ("medium", "detailed"),
    }


def plan_beat_brief(prompt: str, *, locale: str | None = None) -> dict[str, Any]:
    """Distil user prompt → structured brief (planner LLM)."""
    from llm_client import complete_json

    loc = (locale or "en").strip()
    user = (
        f"User prompt ({loc}):\n{prompt.strip()}\n\n"
        "Output the musical brief JSON for PLG pattern generation."
    )
    brief = complete_json(
        user,
        _PLANNER_SYSTEM,
        _BEAT_BRIEF_SCHEMA,
        temperature=0.25,
        max_tokens=1024,
    )
    logger.info(
        "Smart plan: bpm=%s key=%s style=%s",
        brief.get("bpm"),
        brief.get("key"),
        brief.get("style"),
    )
    return brief


def brief_to_llm_addendum(brief: dict[str, Any]) -> str:
    return (
        "PRODUCER BRIEF — follow exactly when writing MIDI JSON:\n"
        + json.dumps(brief, ensure_ascii=False, indent=2)
    )


def apply_brief_metadata(pattern: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    """Lock core fields from the planner so Flash cannot drift."""
    if (
        isinstance(brief.get("bpm"), (int, float))
        and not pattern.get("plg_bpm_locked_from_midi")
    ):
        pattern["bpm"] = int(round(float(brief["bpm"])))
    if brief.get("key"):
        pattern["plg_key"] = str(brief["key"])
    if brief.get("style"):
        pattern["style"] = str(brief["style"])
    pattern["plg_beat_brief"] = brief
    pattern["plg_smart_plan"] = True
    return pattern


def planner_enabled() -> bool:
    raw = os.environ.get("PLG_SMART_PLAN", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")

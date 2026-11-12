"""Section-aware roles for every analysed MIDI phrase.

This is the replacement planning layer for the old default-grid-first path.
It writes no notes: the rhythm composer consumes these roles in a later pass.
"""
from __future__ import annotations

from typing import Any

BEATS_PER_BAR = 4.0


def _section_at(arrangement: list[dict[str, Any]], beat: float) -> str:
    bar = int(beat // BEATS_PER_BAR)
    for section in arrangement:
        start = int(section.get("start_bar", 0) or 0)
        bars = int(section.get("bars", 0) or 0)
        if start <= bar < start + bars:
            return str(section.get("name") or "verse")
    return "verse"


def build_phrase_rhythm_plan(pattern: dict[str, Any]) -> dict[str, Any]:
    structure = pattern.get("plg_midi_structure")
    arrangement = pattern.get("plg_arrangement")
    if not isinstance(structure, dict) or structure.get("status") != "ok":
        return {"version": 1, "enabled": False, "reason": "missing_structure", "phrases": []}
    phrases = structure.get("phrases") if isinstance(structure.get("phrases"), list) else []
    confidence = float(structure.get("confidence", 0.0) or 0.0)
    if confidence < 0.58 or len(phrases) < 4:
        return {"version": 1, "enabled": False, "reason": "insufficient_coverage", "phrases": []}
    arrangement = arrangement if isinstance(arrangement, list) else []
    tension = {int(item.get("bar", 0)): float(item.get("value", 0) or 0) for item in structure.get("tension_curve", []) if isinstance(item, dict)}
    pause_starts = {round(float(item.get("next_onset", -999)), 2) for item in structure.get("pauses", []) if isinstance(item, dict) and float(item.get("strength", 0) or 0) >= .6}
    result: list[dict[str, Any]] = []
    previous = ""
    for index, phrase in enumerate(phrases):
        if not isinstance(phrase, dict):
            continue
        start, end = float(phrase.get("start", 0)), float(phrase.get("end", 0))
        section = _section_at(arrangement, start)
        end_bar = max(0, int((end - .01) // BEATS_PER_BAR))
        hot = tension.get(end_bar, 0.0) >= .32
        if round(start, 2) in pause_starts:
            role = "space"
        elif section in {"hook", "chorus", "drop"} and (index == 0 or _section_at(arrangement, max(0, start - .01)) != section):
            role = "landing"
        elif section in {"hook", "chorus", "drop"} and hot:
            role = "lift"
        elif index and phrase.get("contour") != (phrases[index - 1].get("contour") if isinstance(phrases[index - 1], dict) else None):
            role = "answer"
        else:
            role = "statement"
        # A groove can repeat once; the third identical function must become
        # an answer or a space so a 40-bar arrangement cannot be one loop.
        if role == previous == "statement" and index >= 2 and result[-2]["role"] == "statement":
            role = "answer"
        result.append({"id": str(phrase.get("id", f"P{index+1}")), "start": start, "end": end, "section": section, "role": role})
        previous = role
    return {"version": 1, "enabled": True, "confidence": round(confidence, 4), "phrases": result,
            "role_counts": {role: sum(p["role"] == role for p in result) for role in ("statement", "answer", "space", "lift", "landing")}}


def attach_phrase_rhythm_plan(pattern: dict[str, Any]) -> dict[str, Any]:
    plan = build_phrase_rhythm_plan(pattern)
    pattern["plg_phrase_rhythm_plan"] = plan
    return plan


__all__ = ["build_phrase_rhythm_plan", "attach_phrase_rhythm_plan"]

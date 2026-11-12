"""Phrase-aware, read-only priorities for pairing uploaded MIDI with drums.

The planner does not write MIDI or drum notes.  It reduces a dense melody to
musically meaningful *priorities*: one kick/808 anchor per phrase-sized idea
at most, plus optional response candidates for later drum layers.  Consumers
must retain their own density and safety invariants.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pattern_utils import track_notes

BEATS_PER_BAR = 4.0
_MIN_CONFIDENCE = 0.58


def _time(note: dict[str, Any]) -> float:
    return round(float(note.get("time_step", 0.0) or 0.0), 5)


def _length(note: dict[str, Any]) -> float:
    return max(0.03, float(note.get("length", 0.25) or 0.25))


def _nearest_onset(notes: list[dict[str, Any]], target: float, end: float) -> dict[str, Any] | None:
    candidates = [note for note in notes if target - 0.35 <= _time(note) < end]
    if not candidates:
        return None
    return min(candidates, key=lambda note: (abs(_time(note) - target), _time(note)))


def _phrase_anchor(notes: list[dict[str, Any]], phrase: dict[str, Any], tension: dict[int, float]) -> float | None:
    start, end = float(phrase["start"]), float(phrase["end"])
    members = [note for note in notes if start <= _time(note) < end]
    if not members:
        return None
    # Phrase beginning is the primary musical statement.  Strong downbeats,
    # longer notes and the local tension peak are candidates, never a reason
    # to spray a kick on every melody note.
    candidates: dict[float, float] = {}
    first = _nearest_onset(members, start, end)
    if first:
        candidates[_time(first)] = 7.0 - min(1.0, abs(_time(first) - start))
    for note in members:
        time = _time(note)
        within = time % BEATS_PER_BAR
        bar = int(time // BEATS_PER_BAR)
        score = 1.0 + min(1.5, _length(note)) + (float(note.get("velocity", 96) or 96) / 127.0)
        if within <= 0.12 or within >= 3.88:
            score += 2.5
        elif abs(within - 2.0) <= 0.12:
            score += 0.8
        score += 1.2 * tension.get(bar, 0.0)
        candidates[time] = max(candidates.get(time, 0.0), score)
    return max(candidates, key=lambda value: (candidates[value], -value))


def _response_anchor(notes: list[dict[str, Any]], phrase: dict[str, Any]) -> float | None:
    """Choose one late-phrase note for a future hat/snare response layer."""
    start, end = float(phrase["start"]), float(phrase["end"])
    target = start + (end - start) * 0.72
    return _nearest_onset(notes, target, end)


def build_midi_beat_plan(pattern: dict[str, Any]) -> dict[str, Any]:
    """Build deterministic phrase priorities; never mutate any note list."""
    structure = pattern.get("plg_midi_structure")
    notes = track_notes(pattern, "melody_lead")
    if not isinstance(structure, dict) or structure.get("status") != "ok" or not notes:
        return {"version": 1, "enabled": False, "reason": "missing_structure", "phrases": []}
    confidence = float(structure.get("confidence", 0.0) or 0.0)
    phrases = structure.get("phrases") if isinstance(structure.get("phrases"), list) else []
    if confidence < _MIN_CONFIDENCE or not phrases:
        return {
            "version": 1,
            "enabled": False,
            "reason": "low_confidence",
            "confidence": round(confidence, 4),
            "phrases": [],
        }

    tension = {
        int(row.get("bar", 0)): float(row.get("value", 0.0) or 0.0)
        for row in structure.get("tension_curve", [])
        if isinstance(row, dict)
    }
    used_bars: set[int] = set()
    kick808: list[float] = []
    responses: list[float] = []
    plan_phrases: list[dict[str, Any]] = []
    for phrase in phrases:
        if not isinstance(phrase, dict) or "start" not in phrase or "end" not in phrase:
            continue
        anchor = _phrase_anchor(notes, phrase, tension)
        phrase_bars = range(int(float(phrase["start"]) // BEATS_PER_BAR), int((float(phrase["end"]) - 1e-5) // BEATS_PER_BAR) + 1)
        role = "space" if not any(start in used_bars for start in phrase_bars) and phrase.get("note_events", 0) <= 2 else "support"
        if anchor is not None:
            bar = int(anchor // BEATS_PER_BAR)
            if bar not in used_bars:
                kick808.append(anchor)
                used_bars.add(bar)
            role = "support"
        response = _response_anchor(notes, phrase)
        response_time = _time(response) if response is not None else None
        if response_time is not None and response_time != anchor:
            responses.append(response_time)
            if tension.get(int(response_time // BEATS_PER_BAR), 0.0) >= 0.34:
                role = "answer"
        plan_phrases.append({
            "phrase_id": str(phrase.get("id", f"P{len(plan_phrases) + 1}")),
            "start": round(float(phrase["start"]), 5), "end": round(float(phrase["end"]), 5),
            "role": role, "kick808_anchor": anchor, "response_anchor": response_time,
        })

    spaces = [
        {"start": round(float(p["start"]), 5), "end": round(float(p["end"]), 5)}
        for p in structure.get("pauses", []) if isinstance(p, dict)
        and float(p.get("length", 0.0) or 0.0) >= 0.75
    ]
    return {
        "version": 1, "enabled": bool(kick808), "confidence": round(confidence, 4),
        "kick808_anchors": sorted(set(kick808)), "hat_response_anchors": sorted(set(responses)),
        "space_windows": spaces, "phrases": plan_phrases,
        "invariants": {"max_new_kick808_priority_per_bar": 1, "edits_notes": False, "edits_velocity": False, "allows_slides": False},
    }


def attach_midi_beat_plan(pattern: dict[str, Any]) -> dict[str, Any]:
    plan = build_midi_beat_plan(pattern)
    pattern["plg_midi_beat_plan"] = plan
    return plan


__all__ = ["build_midi_beat_plan", "attach_midi_beat_plan"]

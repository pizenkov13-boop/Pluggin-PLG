"""One observable rulebook for the producer techniques already in PLG.

The point is not to add every trick to every beat.  It makes the existing
techniques inspectable after generation, so a later director can choose a few
compatible moves deliberately instead of silently stacking contradictory ones.
"""
from __future__ import annotations

from typing import Any

from pattern_utils import track_notes


RULES = {
    "paired_low_end": "Every kick lands with an 808 attack; fixed kick/808 dynamics.",
    "phrase_pocket": "Kick/808 priorities follow MIDI phrases, not every note.",
    "short_808": "808 attacks are short and mono; slides are off by default.",
    "hat_energy": "Hat density grows only where the section/melody needs lift.",
    "melody_space": "Busy melody gets drum thinning and real pauses stay clear.",
    "section_contrast": "Verse, hook and drop cannot be one unchanged loop.",
    "transition_tension": "A pickup, fill or breath sets up a section landing.",
    "hook_identity": "The core melody is developed as phrases, not tiled blindly.",
}


def build_producer_rule_audit(pattern: dict[str, Any]) -> dict[str, Any]:
    """Return evidence for rules that actually happened in this export."""
    tracks = pattern.get("tracks") if isinstance(pattern.get("tracks"), dict) else {}
    kick = track_notes(pattern, "kick")
    bass = track_notes(pattern, "808_bass")
    hats = track_notes(pattern, "hi_hats")
    arrangement = pattern.get("plg_arrangement") if isinstance(pattern.get("plg_arrangement"), list) else []
    kick_times = {round(float(note.get("time_step", 0) or 0), 4) for note in kick}
    bass_times = {round(float(note.get("time_step", 0) or 0), 4) for note in bass}
    plan = pattern.get("plg_midi_beat_plan") if isinstance(pattern.get("plg_midi_beat_plan"), dict) else {}
    transitions = pattern.get("plg_midi_transitions") or pattern.get("plg_transition_plan") or []
    sections = {str(item.get("name")) for item in arrangement if isinstance(item, dict)}
    return {
        "version": 1,
        "rules": RULES,
        "evidence": {
            "paired_low_end": {"applied": bool(kick), "pairing_ratio": round(len(kick_times & bass_times) / max(1, len(kick_times)), 3)},
            "phrase_pocket": {"applied": bool(plan.get("enabled")), "anchors": len(plan.get("kick808_anchors") or [])},
            "short_808": {"applied": bool(bass), "max_length": round(max((float(note.get("length", 0) or 0) for note in bass), default=0.0), 3), "slides": sum(bool(note.get("slide")) for note in bass)},
            "hat_energy": {"applied": bool(hats), "notes": len(hats)},
            "melody_space": {"applied": bool(plan.get("space_windows")), "windows": len(plan.get("space_windows") or [])},
            "section_contrast": {"applied": len(sections) >= 3, "sections": sorted(sections)},
            "transition_tension": {"applied": bool(transitions), "events": len(transitions) if isinstance(transitions, list) else 0},
            "hook_identity": {"applied": bool(pattern.get("plg_midi_structure")), "phrases": len((pattern.get("plg_midi_structure") or {}).get("phrases") or [])},
        },
        "notes": "This is an audit, not a score. A rule must be deliberately chosen, not forced into every beat.",
    }


__all__ = ["RULES", "build_producer_rule_audit"]

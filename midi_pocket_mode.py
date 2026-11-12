"""Safe routing from a read-only MIDI passport to existing pocket choices."""
from __future__ import annotations

from typing import Any


def choose_midi_pocket_mode(passport: dict[str, Any] | None) -> dict[str, Any]:
    """Select a conservative reading or explicitly preserve the old default."""
    if not isinstance(passport, dict) or passport.get("status") != "ok":
        return {"enabled": False, "mode": "general", "reason": "missing_passport"}
    safety = passport.get("safety") if isinstance(passport.get("safety"), dict) else {}
    structure = passport.get("structure") if isinstance(passport.get("structure"), dict) else {}
    confidence = float(structure.get("confidence", 0.0) or 0.0)
    if not safety.get("tempo_reliable"):
        return {"enabled": False, "mode": "general", "reason": "tempo_not_reliable"}
    if not safety.get("key_reliable"):
        return {"enabled": False, "mode": "general", "reason": "key_not_reliable"}
    if confidence < 0.72:
        return {"enabled": False, "mode": "general", "reason": "structure_confidence_low"}
    role = passport.get("role") if isinstance(passport.get("role"), dict) else {}
    musical_role = str(role.get("musical_role") or "")
    modes = {
        "sustained_melody": "sustained_space",
        "chordal": "chordal_roots",
        "melodic_phrase": "phrase_answer",
    }
    mode = modes.get(musical_role)
    if mode is None:
        return {"enabled": False, "mode": "general", "reason": "role_not_supported"}
    return {
        "enabled": True,
        "mode": mode,
        "role": musical_role,
        "confidence": round(confidence, 4),
        "reason": "passport_safe",
    }


def attach_midi_pocket_mode(pattern: dict[str, Any]) -> dict[str, Any]:
    mode = choose_midi_pocket_mode(pattern.get("plg_midi_passport"))
    pattern["plg_midi_pocket_mode"] = mode
    return mode


__all__ = ["attach_midi_pocket_mode", "choose_midi_pocket_mode"]

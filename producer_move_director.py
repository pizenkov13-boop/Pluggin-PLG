"""Select a small compatible set of proven producer moves per beat."""
from __future__ import annotations

from typing import Any


def choose_moves(pattern: dict[str, Any], genre: str) -> dict[str, Any]:
    """Return <=4 concrete moves, never a kitchen-sink preset.

    This uses observable structure, so the choice remains deterministic and
    explainable.  The actual move functions continue to own their safety
    checks and MIDI lanes are never rewritten here.
    """
    structure = pattern.get("plg_midi_structure") if isinstance(pattern.get("plg_midi_structure"), dict) else {}
    arrangement = pattern.get("plg_arrangement") if isinstance(pattern.get("plg_arrangement"), list) else []
    phrases = structure.get("phrases") if isinstance(structure.get("phrases"), list) else []
    pauses = structure.get("pauses") if isinstance(structure.get("pauses"), list) else []
    sections = {str(item.get("name")) for item in arrangement if isinstance(item, dict)}
    moves: list[dict[str, str]] = []
    if len(phrases) >= 4 and ("hook" in sections or "chorus" in sections or "drop" in sections):
        moves.append({"id": "transition_fill", "why": "section landing needs a pickup"})
    if len(phrases) >= 4:
        moves.append({"id": "phrase_pocket", "why": "MIDI has enough phrases to drive the low-end pocket"})
    if not pauses and len(phrases) >= 4:
        moves.append({"id": "drum_density_inverse", "why": "continuous melody needs selected drum breathing room"})
    if len(phrases) >= 8 and genre in {"trap", "rage", "opium", "underground", "pluggnb"}:
        moves.append({"id": "pre_snare_pickup", "why": "long form benefits from small phrase-turn anticipation"})
    return {"version": 1, "moves": moves[:4], "move_ids": [move["id"] for move in moves[:4]]}


__all__ = ["choose_moves"]

"""Deterministic melodic development across song sections.

The composer LLM writes one 8-bar core motif (see llm_pipeline.COMPOSER_SYSTEM);
arranger.py used to tile that exact core verbatim into every section, so the
"chorus" was never anything but the verse played louder. These are classical,
non-LLM motif-development techniques applied to the core BEFORE arranger.py
tiles it per section — same idea, genuinely different treatment, zero LLM cost
or reliability risk since it's pure Python over already-generated notes.

Register safety: transformed pitches are NOT clamped here. beat_humanize.py's
normalize_registers() unconditionally folds the whole melody_lead track back
into MELODY_REGISTER after arrangement (_clamp_octave's while-loop handles any
distance out of range), so duplicating that here would be redundant.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from music_theory import diatonic_transpose, name_for_midi, snap_pc_to_scale
from pattern_utils import parse_note_name

SEQUENCE_STEPS = 2  # diatonic third up — the classic "chorus lifts" move


def sequence_transform(
    notes: list[dict[str, Any]], root_pc: int, scale: str, *, steps: int = SEQUENCE_STEPS
) -> list[dict[str, Any]]:
    """Transpose the whole motif by `steps` SCALE degrees (diatonic, not a flat
    chromatic shift) — same rhythm, same contour, sitting higher in the key."""
    out: list[dict[str, Any]] = []
    for entry in notes:
        note = deepcopy(entry)
        try:
            midi = parse_note_name(str(entry.get("note", "A4")))
            note["note"] = name_for_midi(diatonic_transpose(midi, root_pc, scale, steps))
        except (KeyError, ValueError):
            pass
        out.append(note)
    return out


def inversion_transform(
    notes: list[dict[str, Any]], root_pc: int, scale: str
) -> list[dict[str, Any]]:
    """Mirror every pitch's interval around the motif's own first note (by
    time), then snap back onto the scale. A contour that goes up now goes
    down by the same amount — recognizably the same rhythm, an inverted
    shape."""
    if not notes:
        return notes
    ordered = sorted(notes, key=lambda n: float(n.get("time_step", 0)))
    try:
        axis = parse_note_name(str(ordered[0].get("note", "A4")))
    except (KeyError, ValueError):
        return [deepcopy(n) for n in notes]

    out: list[dict[str, Any]] = []
    for entry in notes:
        note = deepcopy(entry)
        try:
            midi = parse_note_name(str(entry.get("note", "A4")))
            mirrored = snap_pc_to_scale(2 * axis - midi, root_pc, scale)
            note["note"] = name_for_midi(mirrored)
        except (KeyError, ValueError):
            pass
        out.append(note)
    return out


def retrograde_transform(
    notes: list[dict[str, Any]], core_beats: float
) -> list[dict[str, Any]]:
    """Time-reverse the motif: the note that played last now plays first, and
    every gap between notes mirrors too (not just the note order) — the
    closed-form mapping is new_start = core_beats - old_end for each note,
    which keeps every note's own length and reflects the whole rhythm,
    including its silences, around the motif's midpoint.

    Example: notes at [0,1), [2,2.5), [3,4) inside a 4-beat core have gaps of
    1 beat then 0.5 beats between them (reading forward). Retrograded, the
    same three notes land at [3,4), [1.5,2), [0,1) — reading forward that's
    gaps of 0.5 then 1 beat: the gap sequence is exactly reversed, which is
    what "playing the timeline backward" means (not just reordering pitches).
    """
    out: list[dict[str, Any]] = []
    for entry in notes:
        note = deepcopy(entry)
        step = float(entry.get("time_step", 0))
        length = float(entry.get("length", 0.25))
        new_step = core_beats - step - length
        # Defensive clamp: a note that (due to upstream rounding) slightly
        # overhangs core_beats must not produce a negative time_step.
        note["time_step"] = round(max(0.0, new_step), 5)
        out.append(note)
    return sorted(out, key=lambda n: float(n.get("time_step", 0)))


def fragment_transform(
    notes: list[dict[str, Any]], core_beats: float
) -> tuple[list[dict[str, Any]], float]:
    """Keep only the motif's first half (by time) and shrink core_beats to
    match — arranger.py's _tile() then naturally loops this shorter fragment
    MORE times to fill the same section length, reading as "teasing the idea"
    rather than stating it in full (intro / first verse pass)."""
    if not notes or core_beats <= 0:
        return [deepcopy(n) for n in notes], core_beats
    half = core_beats / 2.0
    kept = [deepcopy(n) for n in notes if float(n.get("time_step", 0)) < half - 1e-6]
    if not kept:
        # A motif with nothing in its first half (e.g. one long pickup note)
        # would otherwise vanish — keep the original rather than go silent.
        return [deepcopy(n) for n in notes], core_beats
    return kept, half


def melody_variant_for_section(
    core_notes: list[dict[str, Any]],
    core_beats: float,
    *,
    section: str,
    occurrence: int,
    is_final_chorus: bool,
    root_pc: int,
    scale: str,
) -> tuple[list[dict[str, Any]], float]:
    """The section -> transformation map. hook is always the untouched
    identity statement (the song's "face"); the final chorus stays untouched
    too so the existing _octave_lift peak treatment in arranger._develop_section
    isn't compounded with a second pitch transform on the same moment."""
    if not core_notes:
        return core_notes, core_beats

    if section == "hook":
        return [deepcopy(n) for n in core_notes], core_beats
    if section == "chorus":
        if is_final_chorus:
            return [deepcopy(n) for n in core_notes], core_beats
        return sequence_transform(core_notes, root_pc, scale), core_beats
    if section == "verse":
        if occurrence <= 1:
            return fragment_transform(core_notes, core_beats)
        return retrograde_transform(core_notes, core_beats), core_beats
    if section in ("outro", "drop"):
        return retrograde_transform(core_notes, core_beats), core_beats
    if section == "intro":
        return fragment_transform(core_notes, core_beats)
    return [deepcopy(n) for n in core_notes], core_beats

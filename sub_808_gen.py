"""808 bass engine — root notes from melody, locked to kick, one long hit per downbeat."""

from __future__ import annotations

from typing import Any

from music_theory import detect_root_pc, name_for_midi
from pattern_utils import parse_note_name

BEATS_PER_BAR = 4.0
# FL naming: C5 = MIDI 60. Library 808 one-shots sound right at C4+ in the
# piano roll — below C4 the sample pitches into muddy garbage.
SUB_LO = 48   # C4 — hard floor; never place 808 below this
SUB_HI = 60   # C5 — one-octave band above the floor
# Jay Cactus / Lollipopbeatz (Detroit): "808 hits harder tuned higher" — the
# whole one-octave band shifted up (not a fixed pitch class — stays correct
# in whatever key the song is in). F4 sits at the center of this band.
SUB_LO_HIGH = 53  # F4
SUB_HI_HIGH = 65  # F5
# FL native one-shot drum pitch (C5 / MIDI 60). Hat grids used to land here;
# 808 now shares that octave, so hat detection uses length, not pitch alone.
DRUM_NATIVE_MIDI = 60
MIN_SUB_LENGTH = 1.0
# beat_humanize.apply_808_breath always eats >= 0.22 beats off a note's tail
# once its gap to the next onset reaches 1.0 beat (its own floor below that
# skips breath entirely, but the note is then legato-glued to the next
# onset, which is just as short). A fresh retrigger closer than
# MIN_SUB_LENGTH + that breath floor to the previous accepted onset is
# therefore guaranteed to end up shorter than MIN_SUB_LENGTH after the
# shared legato/breath passes — so it isn't generated at all here; the
# still-ringing note simply carries through instead ("physically felt, not
# retriggered", see generate_sub_808 below).
MIN_SUB_ONSET_GAP = 1.25


def _note_step(entry: dict[str, Any]) -> float:
    return float(entry.get("time_step", 0))


def _note_end(entry: dict[str, Any]) -> float:
    return _note_step(entry) + float(entry.get("length", 0.25))


def _clamp_sub_midi(midi: int, *, lo: int = SUB_LO, hi: int = SUB_HI) -> int:
    while midi < lo:
        midi += 12
    while midi > hi:
        midi -= 12
    return midi


def clamp_sub_register_notes(
    notes: list[dict[str, Any]], *, lo: int = SUB_LO, hi: int = SUB_HI
) -> list[dict[str, Any]]:
    """Force 808 into a one-octave register band (default C4–C5) with
    sub-appropriate note lengths. ``lo``/``hi`` let a genre shift the whole
    band (e.g. Detroit's higher-tuned 808 — see SUB_LO_HIGH/SUB_HI_HIGH)."""
    out: list[dict[str, Any]] = []
    for raw in notes:
        if not isinstance(raw, dict):
            continue
        try:
            midi = _clamp_sub_midi(parse_note_name(str(raw.get("note", "C4"))), lo=lo, hi=hi)
        except (KeyError, TypeError, ValueError):
            continue
        entry = dict(raw)
        entry["note"] = name_for_midi(midi)
        entry["length"] = max(MIN_SUB_LENGTH, round(float(entry.get("length", 0.75)), 5))
        out.append(entry)
    return out


def is_hat_like_sub_808(notes: list[dict[str, Any]]) -> bool:
    """Detect LLM/hat-grid contamination on the 808 lane (dense micro-notes).

    Pitch alone is not a signal anymore — legitimate 808 lives at C4–C5, same
    octave as FL's drum-native root. Short note density is the tell.
    """
    if len(notes) < 8:
        return False
    short = 0
    for entry in notes:
        if float(entry.get("length", 0.25)) < 0.35:
            short += 1
    return (short / len(notes)) >= 0.55


def should_replace_sub_808(
    existing: list[dict[str, Any]],
    kick: list[dict[str, Any]],
) -> bool:
    """True when the 808 lane should be rebuilt from the kick engine."""
    if not kick:
        return False
    if not existing:
        return True
    if is_hat_like_sub_808(existing):
        return True
    if not any(n.get("sub_808_gen") for n in existing):
        return True
    if len(existing) > max(len(kick) * 2, 24):
        return True
    return False

def _bar_root_midi(
    melody: list[dict[str, Any]],
    bar: int,
    *,
    roots_pc: list[int] | None = None,
) -> int:
    if roots_pc:
        pc = int(roots_pc[bar % len(roots_pc)]) % 12
        return SUB_LO + ((pc - SUB_LO) % 12)
    start = bar * BEATS_PER_BAR
    end = start + BEATS_PER_BAR
    in_bar = [n for n in melody if start <= _note_step(n) < end]
    if not in_bar:
        return detect_root_pc(melody) if melody else SUB_LO
    return min(parse_note_name(str(n["note"])) for n in in_bar)


def generate_sub_808(
    melody: list[dict[str, Any]],
    kick: list[dict[str, Any]],
    *,
    bars: int,
    roots_pc: list[int] | None = None,
) -> list[dict[str, Any]]:
    """One sub hit per kick, held until the next kick — UNLESS the root hasn't
    moved since the previous kick, in which case the two merge into one
    continuous note instead of retriggering the sample on an unchanged pitch
    (2026-07: "physically felt, not retriggered" is the sub's whole identity;
    every kick used to force a fresh note-on even when the harmony hadn't
    moved). Every note boundary that DOES survive is therefore a genuine root
    change — see beat_humanize.apply_sub_808_glide, which relies on exactly
    that to know where a portamento actually belongs.

    Root per bar: the style-corpus progression when pinned (``roots_pc``), else
    the lowest melody note in the bar (legacy static-root behaviour).
    """
    if not kick:
        return []

    raw_kick_times = sorted({round(_note_step(k), 5) for k in kick})
    kick_times: list[float] = []
    for hit in raw_kick_times:
        if kick_times and hit - kick_times[-1] < MIN_SUB_ONSET_GAP:
            continue
        kick_times.append(hit)
    notes: list[dict[str, Any]] = []

    for index, hit in enumerate(kick_times):
        next_hit = kick_times[index + 1] if index + 1 < len(kick_times) else hit + BEATS_PER_BAR
        bar = max(0, int(hit // BEATS_PER_BAR))
        root_midi = _clamp_sub_midi(_bar_root_midi(melody, bar, roots_pc=roots_pc))
        end = round(next_hit, 5)
        if notes and root_midi == notes[-1]["_root_midi"] and abs(hit - notes[-1]["_end"]) < 1e-6:
            notes[-1]["length"] = round(end - notes[-1]["time_step"], 5)
            notes[-1]["_end"] = end
            continue
        notes.append(
            {
                "time_step": hit,
                "note": name_for_midi(root_midi),
                "length": max(MIN_SUB_LENGTH, round(end - hit, 5)),
                "velocity": 127,
                "sub_808_gen": True,
                "_root_midi": root_midi,
                "_end": end,
            }
        )

    for note in notes:
        del note["_root_midi"]
        del note["_end"]
    return notes


def filter_close_onsets(
    notes: list[dict[str, Any]], *, min_gap: float = MIN_SUB_ONSET_GAP
) -> list[dict[str, Any]]:
    """Drop a sub_808 onset too close to the previous kept one to survive
    mono_legato_bass + apply_808_breath at >= MIN_SUB_LENGTH (see
    MIN_SUB_ONSET_GAP). Used on any sub_808 note list right before the final
    legato/breath pass, regardless of where the notes came from (fresh
    generate_sub_808 output, an uploaded/repaired existing line, a kick-phase
    -aligned line, ...) — generate_sub_808 already filters its own kick
    onsets at the source, but every other path that can hand this lane a
    dense note list needs the same floor applied once, right before breath.
    The dropped onset isn't a lost musical moment: the still-ringing
    previous note's length is recomputed against whatever onset survives
    next by the legato pass that follows.
    """
    ordered = sorted((n for n in notes if isinstance(n, dict)), key=_note_step)
    kept: list[dict[str, Any]] = []
    for note in ordered:
        if kept and _note_step(note) - _note_step(kept[-1]) < min_gap:
            continue
        kept.append(note)
    return kept

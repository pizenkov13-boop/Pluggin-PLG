"""Roles of the notes in an imported MIDI: bass / chords / lead / counter.

A producer's MIDI loop is usually ONE track on ONE channel with everything
mixed in: held chord tones under a short melody. This module finds out which
note is which, from the notes alone (no model, no external service), and writes
the answer on each note as ``plg_role``. It changes nothing else: every note
stays in ``tracks["melody_lead"]`` so every stage that reads the melody sees
exactly what it saw before. Later stages read the tags (the chord and bass
evidence for the 808, the lane split at the end of the run).

The rule is rhythmic, because that is how these loops are written. Notes that
start together form a *group*; a group with several notes is a harmony hit, a
lone note is the melody going by:

* a group of three or more notes with nothing held is a chord stab;
* a group with held notes (>= ``HELD_BEATS``) puts the held ones in the chord
  and leaves the short ones on top as the melody;
* the lowest held note of a chord is the **bass** when it sits in the file's
  lowest octave and well below the next voice (or under ``BASS_CEILING``);
* a short note beside a higher short note is a **counter** voice (an octave
  double is not: it is the same line);
* a lone note is **lead**.

Roles that would only be a stray handful of notes are folded back into the
lead, so a plain one-line melody stays one line. ``PLG_MIDI_ROLES=off`` writes
no tags at all.

``split_into_lanes`` then turns the tags into lanes (``chords``,
``counter_melody``) at the end of the run, but only when ``PLG_MIDI_ROLE_LANES``
is on (``lanes_enabled``; off by default: the tags feed the 808, the FLP keeps
the whole melody in ``melody_lead``); see its docstring.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any

from pattern_utils import parse_note_name

ROLE_KEY = "plg_role"
BASS, CHORDS, LEAD, COUNTER = "bass", "chords", "lead", "counter"
ROLES = (BASS, CHORDS, LEAD, COUNTER)

ONSET_TOLERANCE = 0.06  # beats: hand-played notes this close start together
HELD_BEATS = 1.5  # a note this long is harmony under the line, not the line
BASS_GAP = 7  # semitones between the lowest held note and the next voice
BASS_CEILING = 55  # G3: the lowest held note of a chord below this is bass
BASS_REGISTER = 12  # semitones above the file's lowest note a bass can still sit
MIN_CHORD_HITS = 2  # fewer harmony hits than this is not a chord part
MIN_COUNTER_NOTES = 4  # fewer second-voice notes than this is not a counter line

# The two lanes the roles may fill. They are NOT generated lanes
# (midi_validate.MIDI_DISABLED_LANES still lists them, so chord_bed and the
# counter generators stay off): they exist only when split_into_lanes moved the
# user's own tagged notes there, and ``LANES_KEY`` records that it did.
ROLE_LANES = ("chords", "counter_melody")
LANES_KEY = "plg_midi_roles_lanes"
_LANE_FOR_ROLE = {BASS: "chords", CHORDS: "chords", COUNTER: "counter_melody"}

_OFF_VALUES = ("0", "off", "false", "no")
_ON_VALUES = ("1", "on", "true", "yes")


def enabled() -> bool:
    return os.environ.get("PLG_MIDI_ROLES", "").strip().lower() not in _OFF_VALUES


def lanes_enabled() -> bool:
    """Whether the roles may fill the ``chords`` / ``counter_melody`` lanes.

    OFF by default (product decision 2026-10-27): the roles stay what they were
    built for, an internal reading that gives the 808 its chord roots, and the
    FLP carries the seven core lanes with the whole melody in ``melody_lead``.
    ``PLG_MIDI_ROLE_LANES=on`` brings the two lanes back."""
    return os.environ.get("PLG_MIDI_ROLE_LANES", "").strip().lower() in _ON_VALUES


def _groups(notes: list[dict[str, Any]]) -> list[list[tuple[int, int, float]]]:
    """(index, pitch, length) per note, clustered by onset."""
    rows: list[tuple[float, int, int, float]] = []
    for index, note in enumerate(notes):
        try:
            pitch = parse_note_name(str(note.get("note")))
            start = float(note.get("time_step", 0.0) or 0.0)
            length = max(0.0, float(note.get("length", 0.0) or 0.0))
        except (TypeError, ValueError):
            continue
        rows.append((start, pitch, index, length))
    rows.sort()
    groups: list[list[tuple[int, int, float]]] = []
    anchor = None
    for start, pitch, index, length in rows:
        if anchor is None or start - anchor > ONSET_TOLERANCE:
            groups.append([])
            anchor = start
        groups[-1].append((index, pitch, length))
    return groups


def _short_voices(shorts: list[tuple[int, int, float]]) -> dict[int, str]:
    """Lead and counter among the short notes of one group, highest first."""
    ordered = sorted(shorts, key=lambda row: -row[1])
    roles = {ordered[0][0]: LEAD}
    for index, pitch, _length in ordered[1:]:
        octave_double = (ordered[0][1] - pitch) % 12 == 0
        roles[index] = LEAD if octave_double else COUNTER
    return roles


def _classify_group(group: list[tuple[int, int, float]], bass_limit: int) -> dict[int, str]:
    if len(group) == 1:
        return {group[0][0]: LEAD}
    held = [row for row in group if row[2] >= HELD_BEATS]
    short = [row for row in group if row[2] < HELD_BEATS]
    roles: dict[int, str] = {}
    if not held:
        if len(group) >= 3:
            return {index: CHORDS for index, _p, _l in group}
        return _short_voices(short)
    top_short = max((row[1] for row in short), default=None)
    if len(held) == 1 and top_short is not None and held[0][1] > top_short:
        # One held note ABOVE the short ones is the melody; the shorts answer it.
        roles[held[0][0]] = LEAD
        for index, _pitch, _length in short:
            roles[index] = COUNTER
        return roles
    for index, _pitch, _length in held:
        roles[index] = CHORDS
    lowest = min(held, key=lambda row: row[1])
    others = sorted(row[1] for row in group if row is not lowest)
    if others and lowest[1] <= bass_limit and (lowest[1] <= BASS_CEILING or others[0] - lowest[1] >= BASS_GAP):
        roles[lowest[0]] = BASS
    if short:
        roles.update(_short_voices(short))
    return roles


def classify(notes: list[dict[str, Any]]) -> list[str]:
    """One role per note, in the order given. Does not touch the notes."""
    roles = [LEAD] * len(notes)
    groups = _groups(notes)
    pitches = [pitch for group in groups for _i, pitch, _l in group]
    # A bass lives in the file's lowest octave: a held treble note under a
    # higher short one is a chord tone, not a bass.
    bass_limit = (min(pitches) + BASS_REGISTER) if pitches else 0
    for group in groups:
        for index, role in _classify_group(group, bass_limit).items():
            roles[index] = role
    chord_hits = {
        round(float(notes[i].get("time_step", 0.0) or 0.0), 2)
        for i, role in enumerate(roles)
        if role in (CHORDS, BASS)
    }
    if len(chord_hits) < MIN_CHORD_HITS:
        roles = [LEAD if role in (CHORDS, BASS) else role for role in roles]
    if sum(1 for role in roles if role == COUNTER) < MIN_COUNTER_NOTES:
        roles = [LEAD if role == COUNTER else role for role in roles]
    return roles


def tag_roles(notes: list[dict[str, Any]]) -> dict[str, Any]:
    """Write ``plg_role`` on every note and return the summary."""
    roles = classify(notes)
    for note, role in zip(notes, roles):
        note[ROLE_KEY] = role
    counts = Counter(roles)
    return {
        "version": 1,
        "counts": {role: counts.get(role, 0) for role in ROLES},
        "notes": len(notes),
    }


def split_into_lanes(data: dict[str, Any]) -> dict[str, int]:
    """Move the tagged chord / bass / counter notes out of ``melody_lead``.

    Runs once, at the very end of ``humanize_pattern``: every stage before it
    (drums, 808, audits) has already read the whole melody as one lane, so the
    split changes none of them. ``melody_lead`` keeps the lead; chord and bass
    notes become the ``chords`` lane, counter notes the ``counter_melody`` lane.
    Nothing is dropped and nothing is generated.

    Returns the note count per lane it filled (and records it under
    ``LANES_KEY``); ``{}`` when it did nothing: roles off, lanes off
    (``lanes_enabled``, the default), not a MIDI beat, no tags, or no lead would
    be left (``melody_lead`` is a required lane).
    """
    if not enabled() or not lanes_enabled() or not data.get("plg_midi_melody"):
        return {}
    tracks = data.get("tracks")
    lane = tracks.get("melody_lead") if isinstance(tracks, dict) else None
    if not isinstance(lane, list) or not lane:
        return {}
    kept: list[dict[str, Any]] = []
    moved: dict[str, list[dict[str, Any]]] = {name: [] for name in ROLE_LANES}
    for note in lane:
        target = _LANE_FOR_ROLE.get(note.get(ROLE_KEY)) if isinstance(note, dict) else None
        (moved[target] if target else kept).append(note)
    if not kept or not any(moved.values()):
        return {}
    tracks["melody_lead"] = kept
    counts = {name: len(notes) for name, notes in moved.items() if notes}
    for name, notes in moved.items():
        if notes:
            tracks[name] = notes
    data[LANES_KEY] = counts
    return counts


def role_lane_live(data: dict[str, Any], lane: str) -> bool:
    """True when ``lane`` is a role lane that ``split_into_lanes`` filled.

    The tag alone is not enough: a generator that copies the melody copies its
    tags too. The lane must be one the split recorded, and hold only tagged notes.
    """
    if lane not in ROLE_LANES:
        return False
    recorded = data.get(LANES_KEY)
    tracks = data.get("tracks")
    notes = tracks.get(lane) if isinstance(tracks, dict) else None
    if not isinstance(recorded, dict) or not recorded.get(lane) or not notes:
        return False
    return all(isinstance(note, dict) and note.get(ROLE_KEY) in ROLES for note in notes)


__all__ = [
    "BASS", "CHORDS", "COUNTER", "LANES_KEY", "LEAD", "ROLES", "ROLE_KEY", "ROLE_LANES",
    "classify", "enabled", "lanes_enabled", "role_lane_live", "split_into_lanes", "tag_roles",
]

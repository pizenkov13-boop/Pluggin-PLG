"""Key detection + dark-scale quantization (Rule 11 / Rule 18).

Pure, dependency-light music theory the producer brain uses to lock melodies
and 808s to an underground minor palette — instead of blindly flattening every
E and B regardless of key (the old heuristic broke any non-C-ish song).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pattern_utils import NOTE_ALIASES, NOTE_INDEX, NOTE_NAMES, parse_note_name

# Semitone offsets from the root. Dark / rage palette = natural minor + phrygian.
SCALES: dict[str, tuple[int, ...]] = {
    "natural_minor": (0, 2, 3, 5, 7, 8, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "major": (0, 2, 4, 5, 7, 9, 11),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "locrian": (0, 1, 3, 5, 6, 8, 10),
}
_SCALE_QUALITY = {
    "natural_minor": "minor",
    "harmonic_minor": "harmonic minor",
    "phrygian": "phrygian",
    "major": "major",
    "dorian": "dorian",
    "lydian": "lydian",
    "locrian": "locrian",
}
DEFAULT_ROOT_PC = 9  # A — PLG's home key when nothing can be detected

# MIDI Key Signature meta: sf × mi → (root_pc, scale, label).
# sf: 0 = no accidentals; +1..+7 = sharps; −1..−7 = flats (negative = flats).
# mi: 0 = major, 1 = minor.
# 15 major + 15 minor = 30 rows (C/Am through the 7-sharp and 7-flat sides,
# including enharmonics like C#/Db). Matches the MIDI sf/mi cheat sheet.
KEY_SIGNATURE_SF_MI: dict[tuple[int, int], tuple[int, str, str]] = {
    # (sf, mi) → (root_pc, scale, display label)
    # --- no accidentals ---
    (0, 0): (0, "major", "C"),
    (0, 1): (9, "natural_minor", "Am"),
    # --- sharps (+1..+7) ---
    (1, 0): (7, "major", "G"),
    (1, 1): (4, "natural_minor", "Em"),
    (2, 0): (2, "major", "D"),
    (2, 1): (11, "natural_minor", "Bm"),
    (3, 0): (9, "major", "A"),
    (3, 1): (6, "natural_minor", "F#m"),
    (4, 0): (4, "major", "E"),
    (4, 1): (1, "natural_minor", "C#m"),
    (5, 0): (11, "major", "B"),
    (5, 1): (8, "natural_minor", "G#m"),
    (6, 0): (6, "major", "F#"),
    (6, 1): (3, "natural_minor", "D#m"),
    (7, 0): (1, "major", "C#"),
    (7, 1): (10, "natural_minor", "A#m"),
    # --- flats (−1..−7) ---
    (-1, 0): (5, "major", "F"),
    (-1, 1): (2, "natural_minor", "Dm"),
    (-2, 0): (10, "major", "Bb"),
    (-2, 1): (7, "natural_minor", "Gm"),
    (-3, 0): (3, "major", "Eb"),
    (-3, 1): (0, "natural_minor", "Cm"),
    (-4, 0): (8, "major", "Ab"),
    (-4, 1): (5, "natural_minor", "Fm"),
    (-5, 0): (1, "major", "Db"),
    (-5, 1): (10, "natural_minor", "Bbm"),
    (-6, 0): (6, "major", "Gb"),
    (-6, 1): (3, "natural_minor", "Ebm"),
    (-7, 0): (11, "major", "Cb"),
    (-7, 1): (8, "natural_minor", "Abm"),
}

def key_from_sf_mi(sf: int, mi: int) -> tuple[int, str] | None:
    """MIDI Key Signature ``sf``/``mi`` → ``(root_pc, scale)``.

    ``sf``: +1..+7 sharps, −1..−7 flats, 0 = no accidentals.
    ``mi``: 0 = major, 1 = minor.
    """
    try:
        sf_i = int(sf)
        mi_i = int(mi)
    except (TypeError, ValueError):
        return None
    hit = KEY_SIGNATURE_SF_MI.get((sf_i, 1 if mi_i else 0))
    if hit is None:
        return None
    return hit[0], hit[1]


def key_label_from_sf_mi(sf: int, mi: int) -> str | None:
    hit = KEY_SIGNATURE_SF_MI.get((int(sf), 1 if int(mi) else 0))
    return hit[2] if hit else None


def key_from_midi_key_name(key: str) -> tuple[int, str] | None:
    """Parse mido-style key names (``Cm``, ``F#m``, ``Bb``, ``C``) → (root_pc, scale).

    Convention: trailing ``m`` (not ``maj``/``major``) means minor; bare tonic = major.
    """
    if not key or not isinstance(key, str):
        return None
    text = key.strip().replace("♯", "#").replace("♭", "b")
    lower = text.lower()
    if lower.endswith("major"):
        tonic, scale = text[: -len("major")].strip(), "major"
    elif lower.endswith("minor"):
        tonic, scale = text[: -len("minor")].strip(), "natural_minor"
    elif lower.endswith("maj"):
        tonic, scale = text[: -len("maj")].strip(), "major"
    elif lower.endswith("min"):
        tonic, scale = text[: -len("min")].strip(), "natural_minor"
    elif len(text) >= 2 and text[-1] in ("m", "M"):
        tonic, scale = text[:-1], "natural_minor"
    else:
        tonic, scale = text, "major"
    if not tonic:
        return None
    letter = tonic[0].upper()
    if letter not in "ABCDEFG":
        return None
    rest = tonic[1:]
    if rest in ("#", "b"):
        letter = letter + rest
    elif rest:
        return None
    if letter in NOTE_ALIASES:
        letter = NOTE_ALIASES[letter]
    pc = NOTE_INDEX.get(letter)
    if pc is None:
        return None
    return int(pc) % 12, scale


def _safe_midi(note: str) -> int | None:
    try:
        return parse_note_name(note)
    except ValueError:
        return None


def detect_root_pc(notes: list[dict[str, Any]], *, default: int = DEFAULT_ROOT_PC) -> int:
    """Most prominent pitch class, weighted by note length × velocity."""
    weights: Counter[int] = Counter()
    for entry in notes:
        midi = _safe_midi(str(entry.get("note", "")))
        if midi is None:
            continue
        weight = max(1.0, float(entry.get("length", 0.25))) * max(1, int(entry.get("velocity", 100)))
        weights[midi % 12] += weight
    if not weights:
        return default % 12
    # Highest weight wins; ties resolve to the lower pitch class (darker root).
    return max(weights.items(), key=lambda kv: (kv[1], -kv[0]))[0]


def detect_scale(notes: list[dict[str, Any]], root_pc: int, *, default: str = "natural_minor") -> str:
    """Best-fit SCALES entry for `notes` against `root_pc` — same weighting
    (length x velocity) as detect_root_pc, scored as in-scale pitch-class
    coverage per candidate scale. Used for MIDI-upload ingest's
    respect_uploaded_scale option: derive the section-transform scale from
    the user's own material instead of the genre profile's default."""
    weights: Counter[int] = Counter()
    for entry in notes:
        midi = _safe_midi(str(entry.get("note", "")))
        if midi is None:
            continue
        weight = max(1.0, float(entry.get("length", 0.25))) * max(1, int(entry.get("velocity", 100)))
        weights[midi % 12] += weight
    if not weights:
        return default
    best_scale = default
    best_score = -1.0
    for scale_name, intervals in SCALES.items():
        scale_pcs = {(root_pc + i) % 12 for i in intervals}
        score = sum(w for pc, w in weights.items() if pc in scale_pcs)
        if score > best_score:
            best_score = score
            best_scale = scale_name
    return best_scale


def snap_pc_to_scale(midi: int, root_pc: int, scale: str = "natural_minor") -> int:
    """Snap a MIDI note into the scale; ties resolve downward (darker)."""
    intervals = SCALES.get(scale, SCALES["natural_minor"])
    scale_pcs = {(root_pc + i) % 12 for i in intervals}
    pc = midi % 12
    if pc in scale_pcs:
        return midi
    for dist in range(1, 7):
        if (pc - dist) % 12 in scale_pcs:
            return midi - dist
        if (pc + dist) % 12 in scale_pcs:
            return midi + dist
    return midi


def diatonic_transpose(midi: int, root_pc: int, scale: str, steps: int) -> int:
    """Move `midi` by `steps` SCALE DEGREES (not semitones) — steps=2 is a
    diatonic third above (e.g. A natural minor: A->C, D->F, both minor
    thirds — the actual semitone distance varies by degree, which is the
    whole point of "diatonic" vs. a fixed chromatic interval). Off-scale
    input is snapped onto the scale first (snap_pc_to_scale) so a chromatic
    passing tone still gets a sensible harmony note. Carries octave
    correctly across scale-degree wraparound (Python's floor-division
    divmod handles negative `steps` too, for a third below)."""
    intervals = SCALES.get(scale, SCALES["natural_minor"])
    n = len(intervals)
    snapped = snap_pc_to_scale(midi, root_pc, scale)
    idx = intervals.index((snapped - root_pc) % 12)
    octave = (snapped - intervals[idx] - root_pc) // 12
    new_octave, new_idx = divmod(octave * n + idx + steps, n)
    return root_pc + 12 * new_octave + intervals[new_idx]


def name_for_midi(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12}"


def nearest_off_root_pc(root_pc: int, scale: str = "natural_minor", *, prefer: tuple[int, ...] = (7, 5)) -> int:
    """Pitch class of the scale's 5th (7 semitones) or 4th (5 semitones) degree
    above the root, whichever is actually in the scale (5th preferred) — the
    "don't start the melody on the tonic" move (Deon/Infinite)."""
    intervals = SCALES.get(scale, SCALES["natural_minor"])
    offsets = set(intervals)
    for interval in prefer:
        if interval in offsets:
            return (root_pc + interval) % 12
    for interval in intervals:
        if interval != 0:
            return (root_pc + interval) % 12
    return root_pc % 12


def chromatic_neighbor_pc(root_pc: int, scale: str = "natural_minor") -> int:
    """A semitone adjacent to the root that is NOT in the scale (prefer -1,
    then +1) — genuine chromatic tension, not just an in-scale color tone.
    Dirkey "Destroy Lonely": the root starts off the scale, not on a
    consonant degree (unlike nearest_off_root_pc's in-scale open)."""
    intervals = SCALES.get(scale, SCALES["natural_minor"])
    scale_pcs = {(root_pc + i) % 12 for i in intervals}
    for delta in (-1, 1):
        candidate = (root_pc + delta) % 12
        if candidate not in scale_pcs:
            return candidate
    return (root_pc - 1) % 12


def key_label(root_pc: int, scale: str = "natural_minor") -> str:
    quality = _SCALE_QUALITY.get(scale, "minor")
    return f"{NOTE_NAMES[root_pc % 12]} {quality}"

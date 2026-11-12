"""Chord bed — the pinned progression VOICED as sustained chords (PLG Chords).

The engine always knows the harmony (``plg_progression``: per-bar chord tones,
anchored to the melody's root), but until now only the 808 walked it — the beat
had a lead and a bass with nothing holding the middle. A real record keeps the
changes sounding: sustained voice-led chords under the hook. This module writes
them onto ``tracks["chords"]``; starter_kit attaches a pad/keys one-shot (or
reuses the lead's instrument — chords + melody on one timbre is the classic
piano-roll look), flp_writer renders the "PLG Chords" channel.

Voice leading: each bar's triad is placed so every voice moves the SMALLEST
distance from the previous voicing — no parallel block jumps. Register sits
between the 808 (C1–B2) and the lead (C4+).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("plg.chord_bed")

BEATS_PER_BAR = 4.0
_LO, _HI = 48, 67  # C3..G4 — under the lead, over the sub
_SECTION_VELOCITY = {
    "intro": 56,
    "verse": 60,
    "outro": 56,
    "hook": 72,
    "chorus": 72,
    "drop": 72,
    "bridge": 62,
}
_SUSTAIN = 3.9  # beats — whole-bar pad with a hair of release before the next


def chord_bed_enabled() -> bool:
    return os.environ.get("PLG_CHORD_BED", "auto").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _clamp_octave(midi: int) -> int:
    while midi < _LO:
        midi += 12
    while midi > _HI:
        midi -= 12
    return midi


def _nearest_voicing(pcs: list[int], previous: list[int] | None) -> list[int]:
    """Place each pitch class in the octave closest to the previous voicing."""
    if not previous:
        return sorted(_clamp_octave(60 + ((pc - 60) % 12)) for pc in pcs)
    voiced: list[int] = []
    used: set[int] = set()
    for pc in pcs:
        candidates = [m for m in range(_LO, _HI + 1) if m % 12 == pc % 12 and m not in used]
        if not candidates:
            candidates = [m for m in range(_LO, _HI + 1) if m % 12 == pc % 12]
        if not candidates:
            continue
        best = min(candidates, key=lambda m: min(abs(m - p) for p in previous))
        voiced.append(best)
        used.add(best)
    return sorted(voiced)


def _compact_wide_voicing(voicing: list[int], *, max_span: int = 12) -> list[int]:
    """If the voicing's outer notes span more than ``max_span`` semitones,
    octave-invert the extreme farther from the middle voice in toward it
    (Dirkey soulful-guitar video: too-wide-apart chord tones read as
    unnatural — pull one in for a more human, compact voicing)."""
    if len(voicing) < 2:
        return voicing
    voicing = sorted(voicing)
    if voicing[-1] - voicing[0] <= max_span:
        return voicing
    lo, hi = voicing[0], voicing[-1]
    mid = voicing[len(voicing) // 2]
    if (hi - mid) >= (mid - lo):
        candidate = hi - 12
        if candidate > lo:
            voicing[-1] = candidate
    else:
        candidate = lo + 12
        if candidate < hi:
            voicing[0] = candidate
    return sorted(voicing)


def _section_for_bar(arrangement: list[dict[str, Any]], bar: int) -> tuple[str, int]:
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        start = int(section.get("start_bar", 0))
        if start <= bar < start + int(section.get("bars", 0)):
            return str(section.get("name", "verse")), int(section.get("occurrence", 1))
    return "verse", 1


def compose_chord_bed(pattern: dict[str, Any], profile: Any = None) -> int:
    """Write voice-led sustained chords for the whole arrangement.

    ``profile`` (a ``genre_profiles.GenreProfile``) is optional — every knob it
    can flip (``chord_sus_mode``, ``chord_power_chord``, ``chord_root_octave_down``,
    ``chord_voicing_spread``) defaults to a no-op, so omitting it reproduces the
    exact behavior every genre already had before those options existed.

    Returns the number of notes written (0 = skipped)."""
    if not chord_bed_enabled():
        return 0
    tracks = pattern.get("tracks")
    prog = pattern.get("plg_progression")
    arrangement = pattern.get("plg_arrangement")
    if not isinstance(tracks, dict) or not isinstance(prog, dict):
        return 0
    if tracks.get("chords"):
        return 0  # already composed (best-of variants re-run humanize)
    chord_tones = prog.get("chord_tones_pc")
    if not isinstance(chord_tones, list) or not chord_tones:
        return 0
    if not isinstance(arrangement, list) or not arrangement:
        return 0

    import random

    from music_theory import name_for_midi

    total_bars = 0
    for section in arrangement:
        if isinstance(section, dict):
            total_bars = max(
                total_bars, int(section.get("start_bar", 0)) + int(section.get("bars", 0))
            )
    if total_bars <= 0:
        return 0

    from style_family import genre_of, is_carti

    sevenths, ninths = _extensions(prog)
    sus_mode = getattr(profile, "chord_sus_mode", None)
    sus_pcs = _sus_pcs(prog, sus_mode) if sus_mode in ("sus2", "sus4") else []
    power_chord = bool(getattr(profile, "chord_power_chord", False))
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    rng = random.Random(int(variety.get("seed") or 0) ^ 0xC0FFEE)
    from style_family import is_kanye

    genre_now = genre_of(pattern)
    carti = is_carti(genre_now)  # Carti = semitone-cluster tension, no 7/9 gloss
    kanye = is_kanye(genre_now)  # Kanye = lush gospel stack: 7ths AND 9ths always
    root_octave_down = kanye or bool(getattr(profile, "chord_root_octave_down", False))
    compact_wide = bool(getattr(profile, "chord_compact_wide_voicings", False))
    section_variation = bool(getattr(profile, "chord_section_variation", False))

    notes: list[dict[str, Any]] = []
    previous: list[int] | None = None
    for bar in range(total_bars):
        pcs_raw = chord_tones[bar % len(chord_tones)]
        pcs = [int(pc) % 12 for pc in pcs_raw] if isinstance(pcs_raw, (list, tuple)) else []
        if sus_pcs:
            pcs = sus_pcs[bar % len(sus_pcs)]
        if not pcs:
            continue
        if power_chord and len(pcs) >= 3:
            pcs = [pcs[0], pcs[2]]  # root + 5th only — drop the 3rd/sus tone
        voicing = _nearest_voicing(pcs, previous)
        if not voicing:
            continue
        if compact_wide:
            voicing = _compact_wide_voicing(voicing)
        previous = voicing
        section_name, occurrence = _section_for_bar(arrangement, bar)
        velocity = _SECTION_VELOCITY.get(section_name, 60)
        start = bar * BEATS_PER_BAR
        big = section_name in ("hook", "chorus", "drop")
        # Repeat sections develop instead of copy-pasting: thin the color tones
        # (7th/9th/cluster) on some bars of the 2nd+ occurrence of a section.
        vary_bar = section_variation and occurrence >= 2 and rng.random() < 0.4

        stack: list[tuple[int, int]] = [
            (midi, velocity + (4 if i == len(voicing) - 1 else 0))
            for i, midi in enumerate(voicing)
        ]
        if carti:
            # CARTI DNA: dissonant semitone cluster (B+A#, D#+E) — tension, not
            # a pretty chord. A neighbor a half-step off the top voice, quiet.
            if not vary_bar:
                top = max(voicing)
                cluster = top + (1 if (top - 1) in voicing else -1)
                while cluster > _HI:
                    cluster -= 12
                while cluster < _LO:
                    cluster += 12
                if cluster not in voicing:
                    stack.append((cluster, max(30, velocity - 14)))
        else:
            # Extended voicing — the 7th rides on top, the 9th colors big
            # sections (Cm -> Cm7 / Cm9, the melodic/Travis "expensive" spread).
            # Kanye = a LUSH gospel stack: 7th AND 9th on every bar, plus a bass
            # root doubled an octave down for soulful weight.
            if sevenths and not vary_bar:
                seventh = _place_above(sevenths[bar % len(sevenths)], voicing)
                if seventh is not None:
                    stack.append((seventh, velocity - 8))
            if (big or kanye) and ninths and not vary_bar:
                ninth = _place_above(ninths[bar % len(ninths)], voicing, lift=4)
                if ninth is not None:
                    stack.append((ninth, velocity - 12))

        if root_octave_down:
            bass = min(voicing) - 12
            if bass >= 36:
                stack.append((bass, velocity - 4))  # octave-down root anchor

        # Breathing: odd bars re-strike (long + late swell) instead of one flat
        # sustain — the bed pulses every 2 bars instead of hanging like a pad.
        if bar % 2 == 1:
            hits = [(0.0, 2.4, 0), (2.5, 1.4, -10)]
        else:
            hits = [(0.0, _SUSTAIN, 0)]

        for offset, length, vel_shift in hits:
            for midi, vel in stack:
                notes.append({
                    # humanized: never exactly on the grid — floored at 0 so
                    # the very first chord of the song (bar 0) can't draw a
                    # negative time_step (2026-07 audit: confirmed reaching
                    # a real generated pattern as time_step=-0.0042; masked
                    # only by flp_writer's own floor clamp, not fixed here).
                    "time_step": round(max(0.0, start + offset + rng.uniform(-0.015, 0.02)), 4),
                    "note": name_for_midi(midi),
                    "length": length,
                    "velocity": max(30, min(127, vel + vel_shift + rng.randint(-6, 6))),
                })

    spread = float(getattr(profile, "chord_voicing_spread", 0.0) or 0.0)
    if spread > 0 and notes:
        from beat_humanize import apply_chord_voicing_spread  # local: avoid circular import

        notes = apply_chord_voicing_spread(notes, rng, chance=spread, register=(_LO, _HI))

    if notes:
        tracks["chords"] = notes
        logger.info("Chord bed: %d notes over %d bars", len(notes), total_bars)
    return len(notes)


def _extensions(prog: dict[str, Any]) -> tuple[list[int], list[int]]:
    """Per-bar 7th / 9th pitch classes from the progression's own scale."""
    try:
        from style_corpus import NOTE_NAMES, SCALES, normalize_scale

        degrees = [int(d) for d in (prog.get("bar_degrees") or [])]
        key = str(prog.get("key") or "A")
        intervals = SCALES.get(normalize_scale(str(prog.get("scale") or "natural_minor")))
        if not degrees or not intervals:
            return [], []
        root_pc = NOTE_NAMES.index(key) if key in NOTE_NAMES else 9
        size = len(intervals)

        def stacked(degree: int, step: int) -> int:
            idx = (degree + step) % size
            return (root_pc + intervals[idx]) % 12

        sevenths = [stacked(d, 6) for d in degrees]
        ninths = [stacked(d, 8) for d in degrees]
        return sevenths, ninths
    except Exception:  # noqa: BLE001 — extensions are color, never a blocker
        return [], []


def _sus_pcs(prog: dict[str, Any], mode: str) -> list[list[int]]:
    """Per-bar sus2/sus4 triad pitch classes — replace the chord's 3rd with the
    scale's 2nd (sus2) or 4th (sus4) degree, the classic "tension" substitution
    (Dirkey "How To Make UNLIMITED Chords"). Returned in [root, sub, fifth]
    order, matching ``style_corpus._triad_pcs``'s [root, third, fifth]."""
    try:
        from style_corpus import NOTE_NAMES, SCALES, normalize_scale

        degrees = [int(d) for d in (prog.get("bar_degrees") or [])]
        key = str(prog.get("key") or "A")
        intervals = SCALES.get(normalize_scale(str(prog.get("scale") or "natural_minor")))
        if not degrees or not intervals:
            return []
        root_pc = NOTE_NAMES.index(key) if key in NOTE_NAMES else 9
        size = len(intervals)
        sub_step = 1 if mode == "sus2" else 3  # scale steps from the root

        def stacked(degree: int, step: int) -> int:
            idx = (degree + step) % size
            return (root_pc + intervals[idx]) % 12

        return [[stacked(d, 0), stacked(d, sub_step), stacked(d, 4)] for d in degrees]
    except Exception:  # noqa: BLE001 — sus voicing is color, never a blocker
        return []


def _place_above(pc: int, voicing: list[int], *, lift: int = 0) -> int | None:
    """Put a color tone just above the voicing's top, inside the bed register."""
    top = max(voicing)
    midi = top + ((int(pc) - top) % 12)
    if midi <= top:
        midi += 12
    midi += lift - (lift % 12 if midi + lift > _HI else 0)
    while midi > _HI:
        midi -= 12
    if midi <= max(voicing) - 12 or midi < _LO:
        return None
    return midi if midi not in voicing else None

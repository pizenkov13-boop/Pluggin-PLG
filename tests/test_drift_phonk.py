"""Drift Phonk tutorial. Confirmed there was no existing mechanism for a
pervasive off-scale melody — chromatic_neighbor_pc (music_theory.py) only
ever nudges a single opening/root note (melody_root_delay_tension), never a
sustained note-to-note chromatic crawl. Two genuinely new mechanics ported,
enabled on "phonk":

- the melody moves chromatically note-to-note — deliberate passing tones
  OUTSIDE the scale (every other genre in this system stays strictly
  in-scale; this is the first one where drifting off it is the point).
- the kick is placed WHERE the 808 hits, reinforcing it, instead of being
  generated independently.

Items 4-6 (hat-roll "sauce" FX, doubled vocal takes, bitcrush-as-default)
stayed out of scope: too vague to port concretely, not applicable (no live
vocal), or not flagged as key by the brief.
"""

from __future__ import annotations

import random

from beat_humanize import apply_chromatic_melody_walk, apply_kick_synced_to_808
from genre_profiles import PROFILES


def test_phonk_has_the_two_new_flags():
    profile = PROFILES["phonk"]
    assert profile.melody_chromatic_walk is True
    assert profile.kick_synced_to_808 is True


def test_only_phonk_has_these_two_flags():
    for name, profile in PROFILES.items():
        if name == "phonk":
            continue
        assert profile.melody_chromatic_walk is False, name
        assert profile.kick_synced_to_808 is False, name


def test_chromatic_walk_produces_semitone_steps():
    notes = [
        {"time_step": float(i), "note": "C4", "length": 0.5, "velocity": 90}
        for i in range(12)
    ]
    out = apply_chromatic_melody_walk(notes, random.Random(7))
    # a genuinely non-trivial run should include at least one note moved by
    # exactly a semitone from its predecessor (the chromatic crawl signature)
    from pattern_utils import parse_note_name

    midis = [parse_note_name(n["note"]) for n in out]
    diffs = [b - a for a, b in zip(midis, midis[1:])]
    assert any(abs(d) == 1 for d in diffs)


def test_chromatic_walk_preserves_note_count_and_order():
    notes = [
        {"time_step": 3.0, "note": "C4", "length": 0.5, "velocity": 90},
        {"time_step": 1.0, "note": "D4", "length": 0.5, "velocity": 90},
    ]
    out = apply_chromatic_melody_walk(notes, random.Random(1))
    assert len(out) == 2
    assert [n["time_step"] for n in out] == [1.0, 3.0]


def test_chromatic_walk_noop_on_empty():
    assert apply_chromatic_melody_walk([], random.Random(0)) == []


def test_kick_synced_to_808_adds_missing_hits():
    kick = [{"time_step": 0.0, "note": "C1", "length": 0.3, "velocity": 110}]
    sub = [
        {"time_step": 0.0, "note": "E2", "length": 1.0, "velocity": 100},   # already has a kick
        {"time_step": 2.5, "note": "E2", "length": 1.0, "velocity": 100},   # needs one added
    ]
    out = apply_kick_synced_to_808(kick, sub)
    assert len(out) == 2
    steps = sorted(round(n["time_step"], 3) for n in out)
    assert steps == [0.0, 2.5]
    added = [n for n in out if n.get("kick_808_sync")]
    assert len(added) == 1
    assert added[0]["time_step"] == 2.5


def test_kick_synced_to_808_skips_near_existing_hits():
    kick = [{"time_step": 2.0, "note": "C1", "length": 0.3, "velocity": 110}]
    sub = [{"time_step": 2.05, "note": "E2", "length": 1.0, "velocity": 100}]
    out = apply_kick_synced_to_808(kick, sub)
    assert len(out) == 1  # within tolerance -> no duplicate added


def test_kick_synced_to_808_noop_without_808():
    kick = [{"time_step": 0.0, "note": "C1", "length": 0.3, "velocity": 110}]
    assert apply_kick_synced_to_808(kick, []) == kick

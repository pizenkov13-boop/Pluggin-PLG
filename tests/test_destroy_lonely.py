"""Dirkey Destroy Lonely video: root delay + chromatic tension (P.1) and the
chords_reverse_chop / melody_root_delay_tension genre-profile gating (P.2)."""

from __future__ import annotations

from beat_humanize import apply_root_delay_tension
from genre_profiles import PROFILES
from pattern_utils import parse_note_name


def test_root_note_opens_late_and_off_the_scale():
    # A phrygian root (root pc 9, A) established by the loud opening note.
    notes = [
        {"time_step": 0.0, "note": "A3", "length": 1.0, "velocity": 120},
        {"time_step": 1.0, "note": "C4", "length": 1.0, "velocity": 90},
    ]
    out = apply_root_delay_tension(notes, scale="phrygian", delay_beats=0.15)
    first = out[0]
    assert first["time_step"] == 0.15  # delayed onset
    assert round(first["length"], 5) == 0.85  # shortened to make room for the delay
    pc = parse_note_name(first["note"]) % 12
    assert pc == 8  # A phrygian: -1 semitone (Ab/G#) is genuinely out of scale
    assert out[1]["note"] == "C4"  # rest of the phrase untouched


def test_non_root_opening_note_is_a_noop():
    notes = [
        {"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100},
        {"time_step": 1.0, "note": "A3", "length": 1.0, "velocity": 120},
    ]
    out = apply_root_delay_tension(notes, scale="phrygian")
    assert out[0]["note"] == "C4"
    assert out[0]["time_step"] == 0.0


def test_empty_notes_is_a_noop():
    assert apply_root_delay_tension([], scale="phrygian") == []


def test_opium_opts_into_both_destroy_lonely_flags():
    profile = PROFILES["opium"]
    assert profile.melody_root_delay_tension is True
    assert profile.chords_reverse_chop is True
    # not the same move as avoid_melody_root_start (west_coast) — mutually exclusive
    assert profile.melody_avoid_root_start is False


def test_only_opium_has_these_flags_enabled():
    for name, other in PROFILES.items():
        if name == "opium":
            continue
        assert other.melody_root_delay_tension is False, name
        assert other.chords_reverse_chop is False, name

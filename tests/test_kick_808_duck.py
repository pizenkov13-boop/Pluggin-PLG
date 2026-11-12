"""Broadband kick->808 duck baked as real note splits (no automation clip)."""

from __future__ import annotations

from beat_humanize import duck_808_under_kick
from genre_profiles import PROFILES


def test_mid_sustain_kick_splits_the_808_note():
    kicks = [{"time_step": 1.0, "note": "C1", "length": 0.3, "velocity": 112}]
    subs = [{"time_step": 0.0, "note": "E2", "length": 2.0, "velocity": 120}]

    out = duck_808_under_kick(kicks, subs, gap_beats=0.05, recover_beats=0.6, velocity_drop=26)

    assert [round(n["time_step"], 4) for n in out] == [0.0, 1.05, 1.65]
    before, duck, recover = out
    assert round(before["length"], 4) == 1.0
    assert before["velocity"] == 120
    assert round(duck["length"], 4) == 0.6
    assert duck["velocity"] == 94  # 120 - 26
    assert round(recover["length"], 4) == 0.35  # remainder to note end (2.0)
    assert recover["velocity"] == 120
    # every segment keeps the original pitch
    assert {n["note"] for n in out} == {"E2"}


def test_default_duck_is_twenty_percent():
    """Kick→808 duck leaves 80% velocity (~20% sit-down) so dense kicks don't mute the body."""
    kicks = [{"time_step": 1.0, "note": "C1", "length": 0.3, "velocity": 112}]
    subs = [{"time_step": 0.0, "note": "E2", "length": 2.0, "velocity": 120}]
    out = duck_808_under_kick(kicks, subs)
    duck = next(n for n in out if round(float(n["time_step"]), 2) == 1.05)
    assert duck["velocity"] == 72  # 120 * 0.60 (~−4.4 dB Rich Amiri sidechain)


def test_kick_on_the_notes_own_onset_is_not_a_duck():
    kicks = [{"time_step": 0.0, "note": "C1", "length": 0.3, "velocity": 112}]
    subs = [{"time_step": 0.0, "note": "E2", "length": 1.0, "velocity": 120}]

    out = duck_808_under_kick(kicks, subs)
    assert out == subs  # unchanged — this kick IS the note's own attack


def test_glide_and_slide_notes_are_never_split():
    kicks = [{"time_step": 1.0, "note": "C1", "length": 0.3, "velocity": 112}]
    subs = [
        {"time_step": 0.0, "note": "E2", "length": 2.0, "velocity": 120, "glide": True},
        {"time_step": 2.5, "note": "E2", "length": 2.0, "velocity": 120, "slide": True},
    ]
    out = duck_808_under_kick(kicks, subs)
    assert out == subs


def test_multiple_kicks_under_one_sustain_produce_multiple_ducks():
    kicks = [
        {"time_step": 1.0, "note": "C1", "length": 0.3, "velocity": 112},
        {"time_step": 2.0, "note": "C1", "length": 0.3, "velocity": 112},
    ]
    subs = [{"time_step": 0.0, "note": "E2", "length": 4.0, "velocity": 120}]

    out = duck_808_under_kick(kicks, subs, gap_beats=0.05, recover_beats=0.3, velocity_drop=26)
    ducked = [n for n in out if n["velocity"] == 94]
    assert len(ducked) == 2  # one duck per kick, both land under the same sustain


def test_no_kicks_inside_sustain_leaves_notes_untouched():
    kicks = [{"time_step": 5.0, "note": "C1", "length": 0.3, "velocity": 112}]
    subs = [{"time_step": 0.0, "note": "E2", "length": 1.0, "velocity": 120}]
    assert duck_808_under_kick(kicks, subs) == subs


def test_genre_gate_matches_octave_down_layer_family():
    gated = ("trap", "rage", "opium", "phonk", "drill")
    for name in gated:
        assert PROFILES[name].kick_808_duck is True
    for name in ("pop_rap", "toliver", "kanye", "lofi", "boombap"):
        assert PROFILES[name].kick_808_duck is False

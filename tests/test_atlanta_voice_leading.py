"""Richie Souf ("New Atlanta Sound"): counter_melody follows melody_lead's
phrase direction instead of fighting it — a contour mirror on conflict only."""

from __future__ import annotations

from beat_humanize import apply_voice_leading_direction
from genre_profiles import PROFILES
from pattern_utils import parse_note_name


def test_conflicting_direction_gets_mirrored_to_agree():
    # Melody rises C4->C5 (+12). Counter falls A4->E4 (-5): a conflict.
    melody = [
        {"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100},
        {"time_step": 4.0, "note": "C5", "length": 1.0, "velocity": 100},
    ]
    counter = [
        {"time_step": 0.0, "note": "A4", "length": 1.0, "velocity": 90},
        {"time_step": 4.0, "note": "E4", "length": 1.0, "velocity": 90},
    ]
    out = apply_voice_leading_direction(melody, counter, phrase_bars=2.0)
    assert out[0]["note"] == "A4"  # first note of the phrase is the mirror axis, unchanged
    new_dir = parse_note_name(out[1]["note"]) - parse_note_name(out[0]["note"])
    assert new_dir > 0  # now rises, matching the melody


def test_agreeing_direction_is_left_alone():
    melody = [
        {"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100},
        {"time_step": 4.0, "note": "C5", "length": 1.0, "velocity": 100},
    ]
    counter = [
        {"time_step": 0.0, "note": "E4", "length": 1.0, "velocity": 90},
        {"time_step": 4.0, "note": "A4", "length": 1.0, "velocity": 90},
    ]
    out = apply_voice_leading_direction(melody, counter, phrase_bars=2.0)
    assert out[0]["note"] == "E4"
    assert out[1]["note"] == "A4"  # already rises with the melody — untouched


def test_only_the_conflicting_window_is_mirrored():
    # Window 1 (bars 0-2): melody rises, counter falls -> mirrored.
    # Window 2 (bars 2-4): melody falls, counter falls -> already agrees, untouched.
    melody = [
        {"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100},
        {"time_step": 4.0, "note": "C5", "length": 1.0, "velocity": 100},
        {"time_step": 8.0, "note": "C5", "length": 1.0, "velocity": 100},
        {"time_step": 12.0, "note": "C4", "length": 1.0, "velocity": 100},
    ]
    counter = [
        {"time_step": 0.0, "note": "A4", "length": 1.0, "velocity": 90},
        {"time_step": 4.0, "note": "E4", "length": 1.0, "velocity": 90},
        {"time_step": 8.0, "note": "A4", "length": 1.0, "velocity": 90},
        {"time_step": 12.0, "note": "E4", "length": 1.0, "velocity": 90},
    ]
    out = apply_voice_leading_direction(melody, counter, phrase_bars=2.0)
    win1 = [n for n in out if n["time_step"] < 8.0]
    win2 = [n for n in out if n["time_step"] >= 8.0]
    assert parse_note_name(win1[1]["note"]) - parse_note_name(win1[0]["note"]) > 0  # flipped to agree
    assert win2[0]["note"] == "A4" and win2[1]["note"] == "E4"  # untouched, already agreed


def test_window_with_a_single_note_has_no_defined_direction():
    melody = [{"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100}]
    counter = [{"time_step": 0.0, "note": "A4", "length": 1.0, "velocity": 90}]
    out = apply_voice_leading_direction(melody, counter, phrase_bars=2.0)
    assert out[0]["note"] == "A4"  # nothing to compare -> untouched


def test_empty_inputs_are_a_noop():
    assert apply_voice_leading_direction([], [{"time_step": 0.0, "note": "A4"}]) == [
        {"time_step": 0.0, "note": "A4"}
    ]
    assert apply_voice_leading_direction([{"time_step": 0.0, "note": "A4"}], []) == []


def test_trap_family_opts_into_the_direction_check():
    for name in ("trap", "rage", "opium", "drill", "underground"):
        assert PROFILES[name].counter_melody_matches_direction is True, name
    # phonk is NOT a trap-family derivative (own tradition — see
    # style_family.py's PHONK world) — this Atlanta-specific device doesn't
    # carry over, unlike this loop's other members.
    for name in ("toliver", "kanye", "west_coast", "lofi", "boombap", "phonk"):
        assert PROFILES[name].counter_melody_matches_direction is False, name

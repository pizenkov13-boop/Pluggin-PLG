"""Nettspend ("Early Life Crisis"): artist_style -> underground (natural minor
already matches; BPM floor widened to cover 150) + upward melody bias."""

from __future__ import annotations

from artist_styles import resolve_artist_style, sanitize_artist_names
from beat_humanize import apply_melody_upward_bias
from genre_profiles import PROFILES
from pattern_utils import parse_note_name


def test_nettspend_resolves_to_underground():
    hit = resolve_artist_style("nettspend type beat")
    assert hit is not None
    assert hit["genre"] == "underground"
    assert "150" in hit["vibe"]
    assert "70-90" in hit["vibe"]


def test_sanitize_replaces_nettspend_with_vibe_text():
    out = sanitize_artist_names("make a nettspend type beat")
    assert "nettspend" not in out.lower()
    assert "upward" in out or "climbing" in out


def test_descending_phrase_gets_mirrored_to_rise():
    notes = [
        {"time_step": 0.0, "note": "C5", "length": 1.0, "velocity": 100},
        {"time_step": 4.0, "note": "C4", "length": 1.0, "velocity": 100},
    ]
    out = apply_melody_upward_bias(notes, phrase_bars=2.0)
    assert parse_note_name(out[-1]["note"]) - parse_note_name(out[0]["note"]) > 0


def test_ascending_phrase_is_left_alone():
    notes = [
        {"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100},
        {"time_step": 4.0, "note": "C5", "length": 1.0, "velocity": 100},
    ]
    out = apply_melody_upward_bias(notes, phrase_bars=2.0)
    assert out[0]["note"] == "C4"
    assert out[1]["note"] == "C5"


def test_flat_phrase_gets_a_forced_rise():
    notes = [
        {"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100},
        {"time_step": 2.0, "note": "E4", "length": 1.0, "velocity": 100},
        {"time_step": 4.0, "note": "C4", "length": 1.0, "velocity": 100},
    ]
    out = apply_melody_upward_bias(notes, phrase_bars=2.0)
    assert parse_note_name(out[-1]["note"]) - parse_note_name(out[0]["note"]) > 0


def test_empty_notes_is_a_noop():
    assert apply_melody_upward_bias([]) == []


def test_only_underground_has_the_upward_bias_flag():
    assert PROFILES["underground"].melody_upward_bias is True
    for name, other in PROFILES.items():
        if name == "underground":
            continue
        assert other.melody_upward_bias is False, name

"""Tests for note parsing and pattern helpers."""

from pattern_utils import parse_note_name, step_to_beats, track_notes


def test_step_to_beats() -> None:
    assert step_to_beats(1.0) == 1.0
    assert step_to_beats(0.25) == 0.25


def test_parse_note_name() -> None:
    assert parse_note_name("C4") == 48
    assert parse_note_name("C5") == 60
    assert parse_note_name("F#4") == 54
    assert parse_note_name("Bb3") == 46


def test_track_notes() -> None:
    data = {
        "tracks": {
            "hi_hats": [{"note": "C5", "time_step": 0.0, "length": 0.25, "velocity": 100}],
            "sub_808": [],
        }
    }
    assert len(track_notes(data, "hi_hats")) == 1
    assert track_notes(data, "sub_808") == []

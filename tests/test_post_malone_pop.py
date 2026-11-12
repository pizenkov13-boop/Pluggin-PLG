"""Sam Tsui ("How to make a Post Malone type beat"): second source for the pop
direction (after Dillon XO — not yet processed, first pass treats this video
standalone). Two genuinely new techniques out of the six listed:

- vocal-melody-first workflow: turns out beat_humanize.py already anchors the
  chord progression's ROOT to the detected melody root (attach_progression,
  see tests/test_style_corpus.py) generically for every genre — no new code
  needed here, this test file only covers the second technique.
- minor -> major chord duplication: a quiet second copy of the chord bed,
  transposed +3 semitones (relative major), riding counter_melody (repurposed
  — pop's usual off-beat answer line steps aside) for the "bittersweet" color.
"""

from __future__ import annotations

from beat_humanize import add_major_duplicate_chords
from genre_profiles import PROFILES


def test_pop_has_the_major_duplicate_flag():
    assert PROFILES["pop"].chords_major_duplicate_layer is True


def test_only_pop_has_the_major_duplicate_flag():
    for name, profile in PROFILES.items():
        if name == "pop":
            continue
        assert profile.chords_major_duplicate_layer is False, name


def test_major_duplicate_transposes_up_three_semitones_and_quiet():
    data = {
        "tracks": {
            "chords": [
                {"time_step": 0.0, "note": "A3", "length": 2.4, "velocity": 60},
                {"time_step": 0.0, "note": "C4", "length": 2.4, "velocity": 64},
                {"time_step": 0.0, "note": "E4", "length": 2.4, "velocity": 64},
            ],
        },
    }
    placed = add_major_duplicate_chords(data)
    assert placed == 3
    dup = data["tracks"]["counter_melody"]
    assert {n["note"] for n in dup} == {"C4", "D#4", "G4"}
    for original, copy in zip(data["tracks"]["chords"], dup):
        assert copy["velocity"] < original["velocity"]


def test_major_duplicate_noop_without_chords():
    data = {"tracks": {}}
    assert add_major_duplicate_chords(data) == 0
    assert "counter_melody" not in data["tracks"]


def test_major_duplicate_overwrites_existing_counter_melody():
    data = {
        "tracks": {
            "chords": [{"time_step": 0.0, "note": "A3", "length": 2.4, "velocity": 60}],
            "counter_melody": [{"time_step": 1.0, "note": "G4", "length": 0.5, "velocity": 80}],
        },
    }
    placed = add_major_duplicate_chords(data)
    assert placed == 1
    assert data["tracks"]["counter_melody"][0]["note"] == "C4"

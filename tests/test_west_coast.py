"""West Coast genre profile + Deon/Infinite "don't open on the root" move
+ Jay Cactus's pad holdback / texture / sample-selection touches."""

from __future__ import annotations

from beat_humanize import apply_west_coast_pad_holdback, avoid_melody_root_start
from fx_layer import _TEXTURE_GENRES
from genre_profiles import HIPHOP_LANES, PROFILES, detect_genre
from pattern_utils import parse_note_name
from sample_match import TRACK_PROFILES


def test_root_opening_note_gets_nudged_off_the_tonic():
    # A dorian root established by the long, loud opening note (A3) — the rest
    # of the phrase just outlines the chord.
    notes = [
        {"time_step": 0.0, "note": "A3", "length": 2.0, "velocity": 120},
        {"time_step": 2.0, "note": "C4", "length": 1.0, "velocity": 90},
        {"time_step": 3.0, "note": "E4", "length": 1.0, "velocity": 90},
    ]
    out = avoid_melody_root_start(notes, scale="dorian")
    first = out[0]
    assert parse_note_name(first["note"]) % 12 != 9  # no longer A (root pc 9)
    assert parse_note_name(first["note"]) % 12 == 4  # nudged to the 5th (E)
    # register stays close to the original (A3 = 45, nearest E is 40 or 52 -> 40 is closer)
    assert abs(parse_note_name(first["note"]) - 45) <= 6
    # every other note is untouched
    assert out[1]["note"] == "C4" and out[2]["note"] == "E4"


def test_non_root_opening_note_is_a_noop():
    notes = [
        {"time_step": 0.0, "note": "E4", "length": 1.0, "velocity": 100},
        {"time_step": 1.0, "note": "A3", "length": 2.0, "velocity": 120},
    ]
    out = avoid_melody_root_start(notes, scale="dorian")
    assert out[0]["note"] == "E4"


def test_empty_notes_is_a_noop():
    assert avoid_melody_root_start([], scale="dorian") == []


def test_west_coast_profile_exists_and_is_gated_correctly():
    profile = PROFILES["west_coast"]
    assert profile.melody_avoid_root_start is True
    assert profile.eight08 is True  # has a real 808 line — "piano layer over 808"
    assert profile.melody_scale == "dorian"
    for name in ("trap", "rage", "opium", "phonk", "drill"):
        assert PROFILES[name].melody_avoid_root_start is False


def test_west_coast_is_in_the_mvp_hiphop_lane():
    assert "west_coast" in HIPHOP_LANES


def test_genre_detection_routes_west_coast_keywords():
    assert detect_genre(prompt="west coast beat") == "west_coast"
    assert detect_genre(prompt="g-funk vibe") == "west_coast"
    assert detect_genre(prompt="вест-кост биток") == "west_coast"
    # "g-funk" must not fall through to "funk" via substring collision
    assert detect_genre(prompt="gfunk cruiser") == "west_coast"


def test_west_coast_is_in_the_texture_lane_genres():
    assert "west_coast" in _TEXTURE_GENRES


def test_whistle_is_a_recognized_melody_lead_keyword():
    assert "whistle" in TRACK_PROFILES["melody_lead"]["strong"]


def _arrangement_two_chorus_passes():
    return [
        {"name": "intro", "start_bar": 0, "bars": 4, "occurrence": 1},
        {"name": "chorus", "start_bar": 4, "bars": 8, "occurrence": 1},
        {"name": "verse", "start_bar": 12, "bars": 8, "occurrence": 1},
        {"name": "chorus", "start_bar": 20, "bars": 8, "occurrence": 2},
    ]


def test_pad_holdback_removes_only_the_first_chorus_pass():
    pattern = {
        "plg_arrangement": _arrangement_two_chorus_passes(),
        "tracks": {
            "chords": [
                {"time_step": 16.0, "note": "C4", "length": 4.0, "velocity": 80},  # 1st chorus (bar 4-11)
                {"time_step": 80.0, "note": "C4", "length": 4.0, "velocity": 80},  # 2nd chorus (bar 20-27)
            ],
        },
    }
    removed = apply_west_coast_pad_holdback(pattern)
    chords = pattern["tracks"]["chords"]
    assert removed == 1
    assert len(chords) == 1
    assert chords[0]["time_step"] == 80.0


def test_pad_holdback_noop_without_chords():
    pattern = {"plg_arrangement": _arrangement_two_chorus_passes(), "tracks": {}}
    assert apply_west_coast_pad_holdback(pattern) == 0

"""Pluggnb (Summrs-style), built from two sources — prod gas ("How To Make
Pluggnb") and GOYXRD's Pluggnb build — combined into one profile, the same
two-source approach used for West Coast.

Genuinely new, measurable techniques ported:
- prod gas: one fixed clap hit per 2-bar cell lands a quarter-beat late.
- prod gas: no two 8-bar melody blocks repeat identically (add/drop a note
  at every 8-bar boundary).
- GOYXRD: the "bell cheat code" — counter_melody becomes a full octave-up
  duplicate of melody_lead riding its own independently-picked preset,
  replacing the usual off-beat answer line.

Also: "pluggnb"/"plugg" used to be rage keywords (wrong, aggressive fallback
for this mellow/melody-led genre) — split off into their own entry.
"""

from __future__ import annotations

from beat_humanize import (
    add_delayed_clap_hit,
    apply_melody_8bar_variation,
    materialize_bell_octave_counter_melody,
)
from genre_profiles import GENRE_KEYWORDS, HIPHOP_LANES, PRIORITY, PROFILES, detect_genre
from style_corpus import BPM_RANGES, DRUM_CELLS, MIX_RECIPES, PROGRESSIONS, RECIPES, _FX_FLAVOR


def test_pluggnb_profile_exists_with_expected_flags():
    profile = PROFILES["pluggnb"]
    assert profile.melody_scale == "dorian"
    assert profile.melody_avoid_root_start is True
    assert profile.delayed_clap_hit is True
    assert profile.melody_8bar_variation is True
    assert profile.bell_octave_counter_melody is True
    assert profile.sub_808_volume < PROFILES["opium"].sub_808_volume


def test_pluggnb_bpm_range():
    lo, hi = BPM_RANGES["pluggnb"]
    assert lo == 136 and hi == 145
    assert lo <= 140 <= hi and lo <= 141 <= hi


def test_pluggnb_has_full_corpus():
    for table in (PROGRESSIONS, BPM_RANGES, DRUM_CELLS, RECIPES, MIX_RECIPES, _FX_FLAVOR):
        assert "pluggnb" in table


def test_pluggnb_in_lanes_and_priority():
    assert "pluggnb" in HIPHOP_LANES
    assert "pluggnb" in PRIORITY


def test_pluggnb_keywords_no_longer_collide_with_rage():
    assert detect_genre("pluggnb type beat") == "pluggnb"
    assert detect_genre("melodic plugg type beat") == "pluggnb"
    assert "plugg" not in GENRE_KEYWORDS["rage"]
    assert "pluggnb" not in GENRE_KEYWORDS["rage"]
    assert detect_genre("dark trap type beat") == "rage"


def test_only_pluggnb_has_its_three_new_flags():
    for name, profile in PROFILES.items():
        if name == "pluggnb":
            continue
        assert profile.delayed_clap_hit is False, name
        assert profile.melody_8bar_variation is False, name
        assert profile.bell_octave_counter_melody is False, name


def test_delayed_clap_shifts_the_latest_hit_per_2bar_cell():
    data = {
        "tracks": {
            "clap": [
                {"time_step": 2.0, "note": "E1", "length": 0.22, "velocity": 100},
                {"time_step": 6.0, "note": "E1", "length": 0.22, "velocity": 100},
                {"time_step": 10.0, "note": "E1", "length": 0.22, "velocity": 100},
            ],
        },
    }
    shifted = add_delayed_clap_hit(data)
    assert shifted == 2  # one per 2-bar cycle (cycle 0 has 2.0 & 6.0, cycle 1 has 10.0)
    steps = sorted(round(n["time_step"], 3) for n in data["tracks"]["clap"])
    assert 2.0 in steps  # untouched — not the latest in its cycle
    assert 6.25 in steps  # latest in cycle 0, shifted
    assert 10.25 in steps  # only hit in cycle 1, shifted


def test_delayed_clap_skips_collision():
    # 7.9 is cycle 0's sole (=latest) clap; its +0.25 target (8.15) is already
    # occupied by cycle 1's own original clap hit -> cycle 0's shift is blocked.
    data = {
        "tracks": {
            "clap": [
                {"time_step": 7.9, "note": "E1", "length": 0.22, "velocity": 100},
                {"time_step": 8.15, "note": "E1", "length": 0.22, "velocity": 90},
            ],
        },
    }
    shifted = add_delayed_clap_hit(data)
    assert shifted == 1  # only cycle 1's own (unblocked) shift goes through
    steps = sorted(round(n["time_step"], 3) for n in data["tracks"]["clap"])
    assert 7.9 in steps  # cycle 0's shift was blocked by the collision


def test_delayed_clap_noop_without_clap():
    assert add_delayed_clap_hit({"tracks": {}}) == 0


def test_melody_8bar_variation_thins_odd_blocks():
    notes = [
        {"time_step": float(i * 2), "note": "C4", "length": 1.0, "velocity": 90}
        for i in range(20)  # spans block 0 (0-32) and block 1 (32-40+)
    ]
    out = apply_melody_8bar_variation(notes)
    block0 = [n for n in out if float(n["time_step"]) < 32.0]
    block1 = [n for n in out if float(n["time_step"]) >= 32.0]
    original_block1_count = len([n for n in notes if float(n["time_step"]) >= 32.0])
    assert len(block1) == original_block1_count - 1  # odd block (index 1) loses its last note
    assert len(block0) == len([n for n in notes if float(n["time_step"]) < 32.0])  # even block untouched


def test_melody_8bar_variation_adds_passing_tone_when_too_sparse():
    notes = [
        {"time_step": 32.0, "note": "C4", "length": 1.0, "velocity": 90},
        {"time_step": 34.0, "note": "D4", "length": 1.0, "velocity": 90},
    ]
    out = apply_melody_8bar_variation(notes)
    assert len(out) == 3  # too sparse to drop from -> a note is added instead


def test_melody_8bar_variation_noop_on_empty():
    assert apply_melody_8bar_variation([]) == []


def test_bell_octave_counter_melody_transposes_up_and_overwrites():
    data = {
        "tracks": {
            "melody_lead": [
                {"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100},
            ],
            "counter_melody": [
                {"time_step": 0.5, "note": "G4", "length": 0.25, "velocity": 80},
            ],
        },
    }
    placed = materialize_bell_octave_counter_melody(data)
    assert placed == 1
    bell = data["tracks"]["counter_melody"]
    assert bell[0]["note"] == "C5"
    assert bell[0]["velocity"] < 100


def test_bell_octave_counter_melody_noop_without_melody():
    data = {"tracks": {}}
    assert materialize_bell_octave_counter_melody(data) == 0
    assert "counter_melody" not in data["tracks"]

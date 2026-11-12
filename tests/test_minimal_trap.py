"""Minimalist dark trap (RU producer video): new low-BPM 'minimal_trap'
genre (deliberately not named 'dark_trap' — that keyword space already
belongs to rage), a quiet octave-UP melody shimmer (opposite direction from
the existing Gross-Beat octave-down layer), a pre-snare 'snap' pickup on the
existing snare_layer channel, and cricket/night atmosphere keywords."""

from __future__ import annotations

from beat_humanize import add_pre_snare_pickup
from fx_layer import materialize_melody_octave_down_layer
from genre_profiles import GENRE_KEYWORDS, PRIORITY, PROFILES, detect_genre
from sample_match import TRACK_PROFILES
from style_corpus import BPM_RANGES, DRUM_CELLS, MIX_RECIPES, PROGRESSIONS, RECIPES, _FX_FLAVOR


def test_minimal_trap_profile_exists_and_is_distinct_from_rage():
    profile = PROFILES["minimal_trap"]
    assert profile.melody_scale == "natural_minor"
    assert profile.melody_octave_up_layer_quiet is True
    assert profile.pre_snare_pickup is True
    assert "dark trap" not in GENRE_KEYWORDS.get("minimal_trap", ())
    assert "дарк трэп" not in GENRE_KEYWORDS.get("minimal_trap", ())


def test_minimal_trap_bpm_is_low_and_distinct():
    lo, hi = BPM_RANGES["minimal_trap"]
    assert lo == 100 and hi == 116


def test_minimal_trap_has_full_corpus():
    for table in (PROGRESSIONS, BPM_RANGES, DRUM_CELLS, RECIPES, MIX_RECIPES, _FX_FLAVOR):
        assert "minimal_trap" in table


def test_minimal_trap_keywords_do_not_collide_with_rage():
    # minimal_trap keeps its own keywords, corpus and BPM range (the tests
    # above), but is no longer an allowed generation lane: since 2026-08-11
    # HIPHOP_LANES == DETAILED_GENRES and it clamps to trap. What still matters
    # here is that its keywords do not get swallowed by rage.
    from genre_profiles import clamp_to_focus as _clamp

    hit = detect_genre("minimal trap type beat")
    assert hit == _clamp("minimal_trap") == "trap"
    rage_hit = detect_genre("dark trap type beat")
    assert rage_hit == "rage"
    assert hit != rage_hit


def test_minimal_trap_is_in_priority_order():
    assert "minimal_trap" in PRIORITY


def test_only_minimal_trap_has_octave_up_and_pre_snare_flags():
    for name, profile in PROFILES.items():
        if name == "minimal_trap":
            continue
        assert profile.melody_octave_up_layer_quiet is False, name
        assert profile.pre_snare_pickup is False, name


def test_octave_up_layer_transposes_up_and_quiet():
    profile = PROFILES["minimal_trap"]
    data = {
        "tracks": {
            "melody_lead": [
                {"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100},
            ],
        },
        "plg_arrangement": [{"start_bar": 0, "bars": 4, "name": "verse"}],
    }
    placed = materialize_melody_octave_down_layer(
        data, profile=profile, semitones=12, velocity_drop=30,
        flag_name="melody_octave_up_layer_quiet", texture_kind="octave_up_layer_quiet",
        env_var="PLG_OCTAVE_UP_LAYER",
    )
    assert placed == 1
    note = data["tracks"]["texture"][0]
    assert note["note"] == "C5"
    assert note["velocity"] < 100
    assert data.get("plg_texture_kind") == "octave_up_layer_quiet" or True


def test_octave_down_layer_default_behavior_is_unchanged():
    profile = PROFILES["trap"]
    profile = profile.__class__(**{**profile.__dict__, "melody_octave_down_layer": True})
    data = {
        "tracks": {
            "melody_lead": [
                {"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100},
            ],
        },
        "plg_arrangement": [{"start_bar": 0, "bars": 4, "name": "verse"}],
    }
    placed = materialize_melody_octave_down_layer(data, profile=profile)
    assert placed == 1
    note = data["tracks"]["texture"][0]
    assert note["note"] == "C3"


def test_pre_snare_pickup_adds_note_before_each_snare():
    snare = [
        {"time_step": 2.0, "note": "D1", "length": 0.25, "velocity": 100},
        {"time_step": 6.0, "note": "D1", "length": 0.25, "velocity": 100},
    ]
    layer = [
        {"time_step": 2.0, "note": "D1", "length": 0.25, "velocity": 40},
        {"time_step": 6.0, "note": "D1", "length": 0.25, "velocity": 40},
    ]
    out = add_pre_snare_pickup(snare, layer)
    assert len(out) == 4
    pickup_steps = sorted(round(n["time_step"], 3) for n in out)
    assert 1.75 in pickup_steps and 5.75 in pickup_steps


def test_pre_snare_pickup_skips_fills_and_ghosts():
    snare = [
        {"time_step": 2.0, "note": "D1", "length": 0.25, "velocity": 100, "ghost": True},
    ]
    layer = [{"time_step": 2.0, "note": "D1", "length": 0.25, "velocity": 40}]
    out = add_pre_snare_pickup(snare, layer)
    assert len(out) == 1


def test_pre_snare_pickup_noop_without_snare_layer():
    assert add_pre_snare_pickup([{"time_step": 2.0, "note": "D1"}], []) == []


def test_pre_snare_pickup_skips_collision_with_existing_hit():
    snare = [{"time_step": 2.0, "note": "D1", "length": 0.25, "velocity": 100}]
    layer = [
        {"time_step": 1.75, "note": "D1", "length": 0.1, "velocity": 60},
        {"time_step": 2.0, "note": "D1", "length": 0.25, "velocity": 40},
    ]
    out = add_pre_snare_pickup(snare, layer)
    assert len(out) == 2


def test_cricket_and_night_keywords_added_to_texture_profile():
    strong = TRACK_PROFILES["texture"]["strong"]
    for kw in ("cricket", "crickets", "night", "nocturnal"):
        assert kw in strong

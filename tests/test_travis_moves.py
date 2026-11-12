"""Trifreeze/Travis arrangement moves: 808 walk-up, verse idea, texture lane."""

from __future__ import annotations

from arranger import arrange_song
from beat_humanize import add_808_ascent, humanize_pattern
from fx_layer import materialize_melody_octave_down_layer, materialize_texture_notes
from genre_profiles import PROFILES
from pattern_utils import parse_note_name


def _pattern(genre: str = "trap", seed: int = 33) -> dict:
    return {
        "bpm": 148,
        "key": "E",
        "scale": "phrygian",
        "style": genre,
        "user_prompt": f"{genre} beat",
        "plg_variety": {
            "seed": seed, "key": "E", "bpm": 148, "scale": "phrygian", "genre": genre,
            "drum_variant": seed % 8, "melody_variant": seed % 12, "swing_profile": "lazy",
        },
        "tracks": {
            "kick": [{"time_step": t, "note": "C1", "length": 0.3, "velocity": 112} for t in (0.0, 2.5, 4.0, 6.5)],
            "snare": [{"time_step": t, "note": "D1", "length": 0.2, "velocity": 105} for t in (2.0, 6.0)],
            "hi_hats": [{"time_step": i * 0.5, "note": "F#5", "length": 0.1, "velocity": 80} for i in range(16)],
            "sub_808": [{"time_step": b * 4.0, "note": "E2", "length": 2.0, "velocity": 120} for b in range(2)],
            "melody_lead": [
                {"time_step": i, "note": n, "length": 0.75, "velocity": 95}
                for i, n in enumerate(("E5", "G5", "B5", "A5", "F#5", "E5", "D5", "G5"))
            ],
        },
        "build_order": ["kick", "sub_808", "snare", "hi_hats", "melody_lead"],
    }


def test_808_ascends_into_landings():
    notes = [
        {"time_step": b * 4.0, "note": "E2", "length": 1.6, "velocity": 120}
        for b in range(12)
    ]
    data = {"plg_arrangement": [
        {"name": "verse", "start_bar": 0, "bars": 8},
        {"name": "chorus", "start_bar": 8, "bars": 4},
    ]}
    out = add_808_ascent(notes, data)
    walk = [n for n in out if n.get("ascent")]
    assert len(walk) == 2
    landing = parse_note_name("E2")
    steps = sorted((round(float(n["time_step"]), 2), parse_note_name(n["note"])) for n in walk)
    assert steps == [(31.0, landing - 2), (31.5, landing - 1)]  # chromatic climb
    # still monophonic after insertion
    ordered = sorted(out, key=lambda n: float(n["time_step"]))
    assert all(
        float(a["time_step"]) + float(a["length"]) <= float(b["time_step"]) + 0.01
        for a, b in zip(ordered, ordered[1:])
    )


def test_full_pipeline_walkup_present_for_travis():
    pattern = _pattern("trap")  # Travis family — walk-up belongs here
    arrange_song(pattern)
    result = humanize_pattern(pattern)
    walk = [n for n in result["tracks"]["sub_808"] if n.get("ascent")]
    assert walk, "no chromatic walk-up into any landing"


def test_walkup_absent_for_carti():
    pattern = _pattern("opium")  # Carti family — 808 stays on root, NO walk-up
    arrange_song(pattern)
    result = humanize_pattern(pattern)
    walk = [n for n in result["tracks"]["sub_808"] if n.get("ascent")]
    assert not walk, "Carti 808 must NOT get Travis chromatic walk-ups"


def test_verse_melody_folds_down():
    pattern = _pattern("trap")
    arrange_song(pattern)
    result = humanize_pattern(pattern)
    arrangement = result.get("plg_arrangement") or []
    verse_ranges = [
        (s["start_bar"] * 4.0, (s["start_bar"] + s["bars"]) * 4.0)
        for s in arrangement if s.get("name") == "verse"
    ]
    chorus_ranges = [
        (s["start_bar"] * 4.0, (s["start_bar"] + s["bars"]) * 4.0)
        for s in arrangement if s.get("name") in ("chorus", "hook")
    ]
    assert verse_ranges and chorus_ranges
    mel = result["tracks"]["melody_lead"]

    def top_pitch(ranges):
        pitches = [
            parse_note_name(n["note"]) for n in mel
            if any(lo <= float(n["time_step"]) < hi for lo, hi in ranges)
        ]
        return max(pitches) if pitches else 0

    assert top_pitch(chorus_ranges) > top_pitch(verse_ranges), (
        "the hook must fly above the verse — two ideas, not one loop"
    )


def test_texture_lane_per_section():
    pattern = _pattern("opium")
    pattern["plg_arrangement"] = [
        {"name": "intro", "start_bar": 0, "bars": 4},
        {"name": "verse", "start_bar": 4, "bars": 8},
        {"name": "chorus", "start_bar": 12, "bars": 8},
    ]
    written = materialize_texture_notes(pattern)
    tex = pattern["tracks"]["texture"]
    assert written == len(tex) == 3
    assert [n["time_step"] for n in tex] == [0.0, 16.0, 48.0]
    assert tex[2]["velocity"] > tex[0]["velocity"]  # chorus atmosphere louder


def test_texture_skips_clean_genres_and_kill_switch(monkeypatch):
    pattern = _pattern("pop_rap")
    pattern["plg_variety"]["genre"] = "boombap"
    pattern["plg_arrangement"] = [{"name": "hook", "start_bar": 0, "bars": 8}]
    assert materialize_texture_notes(pattern) == 0

    monkeypatch.setenv("PLG_TEXTURE_LANE", "off")
    pattern2 = _pattern("opium")
    pattern2["plg_arrangement"] = [{"name": "hook", "start_bar": 0, "bars": 8}]
    assert materialize_texture_notes(pattern2) == 0


def test_octave_down_layer_transposes_full_melody_and_claims_texture_lane():
    pattern = _pattern("trap")
    pattern["plg_arrangement"] = [{"name": "hook", "start_bar": 0, "bars": 8}]
    mel = pattern["tracks"]["melody_lead"]

    written = materialize_melody_octave_down_layer(pattern, profile=PROFILES["trap"])
    tex = pattern["tracks"]["texture"]
    assert written == len(tex) == len(mel)
    assert pattern["plg_texture_kind"] == "octave_down_layer"
    for src, dup in zip(mel, tex):
        assert parse_note_name(dup["note"]) == parse_note_name(src["note"]) - 12
        assert dup["time_step"] == src["time_step"]  # timing untouched — the octave drop slows it
        assert dup["velocity"] < src["velocity"]  # sits behind the original

    # Once claimed, the generic atmosphere layer must not overwrite it.
    assert materialize_texture_notes(pattern) == 0
    assert pattern["tracks"]["texture"] is tex


def test_octave_down_layer_skips_short_sections_and_ungated_genres(monkeypatch):
    short = _pattern("trap")
    short["plg_arrangement"] = [{"name": "intro", "start_bar": 0, "bars": 1}]  # < 2 bars
    assert materialize_melody_octave_down_layer(short, profile=PROFILES["trap"]) == 0

    ungated = _pattern("pop_rap")
    ungated["plg_arrangement"] = [{"name": "hook", "start_bar": 0, "bars": 8}]
    assert materialize_melody_octave_down_layer(ungated, profile=PROFILES["pop_rap"]) == 0

    monkeypatch.setenv("PLG_OCTAVE_DOWN_LAYER", "off")
    off = _pattern("trap")
    off["plg_arrangement"] = [{"name": "hook", "start_bar": 0, "bars": 8}]
    assert materialize_melody_octave_down_layer(off, profile=PROFILES["trap"]) == 0

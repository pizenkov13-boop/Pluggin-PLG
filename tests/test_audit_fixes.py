"""Regression tests for the 2026-07 consistency audit fixes:

1. The opium melody engine's gate trusts the ALREADY-RESOLVED profile instead
   of re-detecting genre from prompt text (text fallback = "trap" used to let
   it hijack minimal_trap/pluggnb/detroit patterns whose prompt had no
   recognizable keyword — replacing the melody, resurrecting the disabled
   counter_melody lane, and starving the texture shimmer via the sound cap).
2. Ear-candy lane priority: chords first (core bed, protected), then the
   melody-derived texture layers, then the fx crash/riser — previously
   fx+chords consumed both free cap slots and the octave-down/up layers were
   silently dead in every full-lane arrangement.
3. Bass-type-aware sidechain reads the bass type pinned BEFORE the snare-gate
   stutter shortened the 808 notes.
4. gospel/госпел EN/RU keyword parity (bare word -> soul, sample-flip
   phrasing -> kanye).
"""

from __future__ import annotations

import os

import pytest

from beat_humanize import bass_type_aware_sidechain_depth, classify_bass_type, humanize_pattern
from genre_profiles import PROFILES, detect_genre
from symbolic_composer import local_opium_melody_mode


def _full_pattern(genre: str, prompt: str) -> dict:
    melody = [
        {"time_step": float(b * 4 + s), "note": n, "length": 0.75, "velocity": 96}
        for b in range(8) for s, n in ((0, "A4"), (1.5, "C5"), (3.0, "E5"))
    ]
    kick = [{"time_step": b * 4.0, "note": "C1", "length": 0.3, "velocity": 112} for b in range(8)]
    return {
        "bpm": 150, "style": "", "user_prompt": prompt,
        "plg_variety": {"seed": 42, "genre": genre, "key": "A", "scale": "natural_minor"},
        "tracks": {"melody_lead": melody, "kick": kick},
        "plg_arrangement": [
            {"name": "intro", "start_bar": 0, "bars": 4},
            {"name": "hook", "start_bar": 4, "bars": 8, "occurrence": 1},
            {"name": "verse", "start_bar": 12, "bars": 8, "occurrence": 1},
            {"name": "chorus", "start_bar": 20, "bars": 8, "occurrence": 1},
        ],
    }


def test_opium_gate_trusts_explicit_genre():
    assert local_opium_melody_mode("dark beat 110", "", genre="minimal_trap") is False
    assert local_opium_melody_mode("dark beat 110", "", genre="trap") is True
    # no genre given -> legacy text detection still works
    assert local_opium_melody_mode("rage type beat", "") is True


def test_opium_engine_no_longer_hijacks_keywordless_prompts(monkeypatch):
    monkeypatch.setenv("PLG_LANES", "all")
    out = humanize_pattern(
        _full_pattern("minimal_trap", "dark beat 110"),
        profile=PROFILES["minimal_trap"],
    )
    assert not out.get("plg_symbolic_opium_melody")
    assert not out["tracks"].get("counter_melody")  # stays disabled by design


def test_trap_prompt_still_gets_the_opium_hook(monkeypatch):
    monkeypatch.setenv("PLG_LANES", "all")
    out = humanize_pattern(
        _full_pattern("trap", "dark beat 110"),
        profile=PROFILES["trap"],
    )
    assert out.get("plg_symbolic_opium_melody") is True


@pytest.mark.parametrize("genre", ["trap", "rage", "opium", "drill"])
def test_octave_down_layer_survives_full_lane_arrangements(genre, monkeypatch):
    # The audit's headline finding: fx+chords used to eat both free cap slots
    # so the Gross-Beat octave layer never fired on a full pattern.
    monkeypatch.setenv("PLG_LANES", "all")
    out = humanize_pattern(_full_pattern(genre, f"{genre} type beat"), profile=PROFILES[genre])
    assert out["tracks"].get("texture"), genre
    assert out.get("plg_texture_kind") == "octave_down_layer", genre


def test_minimal_trap_shimmer_survives_full_lane_arrangements(monkeypatch):
    monkeypatch.setenv("PLG_LANES", "all")
    out = humanize_pattern(
        _full_pattern("minimal_trap", "minimal trap type beat"),
        profile=PROFILES["minimal_trap"],
    )
    assert out.get("plg_texture_kind") == "octave_up_layer_quiet"


def test_chord_bed_still_composes_on_full_patterns(monkeypatch):
    # The deliberate deviation from "texture above chords": the bed stays
    # first so it can never be starved by ear candy.
    monkeypatch.setenv("PLG_LANES", "all")
    for genre in ("trap", "rage"):
        out = humanize_pattern(_full_pattern(genre, f"{genre} type beat"), profile=PROFILES[genre])
        assert out["tracks"].get("chords"), genre


def test_classify_bass_type():
    plucks = [{"time_step": float(i), "note": "C2", "length": 0.2, "velocity": 100} for i in range(4)]
    sustained = [{"time_step": float(i * 4), "note": "C2", "length": 3.0, "velocity": 100} for i in range(4)]
    assert classify_bass_type(plucks) == "pluck"
    assert classify_bass_type(sustained) == "sustained"
    assert classify_bass_type([]) is None


def test_sidechain_prefers_pre_gate_kind_over_gated_lengths():
    # gate-chopped sustained 808: post-gate lengths look plucky, but the
    # pre-gate pin says sustained -> full depth kept
    gated = [{"time_step": float(i * 4), "note": "C2", "length": 0.3, "velocity": 100} for i in range(4)]
    assert bass_type_aware_sidechain_depth(gated, 0.65, pre_gate_kind="sustained") == 0.65
    assert bass_type_aware_sidechain_depth(gated, 0.65) < 0.65  # without the pin: pluck math


def test_hyperpop_pins_bass_type_before_gate(monkeypatch):
    monkeypatch.setenv("PLG_LANES", "all")
    out = humanize_pattern(
        _full_pattern("hyperpop", "hyperpop type beat"),
        profile=PROFILES["hyperpop"],
    )
    assert out.get("plg_bass_type") in ("pluck", "sustained")


def test_detroit_intro_counter_not_stacked_two_octaves(monkeypatch):
    monkeypatch.setenv("PLG_LANES", "all")
    from pattern_utils import parse_note_name

    out = humanize_pattern(
        _full_pattern("detroit", "detroit type beat"),
        profile=PROFILES["detroit"],
    )
    melody = out["tracks"].get("melody_lead") or []
    counter = out["tracks"].get("counter_melody") or []
    intro_end = 16.0
    intro_melody = [parse_note_name(n["note"]) for n in melody if float(n["time_step"]) < intro_end]
    intro_counter = [parse_note_name(n["note"]) for n in counter if float(n["time_step"]) < intro_end]
    if intro_melody and intro_counter:
        # counter is the +12 top-layer of the SAME raised intro — never +24 above it
        assert max(intro_counter) <= max(intro_melody) + 12


def test_gospel_keyword_parity():
    # The point of this test is EN/RU parity, not the specific lane. Since the
    # 2026-08-11 narrowing, `kanye` is no longer an allowed lane and clamps to
    # west_coast — assert against the same clamp both languages go through.
    from genre_profiles import clamp_to_focus as _clamp

    assert detect_genre("gospel sample flip") == _clamp("kanye")
    assert detect_genre("госпел сэмпл") == _clamp("kanye")
    assert detect_genre("gospel sample flip") == detect_genre("госпел сэмпл")
    # bare word -> soul in BOTH languages now (clamped to its hip-hop cousin
    # unless PLG_LANES=all, so compare against the same clamp)
    os.environ.get("PLG_LANES")  # no-op read; detect under default clamp
    from genre_profiles import clamp_to_focus

    assert detect_genre("gospel choir") == clamp_to_focus("soul")
    assert detect_genre("госпел хор") == clamp_to_focus("soul")

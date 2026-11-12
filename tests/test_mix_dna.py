"""Per-genre mix DNA — 808 drive, space, sidechain, FX auto-plan, hat archetypes."""

from __future__ import annotations

from typing import Any

import pytest

import mix_insert_fx
from genre_profiles import PROFILES
from style_corpus import (
    MIX_RECIPES,
    attach_default_fx_intent,
    mix_recipe,
    mix_recipe_for_pattern,
)


def _pattern(prompt: str, genre: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"user_prompt": prompt, "tracks": {}}
    if genre:
        out["plg_variety"] = {"genre": genre}
    return out


def test_every_profile_genre_has_mix_recipe():
    missing = [g for g in PROFILES if g not in MIX_RECIPES]
    assert not missing, f"genres without mix DNA: {missing}"


def test_recipes_sane():
    for genre, recipe in MIX_RECIPES.items():
        assert 0.0 <= recipe["sidechain"] <= 0.98, genre
        assert 0.0 <= recipe["sub_boost_db"] <= 9.0, genre
        assert 10.0 <= recipe["wet"] <= 70.0, genre
        assert 0.5 <= recipe["decay"] <= 6.0, genre
        assert 2000.0 <= recipe["lpf_hz"] <= 12000.0, genre


def test_rage_hits_harder_than_pop():
    rage = mix_recipe("rage")
    pop = mix_recipe("pop")
    assert rage["sub_drive"] and not pop["sub_drive"]
    assert rage["sub_boost_db"] > pop["sub_boost_db"] + 3  # the Carti дисторшн
    assert mix_recipe("opium")["wet"] > rage["wet"]  # opium = huge wash, rage = dry punch


def test_configure_space_mix_enables_genre_chains(monkeypatch):
    monkeypatch.setenv("PLG_INSERT_SPACE_FX", "on")  # insert chains are opt-in
    pattern = _pattern("бит как у карти", genre="rage")
    recipe = mix_insert_fx.configure_space_mix(pattern)
    assert recipe["sub_drive"] is True
    assert mix_insert_fx.sub_drive_enabled(pattern)
    assert mix_insert_fx.melody_space_enabled(pattern)
    # patched blob actually carries the hotter preamp
    stock = mix_insert_fx._patch_blood_overdrive(
        mix_insert_fx._BLOOD_OVERDRIVE_BOOST_D5, preamp_boost_db=3.0
    )
    hot = mix_insert_fx.blood_overdrive_plugin_data()
    assert hot != stock


def test_configure_space_mix_resets_between_patterns(monkeypatch):
    monkeypatch.setenv("PLG_INSERT_SPACE_FX", "on")  # insert chains are opt-in
    mix_insert_fx.configure_space_mix(_pattern("rage", genre="rage"))
    assert mix_insert_fx.sub_drive_enabled(_pattern("x"))
    # the next export must NOT inherit the previous pattern's hot recipe:
    # funk's DNA loads no chains at all
    funk = _pattern("funky groove", genre="funk")
    mix_insert_fx.configure_space_mix(funk)
    assert not mix_insert_fx.sub_drive_enabled(funk)
    assert not mix_insert_fx.melody_space_enabled(funk)


def test_default_lane_gets_trap_mix(monkeypatch):
    monkeypatch.setenv("PLG_INSERT_SPACE_FX", "on")  # insert chains are opt-in
    # no genre words at all → trap default everywhere, including the mix DNA
    unknown = _pattern("just something")
    recipe = mix_insert_fx.configure_space_mix(unknown)
    assert recipe["sub_boost_db"] == MIX_RECIPES["trap"]["sub_boost_db"]
    assert mix_insert_fx.sub_drive_enabled(unknown)


def test_explicit_space_mix_flag_still_wins():
    pattern = _pattern("plain", genre=None)
    pattern["plg_space_mix"] = True
    mix_insert_fx.configure_space_mix(pattern)
    assert mix_insert_fx.sub_drive_enabled(pattern)
    assert mix_insert_fx.melody_space_enabled(pattern)


def test_lofi_is_dark_and_gentle():
    lofi = mix_recipe("lofi")
    assert lofi["lpf_hz"] <= 3500
    assert not lofi["sub_drive"]
    assert lofi["sidechain"] < mix_recipe("house")["sidechain"] - 0.3


# --- FX auto-plan --------------------------------------------------------------


def _arranged(genre: str) -> dict[str, Any]:
    return {
        "user_prompt": "vibe",
        "plg_variety": {"genre": genre},
        "plg_arrangement": [
            {"name": "hook", "start_bar": 0, "bars": 8},
            {"name": "verse", "start_bar": 8, "bars": 8},
            {"name": "chorus", "start_bar": 16, "bars": 8},
            {"name": "verse", "start_bar": 24, "bars": 8},
            {"name": "chorus", "start_bar": 32, "bars": 8, "final_chorus": True},
            {"name": "outro", "start_bar": 40, "bars": 8},
        ],
        "tracks": {},
    }


def test_fx_auto_plan_marks_sections():
    pattern = _arranged("edm")
    events = attach_default_fx_intent(pattern)
    assert events and pattern["plg_fx_auto"] is True
    kinds = {e["type"] for e in events}
    assert {"crash", "riser", "impact"} <= kinds
    crash_bars = sorted(e["bar"] for e in events if e["type"] == "crash")
    assert 17 in crash_bars and 33 in crash_bars  # 1-indexed chorus landings


def test_fx_auto_plan_respects_existing_intent():
    pattern = _arranged("edm")
    pattern["fx_intent"] = [{"bar": 8, "type": "crash"}]
    assert attach_default_fx_intent(pattern) == []


def test_lofi_gets_texture_not_risers():
    events = attach_default_fx_intent(_arranged("lofi"))
    kinds = {e["type"] for e in events}
    assert kinds == {"texture"}


def test_mix_recipe_for_pattern_uses_lane():
    assert mix_recipe_for_pattern(_pattern("anything", genre="house"))["sidechain"] >= 0.85


# --- hat archetypes -------------------------------------------------------------


def _core(genre: str) -> dict[str, Any]:
    return {
        "bpm": 124,
        "style": "",
        "user_prompt": "vibe",
        "plg_variety": {"seed": 3, "genre": genre},
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.3, "velocity": 110}],
            "snare": [{"time_step": 1.0, "note": "D1", "length": 0.3, "velocity": 100}],
            "hi_hats": [{"time_step": i * 0.5, "note": "F#1", "length": 0.2, "velocity": 90} for i in range(8)],
            "melody_lead": [
                {"time_step": float(i), "note": n, "length": 0.75, "velocity": 96}
                for i, n in enumerate(("A4", "C5", "E5", "G5"))
            ],
        },
    }


@pytest.mark.parametrize("genre", ["house", "trance", "garage"])
def test_four_floor_verses_ride_offbeat_open_hats(genre: str):
    from arranger import arrange_song

    pattern = _core(genre)
    arrange_song(pattern)
    verse = next(s for s in pattern["plg_arrangement"] if s["name"] == "verse")
    start, end = verse["start_bar"] * 4.0, (verse["start_bar"] + verse["bars"]) * 4.0
    hats = [
        n for n in pattern["tracks"]["hi_hats"]
        if start <= float(n["time_step"]) < end and n.get("steady_hat")
    ]
    assert hats
    assert all(abs(float(n["time_step"]) % 1.0 - 0.5) < 1e-6 for n in hats)


@pytest.mark.parametrize("genre", ["afrobeats", "reggaeton"])
def test_afro_latin_verses_ride_shaker_grid(genre: str):
    from arranger import arrange_song

    pattern = _core(genre)
    arrange_song(pattern)
    verse = next(s for s in pattern["plg_arrangement"] if s["name"] == "verse")
    start = verse["start_bar"] * 4.0
    bar_hats = [
        n for n in pattern["tracks"]["hi_hats"]
        if start <= float(n["time_step"]) < start + 4.0 and n.get("shaker")
    ]
    assert len(bar_hats) == 16  # 1/16 grid
    assert len({n["velocity"] for n in bar_hats}) >= 3  # rolling wave, not flat


def test_trap_verse_hats_stay_steady_eighths():
    from arranger import arrange_song

    pattern = _core("trap")
    arrange_song(pattern)
    verse = next(s for s in pattern["plg_arrangement"] if s["name"] == "verse")
    start = verse["start_bar"] * 4.0
    hats = [
        n for n in pattern["tracks"]["hi_hats"]
        if start <= float(n["time_step"]) < start + 4.0 and n.get("steady_hat")
    ]
    assert len(hats) == 8

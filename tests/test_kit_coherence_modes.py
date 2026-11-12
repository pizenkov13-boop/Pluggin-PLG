"""PLG_KIT_COHERENCE off / shadow / on, through starter_kit.attach_sounds_to_pattern.

off     the old path, untouched (the default)
shadow  records a proposal next to the old kit; the sounds do not change
on      the proposal replaces the five drum lanes and the 808, never the melody
"""
from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

import pytest

import audio_features
import kit_coherence
import kit_variety
import library_catalog
import sample_match
import sample_passport
import starter_kit
from kit_library_fixture import build_library

DRUM_LANES = ("808_bass", "kick", "snare", "clap", "snare_layer", "hi_hats")


@pytest.fixture(scope="module", autouse=True)
def _private_caches(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(audio_features, "CACHE_FILE", tmp_path_factory.mktemp("af") / "audio_features.json")
    patch.setattr(audio_features, "_cache", None)
    patch.setattr(sample_passport, "PASSPORT_FILE", tmp_path_factory.mktemp("sp") / "sample_passports.json")
    sample_passport.reset_memory_cache()
    yield
    patch.undo()
    sample_passport.reset_memory_cache()


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    return build_library(tmp_path_factory.mktemp("modes_lib"))


@pytest.fixture(scope="module")
def catalog(library):
    return library_catalog.scan_library(library)


@pytest.fixture()
def saved_kits(monkeypatch):
    """Hermetic kit history: never read the user's, record what would be saved."""
    saved: list[dict[str, Path]] = []
    monkeypatch.setattr(kit_variety, "save_kit_pick", lambda picks: saved.append(dict(picks)))
    monkeypatch.setattr(kit_variety, "_load_history", lambda: [])
    monkeypatch.setattr(time, "time_ns", lambda: 1234567890123456789)
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")
    monkeypatch.delenv("PLG_KIT_COHERENCE", raising=False)   # unset = the default mode (on)
    return saved


def _pattern() -> dict:
    return {
        "bpm": 140,
        "style": "trap",
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 110}],
            "808_bass": [
                {"time_step": 0.0, "note": "A1", "length": 1.0, "velocity": 100},
                {"time_step": 4.0, "note": "E2", "length": 1.0, "velocity": 100},
            ],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 100}],
        },
        "plg_variety": {"genre": "trap"},
        "plg_style_card": {"key": {"root_pc": 9, "scale": "natural_minor"}, "kit_target": {"centroid": "mid"}},
    }


def _run(catalog, library, monkeypatch, mode: str | None):
    """``mode=None`` leaves PLG_KIT_COHERENCE UNSET, i.e. the shipped default."""
    if mode is None:
        monkeypatch.delenv("PLG_KIT_COHERENCE", raising=False)
    else:
        monkeypatch.setenv("PLG_KIT_COHERENCE", mode)
    pattern = _pattern()
    sound_map = starter_kit.attach_sounds_to_pattern(pattern, catalog, library_root=library, prompt="")
    return pattern, sound_map


def _names(sound_map) -> dict[str, str]:
    return {lane: Path(p).name for lane, p in sorted(sound_map.items())}


# --- off ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["off", "OFF", "banana", "0"])
def test_off_is_the_old_path_and_never_plans(catalog, library, saved_kits, monkeypatch, mode):
    monkeypatch.setattr(kit_coherence, "plan_kit", lambda *a, **k: pytest.fail("planned in off mode"))
    pattern, sound_map = _run(catalog, library, monkeypatch, mode)
    assert "plg_kit_coherence" not in pattern and sound_map


def test_with_the_variable_unset_the_coherent_kit_is_applied(catalog, library, saved_kits, monkeypatch):
    """The shipped default."""
    pattern, _sound_map = _run(catalog, library, monkeypatch, None)
    assert pattern["plg_kit_coherence"]["mode"] == "on" and pattern["plg_kit_coherence"]["applied"] is True


def test_off_restores_the_old_kit_exactly(catalog, library, saved_kits, monkeypatch):
    """The rollback: one variable, same sounds as before the feature existed."""
    _p, old = _run(catalog, library, monkeypatch, "off")
    _p2, default = _run(catalog, library, monkeypatch, None)
    assert _names(old) != _names(default)       # the default really does change the kit
    _p3, again_off = _run(catalog, library, monkeypatch, "off")
    assert _names(again_off) == _names(old)


# --- shadow --------------------------------------------------------------------------


def test_shadow_changes_nothing_audible_and_records_a_proposal(catalog, library, saved_kits, monkeypatch):
    base_pattern, base_map = _run(catalog, library, monkeypatch, "off")
    base_saved = list(saved_kits)
    saved_kits.clear()
    pattern, sound_map = _run(catalog, library, monkeypatch, "shadow")

    assert _names(sound_map) == _names(base_map)
    assert pattern["plg_sound_paths"] == base_pattern["plg_sound_paths"]
    assert pattern["samples"] == base_pattern["samples"]
    assert saved_kits == base_saved                      # the old kit is what the history remembers

    card = pattern["plg_kit_coherence"]
    assert json.loads(json.dumps(card)) == card
    assert card["mode"] == "shadow" and card["applied"] is False
    assert set(card["proposal"]["picks"]) <= set(DRUM_LANES) and len(card["proposal"]["picks"]) >= 3
    assert set(card["legacy"]["names"]) <= set(DRUM_LANES)
    assert "total" in card["legacy"] and "total" in card["proposal"]


def test_shadow_only_adds_its_own_key(catalog, library, saved_kits, monkeypatch):
    base_pattern, _ = _run(catalog, library, monkeypatch, "off")
    pattern, _ = _run(catalog, library, monkeypatch, "shadow")
    pattern.pop("plg_kit_coherence")
    assert pattern == base_pattern


def test_shadow_legacy_kit_is_the_kit_the_old_path_really_chose(catalog, library, saved_kits, monkeypatch):
    _base, base_map = _run(catalog, library, monkeypatch, "off")
    pattern, _ = _run(catalog, library, monkeypatch, "shadow")
    legacy = pattern["plg_kit_coherence"]["legacy"]["names"]
    # pre-bake names, so compare by containment against the final (possibly baked) file names
    final = _names(base_map)
    for lane, name in legacy.items():
        assert Path(name).stem in final[lane]


# --- on -------------------------------------------------------------------------------------


def test_on_replaces_the_drum_lanes_with_the_proposal_and_keeps_the_melody(catalog, library, saved_kits, monkeypatch):
    _base, base_map = _run(catalog, library, monkeypatch, "off")
    pattern, sound_map = _run(catalog, library, monkeypatch, "on")
    card = pattern["plg_kit_coherence"]
    assert card["applied"] is True and card["mode"] == "on" and "legacy" not in card
    for lane, path in card["proposal"]["picks"].items():
        assert Path(path).stem in Path(sound_map[lane]).name, lane
    assert "808_bass" in sound_map
    assert sound_map["melody_lead"] == base_map["melody_lead"]


def test_on_skips_the_perfect_pair_repick_that_would_undo_the_kit(catalog, library, saved_kits, monkeypatch):
    """partner_kick_keywords is called once by kit_variety (picking the kick) and once by the
    Perfect Pair block that RE-PICKS the kick after the 808. Coherent mode must drop only the latter."""
    calls: list[str] = []
    real = sample_match.partner_kick_keywords
    monkeypatch.setattr(sample_match, "partner_kick_keywords", lambda name: calls.append(name) or real(name))
    _run(catalog, library, monkeypatch, "off")
    old_path_calls = len(calls)
    assert old_path_calls == 2, "the old path should run both the legacy bias and the Perfect Pair re-pick"
    calls.clear()
    pattern, _ = _run(catalog, library, monkeypatch, "on")
    assert pattern["plg_kit_coherence"]["applied"] is True
    assert len(calls) == old_path_calls - 1
    calls.clear()
    _run(catalog, library, monkeypatch, "shadow")
    assert len(calls) == old_path_calls   # shadow must leave the old path whole


def test_on_remembers_the_coherent_kit_in_the_history(catalog, library, saved_kits, monkeypatch):
    pattern, _ = _run(catalog, library, monkeypatch, "on")
    picks = pattern["plg_kit_coherence"]["proposal"]["picks"]
    assert len(saved_kits) == 1
    for lane in ("kick", "snare", "clap", "hi_hats", "snare_layer"):
        if lane in picks:
            assert str(saved_kits[0][lane]) == picks[lane]


def test_on_is_deterministic(catalog, library, saved_kits, monkeypatch):
    first_pattern, first = _run(catalog, library, monkeypatch, "on")
    second_pattern, second = _run(catalog, library, monkeypatch, "on")
    assert _names(first) == _names(second)
    assert first_pattern["plg_kit_coherence"]["proposal"] == second_pattern["plg_kit_coherence"]["proposal"]


def test_on_never_hands_the_melody_a_drum_file(catalog, library, saved_kits, monkeypatch):
    pattern, sound_map = _run(catalog, library, monkeypatch, "on")
    drums = {str(Path(sound_map[l]).resolve()) for l in DRUM_LANES if l in sound_map}
    assert str(Path(sound_map["melody_lead"]).resolve()) not in drums


def test_on_without_the_808_lane_leaves_the_808_alone(catalog, library, saved_kits, monkeypatch):
    import genre_profiles

    real = genre_profiles.profile_for
    monkeypatch.setattr(
        genre_profiles, "profile_for", lambda *a, **k: dataclasses.replace(real(*a, **k), bass808_enabled=False)
    )
    pattern, sound_map = _run(catalog, library, monkeypatch, "on")
    assert "808_bass" not in pattern["plg_kit_coherence"]["proposal"]["picks"]
    assert pattern["plg_kit_coherence"]["applied"] is True and "kick" in pattern["plg_kit_coherence"]["proposal"]["picks"]


# --- failure ----------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["shadow", "on"])
def test_a_crash_in_the_scorer_leaves_the_old_kit_untouched(catalog, library, saved_kits, monkeypatch, mode):
    _base, base_map = _run(catalog, library, monkeypatch, "off")

    def boom(*_a, **_k):
        raise RuntimeError("scorer exploded")

    monkeypatch.setattr(kit_coherence, "plan_kit", boom)
    pattern, sound_map = _run(catalog, library, monkeypatch, mode)
    assert _names(sound_map) == _names(base_map)
    assert pattern["plg_kit_coherence"]["mode"] == "error" and pattern["plg_kit_coherence"]["applied"] is False


@pytest.mark.parametrize("mode", ["shadow", "on"])
def test_no_user_library_means_no_plan_and_no_change(library, saved_kits, monkeypatch, mode):
    monkeypatch.setattr(kit_coherence, "plan_kit", lambda *a, **k: pytest.fail("planned without a library"))
    pattern, sound_map = _run(None, library, monkeypatch, mode)
    assert pattern["plg_kit_coherence"]["applied"] is False and sound_map


@pytest.mark.parametrize("mode", ["shadow", "on"])
def test_a_library_too_small_to_improve_keeps_the_old_kit(catalog, library, saved_kits, monkeypatch, mode):
    _base, base_map = _run(catalog, library, monkeypatch, "off")
    monkeypatch.setattr(kit_coherence, "plan_kit", lambda *a, **k: None)
    pattern, sound_map = _run(catalog, library, monkeypatch, mode)
    assert _names(sound_map) == _names(base_map)
    assert pattern["plg_kit_coherence"] == {
        "version": 1, "mode": mode, "applied": False, "note": "library too small to improve",
    }


def test_the_old_path_is_unchanged_by_merely_having_the_module(catalog, library, saved_kits, monkeypatch):
    """off run, with the module imported and a plan_kit that would explode: identical to a run without it."""
    _p1, first = _run(catalog, library, monkeypatch, "off")
    monkeypatch.setattr(kit_coherence, "plan_kit", lambda *a, **k: pytest.fail("touched"))
    _p2, second = _run(catalog, library, monkeypatch, "off")
    assert _names(first) == _names(second)


@pytest.fixture(autouse=True)
def _melody_sample_on(monkeypatch):
    """This file tests the melody sample machinery: PLG_MELODY_SAMPLE is off by default (sound_policy)."""
    monkeypatch.setenv("PLG_MELODY_SAMPLE", "on")

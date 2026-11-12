"""PLG_MELODY_SAMPLE (sound_policy): by default the melody channel carries no sample of ours.

The notes are the user's; the user puts their own synth on an empty Sampler. ``on`` brings the
bundled melody sample (and its warmth / FX / width copies, and the sample chop) back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import audio_features
import kit_variety
import library_catalog
import sample_passport
import sound_policy
import starter_kit
from kit_library_fixture import build_library


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr(audio_features, "CACHE_FILE", tmp_path / "audio_features.json")
    monkeypatch.setattr(audio_features, "_cache", None)
    monkeypatch.setattr(sample_passport, "PASSPORT_FILE", tmp_path / "sample_passports.json")
    sample_passport.reset_memory_cache()
    monkeypatch.setattr(kit_variety, "save_kit_pick", lambda picks: None)
    monkeypatch.setattr(kit_variety, "_load_history", lambda: [])
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")  # no baked copies written into assets/starter
    monkeypatch.delenv("PLG_MELODY_SAMPLE", raising=False)
    yield
    sample_passport.reset_memory_cache()


def _pattern() -> dict:
    return {
        "bpm": 140,
        "style": "trap",
        "plg_midi_melody": True,
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 110}],
            "808_bass": [{"time_step": 0.0, "note": "A1", "length": 1.0, "velocity": 100}],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 100}],
        },
        "plg_variety": {"genre": "trap"},
    }


@pytest.fixture()
def library(tmp_path):
    return build_library(tmp_path / "lib")


def test_the_switch_is_off_unless_asked(monkeypatch):
    assert sound_policy.melody_sample_enabled() is False
    for raw in ("on", "1", "true", "YES"):
        monkeypatch.setenv("PLG_MELODY_SAMPLE", raw)
        assert sound_policy.melody_sample_enabled() is True
    for raw in ("", "off", "0", "banana"):
        monkeypatch.setenv("PLG_MELODY_SAMPLE", raw)
        assert sound_policy.melody_sample_enabled() is False


def test_by_default_the_melody_gets_no_sample_and_the_drums_still_do(library):
    catalog = library_catalog.scan_library(library)
    pattern = _pattern()
    sound_map = starter_kit.attach_sounds_to_pattern(pattern, catalog, library_root=library, prompt="")

    assert "melody_lead" not in sound_map
    assert "melody_lead" not in pattern["plg_sound_paths"] and "melody_lead" not in pattern["plg_sample_picks"]
    assert all(layer.get("track") != "melody_lead" for layer in pattern.get("samples") or [])
    for lane in ("kick", "808_bass", "hi_hats", "clap", "snare"):
        assert lane in sound_map, lane
    assert pattern["tracks"]["melody_lead"], "the user's notes are untouched"
    assert "plg_sample_chop" not in pattern


def test_with_the_switch_on_the_melody_sample_is_back(library, monkeypatch):
    monkeypatch.setenv("PLG_MELODY_SAMPLE", "on")
    catalog = library_catalog.scan_library(library)
    pattern = _pattern()
    sound_map = starter_kit.attach_sounds_to_pattern(pattern, catalog, library_root=library, prompt="")
    assert "melody_lead" in sound_map and Path(sound_map["melody_lead"]).is_file()


def test_an_empty_melody_channel_is_what_the_flp_writer_exports(library):
    import flp_writer

    catalog = library_catalog.scan_library(library)
    pattern = _pattern()
    starter_kit.attach_sounds_to_pattern(pattern, catalog, library_root=library, prompt="")
    paths = flp_writer._sample_paths_from_data(pattern)
    assert "melody_lead" not in paths and "kick" in paths


def test_a_live_chords_lane_keeps_its_notes_without_a_melody_sample(library, monkeypatch):
    """Role lanes (the user's own notes) must not be dropped because the lead has no sample."""
    import midi_roles

    monkeypatch.setenv("PLG_MIDI_ROLE_LANES", "on")
    catalog = library_catalog.scan_library(library)
    pattern = _pattern()
    chord = [{"time_step": 0.0, "note": "A3", "length": 4.0, "velocity": 90, "plg_role": "chords"}]
    pattern["tracks"]["chords"] = chord
    pattern[midi_roles.LANES_KEY] = {"chords": 1}
    starter_kit.attach_sounds_to_pattern(pattern, catalog, library_root=library, prompt="")
    assert pattern["tracks"].get("chords") == chord

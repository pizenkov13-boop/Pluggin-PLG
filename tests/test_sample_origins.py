"""Whose sound plays each drum lane: ``plg_sample_origins`` (what the UI lists).

``user`` a file of the user's library; ``user_soft`` the user's file that only passed
the last-resort ladder; ``builtin`` a bundled starter sound; ``synthetic`` a generated
808 / hat (both only with PLG_SUBSTITUTE_SOUNDS=on); ``absent`` no sound at all, the lane is
left out of the .flp. The record is made BEFORE the warmth / phase / width copies rename files.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import numpy as np
import pytest

import audio_features
import kit_variety
import library_catalog
import sample_passport
import starter_kit
from lane_fallback import ORIGIN_LANES, sample_origins

RATE = 44100


def _brief(row: dict) -> dict:
    """The origin and the file name; ``path`` has its own test."""
    return {key: row[key] for key in ("origin", "file")}


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


# --- the classifier ----------------------------------------------------------------


def test_a_file_inside_the_library_is_the_users_and_one_outside_is_built_in(tmp_path):
    root = tmp_path / "lib"
    mine = _touch(root / "imports" / "Kick 01.wav")
    bundled = _touch(tmp_path / "assets" / "starter" / "PLG_starter_snare.wav")

    out = sample_origins({}, {"kick": mine, "snare": bundled}, root)

    assert _brief(out["kick"]) == {"origin": "user", "file": "Kick 01.wav"}
    assert _brief(out["snare"]) == {"origin": "builtin", "file": "PLG_starter_snare.wav"}
    assert {lane for lane, row in out.items() if row["origin"] == "absent"} == {"clap", "snare_layer", "hi_hats", "808_bass"}


def test_the_fallback_record_is_the_final_word(tmp_path):
    root = tmp_path / "lib"
    mine = _touch(root / "808 bright.wav")
    synth = tmp_path / "PLGallback_samples" / "PLG_fallback_808_1750ms.wav"
    starter_kick = tmp_path / "starter" / "PLG_starter_kick.wav"
    pattern = {
        "plg_sample_fallback": {
            "808_bass": {"tier": 2, "sample": str(synth), "reasons": [], "user_files": 0},
            "hi_hats": {"tier": 1, "sample": str(mine), "reasons": ["missing_track_identity"], "user_files": 1},
            "kick": {"tier": 2, "sample": str(starter_kick), "reasons": [], "user_files": 0},
        }
    }
    pre_bake = {"808_bass": mine, "hi_hats": mine, "kick": mine}  # what the picker had before the ladder

    out = sample_origins(pattern, pre_bake, root)

    assert _brief(out["808_bass"]) == {"origin": "synthetic", "file": "PLG_fallback_808_1750ms.wav"}
    assert out["hi_hats"]["origin"] == "user_soft" and out["hi_hats"]["file"] == "808 bright.wav"
    assert _brief(out["kick"]) == {"origin": "builtin", "file": "PLG_starter_kick.wav"}


def test_a_broken_entry_never_raises_and_never_hides_the_others(tmp_path):
    root = tmp_path / "lib"
    mine = _touch(root / "Hat 01.wav")
    out = sample_origins({"plg_sample_fallback": {"kick": "not a dict"}}, {"hi_hats": mine, "kick": None}, root)
    assert _brief(out["hi_hats"]) == {"origin": "user", "file": "Hat 01.wav"}
    assert _brief(out["kick"]) == {"origin": "absent", "file": ""}  # the broken record and the empty map say "no sound"


def test_only_the_drum_lanes_are_listed(tmp_path):
    assert ORIGIN_LANES == ("kick", "snare", "clap", "snare_layer", "hi_hats", "808_bass")
    root = tmp_path / "lib"
    mine = _touch(root / "Lead.wav")
    out = sample_origins({}, {"melody_lead": mine}, root)  # the melody has no sample of ours by design
    assert set(out) == set(ORIGIN_LANES) and {row["origin"] for row in out.values()} == {"absent"}


def test_an_absent_record_is_reported_as_absent(tmp_path):
    root = tmp_path / "lib"
    pattern = {"plg_sample_fallback": {"808_bass": {"tier": 0, "sample": None, "reasons": [], "user_files": 2}}}
    out = sample_origins(pattern, {"808_bass": _touch(root / "808 X.wav")}, root)
    assert _brief(out["808_bass"]) == {"origin": "absent", "file": ""}


# --- through attach_sounds_to_pattern ------------------------------------------------


@pytest.fixture(autouse=True)
def _private(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr(audio_features, "CACHE_FILE", tmp_path / "audio_features.json")
    monkeypatch.setattr(audio_features, "_cache", None)
    monkeypatch.setattr(sample_passport, "PASSPORT_FILE", tmp_path / "sample_passports.json")
    sample_passport.reset_memory_cache()
    monkeypatch.setattr(kit_variety, "save_kit_pick", lambda picks: None)
    monkeypatch.setattr(kit_variety, "_load_history", lambda: [])
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")  # no baked copies written into assets/starter
    yield
    sample_passport.reset_memory_cache()


def _write(path: Path, samples) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"".join(struct.pack("<h", int(v * 30000)) for v in pcm))


def _band_noise(n, lo, hi, seed):
    spectrum = np.fft.rfft(np.random.default_rng(seed).normal(0, 1, n))
    freqs = np.fft.rfftfreq(n, 1.0 / RATE)
    spectrum[(freqs < lo) | (freqs > hi)] = 0
    out = np.fft.irfft(spectrum, n)
    return out / max(1e-9, np.abs(out).max())


def _kit_without_snare_and_808(root: Path) -> None:
    n = int(RATE * 0.25)
    t = np.arange(n) / RATE
    _write(root / "Kick 01.wav", np.sin(2 * np.pi * np.cumsum(45 + 110 * np.exp(-t / 0.03)) / RATE) * np.exp(-t / 0.09) * 0.9)
    m = int(RATE * 0.2)
    _write(root / "Clap 01.wav", _band_noise(m, 1500, 5500, 1) * np.exp(-np.arange(m) / (0.05 * RATE)) * 0.8)
    h = int(RATE * 0.07)
    _write(root / "Hat 01.wav", _band_noise(h, 6000, 10500, 2) * np.exp(-np.arange(h) / (0.012 * RATE)) * 0.8)


def _attach_kit_without_snare_and_808(tmp_path):
    root = tmp_path / "lib"
    _kit_without_snare_and_808(root)
    catalog = library_catalog.scan_library(root, quarantine=False)
    pattern = {
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

    starter_kit.attach_sounds_to_pattern(pattern, catalog, library_root=root, prompt="")
    return pattern["plg_sample_origins"]


def test_a_kit_with_no_snare_and_no_808_says_so(tmp_path):
    origins = _attach_kit_without_snare_and_808(tmp_path)
    assert origins["kick"]["origin"] == "user" and origins["kick"]["file"] == "Kick 01.wav"
    assert origins["clap"]["origin"] == "user" and origins["hi_hats"]["origin"] == "user"
    assert origins["snare"]["origin"] == "absent"  # nothing in the kit says "snare": the lane is absent
    assert origins["808_bass"]["origin"] == "absent"  # no 808 file either, and no synthetic one


def test_with_substitutes_on_the_same_kit_gets_our_sounds(tmp_path, monkeypatch):
    monkeypatch.setenv("PLG_SUBSTITUTE_SOUNDS", "on")
    origins = _attach_kit_without_snare_and_808(tmp_path)
    assert origins["snare"]["origin"] == "builtin"
    assert origins["808_bass"]["origin"] in ("synthetic", "builtin")


def test_the_record_carries_the_full_source_path(tmp_path):
    root = tmp_path / "lib"
    mine = _touch(root / "imports" / "Kick 01.wav")
    out = sample_origins({}, {"kick": mine, "snare_layer": mine}, root)
    assert out["kick"]["path"] == str(mine) and out["snare_layer"]["path"] == str(mine)
    assert out["snare"]["path"] == ""  # absent: no file


def test_an_explicit_lane_reports_the_users_file_not_the_baked_copy_the_record_holds(tmp_path):
    root = tmp_path / "lib"
    mine = _touch(root / "808" / "XPOSED 808.wav")
    baked = tmp_path / "starter" / "_phase" / "kick__phase_inv_XPOSED 808.wav"
    pattern = {"plg_sample_fallback": {"808_bass": {"tier": 1, "sample": str(baked), "explicit": True, "reasons": []}}}

    out = sample_origins(pattern, {"808_bass": mine}, root)

    assert out["808_bass"] == {"origin": "user", "file": "XPOSED 808.wav", "path": str(mine)}

"""Files whose NAME says nothing about their lane are judged by their SOUND.

A flat folder of ``sample_01.wav`` ... ``sample_09.wav`` used to give the kick lane
to the bundled starter, and the first file alphabetically (a kick) to the hats.
``sound_type`` reads the audio features the matcher already caches and answers
"clearly this lane" / "clearly another drum"; labelled files are never overruled.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import numpy as np
import pytest

import sound_type
from generation_variety import set_active_context
from lane_fallback import resolve_lane_sample
from library_catalog import scan_library
from sample_match import track_candidate_rejection_reasons

RATE = 44100


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.delenv("PLG_SOUND_TYPE", raising=False)
    yield
    set_active_context(None)


def _write(path: Path, samples) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"".join(struct.pack("<h", int(v * 30000)) for v in pcm))
    return path


def _env(n, tau):
    return np.exp(-np.arange(n) / (tau * RATE))


def _band_noise(n, lo, hi, seed):
    spectrum = np.fft.rfft(np.random.default_rng(seed).normal(0, 1, n))
    freqs = np.fft.rfftfreq(n, 1.0 / RATE)
    spectrum[(freqs < lo) | (freqs > hi)] = 0
    out = np.fft.irfft(spectrum, n)
    return out / max(1e-9, np.abs(out).max())


def kick(seed=1, seconds=0.25):
    n = int(RATE * seconds)
    t = np.arange(n) / RATE
    return np.sin(2 * np.pi * np.cumsum(45 + 110 * np.exp(-t / 0.03)) / RATE) * _env(n, 0.09) * 0.9


def bass808(seed=1, seconds=1.4):
    n = int(RATE * seconds)
    return np.sin(2 * np.pi * 45 * np.arange(n) / RATE) * _env(n, 0.8) * 0.9


def hat(seed=1, seconds=0.07):
    n = int(RATE * seconds)
    return _band_noise(n, 6000, 10500, seed) * _env(n, 0.012) * 0.8


def clap(seed=1, seconds=0.2):
    n = int(RATE * seconds)
    return (_band_noise(n, 1500, 5500, seed) * 0.8) * _env(n, 0.05)


def test_the_four_drum_kinds_are_told_apart_by_sound(tmp_path):
    files = {
        "kick": _write(tmp_path / "a.wav", kick()),
        "808": _write(tmp_path / "b.wav", bass808()),
        "hat": _write(tmp_path / "c.wav", hat()),
        "clap": _write(tmp_path / "d.wav", clap()),
    }
    assert sound_type.identifies(files["kick"], "kick") and not sound_type.identifies(files["kick"], "808_bass")
    assert sound_type.identifies(files["808"], "808_bass") and not sound_type.identifies(files["808"], "kick")
    assert sound_type.identifies(files["hat"], "hi_hats")
    assert not sound_type.identifies(files["clap"], "hi_hats") and not sound_type.identifies(files["clap"], "kick")

    assert sound_type.conflicts(files["kick"], "hi_hats") and sound_type.conflicts(files["kick"], "clap")
    assert sound_type.conflicts(files["hat"], "kick") and sound_type.conflicts(files["hat"], "snare")
    assert sound_type.conflicts(files["clap"], "hi_hats") and sound_type.conflicts(files["clap"], "kick")
    assert not sound_type.conflicts(files["clap"], "snare") and not sound_type.conflicts(files["clap"], "snare_layer")
    assert not sound_type.conflicts(files["kick"], "kick") and not sound_type.conflicts(files["hat"], "hi_hats")


def test_an_unreadable_file_gets_no_opinion(tmp_path):
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"x")
    for lane in ("kick", "hi_hats", "clap", "808_bass"):
        assert not sound_type.conflicts(broken, lane) and not sound_type.identifies(broken, lane)
    assert sound_type.fit(broken, "kick") == 0.5


def test_the_switch_removes_every_effect(tmp_path, monkeypatch):
    monkeypatch.setenv("PLG_SOUND_TYPE", "off")
    thump = _write(tmp_path / "a.wav", kick())
    assert not sound_type.conflicts(thump, "hi_hats") and not sound_type.identifies(thump, "kick")
    assert sound_type.fit(thump, "kick") == 0.5


def test_fit_prefers_the_typical_sound(tmp_path):
    typical = _write(tmp_path / "a.wav", hat())
    off = _write(tmp_path / "b.wav", _band_noise(int(RATE * 0.07), 1500, 3000, 3) * _env(int(RATE * 0.07), 0.02))
    assert sound_type.fit(typical, "hi_hats") > sound_type.fit(off, "hi_hats")


# --- the shared gate -------------------------------------------------------------


def test_an_unlabelled_kick_cannot_play_the_hats_but_can_play_the_kick(tmp_path):
    thump = _write(tmp_path / "07.wav", kick())
    assert "sound_conflicts_with_track" in track_candidate_rejection_reasons(thump, "hi_hats")
    assert track_candidate_rejection_reasons(thump, "kick") == []  # identified by its sound


def test_an_unlabelled_hat_is_identified_and_is_no_clap(tmp_path):
    tick = _write(tmp_path / "07.wav", hat())
    assert track_candidate_rejection_reasons(tick, "hi_hats") == []
    assert "sound_conflicts_with_track" in track_candidate_rejection_reasons(tick, "clap")
    assert "sound_conflicts_with_track" in track_candidate_rejection_reasons(tick, "kick")


def test_a_labelled_file_is_never_overruled_by_its_sound(tmp_path):
    # Named a hat but sounds like a thump: the user said what it is.
    labelled = _write(tmp_path / "Hat 01.wav", kick())
    assert "sound_conflicts_with_track" not in track_candidate_rejection_reasons(labelled, "hi_hats")
    # A dedicated folder counts as a label too.
    foldered = _write(tmp_path / "Kicks" / "01.wav", hat())
    assert "sound_conflicts_with_track" not in track_candidate_rejection_reasons(foldered, "kick")


# --- the last-resort ladder: no more "first file alphabetically" ------------------


def _flat_kit(tmp_path):
    """Nine untyped files, a SHORT kick first alphabetically (the old winner: it
    passes the old hat length gate, like the 0.1 s kicks of real kits)."""
    lib = tmp_path / "lib"
    spec = {
        "sample_01.wav": kick(1, 0.1), "sample_02.wav": clap(2), "sample_03.wav": hat(3),
        "sample_04.wav": kick(4, 0.3), "sample_05.wav": hat(5, 0.06), "sample_06.wav": clap(6),
        "sample_07.wav": hat(7, 0.08), "sample_08.wav": kick(8, 0.2), "sample_09.wav": clap(9),
    }
    for name, data in spec.items():
        _write(lib / name, data)
    return lib, scan_library(lib, quarantine=False)


def _hat_pick(lib, catalog, seed):
    set_active_context({"seed": seed} if seed is not None else None)
    pattern = {"bpm": 140, "plg_midi_melody": True, "tracks": {}}
    return resolve_lane_sample("hi_hats", pattern, {}, catalog, lib).name


def test_the_hat_lane_never_gets_a_kick_and_rotates_with_the_seed(tmp_path):
    lib, catalog = _flat_kit(tmp_path)
    hats = {"sample_03.wav", "sample_05.wav", "sample_07.wav"}

    picks = [_hat_pick(lib, catalog, seed) for seed in range(40)]
    assert set(picks) <= hats, f"a non-hat played the hats: {set(picks) - hats}"
    assert len(set(picks)) >= 2, "the pick never rotates"
    assert _hat_pick(lib, catalog, 7) == _hat_pick(lib, catalog, 7)  # same seed, same file


def test_with_the_switch_off_the_old_alphabetical_pick_is_back(tmp_path, monkeypatch):
    monkeypatch.setenv("PLG_SOUND_TYPE", "off")
    lib, catalog = _flat_kit(tmp_path)
    assert {_hat_pick(lib, catalog, seed) for seed in range(10)} == {"sample_01.wav"}

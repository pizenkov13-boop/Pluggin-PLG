"""audio_duration_seconds / is_probably_one_shot (audit 2026-07): the shared
duration probe behind the two-layer one-shot fix in library_catalog.py and
sample_match.py — a 206-second melodic loop defaulted into the "kits"
category and got picked for the kick channel. Keyword guards alone can't
catch this (the loop's own filename carried no "kick"/"loop"/"bpm" marker;
only its PARENT FOLDER did) — duration is the harder-to-dodge signal."""

from __future__ import annotations

import wave
from pathlib import Path

from library_paths import (
    DEFAULT_LIBRARY_DIR,
    DEFAULT_MIDI_DIR,
    ONE_SHOT_MAX_SECONDS,
    PROJECT_DIR,
    audio_duration_seconds,
    is_probably_one_shot,
)


def _write_wav(path: Path, seconds: float, sr: int = 44100) -> None:
    n = int(sr * seconds)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n)


def test_duration_of_a_short_one_shot(tmp_path: Path):
    path = tmp_path / "kick.wav"
    _write_wav(path, 0.4)
    duration = audio_duration_seconds(path)
    assert duration is not None
    assert 0.35 < duration < 0.45


def test_duration_of_a_long_loop(tmp_path: Path):
    path = tmp_path / "loop.wav"
    _write_wav(path, 10.0)
    duration = audio_duration_seconds(path)
    assert duration is not None
    assert 9.9 < duration < 10.1


def test_duration_unreadable_file_returns_none(tmp_path: Path):
    path = tmp_path / "not_really_audio.wav"
    path.write_bytes(b"garbage, not a wav header")
    assert audio_duration_seconds(path) is None


def test_duration_missing_file_returns_none(tmp_path: Path):
    assert audio_duration_seconds(tmp_path / "does_not_exist.wav") is None


def test_is_probably_one_shot_true_under_cap(tmp_path: Path):
    path = tmp_path / "clap.wav"
    _write_wav(path, 1.0)
    assert is_probably_one_shot(path) is True


def test_is_probably_one_shot_false_over_cap(tmp_path: Path):
    path = tmp_path / "loop.wav"
    _write_wav(path, ONE_SHOT_MAX_SECONDS + 5.0)
    assert is_probably_one_shot(path) is False


def test_is_probably_one_shot_fails_open_on_unreadable(tmp_path: Path):
    """Never block a file we simply couldn't measure — the keyword/score
    gates elsewhere still apply on top of this."""
    path = tmp_path / "corrupt.wav"
    path.write_bytes(b"not a wav")
    assert is_probably_one_shot(path) is True


def test_is_probably_one_shot_boundary_exactly_at_cap(tmp_path: Path):
    path = tmp_path / "boundary.wav"
    _write_wav(path, ONE_SHOT_MAX_SECONDS)
    assert is_probably_one_shot(path) is True


def test_default_midi_dir_is_the_dedicated_material_root():
    """The active desktop route reads the user's sibling PLG_MIDI folder."""
    assert DEFAULT_MIDI_DIR == PROJECT_DIR / "PLG_MIDI"
    assert DEFAULT_MIDI_DIR != DEFAULT_LIBRARY_DIR

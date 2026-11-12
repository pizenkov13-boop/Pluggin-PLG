"""Regression coverage for pydub 0.25.1's Windows decoder compatibility shim."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import struct
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

import pydub_windows_compat as compat


def _fake_pydub_launchers(monkeypatch):
    from pydub import audio_segment, utils

    audio_calls: list[tuple[tuple, dict]] = []
    probe_calls: list[tuple[tuple, dict]] = []

    def audio_popen(*args, **kwargs):
        audio_calls.append((args, kwargs))
        return object()

    def probe_popen(*args, **kwargs):
        probe_calls.append((args, kwargs))
        return object()

    fake_subprocess = ModuleType("pydub_audio_segment_test_subprocess")
    fake_subprocess.Popen = audio_popen
    fake_subprocess.PIPE = object()
    monkeypatch.setattr(audio_segment, "subprocess", fake_subprocess)
    monkeypatch.setattr(utils, "Popen", probe_popen)
    return audio_segment, utils, fake_subprocess, audio_popen, probe_popen, audio_calls, probe_calls


def test_supported_pydub_version_is_explicitly_detected():
    assert compat.installed_pydub_version() == "0.25.1"
    assert compat.supports_installed_pydub() is True


@pytest.mark.skipif(os.name != "nt", reason="CREATE_NO_WINDOW is a Windows-only creation flag")
def test_supported_pydub_paths_hide_only_ffmpeg_and_ffprobe(monkeypatch):
    (
        audio_segment,
        utils,
        fake_subprocess,
        audio_popen,
        probe_popen,
        audio_calls,
        probe_calls,
    ) = _fake_pydub_launchers(monkeypatch)
    original_global_popen = subprocess.Popen

    with compat.hidden_pydub_decoder_windows():
        assert audio_segment.subprocess is not fake_subprocess
        assert utils.Popen is not probe_popen
        assert subprocess.Popen is original_global_popen

        audio_segment.subprocess.Popen(["ffmpeg.exe", "-i", "in.wav"], "positional", stdout="captured")
        utils.Popen(args=["ffprobe", "-of", "json", "in.wav"], stderr="captured")
        audio_segment.subprocess.Popen(["not-ffmpeg.exe", "--version"], marker="unchanged")
        utils.Popen(["not-ffprobe.exe", "--version"], marker="unchanged")

    assert audio_segment.subprocess is fake_subprocess
    assert utils.Popen is probe_popen
    assert len(audio_calls) == 2
    assert len(probe_calls) == 2

    ffmpeg_args, ffmpeg_kwargs = audio_calls[0]
    assert ffmpeg_args == (["ffmpeg.exe", "-i", "in.wav"], "positional")
    assert ffmpeg_kwargs == {
        "stdout": "captured",
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }
    ffprobe_args, ffprobe_kwargs = probe_calls[0]
    assert ffprobe_args == ()
    assert ffprobe_kwargs == {
        "args": ["ffprobe", "-of", "json", "in.wav"],
        "stderr": "captured",
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }
    assert audio_calls[1] == ((["not-ffmpeg.exe", "--version"],), {"marker": "unchanged"})
    assert probe_calls[1] == ((["not-ffprobe.exe", "--version"],), {"marker": "unchanged"})


@pytest.mark.skipif(os.name != "nt", reason="CREATE_NO_WINDOW is a Windows-only creation flag")
def test_existing_creationflags_are_preserved_and_augmented(monkeypatch):
    audio_segment, _utils, _fake, _audio_popen, _probe_popen, audio_calls, _probe_calls = _fake_pydub_launchers(monkeypatch)
    existing = 0x20

    with compat.hidden_pydub_decoder_windows():
        audio_segment.subprocess.Popen(["ffmpeg", "-version"], creationflags=existing, timeout=3)

    assert audio_calls == [
        ((["ffmpeg", "-version"],), {"creationflags": existing | subprocess.CREATE_NO_WINDOW, "timeout": 3})
    ]


def test_non_windows_does_not_patch_pydub(monkeypatch):
    audio_segment, utils, fake_subprocess, _audio_popen, probe_popen, audio_calls, probe_calls = _fake_pydub_launchers(monkeypatch)
    monkeypatch.setattr(compat, "_running_on_windows", lambda: False)

    with compat.hidden_pydub_decoder_windows():
        assert audio_segment.subprocess is fake_subprocess
        assert utils.Popen is probe_popen
        audio_segment.subprocess.Popen(["ffmpeg", "-version"])
        utils.Popen(["ffprobe", "-version"])

    assert audio_calls == [((["ffmpeg", "-version"],), {})]
    assert probe_calls == [((["ffprobe", "-version"],), {})]


def test_unsupported_pydub_version_leaves_internals_untouched(monkeypatch, caplog):
    audio_segment, utils, fake_subprocess, _audio_popen, probe_popen, _audio_calls, _probe_calls = _fake_pydub_launchers(monkeypatch)
    monkeypatch.setattr(compat, "installed_pydub_version", lambda: "0.25.2")
    monkeypatch.setattr(compat, "_running_on_windows", lambda: True)
    monkeypatch.setattr(compat, "_MISMATCH_LOGGED", False)

    with caplog.at_level(logging.WARNING):
        with compat.hidden_pydub_decoder_windows():
            assert audio_segment.subprocess is fake_subprocess
            assert utils.Popen is probe_popen

    assert "compatibility skipped" in caplog.text
    assert "installed=0.25.2" in caplog.text


def _write_float_wav(path: Path) -> None:
    rate = 8000
    channels = 1
    samples = [0.0, 0.25, -0.25, 0.5, -0.5, 0.0] * 160
    payload = struct.pack("<" + "f" * len(samples), *samples)
    fmt = struct.pack("<HHIIHH", 3, channels, rate, rate * channels * 4, channels * 4, 32)
    riff_size = 4 + 8 + len(fmt) + 8 + len(payload)
    path.write_bytes(b"RIFF" + struct.pack("<I", riff_size) + b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(payload)) + payload)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg and ffprobe are required")
def test_float_wav_decode_is_byte_identical_before_and_after_compat(tmp_path):
    from pydub import AudioSegment

    source = tmp_path / "float.wav"
    _write_float_wav(source)

    before = AudioSegment.from_file(source)
    after = compat.audio_segment_from_file(source)

    assert (after.frame_rate, after.channels, after.sample_width, len(after.get_array_of_samples())) == (
        before.frame_rate,
        before.channels,
        before.sample_width,
        len(before.get_array_of_samples()),
    )
    assert hashlib.sha256(after.raw_data).digest() == hashlib.sha256(before.raw_data).digest()

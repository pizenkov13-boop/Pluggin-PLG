"""The export-boundary tripwire on the audio FL actually loads.

Why this exists: the 2026-08-14 investigation into a reported 808 noise ended
at ~85% confidence for exactly one reason -- the damaged intermediate wav had
already been overwritten by the next generation, so there was nothing left to
measure. `flp_writer._verify_published_sample` makes a recurrence provable:
it detects the damage, PRESERVES A COPY out of the overwrite path, and repairs.

See the project issue log (not included) section 0j.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

import flp_writer
from wav_compat import convert_to_pcm24, describe_wav_damage


def _write_float_wav(path: Path, peak: float = 0.8, n: int = 4410) -> Path:
    x = peak * np.sin(2 * np.pi * 32.5 * np.arange(n) / 44100)
    data = np.asarray(x, dtype="<f4").tobytes()
    fmt = struct.pack("<HHIIHH", 3, 1, 44100, 44100 * 4, 4, 32)
    path.write_bytes(
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        + b"fmt " + struct.pack("<I", len(fmt)) + fmt
        + b"data" + struct.pack("<I", len(data)) + data
    )
    return path


# --------------------------------------------------------------------------
# describe_wav_damage — the exact states captured from the pre-fix writer
# --------------------------------------------------------------------------

def test_damage_report_names_the_captured_failure_states(tmp_path):
    src = _write_float_wav(tmp_path / "s.wav")
    good = tmp_path / "s_pcm24.wav"
    assert convert_to_pcm24(src, good)
    assert describe_wav_damage(good) is None

    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    assert "empty" in describe_wav_damage(empty)

    full = good.read_bytes()
    partial = tmp_path / "partial.wav"
    partial.write_bytes(full[: len(full) // 3])
    msg = describe_wav_damage(partial)
    assert "short by" in msg and "bytes" in msg

    hdr = tmp_path / "hdr.wav"
    hdr.write_bytes(full[:20])
    assert "truncated" in describe_wav_damage(hdr)

    assert describe_wav_damage(tmp_path / "gone.wav") is not None


# --------------------------------------------------------------------------
# the tripwire itself
# --------------------------------------------------------------------------

def test_intact_sample_passes_through_untouched(tmp_path):
    src = _write_float_wav(tmp_path / "ok.wav")
    dest = tmp_path / "ok_pcm24.wav"
    assert convert_to_pcm24(src, dest)
    before = dest.read_bytes()

    out = flp_writer._verify_published_sample("808_bass", src, dest, True)
    assert out == dest
    assert dest.read_bytes() == before


def test_damaged_sample_is_preserved_and_repaired(tmp_path, monkeypatch, caplog):
    quarantine = tmp_path / "quarantine"
    monkeypatch.setattr(flp_writer, "_sample_quarantine_dir", lambda: quarantine)

    src = _write_float_wav(tmp_path / "bad.wav")
    dest = tmp_path / "bad_pcm24.wav"
    assert convert_to_pcm24(src, dest)
    healthy = dest.read_bytes()

    # Reproduce the captured corruption: header intact, audio truncated away.
    dest.write_bytes(healthy[: len(healthy) // 4])
    assert describe_wav_damage(dest) is not None

    with caplog.at_level("WARNING"):
        out = flp_writer._verify_published_sample("808_bass", src, dest, True)

    # repaired in place, and the channel still points at the safe copy
    assert out == dest
    assert describe_wav_damage(dest) is None
    assert dest.read_bytes() == healthy

    # THE POINT: the evidence survived, instead of being silently overwritten
    kept = sorted(quarantine.glob("*__808_bass__bad_pcm24.wav"))
    assert len(kept) == 1, "the damaged file must be preserved for diagnosis"
    assert kept[0].read_bytes() == healthy[: len(healthy) // 4]
    note = kept[0].with_suffix(kept[0].suffix + ".txt")
    assert note.is_file()
    body = note.read_text(encoding="utf-8")
    # "reason:", not "damage:", since 2026-08-25: the same preserver now
    # also files a published file that is STRUCTURALLY PERFECT but holds
    # the wrong audio, which this note cannot honestly call damage.
    assert "808_bass" in body and "reason:" in body

    text = caplog.text
    assert "DAMAGED SAMPLE" in text and "808_bass" in text


def test_empty_published_file_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(flp_writer, "_sample_quarantine_dir",
                        lambda: tmp_path / "q")
    src = _write_float_wav(tmp_path / "e.wav")
    dest = tmp_path / "e_pcm24.wav"
    assert convert_to_pcm24(src, dest)
    good = dest.read_bytes()
    dest.write_bytes(b"")

    out = flp_writer._verify_published_sample("hi_hats", src, dest, True)
    assert out == dest
    assert dest.read_bytes() == good
    assert list((tmp_path / "q").glob("*__hi_hats__*")) != []


def test_unrepairable_damage_falls_back_to_the_source_wav(tmp_path, monkeypatch):
    monkeypatch.setattr(flp_writer, "_sample_quarantine_dir",
                        lambda: tmp_path / "q")
    src = _write_float_wav(tmp_path / "u.wav")
    dest = tmp_path / "u_pcm24.wav"
    assert convert_to_pcm24(src, dest)
    dest.write_bytes(b"")
    # source disappears, so the repair cannot succeed
    src.unlink()

    out = flp_writer._verify_published_sample("808_bass", src, dest, True)
    assert out == src, "must not ship a channel pointing at a damaged wav"


def test_quarantine_failure_never_breaks_an_export(tmp_path, monkeypatch):
    def boom():
        raise OSError("no quarantine dir")

    monkeypatch.setattr(flp_writer, "_sample_quarantine_dir", boom)
    src = _write_float_wav(tmp_path / "q.wav")
    dest = tmp_path / "q_pcm24.wav"
    assert convert_to_pcm24(src, dest)
    good = dest.read_bytes()
    dest.write_bytes(b"")

    out = flp_writer._verify_published_sample("808_bass", src, dest, True)
    assert out == dest
    assert dest.read_bytes() == good


def test_tripwire_runs_on_the_real_export_path(tmp_path, monkeypatch):
    """_ensure_fl_safe_sample must route through the verifier, not bypass it."""
    seen = {}

    real = flp_writer._verify_published_sample

    def spy(key, source, dest, converted):
        seen["key"] = key
        return real(key, source, dest, converted)

    monkeypatch.setattr(flp_writer, "_verify_published_sample", spy)
    src = _write_float_wav(tmp_path / "route.wav")
    out = flp_writer._ensure_fl_safe_sample("808_bass", src, tmp_path / "safe")
    assert seen.get("key") == "808_bass"
    assert describe_wav_damage(out) is None

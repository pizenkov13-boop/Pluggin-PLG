"""FL-safe transcode — the module that produces the exact bytes FL plays.

Locked here because nothing else covered it: `wav_compat.convert_to_pcm24` is
the sole owner of the export transcode (only caller:
`flp_writer._ensure_fl_safe_sample`), and it is the LAST code that touches the
audio of the two lanes `sample_saturate.SATURATION` deliberately skips —
`808_bass` and `hi_hats` reach it with no peak ceiling applied by anything
upstream.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from wav_compat import (
    _decode_to_float,
    _parse_wav,
    _peak_normalize,
    convert_to_pcm24,
    needs_fl_conversion,
)


def _write_float_wav(path: Path, samples: np.ndarray, sr: int = 44100, ch: int = 1) -> Path:
    data = np.asarray(samples, dtype="<f4").tobytes()
    fmt = struct.pack("<HHIIHH", 3, ch, sr, sr * ch * 4, ch * 4, 32)
    path.write_bytes(
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        + b"fmt " + struct.pack("<I", len(fmt)) + fmt
        + b"data" + struct.pack("<I", len(data)) + data
    )
    return path


def _decode(path: Path) -> np.ndarray:
    tag, ch, sr, bits, data = _parse_wav(path.read_bytes())
    return _decode_to_float(tag, bits, data)


def _sine(peak: float, n: int = 4410, f: float = 32.5, sr: int = 44100) -> np.ndarray:
    return peak * np.sin(2 * np.pi * f * np.arange(n) / sr)


# --------------------------------------------------------------------------
# The defect: an over-full-scale source was hard-clipped into the one copy FL
# plays. Measured on the shipping library, "XPOSED 808" (peak 1.261) came out
# flat-topped across 14.5 % of the file, in plateaus up to 4.24 ms.
# --------------------------------------------------------------------------

def test_over_full_scale_source_is_scaled_not_clipped(tmp_path):
    src = _write_float_wav(tmp_path / "hot.wav", _sine(1.26))
    dst = tmp_path / "hot_pcm24.wav"
    assert convert_to_pcm24(src, dst)

    out = _decode(dst)
    # Nothing is pinned at full scale for more than a rounding sample: a
    # clipped conversion pins hundreds of consecutive samples there.
    at_fs = np.abs(out) >= (8388606 / 8388608.0)
    if at_fs.any():
        edges = np.flatnonzero(np.diff(np.concatenate(([0], at_fs.view(np.int8), [0]))))
        assert int((edges[1::2] - edges[0::2]).max()) <= 2

    # The waveform SHAPE survives: output is the input times one constant gain.
    src_audio = _decode(src)
    ratio = out[np.abs(src_audio) > 0.05] / src_audio[np.abs(src_audio) > 0.05]
    assert np.allclose(ratio, ratio[0], atol=1e-4)
    assert out.max() == pytest.approx(1.0, abs=1e-3)


def test_a_clipping_conversion_would_have_failed_the_shape_check(tmp_path):
    """Guard the guard: the assertion above genuinely distinguishes the bug."""
    hot = _sine(1.26)
    clipped = np.clip(hot, -1.0, 1.0)
    ratio = clipped[np.abs(hot) > 0.05] / hot[np.abs(hot) > 0.05]
    assert not np.allclose(ratio, ratio[0], atol=1e-4)


@pytest.mark.parametrize("peak", [0.2, 0.5, 0.8657, 0.97, 1.0])
def test_at_or_below_full_scale_is_byte_identical_to_before(tmp_path, peak):
    """The fix must not touch any sample that was already representable —
    every reachable 808/hat/kick/snare/clap in the shipping library."""
    src = _write_float_wav(tmp_path / "ok.wav", _sine(peak))
    dst = tmp_path / "ok_pcm24.wav"
    assert convert_to_pcm24(src, dst)
    got = _decode(dst)
    want = np.clip(_decode(src), -1.0, 1.0)
    assert np.max(np.abs(got - want)) < 1e-6


def test_peak_normalize_leaves_quiet_signals_alone():
    x = _sine(0.4)
    assert np.array_equal(_peak_normalize(x), x)


def test_non_finite_samples_do_not_become_noise():
    """NaN/inf reached int casting as undefined behaviour before."""
    x = np.array([0.5, np.nan, np.inf, -np.inf, -0.5])
    out = _peak_normalize(x)
    assert np.all(np.isfinite(out))
    assert np.all(np.abs(out) <= 1.0)


# --------------------------------------------------------------------------
# Publication safety: every .flp ever exported points at these files by
# absolute path, and the 808's canonical name is re-converted on every export.
# --------------------------------------------------------------------------

def test_reconversion_does_not_touch_an_already_correct_file(tmp_path):
    src = _write_float_wav(tmp_path / "a.wav", _sine(0.8))
    dst = tmp_path / "a_pcm24.wav"
    assert convert_to_pcm24(src, dst)
    first = dst.stat().st_mtime_ns
    payload = dst.read_bytes()

    assert convert_to_pcm24(src, dst)
    assert dst.stat().st_mtime_ns == first, "re-export rewrote an identical file"
    assert dst.read_bytes() == payload


def test_changed_source_does_replace_the_published_file(tmp_path):
    src = _write_float_wav(tmp_path / "b.wav", _sine(0.8))
    dst = tmp_path / "b_pcm24.wav"
    assert convert_to_pcm24(src, dst)
    before = dst.read_bytes()

    _write_float_wav(src, _sine(0.4))
    assert convert_to_pcm24(src, dst)
    assert dst.read_bytes() != before


def test_no_temp_files_are_left_behind(tmp_path):
    src = _write_float_wav(tmp_path / "c.wav", _sine(1.3))
    dst = tmp_path / "c_pcm24.wav"
    assert convert_to_pcm24(src, dst)
    assert [p.name for p in tmp_path.glob("*.tmp")] == []


# --------------------------------------------------------------------------
# Contention. os.replace raises PermissionError on Windows while another
# process holds the target open — which is exactly the situation this code
# exists for (FL has the project loaded). Letting that propagate would make
# convert_to_pcm24 report failure and flp_writer fall back to the raw library
# path, whose U+007F chars load "red"/silent in FL.
# --------------------------------------------------------------------------

def test_publish_survives_a_reader_holding_the_file_open(tmp_path):
    from wav_compat import _is_intact_wav

    src = _write_float_wav(tmp_path / "held.wav", _sine(0.8))
    dst = tmp_path / "held_pcm24.wav"
    assert convert_to_pcm24(src, dst)
    good = dst.read_bytes()

    _write_float_wav(src, _sine(0.55))  # force a real replace
    fh = open(dst, "rb")
    try:
        assert convert_to_pcm24(src, dst) is True
        # Whatever happened, the published file is a COMPLETE wav — never the
        # half-written state the old truncate-and-stream writer could leave.
        assert _is_intact_wav(dst)
        assert dst.read_bytes() in (good, _read_expected(src))
    finally:
        fh.close()
    assert [p.name for p in tmp_path.glob("*.tmp")] == []


def _read_expected(src: Path) -> bytes:
    import tempfile

    out = Path(tempfile.mkdtemp()) / "x.wav"
    convert_to_pcm24(src, out)
    return out.read_bytes()


def test_is_intact_wav_rejects_the_half_written_states(tmp_path):
    from wav_compat import _is_intact_wav

    src = _write_float_wav(tmp_path / "t.wav", _sine(0.8))
    dst = tmp_path / "t_pcm24.wav"
    assert convert_to_pcm24(src, dst)
    full = dst.read_bytes()
    assert _is_intact_wav(dst)

    # exactly the states the race harness captured from the old writer
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    assert not _is_intact_wav(empty)

    partial = tmp_path / "partial.wav"
    partial.write_bytes(full[: len(full) // 3])
    assert not _is_intact_wav(partial), "a truncated wav must not pass"

    header_only = tmp_path / "hdr.wav"
    header_only.write_bytes(full[:44])
    assert not _is_intact_wav(header_only)

    assert not _is_intact_wav(tmp_path / "does_not_exist.wav")


def test_needs_fl_conversion_still_targets_float_and_odd_depths(tmp_path):
    src = _write_float_wav(tmp_path / "f.wav", _sine(0.5))
    assert needs_fl_conversion(src) is True
    dst = tmp_path / "f_pcm24.wav"
    assert convert_to_pcm24(src, dst)
    assert needs_fl_conversion(dst) is False


# --------------------------------------------------------------------------
# The second question. Every check above answers "is this a loadable WAV".
# A published file holding some OTHER sample passes all of them, and that is
# the one state the container checks cannot see (the project issue log (not included) 0y.1).
# --------------------------------------------------------------------------


def test_content_check_passes_a_correctly_published_file(tmp_path):
    from wav_compat import published_content_mismatch

    src = _write_float_wav(tmp_path / "src.wav", _sine(0.8))
    dst = tmp_path / "pub.wav"
    assert convert_to_pcm24(src, dst)
    assert published_content_mismatch(src, dst) is None


def test_content_check_catches_a_whole_wav_holding_the_wrong_audio(tmp_path):
    """The exact hole: same size, valid header, different sample."""
    from wav_compat import describe_wav_damage, published_content_mismatch

    src = _write_float_wav(tmp_path / "src.wav", _sine(0.8))
    other = _write_float_wav(tmp_path / "other.wav", _sine(0.8, f=110.0))
    dst = tmp_path / "pub.wav"
    assert convert_to_pcm24(other, dst)          # publish the WRONG source

    assert dst.stat().st_size > 44
    assert describe_wav_damage(dst) is None      # container checks see nothing
    reason = published_content_mismatch(src, dst)
    assert reason is not None
    assert "differs" in reason


def test_content_check_reports_a_size_difference_separately(tmp_path):
    from wav_compat import published_content_mismatch

    src = _write_float_wav(tmp_path / "src.wav", _sine(0.8, n=4410))
    short = _write_float_wav(tmp_path / "short.wav", _sine(0.8, n=2205))
    dst = tmp_path / "pub.wav"
    assert convert_to_pcm24(short, dst)
    reason = published_content_mismatch(src, dst)
    assert reason is not None and "size differs" in reason


def test_content_check_never_false_alarms_on_an_unreadable_source(tmp_path):
    """A source this module cannot read is a different owner's failure —
    reporting it here would quarantine a healthy published file."""
    from wav_compat import published_content_mismatch

    src = tmp_path / "notawav.wav"
    src.write_bytes(b"not a wav at all")
    dst = tmp_path / "pub.wav"
    _write_float_wav(dst, _sine(0.5))
    assert published_content_mismatch(src, dst) is None

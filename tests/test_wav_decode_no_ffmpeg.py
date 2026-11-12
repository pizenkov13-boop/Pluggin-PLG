"""WAV (PCM + IEEE float) and FLAC must decode without ffmpeg.

Producer kits are mostly IEEE-float WAVs. stdlib ``wave`` refuses them and pydub
needs ffmpeg for them; an ordinary user has no ffmpeg. Every float file then
looked unreadable — duration ``None``, empty pitch passport, audio safety gates
skipped — and the user's own 808 was silently replaced by a synthetic one.

The ``no_ffmpeg`` fixture makes any attempt to reach pydub/ffmpeg observable,
so these tests prove the ffmpeg-free path is the one being used.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np
import pytest

import audio_decode
import audio_features
import library_paths
import sample_pitch_passport as passport
import sample_saturate
import wav_decode
from wav_decode import WavDecodeError

RATE = 44100


# --- helpers -----------------------------------------------------------------------


def _chunk(cid: bytes, body: bytes) -> bytes:
    return cid + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) & 1 else b"")


def wav_bytes(
    samples: np.ndarray,
    *,
    tag: int = 3,
    bits: int = 32,
    rate: int = RATE,
    extensible: bool = False,
    before_data: bytes = b"",
    after_data: bytes = b"",
    declared_data_size: int | None = None,
    block_align: int | None = None,
) -> bytes:
    """Hand-built WAV so the tests do not depend on the code under test to make it."""
    arr = np.asarray(samples, dtype=np.float64)
    channels = arr.shape[1] if arr.ndim == 2 else 1
    flat = arr.reshape(-1)
    if tag == 3:
        payload = flat.astype("<f4" if bits == 32 else "<f8").tobytes()
    elif bits == 8:
        payload = (flat * 127.0 + 128.0).astype(np.uint8).tobytes()
    elif bits == 16:
        payload = (flat * 32767.0).astype("<i2").tobytes()
    elif bits == 24:
        ints = (flat * 8388607.0).astype("<i4")
        payload = b"".join(struct.pack("<i", int(v))[:3] for v in ints)
    else:
        payload = (flat * 2147483647.0).astype("<i4").tobytes()
    frame = channels * bits // 8
    align = frame if block_align is None else block_align
    if extensible:
        guid = struct.pack("<H", tag) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
        # 16 base bytes + cbSize(22) + valid bits + channel mask + 16-byte sub-format GUID
        fmt = struct.pack("<HHIIHH", 0xFFFE, channels, rate, rate * frame, align, bits)
        fmt += struct.pack("<HHI", 22, bits, 3) + guid
    else:
        fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * frame, align, bits)
    data_body = payload
    data_chunk = b"data" + struct.pack("<I", len(data_body) if declared_data_size is None else declared_data_size) + data_body
    if len(data_body) & 1 and declared_data_size is None:
        data_chunk += b"\x00"
    body = b"WAVE" + _chunk(b"fmt ", fmt) + before_data + data_chunk + after_data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def tone(seconds: float = 1.0, hz: float = 55.0, rate: int = RATE) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (0.8 * np.sin(2 * np.pi * hz * t) * np.exp(-t * 1.2)).astype(np.float64)


def write(tmp_path: Path, name: str, blob: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(blob)
    return path


@pytest.fixture
def no_ffmpeg(monkeypatch):
    """No ffmpeg on PATH, and record every attempt to reach pydub."""
    import os

    monkeypatch.setenv("PATH", os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if "ffmpeg" not in p.lower()))
    calls: list[str] = []

    def _boom(*args, **kwargs):
        calls.append(str(args[0]) if args else "?")
        raise FileNotFoundError("ffmpeg is not installed")

    import pydub_windows_compat

    monkeypatch.setattr(pydub_windows_compat, "audio_segment_from_file", _boom)
    return calls


# --- the decoder ---------------------------------------------------------------------


@pytest.mark.parametrize(("tag", "bits", "tol"), [(3, 32, 1e-6), (3, 64, 1e-12), (1, 16, 1e-3), (1, 24, 1e-6), (1, 32, 1e-8), (1, 8, 2e-2)])
def test_every_supported_format_round_trips(tmp_path, tag, bits, tol):
    src = tone(0.2)
    audio = wav_decode.read_wav(write(tmp_path, "a.wav", wav_bytes(src, tag=tag, bits=bits)))
    assert audio.rate == RATE and audio.channels == 1 and audio.samples.shape == src.shape
    assert np.max(np.abs(audio.samples - src)) < tol


def test_stereo_keeps_channels_and_mono_mix_averages_them(tmp_path):
    left, right = tone(0.1), -tone(0.1)
    path = write(tmp_path, "st.wav", wav_bytes(np.stack([left, right], axis=1)))
    audio = wav_decode.read_wav(path)
    assert audio.samples.shape == (len(left), 2) and audio.channels == 2
    mono, rate, duration = wav_decode.read_wav_mono(path)
    assert np.allclose(mono, 0.0, atol=1e-6) and rate == RATE and duration == pytest.approx(0.1, abs=1e-3)


def test_extensible_float_is_read_as_float(tmp_path):
    src = tone(0.1)
    audio = wav_decode.read_wav(write(tmp_path, "ext.wav", wav_bytes(src, extensible=True)))
    assert np.max(np.abs(audio.samples - src)) < 1e-6


def test_a_data_chunk_that_overstates_its_size_is_clamped_to_the_file(tmp_path):
    src = tone(0.1)
    for declared in (10**6, 0xFFFFFFFF):
        path = write(tmp_path, f"trunc{declared}.wav", wav_bytes(src, declared_data_size=declared))
        header = wav_decode.read_header(path)
        assert header.frames == len(src)


def test_other_chunks_around_data_are_skipped(tmp_path):
    src = tone(0.1)
    blob = wav_bytes(
        src,
        before_data=_chunk(b"LIST", b"odd"),  # odd length -> pad byte
        after_data=_chunk(b"smpl", b"\x00" * 36),
    )
    assert np.max(np.abs(wav_decode.read_wav(write(tmp_path, "chunks.wav", blob)).samples - src)) < 1e-6


def test_max_ms_decodes_only_the_head_but_reports_the_whole_duration(tmp_path):
    path = write(tmp_path, "long.wav", wav_bytes(tone(2.0)))
    samples, rate, duration = wav_decode.read_wav_mono(path, max_ms=500)
    assert samples.size == int(RATE * 0.5) and duration == pytest.approx(2.0, abs=1e-3)


def test_nan_and_inf_in_float_files_become_silence(tmp_path):
    bad = np.array([0.5, np.nan, np.inf, -np.inf, -0.5])
    out = wav_decode.read_wav(write(tmp_path, "nan.wav", wav_bytes(bad))).samples
    assert np.isfinite(out).all() and out[1] == out[2] == out[3] == 0.0


def test_duration_comes_from_the_header_alone(tmp_path):
    path = write(tmp_path, "d.wav", wav_bytes(tone(1.5)))
    assert wav_decode.duration_seconds(path) == pytest.approx(1.5, abs=1e-3)


@pytest.mark.parametrize(
    "blob",
    [
        b"",
        b"not a wav at all",
        b"RIFF\x04\x00\x00\x00WAVE",  # no chunks
        wav_bytes(tone(0.05), tag=1, bits=16)[:30],  # cut inside fmt
    ],
)
def test_garbage_raises_a_value_error_subclass(tmp_path, blob):
    with pytest.raises(WavDecodeError):
        wav_decode.read_wav(write(tmp_path, "bad.wav", blob))
    assert issubclass(WavDecodeError, ValueError)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tag": 3, "bits": 16},  # float has to be 32/64
        {"tag": 1, "bits": 16, "block_align": 6},  # inconsistent alignment
    ],
)
def test_unsupported_layouts_are_refused_not_misdecoded(tmp_path, kwargs):
    with pytest.raises(WavDecodeError):
        wav_decode.read_wav(write(tmp_path, "u.wav", wav_bytes(tone(0.05), **kwargs)))


def test_adpcm_or_vorbis_in_a_wav_container_is_refused(tmp_path):
    blob = bytearray(wav_bytes(tone(0.05), tag=1, bits=16))
    i = blob.find(b"fmt ")
    struct.pack_into("<H", blob, i + 8, 26447)  # Ogg Vorbis inside RIFF, as in a real kit
    with pytest.raises(WavDecodeError):
        wav_decode.read_wav(write(tmp_path, "vorbis.wav", bytes(blob)))


def test_sample_saturate_reads_through_the_shared_decoder(tmp_path):
    src = tone(0.1)
    audio, rate, channels = sample_saturate._read_wav(write(tmp_path, "s.wav", wav_bytes(src, extensible=True)))
    assert rate == RATE and channels == 1 and np.max(np.abs(audio - src)) < 1e-6
    with pytest.raises(ValueError):
        sample_saturate._read_wav(write(tmp_path, "bad.wav", b"junk"))


# --- FLAC ------------------------------------------------------------------------------


@pytest.fixture
def sf():
    return pytest.importorskip("soundfile")


@pytest.mark.parametrize("subtype", ["PCM_16", "PCM_24"])
def test_flac_duration_and_samples(tmp_path, sf, subtype):
    src = tone(0.5)
    path = tmp_path / "a.flac"
    sf.write(str(path), src, RATE, format="FLAC", subtype=subtype)
    assert audio_decode.duration_seconds(path) == pytest.approx(0.5, abs=1e-3)
    samples, rate, duration = audio_decode.read_mono(path)
    assert rate == RATE and samples.shape == src.shape and duration == pytest.approx(0.5, abs=1e-3)
    assert np.max(np.abs(samples - src)) < (1e-3 if subtype == "PCM_16" else 1e-5)


def test_flac_stereo_is_mixed_down_and_max_ms_limits_the_head(tmp_path, sf):
    src = tone(1.0)
    path = tmp_path / "st.flac"
    sf.write(str(path), np.stack([src, src], axis=1), RATE, format="FLAC", subtype="PCM_24")
    samples, _rate, duration = audio_decode.read_mono(path, max_ms=250)
    assert samples.ndim == 1 and samples.size == int(RATE * 0.25) and duration == pytest.approx(1.0, abs=1e-3)


def test_a_corrupt_flac_is_an_audio_decode_error(tmp_path, sf):
    path = write(tmp_path, "bad.flac", b"fLaC" + b"\x00" * 64)
    with pytest.raises(audio_decode.AudioDecodeError):
        audio_decode.duration_seconds(path)
    with pytest.raises(audio_decode.AudioDecodeError):
        audio_decode.read_mono(path)


def test_without_soundfile_flac_falls_back_instead_of_crashing(tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "soundfile", None)  # makes `import soundfile` raise
    path = write(tmp_path, "a.flac", b"fLaC")
    with pytest.raises(audio_decode.AudioDecodeError):
        audio_decode.duration_seconds(path)


def test_mp3_and_ogg_are_not_handled_here(tmp_path):
    for name in ("a.mp3", "a.ogg", "a.aif"):
        assert not audio_decode.can_decode(name)
        with pytest.raises(audio_decode.AudioDecodeError):
            audio_decode.read_mono(write(tmp_path, name, b"x"))
        with pytest.raises(audio_decode.AudioDecodeError):
            audio_decode.duration_seconds(tmp_path / name)


# --- the three consumers, with ffmpeg absent --------------------------------------------


def test_duration_of_a_float_wav_needs_no_ffmpeg(tmp_path, no_ffmpeg):
    path = write(tmp_path, "d1.wav", wav_bytes(tone(1.25)))
    assert library_paths.audio_duration_seconds(path) == pytest.approx(1.25, abs=1e-3)
    assert no_ffmpeg == []


def test_duration_of_a_flac_needs_no_ffmpeg(tmp_path, sf, no_ffmpeg):
    path = tmp_path / "d2.flac"
    sf.write(str(path), tone(0.75), RATE, format="FLAC")
    assert library_paths.audio_duration_seconds(path) == pytest.approx(0.75, abs=1e-3)
    assert no_ffmpeg == []


def test_a_wav_the_light_decoder_refuses_still_goes_to_pydub(tmp_path, monkeypatch):
    class Segment:
        def __len__(self):
            return 1234  # ms

    import pydub_windows_compat

    monkeypatch.setattr(pydub_windows_compat, "audio_segment_from_file", lambda *_a, **_k: Segment())
    assert library_paths.audio_duration_seconds(write(tmp_path, "weird.wav", b"RIFFjunk")) == pytest.approx(1.234)


def test_features_of_a_float_wav_need_no_ffmpeg_and_match_the_pcm_version(tmp_path, no_ffmpeg):
    # 32-bit PCM as the reference: 16-bit would quantise the near-silent tail of
    # the decaying tone and shift the spectral centroid on its own.
    src = tone(1.0, hz=60.0)
    as_float = audio_features.analyze(write(tmp_path, "f.wav", wav_bytes(src)))
    as_pcm = audio_features.analyze(write(tmp_path, "p.wav", wav_bytes(src, tag=1, bits=32)))
    assert as_float is not None and as_pcm is not None and no_ffmpeg == []
    for key in ("rms", "centroid", "pitch", "duration_s"):
        assert as_float[key] == pytest.approx(as_pcm[key], rel=0.05, abs=1e-3), key


def test_features_of_a_flac_need_no_ffmpeg(tmp_path, sf, no_ffmpeg):
    path = tmp_path / "f.flac"
    sf.write(str(path), tone(1.0), RATE, format="FLAC", subtype="PCM_24")
    feats = audio_features.analyze(path)
    assert feats is not None and feats["duration_s"] == pytest.approx(1.0, abs=1e-3) and no_ffmpeg == []
    assert audio_features.rms_dbfs(path) is not None


def test_a_stale_cached_none_from_an_ffmpegless_run_is_not_reused(tmp_path, monkeypatch, no_ffmpeg):
    path = write(tmp_path, "cached.wav", wav_bytes(tone(0.5)))
    st = path.stat()
    monkeypatch.setattr(audio_features, "_cache", {f"v2:{path}:{int(st.st_mtime)}:{st.st_size}": None})
    monkeypatch.setattr(audio_features, "_save_cache", lambda: None)
    assert audio_features.analyze_cached(path) is not None


def test_pitch_passport_of_a_float_808_needs_no_ffmpeg(tmp_path, no_ffmpeg):
    src = tone(1.5, hz=55.0)
    float_pp = passport.sample_pitch_passport(write(tmp_path, "808f.wav", wav_bytes(src)))
    pcm_pp = passport.sample_pitch_passport(write(tmp_path, "808p.wav", wav_bytes(src, tag=1, bits=24)))
    assert no_ffmpeg == []
    assert float_pp.get("root_trusted") is True, float_pp
    for key in ("root_midi", "root_trusted", "weak_pitch_classes"):
        assert float_pp.get(key) == pcm_pp.get(key), key


def test_pitch_passport_of_a_flac_808_needs_no_ffmpeg(tmp_path, sf, no_ffmpeg):
    path = tmp_path / "808.flac"
    sf.write(str(path), tone(1.5, hz=55.0), RATE, format="FLAC", subtype="PCM_24")
    pp = passport.sample_pitch_passport(path)
    assert no_ffmpeg == [] and pp.get("root_trusted") is True, pp


def test_the_808_gates_judge_a_float_808_without_ffmpeg(tmp_path, no_ffmpeg):
    """The end of the chain the user saw: not 808_root_unverified, and a real reason
    only when the audio really fails."""
    from sample_match import track_candidate_rejection_reason

    path = write(tmp_path, "808.wav", wav_bytes(tone(1.5, hz=55.0)))
    assert track_candidate_rejection_reason(path, "808_bass") != "808_root_unverified"
    assert no_ffmpeg == []
    assert math.isfinite(library_paths.audio_duration_seconds(path))

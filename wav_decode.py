"""WAV decoding: PCM 8/16/24/32-bit and IEEE float 32/64-bit with numpy alone, plus the compressed WAV
formats (A-law, mu-law, IMA / MS ADPCM, Ogg Vorbis stored in a WAV) through ``soundfile``.

Producer kits ship mostly WAVE_FORMAT_IEEE_FLOAT (format tag 3) — about 86% of
one real library — and stdlib ``wave`` refuses those. Decoding them through
pydub needs ffmpeg, which an ordinary user does not have and PLG does not ship.
Without it every such file looked unreadable: duration ``None``, empty pitch
passport, the audio safety gates silently skipped, and the user's own 808 was
replaced by a synthetic one without a word. This module reads them with numpy.

Also handles WAVE_FORMAT_EXTENSIBLE (tag 0xFFFE, the real tag lives in the
sub-format GUID), a ``data`` chunk whose declared size runs past the end of the
file (truncated or streamed), and chunks before/after ``fmt``/``data``.

The compressed formats (``SPECIAL_TAGS``) are decoded by libsndfile, which ships inside the soundfile
wheel and in PLG.exe; the Ogg-in-WAV tag (0x674F) is a plain Ogg stream inside the ``data`` chunk, which
is cut out and decoded as such. They used to be read as if they were PCM, which turned them into noise.
``decodable(path)`` is the one answer to "can PLG read this WAV at all": a file it cannot read (an
unknown tag, a damaged stream, no libsndfile) must never be chosen, previewed or exported. Anything
else (mp3-in-WAV, RF64, 20-bit packed...) raises ``WavDecodeError`` and stays with the callers'
pydub fallback.

Samples are returned as float64, PCM scaled to [-1, 1); float files are
returned as stored (they may exceed 1.0) with NaN/inf zeroed.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

_PCM = 1
_FLOAT = 3
_EXTENSIBLE = 0xFFFE
_SUPPORTED_BITS = {_PCM: (8, 16, 24, 32), _FLOAT: (32, 64)}
_MS_ADPCM, _ALAW, _MULAW, _IMA_ADPCM = 2, 6, 7, 0x11
#: Ogg Vorbis stored in a WAV by the Ogg ACM codec (0x674F is "Vorbis mode 1"; the others are its siblings).
OGG_TAGS = frozenset({0x674F, 0x6750, 0x6751, 0x676F, 0x6770, 0x6771})
#: Compressed formats PLG decodes through libsndfile instead of reading them as PCM.
SPECIAL_TAGS = frozenset({_MS_ADPCM, _ALAW, _MULAW, _IMA_ADPCM}) | OGG_TAGS
_MAX_CHUNKS = 256  # a real WAV has a handful; bounds a corrupt chunk walk


class WavDecodeError(ValueError):
    """The file is not a WAV this module can decode."""


@dataclass(frozen=True)
class WavHeader:
    format_tag: int  # 1 = PCM, 3 = IEEE float (EXTENSIBLE already resolved)
    channels: int
    rate: int
    bits: int  # container bits per sample
    data_offset: int
    data_bytes: int  # clamped to what the file really holds

    @property
    def frame_bytes(self) -> int:
        return self.channels * (self.bits // 8)

    @property
    def frames(self) -> int:
        return self.data_bytes // self.frame_bytes

    @property
    def duration_s(self) -> float:
        return self.frames / self.rate


@dataclass(frozen=True)
class WavAudio:
    samples: np.ndarray  # (frames,) for mono, (frames, channels) otherwise
    rate: int
    channels: int
    total_frames: int  # frames in the whole file, not just the ones decoded


def read_header(path: Path | str) -> WavHeader:
    """Parse the chunk headers only — no audio is read."""
    p = Path(path)
    size_on_disk = p.stat().st_size
    with p.open("rb") as fh:
        head = fh.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise WavDecodeError("not a RIFF/WAVE file")
        fmt: tuple[int, int, int, int, int] | None = None
        data: tuple[int, int] | None = None
        offset = 12
        for _ in range(_MAX_CHUNKS):
            if offset + 8 > size_on_disk:
                break
            fh.seek(offset)
            chunk = fh.read(8)
            if len(chunk) < 8:
                break
            cid, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            body = offset + 8
            if cid == b"fmt " and fmt is None:
                raw = fh.read(min(size, 40))
                fmt = _parse_fmt(raw)
            elif cid == b"data" and data is None:
                # A streamed/truncated file declares more than it holds.
                data = (body, max(0, min(size, size_on_disk - body)))
            if fmt is not None and data is not None:
                break
            offset = body + size + (size & 1)
    if fmt is None or data is None:
        raise WavDecodeError("missing fmt/data chunk")
    tag, channels, rate, bits, block_align = fmt
    if block_align != channels * (bits // 8):
        raise WavDecodeError(f"unsupported block alignment {block_align} ({channels}ch/{bits}bit)")
    return WavHeader(tag, channels, rate, bits, data[0], data[1])


def _parse_fmt(raw: bytes) -> tuple[int, int, int, int, int]:
    if len(raw) < 16:
        raise WavDecodeError("fmt chunk too short")
    tag, channels, rate, _byte_rate, block_align, bits = struct.unpack("<HHIIHH", raw[:16])
    if tag == _EXTENSIBLE:
        if len(raw) < 26:
            raise WavDecodeError("truncated WAVE_FORMAT_EXTENSIBLE fmt chunk")
        tag = struct.unpack("<H", raw[24:26])[0]  # first 2 bytes of the sub-format GUID
    if tag not in _SUPPORTED_BITS:
        raise WavDecodeError(f"unsupported WAV format tag {tag}")
    if bits not in _SUPPORTED_BITS[tag]:
        raise WavDecodeError(f"unsupported {bits}-bit WAV format {tag}")
    if channels <= 0 or rate <= 0:
        raise WavDecodeError("invalid channel count or sample rate")
    return tag, channels, rate, bits, block_align


def duration_seconds(path: Path | str) -> float:
    """Duration from the header alone (raises ``WavDecodeError`` if undecodable)."""
    if format_tag(path) in SPECIAL_TAGS:
        samples, rate = _special_audio(path)
        return len(samples) / rate
    return read_header(path).duration_s


def format_tag(path: Path | str) -> int | None:
    """The WAV format tag (EXTENSIBLE resolved), from the ``fmt`` chunk alone; None when it is not a WAV."""
    p = Path(path)
    try:
        size = p.stat().st_size
        with p.open("rb") as fh:
            head = fh.read(12)
            if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                return None
            offset = 12
            for _ in range(_MAX_CHUNKS):
                if offset + 8 > size:
                    break
                fh.seek(offset)
                chunk = fh.read(8)
                if len(chunk) < 8:
                    break
                cid, length = chunk[:4], struct.unpack("<I", chunk[4:])[0]
                if cid == b"fmt ":
                    raw = fh.read(min(length, 40))
                    if len(raw) < 16:
                        return None
                    tag = struct.unpack("<H", raw[:2])[0]
                    if tag == _EXTENSIBLE and len(raw) >= 26:
                        tag = struct.unpack("<H", raw[24:26])[0]
                    return tag
                offset += 8 + length + (length & 1)
    except OSError:
        return None
    return None


def _data_chunk(path: Path | str) -> bytes:
    p = Path(path)
    size = p.stat().st_size
    with p.open("rb") as fh:
        offset = 12
        for _ in range(_MAX_CHUNKS):
            if offset + 8 > size:
                break
            fh.seek(offset)
            chunk = fh.read(8)
            if len(chunk) < 8:
                break
            cid, length = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            if cid == b"data":
                return fh.read(max(0, min(length, size - offset - 8)))
            offset += 8 + length + (length & 1)
    raise WavDecodeError("no data chunk")


def _soundfile():
    try:
        import soundfile
    except (ImportError, OSError) as exc:  # OSError: libsndfile failed to load
        raise WavDecodeError(f"soundfile unavailable: {exc}") from exc
    return soundfile


def _special_audio(path: Path | str) -> tuple[np.ndarray, int]:
    """Decode a compressed WAV: ``(frames x channels float64, rate)``; raises ``WavDecodeError``."""
    sf = _soundfile()
    tag = format_tag(path)
    try:
        if tag in OGG_TAGS:
            data, rate = sf.read(io.BytesIO(_data_chunk(path)), dtype="float64", always_2d=True)
        else:
            data, rate = sf.read(str(path), dtype="float64", always_2d=True)
    except (RuntimeError, OSError, ValueError) as exc:
        raise WavDecodeError(f"unreadable compressed WAV (format {tag}): {exc}") from exc
    if rate <= 0 or len(data) == 0:
        raise WavDecodeError("compressed WAV holds no audio")
    return np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0), int(rate)


def decodable(path: Path | str) -> bool:
    """Can PLG read this file's audio? PCM / float and the compressed WAVs it decodes: yes; a WAV in any
    other format, or a compressed one that does not decode: no. A file that is not a WAV at all, or whose
    header cannot be read, is not judged here (other checks own that)."""
    p = Path(path)
    try:
        stat = p.stat()
    except OSError:
        return True
    return _decodable_cached(str(p.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=4096)
def _decodable_cached(resolved: str, _mtime_ns: int, _size: int) -> bool:
    tag = format_tag(resolved)
    if tag is None or tag in (_PCM, _FLOAT):
        return True
    if tag not in SPECIAL_TAGS:
        return False
    try:
        sf = _soundfile()
        if tag in OGG_TAGS:
            ogg = _data_chunk(resolved)
            if ogg[:4] != b"OggS":
                return False
            info = sf.info(io.BytesIO(ogg))
        else:
            info = sf.info(resolved)
    except (WavDecodeError, RuntimeError, OSError, ValueError):
        return False
    return info.frames > 0 and info.samplerate > 0


def read_wav(path: Path | str, *, max_ms: int | None = None) -> WavAudio:
    """Decode the file (or just its first ``max_ms`` milliseconds)."""
    if format_tag(path) in SPECIAL_TAGS:
        data, rate = _special_audio(path)
        total = len(data)
        if max_ms is not None:
            data = data[: min(total, max(1, int(rate * max_ms / 1000)))]
        return WavAudio(data[:, 0] if data.shape[1] == 1 else data, rate, data.shape[1], total)
    header = read_header(path)
    frames = header.frames
    if max_ms is not None:
        frames = min(frames, max(1, int(header.rate * max_ms / 1000)))
    with Path(path).open("rb") as fh:
        fh.seek(header.data_offset)
        raw = fh.read(frames * header.frame_bytes)
    frames = len(raw) // header.frame_bytes
    raw = raw[: frames * header.frame_bytes]
    samples = _decode(raw, header)
    if header.channels > 1:
        samples = samples.reshape(-1, header.channels)
    return WavAudio(samples, header.rate, header.channels, header.frames)


def read_wav_mono(path: Path | str, *, max_ms: int | None = None) -> tuple[np.ndarray, int, float]:
    """``(mono samples, rate, whole-file duration in seconds)``."""
    audio = read_wav(path, max_ms=max_ms)
    samples = audio.samples.mean(axis=1) if audio.channels > 1 else audio.samples
    return samples, audio.rate, audio.total_frames / audio.rate


def _decode(raw: bytes, header: WavHeader) -> np.ndarray:
    tag, bits = header.format_tag, header.bits
    if tag == _FLOAT:
        out = np.frombuffer(raw, dtype="<f4" if bits == 32 else "<f8").astype(np.float64)
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
    if bits == 8:
        return (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    if bits == 16:
        return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if bits == 24:
        packed = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        ints = packed[:, 0] | (packed[:, 1] << 8) | (packed[:, 2] << 16)
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        return ints.astype(np.float64) / 8388608.0
    return np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0

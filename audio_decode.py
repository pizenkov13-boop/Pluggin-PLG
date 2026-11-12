"""Which lightweight decoder can read this file without ffmpeg?

  .wav   numpy (``wav_decode``): PCM 8/16/24/32, IEEE float 32/64, EXTENSIBLE
  .flac  ``soundfile`` (libsndfile ships inside its wheel and is collected by
         PyInstaller's soundfile hook)

Everything else (mp3, ogg, aiff, ADPCM WAV...) raises ``AudioDecodeError`` and
stays with the callers' existing pydub fallback — deliberately: decoding mp3
would mean bundling ffmpeg or a third-party C decoder.

``AudioDecodeError`` subclasses ``wav_decode.WavDecodeError`` (a ``ValueError``),
so callers can catch either.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from wav_decode import WavDecodeError, duration_seconds as _wav_duration, read_wav_mono as _wav_mono

LIGHT_SUFFIXES = frozenset({".wav", ".flac"})


class AudioDecodeError(WavDecodeError):
    """No lightweight decoder can read this file."""


def can_decode(path: Path | str) -> bool:
    """True when the extension is one this module handles (says nothing about
    whether the file's content is valid)."""
    return Path(path).suffix.lower() in LIGHT_SUFFIXES


def _soundfile():
    try:
        import soundfile
    except (ImportError, OSError) as exc:  # OSError: libsndfile failed to load
        raise AudioDecodeError(f"soundfile unavailable: {exc}") from exc
    return soundfile


def duration_seconds(path: Path | str) -> float:
    """Whole-file duration from the header alone."""
    suffix = Path(path).suffix.lower()
    if suffix == ".wav":
        return _wav_duration(path)
    if suffix == ".flac":
        sf = _soundfile()
        try:
            info = sf.info(str(path))
        except (RuntimeError, OSError) as exc:
            raise AudioDecodeError(f"unreadable FLAC: {exc}") from exc
        if info.frames <= 0 or info.samplerate <= 0:
            raise AudioDecodeError("FLAC header does not declare its length")
        return info.frames / info.samplerate
    raise AudioDecodeError(f"no lightweight decoder for {suffix or 'this file'}")


def read_mono(path: Path | str, *, max_ms: int | None = None) -> tuple[np.ndarray, int, float]:
    """``(mono float64 samples, rate, whole-file duration in seconds)``.

    ``max_ms`` limits decoding to the head of the file; the duration returned is
    still the whole file's.
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".wav":
        return _wav_mono(path, max_ms=max_ms)
    if suffix != ".flac":
        raise AudioDecodeError(f"no lightweight decoder for {suffix or 'this file'}")
    sf = _soundfile()
    try:
        with sf.SoundFile(str(path)) as handle:
            rate, total = handle.samplerate, handle.frames
            if rate <= 0 or total <= 0:
                raise AudioDecodeError("FLAC header does not declare its length")
            want = total if max_ms is None else min(total, max(1, int(rate * max_ms / 1000)))
            data = handle.read(frames=want, dtype="float64", always_2d=True)
    except (RuntimeError, OSError) as exc:
        raise AudioDecodeError(f"unreadable FLAC: {exc}") from exc
    return data.mean(axis=1), rate, total / rate

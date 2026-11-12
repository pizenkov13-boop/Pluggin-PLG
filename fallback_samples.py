"""Last-resort one-shots that pass every export gate BY CONSTRUCTION.

The bundled starter/bundled_sounds families cannot be that guarantee: every
bundled hat is white-noise-plus-tone (centroid ~10.7 kHz, zcr ~0.5) and trips
``metallic_closed_hat_tick``, and every bundled 808 is 0.4-0.8 s long, so the
tail gate rejects it for any note over half a beat. These are generated at run
time instead, sized to the song, cached under the user data dir, and locked by
tests that push them through the very gates that reject everything else.

Only ``lane_fallback`` should call this.
"""

from __future__ import annotations

import logging
import math
import random
from pathlib import Path

from plg_paths import user_data_dir

logger = logging.getLogger(__name__)

RATE = 44100
FALLBACK_DIRNAME = "fallback_samples"
_MAX_808_SECONDS = 20.0
_808_FREQ_HZ = 41.2034  # E1 exactly: the passport reads root 28 with ~0 cents


def fallback_dir() -> Path:
    path = user_data_dir() / FALLBACK_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _soft_clip(value: float, drive: float) -> float:
    return math.tanh(value * drive) / math.tanh(drive)


def _synth_808(duration: float) -> list[float]:
    """A plain, clean sub 808: steady pitch, fast attack, long smooth decay.

    Constant pitch on purpose (no glide): the passport must read one clear root
    so the export can set FL's root note. Harmonics keep it audible on small
    speakers; the soft clip stays gentle (no pre-distorted 'overdriven' file).
    """
    total = int(RATE * duration)
    tau = max(0.9, duration / 2.6)
    out: list[float] = []
    for i in range(total):
        t = i / RATE
        attack = min(1.0, t / 0.004)
        decay = math.exp(-t / tau)
        fade = min(1.0, (duration - t) / 0.03)  # never end on a hard edge
        body = (
            math.sin(2 * math.pi * _808_FREQ_HZ * t)
            + 0.28 * math.sin(2 * math.pi * 2 * _808_FREQ_HZ * t)
            + 0.10 * math.sin(2 * math.pi * 3 * _808_FREQ_HZ * t)
        )
        click = math.sin(2 * math.pi * 160 * t) * math.exp(-t * 220) * 0.25
        # Deliberately modest: the last-resort 808 must never be the loud one
        # (peak ~ -5 dBFS, rms ~ -11 dBFS); the mixer stage adds gain if wanted.
        out.append(_soft_clip((body * 0.85 + click) * attack * decay * fade, 1.4) * 0.55)
    return out


def _synth_hat(duration: float = 0.06) -> list[float]:
    """A dull closed hat: band-limited noise, fast decay.

    White noise + a 9-10 kHz tone is what the metallic-tick gate is built to
    reject; a 2.5-7.5 kHz band keeps centroid and zero-crossing rate well under
    its limits while still reading as a tick.
    """
    rng = random.Random(808)
    total = int(RATE * duration)
    # The gate's centroid is magnitude-weighted, so a gentle one-pole roll-off
    # still leaves a long bright tail: cascade four poles for a steep cut.
    a_hi = 1.0 - math.exp(-2 * math.pi * 5000.0 / RATE)
    a_lo = 1.0 - math.exp(-2 * math.pi * 1500.0 / RATE)
    stages = [0.0, 0.0, 0.0, 0.0]
    slow = 0.0
    out: list[float] = []
    for i in range(total):
        t = i / RATE
        value = rng.uniform(-1.0, 1.0)
        for k in range(4):
            stages[k] += a_hi * (value - stages[k])
            value = stages[k]
        slow += a_lo * (value - slow)
        band = value - slow
        env = math.exp(-t / 0.011) * min(1.0, t / 0.0008)
        out.append(band * env)
    peak = max(abs(x) for x in out) or 1.0
    return [0.7 * x / peak for x in out]


def _write(path: Path, samples: list[float]) -> None:
    from starter_kit import _write_wav_mono

    _write_wav_mono(path, samples, RATE)


def fallback_hat() -> Path:
    path = fallback_dir() / "PLG_fallback_hat.wav"
    if not path.is_file():
        _write(path, _synth_hat())
    return path


def _ensure_808(duration: float) -> Path:
    # Length buckets of 0.25 s keep the cache small and the names stable.
    bucket = min(_MAX_808_SECONDS, math.ceil(max(0.5, duration) * 4.0) / 4.0)
    path = fallback_dir() / f"PLG_fallback_808_{int(round(bucket * 1000))}ms.wav"
    if not path.is_file():
        _write(path, _synth_808(bucket))
    return path


def fallback_808(notes: list[dict], bpm: float) -> Path:
    """A synthetic 808 long enough for the song's longest note.

    Sized with the SAME formula the tail gate uses (``library_quality``): write
    a candidate, read its passport, measure the longest stretch of sample any
    note needs, and grow it until it covers that with headroom.
    """
    from library_paths import audio_duration_seconds
    from library_quality import bass_808_max_source_seconds
    from sample_pitch_passport import sample_pitch_passport

    length = 1.6
    path = _ensure_808(length)
    for _ in range(5):
        duration = audio_duration_seconds(path) or length
        need = bass_808_max_source_seconds(notes, bpm, sample_pitch_passport(path)) if notes else 0.0
        if need * 1.05 <= duration or duration >= _MAX_808_SECONDS:
            return path
        length = min(_MAX_808_SECONDS, max(duration * 1.3, need * 1.25))
        path = _ensure_808(length)
    return path

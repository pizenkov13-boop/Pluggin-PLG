"""Plugin-free stereo width — bake the Haas effect into melodic samples.

No Ozone Imager / Haas plugin. We exploit the Haas precedence effect: the same
signal in both ears, one side delayed ~12-18 ms, is heard as one huge wide source
(not an echo). So for melodic layers we render a stereo wav where:

    Left  = dry mono
    Right = same mono, delayed D ms

FL's native Sampler loads the stereo wav and the melody sounds cinematic-wide,
while kick/808 stay mono in the centre — the low end punches through clean.

Reuses the self-contained WAV I/O from sample_saturate (PCM 16/24/32 + float32).
"""

from __future__ import annotations

import logging
from pathlib import Path

# Per-track Haas delay (ms). Only melodic/wide layers; drums + 808 stay centred
# (absent here = untouched). counter_melody uses a different delay so the two
# melodic layers don't comb-filter into each other identically.
WIDEN_MS: dict[str, float] = {
    "melody_lead": 14.0,
    "counter_melody": 18.0,
}
_PEAK_CEILING = 0.97


def haas_widen(samples, sr: int, delay_ms: float):
    """mono/stereo float -> stereo float (n, 2) with one side delayed."""
    import numpy as np

    x = np.asarray(samples, dtype=np.float64)
    mono = x.mean(axis=1) if x.ndim > 1 else x
    delay = max(1, int(round(sr * float(delay_ms) / 1000.0)))
    right = np.concatenate([np.zeros(delay, dtype=np.float64), mono])[: len(mono)]
    stereo = np.stack([mono, right], axis=1)
    peak = float(np.max(np.abs(stereo))) if stereo.size else 0.0
    if peak > _PEAK_CEILING:
        stereo = stereo * (_PEAK_CEILING / peak)
    return stereo


def widen_file(src: Path, dst: Path, *, delay_ms: float) -> bool:
    from sample_saturate import _read_wav, _write_wav16

    try:
        audio, sr, _ = _read_wav(src)
        wide = haas_widen(audio, sr, delay_ms)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _write_wav16(dst, wide, sr)
        return True
    except Exception as exc:  # noqa: BLE001 — anything we can't process passes through
        logging.info("Haas widen skipped for %s (%s)", Path(src).name, exc)
        return False


def widen_sound_map(
    sound_map: dict[str, Path],
    cache_dir: Path,
    *,
    overrides: dict[str, float] | None = None,
) -> dict[str, Path]:
    """Return a new track->path map with Haas-wide stereo copies for melodic lanes."""
    cache_dir = Path(cache_dir)
    table = dict(WIDEN_MS)
    if overrides:
        table.update(overrides)
    result: dict[str, Path] = {}
    for track, path in sound_map.items():
        path = Path(path)
        delay_ms = table.get(track, 0.0)
        if delay_ms > 0.0 and path.suffix.lower() == ".wav":
            dst = cache_dir / f"{track}__wide_{int(delay_ms)}ms_{path.stem}.wav"
            if dst.is_file() or widen_file(path, dst, delay_ms=delay_ms):
                result[track] = dst
                continue
        result[track] = path
    return result


if __name__ == "__main__":
    import sys

    s, d = Path(sys.argv[1]), Path(sys.argv[2])
    print("widened" if widen_file(s, d, delay_ms=14.0) else "passed through", "->", d)

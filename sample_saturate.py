"""Plugin-free analog warmth — bake tanh saturation into the sample audio.

The AI can't open FabFilter Saturn / Decapitator to fatten a flat stock sample.
So we do it in code: read the wav, push it through a hyperbolic-tangent transfer
curve (soft-clip), which rounds the wave and adds the warm *even/odd harmonics* a
hardware desk gives — then write a warmed copy that FL's native Sampler loads.

    out = (1-mix) * x  +  mix * tanh(drive * x) / tanh(drive)

WAV I/O (PCM 8/16/24/32 + float32/64, via ``wav_decode``) through numpy — no soundfile/ffmpeg.
Anything it can't read (e.g. mp3) passes through untouched.

``pre_hp_hz`` ports Outlet's "selective distortion bus" trick (Yeat/rage
production): instead of a live send bus that only some layers feed, the WET
(saturated) component alone is high-passed before the tanh curve, while the
DRY component keeps its full low end — same parallel-blend result (a "dirty"
copy layered under a clean original, no phase-smeared sub) baked straight into
the one-shot, no new mixer-bus topology required.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Any

# Per-track saturation: (drive, mix, pre_hp_hz). drive<=0 or mix<=0 => skip
# (pass-through). Hats stay crisp (skipped); 808/melody get the most warmth.
# pre_hp_hz > 0 high-passes the WET component only (Outlet's distortion-send
# trick) — only sub_808 needs it: the low end is where a dirty parallel copy
# would otherwise phase-smear against the clean original.
SATURATION: dict[str, tuple[float, float, float]] = {
    "melody_lead": (1.7, 0.55, 0.0),
    "counter_melody": (1.7, 0.55, 0.0),
    # sub_808 intentionally omitted — tanh/warm copies made FL playback nothing
    # like the library wav the user auditioned in the folder.
    "kick": (1.4, 0.35, 0.0),
    "snare": (1.2, 0.30, 0.0),
    "snare_layer": (1.2, 0.30, 0.0),
    "clap": (1.1, 0.25, 0.0),
    "hi_hats": (0.0, 0.0, 0.0),
}
_PEAK_CEILING = 0.97


def _pre_highpass(x: Any, sr: int, hz: float) -> Any:
    """One-pole highpass — same math as sample_fx.low_cut, duplicated here to
    keep this module dependency-free (see module docstring)."""
    import numpy as np

    n = x.shape[0]
    alpha = 1.0 - np.exp(-2.0 * np.pi * hz / sr)
    lp = np.empty_like(x)
    state = np.zeros(x.shape[1:], dtype=np.float64) if x.ndim > 1 else 0.0
    for i in range(n):
        state = state + alpha * (x[i] - state)
        lp[i] = state
    return x - lp


def tanh_warm(samples: Any, drive: float, mix: float, *, pre_hp_hz: float = 0.0, sr: int = 44100) -> Any:
    """Apply the warmth curve to a float array in [-1, 1]. Pure / testable.

    ``pre_hp_hz``: high-pass the signal BEFORE computing the saturated (wet)
    component — the dry blend stays full-band, so a bass-heavy layer keeps its
    clean sub while only the distorted portion loses it (see module docstring).
    """
    import numpy as np

    x = np.asarray(samples, dtype=np.float64)
    if drive <= 0.0 or mix <= 0.0:
        return x
    src = _pre_highpass(x, sr, pre_hp_hz) if pre_hp_hz > 0.0 else x
    sat = np.tanh(drive * src) / np.tanh(drive)
    out = (1.0 - mix) * x + mix * sat
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > _PEAK_CEILING:
        out = out * (_PEAK_CEILING / peak)
    return out


def _read_wav(path: Path):
    """``(audio, sample_rate, channels)``; audio is float64, ``(n,)`` mono or
    ``(n, channels)``. The decoding itself lives in ``wav_decode`` (shared with
    the analysis code); raises ``ValueError`` for anything it cannot read."""
    from wav_decode import read_wav

    audio = read_wav(path)
    return audio.samples, audio.rate, max(1, audio.channels)


def _write_wav16(path: Path, audio: Any, sr: int) -> None:
    import numpy as np

    data = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    ch = audio.shape[1] if getattr(audio, "ndim", 1) > 1 else 1
    block = ch * 2
    fmt = struct.pack("<IHHIIHH", 16, 1, ch, sr, sr * block, block, 16)
    riff = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    Path(path).write_bytes(riff + b"fmt " + fmt + b"data" + struct.pack("<I", len(data)) + data)


def saturate_file(src: Path, dst: Path, *, drive: float, mix: float, pre_hp_hz: float = 0.0) -> bool:
    """Warm one wav into ``dst``. Returns True if a warmed file was written."""
    if drive <= 0.0 or mix <= 0.0:
        return False
    try:
        audio, sr, _ = _read_wav(src)
        warmed = tanh_warm(audio, drive, mix, pre_hp_hz=pre_hp_hz, sr=sr)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _write_wav16(dst, warmed, sr)
        return True
    except Exception as exc:  # noqa: BLE001 — any wav we can't process passes through
        logging.info("Saturation skipped for %s (%s)", Path(src).name, exc)
        return False


def _unpack_recipe(entry: tuple[float, ...]) -> tuple[float, float, float]:
    """Accept legacy (drive, mix) 2-tuples alongside (drive, mix, pre_hp_hz)."""
    if len(entry) >= 3:
        return float(entry[0]), float(entry[1]), float(entry[2])
    if len(entry) == 2:
        return float(entry[0]), float(entry[1]), 0.0
    return 0.0, 0.0, 0.0


def saturate_sound_map(
    sound_map: dict[str, Path],
    cache_dir: Path,
    *,
    overrides: dict[str, tuple[float, ...]] | None = None,
) -> dict[str, Path]:
    """Return a new track->path map with warmed copies where possible."""
    cache_dir = Path(cache_dir)
    table = dict(SATURATION)
    if overrides:
        table.update(overrides)
    result: dict[str, Path] = {}
    for track, path in sound_map.items():
        path = Path(path)
        drive, mix, pre_hp_hz = _unpack_recipe(table.get(track, (0.0, 0.0, 0.0)))
        if drive > 0.0 and mix > 0.0 and path.suffix.lower() == ".wav":
            dst = cache_dir / f"{track}__warm_d{drive:.2f}_hp{pre_hp_hz:.0f}_{path.stem}.wav"
            if dst.is_file() or saturate_file(path, dst, drive=drive, mix=mix, pre_hp_hz=pre_hp_hz):
                result[track] = dst
                continue
        result[track] = path
    return result


def _first_peak_sign(audio: Any, *, threshold: float = 0.02) -> int:
    """Sign of the first transient peak (+1 / -1 / 0). Used for kick↔808 phase."""
    import numpy as np

    x = np.asarray(audio, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if x.size == 0:
        return 0
    # Skip leading near-silence, then take the first sample past threshold.
    abs_x = np.abs(x)
    hits = np.where(abs_x >= threshold)[0]
    if hits.size == 0:
        peak_i = int(np.argmax(abs_x))
        if abs_x[peak_i] < 1e-6:
            return 0
        return 1 if x[peak_i] >= 0 else -1
    i = int(hits[0])
    return 1 if x[i] >= 0 else -1


def invert_wav_polarity(src: Path, dst: Path) -> bool:
    """Write ``dst`` as ``-src`` (Invert Phase). Returns True on success."""
    try:
        audio, sr, _ = _read_wav(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _write_wav16(dst, -audio, sr)
        return True
    except Exception as exc:  # noqa: BLE001 — phase is a bonus, never block export
        logging.info("Phase invert skipped for %s (%s)", Path(src).name, exc)
        return False


def align_kick_808_phase(
    sound_map: dict[str, Path],
    cache_dir: Path,
    *,
    bass_keys: tuple[str, ...] = ("808_bass", "sub_808"),
) -> dict[str, Path]:
    """If kick and 808 first peaks face opposite ways, invert the kick sample.

    Rich Amiri rule: both transients must push the same direction or they
    cancel in the low end. Timing phase_align (note delay) is separate —
    this is true sample polarity Invert Phase.
    """
    kick_path = sound_map.get("kick")
    if kick_path is None:
        return sound_map
    bass_path = next((sound_map[k] for k in bass_keys if k in sound_map), None)
    if bass_path is None:
        return sound_map
    kick_path, bass_path = Path(kick_path), Path(bass_path)
    if kick_path.suffix.lower() != ".wav" or bass_path.suffix.lower() != ".wav":
        return sound_map
    try:
        kick_audio, _, _ = _read_wav(kick_path)
        bass_audio, _, _ = _read_wav(bass_path)
    except Exception as exc:  # noqa: BLE001
        logging.info("Phase align skipped (%s)", exc)
        return sound_map
    kick_sign = _first_peak_sign(kick_audio)
    bass_sign = _first_peak_sign(bass_audio)
    if kick_sign == 0 or bass_sign == 0 or kick_sign == bass_sign:
        return sound_map
    cache_dir = Path(cache_dir)
    dst = cache_dir / f"kick__phase_inv_{kick_path.stem}.wav"
    if dst.is_file() or invert_wav_polarity(kick_path, dst):
        out = dict(sound_map)
        out["kick"] = dst
        logging.info(
            "Kick phase inverted to match %s (kick %s vs bass %s)",
            bass_path.name,
            "+" if kick_sign > 0 else "-",
            "+" if bass_sign > 0 else "-",
        )
        return out
    return sound_map


if __name__ == "__main__":
    import sys

    s, d = Path(sys.argv[1]), Path(sys.argv[2])
    ok = saturate_file(s, d, drive=1.8, mix=0.6)
    print("warmed" if ok else "passed through", "->", d)

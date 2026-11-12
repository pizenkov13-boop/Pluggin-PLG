"""Audio analysis for "hearing" sample selection (V2).

Listens to the actual waveform — spectral brightness (centroid), attack/decay
transient, grit (zero-crossing rate) and low fundamental (808 tone) — so the
picker matches the vibe a user asked for even when files are named garbage
(01.wav, final_v3.wav). Pure numpy + pydub; no librosa, so it stays light and
PyInstaller-friendly. WAV (PCM and IEEE float) and FLAC are decoded by
``audio_decode`` with no ffmpeg; anything else goes through pydub, and anything unreadable
(e.g. mp3 with no ffmpeg) returns None and the name-based scorer takes over.

Features are cached by path+mtime+size so big libraries are analyzed once.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from plg_paths import user_data_dir

logger = logging.getLogger("plg.audio")

CACHE_FILE = user_data_dir() / "audio_features.json"
ANALYZE_MS = 2000  # only the head of a file is needed for one-shots/loops

# (high_threshold, low_threshold) per feature, tuned for 44.1k one-shots.
THRESHOLDS = {
    "centroid": (3500.0, 1800.0),
    "zcr": (0.08, 0.03),
    "attack": (60.0, 15.0),   # ms — "low" = punchy
    "decay": (600.0, 150.0),  # ms — "low" = tight
    "pitch": (200.0, 120.0),  # Hz — "low" = deep sub
}

# descriptor family -> desired feature directions
FAMILY_TARGET: dict[str, dict[str, str]] = {
    "dark": {"centroid": "low"},
    "bright": {"centroid": "high"},
    "deep": {"centroid": "low", "pitch": "low"},
    "punchy": {"attack": "low", "decay": "low"},
    "hard": {"zcr": "high", "decay": "low"},
    "distorted": {"zcr": "high"},
    "clean": {"zcr": "low"},
    "vintage": {"centroid": "low"},
    "metallic": {"centroid": "high", "zcr": "high"},
}


def _features_from_array(x: np.ndarray, sr: int, duration_s: float) -> dict[str, float] | None:
    if x.size == 0:
        return None

    env = np.abs(x)
    peak_i = int(np.argmax(env))
    peak = float(env[peak_i])
    if peak <= 1e-6:
        return None

    attack_ms = peak_i / sr * 1000.0
    after = env[peak_i:]
    below = np.where(after < 0.1 * peak)[0]
    decay_ms = float((below[0] if below.size else len(after)) / sr * 1000.0)

    window = x[:sr] if x.size > sr else x
    spec = np.abs(np.fft.rfft(window * np.hanning(len(window))))
    freqs = np.fft.rfftfreq(len(window), 1.0 / sr)
    spec_sum = float(spec.sum()) + 1e-9
    centroid = float((freqs * spec).sum() / spec_sum)

    band = (freqs >= 40.0) & (freqs <= 500.0)
    pitch = float(freqs[band][int(np.argmax(spec[band]))]) if band.any() and spec[band].size else 0.0

    zcr = float(np.mean(np.abs(np.diff(np.sign(x))) > 0)) if x.size > 1 else 0.0

    # A note-gated 808 needs an audible body after its attack.  A sample with
    # a huge first 60 ms but almost no signal from 120 ms onward is perceived
    # as a snap/fire-crackle, even when its filename says "808".  This ratio
    # is deliberately signal-based so it also catches badly named files.
    attack_end = min(x.size, max(1, int(sr * 0.060)))
    body_start = min(x.size, max(attack_end, int(sr * 0.120)))
    body_end = min(x.size, max(body_start + 1, int(sr * 0.800)))
    attack_rms = float(np.sqrt(np.mean(x[:attack_end] ** 2)))
    body = x[body_start:body_end]
    body_rms = float(np.sqrt(np.mean(body ** 2))) if body.size else 0.0
    transient_body_ratio = attack_rms / max(body_rms, 1e-6)

    return {
        "duration_s": round(duration_s, 3),
        "rms": round(float(np.sqrt(np.mean(x ** 2))), 5),
        "centroid": round(centroid, 1),
        "zcr": round(zcr, 4),
        "attack": round(attack_ms, 1),
        "decay": round(decay_ms, 1),
        "pitch": round(pitch, 1),
        "transient_body_ratio": round(transient_body_ratio, 3),
    }


def _load_native_samples(path: Path, *, max_ms: int = ANALYZE_MS) -> tuple[np.ndarray, int, float] | None:
    """Read a WAV (PCM or IEEE float) or FLAC without ffmpeg — see ``audio_decode``."""
    from audio_decode import AudioDecodeError, read_mono

    try:
        samples, sr, duration_s = read_mono(path, max_ms=max_ms)
    except (AudioDecodeError, EOFError, OSError, ValueError):
        return None
    if samples.size == 0:
        return None
    # Float files may exceed full scale; ffmpeg's conversion clips them, so do
    # the same to keep the features identical to the pydub path.
    return np.clip(samples, -1.0, 1.0), sr, duration_s


def _load_samples(path: Path, *, max_ms: int = ANALYZE_MS) -> tuple[np.ndarray, int, float] | None:
    """Head of the file as mono float samples: the ffmpeg-free decoders first
    (WAV, FLAC), pydub — and so ffmpeg — only for what they cannot read."""
    from audio_decode import can_decode

    loaded = _load_native_samples(path, max_ms=max_ms) if can_decode(path) else None
    return loaded if loaded is not None else _load_pydub_samples(path, max_ms=max_ms)


def _load_pydub_samples(path: Path, *, max_ms: int = ANALYZE_MS) -> tuple[np.ndarray, int, float] | None:
    try:
        from pydub_windows_compat import audio_segment_from_file

        seg = audio_segment_from_file(str(path))
    except Exception as exc:  # unreadable / no ffmpeg for mp3 — fall back to names
        logger.debug("audio analyze skip %s: %s", path, exc)
        return None
    if len(seg) == 0:
        return None

    seg = seg[:max_ms].set_channels(1)
    sr = seg.frame_rate or 44100
    samples = np.asarray(seg.get_array_of_samples(), dtype=np.float64)
    if samples.size == 0:
        return None
    maxv = float(2 ** (8 * seg.sample_width - 1)) or 1.0
    x = samples / maxv
    return x, sr, len(seg) / 1000.0


def analyze(path: Path | str) -> dict[str, float] | None:
    """Extract features from a file head. Returns None if unreadable/silent."""
    p = Path(path)
    loaded = _load_samples(p)
    if loaded is None:
        return None
    x, sr, duration_s = loaded
    return _features_from_array(x, sr, duration_s)


# --- sample "character" numbers (Phase 2 passport input) ----------------------
# Additive to analyze(): none of the fields analyze() returns is read, renamed
# or re-derived here, because sample_match / library_quality thresholds are
# tuned on them. Same decoded signal, same 1 s spectral window as the centroid.
_BAND_NAMES = ("sub", "low", "mid", "presence", "air")
_BAND_EDGES_HZ = ((0.0, 90.0), (90.0, 250.0), (250.0, 2000.0), (2000.0, 6000.0), (6000.0, float("inf")))
_FRAME_MS = 5.0
_CLIP_LEVEL = 0.999


def _finite(value: float, digits: int) -> float | None:
    out = float(value)
    return round(out, digits) if out == out and abs(out) != float("inf") else None


def _character_from_array(x: np.ndarray, sr: int) -> dict[str, Any] | None:
    """Spectral-band shares, rolloff, flatness, level and decay-to-threshold
    times of one decoded sample. Pure and deterministic; None when silent."""
    if x.size < 2 or sr <= 0:
        return None
    env = np.abs(x)
    peak = float(env.max())
    if peak <= 1e-6:
        return None
    rms = float(np.sqrt(np.mean(x ** 2)))

    window = x[:sr] if x.size > sr else x
    power = np.abs(np.fft.rfft(window * np.hanning(len(window)))) ** 2
    freqs = np.fft.rfftfreq(len(window), 1.0 / sr)
    total = float(power.sum()) + 1e-12
    bands = {
        name: _finite(float(power[(freqs >= lo) & (freqs < hi)].sum()) / total, 4)
        for name, (lo, hi) in zip(_BAND_NAMES, _BAND_EDGES_HZ)
    }
    cum = np.cumsum(power)
    rolloff = float(freqs[min(int(np.searchsorted(cum, 0.85 * total)), len(freqs) - 1)])
    tonal_bins = power[freqs >= 40.0]
    if tonal_bins.size:
        flatness = float(np.exp(np.mean(np.log(tonal_bins + 1e-20))) / (np.mean(tonal_bins) + 1e-20))
        flatness = max(0.0, min(1.0, flatness))
    else:
        flatness = 0.0

    # Decay: time from the loudest 5 ms frame until the frame level has fallen
    # 20 / 40 dB. ``truncated`` = the analysed window ended before -40 dB.
    frame = max(1, int(sr * _FRAME_MS / 1000.0))
    usable = (x.size // frame) * frame
    if usable >= frame:
        frames = np.sqrt(np.mean(x[:usable].reshape(-1, frame) ** 2, axis=1))
        pk = int(np.argmax(frames))
        ref = float(frames[pk])
        after = frames[pk:]
        below20 = np.where(after < ref * 0.1)[0]
        below40 = np.where(after < ref * 0.01)[0]
        t20 = float((below20[0] if below20.size else after.size) * _FRAME_MS)
        t40 = float((below40[0] if below40.size else after.size) * _FRAME_MS)
        truncated = not below40.size
    else:
        t20 = t40 = 0.0
        truncated = False

    peak_db = 20.0 * np.log10(max(peak, 1e-9))
    rms_db = 20.0 * np.log10(max(rms, 1e-9))
    return {
        "bands": bands,
        "rolloff85_hz": _finite(rolloff, 1),
        "flatness": _finite(flatness, 4),
        "peak_db": _finite(peak_db, 2),
        "rms_db": _finite(rms_db, 2),
        "crest_db": _finite(peak_db - rms_db, 2),
        "clip_frac": _finite(float(np.mean(env >= _CLIP_LEVEL)), 5),
        "t20_ms": _finite(t20, 1),
        "t40_ms": _finite(t40, 1),
        "decay_truncated": bool(truncated),
        "analyzed_s": _finite(x.size / sr, 3),
    }


def analyze_with_character(path: Path | str) -> tuple[dict[str, float], dict[str, Any]] | None:
    """``analyze()``'s features plus the character numbers, from ONE decode.

    The first element is exactly what ``analyze()`` returns for this file."""
    loaded = _load_samples(Path(path))
    if loaded is None:
        return None
    x, sr, duration_s = loaded
    base = _features_from_array(x, sr, duration_s)
    character = _character_from_array(x, sr) if base is not None else None
    if base is None or character is None:
        return None
    return base, character


_cache: dict[str, Any] | None = None

# The cache file is ~8 MB on a real library and was rewritten IN FULL on every
# miss, non-atomically: a cold 850-sample scan meant hundreds of 8 MB writes,
# and a crash or a second writer (generation runs on a worker thread) could
# leave a truncated file that json.loads then discarded, throwing the whole
# cache away. Now: one lock for every mutation and write, atomic replace, and
# ``cache_batch`` to coalesce a bulk analysis into a single write.
_cache_lock = threading.RLock()
_batch_depth = 0
_dirty = False


def _load_cache() -> dict[str, Any]:
    global _cache
    with _cache_lock:
        if _cache is None:
            try:
                _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                _cache = {}
        return _cache


def atomic_write_text(path: Path, text: str) -> bool:
    """Replace ``path`` with ``text`` so a reader sees the old file or the new
    one, never a partial one. Returns False (never raises) when it could not.

    On Windows ``os.replace`` raises PermissionError while another process holds
    the target open, so it is retried briefly before giving up."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False
    for delay in (0.0, 0.05, 0.15, 0.3):
        if delay:
            time.sleep(delay)
        try:
            os.replace(tmp, path)
            return True
        except OSError:
            continue
    with contextlib.suppress(OSError):
        tmp.unlink()
    return False


def _save_cache() -> None:
    """Persist the cache — or, inside ``cache_batch``, just remember to."""
    global _dirty
    with _cache_lock:
        if _batch_depth > 0:
            _dirty = True
            return
        if atomic_write_text(CACHE_FILE, json.dumps(_load_cache())):
            _dirty = False


@contextlib.contextmanager
def cache_batch() -> Iterator[None]:
    """Defer every cache write inside the block to ONE write at the end.

    Re-entrant. The cache dict itself is updated immediately, so lookups inside
    the block see new entries; only the disk write is coalesced."""
    global _batch_depth
    with _cache_lock:
        _batch_depth += 1
    try:
        yield
    finally:
        with _cache_lock:
            _batch_depth -= 1
            if _batch_depth == 0 and _dirty:
                _save_cache()


def analyze_cached(path: Path | str) -> dict[str, float] | None:
    """analyze() with a path+mtime+size cache so libraries are scanned once."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    # Bump the cache schema when feature extraction changes: otherwise an old
    # cached ``None`` for a float WAV keeps bypassing the new decoder forever
    # (v3: float WAVs and FLACs are decoded without ffmpeg — v2 cached
    # ``None`` for them on every machine that has none).
    key = f"v3:{p}:{int(st.st_mtime)}:{st.st_size}"
    cache = _load_cache()
    with _cache_lock:
        if key in cache:
            return cache[key]
    feats = analyze(p)
    with _cache_lock:
        cache[key] = feats
        _save_cache()
    return feats


SUSTAIN_ANALYZE_S = 20.0  # long enough to tell a real pad/sustain (10-18s+ in
# the PLG_Samples bank) apart from a one-shot's few-second decay tail


def analyze_sustain(path: Path | str, *, max_seconds: float = SUSTAIN_ANALYZE_S) -> dict[str, float] | None:
    """Longer-window sibling of ``analyze()`` for melodic/sustained material
    (melody_sample_bank.py's fallback picker). ``analyze()``'s 2-second
    ``ANALYZE_MS`` window is correct for one-shot drum matching but reports a
    meaningless decay time for a 10-20s pad — telling a genuine sustain
    sample apart from a one-shot with a few seconds of decay tail requires
    actually hearing past 2 seconds. Reuses the same wav/pydub loaders with a
    wider window; returns only ``duration_s`` (also reported by ``analyze()``,
    already accurate there — repeated here so callers need one function) and
    ``tail_rms_ratio`` (loudness in the last ~1s of the analyzed window vs the
    first ~1s — near 1.0 = still sustaining, near 0 = faded out). The one-shot
    attack/decay/transient_body_ratio fields stay meaningless at this length
    and are deliberately omitted rather than returned wrong."""
    p = Path(path)
    max_ms = int(max_seconds * 1000)
    loaded = _load_samples(p, max_ms=max_ms)
    if loaded is None:
        return None
    x, sr, duration_s = loaded
    if x.size == 0:
        return None

    env = np.abs(x)
    if float(env.max()) <= 1e-6:
        return None

    head_end = min(x.size, sr)
    tail_start = max(0, x.size - sr)
    head_rms = float(np.sqrt(np.mean(x[:head_end] ** 2))) if head_end else 0.0
    tail_rms = float(np.sqrt(np.mean(x[tail_start:] ** 2))) if x.size > tail_start else 0.0
    tail_ratio = tail_rms / max(head_rms, 1e-6)

    return {
        "duration_s": round(duration_s, 3),
        "tail_rms_ratio": round(min(tail_ratio, 1.0), 4),
    }


def analyze_sustain_cached(path: Path | str) -> dict[str, float] | None:
    """analyze_sustain() with the same path+mtime+size cache as analyze_cached()
    (shared cache file, distinct key prefix so the two never collide)."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = f"sustain_v1:{p}:{int(st.st_mtime)}:{st.st_size}"
    cache = _load_cache()
    with _cache_lock:
        if key in cache:
            return cache[key]
    feats = analyze_sustain(p)
    with _cache_lock:
        cache[key] = feats
        _save_cache()
    return feats


RMS_ANALYZE_S = 30.0  # far enough into the file that a long pad's body
# loudness is representative, not just its (often quieter) attack transient


def rms_dbfs(path: Path | str, *, max_seconds: float = RMS_ANALYZE_S) -> float | None:
    """Whole-body loudness in dBFS (20*log10(rms)) over up to ``max_seconds``.

    Used for cross-file gain matching (flp_writer._channel_level's melody-lane
    correction) so the sampler channel doesn't end up systematically louder or
    quieter just because THIS candidate file happened to be recorded/rendered
    hotter or colder than another one in the same pool — one bank file at
    -10dBFS and another at -24dBFS would otherwise both get the exact same
    static channel volume. Deliberately reads further into the file than
    ``analyze()``'s 2-second head (tuned for one-shot classification, not
    loudness matching) so a pad's sustained body — not just its transient —
    sets the measured level. Returns ``None`` if unreadable or silent."""
    p = Path(path)
    max_ms = int(max_seconds * 1000)
    loaded = _load_samples(p, max_ms=max_ms)
    if loaded is None:
        return None
    x, _sr, _duration_s = loaded
    if x.size == 0:
        return None
    rms = float(np.sqrt(np.mean(x ** 2)))
    if rms <= 1e-9:
        return None
    return float(20.0 * np.log10(rms))


def target_from_prompt(prompt: str = "", style: str = "", track: str | None = None) -> dict[str, str]:
    """Desired feature directions from the prompt's descriptor families."""
    from sound_descriptors import DESCRIPTOR_FAMILIES

    text = f"{prompt} {style}".lower()
    target: dict[str, str] = {}
    for family, (triggers, _hints, tracks) in DESCRIPTOR_FAMILIES.items():
        if family not in FAMILY_TARGET:
            continue
        if track is not None and tracks and track not in tracks:
            continue
        if any(trig in text for trig in triggers):
            target.update(FAMILY_TARGET[family])
    return target


def gap_fill_target(reference_paths: list) -> dict[str, str]:
    """Bias the NEXT layer's pick away from already-chosen layers' occupied
    spectral centroid band (Dirkey "Fix Your Sound Selection" — pick each new
    sound in the frequency range the earlier layers left unoccupied, e.g. a
    low-mid piano followed by a high, pitched-up bell rather than another
    low-mid layer). Reuses the same centroid feature + thresholds
    ``feature_match_score`` already scores candidates against; returns {} (no
    bias) when references are unreadable or sit in the ambiguous middle band.
    """
    centroids: list[float] = []
    for path in reference_paths:
        feats = analyze_cached(path)
        if feats and "centroid" in feats:
            centroids.append(feats["centroid"])
    if not centroids:
        return {}
    avg = sum(centroids) / len(centroids)
    high, low = THRESHOLDS["centroid"]
    if avg >= high:
        return {"centroid": "low"}
    if avg <= low:
        return {"centroid": "high"}
    return {}


# Two percussion lanes read as two instruments only while they hold different
# roles — snare as body in the mids, clap as click and width above it. They
# collapse into one thickened slap when they share a band AND open with the
# same attack, which sums coherently instead of layering (measured 2026-08-21:
# a "SnClap" combo won the clap lane on top of a separate snare, 282 Hz apart
# with 2.3 ms between their attacks).
#
# These are DISTANCES between two chosen samples, not absolute bands. The
# absolute test `gap_fill_target` runs cannot do this job: every clap in a
# real library sits above the 3500 Hz "high" threshold, so an absolute bias
# scores the whole pool alike and changes no ranking at all.
ROLE_SPLIT_CENTROID_HZ = 400.0
ROLE_SPLIT_ATTACK_MS = 10.0


def roles_collide(a: Path | str, b: Path | str) -> bool:
    """True when two samples would land in one role instead of two.

    Unreadable files return False — never veto on missing analysis, the
    name-based scorer stays in charge exactly as it does elsewhere here.
    """
    fa, fb = analyze_cached(a), analyze_cached(b)
    if not fa or not fb:
        return False
    try:
        d_centroid = abs(float(fa["centroid"]) - float(fb["centroid"]))
        d_attack = abs(float(fa["attack"]) - float(fb["attack"]))
    except (KeyError, TypeError, ValueError):
        return False
    return d_centroid < ROLE_SPLIT_CENTROID_HZ and d_attack < ROLE_SPLIT_ATTACK_MS


def feature_match_score(features: dict[str, float] | None, target: dict[str, str]) -> int:
    """+12 per feature matching the wanted direction, -6 for the opposite."""
    if not features or not target:
        return 0
    score = 0
    for feature, want in target.items():
        value = features.get(feature)
        if value is None or feature not in THRESHOLDS:
            continue
        high, low = THRESHOLDS[feature]
        if want == "high":
            score += 12 if value >= high else (-6 if value <= low else 0)
        else:  # "low"
            score += 12 if value <= low else (-6 if value >= high else 0)
    return score

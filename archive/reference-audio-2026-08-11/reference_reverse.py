"""Reverse a reference audio snippet into MIDI via basic-pitch (.venv-bp).

When the user asks to copy / transcribe a reference (or drops audio in
``references/``), PLG optionally splits the clip with Demucs (bass + other
stems), then runs basic-pitch per stem for cleaner melody/808 separation.
Falls back to transcribing the full mix when Demucs is unavailable.

Set ``PLG_BASIC_PITCH_PYTHON`` to override the basic-pitch interpreter.
Set ``PLG_USE_BASIC_PITCH=0`` to disable transcription.
Set ``PLG_USE_DEMUCS_REVERSE=0`` to skip stem split (full-mix only).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

from library_paths import AUDIO_EXTENSIONS
from midi_ingest import MAX_BEATS, read_midi_notes, split_by_register
from pattern_utils import track_notes
from plg_paths import app_dir, user_data_dir

logger = logging.getLogger("plg.reference_reverse")

REFERENCE_FOLDERS = ("references", "splice", "melodies", "textures", "kits", "fx")

_REVERSE_TRIGGERS = (
    "reference",
    "референс",
    "реф",
    "реверс",
    "reverse",
    "snippet",
    "сниппет",
    "transcribe",
    "транскриб",
    "разбери",
    "скопируй",
    "copy from",
    "like the",
    "как в",
    "из трека",
    "from track",
    "from the track",
    "melody from",
    "мелодию из",
    "audio ref",
    "аудио реф",
)

_BP_TIMEOUT_S = 600
_DEMUCS_STEMS = ("bass", "other")


def demucs_reverse_enabled() -> bool:
    if _env_flag("PLG_USE_DEMUCS_REVERSE", "auto") in ("0", "false", "no", "off"):
        return False
    try:
        from stem_split import stems_available

        return stems_available()
    except ImportError:
        return False


def _env_flag(name: str, default: str = "auto") -> str:
    return os.environ.get(name, default).strip().lower()


def basic_pitch_available() -> bool:
    if _env_flag("PLG_USE_BASIC_PITCH", "auto") in ("0", "false", "no", "off"):
        return False
    return resolve_basic_pitch_python() is not None


def resolve_basic_pitch_python() -> Path | None:
    override = os.environ.get("PLG_BASIC_PITCH_PYTHON", "").strip()
    if override:
        path = Path(override)
        if path.is_file():
            return path.resolve()
    if sys.platform == "win32":
        venv_py = app_dir() / ".venv-bp" / "Scripts" / "python.exe"
    else:
        venv_py = app_dir() / ".venv-bp" / "bin" / "python"
    if venv_py.is_file():
        return venv_py.resolve()
    return None


def wants_reference_reverse(prompt: str = "", style: str = "") -> bool:
    text = f"{prompt} {style}".lower()
    return any(trigger in text for trigger in _REVERSE_TRIGGERS)


def _tokens(prompt: str, style: str) -> set[str]:
    text = f"{prompt} {style}".lower()
    found = set(re.findall(r"[a-z0-9]{3,}", text))
    from sound_descriptors import descriptor_hints

    found |= set(descriptor_hints(prompt, style, track="melody_lead"))
    return found


def find_reference_audio(library_root: Path, prompt: str = "", style: str = "") -> Path | None:
    """Best reference audio clip in the library for this prompt."""
    root = Path(library_root)
    if not root.is_dir():
        return None

    tokens = _tokens(prompt, style)
    explicit = wants_reference_reverse(prompt, style)
    candidates: list[Path] = []

    for folder in REFERENCE_FOLDERS:
        folder_path = root / folder
        if not folder_path.is_dir():
            continue
        for path in folder_path.rglob("*"):
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
                candidates.append(path)

    if not candidates:
        return None

    if explicit:
        ref_only = [
            p for p in candidates
            if str(p.relative_to(root)).lower().replace("\\", "/").startswith("references/")
        ]
        if ref_only:
            candidates = ref_only

    def score(path: Path) -> int:
        name = path.stem.lower()
        rel = str(path.relative_to(root)).lower().replace("\\", "/")
        s = 0
        if rel.startswith("references/"):
            s += 30
        for token in tokens:
            if token in name:
                s += 12
            elif token in rel:
                s += 6
        return s

    best = max(candidates, key=score)
    if score(best) > 0 or explicit:
        return best.resolve()
    if str(best.relative_to(root)).lower().startswith("references/"):
        return best.resolve()
    return None


def _cache_dir() -> Path:
    path = user_data_dir() / "reference_reverse"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(audio: Path) -> str:
    st = audio.stat()
    raw = f"{audio.resolve()}:{st.st_mtime_ns}:{st.st_size}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def split_reference_stems(audio_path: Path) -> dict[str, Path] | None:
    """Demucs split into cached stems; None if unavailable or failed."""
    if not demucs_reverse_enabled():
        return None

    audio = Path(audio_path).resolve()
    stem_dir = _cache_dir() / _cache_key(audio) / "demucs"
    expected = {name: stem_dir / f"{name}.wav" for name in _DEMUCS_STEMS}
    if all(path.is_file() for path in expected.values()):
        logger.info("Demucs cache hit for %s", audio.name)
        return expected

    from stem_split import StemSplitError, split_stems

    try:
        written = split_stems(audio, stem_dir)
    except StemSplitError as exc:
        logger.warning("Demucs reverse skipped: %s", exc)
        return None

    picked = {name: written[name] for name in _DEMUCS_STEMS if name in written}
    return picked or None


def transcribe_audio_to_midi(
    audio_path: Path,
    *,
    bpm: float = 120.0,
    midi_name: str | None = None,
    cache_root: Path | None = None,
) -> Path | None:
    """Run basic-pitch subprocess; return path to cached/generated .mid."""
    audio = Path(audio_path).resolve()
    if not audio.is_file():
        return None

    py = resolve_basic_pitch_python()
    if py is None:
        logger.info("basic-pitch venv not found (.venv-bp); skip reference reverse")
        return None

    if cache_root is None:
        cache_root = _cache_dir() / _cache_key(audio)
    cache_root.mkdir(parents=True, exist_ok=True)
    cached = cache_root / (midi_name or f"{audio.stem}.mid")
    if cached.is_file():
        logger.info("Reference reverse MIDI cache hit: %s", cached.name)
        return cached

    script = app_dir() / "scripts" / "bp_transcribe.py"
    if not script.is_file():
        logger.warning("Missing %s", script)
        return None

    cmd = [
        str(py),
        str(script),
        str(audio),
        str(cache_root),
        "--bpm",
        str(max(40.0, min(300.0, float(bpm)))),
    ]
    logger.info("Reference reverse: basic-pitch on %s", audio.name)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_BP_TIMEOUT_S,
            cwd=str(app_dir()),
        )
    except subprocess.TimeoutExpired:
        logger.warning("basic-pitch timed out on %s", audio.name)
        return None
    except OSError as exc:
        logger.warning("basic-pitch subprocess failed: %s", exc)
        return None

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        logger.warning("basic-pitch failed (%s): %s", result.returncode, err[:500])
        return None

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    midi_path = Path(lines[-1]) if lines else cached
    if not midi_path.is_file():
        mids = list(cache_root.glob("*.mid"))
        if not mids:
            return None
        midi_path = mids[0]

    target = cached
    if midi_path.resolve() != target.resolve() and midi_path.is_file():
        try:
            if target.is_file():
                target.unlink()
            midi_path.replace(target)
            midi_path = target
        except OSError:
            pass

    return midi_path if midi_path.is_file() else None


def transcribe_reference_stems(
    audio_path: Path,
    *,
    bpm: float = 120.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return (melody_notes, bass_notes, meta) from demucs+basic-pitch or full mix."""
    audio = Path(audio_path).resolve()
    cache_base = _cache_dir() / _cache_key(audio)
    meta: dict[str, Any] = {"demucs": False, "sources": []}

    melody_notes: list[dict[str, Any]] = []
    bass_notes: list[dict[str, Any]] = []

    stems = split_reference_stems(audio)
    jobs: list[tuple[str, Path]] = []
    if stems:
        meta["demucs"] = True
        if "bass" in stems:
            jobs.append(("bass", stems["bass"]))
        if "other" in stems:
            jobs.append(("melody", stems["other"]))
    if not jobs:
        jobs.append(("full", audio))

    for label, stem_audio in jobs:
        midi = transcribe_audio_to_midi(
            stem_audio,
            bpm=bpm,
            midi_name=f"{label}.mid",
            cache_root=cache_base,
        )
        if midi is None:
            continue
        notes = read_midi_notes(midi, max_beats=MAX_BEATS)
        meta["sources"].append({
            "label": label,
            "audio": str(stem_audio),
            "midi": str(midi),
            "notes": len(notes),
        })
        if label == "bass":
            bass_notes.extend(notes)
        elif label == "melody":
            melody_notes.extend(notes)
        else:
            mel, bass = split_by_register(notes)
            melody_notes.extend(mel)
            bass_notes.extend(bass)

    return melody_notes, bass_notes, meta


def merge_stem_notes_into_pattern(
    pattern: dict[str, Any],
    melody_notes: list[dict[str, Any]],
    bass_notes: list[dict[str, Any]],
    *,
    source: str,
    name: str,
    meta_key: str = "plg_reference_reverse",
    manual_prefix: str = "Reference reverse",
) -> bool:
    """Apply stem transcription into melody_lead / sub_808."""
    tracks = pattern.setdefault("tracks", {})
    if not isinstance(tracks, dict):
        return False

    total = len(melody_notes) + len(bass_notes)
    if total == 0:
        return False

    melody_count = len(melody_notes)
    bass_count = len(bass_notes)

    if melody_notes:
        tracks["melody_lead"] = melody_notes
    elif bass_notes:
        tracks["melody_lead"] = bass_notes
        bass_count = 0

    if bass_notes and not track_notes(pattern, "sub_808"):
        tracks["sub_808"] = bass_notes

    pattern[meta_key] = {
        "source": source,
        "name": name,
        "notes": total,
        "melody_notes": melody_count,
        "bass_notes": bass_count,
    }
    steps = list(pattern.get("manual_steps") or [])
    steps.insert(0, f"{manual_prefix}: played {name} ({total} notes from reference).")
    pattern["manual_steps"] = steps[:16]
    logger.info("%s: %s (%s notes)", manual_prefix, name, total)
    return True


def reference_reverse_status() -> dict[str, Any]:
    """Tooling readiness for UI (basic-pitch required, Demucs optional)."""
    bp = basic_pitch_available()
    demucs = demucs_reverse_enabled()
    return {
        "basic_pitch": bp,
        "demucs": demucs,
        "ready": bp,
        "hint": (
            "Ready — drop a reference track to extract melody + 808."
            if bp
            else "Install .venv-bp with basic-pitch (see requirements-optional.txt)."
        ),
    }


def _load_audio_mono(path: Path, *, max_ms: int = 30_000) -> tuple[np.ndarray, int] | None:
    """Mono float samples for analysis (WAV stdlib, mp3 via pydub)."""
    from audio_features import _load_pydub_samples, _load_wav_samples

    if path.suffix.lower() == ".wav":
        loaded = _load_wav_samples(path, max_ms=max_ms)
    else:
        loaded = _load_pydub_samples(path, max_ms=max_ms)
    if loaded is None:
        return None
    samples, sr, _duration = loaded
    return samples, sr


def estimate_bpm_from_audio(audio_path: Path) -> float:
    """Rough BPM from onset autocorrelation (60–200); 140 if unreadable."""
    loaded = _load_audio_mono(audio_path)
    if loaded is None:
        return 140.0

    x, sr = loaded
    hop = max(1, sr // 200)
    if len(x) < hop * 8:
        return 140.0

    frames = [
        float(np.sqrt(np.mean(x[i : i + hop] ** 2)))
        for i in range(0, len(x) - hop, hop)
    ]
    env = np.asarray(frames, dtype=np.float64)
    onset = np.maximum(0.0, np.diff(env, prepend=env[0]))
    onset -= float(onset.mean())
    std = float(onset.std())
    if std > 1e-9:
        onset /= std

    fps = sr / hop
    min_lag = max(1, int(fps * 60 / 200))
    max_lag = max(min_lag, int(fps * 60 / 60))
    best_lag = 0
    best_corr = -1.0
    upper = min(len(onset) // 2, max_lag)
    for lag in range(min_lag, upper + 1):
        corr = float(np.dot(onset[:-lag], onset[lag:]))
        if corr > best_corr:
            best_corr = corr
            best_lag = lag

    if best_lag > 0:
        bpm = 60.0 * fps / best_lag
        return round(max(60.0, min(200.0, bpm)), 1)
    return 140.0


def stage_reference_audio(source: Path, library_root: Path) -> Path:
    """Copy a user-picked clip into ``references/`` for reuse on later beats."""
    src = Path(source).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Audio not found: {src}")
    if src.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"Unsupported audio type: {src.suffix}")

    dest_dir = Path(library_root) / "references"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src != dest.resolve():
        shutil.copy2(src, dest)
    return dest.resolve()


def ingest_reference_from_file(
    pattern: dict[str, Any],
    audio_path: Path,
    *,
    bpm: float | None = None,
    respect_uploaded_scale: bool = False,
) -> dict[str, Any] | None:
    """Transcribe an explicit audio file and merge notes into ``pattern``.

    ``bpm=None`` (apply_reference_track's use — building a fresh beat FROM
    the reference) auto-detects BPM from the audio and overwrites
    ``pattern["bpm"]`` with it. Pass the pattern's own already-resolved BPM
    explicitly when fitting a reference into an EXISTING generation (see
    ingest_uploaded_reference_audio below) — the user's requested BPM is
    sacred, the reference should fit the track, not the other way around.

    ``respect_uploaded_scale`` — same contract as midi_ingest.
    ingest_uploaded_midi's option of the same name: off by default (genre
    profile's scale still drives arrange_song's section transforms); on
    detects the scale from the transcribed melody and pins it into
    pattern["scale"]."""
    if not basic_pitch_available():
        return None

    audio = Path(audio_path).resolve()
    if not audio.is_file():
        return None

    detected_bpm = float(bpm or estimate_bpm_from_audio(audio))
    melody_notes, bass_notes, stem_meta = transcribe_reference_stems(audio, bpm=detected_bpm)
    if not melody_notes and not bass_notes:
        return None

    for note in melody_notes + bass_notes:
        note["from_reference"] = True

    prefix = (
        "Reference reverse (demucs+basic-pitch)"
        if stem_meta.get("demucs")
        else "Reference reverse (basic-pitch)"
    )
    ok = merge_stem_notes_into_pattern(
        pattern,
        melody_notes,
        bass_notes,
        source=str(audio),
        name=audio.name,
        manual_prefix=prefix,
    )
    if not ok:
        return None

    pattern["bpm"] = detected_bpm
    # Protect the transcribed melody from the opium/hook-composer repair
    # steps later in humanize_pattern the same way midi_ingest.
    # ingest_uploaded_midi's melody is protected — this was a pre-existing
    # gap (neither this function nor ingest_reference_audio set the flag
    # before), fixed here since it benefits every caller, not just the new
    # explicit-upload-during-generation path below.
    pattern["plg_midi_melody"] = True
    # Product decision (2026-07): both callers of this function (apply_
    # reference_track's user-picked clip, ingest_uploaded_reference_audio's
    # explicit upload) hand this an explicit, user-pointed-at-one-file
    # source — finished material the engine should arrange drums AROUND
    # (density-driven rolls, kick/808 duck), never rewrite. Always on, no
    # opt-out: unlike plg_midi_melody above (shared with the library-
    # keyword-matched AUTO tier in ingest_reference_audio, which keeps its
    # own variation), this is only ever set here. See arranger.py's
    # melody_root_pc gate, the only consumer.
    pattern["plg_preserve_melody_sections"] = True
    meta = pattern.setdefault("plg_reference_reverse", {})
    if isinstance(meta, dict):
        meta["transcriber"] = "demucs+basic-pitch" if stem_meta.get("demucs") else "basic-pitch"
        meta["stems"] = stem_meta.get("sources", [])
        meta["demucs"] = bool(stem_meta.get("demucs"))
        meta["bpm_detected"] = detected_bpm
        meta["melody_notes"] = len(melody_notes)
        meta["bass_notes"] = len(bass_notes)

    if respect_uploaded_scale:
        from music_theory import detect_root_pc, detect_scale

        melody = track_notes(pattern, "melody_lead")
        if len(melody) >= 3:
            root_pc = detect_root_pc(melody)
            scale = detect_scale(melody, root_pc)
            pattern["scale"] = scale
            pattern["plg_uploaded_scale_lock"] = {"root_pc": root_pc, "scale": scale}

    return {
        "bpm": detected_bpm,
        "notes": len(melody_notes) + len(bass_notes),
        "melody_notes": len(melody_notes),
        "bass_notes": len(bass_notes),
        "transcriber": meta.get("transcriber") if isinstance(meta, dict) else "basic-pitch",
        "audio": str(audio),
    }


def ingest_reference_audio(
    pattern: dict[str, Any],
    *,
    library_root: Path | str,
    prompt: str = "",
    style: str = "",
) -> bool:
    """Transcribe matching library audio and merge notes into the pattern."""
    if not basic_pitch_available():
        return False

    root = Path(library_root)
    audio_path = find_reference_audio(root, prompt, style)
    if audio_path is None:
        return False

    try:
        rel = str(audio_path.relative_to(root)).lower().replace("\\", "/")
    except ValueError:
        rel = audio_path.name.lower()

    if not wants_reference_reverse(prompt, style) and not rel.startswith("references/"):
        return False

    bpm = float(pattern.get("bpm") or 120.0)
    melody_notes, bass_notes, stem_meta = transcribe_reference_stems(audio_path, bpm=bpm)
    if not melody_notes and not bass_notes:
        return False

    for note in melody_notes + bass_notes:
        note["from_reference"] = True

    prefix = "Reference reverse (demucs+basic-pitch)" if stem_meta.get("demucs") else "Reference reverse (basic-pitch)"
    ok = merge_stem_notes_into_pattern(
        pattern,
        melody_notes,
        bass_notes,
        source=str(audio_path),
        name=audio_path.name,
        manual_prefix=prefix,
    )
    if ok:
        pattern["plg_reference_reverse"]["transcriber"] = (
            "demucs+basic-pitch" if stem_meta.get("demucs") else "basic-pitch"
        )
        pattern["plg_reference_reverse"]["stems"] = stem_meta.get("sources", [])
        pattern["plg_reference_reverse"]["demucs"] = bool(stem_meta.get("demucs"))
        pattern["plg_midi_melody"] = True
    return ok


UPLOAD_AUDIO_MAX_FILE_BYTES = 60_000_000  # ~60 MB — a 4-min 44.1kHz/16-bit stereo WAV is ~42 MB; headroom for that, defense-in-depth against a pathological upload (same spirit as midi_ingest.UPLOAD_MAX_FILE_BYTES)
UPLOAD_AUDIO_RIGHTS_WARNING = "Only upload audio you own or have the rights to use."


def ingest_uploaded_reference_audio(
    pattern: dict[str, Any],
    audio_path: Path | str,
    *,
    respect_uploaded_scale: bool = False,
) -> dict[str, Any]:
    """Explicit user audio upload, wired for the SAME call-site priority as
    midi_ingest.ingest_uploaded_midi (audit 2026-07): thin wrapper around the
    EXISTING ingest_reference_from_file (already used by
    plg_api.apply_reference_track) — no duplicated transcription/merge logic,
    just the extra guardrails an untrusted upload-during-generation needs and
    a result shape matching ingest_uploaded_midi's:
        {"ok": bool, "warning": str | None, "notes": int}

    Passes the pattern's OWN already-resolved bpm through explicitly (not
    None) so ingest_reference_from_file does NOT auto-detect + overwrite
    pattern["bpm"] from the audio — the user's requested BPM is sacred here;
    the reference fits the track, not the other way around (contrast with
    apply_reference_track, which builds a fresh beat FROM the reference and
    deliberately wants BPM auto-detection).

    Length cap is the SAME MAX_BEATS=64.0 (16 bars) already baked into
    transcribe_reference_stems's own read_midi_notes call — inherited via
    ingest_reference_from_file, not reimplemented here."""
    result: dict[str, Any] = {"ok": False, "warning": None, "notes": 0}
    path = Path(audio_path)

    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        result["warning"] = "Not a supported audio file."
        return result
    try:
        size = path.stat().st_size
    except OSError:
        result["warning"] = "Audio file not found or unreadable."
        return result
    if size > UPLOAD_AUDIO_MAX_FILE_BYTES:
        result["warning"] = (
            f"Audio file too large ({size // 1_000_000} MB, "
            f"max {UPLOAD_AUDIO_MAX_FILE_BYTES // 1_000_000} MB)."
        )
        return result
    if not basic_pitch_available():
        result["warning"] = (
            "MIDI transcription isn't available (basic-pitch not installed) — "
            "install .venv-bp with basic-pitch (see requirements-optional.txt), "
            "or upload a .mid file instead."
        )
        return result

    meta = ingest_reference_from_file(
        pattern,
        path,
        bpm=float(pattern.get("bpm") or 120.0),
        respect_uploaded_scale=respect_uploaded_scale,
    )
    if meta is None:
        result["warning"] = "Transcription produced no usable notes from this audio file."
        return result

    pattern["plg_reference_uploaded"] = True
    result.update(ok=True, notes=meta["notes"], warning=UPLOAD_AUDIO_RIGHTS_WARNING)
    logger.info(
        "Uploaded reference audio transcribed: %s (%d notes, transcriber=%s, respect_scale=%s)",
        path.name,
        meta["notes"],
        meta.get("transcriber"),
        respect_uploaded_scale,
    )
    return result

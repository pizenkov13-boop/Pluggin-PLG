"""AI Sample-Chop Engine — slice foreign melodies into unrecognizable plugg builds."""

from __future__ import annotations

import hashlib
import logging
import random
import re
import shutil
from pathlib import Path
from typing import Any

from pydub import AudioSegment
from pydub_windows_compat import audio_segment_from_file

from plg_paths import app_dir

logger = logging.getLogger("plg.chop")

CHOP_COUNT = 16
MIN_CHOP_SOURCE_MS = 1800
CHOP_KEYWORDS = (
    "chop",
    "sample",
    "vintage",
    "loop",
    "japan",
    "gothic",
    "choir",
    "pop",
    "flip",
    "старый",
    "сэмпл",
    "нарез",
)

# 2026-07 audit: a plain substring check let "pop" match "hyperpop" — one of
# PLG's 10 core detailed genres — silently chopping/pitch/tempo-shifting the
# melody on EVERY hyperpop generation. Whole-word match only, so "hyperpop"
# no longer trips "pop" (no word boundary between "hyper" and "pop"), while
# an actual standalone "pop" in the prompt still does.
_CHOP_KEYWORD_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in CHOP_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

PROJECT_DIR = app_dir()
CHOP_OUTPUT_DIR = PROJECT_DIR / "output_chops"


def _rng_for(path: Path, prompt: str) -> random.Random:
    seed = hashlib.sha256(f"{path}|{prompt}".encode()).hexdigest()
    return random.Random(int(seed[:16], 16))


def _ffmpeg_available() -> bool:
    """pydub needs ffprobe/ffmpeg on PATH for most formats (incl. mp3)."""
    return shutil.which("ffprobe") is not None or shutil.which("ffmpeg") is not None


def _wants_chop_by_text(prompt: str, style: str = "") -> bool:
    text = f"{prompt} {style}"
    return bool(_CHOP_KEYWORD_PATTERN.search(text))


def should_chop_source(
    path: Path,
    prompt: str = "",
    style: str = "",
    pattern: dict[str, Any] | None = None,
) -> bool:
    if not _ffmpeg_available():
        return False
    if not path.is_file():
        return False
    try:
        audio = _load_audio(path)
    except (FileNotFoundError, OSError) as exc:
        logger.debug("Sample chop probe failed for %s: %s", path.name, exc)
        return False
    if len(audio) < 30:
        return False
    # Explicit chop keywords still win — intentional sample-chop workflow.
    if _wants_chop_by_text(prompt, style):
        return True
    # Length-based auto-chop must not destroy sustain-leaning MIDI melodies
    # (even borderline profiles like ~0.16 long_fraction / ~2s max).
    if pattern is not None:
        try:
            from melody_sample_bank import SUSTAIN_AFFINITY_SOFT_FLOOR, midi_sustain_affinity

            if midi_sustain_affinity(pattern) >= SUSTAIN_AFFINITY_SOFT_FLOOR:
                logger.info(
                    "Sample chop skipped (MIDI sustain affinity) for %s",
                    path.name,
                )
                return False
        except Exception as exc:  # noqa: BLE001 — affinity is advisory, never block export
            logger.debug("MIDI sustain affinity probe failed: %s", exc)
    return len(audio) >= MIN_CHOP_SOURCE_MS


def _load_audio(path: Path) -> AudioSegment:
    if not _ffmpeg_available():
        raise FileNotFoundError("ffmpeg/ffprobe not found on PATH")
    return audio_segment_from_file(path)


def chop_audio(
    path: Path,
    *,
    count: int = CHOP_COUNT,
    rng: random.Random | None = None,
) -> list[AudioSegment]:
    audio = _load_audio(path)
    rng = rng or random.Random(42)
    slice_ms = max(40, len(audio) // count)
    slices: list[AudioSegment] = []
    for i in range(count):
        start = i * slice_ms
        end = min(len(audio), start + slice_ms)
        if end - start < 30:
            continue
        slices.append(audio[start:end])
    rng.shuffle(slices)
    return slices


def _pitch_shift(segment: AudioSegment, semitones: float) -> AudioSegment:
    if abs(semitones) < 0.05:
        return segment
    factor = 2 ** (semitones / 12.0)
    new_rate = int(segment.frame_rate * factor)
    shifted = segment._spawn(segment.raw_data, overrides={"frame_rate": new_rate})
    return shifted.set_frame_rate(segment.frame_rate)


def _tempo_stretch(segment: AudioSegment, ratio: float) -> AudioSegment:
    if abs(ratio - 1.0) < 0.02:
        return segment
    # ratio > 1 = faster = shorter
    new_rate = int(segment.frame_rate * ratio)
    stretched = segment._spawn(segment.raw_data, overrides={"frame_rate": new_rate})
    return stretched.set_frame_rate(segment.frame_rate)


def write_chop_files(
    path: Path,
    *,
    prompt: str = "",
    count: int = CHOP_COUNT,
) -> tuple[list[Path], dict[str, Any]]:
    rng = _rng_for(path, prompt)
    slices = chop_audio(path, count=count, rng=rng)
    if not slices:
        return [], {}

    digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
    out_dir = CHOP_OUTPUT_DIR / digest
    out_dir.mkdir(parents=True, exist_ok=True)

    pitch_shift = rng.uniform(-3.0, 3.0)
    tempo_ratio = rng.uniform(0.88, 1.12)
    written: list[Path] = []

    for index, segment in enumerate(slices):
        piece = _pitch_shift(segment, pitch_shift + rng.uniform(-0.5, 0.5))
        piece = _tempo_stretch(piece, tempo_ratio * rng.uniform(0.95, 1.05))
        piece = piece.fade_in(8).fade_out(12)
        dest = out_dir / f"chop_{index:02d}.wav"
        piece.export(dest, format="wav")
        written.append(dest.resolve())

    meta = {
        "source": str(path.resolve()),
        "chop_count": len(written),
        "pitch_semitones": round(pitch_shift, 2),
        "tempo_ratio": round(tempo_ratio, 3),
        "output_dir": str(out_dir),
    }
    logger.info("Sample chop: %s -> %s pieces", path.name, len(written))
    return written, meta


def build_chop_arrangement(
    chop_paths: list[Path],
    *,
    bpm: float,
    bars: int = 8,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Scatter shuffled chops across the beat grid (melody_lead)."""
    rng = rng or random.Random(7)
    if not chop_paths:
        return []

    beats_per_bar = 4.0
    total_beats = bars * beats_per_bar
    placements: list[dict[str, Any]] = []
    order = list(chop_paths)
    rng.shuffle(order)

    step = 0.5  # eighth-note grid
    t = 0.0
    chop_index = 0
    while t < total_beats and chop_index < len(order):
        if rng.random() < 0.35:
            t += step
            continue
        path = order[chop_index % len(order)]
        length = rng.choice((0.25, 0.375, 0.5, 0.75))
        placements.append({
            "file": str(path),
            "track": "melody_lead",
            "time_step": round(t, 4),
            "length": length,
            "note": "C4",
            "velocity": rng.randint(72, 108),
            "chop": True,
            "chop_index": chop_index % len(order),
        })
        chop_index += 1
        t += length + rng.uniform(0.0, 0.25)

    return placements


def apply_sample_chop_to_pattern(
    pattern: dict[str, Any],
    melody_path: Path,
    *,
    prompt: str = "",
) -> bool:
    """Replace melody sample layer with chopped arrangement when appropriate."""
    if not should_chop_source(
        melody_path,
        prompt,
        str(pattern.get("style", "")),
        pattern=pattern,
    ):
        return False

    try:
        chops, meta = write_chop_files(melody_path, prompt=prompt)
    except (FileNotFoundError, OSError) as exc:
        logger.warning("Sample chop skipped (%s): %s", melody_path.name, exc)
        return False
    if not chops:
        return False

    rng = _rng_for(melody_path, prompt)
    bpm = float(pattern.get("bpm", 140))
    arrangement = build_chop_arrangement(chops, bpm=bpm, rng=rng)

    samples = [s for s in (pattern.get("samples") or []) if s.get("track") != "melody_lead"]
    samples.extend(arrangement)
    pattern["samples"] = samples
    pattern["plg_sample_chop"] = meta
    pattern["plg_sound_paths"] = dict(pattern.get("plg_sound_paths") or {})
    pattern["plg_sound_paths"]["melody_lead"] = str(chops[0])
    pattern["plg_sample_picks"] = dict(pattern.get("plg_sample_picks") or {})
    pattern["plg_sample_picks"]["melody_lead"] = f"{len(chops)}x chop"

    steps = list(pattern.get("manual_steps") or [])
    steps.insert(
        0,
        f"Sample-Chop Engine: {meta['chop_count']} slices from {Path(meta['source']).name} "
        f"(pitch {meta['pitch_semitones']:+.1f} st, tempo x{meta['tempo_ratio']}).",
    )
    pattern["manual_steps"] = steps[:14]
    return True

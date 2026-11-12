"""Numeric feature vector for beat patterns (reward model / offline learning)."""

from __future__ import annotations

import math
from typing import Any

from genre_profiles import PROFILES, detect_genre
from midi_validate import validate_pattern
from music_theory import detect_root_pc, snap_pc_to_scale
from pattern_score import score_humanized_pattern
from pattern_utils import count_all_track_notes, parse_note_name, track_notes

FEATURE_ORDER: tuple[str, ...] = (
    "bpm_norm",
    "kick_n",
    "snare_n",
    "hat_n",
    "melody_n",
    "bass_n",
    "proxy_score",
    "hat_vel_std",
    "kick_on_grid",
    "melody_in_scale",
    "warning_n",
    "genre_idx",
    "drive",
    "density",
)

_GENRE_INDEX = {name: idx for idx, name in enumerate(PROFILES)}


def _vel_std(notes: list[dict[str, Any]]) -> float:
    if len(notes) < 2:
        return 0.0
    vels = [int(n.get("velocity", 100)) for n in notes]
    mean = sum(vels) / len(vels)
    var = sum((v - mean) ** 2 for v in vels) / len(vels)
    return math.sqrt(var)


def _on_grid(step: float, mod: float = 4.0, targets: tuple[float, ...] = (0.0, 2.0)) -> bool:
    pos = step % mod
    return any(abs(pos - t) < 0.06 for t in targets)


def extract_pattern_features(pattern: dict[str, Any]) -> dict[str, float]:
    """Stable feature dict for one humanized pattern."""
    genre = str(pattern.get("plg_genre") or detect_genre(
        str(pattern.get("style", "")),
        str(pattern.get("user_prompt", "")),
    ))
    profile = PROFILES.get(genre, PROFILES["trap"])
    kicks = track_notes(pattern, "kick")
    snares = track_notes(pattern, "snare")
    hats = track_notes(pattern, "hi_hats")
    melody = track_notes(pattern, "melody_lead")
    bass = track_notes(pattern, "sub_808")

    bpm = float(pattern.get("bpm") or 120.0)
    proxy = score_humanized_pattern(pattern, profile)
    warnings = validate_pattern(pattern)

    kick_on_grid = 0.0
    if kicks:
        kick_on_grid = sum(1 for k in kicks if _on_grid(float(k["time_step"]))) / len(kicks)

    melody_in_scale = 0.0
    if melody and profile.melody_scale:
        root = detect_root_pc(melody + bass)
        in_scale = 0
        for note in melody:
            try:
                midi = parse_note_name(str(note["note"]))
            except (ValueError, TypeError):
                continue
            if snap_pc_to_scale(midi, root, profile.melody_scale) == midi:
                in_scale += 1
        melody_in_scale = in_scale / len(melody)

    total_notes = count_all_track_notes(pattern)
    density = min(1.0, total_notes / 48.0)

    return {
        "bpm_norm": min(1.5, max(0.0, bpm / 200.0)),
        "kick_n": min(1.0, len(kicks) / 16.0),
        "snare_n": min(1.0, len(snares) / 16.0),
        "hat_n": min(1.0, len(hats) / 32.0),
        "melody_n": min(1.0, len(melody) / 24.0),
        "bass_n": min(1.0, len(bass) / 16.0),
        "proxy_score": min(1.0, max(0.0, proxy / 100.0)),
        "hat_vel_std": min(1.0, _vel_std(hats) / 30.0),
        "kick_on_grid": kick_on_grid,
        "melody_in_scale": melody_in_scale,
        "warning_n": min(1.0, len(warnings) / 5.0),
        "genre_idx": _GENRE_INDEX.get(genre, 0) / max(1, len(_GENRE_INDEX) - 1),
        "drive": 1.0 if (pattern.get("plg_clipper_mode") or pattern.get("plg_filth_mode")) else 0.0,
        "density": density,
    }


def features_vector(pattern: dict[str, Any]) -> list[float]:
    feats = extract_pattern_features(pattern)
    return [float(feats[key]) for key in FEATURE_ORDER]

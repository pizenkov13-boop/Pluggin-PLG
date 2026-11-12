"""Proxy quality score for humanized PLG patterns (offline tuning / retry).

Lightweight MIDI-only metrics — no audio render, no LLM. Used by ``plg_tune.py``
to rank ``genre_profiles`` knob settings.
"""

from __future__ import annotations

import math
from typing import Any

from genre_profiles import GenreProfile
from music_theory import detect_root_pc, snap_pc_to_scale
from pattern_utils import parse_note_name, track_notes


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


def score_humanized_pattern(pattern: dict[str, Any], profile: GenreProfile) -> float:
    """Higher is better. Typical range ~20–90 depending on fixture density."""
    score = 0.0
    hats = track_notes(pattern, "hi_hats")
    kicks = track_notes(pattern, "kick")
    snares = track_notes(pattern, "snare")
    melody = track_notes(pattern, "melody_lead")
    bass = track_notes(pattern, "sub_808")

    # 2026-07: kick/808_bass note generation was restored (see
    # beat_humanize._apply_808_bass_lane) — an empty kick is a defect again
    # for every profile, bass808_enabled included. Do NOT re-add the
    # bass808_enabled exemption that lived here during the generation-removed
    # window: the new generator is deliberately sparser (2-3 onsets per
    # 4-bar cell, not 4-6), so this -25 plus the on-grid/kick_syncopation
    # bonuses below may need recalibrating for the new density once the
    # sound is confirmed — but they must stay a real, live signal, not a
    # bypassed one, or a genuinely broken/empty generation would sail
    # through the quality gate unnoticed exactly like it would have before.
    if not kicks:
        score -= 25.0
    if not hats:
        score -= 20.0
    if not snares:
        score -= 10.0

    if hats:
        vstd = _vel_std(hats)
        cap = 14.0 if profile.markov_hats else 10.0
        score += min(cap, vstd * 0.85)

    if hats and profile.hat_swing > 0.05:
        off = sum(
            1 for h in hats
            if abs((float(h["time_step"]) * 4) % 1 - round(float(h["time_step"]) * 4) % 1) > 0.02
        )
        score += min(12.0, off * 0.45 * profile.hat_swing)

    if kicks:
        on_main = sum(1 for k in kicks if _on_grid(float(k["time_step"])))
        score += min(12.0, on_main * 2.5)
        sync_hits = sum(1 for k in kicks if k.get("kick_sync"))
        if profile.kick_syncopation > 0:
            score += min(14.0, sync_hits * 3.5)

    if profile.melody_scale and melody:
        root = detect_root_pc(melody + bass)
        in_scale = 0
        for note in melody:
            try:
                midi = parse_note_name(str(note["note"]))
            except (ValueError, TypeError):
                continue
            if snap_pc_to_scale(midi, root, profile.melody_scale) == midi:
                in_scale += 1
        score += 22.0 * in_scale / max(1, len(melody))

    if profile.melody_lag_ms > 0 and melody:
        lagged = sum(1 for n in melody if n.get("groove_lag_ms"))
        score += 12.0 * lagged / len(melody)

    if bass:
        starts = [round(float(b["time_step"]), 4) for b in bass]
        if len(starts) == len(set(starts)):
            score += 6.0

    if profile.counter_melody and track_notes(pattern, "counter_melody"):
        score += 8.0

    if profile.snare_riser:
        risers = sum(1 for s in snares if s.get("riser"))
        score += min(16.0, risers * 0.6)

    if profile.drop_tension:
        # Gap before phrase drop (~beat 3.5 in last bar) — kick cleared.
        late_bar_kicks = [k for k in kicks if float(k["time_step"]) % 4 >= 3.45]
        if len(late_bar_kicks) < len([k for k in kicks if float(k["time_step"]) % 4 < 3.0]):
            score += 5.0

    # drive is a mix/aggression hint — not a MIDI metric; never score it (Optuna gamed +4/ unit).
    return round(score, 3)

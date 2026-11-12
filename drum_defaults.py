"""Default MIDI for kick / snare / clap when the LLM omits drum lanes."""

from __future__ import annotations

from typing import Any


def _bar_count(pattern: dict[str, Any], *, default: int = 4) -> int:
    tracks = pattern.get("tracks")
    if not isinstance(tracks, dict):
        return default

    max_step = 0.0
    for notes in tracks.values():
        if not isinstance(notes, list):
            continue
        for entry in notes:
            if isinstance(entry, dict):
                max_step = max(max_step, float(entry.get("time_step", 0)))
    if max_step <= 0:
        return default
    return max(default, int(max_step // 4) + 1)


# Half-time rage feel: clap+snare land on beat 3, kick bounces.
_KICK_HITS = ((0.0, 115), (0.75, 96), (2.5, 102), (3.5, 92))


def _kick_pattern(bars: int) -> list[dict[str, Any]]:
    return [
        {"time_step": bar * 4.0 + t, "note": "C1", "length": 0.45, "velocity": v}
        for bar in range(bars)
        for t, v in _KICK_HITS
    ]


def _snare_pattern(bars: int) -> list[dict[str, Any]]:
    # Half-time backbeat: one strong hit on beat 3.
    return [
        {"time_step": bar * 4.0 + 2.0, "note": "D1", "length": 0.35, "velocity": 110}
        for bar in range(bars)
    ]


def _clap_pattern(bars: int) -> list[dict[str, Any]]:
    return [
        {"time_step": bar * 4.0 + 2.0, "note": "E1", "length": 0.25, "velocity": 102}
        for bar in range(bars)
    ]


def _hat_pattern(bars: int) -> list[dict[str, Any]]:
    # Continuous 1/8 drive; hat_roll_engine then carves 1/32 rolls before beat 3.
    return [
        {"time_step": bar * 4.0 + step * 0.5, "note": "C5", "length": 0.2, "velocity": 94}
        for bar in range(bars)
        for step in range(8)
    ]


def ensure_drum_tracks(pattern: dict[str, Any]) -> None:
    """Fill kick/snare/clap/hats MIDI if the model returned only 808/melody."""
    tracks = pattern.setdefault("tracks", {})
    if not isinstance(tracks, dict):
        pattern["tracks"] = tracks = {}

    bars = _bar_count(pattern)
    if not tracks.get("kick"):
        tracks["kick"] = _kick_pattern(bars)
    if not tracks.get("snare"):
        tracks["snare"] = _snare_pattern(bars)
    if not tracks.get("clap"):
        tracks["clap"] = _clap_pattern(bars)
    if not tracks.get("hi_hats"):
        tracks["hi_hats"] = _hat_pattern(bars)

    order = pattern.get("build_order")
    if not isinstance(order, list) or not order:
        from pattern_utils import DEFAULT_BUILD_ORDER

        pattern["build_order"] = list(DEFAULT_BUILD_ORDER)

"""Pattern validator — structural checks + mix-safe sanitization before FLP export.

Runs after Symbolic ML to clamp velocities, trim overlaps,
and surface schema warnings without crashing the pipeline.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

from midi_validate import validate_pattern
from pattern_utils import TRACK_KEYS, parse_note_name, track_notes

logger = logging.getLogger("plg.validator")

MIN_NOTE_BEATS = 0.0625
MAX_VELOCITY = 127


@dataclass
class ValidationReport:
    ok: bool
    warnings: list[str] = field(default_factory=list)
    fixes: int = 0
    hard_fail: bool = False


def _sanitize_track(notes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Clamp fields and drop invalid note dicts."""
    fixes = 0
    cleaned: list[dict[str, Any]] = []
    for raw in notes:
        if not isinstance(raw, dict):
            fixes += 1
            continue
        try:
            pitch = parse_note_name(str(raw["note"]))
            if not 0 <= pitch <= 127:
                fixes += 1
                continue
            start = max(0.0, float(raw.get("time_step", 0.0)))
            length = max(MIN_NOTE_BEATS, float(raw.get("length", 0.25)))
            velocity = max(1, min(MAX_VELOCITY, int(raw.get("velocity", 100))))
        except (KeyError, TypeError, ValueError):
            fixes += 1
            continue

        entry = dict(raw)
        if entry.get("time_step") != start:
            fixes += 1
        if entry.get("length") != length:
            fixes += 1
        if entry.get("velocity") != velocity:
            fixes += 1
        entry["time_step"] = round(start, 5)
        entry["length"] = round(length, 5)
        entry["velocity"] = velocity
        cleaned.append(entry)

    cleaned.sort(
        key=lambda n: (
            float(n["time_step"]),
            -float(n["length"]),
            -int(n.get("velocity", 0)),
        )
    )

    accepted: list[dict[str, Any]] = []
    for entry in cleaned:
        pitch = parse_note_name(str(entry["note"]))
        start = float(entry["time_step"])
        length = float(entry["length"])

        for other in accepted:
            if parse_note_name(str(other["note"])) != pitch:
                continue
            o_start = float(other["time_step"])
            o_end = o_start + float(other["length"])
            if start < o_end and (start + length) > o_start:
                if o_start <= start:
                    new_len = max(MIN_NOTE_BEATS, round(start - o_start, 5))
                    if new_len != other["length"]:
                        fixes += 1
                    other["length"] = new_len
                else:
                    length = max(MIN_NOTE_BEATS, round(o_start - start, 5))
                    fixes += 1

        if length >= MIN_NOTE_BEATS:
            fixed = dict(entry)
            fixed["time_step"] = round(start, 5)
            fixed["length"] = round(length, 5)
            accepted.append(fixed)

    return sorted(accepted, key=lambda n: (float(n["time_step"]), str(n.get("note", "")))), fixes


def validate_and_sanitize_pattern(
    pattern: dict[str, Any],
    *,
    strict: bool = False,
) -> tuple[dict[str, Any], ValidationReport]:
    """Return a sanitized copy plus a validation report."""
    out = copy.deepcopy(pattern)
    warnings = validate_pattern(out)
    hard_fail = any(
        w.startswith(token)
        for w in warnings
        for token in ("no notes", "missing bpm", "invalid bpm", "tracks missing")
    )

    total_fixes = 0
    tracks = out.get("tracks")
    if isinstance(tracks, dict):
        for key in TRACK_KEYS:
            raw = tracks.get(key)
            if not isinstance(raw, list):
                continue
            sanitized, fixes = _sanitize_track(raw)
            tracks[key] = sanitized
            total_fixes += fixes

    ok = not hard_fail and (not strict or not warnings)
    report = ValidationReport(
        ok=ok,
        warnings=warnings,
        fixes=total_fixes,
        hard_fail=hard_fail,
    )

    if warnings:
        logger.debug("Validator warnings: %s", "; ".join(warnings[:8]))
    if total_fixes:
        logger.info("Validator applied %d sanitization fix(es)", total_fixes)

    meta = out.setdefault("metadata", {})
    if isinstance(meta, dict):
        meta["validator_warnings"] = len(warnings)
        meta["validator_sanitized"] = total_fixes > 0

    return out, report

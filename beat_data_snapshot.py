"""Build privacy-conscious beat payloads for PLG's cloud learning dataset."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pattern_features import extract_pattern_features
from pattern_utils import count_all_track_notes

_PATH_KEYS = ("path", "paths", "file", "files", "library", "dir", "folder")
_DROP_KEYS = {"plg_sound_paths", "sample_library", "plg_stem_files"}
_MAX_PATTERN_BYTES = 160_000


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()


def _basename(value: Any) -> Any:
    if isinstance(value, str):
        return Path(value).name or value
    if isinstance(value, list):
        return [_basename(item) for item in value[:64]]
    if isinstance(value, dict):
        return {str(k): _basename(v) for k, v in list(value.items())[:128]}
    return value


def _sanitize(value: Any, *, key: str = "") -> Any:
    if key in _DROP_KEYS:
        return None
    lowered = key.lower()
    if any(part in lowered for part in _PATH_KEYS):
        return _basename(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for raw_key, raw_val in value.items():
            child_key = str(raw_key)
            sanitized = _sanitize(raw_val, key=child_key)
            if sanitized is not None:
                out[child_key] = sanitized
        return out
    if isinstance(value, list):
        return [_sanitize(item, key=key) for item in value[:2048]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def sanitized_pattern(pattern: dict[str, Any]) -> dict[str, Any]:
    clean = _sanitize(pattern)
    return clean if isinstance(clean, dict) else {}


def pattern_size_bytes(pattern: dict[str, Any]) -> int:
    return len(json.dumps(pattern, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def build_feature_snapshot(pattern: dict[str, Any]) -> dict[str, Any]:
    features = extract_pattern_features(pattern)
    tracks = pattern.get("tracks") if isinstance(pattern.get("tracks"), dict) else {}
    out = {
        **features,
        "note_count": count_all_track_notes(pattern),
        "track_count": len([name for name, notes in tracks.items() if isinstance(notes, list) and notes]),
        "sample_count": len(pattern.get("samples") or []),
    }
    # Style-DNA identity strings ride along so the fleet can learn WHICH
    # progressions/pockets win likes per genre (numeric aggregators skip them).
    prog = pattern.get("plg_progression")
    if isinstance(prog, dict) and prog.get("name"):
        out["dna_progression"] = str(prog["name"])
    quality = pattern.get("plg_quality") if isinstance(pattern.get("plg_quality"), dict) else {}
    if quality.get("genre"):
        out["dna_genre"] = str(quality["genre"])
    return out


def build_beat_sync_payload(
    pattern: dict[str, Any],
    *,
    prompt: str,
    source: str = "desktop",
    model: str | None = None,
    parent_beat_id: str | None = None,
) -> dict[str, Any]:
    quality = pattern.get("plg_quality") if isinstance(pattern.get("plg_quality"), dict) else {}
    pipeline = pattern.get("plg_pipeline") if isinstance(pattern.get("plg_pipeline"), dict) else {}
    sample_picks = pattern.get("plg_sample_picks") if isinstance(pattern.get("plg_sample_picks"), dict) else {}
    clean_pattern = sanitized_pattern(pattern)
    size = pattern_size_bytes(clean_pattern)
    if size > _MAX_PATTERN_BYTES:
        # Keep the metadata even if a pathological pattern grows too large.
        clean_pattern = {
            "bpm": pattern.get("bpm"),
            "style": pattern.get("style"),
            "tracks": {key: len(val) for key, val in (pattern.get("tracks") or {}).items() if isinstance(val, list)},
            "truncated": True,
        }
        size = pattern_size_bytes(clean_pattern)

    return {
        "beat_id": str(pattern.get("plg_beat_id") or quality.get("beat_id") or ""),
        "parent_beat_id": parent_beat_id,
        "prompt_hash": prompt_hash(prompt),
        "locale": pattern.get("plg_locale"),
        "bpm": pattern.get("bpm"),
        "style": pattern.get("style"),
        "key": pattern.get("key"),
        "scale": pattern.get("scale"),
        "model": model,
        "quality": quality,
        "pipeline": pipeline,
        "sample_picks": {str(k): Path(str(v)).name for k, v in sample_picks.items()},
        "features": build_feature_snapshot(pattern),
        "pattern": clean_pattern,
        "pattern_version": 1,
        "pattern_size_bytes": size,
        "source": source,
    }


def build_event_properties(pattern: dict[str, Any], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    quality = pattern.get("plg_quality") if isinstance(pattern.get("plg_quality"), dict) else {}
    sample_picks = pattern.get("plg_sample_picks") if isinstance(pattern.get("plg_sample_picks"), dict) else {}
    props = {
        "genre": quality.get("genre"),
        "quality": quality,
        "quality_score": quality.get("score"),
        "proxy_score": quality.get("proxy_score"),
        "features": build_feature_snapshot(pattern),
        "pipeline": pattern.get("plg_pipeline") if isinstance(pattern.get("plg_pipeline"), dict) else {},
        "sample_picks": {str(k): Path(str(v)).name for k, v in sample_picks.items()},
    }
    if extra:
        props.update(extra)
    return props

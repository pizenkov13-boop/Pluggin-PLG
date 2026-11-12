"""Validate LLM beat pattern JSON before returning to client."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def validate_pattern(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise HTTPException(502, "Model returned invalid JSON.")

    for key in ("bpm", "tracks", "build_order"):
        if key not in data:
            raise HTTPException(502, f"Model output missing '{key}'.")

    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        raise HTTPException(502, "Model output 'tracks' must be an object.")

    try:
        bpm = float(data["bpm"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(502, "Invalid BPM in model output.") from exc

    if bpm < 40 or bpm > 220:
        raise HTTPException(502, "BPM out of range.")

    return data

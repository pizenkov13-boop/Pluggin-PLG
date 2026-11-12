"""Cloud data moat writes: beat metadata, sanitized patterns, and feedback."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from supabase import Client

_EVENT_RATING = {"liked": 1, "disliked": -1}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _quality_fields(quality: dict[str, Any]) -> dict[str, Any]:
    return {
        "quality_score": _num(quality.get("score")),
        "proxy_score": _num(quality.get("proxy_score")),
        "reward_score": _num(quality.get("reward_score")),
        "passed": quality.get("passed") if isinstance(quality.get("passed"), bool) else None,
        "hard_fail": quality.get("hard_fail") if isinstance(quality.get("hard_fail"), bool) else None,
        "genre": str(quality.get("genre") or "").strip() or None,
    }


def _ensure_beat(client: Client, user_id: str, beat_id: str) -> None:
    row = client.table("beats").select("beat_id,user_id").eq("beat_id", beat_id).maybe_single().execute()
    if row.data:
        if row.data.get("user_id") != user_id:
            # beat_id is globally unique, not scoped per-user — don't reveal
            # that a beat_id belonging to someone else exists.
            raise HTTPException(404, "Beat not found.")
        return
    client.table("beats").insert({"beat_id": beat_id, "user_id": user_id, "source": "event_stub"}).execute()


def upsert_beat(client: Client, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    beat_id = str(payload.get("beat_id") or "").strip()
    if not beat_id:
        raise HTTPException(400, "Missing beat_id.")

    # beat_id has a global-unique constraint (not (user_id, beat_id)), so
    # on_conflict="beat_id" below would silently overwrite someone else's
    # row if we let a foreign beat_id through — reject it up front instead.
    existing = client.table("beats").select("user_id").eq("beat_id", beat_id).maybe_single().execute()
    if existing.data and existing.data.get("user_id") != user_id:
        raise HTTPException(404, "Beat not found.")

    quality = _dict(payload.get("quality"))
    row: dict[str, Any] = {
        "beat_id": beat_id,
        "user_id": user_id,
        "parent_beat_id": payload.get("parent_beat_id"),
        "prompt_hash": payload.get("prompt_hash"),
        # Prompt previews were removed from the privacy contract. Keep the
        # column for backwards-compatible migrations, but never persist input.
        "prompt_preview": None,
        "locale": payload.get("locale"),
        "bpm": _num(payload.get("bpm")),
        "style": payload.get("style"),
        "key": payload.get("key"),
        "scale": payload.get("scale"),
        "model": payload.get("model"),
        "pipeline": _dict(payload.get("pipeline")),
        "sample_picks": _dict(payload.get("sample_picks")),
        "features": _dict(payload.get("features")),
        "source": payload.get("source"),
        "synced_at": _now(),
        "updated_at": _now(),
    }
    row.update(_quality_fields(quality))

    client.table("beats").upsert(row, on_conflict="beat_id").execute()

    pattern = payload.get("pattern")
    if isinstance(pattern, dict):
        pattern_row = {
            "beat_id": beat_id,
            "user_id": user_id,
            "pattern": pattern,
            "pattern_version": int(payload.get("pattern_version") or 1),
            "size_bytes": int(payload.get("pattern_size_bytes") or 0),
            "updated_at": _now(),
        }
        client.table("beat_patterns").upsert(pattern_row, on_conflict="beat_id").execute()

    record_beat_event(
        client,
        user_id,
        beat_id,
        "generated",
        source=str(payload.get("source") or "desktop"),
        properties={
            "bpm": row.get("bpm"),
            "style": row.get("style"),
            "genre": row.get("genre"),
            "quality_score": row.get("quality_score"),
            "model": row.get("model"),
        },
    )
    return {"ok": True, "beat_id": beat_id}


def record_beat_event(
    client: Client,
    user_id: str,
    beat_id: str,
    event_type: str,
    *,
    source: str | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    beat_id = (beat_id or "").strip()
    event_type = (event_type or "").strip().lower()
    if not beat_id:
        raise HTTPException(400, "Missing beat_id.")
    if event_type not in {"generated", "liked", "disliked", "deleted", "exported", "regenerated"}:
        raise HTTPException(400, f"Unsupported beat event: {event_type}")

    _ensure_beat(client, user_id, beat_id)
    props = properties or {}
    rating = _EVENT_RATING.get(event_type)
    client.table("beat_events").insert(
        {
            "beat_id": beat_id,
            "user_id": user_id,
            "event_type": event_type,
            "rating": rating,
            "source": source,
            "properties": props,
        }
    ).execute()

    if event_type == "deleted":
        client.table("beats").update({"status": "deleted", "updated_at": _now()}).eq("beat_id", beat_id).eq(
            "user_id", user_id
        ).execute()

    if rating is not None:
        quality = _dict(props.get("quality"))
        rating_row: dict[str, Any] = {
            "beat_id": beat_id,
            "user_id": user_id,
            "rating": rating,
            "genre": props.get("genre") or quality.get("genre"),
            "quality_score": _num(props.get("quality_score") or quality.get("score")),
            "proxy_score": _num(props.get("proxy_score") or quality.get("proxy_score")),
            "features": _dict(props.get("features")),
            "pipeline": _dict(props.get("pipeline")),
            "sample_picks": _dict(props.get("sample_picks")),
            "updated_at": _now(),
        }
        client.table("beat_ratings").upsert(rating_row, on_conflict="user_id,beat_id").execute()

    return {"ok": True, "beat_id": beat_id, "event_type": event_type}

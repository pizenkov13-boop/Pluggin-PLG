"""Offline-safe client sync for PLG's cloud learning dataset."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from beat_data_snapshot import build_beat_sync_payload, build_event_properties
from plg_paths import user_data_dir

logger = logging.getLogger("plg.data_sync")

_QUEUE_FILE = "data_sync_queue.jsonl"
_MAX_QUEUE_ROWS = 500


def _queue_path() -> Path:
    path = user_data_dir() / _QUEUE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _cloud_ready() -> bool:
    try:
        from app_config import offline_dev

        if offline_dev():
            return False
        import plg_cloud

        return bool(plg_cloud.is_signed_in())
    except Exception:
        return False


def _append_queue(item: dict[str, Any]) -> None:
    path = _queue_path()
    rows = []
    if path.is_file():
        rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows.append(json.dumps(item, ensure_ascii=False))
    rows = rows[-_MAX_QUEUE_ROWS:]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _send(item: dict[str, Any]) -> dict[str, Any]:
    import plg_cloud

    kind = item.get("kind")
    if kind == "beat":
        return plg_cloud.sync_beat(item.get("payload") or {})
    if kind == "event":
        return plg_cloud.sync_beat_event(
            str(item.get("beat_id") or ""),
            str(item.get("event_type") or ""),
            properties=item.get("properties") if isinstance(item.get("properties"), dict) else {},
            source=str(item.get("source") or "desktop"),
        )
    return {"ok": False, "error": f"Unknown sync item: {kind}", "error_type": "client"}


def flush_queue(limit: int = 25) -> dict[str, Any]:
    if not _cloud_ready():
        return {"ok": True, "flushed": 0, "queued": _queue_count()}
    path = _queue_path()
    if not path.is_file():
        return {"ok": True, "flushed": 0, "queued": 0}

    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    flushed = 0
    remaining: list[str] = []
    for line in lines:
        if flushed >= limit:
            remaining.append(line)
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            flushed += 1
            continue
        result = _send(item)
        if result.get("ok"):
            flushed += 1
        else:
            remaining.append(line)

    if remaining:
        path.write_text("\n".join(remaining[-_MAX_QUEUE_ROWS:]) + "\n", encoding="utf-8")
    else:
        try:
            path.unlink()
        except OSError:
            pass
    return {"ok": True, "flushed": flushed, "queued": len(remaining)}


def _queue_count() -> int:
    path = _queue_path()
    if not path.is_file():
        return 0
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def sync_generated_beat(
    pattern: dict[str, Any],
    *,
    prompt: str,
    model: str | None = None,
    parent_beat_id: str | None = None,
) -> dict[str, Any]:
    if not _cloud_ready():
        return {"ok": True, "skipped": "not_signed_in"}
    payload = build_beat_sync_payload(pattern, prompt=prompt, model=model, parent_beat_id=parent_beat_id)
    if not payload.get("beat_id"):
        return {"ok": False, "error": "Missing beat_id.", "error_type": "client"}

    flush_queue()
    item = {"kind": "beat", "payload": payload}
    result = _send(item)
    if result.get("ok"):
        return result
    logger.warning("beat sync queued: %s", result.get("error"))
    _append_queue(item)
    return {"ok": True, "queued": True, "error": result.get("error")}


def sync_beat_event(
    pattern: dict[str, Any],
    event_type: str,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _cloud_ready():
        return {"ok": True, "skipped": "not_signed_in"}
    beat_id = str(pattern.get("plg_beat_id") or (pattern.get("plg_quality") or {}).get("beat_id") or "")
    if not beat_id:
        return {"ok": False, "error": "Missing beat_id.", "error_type": "client"}

    properties = build_event_properties(pattern, extra=extra)
    flush_queue()
    item = {
        "kind": "event",
        "beat_id": beat_id,
        "event_type": event_type,
        "properties": properties,
        "source": "desktop",
    }
    result = _send(item)
    if result.get("ok"):
        return result
    logger.warning("beat event sync queued: %s", result.get("error"))
    _append_queue(item)
    return {"ok": True, "queued": True, "error": result.get("error")}

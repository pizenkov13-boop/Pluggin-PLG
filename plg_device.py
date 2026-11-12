"""Stable device id for cloud account binding."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from plg_paths import app_dir

_DEVICE_FILE = app_dir() / "plg_device.json"


def get_device_id() -> str:
    env = os.getenv("PLG_DEVICE_ID", "").strip()
    if env:
        return env

    if _DEVICE_FILE.is_file():
        try:
            data = json.loads(_DEVICE_FILE.read_text(encoding="utf-8"))
            device_id = str(data.get("device_id", "")).strip()
            if device_id:
                return device_id
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    device_id = str(uuid.uuid4())
    _DEVICE_FILE.write_text(
        json.dumps({"device_id": device_id}, indent=2),
        encoding="utf-8",
    )
    return device_id


def get_device_name() -> str:
    import platform

    return os.getenv("PLG_DEVICE_NAME") or platform.node() or "PLG Desktop"

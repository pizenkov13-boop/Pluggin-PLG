"""Monthly beat quota — 30 beats per 30-day subscription period."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_QUOTA_FILE = PROJECT_DIR / "beat_quota.json"
DEFAULT_LIMIT = 30
DEFAULT_PERIOD_DAYS = 30


class BeatQuotaExceeded(Exception):
    def __init__(self, message: str, *, days_until_reset: int) -> None:
        super().__init__(message)
        self.days_until_reset = days_until_reset


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _skip_limit() -> bool:
    from app_config import offline_dev

    if offline_dev():
        return True
    return os.getenv("PLG_SKIP_BEAT_LIMIT", "").strip().lower() in ("1", "true", "yes")


def _quota_path() -> Path:
    custom = os.getenv("PLG_BEAT_QUOTA_FILE", "").strip()
    return Path(custom) if custom else DEFAULT_QUOTA_FILE


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _subscription_start_from_env() -> date | None:
    raw = os.getenv("PLG_SUBSCRIPTION_START", "").strip()
    if not raw:
        return None
    try:
        return _parse_date(raw)
    except ValueError:
        return None


def load_quota_data() -> dict:
    path = _quota_path()
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("period_start"):
            return data

    today = date.today()
    subscription = _subscription_start_from_env() or today
    return {
        "subscription_start": subscription.isoformat(),
        "period_start": subscription.isoformat(),
        "beats_used": 0,
    }


def save_quota_data(data: dict) -> None:
    path = _quota_path()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _period_days() -> int:
    return _env_int("PLG_BEAT_PERIOD_DAYS", DEFAULT_PERIOD_DAYS)


def _beat_limit() -> int:
    return _env_int("PLG_BEAT_LIMIT", DEFAULT_LIMIT)


def _roll_period_if_needed(data: dict) -> dict:
    period_days = _period_days()
    period_start = _parse_date(str(data["period_start"]))
    today = date.today()
    while today >= period_start + timedelta(days=period_days):
        period_start = period_start + timedelta(days=period_days)
        data["beats_used"] = 0
        data["period_start"] = period_start.isoformat()
    return data


def get_quota_snapshot() -> dict:
    limit = _beat_limit()
    period_days = _period_days()
    if _skip_limit():
        return {
            "used": 0,
            "limit": limit,
            "remaining": limit,
            "days_until_reset": period_days,
            "skipped": True,
        }

    data = _roll_period_if_needed(load_quota_data())
    save_quota_data(data)

    period_start = _parse_date(str(data["period_start"]))
    period_end = period_start + timedelta(days=period_days)
    days_until = max(0, (period_end - date.today()).days)
    used = int(data.get("beats_used", 0))
    remaining = max(0, limit - used)

    return {
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "days_until_reset": days_until,
        "period_days": period_days,
        "skipped": False,
    }


def ensure_can_consume_beat() -> None:
    snap = get_quota_snapshot()
    if snap.get("skipped"):
        return
    if snap["remaining"] <= 0:
        period_days = snap.get("period_days", _period_days())
        days = snap["days_until_reset"]
        day_word = "day" if days == 1 else "days"
        raise BeatQuotaExceeded(
            f"Beat limit reached ({snap['limit']} beats / {period_days} days).\n"
            f"Resets in {days} {day_word}.",
            days_until_reset=days,
        )


def consume_beat() -> dict:
    if _skip_limit():
        return get_quota_snapshot()

    ensure_can_consume_beat()
    data = _roll_period_if_needed(load_quota_data())
    data["beats_used"] = int(data.get("beats_used", 0)) + 1
    save_quota_data(data)
    return get_quota_snapshot()


def format_quota_label(snap: dict | None = None) -> str:
    snap = snap or get_quota_snapshot()
    if snap.get("skipped"):
        return ""
    days = snap["days_until_reset"]
    return f"{snap['remaining']}/{snap['limit']} beats · resets in {days}d"

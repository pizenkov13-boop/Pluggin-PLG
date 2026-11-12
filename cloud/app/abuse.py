"""Abuse detection — IP generation spikes, alerts for admin."""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from supabase import Client

from cloud.app.config import IP_DAILY_GEN_ALERT

logger = logging.getLogger("plg.abuse")

_lock = threading.Lock()
_ip_daily_gens: dict[str, list[float]] = defaultdict(list)


def _utc_day_start() -> float:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp()


def record_generation_ip(client: Client, ip: str, user_id: str, device_id: str | None) -> None:
    if not ip:
        return

    day_start = _utc_day_start()
    now = time.time()
    with _lock:
        hits = [t for t in _ip_daily_gens[ip] if t >= day_start]
        hits.append(now)
        _ip_daily_gens[ip] = hits
        count = len(hits)

    if count >= IP_DAILY_GEN_ALERT:
        _raise_ip_alert(client, ip, count, user_id, device_id)


def _raise_ip_alert(
    client: Client,
    ip: str,
    count: int,
    user_id: str,
    device_id: str | None,
) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    alert_key = f"ip_spike:{ip}:{today}"
    existing = (
        client.table("abuse_alerts")
        .select("id")
        .eq("alert_type", alert_key)
        .maybe_single()
        .execute()
    )
    if existing.data:
        return

    client.table("abuse_alerts").insert(
        {
            "alert_type": alert_key,
            "ip_address": ip,
            "user_id": user_id,
            "details": {
                "generations_today": count,
                "threshold": IP_DAILY_GEN_ALERT,
                "device_id": device_id,
            },
        }
    ).execute()
    logger.warning("abuse alert ip=%s gens=%s user=%s", ip, count, user_id)


def list_alerts(client: Client, *, limit: int = 50, unacked_only: bool = True) -> list[dict[str, Any]]:
    query = client.table("abuse_alerts").select("*").order("created_at", desc=True).limit(limit)
    if unacked_only:
        query = query.eq("acknowledged", False)
    result = query.execute()
    return list(result.data or [])

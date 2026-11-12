"""Daily API spend cap (kill switch)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import HTTPException
from supabase import Client

from cloud.app.config import EST_COST_BASE, EST_COST_PREMIUM


def _today() -> date:
    return datetime.now(timezone.utc).date()


def check_kill_switch(client: Client, plan: str) -> None:
    row = client.table("kill_switch").select("*").eq("id", 1).maybe_single().execute()
    data = row.data or {}
    if not data.get("enabled", True):
        raise HTTPException(503, "Generation temporarily disabled.")

    today = _today()
    spend_reset = date.fromisoformat(str(data.get("spend_reset") or today)[:10])
    today_spend = float(data.get("today_spend_usd") or 0)
    cap = float(data.get("daily_spend_cap_usd") or 500)

    if today > spend_reset:
        today_spend = 0.0
        spend_reset = today
        client.table("kill_switch").update(
            {"today_spend_usd": 0, "spend_reset": spend_reset.isoformat()}
        ).eq("id", 1).execute()

    est = EST_COST_PREMIUM if plan == "premium" else EST_COST_BASE
    if today_spend + est > cap:
        raise HTTPException(503, "Daily AI capacity reached. Try again later.")


def record_spend(client: Client, plan: str, cost_usd: float | None = None) -> None:
    row = client.table("kill_switch").select("*").eq("id", 1).maybe_single().execute()
    data = row.data or {}
    today = _today()
    spend_reset = date.fromisoformat(str(data.get("spend_reset") or today)[:10])
    today_spend = float(data.get("today_spend_usd") or 0)
    if today > spend_reset:
        today_spend = 0.0
        spend_reset = today

    delta = cost_usd if cost_usd is not None else (
        EST_COST_PREMIUM if plan == "premium" else EST_COST_BASE
    )
    client.table("kill_switch").update(
        {
            "today_spend_usd": round(today_spend + delta, 6),
            "spend_reset": spend_reset.isoformat(),
        }
    ).eq("id", 1).execute()

"""Product analytics — server events + cohort metrics."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import Client
from cloud.app.privacy import exception_kind

logger = logging.getLogger("plg.analytics")


def track_event(
    client: Client,
    event_name: str,
    *,
    user_id: str | None = None,
    properties: dict[str, Any] | None = None,
) -> None:
    try:
        client.table("analytics_events").insert(
            {
                "user_id": user_id,
                "event_name": event_name,
                "properties": properties or {},
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("analytics event failed type=%s", exception_kind(exc))


def save_attribution(
    client: Client,
    user_id: str,
    *,
    utm_source: str | None = None,
    utm_medium: str | None = None,
    utm_campaign: str | None = None,
    utm_content: str | None = None,
    referrer: str | None = None,
) -> None:
    patch = {k: v for k, v in {
        "utm_source": utm_source,
        "utm_medium": utm_medium,
        "utm_campaign": utm_campaign,
        "utm_content": utm_content,
        "referrer": referrer,
    }.items() if v}
    if not patch:
        return
    client.table("profiles").update(patch).eq("id", user_id).execute()


def mark_first_beat(client: Client, user_id: str) -> None:
    row = client.table("profiles").select("first_beat_at").eq("id", user_id).maybe_single().execute()
    if row.data and row.data.get("first_beat_at"):
        return
    client.table("profiles").update(
        {"first_beat_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", user_id).execute()
    track_event(client, "first_beat", user_id=user_id)


def admin_referral_report(client: Client, *, days: int = 90) -> dict[str, Any]:
    """Payout report for paid-placement referral links (example.com/r/<code>).

    Hand-issued codes only (no self-serve referral program) — see
    cloud/app/checkout_links.py::clean_referral_code and
    cloud/app/providers/yookassa_provider.py's metadata.referral_code.
    Sums *succeeded* payments only; a later refund still counts here — cross-
    check against refund.succeeded rows with the same referral_code manually
    before paying out.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = (
        client.table("payment_events")
        .select("referral_code,amount_cents,currency,processed_at")
        .eq("event_type", "payment.succeeded")
        .not_.is_("referral_code", "null")
        .gte("processed_at", since)
        .limit(2000)
        .execute()
    )

    by_code: dict[str, dict[str, Any]] = {}
    for row in rows.data or []:
        code = str(row.get("referral_code") or "").strip()
        if not code:
            continue
        entry = by_code.setdefault(code, {"referral_code": code, "payments": 0, "amount_rub": 0.0})
        entry["payments"] += 1
        if (row.get("currency") or "RUB").upper() == "RUB":
            entry["amount_rub"] += int(row.get("amount_cents") or 0) / 100

    referrals = sorted(by_code.values(), key=lambda x: -x["amount_rub"])
    for entry in referrals:
        entry["amount_rub"] = round(entry["amount_rub"], 2)

    return {"ok": True, "period_days": days, "referrals": referrals}


def admin_metrics(client: Client, *, days: int = 30) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    gens = client.table("generation_logs").select("cost_usd", count="exact").gte("created_at", since).execute()
    api_spend = sum(float(r.get("cost_usd") or 0) for r in (gens.data or []))

    payments = (
        client.table("payment_events")
        .select("amount_cents,currency")
        .gte("created_at", since)
        .eq("event_type", "payment.succeeded")
        .execute()
    )
    revenue_rub = sum(
        int(p.get("amount_cents") or 0) / 100
        for p in (payments.data or [])
        if (p.get("currency") or "RUB").upper() == "RUB"
    )

    signups = client.table("analytics_events").select("id", count="exact").eq("event_name", "signup").gte("created_at", since).execute()
    first_beats = client.table("analytics_events").select("id", count="exact").eq("event_name", "first_beat").gte("created_at", since).execute()

    utm_rows = (
        client.table("profiles")
        .select("utm_source,status")
        .not_.is_("utm_source", "null")
        .limit(500)
        .execute()
    )
    by_utm: dict[str, int] = {}
    for row in utm_rows.data or []:
        src = str(row.get("utm_source") or "direct")
        by_utm[src] = by_utm.get(src, 0) + 1

    return {
        "ok": True,
        "period_days": days,
        "generations": gens.count or len(gens.data or []),
        "api_spend_usd": round(api_spend, 2),
        "revenue_rub_estimate": round(revenue_rub, 2),
        "signups": signups.count or 0,
        "first_beats": first_beats.count or 0,
        "margin_note": "Revenue from payment_events; API cost from generation_logs estimates.",
        "attribution_top": sorted(by_utm.items(), key=lambda x: -x[1])[:10],
    }

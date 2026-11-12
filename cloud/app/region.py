"""Geo region bootstrap — CIS → RU stack, intl → EU Supabase."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from cloud.app.config import (
    DATA_REGION,
    REGION_CIS_CLOUD_URL,
    REGION_CIS_SUPABASE_ANON_KEY,
    REGION_CIS_SUPABASE_URL,
    REGION_INTL_CLOUD_URL,
    REGION_INTL_SUPABASE_ANON_KEY,
    REGION_INTL_SUPABASE_URL,
)
from cloud.app.security import client_country


# Language-based fallback only, used when the IP can't be placed (see
# security.client_country — same real GeoIP lookup pricing uses). Distinct
# from pricing: this picks the Supabase stack (RU vs EU), not the price.
_CIS_LANG_PREFIXES = ("ru", "uk", "be", "kk", "uz", "hy", "ka", "az", "ky", "tg")


def _region_from_language(accept_language: str | None) -> str:
    lang = (accept_language or "").lower()
    if any(lang.startswith(p) for p in _CIS_LANG_PREFIXES):
        return "cis"
    return "intl"


def resolve_region(
    *,
    accept_language: str | None,
    country: str | None = None,
    explicit: str | None = None,
) -> str:
    if explicit in ("cis", "intl"):
        return explicit
    cis_countries = {
        "RU",
        "BY",
        "KZ",
        "UA",
        "UZ",
        "AM",
        "AZ",
        "GE",
        "KG",
        "TJ",
        "MD",
    }
    if country and country in cis_countries:
        return "cis"
    return _region_from_language(accept_language)


def region_payload(
    request: Request,
    *,
    explicit: str | None = None,
) -> dict[str, Any]:
    # Skip the GeoIP lookup entirely when the caller already forces a region —
    # also keeps this a no-op against a bare test double that has no `.client`.
    country = None if explicit in ("cis", "intl") else client_country(request)
    region = resolve_region(
        accept_language=request.headers.get("Accept-Language"),
        country=country,
        explicit=explicit,
    )
    if region == "cis":
        return {
            "ok": True,
            "region": "cis",
            "data_region": "ru",
            "supabase_url": REGION_CIS_SUPABASE_URL,
            "supabase_anon_key": REGION_CIS_SUPABASE_ANON_KEY,
            "cloud_url": REGION_CIS_CLOUD_URL,
        }
    return {
        "ok": True,
        "region": "intl",
        "data_region": "eu",
        "supabase_url": REGION_INTL_SUPABASE_URL,
        "supabase_anon_key": REGION_INTL_SUPABASE_ANON_KEY,
        "cloud_url": REGION_INTL_CLOUD_URL,
    }


def current_data_region_label() -> str:
    return DATA_REGION or "eu"

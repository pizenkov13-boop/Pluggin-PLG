"""Offline IP -> country lookup, entirely on our own server.

Reads a local DB-IP *IP-to-Country Lite* database (``.mmdb``, MaxMind-DB
format, CC-BY-4.0, published by a EU company). The visitor's IP is compared
against a static table already in memory — nothing is sent anywhere, so this
adds no cross-border transfer of personal data (152-ФЗ). Contrast with a
geo-CDN header (Cloudflare et al.), which would route the IP through a foreign
processor.

The database file is NOT in the image by default. Drop it at
``cloud/app/data/dbip-country-lite.mmdb`` (or point ``PLG_GEOIP_DB`` elsewhere)
and refresh it manually once a quarter — see
``cloud/infra/ru-hosting-runbook.md``. When the file is absent every lookup
returns ``None`` and callers fall back to the domestic tier, so a missing DB
degrades safely instead of breaking checkout.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
from typing import Any

from cloud.app.config import GEOIP_DB_PATH

logger = logging.getLogger("plg.geoip")

_lock = threading.Lock()
_reader: Any | None = None
_loaded = False
_warned = False


def _load_reader() -> Any | None:
    """Open the .mmdb once. Any failure (missing file, missing lib, corrupt
    DB) is logged a single time and turned into ``None`` — never an exception
    on the request path."""
    global _reader, _loaded, _warned
    if _loaded:
        return _reader
    with _lock:
        if _loaded:
            return _reader
        try:
            import maxminddb

            _reader = maxminddb.open_database(GEOIP_DB_PATH)
            logger.info("geoip db loaded path=%s", GEOIP_DB_PATH)
        except FileNotFoundError:
            if not _warned:
                logger.warning(
                    "geoip db missing path=%s — country lookups disabled, "
                    "callers fall back to domestic tier",
                    GEOIP_DB_PATH,
                )
                _warned = True
            _reader = None
        except Exception as exc:  # noqa: BLE001 - never break the request
            if not _warned:
                logger.warning("geoip db load failed path=%s err=%r", GEOIP_DB_PATH, exc)
                _warned = True
            _reader = None
        _loaded = True
    return _reader


def reset_cache() -> None:
    """Drop the cached reader (tests, or after a manual DB swap)."""
    global _reader, _loaded, _warned
    with _lock:
        try:
            if _reader is not None and hasattr(_reader, "close"):
                _reader.close()
        except Exception:  # noqa: BLE001
            pass
        _reader = None
        _loaded = False
        _warned = False


def _is_public(ip: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast:
        return None
    if addr.is_reserved or addr.is_unspecified:
        return None
    return addr


def country_for_ip(ip: str | None) -> str | None:
    """ISO-3166 alpha-2 (upper) for ``ip``, or ``None`` when it can't be
    resolved — private/reserved address, DB absent, or IP not in the table."""
    if not ip:
        return None
    if _is_public(ip) is None:
        return None
    reader = _load_reader()
    if reader is None:
        return None
    try:
        rec = reader.get(ip)
    except (ValueError, KeyError):
        return None
    if not isinstance(rec, dict):
        return None
    country = rec.get("country") or rec.get("registered_country") or {}
    code = country.get("iso_code") if isinstance(country, dict) else None
    return code.upper() if isinstance(code, str) and code else None

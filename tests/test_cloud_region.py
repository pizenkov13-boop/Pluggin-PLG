"""Tests for geo region bootstrap."""

from unittest.mock import patch

from cloud.app.region import resolve_region, region_payload


def test_resolve_region_ru_country():
    assert resolve_region(accept_language="en-US", country="RU") == "cis"


def test_resolve_region_en_locale():
    assert resolve_region(accept_language="en-US", country=None) == "intl"


def test_region_payload_cis():
    class Req:
        headers = {"Accept-Language": "ru-RU"}

    payload = region_payload(Req(), explicit="cis")
    assert payload["region"] == "cis"
    assert payload["data_region"] == "ru"


def test_region_payload_uses_real_geoip_not_just_language():
    """Regression test: region_payload() used to resolve country only via a
    CF-IPCountry header, which is never set in production (no CDN sits in
    front of cloud.example.com — see TRUSTED_PROXY in cloud/app/config.py).
    That silently degraded to guessing the region from Accept-Language for
    nearly every real request. A Russian visitor's IP must still resolve to
    the CIS/RU stack even with an English-language client and no CF-IPCountry
    header — via the same real per-IP GeoIP lookup billing already uses.
    """

    class Req:
        headers = {"Accept-Language": "en-US"}
        client = type("Client", (), {"host": "5.44.0.1"})()

    with patch("cloud.app.geoip.country_for_ip", return_value="RU"):
        payload = region_payload(Req())

    assert payload["region"] == "cis"
    assert payload["data_region"] == "ru"

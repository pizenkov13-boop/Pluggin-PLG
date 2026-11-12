"""plg_cloud has no real server address in its code: a release exe takes PLG_CLOUD_URL from its own .env.

The last-resort bootstrap URL used to be a public IP and port (the old server); it is now loopback, like the
other two defaults in the module, so an app with no configuration never sends a request to a host that may
belong to someone else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import plg_cloud

SOURCE = Path(plg_cloud.__file__)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("PLG_CLOUD_URL", "PLG_REGION_BOOTSTRAP_URL", "PLG_REGION_AUTO"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(plg_cloud, "_region_cache", None)


class _Spy:
    """A client that records the URL asked for and then fails, so nothing leaves the machine."""

    urls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, url, **_kwargs):
        _Spy.urls.append(url)
        raise RuntimeError("no network in tests")


@pytest.fixture
def spy(monkeypatch):
    _Spy.urls = []
    monkeypatch.setattr(plg_cloud, "make_client", lambda *a, **k: _Spy())
    return _Spy


def test_with_nothing_configured_the_bootstrap_asks_loopback_only(spy):
    plg_cloud.bootstrap_region()
    assert spy.urls == ["http://127.0.0.1:8787/v1/region"]


def test_a_release_configuration_is_used_as_it_is(spy, monkeypatch):
    monkeypatch.setenv("PLG_CLOUD_URL", "https://cloud.example.test/")
    plg_cloud.bootstrap_region()
    assert spy.urls == ["https://cloud.example.test/v1/region"]
    assert plg_cloud.cloud_api_url() == "https://cloud.example.test"
    assert plg_cloud._cloud_probe_url() == "https://cloud.example.test"


def test_the_bootstrap_address_beats_the_cloud_address(spy, monkeypatch):
    monkeypatch.setenv("PLG_CLOUD_URL", "https://cloud.example.test")
    monkeypatch.setenv("PLG_REGION_BOOTSTRAP_URL", "https://boot.example.test")
    plg_cloud.bootstrap_region()
    assert spy.urls == ["https://boot.example.test/v1/region"]


def test_the_module_carries_no_address_except_loopback():
    text = SOURCE.read_text(encoding="utf-8")
    addresses = set(re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", text))
    assert addresses <= {"127.0.0.1", "0.0.0.0"}, "a real address is hard-coded in plg_cloud.py"

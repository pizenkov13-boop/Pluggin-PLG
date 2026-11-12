"""The status never waits on the network (plg_api.get_status).

With the server down Home used to sit blank for 1-2 minutes: the status ran the token
refresh, /v1/me, the ping and a second /v1/me one after the other, each with a DoH
retry. Now one background thread asks, get_status waits for it at most _NET_BUDGET_S
and otherwise answers from local state with ``network_checking``.
"""

from __future__ import annotations

import threading
import time

import pytest

import plg_api
import plg_cloud

QUOTA = {"used": 1, "limit": 30, "remaining": 29, "days_until_reset": 5, "skipped": False, "label": "29/30"}


@pytest.fixture
def cloud(monkeypatch):
    """Cloud mode on, the server controlled by the test."""
    gate = threading.Event()
    calls = {"ensure": 0, "ping": 0}

    def ensure_session(*, validate=False):
        calls["ensure"] += 1
        gate.wait(10)
        return {"ok": True, "signed_in": True}

    def ping_cloud():
        calls["ping"] += 1
        return {"ok": True} if calls.get("up", True) else {"ok": False, "error": "timed out", "error_type": "network"}

    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: True)
    monkeypatch.setattr(plg_api, "_NET_BUDGET_S", 0.2)
    monkeypatch.setattr(plg_api, "_net_thread", None)
    monkeypatch.setattr(plg_api, "_net_result", None)
    monkeypatch.setattr(plg_api, "_session_validated", False)
    monkeypatch.setattr(plg_api, "_beat_job_running", lambda: False)
    monkeypatch.setattr(plg_api, "get_quota", lambda: dict(QUOTA))
    monkeypatch.setattr(plg_cloud, "ensure_session", ensure_session)
    monkeypatch.setattr(plg_cloud, "ping_cloud", ping_cloud)
    monkeypatch.setattr(plg_cloud, "session_snapshot", lambda: {"signed_in": True, "email": "a@b.c"})
    monkeypatch.setattr(plg_cloud, "cloud_api_url", lambda: "https://cloud.example")
    yield gate, calls
    gate.set()
    thread = plg_api._net_thread
    if thread is not None:
        thread.join(2)


def test_a_slow_server_does_not_hold_the_status_and_the_local_part_is_there(cloud):
    gate, _calls = cloud
    started = time.monotonic()
    status = plg_api.get_status()
    assert time.monotonic() - started < 1.5, "the status waited for the server"

    assert status["network_checking"] is True
    assert status["network_online"] is None  # no verdict yet: the UI must not show the offline banner
    assert status["signed_in"] is True and status["auth_email"] == "a@b.c"  # from the local session
    assert "library_audio_total" in status and "material_total" in status  # the local half is complete


def test_only_one_probe_runs_however_often_the_status_is_asked(cloud):
    gate, calls = cloud
    for _ in range(5):
        assert plg_api.get_status()["network_checking"] is True
    assert calls["ensure"] == 1, "a second status call started a second server probe"


def test_when_the_probe_finishes_the_next_status_has_the_real_answer(cloud):
    gate, calls = cloud
    assert plg_api.get_status()["network_checking"] is True
    gate.set()
    plg_api._net_thread.join(2)

    status = plg_api.get_status()
    assert status["network_checking"] is False
    assert status["network_online"] is True and status["cloud_error"] is None
    assert status["quota"]["label"] == "29/30"


def test_an_unreachable_server_is_reported_once_it_has_answered(cloud):
    gate, calls = cloud
    calls["up"] = False
    gate.set()
    status = plg_api.get_status()  # fast path: the probe finishes inside the budget
    assert status["network_checking"] is False
    assert status["network_online"] is False
    assert status["cloud_error_type"] == "network"
    assert status["quota"]["skipped"] is True  # nothing to ask the server
    assert calls["ensure"] == 0, "the token refresh was tried against a server that does not answer"


def test_a_blip_that_ping_still_forgives_is_checking_not_online(cloud, monkeypatch):
    gate, calls = cloud
    monkeypatch.setattr(
        plg_cloud, "ping_cloud", lambda: {"ok": True, "probe_degraded": True, "last_probe_error": "timed out"}
    )
    gate.set()
    status = plg_api.get_status()
    assert status["network_checking"] is True  # the UI asks again in a few seconds
    assert status["network_online"] is True and calls["ensure"] == 0


def test_retry_forgets_the_old_answer(cloud, monkeypatch):
    gate, calls = cloud
    calls["up"] = False
    gate.set()
    assert plg_api.get_status()["network_online"] is False

    calls["up"] = True
    result = plg_api.ping_cloud_connection()
    assert result["online"] is True
    assert plg_api._net_result is None
    plg_api._net_thread.join(2)
    assert plg_api.get_status()["network_online"] is True


def test_without_cloud_mode_nothing_is_probed(monkeypatch):
    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: False)
    monkeypatch.setattr(plg_api, "_net_thread", None)

    def boom(*_a, **_k):
        raise AssertionError("no server in local mode")

    monkeypatch.setattr(plg_cloud, "ensure_session", boom)
    monkeypatch.setattr(plg_cloud, "ping_cloud", boom)
    status = plg_api.get_status()
    assert status["network_checking"] is False and status["network_online"] is True
    assert plg_api._net_thread is None

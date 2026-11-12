"""No network must not break the cloud session or the status screen."""

import time

import httpx
import pytest

import plg_api
import plg_cloud


class _Client:
    """Stands in for plg_net.make_client(): either raises on post or answers a status code."""

    def __init__(self, *, error: Exception | None = None, status: int = 200, body: dict | None = None):
        self._error, self._status, self._body = error, status, body or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, *_args, **_kwargs):
        if self._error is not None:
            raise self._error
        return httpx.Response(self._status, json=self._body)


@pytest.fixture
def stored(monkeypatch):
    """An expired access token with a refresh token, kept in a dict instead of the real session file."""
    state = {"session": {"access_token": "old", "refresh_token": "r", "expires_at": time.time() - 3600,
                         "email": "a@b.c"}, "cleared": False, "stored": []}
    monkeypatch.setattr(plg_cloud, "load_session", lambda: dict(state["session"]))

    def clear():
        state["session"], state["cleared"] = {}, True

    monkeypatch.setattr(plg_cloud, "clear_session", clear)
    monkeypatch.setattr(plg_cloud, "_store_auth_response", lambda payload: state["stored"].append(payload))
    monkeypatch.setattr(plg_cloud, "supabase_url", lambda: "https://sb.example")
    monkeypatch.setattr(plg_cloud, "_supabase_headers", lambda: {})
    return state


def _client(monkeypatch, **kwargs):
    monkeypatch.setattr(plg_cloud, "make_client", lambda **_k: _Client(**kwargs))


def test_a_refresh_without_network_reports_it_and_keeps_the_session(stored, monkeypatch):
    _client(monkeypatch, error=httpx.ConnectError("blocked"))

    result = plg_cloud.refresh_session()

    assert result["ok"] is False and result["error_type"] == "network"
    assert stored["cleared"] is False and stored["session"]["refresh_token"] == "r"


def test_without_network_the_user_stays_signed_in_and_nothing_raises(stored, monkeypatch):
    _client(monkeypatch, error=httpx.ConnectError("blocked"))

    assert plg_cloud.ensure_session() == {"ok": True, "signed_in": True, "offline": True}
    assert plg_cloud.is_signed_in() is True
    assert stored["cleared"] is False


@pytest.mark.parametrize("status", [400, 401, 403])
def test_a_refusal_of_the_refresh_token_signs_the_user_out(stored, monkeypatch, status):
    _client(monkeypatch, status=status)

    assert plg_cloud.ensure_session() == {"ok": True, "signed_in": False}
    assert stored["cleared"] is True


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_a_server_hiccup_does_not_sign_the_user_out(stored, monkeypatch, status):
    _client(monkeypatch, status=status)

    result = plg_cloud.refresh_session()

    assert result["ok"] is False and result["error_type"] == "cloud"
    assert stored["cleared"] is False
    assert plg_cloud.ensure_session() == {"ok": True, "signed_in": True, "offline": True}


def test_a_good_refresh_still_stores_the_new_tokens(stored, monkeypatch):
    _client(monkeypatch, status=200, body={"access_token": "new", "refresh_token": "r2"})

    result = plg_cloud.refresh_session()

    assert result["ok"] is True
    assert stored["stored"] == [{"access_token": "new", "refresh_token": "r2"}]


def test_status_opens_offline_with_the_local_library_and_the_cloud_marked_unreachable(stored, monkeypatch):
    _client(monkeypatch, error=httpx.ConnectError("blocked"))
    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: True)
    monkeypatch.setattr(plg_api, "_library_synced_at_boot", True)
    monkeypatch.setattr(plg_api, "_session_validated", False)
    monkeypatch.setattr(plg_api, "_cached_material_counts", lambda: (3, 2, 5))
    monkeypatch.setattr(plg_api, "get_quota", lambda: {"used": 0, "limit": 0, "remaining": 0, "days_until_reset": 0,
                                                       "skipped": False, "label": ""})
    monkeypatch.setattr(plg_cloud, "fetch_me", lambda: {"ok": False, "error": "x", "error_type": "network"})
    monkeypatch.setattr(plg_cloud, "ping_cloud", lambda: {"ok": False, "error": "no route", "error_type": "network"})

    status = plg_api.get_status()

    assert status["ok"] is True
    assert status["signed_in"] is True and status["network_online"] is False
    assert (status["library_audio_total"], status["library_midi_total"], status["material_total"]) == (3, 2, 5)
    assert status["cloud_error_type"] == "network"


def test_the_status_fallback_never_goes_back_to_the_network(stored, monkeypatch):
    """ensure_session raising must not make get_status call the network again."""
    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: True)
    monkeypatch.setattr(plg_api, "_library_synced_at_boot", True)
    monkeypatch.setattr(plg_api, "_session_validated", False)
    monkeypatch.setattr(plg_api, "_cached_material_counts", lambda: (0, 0, 0))
    monkeypatch.setattr(plg_api, "get_quota", lambda: {"used": 0, "limit": 0, "remaining": 0, "days_until_reset": 0,
                                                       "skipped": False, "label": ""})
    monkeypatch.setattr(plg_cloud, "ping_cloud", lambda: {"ok": False, "error": "x", "error_type": "network"})

    def boom(**_k):
        raise httpx.ConnectError("blocked")

    def must_not_run():
        raise AssertionError("is_signed_in() went back to the network")

    monkeypatch.setattr(plg_cloud, "ensure_session", boom)
    monkeypatch.setattr(plg_cloud, "is_signed_in", must_not_run)

    assert plg_api.get_status()["signed_in"] is True

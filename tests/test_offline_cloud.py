"""Tests for offline banner / cloud mode switching."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import plg_api
import plg_cloud


def test_ping_cloud_uses_lightweight_health_endpoint(monkeypatch):
    monkeypatch.setenv("PLG_CLOUD_URL", "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "_region_cache", None)
    response = MagicMock(status_code=200)
    client = MagicMock()
    client.__enter__.return_value.get.return_value = response

    with patch("plg_cloud.httpx.Client", return_value=client):
        result = plg_cloud.ping_cloud()

    assert result == {"ok": True, "cloud_url": "https://cloud.example.test"}
    assert client.__enter__.call_count == 1
    client.__enter__.return_value.get.assert_called_once_with(
        "https://cloud.example.test/health",
        headers={"X-PLG-Version": plg_cloud.app_version()},
    )


def test_ping_cloud_does_not_mark_offline_after_one_transient_timeout(monkeypatch):
    monkeypatch.setenv("PLG_CLOUD_URL", "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "_region_cache", None)
    monkeypatch.setattr(plg_cloud, "_cloud_probe_failures", 0)
    client = MagicMock()
    client.__enter__.return_value.get.side_effect = TimeoutError("timed out")

    with patch("plg_cloud.httpx.Client", return_value=client):
        result = plg_cloud.ping_cloud()

    assert result["ok"] is True
    assert result["probe_degraded"] is True
    assert client.__enter__.return_value.get.call_count == plg_cloud._CLOUD_PROBE_ATTEMPTS


def _probe_client(get_side_effect=None, status_code=200):
    client = MagicMock()
    if get_side_effect is not None:
        client.__enter__.return_value.get.side_effect = get_side_effect
    else:
        client.__enter__.return_value.get.return_value = MagicMock(status_code=status_code)
    return client


def _fresh_probe_state(monkeypatch):
    monkeypatch.setenv("PLG_CLOUD_URL", "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "_region_cache", None)
    monkeypatch.setattr(plg_cloud, "_cloud_probe_failures", 0)
    monkeypatch.setattr(plg_cloud, "_cloud_probe_last_failure_at", 0.0)


def test_a_server_error_page_is_not_a_lost_connection(monkeypatch):
    # A 502 from a proxy or a maintenance 503 used to push the counter straight
    # to "offline" on a single response.
    _fresh_probe_state(monkeypatch)
    monkeypatch.setattr(plg_cloud, "_cloud_probe_failures", 2)
    with patch("plg_cloud.httpx.Client", return_value=_probe_client(status_code=503)):
        result = plg_cloud.ping_cloud()
    assert result["ok"] is True
    assert result["http_status"] == 503
    assert plg_cloud._cloud_probe_failures == 0


def test_three_recent_failed_rounds_mean_offline(monkeypatch):
    _fresh_probe_state(monkeypatch)
    with patch("plg_cloud.httpx.Client", return_value=_probe_client(TimeoutError("timed out"))):
        results = [plg_cloud.ping_cloud() for _ in range(plg_cloud._CLOUD_PROBE_FAILURES_BEFORE_OFFLINE)]
    assert [r["ok"] for r in results] == [True, True, False]
    assert results[-1]["error_type"] == "network"


def test_old_failures_do_not_add_up_to_offline(monkeypatch):
    _fresh_probe_state(monkeypatch)
    clock = {"now": 10_000.0}
    monkeypatch.setattr(plg_cloud.time, "monotonic", lambda: clock["now"])
    with patch("plg_cloud.httpx.Client", return_value=_probe_client(TimeoutError("timed out"))):
        assert plg_cloud.ping_cloud()["ok"] is True
        assert plg_cloud.ping_cloud()["ok"] is True
        clock["now"] += plg_cloud._CLOUD_PROBE_FAILURE_WINDOW_S + 1  # the earlier blips expired
        assert plg_cloud.ping_cloud()["ok"] is True
    assert plg_cloud._cloud_probe_failures == 1


def test_offline_stays_offline_until_a_probe_succeeds(monkeypatch):
    _fresh_probe_state(monkeypatch)
    clock = {"now": 10_000.0}
    monkeypatch.setattr(plg_cloud.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(plg_cloud, "_cloud_probe_failures", plg_cloud._CLOUD_PROBE_FAILURES_BEFORE_OFFLINE)
    monkeypatch.setattr(plg_cloud, "_cloud_probe_last_failure_at", clock["now"])
    clock["now"] += plg_cloud._CLOUD_PROBE_FAILURE_WINDOW_S * 5
    with patch("plg_cloud.httpx.Client", return_value=_probe_client(TimeoutError("timed out"))):
        assert plg_cloud.ping_cloud()["ok"] is False  # one failed re-check does not flip it back
    with patch("plg_cloud.httpx.Client", return_value=_probe_client(status_code=200)):
        assert plg_cloud.ping_cloud()["ok"] is True


def test_no_fresh_probe_while_a_beat_is_being_generated(monkeypatch):
    monkeypatch.setenv("PLG_CLOUD_MODE", "true")
    monkeypatch.setattr(plg_api, "_last_reachability", {"online": True, "url": "https://cloud.example.test"})
    running = plg_api._Job(id="j1", kind="beat")
    monkeypatch.setitem(plg_api._jobs, "j1", running)
    with patch.object(plg_api.plg_cloud, "ping_cloud", side_effect=AssertionError("probed mid-generation")) as ping:
        assert plg_api._cloud_reachability()["online"] is True
        ping.assert_not_called()
    # the banner's explicit retry always probes
    with patch.object(plg_api.plg_cloud, "ping_cloud", return_value={"ok": False, "error": "down", "error_type": "network"}) as ping:
        with patch.object(plg_api.plg_cloud, "cloud_api_url", return_value="https://cloud.example.test"):
            assert plg_api.ping_cloud_connection()["online"] is False
        ping.assert_called_once()


def test_probe_resumes_once_the_beat_is_finished(monkeypatch):
    monkeypatch.setenv("PLG_CLOUD_MODE", "true")
    done = plg_api._Job(id="j2", kind="beat", status="error")
    monkeypatch.setitem(plg_api._jobs, "j2", done)
    with patch.object(plg_api.plg_cloud, "ping_cloud", return_value={"ok": False, "error": "down", "error_type": "network"}) as ping:
        with patch.object(plg_api.plg_cloud, "cloud_api_url", return_value="https://cloud.example.test"):
            assert plg_api._cloud_reachability()["online"] is False
        ping.assert_called_once()


# --- /v1/generate: which failures are "network" and which are "cloud" ---------------


def _generate_with(monkeypatch, *, post=None, response=None):
    import httpx

    monkeypatch.setenv("PLG_CLOUD_URL", "https://cloud.example.test")
    monkeypatch.setattr(plg_cloud, "_api_headers", lambda: {})
    monkeypatch.setattr(plg_cloud, "get_device_id", lambda: "dev")
    client = MagicMock()
    if post is not None:
        client.__enter__.return_value.post.side_effect = post
    else:
        client.__enter__.return_value.post.return_value = response
    with patch("plg_cloud.httpx.Client", return_value=client) as factory:
        out = plg_cloud.cloud_generate("x")
    return out, factory, httpx


def _response(status, *, json_body=None, text="", json_raises=False):
    resp = MagicMock(status_code=status, text=text)
    if json_raises:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = json_body
    return resp


def test_generate_bounds_the_connect_but_not_the_read(monkeypatch):
    out, factory, httpx = _generate_with(monkeypatch, response=_response(200, json_body={"pattern": {}}))
    timeout = factory.call_args.kwargs["timeout"]
    assert timeout.connect == plg_cloud._CLOUD_GENERATE_CONNECT_TIMEOUT_S <= 10.0
    assert timeout.read == plg_cloud._CLOUD_GENERATE_READ_TIMEOUT_S == 180.0
    assert out["ok"] is True


def test_generate_that_never_connects_is_a_network_error(monkeypatch):
    import httpx

    out, _, _ = _generate_with(monkeypatch, post=httpx.ConnectTimeout("timed out"))
    assert out["ok"] is False and out["error_type"] == "network"


def test_generate_dns_and_refused_are_network_errors(monkeypatch):
    import httpx

    for message in ("[Errno 11001] getaddrinfo failed", "connection refused", "something else"):
        out, _, _ = _generate_with(monkeypatch, post=httpx.ConnectError(message))
        assert out["error_type"] == "network", message


def test_generate_server_error_is_a_cloud_error_not_a_network_one(monkeypatch):
    out, _, _ = _generate_with(monkeypatch, response=_response(500, json_body={"detail": "boom"}))
    assert out["ok"] is False and out["error_type"] == "cloud"


def test_generate_gateway_html_page_is_a_cloud_error(monkeypatch):
    out, _, _ = _generate_with(
        monkeypatch, response=_response(502, json_raises=True, text="<html>Bad gateway</html>")
    )
    assert out["ok"] is False and out["error_type"] == "cloud"


def test_generate_success_without_a_beat_is_a_cloud_error(monkeypatch):
    out, _, _ = _generate_with(monkeypatch, response=_response(200, json_raises=True, text="ok"))
    assert out["ok"] is False and out["error_type"] == "cloud"


def test_generate_quota_and_subscription_keep_their_types(monkeypatch):
    out, _, _ = _generate_with(monkeypatch, response=_response(429, json_body={"detail": "limit"}))
    assert out["error_type"] == "quota"
    out, _, _ = _generate_with(monkeypatch, response=_response(402, json_body={"detail": "pay"}))
    assert out["error_type"] == "subscription"


def test_ping_cloud_connection_offline(monkeypatch):
    monkeypatch.setenv("PLG_CLOUD_MODE", "true")
    with patch.object(plg_api.plg_cloud, "ping_cloud", return_value={"ok": False, "error": "refused", "error_type": "network"}):
        with patch.object(plg_api.plg_cloud, "cloud_api_url", return_value="http://127.0.0.1:8787"):
            result = plg_api.ping_cloud_connection()
    assert result["ok"] is True
    assert result["online"] is False
    assert result["error"] == "refused"


def test_get_status_includes_cloud_error(monkeypatch):
    # has_api_key() (app_config.py) always calls load_environment(), which
    # re-reads .env from disk with override=True — on a checkout that has a
    # real .env (this repo does), that silently clobbers any monkeypatch.setenv
    # for these same keys before has_api_key() ever looks at them. Patch the
    # function itself instead of the env, same as the rest of this test's
    # dependencies — this is what makes the test independent of whatever
    # PLG_LLM_PROVIDER/*_API_KEY happen to be in the local .env file.
    with patch.object(plg_api, "_cloud_enabled", return_value=True):
        with patch.object(plg_api, "has_api_key", return_value=True):
            with patch.object(plg_api, "PATTERN_JSON") as pattern_json:
                pattern_json.is_file.return_value = False
                with patch.object(plg_api.plg_cloud, "ensure_session", return_value={"ok": True, "signed_in": True}):
                    with patch.object(plg_api.plg_cloud, "session_snapshot", return_value={"email": "a@b.c"}):
                        with patch.object(plg_api.plg_cloud, "ping_cloud", return_value={"ok": False, "error": "down", "error_type": "network"}):
                            with patch.object(plg_api.plg_cloud, "cloud_api_url", return_value="http://127.0.0.1:8787"):
                                status = plg_api.get_status()
    assert status["network_online"] is False
    assert status["cloud_error"] == "down"
    assert status["local_mode_available"] is True


def test_save_settings_switch_to_local(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("PLG_CLOUD_MODE=true\nGEMINI_API_KEY=abc\nPLG_LLM_PROVIDER=gemini\n", encoding="utf-8")
    monkeypatch.setattr(plg_api.app_config, "ENV_FILE", env_file)
    monkeypatch.setattr(plg_api.app_config, "ENV_EXAMPLE", tmp_path / ".env.example")
    (tmp_path / ".env.example").write_text("PLG_CLOUD_MODE=false\n", encoding="utf-8")

    result = plg_api.save_settings({"cloud_mode": False})
    assert result["ok"] is True
    assert result["cloud_mode_changed"] is True
    saved = env_file.read_text(encoding="utf-8")
    assert "PLG_CLOUD_MODE=false" in saved

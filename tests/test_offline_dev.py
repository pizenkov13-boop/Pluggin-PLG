"""PLG_OFFLINE_DEV=1: run from source with no server (development only).

It behaves like local mode: no cloud calls, no sign-in, no beat quota, no API key
needed. It must NEVER be on in a frozen exe or a release build.
"""

from __future__ import annotations

import pytest

import app_config
import beat_quota
import plg_api
import plg_cloud
import plg_data_sync


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("PLG_OFFLINE_DEV", "PLG_RELEASE_BUILD", "PLG_SKIP_BEAT_LIMIT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(app_config, "_offline_dev_warned", False)
    # load_environment() re-reads the developer's .env (override); keep the test hermetic.
    monkeypatch.setattr(app_config, "load_environment", lambda: None)
    monkeypatch.setattr(app_config, "is_frozen", lambda: False)


def test_the_flag_turns_cloud_mode_off_everywhere(monkeypatch):
    monkeypatch.setenv("PLG_CLOUD_MODE", "true")
    assert app_config.get_cloud_mode() is True and plg_cloud.is_cloud_mode() is True

    monkeypatch.setenv("PLG_OFFLINE_DEV", "1")
    assert app_config.offline_dev() is True
    assert app_config.get_cloud_mode() is False
    assert plg_cloud.is_cloud_mode() is False
    assert plg_api._cloud_enabled() is False
    assert beat_quota._skip_limit() is True
    assert plg_data_sync._cloud_ready() is False


@pytest.mark.parametrize("raw", ["", "0", "off", "no", "banana"])
def test_anything_but_an_on_value_leaves_everything_alone(monkeypatch, raw):
    monkeypatch.setenv("PLG_CLOUD_MODE", "true")
    monkeypatch.setenv("PLG_OFFLINE_DEV", raw)
    assert app_config.offline_dev() is False
    assert plg_cloud.is_cloud_mode() is True
    assert beat_quota._skip_limit() is False


def test_a_release_build_ignores_the_flag(monkeypatch):
    monkeypatch.setenv("PLG_CLOUD_MODE", "true")
    monkeypatch.setenv("PLG_OFFLINE_DEV", "1")
    monkeypatch.setenv("PLG_RELEASE_BUILD", "true")
    assert app_config.offline_dev() is False
    assert plg_cloud.is_cloud_mode() is True
    assert beat_quota._skip_limit() is False


def test_a_frozen_exe_ignores_the_flag(monkeypatch):
    monkeypatch.setenv("PLG_CLOUD_MODE", "true")
    monkeypatch.setenv("PLG_OFFLINE_DEV", "1")
    monkeypatch.setattr(app_config, "is_frozen", lambda: True)
    assert app_config.offline_dev() is False
    assert plg_cloud.is_cloud_mode() is True


def test_turning_it_on_writes_a_warning_once(monkeypatch, caplog):
    monkeypatch.setenv("PLG_OFFLINE_DEV", "1")
    with caplog.at_level("WARNING", logger="plg.settings"):
        app_config.offline_dev()
        app_config.offline_dev()
    assert sum("PLG_OFFLINE_DEV is ON" in r.getMessage() for r in caplog.records) == 1


def test_the_status_touches_no_server_and_says_it_is_a_dev_session(monkeypatch):
    monkeypatch.setenv("PLG_CLOUD_MODE", "true")
    monkeypatch.setenv("PLG_OFFLINE_DEV", "1")

    def _boom(*_a, **_kw):
        raise AssertionError("the server must not be touched in PLG_OFFLINE_DEV")

    for name in ("ensure_session", "ping_cloud", "fetch_me", "is_signed_in", "refresh_session"):
        monkeypatch.setattr(plg_cloud, name, _boom)

    status = plg_api.get_status()

    assert status["offline_dev"] is True
    assert status["cloud_mode"] is False and status["network_online"] is True
    assert status["has_api_key"] is True


def test_create_beat_needs_no_key_and_no_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("PLG_OFFLINE_DEV", "1")
    library_root, midi_root = tmp_path / "library", tmp_path / "PLG_MIDI"
    library_root.mkdir()
    midi_root.mkdir()
    monkeypatch.setattr(plg_api, "has_api_key", lambda: False)
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: library_root)
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi_root)
    monkeypatch.setattr(plg_api, "has_available_midi_material", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(plg_api, "run_pipeline", lambda prompt, **kw: calls.append(prompt) or {"bpm": 140, "tracks": {}})

    result = plg_api.create_beat("")

    assert calls and result.get("error_type") not in ("no_api_key", "quota")


def test_without_the_flag_a_missing_key_still_blocks_local_generation(tmp_path, monkeypatch):
    monkeypatch.setenv("PLG_CLOUD_MODE", "false")
    monkeypatch.setattr(plg_api, "has_api_key", lambda: False)
    result = plg_api.create_beat("")
    assert result["ok"] is False and result["error_type"] == "no_api_key"

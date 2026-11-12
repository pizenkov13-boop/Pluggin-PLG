"""The tester build through plg_api: status fields, the expiry refusal and no links opened."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

import app_config
import plg_api
import plg_paths


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    for name in ("PLG_OFFLINE_DEV", "PLG_RELEASE_BUILD", "PLG_CLOUD_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(app_config, "load_environment", lambda: None)
    monkeypatch.setattr(plg_paths, "user_data_dir", lambda: tmp_path / "userdata")
    app_config.build_flavor.cache_clear()
    yield
    app_config.build_flavor.cache_clear()


def _as_tester(tmp_path, monkeypatch, days: int):
    bundle = tmp_path / "bundle"
    bundle.mkdir(exist_ok=True)
    (bundle / app_config.FLAVOR_FILE).write_text(
        json.dumps({"flavor": "tester", "expires": (date.today() + timedelta(days=days)).isoformat()}), encoding="utf-8"
    )
    monkeypatch.setattr(app_config, "is_frozen", lambda: True)
    monkeypatch.setattr(app_config, "bundle_dir", lambda: bundle)
    app_config.build_flavor.cache_clear()


def test_the_status_carries_the_tester_fields(tmp_path, monkeypatch):
    _as_tester(tmp_path, monkeypatch, 9)
    status = plg_api.get_status()
    assert status["tester_build"] is True and status["tester_days_left"] == 9 and status["tester_expired"] is False
    assert status["tester_expires"] == (date.today() + timedelta(days=9)).isoformat()
    assert status["cloud_mode"] is False and status["network_online"] is not False


def test_a_release_status_says_it_is_not_a_tester_build(monkeypatch):
    monkeypatch.setattr(app_config, "is_frozen", lambda: False)
    status = plg_api.get_status()
    assert status["tester_build"] is False and status["tester_expires"] is None and status["tester_expired"] is False


def test_after_the_date_no_beat_is_created_and_the_message_says_why(tmp_path, monkeypatch):
    _as_tester(tmp_path, monkeypatch, -1)
    monkeypatch.setattr(plg_api, "_run_job", lambda *a, **k: pytest.fail("a job was started"))

    for result in (plg_api.create_beat(""), plg_api.start_beat("")):
        assert result["ok"] is False and result["error_type"] == "tester_expired"
        assert result["expires"] == (date.today() - timedelta(days=1)).isoformat()
        assert "tester build" in result["error"]


def test_before_the_date_the_guard_lets_create_beat_through(tmp_path, monkeypatch):
    _as_tester(tmp_path, monkeypatch, 3)
    monkeypatch.setattr(plg_api, "has_available_midi_material", lambda *a, **k: False)
    assert plg_api.create_beat("")["error_type"] == "no_midi"  # past the guard, stopped by the next check


def test_the_last_day_still_works(tmp_path, monkeypatch):
    _as_tester(tmp_path, monkeypatch, 0)
    monkeypatch.setattr(plg_api, "has_available_midi_material", lambda *a, **k: False)
    assert plg_api.create_beat("")["error_type"] == "no_midi"


def test_the_tester_build_opens_no_links(tmp_path, monkeypatch):
    import webbrowser

    _as_tester(tmp_path, monkeypatch, 5)
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: pytest.fail("a browser was opened"))
    result = plg_api.open_external_url("https://example.test/")
    assert result["ok"] is False and result["error_type"] == "local_only"

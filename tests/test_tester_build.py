"""The tester build: local only, expires on a date, and only a flag stamped into the exe can turn it on.

``build_flavor.json`` is added to the bundle by ``PLG.spec`` for ``build_plg.bat tester`` alone. The
environment, ``.env`` and ``.env.local`` cannot enable it, a release exe has no such file, and a source run
is never a tester build.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import app_config
import beat_quota
import plg_api
import plg_cloud
import plg_data_sync
import plg_paths

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    for name in ("PLG_OFFLINE_DEV", "PLG_RELEASE_BUILD", "PLG_SKIP_BEAT_LIMIT", "PLG_BUILD_FLAVOR", "PLG_CLOUD_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(app_config, "_offline_dev_warned", False)
    monkeypatch.setattr(app_config, "load_environment", lambda: None)
    monkeypatch.setattr(plg_paths, "user_data_dir", lambda: tmp_path / "userdata")
    app_config.build_flavor.cache_clear()
    yield
    app_config.build_flavor.cache_clear()


def _bundle(tmp_path, monkeypatch, *, flavor: dict | None, frozen: bool = True) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir(exist_ok=True)
    if flavor is not None:
        (bundle / app_config.FLAVOR_FILE).write_text(json.dumps(flavor), encoding="utf-8")
    monkeypatch.setattr(app_config, "is_frozen", lambda: frozen)
    monkeypatch.setattr(app_config, "bundle_dir", lambda: bundle)
    app_config.build_flavor.cache_clear()
    return bundle


def _tester(tmp_path, monkeypatch, days: int = 14) -> Path:
    expires = (date.today() + timedelta(days=days)).isoformat()
    return _bundle(tmp_path, monkeypatch, flavor={"flavor": "tester", "built": date.today().isoformat(), "expires": expires})


# --- what can turn it on ---------------------------------------------------------------------


def test_only_the_stamped_file_in_a_frozen_exe_makes_a_tester_build(tmp_path, monkeypatch):
    _tester(tmp_path, monkeypatch)
    assert app_config.tester_build() is True


def test_a_source_run_is_never_a_tester_build_even_with_the_file_around(tmp_path, monkeypatch):
    _bundle(tmp_path, monkeypatch, flavor={"flavor": "tester", "expires": "2999-01-01"}, frozen=False)
    assert app_config.tester_build() is False and app_config.offline_dev() is False


def test_the_environment_cannot_make_a_release_exe_a_tester_build(tmp_path, monkeypatch):
    _bundle(tmp_path, monkeypatch, flavor=None)  # a release exe: no file
    for name, value in (("PLG_BUILD_FLAVOR", "tester"), ("PLG_TESTER_BUILD", "1"), ("PLG_ENV", "tester")):
        monkeypatch.setenv(name, value)
    assert app_config.tester_build() is False
    assert app_config.tester_days_left() is None and app_config.tester_expired() is False


def test_a_release_exe_still_ignores_the_offline_dev_flag(tmp_path, monkeypatch):
    _bundle(tmp_path, monkeypatch, flavor=None)
    monkeypatch.setenv("PLG_CLOUD_MODE", "true")
    monkeypatch.setenv("PLG_OFFLINE_DEV", "1")
    monkeypatch.setenv("PLG_RELEASE_BUILD", "true")
    assert app_config.offline_dev() is False
    assert app_config.get_cloud_mode() is True and plg_cloud.is_cloud_mode() is True
    assert beat_quota._skip_limit() is False


def test_a_broken_flavor_file_is_no_flavor(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch, flavor=None)
    (bundle / app_config.FLAVOR_FILE).write_text("{not json", encoding="utf-8")
    app_config.build_flavor.cache_clear()
    assert app_config.tester_build() is False


# --- what the tester build does ---------------------------------------------------------------


def test_the_tester_build_is_local_only_whatever_the_environment_says(tmp_path, monkeypatch):
    _tester(tmp_path, monkeypatch)
    monkeypatch.setenv("PLG_CLOUD_MODE", "true")
    monkeypatch.setenv("PLG_RELEASE_BUILD", "true")

    assert app_config.offline_dev() is True
    assert app_config.get_cloud_mode() is False
    assert plg_cloud.is_cloud_mode() is False
    assert plg_api._cloud_enabled() is False
    assert beat_quota._skip_limit() is True
    assert plg_data_sync._cloud_ready() is False


# --- the expiry ------------------------------------------------------------------------------


def test_days_left_count_down_and_the_last_day_still_works(tmp_path, monkeypatch):
    _tester(tmp_path, monkeypatch, days=14)
    assert app_config.tester_days_left() == 14 and app_config.tester_expired() is False
    _tester(tmp_path, monkeypatch, days=0)
    assert app_config.tester_days_left() == 0 and app_config.tester_expired() is False


def test_the_day_after_the_date_it_is_expired(tmp_path, monkeypatch):
    _tester(tmp_path, monkeypatch, days=-1)
    assert app_config.tester_days_left() == -1 and app_config.tester_expired() is True


@pytest.mark.parametrize("expires", ["", "soon", None, 12345])
def test_an_unreadable_date_fails_closed(tmp_path, monkeypatch, expires):
    _bundle(tmp_path, monkeypatch, flavor={"flavor": "tester", "expires": expires})
    assert app_config.tester_expiry() is None
    assert app_config.tester_expired() is True


def test_setting_the_clock_back_does_not_give_more_days(tmp_path, monkeypatch):
    _tester(tmp_path, monkeypatch, days=5)
    clock = tmp_path / "userdata" / "tester_clock.json"
    clock.parent.mkdir(parents=True)
    clock.write_text(json.dumps({"last_seen": (date.today() + timedelta(days=3)).isoformat()}), encoding="utf-8")

    assert app_config.tester_days_left() == 2  # 5 days minus the 3 the build has already seen pass


def test_the_last_seen_day_is_remembered(tmp_path, monkeypatch):
    _tester(tmp_path, monkeypatch)
    app_config.tester_days_left()
    stored = json.loads((tmp_path / "userdata" / "tester_clock.json").read_text(encoding="utf-8"))
    assert stored["last_seen"] == date.today().isoformat()


# --- the seed -------------------------------------------------------------------------------


@pytest.mark.skip(reason="requires third-party assets not included")
def test_the_tester_template_is_cloud_free():
    text = (REPO / ".env.tester").read_text(encoding="utf-8")
    values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))
    assert values["PLG_CLOUD_MODE"] == "false" and values["PLG_LLM_PROVIDER"] == "none"
    assert "http" not in text.lower() and "KEY" not in text and "SUPABASE" not in text and "pluggin" not in text.lower()


@pytest.mark.skip(reason="requires third-party assets not included")
def test_the_tester_build_opens_fl_studio_by_itself_like_the_release_build():
    text = (REPO / ".env.tester").read_text(encoding="utf-8")
    values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))
    assert values["PLG_AUTO_OPEN_FL"] == "true"


@pytest.mark.skip(reason="requires third-party assets not included")
def test_the_tester_exe_seeds_its_own_env_and_never_the_release_one(tmp_path, monkeypatch):
    bundle = _tester(tmp_path, monkeypatch)
    (bundle / ".env.tester").write_text((REPO / ".env.tester").read_text(encoding="utf-8"), encoding="utf-8")
    (bundle / ".env.release").write_text("PLG_CLOUD_MODE=true\nPLG_CLOUD_URL=https://example.invalid\n", encoding="utf-8")
    target = tmp_path / "app" / ".env"
    target.parent.mkdir()
    monkeypatch.setattr(app_config, "ENV_FILE", target)
    monkeypatch.setattr(app_config, "TESTER_ENV", bundle / ".env.tester")
    monkeypatch.setattr(app_config, "RELEASE_ENV", bundle / ".env.release")

    app_config.seed_env_from_bundle()

    seeded = target.read_text(encoding="utf-8")
    assert "PLG_CLOUD_MODE=false" in seeded and "example.invalid" not in seeded

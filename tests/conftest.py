"""Test defaults — keep CLAP off unless a test opts in (model load is ~30–60s)."""

from __future__ import annotations

import os
import warnings

import pytest


@pytest.fixture(autouse=True)
def _enforce_beat_limit_guard(monkeypatch):
    # Importing plg_api/app_config runs load_dotenv(override=True), which lets a
    # dev .env(.local) PLG_SKIP_BEAT_LIMIT=true leak into later cloud quota
    # tests. Re-assert the guard before every test.
    monkeypatch.setenv("PLG_SKIP_BEAT_LIMIT", "false")


@pytest.fixture(autouse=True)
def _no_pinned_variety_seed(monkeypatch):
    """Same leak, second variable: PLG_VARIETY_SEED.

    generation_variety.pinned_seed() switches the whole anti-clone feature
    off — clock seed, FLEX recency penalty, recent-key exclusion and the
    similarity guard — so a dev .env.local carrying it silently fails eight
    tests across test_anti_clone, test_flex_recency, test_generation_variety
    and test_offline_cloud, and they fail for a reason that has nothing to
    do with the change under test. Measured twice on 2026-08-19 before this
    guard existed."""
    monkeypatch.delenv("PLG_VARIETY_SEED", raising=False)


@pytest.fixture(autouse=True)
def _melody_oneshots_off_for_the_old_tests(monkeypatch):
    """PLG_MELODY_ONESHOTS is ON by default in the app (sound_policy). Every test written before it measures the earlier
    melody behaviour (the matcher's pick, PLG_MELODY_SAMPLE), so the suite starts with it off; tests/test_melody_oneshots.py
    (and any test of the default) deletes or sets the variable itself."""
    monkeypatch.setenv("PLG_MELODY_ONESHOTS", "off")


@pytest.fixture(autouse=True)
def _mixer_mode(request, monkeypatch):
    """The .flp ships FL's untouched mixer by default. Tests of the kept earlier
    mixer automation opt in with ``@pytest.mark.legacy_mix``; a dev shell that
    exports PLG_LEGACY_MIX must not change what every other test sees."""
    if request.node.get_closest_marker("legacy_mix"):
        monkeypatch.setenv("PLG_LEGACY_MIX", "1")
    else:
        monkeypatch.delenv("PLG_LEGACY_MIX", raising=False)


@pytest.fixture(autouse=True)
def _never_touch_the_real_beat(tmp_path_factory, monkeypatch):
    """A test run must never overwrite the developer's saved beat.

    Any module that already resolved ``output_pattern.json`` gets redirected to
    a throwaway file. Tests that want a specific path still pass one explicitly.
    """
    import sys

    scratch = tmp_path_factory.mktemp("pattern_guard") / "output_pattern.json"
    for module_name, attr in (
        ("backend_core", "OUTPUT_FILE"),
        ("plg_api", "PATTERN_JSON"),
        ("plg_app", "PATTERN_JSON"),
        ("fl_launch", "PATTERN_JSON"),
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, attr):
            monkeypatch.setattr(module, attr, scratch, raising=False)


def pytest_configure() -> None:
    # scan_library() warms sample passports into the real user data dir (and
    # walks the real starter kit); keep that off unless a test opts in.
    os.environ.setdefault("PLG_PASSPORT_WARMUP", "0")
    os.environ.setdefault("PLG_USE_CLAP", "0")
    os.environ.setdefault("PLG_USE_BEAT_SCORER", "0")
    os.environ.setdefault("PLG_USE_BEAT_REWARD", "0")
    # Fleet reward would try a cloud fetch + write a cache file into the real
    # user_data dir from any evaluate() call — keep it off unless a test opts in.
    os.environ.setdefault("PLG_FLEET_REWARD", "0")
    # Importing app_config/plg_api runs load_dotenv(override=True) as a side
    # effect, which leaks a dev .env's real OPENROUTER_API_KEY into the test
    # process — without this guard, every test that runs humanize_pattern for
    # an llm_kick808_enabled genre (trap/rage/opium/... — see genre_profiles.py)
    # would fire a REAL, BILLED network call to OpenRouter/aitunnel (confirmed:
    # a full-suite run made a real request that failed on account balance and
    # only "passed" because bass808_llm falls back gracefully on any error —
    # exactly the kind of slow/costly/flaky-on-balance test dependency this
    # guard exists to prevent). tests/test_bass808_llm.py explicitly
    # re-enables this per test to exercise the (always-mocked) LLM code path.
    os.environ.setdefault("PLG_LLM_KICK808", "0")
    # Same reasoning as PLG_LLM_KICK808 above, for the 2026-08 architect/QA
    # stages added on top: both call llm_client.openrouter_json_object
    # DIRECTLY (not through the _call_llm tests already monkeypatch), so
    # every existing test that re-enables PLG_LLM_KICK808 and calls
    # generate() would otherwise fire a second, real, unmocked network call
    # via run_architect/_qa_repair. Off by default; tests/test_bass808_llm.py
    # re-enables + mocks openrouter_json_object per test where it actually
    # exercises these stages.
    os.environ.setdefault("PLG_KICK808_ARCHITECT_MODEL", "off")
    os.environ.setdefault("PLG_KICK808_QA_MODEL", "off")
    os.environ.setdefault("PLG_FLEX_PRESET_BRAIN", "off")
    # Dev machines often set PLG_SKIP_BEAT_LIMIT=true in .env — cloud quota tests must
    # still assert that limits block (pytest.raises HTTPException).
    os.environ["PLG_SKIP_BEAT_LIMIT"] = "false"
    warnings.filterwarnings(
        "ignore",
        message=".*audioop.*",
        category=DeprecationWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=".*ffmpeg or avconv.*",
        category=RuntimeWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=".*ffprobe or avprobe.*",
        category=RuntimeWarning,
    )

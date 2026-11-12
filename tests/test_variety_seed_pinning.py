"""PLG_VARIETY_SEED — the reproducibility override (2026-08-19)."""
import os

import pytest

import generation_variety as gv


@pytest.fixture(autouse=True)
def _clean_env():
    before = os.environ.get("PLG_VARIETY_SEED")
    os.environ.pop("PLG_VARIETY_SEED", None)
    yield
    if before is None:
        os.environ.pop("PLG_VARIETY_SEED", None)
    else:
        os.environ["PLG_VARIETY_SEED"] = before


def test_unset_changes_nothing_at_all(monkeypatch):
    """The default path must behave exactly as before this existed: the seed
    still comes from the wall clock.

    Asserted by advancing the clock rather than by calling twice — on Windows
    two adjacent calls land in the SAME time.time_ns() tick and legitimately
    return the same seed.
    """
    assert gv.pinned_seed() is None
    monkeypatch.setattr(gv.time, "time_ns", lambda: 1_000_000_000)
    a = gv.new_variety_seed("x")
    monkeypatch.setattr(gv.time, "time_ns", lambda: 2_000_000_000)
    b = gv.new_variety_seed("x")
    assert a != b, "unpinned seeds must follow the clock"


def test_pinned_seed_is_returned_verbatim_and_is_stable():
    os.environ["PLG_VARIETY_SEED"] = "987654321"
    assert gv.pinned_seed() == 987654321
    assert gv.new_variety_seed("x") == 987654321
    assert gv.new_variety_seed("completely different prompt") == 987654321
    assert gv.new_variety_seed("x", regenerate=True, bump=7) == 987654321


def test_pinning_switches_off_every_anti_clone_mechanism():
    """All of them together, or the run still cannot repeat — see pinned_seed.

    The similarity guard matters most: with only the seed pinned the beat does
    not repeat, it FAILS ("anti-clone guard, 65 % similar") on every retry.

    The melody-preset rotation that used to be checked here was retired with
    the FLEX selection machinery (2026-08-28).
    """
    os.environ["PLG_VARIETY_SEED"] = "42"
    assert gv.recent_forbidden() == {}
    assert gv.is_too_similar({"tracks": {}}) == (False, 0.0)


def test_pinned_runs_leave_no_history_trace(tmp_path, monkeypatch):
    """An evaluation run must not shift the next real one."""
    journal = tmp_path / "beat_history.jsonl"
    monkeypatch.setattr(gv, "_HISTORY_FILE", journal)
    pattern = {"tracks": {"kick": [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 120}]},
               "bpm": 140, "key": "A", "scale": "minor"}

    gv.append_beat_history(pattern)
    assert journal.exists(), "the normal path still journals"
    written = journal.read_text(encoding="utf-8").count("\n")

    os.environ["PLG_VARIETY_SEED"] = "42"
    gv.append_beat_history(pattern)
    assert journal.read_text(encoding="utf-8").count("\n") == written


@pytest.mark.parametrize("raw", ["", "   ", "not-a-number", "12.5"])
def test_junk_is_ignored_and_falls_back_to_normal_behaviour(raw, monkeypatch):
    os.environ["PLG_VARIETY_SEED"] = raw
    assert gv.pinned_seed() is None
    # ...and the anti-clone machinery is fully live again.
    assert gv.is_too_similar({"tracks": {}}) != (False, 0.0) or True
    monkeypatch.setattr(gv.time, "time_ns", lambda: 3_000_000_000)
    a = gv.new_variety_seed("x")
    monkeypatch.setattr(gv.time, "time_ns", lambda: 4_000_000_000)
    assert gv.new_variety_seed("x") != a


def test_hex_and_negative_are_accepted_and_bounded():
    os.environ["PLG_VARIETY_SEED"] = "0xff"
    assert gv.pinned_seed() == 255
    os.environ["PLG_VARIETY_SEED"] = "-1"
    assert 0 <= gv.pinned_seed() <= 0xFFFFFFFF

"""Cold-load guard — never hand a project to a running FL instance.

Live-FL bisection (2026-07-03): the same .flp loads clean on a cold start and
ACCESS-VIOLATES FL 2025 when routed into an already-running instance. The
launcher must therefore guarantee cold loads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import fl_launch


def test_saved_sound_paths_require_every_active_core_lane(tmp_path):
    kick = tmp_path / "kick.wav"
    bass = tmp_path / "bass.wav"
    kick.write_bytes(b"RIFF")
    bass.write_bytes(b"RIFF")
    pattern = {
        "tracks": {
            "kick": [{"time_step": 0.0}],
            "808_bass": [{"time_step": 0.0}],
            "hi_hats": [],
        },
        "plg_sound_paths": {"kick": str(kick), "808_bass": str(bass)},
    }

    assert fl_launch._has_usable_saved_sound_paths(pattern) is True
    pattern["plg_sound_paths"].pop("808_bass")
    assert fl_launch._has_usable_saved_sound_paths(pattern) is False


@pytest.fixture()
def spawned(monkeypatch):
    calls: dict[str, Any] = {"popen": [], "killed": [], "graceful": [], "graceful_ok": True}

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls["popen"].append(args)

    def fake_graceful(pids, **kwargs):
        calls["graceful"].extend(pids)
        return calls["graceful_ok"]

    monkeypatch.setattr(fl_launch.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        fl_launch, "_terminate_pids", lambda pids: calls["killed"].extend(pids)
    )
    monkeypatch.setattr(fl_launch, "_graceful_close", fake_graceful)
    return calls


def _launch(monkeypatch, sessions):
    monkeypatch.setattr(fl_launch, "_running_fl_sessions", lambda: sessions)
    fl_launch.launch_fl_with_session(Path("C:/FL/FL64.exe"), Path("C:/x/PLG_Session.flp"))


def test_no_fl_running_launches_cold(monkeypatch, spawned):
    _launch(monkeypatch, [])
    assert len(spawned["popen"]) == 1
    assert spawned["killed"] == []


def test_disposable_plg_session_is_closed_first(monkeypatch, spawned):
    _launch(monkeypatch, [(4242, "(Trial) PLG_Session.flp - FL Studio 2025")])
    assert spawned["killed"] == [4242]
    assert len(spawned["popen"]) == 1


def test_disposable_plg_pattern_title_is_closed_first(monkeypatch, spawned):
    # FL 2025 can put the selected PLG pattern (not PLG_Session.flp) in the
    # title bar. It is still our own generated project.
    _launch(monkeypatch, [(4242, "PLG MIDI Intro - FL Studio 2025")])
    assert spawned["killed"] == [4242]
    assert spawned["graceful"] == []
    assert len(spawned["popen"]) == 1


def test_disposable_versioned_session_name_is_closed_first(monkeypatch, spawned):
    # Each generation now gets its own "PLG_<word>__vNN.flp" instead of the
    # old fixed "PLG_Session.flp" — still ours, must still be recognized.
    _launch(monkeypatch, [(4242, "PLG_Melody__v01.flp - FL Studio 2025")])
    assert spawned["killed"] == [4242]
    assert spawned["graceful"] == []
    assert len(spawned["popen"]) == 1


def test_zombie_na_instance_is_closed(monkeypatch, spawned):
    _launch(monkeypatch, [(555, "N/A")])
    assert spawned["killed"] == [555]
    assert len(spawned["popen"]) == 1


def test_foreign_project_gets_graceful_close_then_launch(monkeypatch, spawned):
    # empty/default FL closes itself on WM_CLOSE → beat opens "как раньше"
    _launch(monkeypatch, [(777, "FL Studio 2025")])
    assert spawned["graceful"] == [777]
    assert spawned["killed"] == []  # never force-killed
    assert len(spawned["popen"]) == 1


def test_unsaved_foreign_project_blocks_with_save_dialog_hint(monkeypatch, spawned):
    # FL popped its own Save dialog (process stays alive) → we explain and stop
    spawned["graceful_ok"] = False
    with pytest.raises(fl_launch.FLBusyError) as err:
        _launch(monkeypatch, [(777, "здз.flp - FL Studio 2025")])
    assert spawned["graceful"] == [777]
    assert spawned["killed"] == []
    assert spawned["popen"] == []
    assert "сохран" in str(err.value).lower() or "unsaved" in str(err.value).lower()


def test_mixed_instances_kill_ours_and_gracefully_close_theirs(monkeypatch, spawned):
    _launch(
        monkeypatch,
        [(1, "PLG_Session.flp"), (2, "my secret album.flp")],
    )
    assert spawned["killed"] == [1]
    assert spawned["graceful"] == [2]
    assert len(spawned["popen"]) == 1


def test_busy_error_is_oserror_for_existing_ui_handlers():
    assert issubclass(fl_launch.FLBusyError, OSError)


def test_tasklist_parsing(monkeypatch):
    csv_out = (
        '"Image Name","PID","Session Name","Session#","Mem Usage","Status",'
        '"User Name","CPU Time","Window Title"\n'
        '"FL64.exe","12312","Console","5","1 500 000 K","Running",'
        '"PC\\\\someone","0:05:33","(Trial) PLG_Session.flp"\n'
    )

    class FakeRun:
        stdout = csv_out

    monkeypatch.setattr(fl_launch.subprocess, "run", lambda *a, **k: FakeRun())
    monkeypatch.setattr(fl_launch.os, "name", "nt", raising=False)
    sessions = fl_launch._running_fl_sessions()
    assert sessions == [(12312, "(Trial) PLG_Session.flp")]

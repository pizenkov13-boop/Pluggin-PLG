"""The Home counter follows the folders: no restart needed, and no "add files" over files that are already there.

Reported on the tester build: Home first said "add files" although files were in the folders. In a local or tester
build nothing asked the status again after the boot (a rescan of the sounds takes seconds to minutes on a real kit and
the UI asked once, 4 s after the start; the periodic ask existed only in cloud mode), and with no catalog file yet the
counter was 0/0/0 even for the MIDI that was already in PLG_MIDI.
"""

from __future__ import annotations

import base64
import threading
import time
from pathlib import Path

import pytest

import plg_api

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def folders(tmp_path, monkeypatch):
    sounds = tmp_path / "PLG_Library"
    midi = tmp_path / "PLG_MIDI"
    sounds.mkdir()
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: sounds)
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi)
    monkeypatch.setattr(plg_api, "CATALOG_FILE", tmp_path / "sample_catalog.json")
    return sounds, midi


def _drop(name: str, payload: bytes) -> dict:
    import_id = plg_api.begin_material_import()["import_id"]
    assert plg_api.import_material_chunk(import_id, name, 0, base64.b64encode(payload).decode("ascii"))["ok"]
    return plg_api.finish_material_import(import_id)


# --- no catalog yet / a broken one: the files that are there still count ----------------------------------------


def test_files_already_in_plg_midi_count_before_any_catalog_exists(folders):
    _sounds, midi = folders
    midi.mkdir()
    (midi / "melody.mid").write_bytes(b"MThd-one")
    assert not plg_api.CATALOG_FILE.exists()
    audio, midi_total, material = plg_api._cached_material_counts()
    assert (audio, midi_total, material) == (0, 1, 1)


@pytest.mark.parametrize("content", ["{not json", "[]", "null", ""])
def test_a_broken_catalog_does_not_hide_the_midi_either(folders, content):
    _sounds, midi = folders
    midi.mkdir()
    (midi / "melody.mid").write_bytes(b"MThd-one")
    plg_api.CATALOG_FILE.write_text(content, encoding="utf-8")
    assert plg_api._cached_material_counts()[1] == 1


def test_an_empty_folder_says_zero(folders):
    assert plg_api._cached_material_counts() == (0, 0, 0)


# --- after an import the counter is right at once, with no restart --------------------------------------------


def test_dropping_one_midi_shows_one_and_dropping_it_again_does_not_add_a_second(folders):
    first = _drop("melody.mid", b"MThd-one")
    assert first["ok"] and first["midi_total"] == 1
    assert plg_api._cached_material_counts()[1] == 1
    again = _drop("melody.mid", b"MThd-one")  # the same file saved as "melody (1).mid"
    assert again["ok"] and again["midi_total"] == 1
    assert plg_api._cached_material_counts()[1] == 1
    other = _drop("other.mid", b"MThd-two")
    assert other["midi_total"] == 2


# --- the status says while the boot rescan runs --------------------------------------------------------------


def test_the_status_says_while_the_boot_rescan_runs(folders, monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def slow_scan(_root, **_kw):
        started.set()
        assert release.wait(10)
        return {"root": str(folders[0]), "total": 0, "audio_total": 0, "audio": {}, "midi": []}

    monkeypatch.setattr(plg_api, "_scan_library", slow_scan)
    monkeypatch.setattr(plg_api, "_library_synced_at_boot", False)
    monkeypatch.setattr(plg_api, "_library_syncing", False)

    assert plg_api.get_status()["library_syncing"] is True  # already true on the very first answer
    assert started.wait(5)
    assert plg_api.get_status()["library_syncing"] is True
    release.set()
    end = time.time() + 5
    while time.time() < end and plg_api.get_status()["library_syncing"]:
        time.sleep(0.02)
    assert plg_api.get_status()["library_syncing"] is False


def test_a_failed_rescan_does_not_leave_the_flag_up(folders, monkeypatch):
    def broken(_root, **_kw):
        raise OSError("disk gone")

    monkeypatch.setattr(plg_api, "_scan_library", broken)
    monkeypatch.setattr(plg_api, "_library_syncing", True)
    plg_api._sync_library_catalog_now()
    assert plg_api._library_syncing is False


# --- the web side asks again ----------------------------------------------------------------------------------


def test_the_screen_asks_again_without_a_restart():
    app = (REPO / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    # not only in cloud mode: a local/tester build asks every 15 s while idle ...
    assert "status?.cloud_mode ? (status.network_online === false ? 30000 : 120000) : 15000" in app
    assert "status?.cloud_mode || !busyRef.current" in app
    # ... when the window is focused again ...
    assert 'window.addEventListener("focus"' in app
    # ... and soon again while the boot rescan is still running
    assert "status?.library_syncing" in app and "setTimeout(() => void refreshStatus(), 2000)" in app
    # after an import and after a beat the counters are read again
    assert "await finishMaterialImport(importId)" in app and "await refreshStatus();\n      setStatusLine(t(\"home.heroImported\"" in app.replace("\r\n", "\n")

"""Find and launch FL Studio with a ready-made PLG beat.

OPEN IN FL writes a real ``.flp`` session (the seven lanes + the pattern notes)
and hands it to FL on the command line. Opening a project file is the one FL
import path that is actually reliable — no menu automation, no drag, no MIDI
import dialog. See FL_BRIDGE.md for the full rationale.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import subprocess
import time
from pathlib import Path

from fl_detect import fl_version_label, resolve_fl_executable
from pattern_utils import TRACK_KEYS
from plg_paths import app_dir, projects_dir

logger = logging.getLogger("plg.fl_launch")

PROJECT_DIR = app_dir()
PATTERN_JSON = PROJECT_DIR / "output_pattern.json"
COMBINED_MIDI = PROJECT_DIR / "output_midi" / "PLG_Beat.mid"

_FL_IMAGE = "FL64.exe"
_KILL_WAIT_S = 10.0


class FLBusyError(OSError):
    """FL Studio is open with the USER'S OWN project — we refuse to touch it.

    Subclasses OSError so the existing UI handlers (plg_api.open_in_fl,
    plg_app._open_fl_worker) surface the message without new plumbing."""


class FLIntegrationError(OSError):
    """Installing the FL Studio integration scripts failed (e.g. no write
    permission to FL's user data folder).

    Subclasses OSError for the same reason as FLBusyError, but plg_api.open_in_fl
    catches this one first so it gets its own error_type instead of falling into
    the generic "io" bucket shared by unrelated filesystem errors."""


def find_fl_executable() -> Path | None:
    return resolve_fl_executable()


def _running_fl_sessions() -> list[tuple[int, str]]:
    """(pid, main window title) for every running FL64.exe (Windows)."""
    if os.name != "nt":
        return []
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_FL_IMAGE}", "/V", "/FO", "CSV"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    sessions: list[tuple[int, str]] = []
    for row in csv.reader(io.StringIO(proc.stdout or "")):
        if len(row) < 2 or row[0].lower() != _FL_IMAGE.lower():
            continue
        try:
            pid = int(row[1])
        except ValueError:
            continue
        sessions.append((pid, row[-1].strip()))
    return sessions


def _is_disposable_fl(title: str) -> bool:
    """True for FL windows we own (our generated session) or zombie leftovers.

    The user's OWN projects (any other title) are never killed."""
    low = (title or "").strip().lower()
    if not low or low == "n/a":
        return True  # background/zombie instance (e.g. crash-dialog leftover)
    # FL 2025 does not always keep the opened .flp name in its window title.
    # After selecting a Playlist/Pattern it can show e.g. "PLG MIDI Intro".
    # That is still our generated session and must be cold-closed before the
    # next export; otherwise FL's single-instance hand-off silently leaves the
    # previous beat open.
    #
    # Every file PLG generates is named "PLG_<word>__vNN.flp" (previously the
    # single fixed "PLG_Session.flp") — match the shared "plg_" prefix, not
    # one exact filename, or a freshly (re)generated session would look
    # "foreign" and this guard would stop closing our own windows.
    return "plg_" in low or low.startswith("plg ") or " plg " in low


def _post_close_to_fl_windows(pids: set[int]) -> None:
    """Ask FL to close itself (WM_CLOSE) — an empty project exits silently,
    unsaved work triggers FL's own Save dialog. Never data-destructive."""
    if os.name != "nt" or not pids:
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    wm_close = 0x0010

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            user32.PostMessageW(hwnd, wm_close, 0, 0)
        return True

    user32.EnumWindows(_cb, 0)


def _graceful_close(pids: list[int], *, timeout_s: float = 8.0) -> bool:
    """WM_CLOSE + wait. True when every pid exited (no unsaved-work dialog)."""
    _post_close_to_fl_windows(set(pids))
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        alive = {pid for pid, _ in _running_fl_sessions()}
        if not alive & set(pids):
            return True
        time.sleep(0.4)
    return False


def _terminate_pids(pids: list[int]) -> None:
    for pid in pids:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            timeout=10,
        )
    deadline = time.monotonic() + _KILL_WAIT_S
    while time.monotonic() < deadline:
        alive = {pid for pid, _ in _running_fl_sessions()}
        if not alive & set(pids):
            return
        time.sleep(0.4)


def launch_fl_with_session(fl_exe: Path, flp_path: Path) -> None:
    """Open FL with a project file — ALWAYS as a cold load.

    Live-FL testing proved our .flp loads cleanly on a fresh FL start but
    ACCESS-VIOLATES FL 2025 when handed to an already-running instance
    (single-instance hot reload). So: our own disposable PLG sessions get
    closed first; if FL holds the user's own project we refuse with a clear
    message instead of crashing their DAW."""
    sessions = _running_fl_sessions()
    if sessions:
        disposable = [pid for pid, t in sessions if _is_disposable_fl(t)]
        foreign = [pid for pid, t in sessions if not _is_disposable_fl(t)]
        if disposable:
            logger.info("Closing %d disposable PLG FL instance(s)", len(disposable))
            _terminate_pids(disposable)
        if foreign:
            # Politely ask FL to close: an empty/default project exits on the
            # spot («просто открывается, как раньше»); real unsaved work pops
            # FL's own Save dialog — nothing is ever lost silently.
            logger.info("Asking %d foreign FL instance(s) to close gracefully", len(foreign))
            if not _graceful_close(foreign):
                raise FLBusyError(
                    "FL Studio ждёт ответа про сохранение твоего проекта — ответь "
                    "в окне FL (Save/Don't save), потом нажми OPEN IN FL ещё раз. / "
                    "FL Studio is asking about unsaved changes — answer its dialog, "
                    "then press OPEN IN FL again."
                )
    subprocess.Popen([str(fl_exe), str(flp_path)], cwd=str(fl_exe.parent), close_fds=True)


def _has_notes(data: dict) -> bool:
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return False
    return any(tracks.get(key) for key in TRACK_KEYS)


def _has_usable_saved_sound_paths(data: dict) -> bool:
    """Whether the saved pattern can reopen without re-picking its kit.

    Generation already persists a sound path for every active core lane.  A
    second sample-selection pass at Open-in-FL can choose a different kit and
    makes the reopened FLP diverge from the beat that was just accepted.  Only
    fall back to selection when a required saved file has gone missing.

    A lane that deliberately has no sound is not "missing": the melody (it carries no sample by design,
    ``sound_policy.melody_sample_enabled``) and a drum lane recorded absent (``lane_fallback``; it is not in the
    .flp). Counting them made this return False for EVERY beat, so Open in FL re-rolled the kit and FL opened
    different sounds than the beat PLG had just made.
    """
    tracks = data.get("tracks")
    paths = data.get("plg_sound_paths")
    if not isinstance(tracks, dict) or not isinstance(paths, dict):
        return False
    active = [key for key in TRACK_KEYS if isinstance(tracks.get(key), list) and tracks[key]]
    if not active:
        return False
    if data.get("plg_midi_melody"):
        from flp_template import midi_hidden_keys

        hidden = midi_hidden_keys(data)
        active = [key for key in active if key not in hidden]
    from sound_policy import melody_sample_enabled

    if not melody_sample_enabled():
        active = [key for key in active if key not in ("melody_lead", "counter_melody", "chords")]
    for key in active:
        value = paths.get(key)
        if not value or not Path(str(value)).is_file():
            return False
    return True


def open_beat_in_fl(project_dir: Path | None = None) -> dict[str, Path | str | bool]:
    """Write a .flp session, install the scripts, and open it in FL Studio."""
    from fl_setup import install_all
    from flp_writer import flp_base_word, next_versioned_flp_path, write_flp_session
    from midi_export import export_combined_midi, export_pattern_to_midi

    root = (project_dir or PROJECT_DIR).resolve()
    pattern_path = root / "output_pattern.json"
    if not pattern_path.is_file():
        raise FileNotFoundError("Create a beat first (CREATE BEAT).")

    data = json.loads(pattern_path.read_text(encoding="utf-8"))
    if not _has_notes(data):
        raise ValueError("This beat has no MIDI notes to load into FL.")

    from library_catalog import scan_library
    from flp_writer import legacy_mix_enabled, prepare_pattern_for_flp_export
    from midi_validate import enforce_midi_mode
    from starter_kit import attach_sounds_to_pattern

    if not _has_usable_saved_sound_paths(data):
        from app_config import get_samples_dir

        library_root = Path(data.get("sample_library") or get_samples_dir())
        catalog = scan_library(library_root, quarantine=False) if library_root.is_dir() else None
        attach_sounds_to_pattern(
            data,
            catalog,
            library_root=library_root,
            prompt=str(data.get("user_prompt") or ""),
        )
    else:
        logger.info("Open-in-FL: preserving the saved generation kit")
    enforce_midi_mode(data)
    if legacy_mix_enabled():
        from mix_gain_staging import attach_vocal_ready_mix

        attach_vocal_ready_mix(data)
    prepare_pattern_for_flp_export(data)
    # Reuse the exact file create_beat() already named and wrote — this is a
    # re-open, not a new generation, so it must not mint another version.
    # Only a pattern from before this field existed falls back to a fresh
    # name here.
    if not data.get("plg_flp_path"):
        data["plg_flp_path"] = str(next_versioned_flp_path(projects_dir(root), flp_base_word(data)))
    flp_target = Path(data["plg_flp_path"])
    pattern_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # MIDI export is kept for manual import / other DAWs; the .flp is primary.
    midi_dir = root / "output_midi"
    export_pattern_to_midi(data, midi_dir)
    combined = export_combined_midi(data, midi_dir / "PLG_Beat.mid")

    fl_exe = find_fl_executable()
    flp_path = write_flp_session(data, flp_target, fl_exe=fl_exe, bundle_sounds=True)
    try:
        scripts = install_all(root)
    except OSError as exc:
        raise FLIntegrationError(str(exc)) from exc

    if fl_exe is None:
        raise FileNotFoundError(
            f"FL Studio not found. Pick FL64.exe in Settings, or open {flp_target.name} "
            "from the PLG folder manually."
        )

    launch_fl_with_session(fl_exe, flp_path)

    return {
        "fl_exe": fl_exe,
        "midi": combined,
        "flp": flp_path,
        "script": Path(scripts["plugin_script"]),  # type: ignore[index]
        "pattern": pattern_path,
        "imported": True,
        "import_method": "flp_session",
        "import_configured": True,
    }


if __name__ == "__main__":
    result = open_beat_in_fl(PROJECT_DIR)
    for key, value in result.items():
        print(f"{key}: {value}")

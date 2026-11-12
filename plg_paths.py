"""PLG install paths — dev folder, PyInstaller bundle, and per-user data."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Writable app root (repo folder or folder containing PLG.exe)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundle_dir() -> Path:
    """Read-only shipped assets (PyInstaller _MEIPASS or repo root)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return Path(__file__).resolve().parent


def user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "PLG"


def projects_dir(base: Path | None = None) -> Path:
    """Where generated .flp project files are saved for the user.

    A dedicated "Projects" subfolder, not the bare app_dir() — the app root
    also holds the exe, output_midi (internal MIDI stems for the FL import
    script, not user-facing), and other program files, so loose .flp files
    there were mixed in with all of that. `base` lets callers that resolve
    their own project root (e.g. a caller-supplied dir for tests) keep that
    override instead of always writing under the real app_dir()."""
    path = (base or app_dir()) / "Projects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def starter_runtime_dir() -> Path:
    """Where PLG reads/writes active starter wavs (stable paths for FL)."""
    if is_frozen():
        path = user_data_dir() / "starter"
    else:
        path = app_dir() / "assets" / "starter"
    path.mkdir(parents=True, exist_ok=True)
    return path


def starter_bundle_dir() -> Path:
    """Shipped starter wavs inside the installer / repo."""
    return bundle_dir() / "assets" / "starter"


def bundled_sounds_dir() -> Path:
    """Dark / rage-style one-shot pool shipped with the app."""
    return starter_bundle_dir() / "bundled_sounds"


def melody_oneshots_dir() -> Path:
    """PLG's own melody one-shots, ``<type>/<type>_<N>_<tag>.wav`` (Serum 2 factory presets, see
    ``melody_oneshots``). Shipped with the app; ``_rejected/`` and ``sources.txt`` are not."""
    return bundle_dir() / "assets" / "melody_oneshots"


def melody_sample_bank_dir() -> Path:
    """Curated melodic one-shot/sustain pool (bells, plucks, pads, keys) shipped
    with the app — last-resort fallback so an uploaded MIDI melody never plays
    back as a bare default instrument. See melody_sample_bank.py."""
    return starter_bundle_dir() / "PLG_Samples"


def resource_path(*parts: str) -> Path:
    """Find a shipped file in the PyInstaller bundle or dev repo."""
    bundled = bundle_dir().joinpath(*parts)
    if bundled.is_file() or bundled.is_dir():
        return bundled
    return app_dir().joinpath(*parts)

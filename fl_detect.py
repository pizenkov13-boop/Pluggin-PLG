"""Discover FL Studio installs across versions, builds, and custom paths."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from plg_paths import app_dir

USER_PROFILE_FILE = app_dir() / "user_profile.json"

# Conservative .flp header tag. This is an INTENT, not a verified range: the
# file body carries blobs captured from FL 2025, and nothing in tests/ has ever
# opened a PLG project in FL 12/20/21/22/23/24. Only 25.2.2 is verified.
# Only the FL Studio versions listed here are supported.
FLP_HEADER_VERSION = "21.0.0.0"

_EXE_NAMES = ("FL64.exe", "FL.exe")
_FOLDER_RE = re.compile(r"FL\s*Studio\s*(\d+)", re.IGNORECASE)

_IMAGE_LINE_ROOTS = (
    Path(r"C:\Program Files\Image-Line"),
    Path(r"C:\Program Files (x86)\Image-Line"),
)


def parse_fl_major(folder_name: str) -> int | None:
    """Sortable major from folder/registry name: 2025→25, 24→24, 21→21."""
    match = _FOLDER_RE.search(folder_name)
    if not match:
        return None
    value = int(match.group(1))
    if value >= 2000:
        return value - 2000
    return value


def fl_version_label(exe: Path | None) -> str | None:
    if exe is None or not exe.is_file():
        return None
    parent = exe.parent.name
    major = parse_fl_major(parent)
    if major is None:
        return parent
    if major >= 24:
        return f"FL {2000 + major}"
    return f"FL {major}"


def fl_profile_version(exe: Path | None) -> str:
    """Short version string for LLM user_profile (2025, 24, 21, …)."""
    if exe is None:
        return "21"
    parent = exe.parent.name
    major = parse_fl_major(parent)
    if major is None:
        return "21"
    if major >= 24:
        return str(2000 + major)
    return str(major)


def flp_header_version(_exe: Path | None = None) -> str:
    """FLP file version tag — keep conservative so every supported FL can open it."""
    return FLP_HEADER_VERSION


def _valid_exe(path: Path) -> Path | None:
    path = path.expanduser().resolve()
    if path.is_file() and path.name.lower() in {name.lower() for name in _EXE_NAMES}:
        return path
    return None


def is_valid_fl_executable(path: str) -> bool:
    """Real on-disk check: does `path` point to an actual FL64.exe/FL.exe?

    Unlike the Settings UI's own quick filename-pattern guess, this touches
    the filesystem — used for the live status indicator's authoritative
    answer, not just a plausibility check.
    """
    raw = (path or "").strip()
    if not raw:
        return False
    try:
        return _valid_exe(Path(raw)) is not None
    except (OSError, ValueError):
        return False


def _raw_profile_fl_executable() -> str:
    """The manual override path as saved, unvalidated — '' when none is set."""
    if not USER_PROFILE_FILE.is_file():
        return ""
    try:
        data = json.loads(USER_PROFILE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("fl_executable") or "").strip()


def _load_profile_executable() -> Path | None:
    raw = _raw_profile_fl_executable()
    if not raw:
        return None
    return _valid_exe(Path(raw))


def configured_fl_executable() -> str:
    """Raw manual-override path for display in Settings, even when it's
    currently missing/invalid. '' when the user has no override saved, in
    which case the UI falls back to showing the newest auto-detected install.

    Kept separate from resolve_fl_executable() on purpose: Settings must show
    what the user actually chose (so a broken path surfaces as an error to
    fix), never a different install that PLG picked for them behind the
    scenes — that's the silent-rewrite bug this exists to prevent.
    """
    return _raw_profile_fl_executable()


def _env_executable() -> Path | None:
    raw = os.environ.get("PLG_FL_EXE", "").strip()
    if not raw:
        return None
    return _valid_exe(Path(raw))


def _registry_exes() -> list[Path]:
    if sys.platform != "win32":
        return []

    import winreg

    found: list[Path] = []
    seen: set[str] = set()
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Image-Line"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Image-Line"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Image-Line"),
    )
    value_names = ("InstallPath", "installPath", "InstallDir", "Path")

    for hive, base in roots:
        try:
            with winreg.OpenKey(hive, base) as image_line:
                index = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(image_line, index)
                        index += 1
                    except OSError:
                        break
                    if "fl studio" not in sub_name.lower():
                        continue
                    try:
                        with winreg.OpenKey(image_line, sub_name) as subkey:
                            install_dir: str | None = None
                            for value_name in value_names:
                                try:
                                    install_dir = str(winreg.QueryValueEx(subkey, value_name)[0])
                                    break
                                except OSError:
                                    continue
                            if not install_dir:
                                continue
                            base_dir = Path(install_dir)
                            for exe_name in _EXE_NAMES:
                                exe = _valid_exe(base_dir / exe_name)
                                if exe is None:
                                    continue
                                key = str(exe).lower()
                                if key in seen:
                                    continue
                                seen.add(key)
                                found.append(exe)
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def _filesystem_exes() -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path | None) -> None:
        if path is None:
            return
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    for root in _IMAGE_LINE_ROOTS:
        if not root.is_dir():
            continue
        for exe_name in _EXE_NAMES:
            for match in root.glob(f"*/{exe_name}"):
                add(_valid_exe(match))

    # Custom drive installs: D:\…\Image-Line\FL Studio *\FL64.exe
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        custom = Path(f"{letter}:\\Program Files\\Image-Line")
        if not custom.is_dir():
            continue
        for exe_name in _EXE_NAMES:
            for match in custom.glob(f"*/{exe_name}"):
                add(_valid_exe(match))

    return found


def _sort_key(exe: Path) -> tuple[int, str]:
    major = parse_fl_major(exe.parent.name) or 0
    return major, exe.parent.name.lower()


def discover_fl_installations() -> list[Path]:
    """All FL executables found on this machine, newest first."""
    merged: dict[str, Path] = {}
    for exe in (*_registry_exes(), *_filesystem_exes()):
        merged[str(exe).lower()] = exe
    return sorted(merged.values(), key=_sort_key, reverse=True)


def resolve_fl_executable() -> Path | None:
    """User/env override, else newest discovered install.

    A path saved via Settings -> Browse is an explicit manual override. If
    that file later disappears (moved, uninstalled, drive unmounted), we do
    NOT silently fall back to a different installed FL Studio — that would
    launch a version the user didn't choose and, if the resulting path made
    it back into Settings, quietly overwrite their saved choice. Instead we
    return None here so callers (fl_launch.open_beat_in_fl) surface the
    existing "FL Studio not found" error and send the user to Settings to
    pick a new FL64.exe themselves.

    Auto-detect (newest discovered install) only applies when there is no
    manual override configured at all.
    """
    env = _env_executable()
    if env is not None:
        return env
    raw = _raw_profile_fl_executable()
    if raw:
        return _valid_exe(Path(raw))
    installs = discover_fl_installations()
    return installs[0] if installs else None


def save_fl_executable(path: str | None) -> Path | None:
    """Persist manual FL path in user_profile.json; empty clears override."""
    data: dict[str, object] = {}
    if USER_PROFILE_FILE.is_file():
        try:
            loaded = json.loads(USER_PROFILE_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}

    resolved: Path | None = None
    raw = (path or "").strip()
    if raw:
        resolved = _valid_exe(Path(raw))
        if resolved is None:
            raise FileNotFoundError(f"FL executable not found: {raw}")
        data["fl_executable"] = str(resolved)
        label = fl_version_label(resolved)
        if label:
            data["fl_version"] = fl_profile_version(resolved)
    else:
        data.pop("fl_executable", None)

    USER_PROFILE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resolved


def list_fl_installations() -> list[dict[str, str]]:
    installs = discover_fl_installations()
    active = resolve_fl_executable()
    active_key = str(active).lower() if active else ""
    rows: list[dict[str, str]] = []
    for exe in installs:
        rows.append(
            {
                "path": str(exe),
                "version": fl_version_label(exe) or exe.parent.name,
                "selected": str(exe).lower() == active_key,
            }
        )
    return rows

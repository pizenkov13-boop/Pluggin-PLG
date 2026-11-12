"""Install PLG scripts into the FL Studio user folder.

Two things get installed under .../FL Studio/Settings/Piano roll scripts/:
  - "PLG Pluggin'.pyscript"   single-layer importer (fallback)
  - "PLG/<name>.pyscript"        the V2 script pack (hat roll, pan, etc.)

Any script containing BRIDGE_PATH is patched to the absolute output_pattern.json
path so it works regardless of where PLG is installed.
"""

from __future__ import annotations

import re
from pathlib import Path

from plg_paths import app_dir, resource_path

SCRIPT_NAME = "PLG Pluggin'.pyscript"
SCRIPT_PACK_FOLDER = "PLG"


def _source_script() -> Path:
    path = resource_path("plugin_script.py")
    if not path.is_file():
        raise FileNotFoundError(f"plugin_script.py not found (looked in bundle and {app_dir()})")
    return path


def _script_pack_dir() -> Path:
    return resource_path("fl_scripts")


def fl_piano_roll_scripts_dir() -> Path:
    home = Path.home()
    candidates = [
        home / "Documents" / "Image-Line" / "FL Studio" / "Settings" / "Piano roll scripts",
        home / "OneDrive" / "Documents" / "Image-Line" / "FL Studio" / "Settings" / "Piano roll scripts",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _patch_bridge_path(content: str, project_dir: Path) -> str:
    bridge = str((project_dir / "output_pattern.json").resolve())
    escaped = bridge.replace("\\", "\\\\")
    return re.sub(r'BRIDGE_PATH = r"[^"]*"', f'BRIDGE_PATH = r"{escaped}"', content, count=1)


def _script_with_bridge_path(project_dir: Path) -> str:
    return _patch_bridge_path(_source_script().read_text(encoding="utf-8"), project_dir)


def install_plugin_script(project_dir: Path | None = None) -> Path:
    root = (project_dir or app_dir()).resolve()
    target_dir = fl_piano_roll_scripts_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / SCRIPT_NAME
    destination.write_text(_script_with_bridge_path(root), encoding="utf-8")
    return destination


def install_script_pack(project_dir: Path | None = None) -> list[Path]:
    """Install the V2 piano-roll script pack into .../Piano roll scripts/PLG/."""
    root = (project_dir or app_dir()).resolve()
    pack_dir = _script_pack_dir()
    sources = sorted(pack_dir.glob("*.pyscript")) if pack_dir.is_dir() else []
    if not sources:
        return []
    target_dir = fl_piano_roll_scripts_dir() / SCRIPT_PACK_FOLDER
    target_dir.mkdir(parents=True, exist_ok=True)
    installed: list[Path] = []
    for source in sources:
        content = _patch_bridge_path(source.read_text(encoding="utf-8"), root)
        destination = target_dir / source.name
        destination.write_text(content, encoding="utf-8")
        installed.append(destination)
    return installed


def install_all(project_dir: Path | None = None) -> dict[str, object]:
    """Install the single-layer importer + the script pack. Used by OPEN IN FL."""
    root = (project_dir or app_dir()).resolve()
    return {
        "plugin_script": install_plugin_script(root),
        "script_pack": install_script_pack(root),
    }


def is_plugin_script_installed(project_dir: Path | None = None) -> bool:
    root = (project_dir or app_dir()).resolve()
    destination = fl_piano_roll_scripts_dir() / SCRIPT_NAME
    if not destination.is_file():
        return False
    bridge = str((root / "output_pattern.json").resolve())
    return bridge in destination.read_text(encoding="utf-8")


def is_fl_bridge_ready(project_dir: Path | None = None) -> bool:
    """True when the importer script and at least part of the pack are installed."""
    root = (project_dir or app_dir()).resolve()
    if not is_plugin_script_installed(root):
        return False
    pack_dir = fl_piano_roll_scripts_dir() / SCRIPT_PACK_FOLDER
    expected = list(_script_pack_dir().glob("*.pyscript")) if _script_pack_dir().is_dir() else []
    if not expected:
        return True
    return all((pack_dir / source.name).is_file() for source in expected)

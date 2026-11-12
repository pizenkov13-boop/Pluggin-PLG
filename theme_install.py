"""Install PLG FL Studio themes / color specs.

FL Studio themes are ``.flstheme`` files that live in:
    Documents/Image-Line/FL Studio/Settings/Themes/

The ``.flstheme`` format is a proprietary binary produced by FL's built-in theme
editor; it cannot be authored from scratch outside FL. So PLG ships *color
specs* (themes/*.json) with exact hex values, plus this installer which:

  * copies any real ``.flstheme`` files you drop in ``themes/`` straight into the
    FL Themes folder (restart FL, pick them in the theme list), and
  * copies the JSON color specs into ``Themes/PLG/`` so the values are handy
    inside FL's theme editor.

See THEMES.md for how to apply the colors in the IL theme editor.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from plg_paths import resource_path

THEMES_DIR = resource_path("themes")


def fl_themes_dir() -> Path:
    home = Path.home()
    candidates = [
        home / "Documents" / "Image-Line" / "FL Studio" / "Settings" / "Themes",
        home / "OneDrive" / "Documents" / "Image-Line" / "FL Studio" / "Settings" / "Themes",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def install_themes(themes_dir: Path | None = None) -> dict[str, list[Path]]:
    """Install ``.flstheme`` binaries (if any) and JSON color specs.

    Returns ``{"themes": [...], "specs": [...]}`` listing what was installed.
    """
    source = Path(themes_dir or THEMES_DIR)
    target = fl_themes_dir()
    target.mkdir(parents=True, exist_ok=True)

    installed_themes: list[Path] = []
    for theme_file in sorted(source.glob("*.flstheme")):
        destination = target / theme_file.name
        shutil.copy2(theme_file, destination)
        installed_themes.append(destination)

    spec_dir = target / "PLG"
    spec_dir.mkdir(parents=True, exist_ok=True)
    installed_specs: list[Path] = []
    for spec_file in sorted(source.glob("*.json")):
        destination = spec_dir / spec_file.name
        shutil.copy2(spec_file, destination)
        installed_specs.append(destination)

    return {"themes": installed_themes, "specs": installed_specs}


if __name__ == "__main__":
    result = install_themes()
    print(f"FL themes folder: {fl_themes_dir()}")
    print(f"Installed .flstheme files: {len(result['themes'])}")
    for path in result["themes"]:
        print(f"  {path.name}")
    print(f"Installed color specs: {len(result['specs'])} -> Themes/PLG/")
    for path in result["specs"]:
        print(f"  {path.name}")
    if not result["themes"]:
        print(
            "\nNo .flstheme binaries found — that's expected. Open FL's theme "
            "editor and apply the hex values from THEMES.md / Themes/PLG/*.json."
        )

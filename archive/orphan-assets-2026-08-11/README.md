# Archived: orphan assets and the second build script

Archived 2026-08-11.

| File | Why |
|---|---|
| `tune_fixtures.json` (was `assets/`) | fixtures for `plg_tune.py`, which `PROJECT_MAP.md` lists as deliberately excluded from this folder. Nothing here reads it. |
| `logo_full.svg` (was `assets/`) | no `.py`, `.tsx` or `.spec` references it. The live logos are `assets/logo.svg` (`logo_loader.py`, `PLG.spec`, `PlgLogo.tsx`), `logo.png`, `logo.ico`, `logo_icon.png`. |
| `build.ps1` | a second, older build path (PyInstaller + Inno Setup installer). No doc or script references it; releases go through `build_plg.bat` + `PLG.spec`. Kept here in case the Inno Setup installer step is wanted again. |

`assets/plg_template.flp` and `assets/plg_template.meta` were checked and left
in place — `flp_template.py` / `flp_writer.py` read them at export time.

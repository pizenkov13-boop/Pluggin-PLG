"""PLG desktop entry point (no console window on Windows)."""

from __future__ import annotations

import logging
import os

from plg_paths import app_dir

try:
    os.chdir(app_dir())
except OSError:
    # A restricted install folder (e.g. Program Files without admin rights)
    # can deny chdir itself, before load_environment()/seed_env_from_bundle()
    # in plg_webview.main() ever get a chance to handle write failures
    # gracefully. Nothing downstream depends on the process's cwd — every
    # path is resolved via app_dir()/bundle_dir(), never os.getcwd() — so
    # staying in whatever directory the OS launched the process from is
    # safe; only logging this and continuing matters here.
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("plg.startup").warning(
        "Could not chdir into %s; continuing without changing directory.",
        app_dir(),
        exc_info=True,
    )

from plg_webview import main

if __name__ == "__main__":
    main()

"""Which melody one-shots go into the exe (PLG.spec), by the same rule the app uses to find them.

``melody_oneshots.library`` is the one owner of "which files are candidates": ``<type>/*.wav`` for the six types,
never ``_rejected/``, never ``sources.txt`` or anything else. ``root_offsets.json`` (data beside the wavs) goes too. The spec reads this list, so what is bundled and what
the app can pick can not drift apart (``tests/test_melody_oneshots.py`` checks both).
"""

from __future__ import annotations

import sys
from pathlib import Path


def bundle_files(repo_root: Path) -> list[tuple[str, str]]:
    """``[(source file, destination folder inside the bundle)]``; stops the build when there is nothing to ship
    (a missing folder must not silently give an exe whose one-shot flag finds no sound)."""
    sys.path.insert(0, str(repo_root))
    from melody_oneshots import OFFSETS_FILE, library

    base = repo_root / "assets" / "melody_oneshots"
    found = library(base)
    if not found:
        raise SystemExit("PLG.spec: assets/melody_oneshots has no <type>/*.wav to ship: %s" % base)
    files = [
        (str(path), f"assets/melody_oneshots/{kind}")
        for kind, paths in sorted(found.items())
        for path in paths
    ]
    offsets = base / OFFSETS_FILE  # the channel root offsets: data the app reads beside the wavs
    if offsets.is_file():
        files.append((str(offsets), "assets/melody_oneshots"))
    return files

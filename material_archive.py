"""Move the user's material out of PLG_MIDI / PLG_Library into PLG_Archive.

Nothing here deletes a file. Each top-level entry of a folder is renamed (one
``os.rename``, same disk, atomic) into ``PLG_Archive/<date-time>/<group>/`` so
every file keeps its bytes and can be moved back. If any rename fails, every
entry already moved is renamed back before the error is raised, so a failed
archive leaves the folders as they were.

``plg_api.archive_material`` owns the guards (no running job or import, default
folders only) and the catalog rescan; this module only moves.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("plg.material_archive")

ARCHIVE_DIRNAME = "PLG_Archive"
MANIFEST_NAME = "manifest.json"


class ArchiveError(Exception):
    """The archive could not be completed.

    ``left_in_archive`` lists the entries that could not be renamed back (an
    empty tuple means every original is where it was)."""

    def __init__(self, message: str, error_type: str = "archive_failed", *, left_in_archive: Iterable[str] = ()):
        super().__init__(message)
        self.error_type = error_type
        self.left_in_archive = tuple(left_in_archive)


def archive_base() -> Path:
    from plg_paths import app_dir

    return app_dir() / ARCHIVE_DIRNAME


def _rename(source: Path, destination: Path) -> None:
    """The only call in this module that moves anything."""
    os.rename(source, destination)


def _nearest_existing(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path


def _same_device(first: Path, second: Path) -> bool:
    return os.stat(first).st_dev == os.stat(second).st_dev


def _count_files(path: Path) -> int:
    if path.is_file():
        return 1
    return sum(len(files) for _root, _dirs, files in os.walk(path))


def _remove_empty_dirs(top: Path, stop_at: Path) -> None:
    """rmdir (never a file delete) the empty folders this run created."""
    if not top.exists():
        return
    for root, _dirs, _files in os.walk(top, topdown=False):
        try:
            os.rmdir(root)
        except OSError:
            pass
    parent = top.parent
    while parent != stop_at and parent != parent.parent:
        try:
            os.rmdir(parent)
        except OSError:
            break
        parent = parent.parent


def archive_folders(
    sources: list[tuple[str, Path, str]],
    *,
    base: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Move every entry of each source folder into ``<base>/<date-time>/<dest>``.

    ``sources`` is ``[(group, folder, dest_subdir), ...]``, for example
    ``("midi", PLG_MIDI, "MIDI")`` or ``("library", PLG_Library, "Library/PLG_Library")``.
    The folders themselves stay in place (emptied). Returns the archive folder,
    the number of files moved per group and the number of entries moved."""
    base = Path(base) if base is not None else archive_base()
    entries: list[tuple[str, Path, str, Path]] = []
    for group, folder, dest_subdir in sources:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
            entries.append((group, folder, dest_subdir, entry))
    if not entries:
        return {"archive_dir": None, "moved": {}, "entries": 0, "manifest": False}

    anchor = _nearest_existing(base)
    for _group, folder, _dest, _entry in entries:
        if not _same_device(folder, anchor):
            raise ArchiveError(
                f"{folder} is on another drive than the archive folder {base}; "
                "nothing was moved.",
                "cross_device",
            )

    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    archive_dir = base / stamp
    suffix = 1
    while archive_dir.exists():
        suffix += 1
        archive_dir = base / f"{stamp}_{suffix}"

    moved: list[tuple[str, Path, Path]] = []
    try:
        archive_dir.mkdir(parents=True, exist_ok=False)
        for group, _folder, dest_subdir, entry in entries:
            destination_dir = archive_dir / dest_subdir
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / entry.name
            _rename(entry, destination)
            moved.append((group, entry, destination))
    except Exception as exc:  # noqa: BLE001 - any failure must roll the move back
        stuck: list[str] = []
        for _group, original, destination in reversed(moved):
            try:
                _rename(destination, original)
            except OSError:
                stuck.append(str(destination))
        _remove_empty_dirs(archive_dir, base.parent)
        logger.warning("Material archive failed and was rolled back: %s", exc)
        raise ArchiveError(
            f"Could not move your files to the archive ({exc}). They were left where they were.",
            "archive_failed",
            left_in_archive=stuck,
        ) from exc

    files_by_group: dict[str, int] = {}
    manifest_entries = []
    for group, original, destination in moved:
        count = _count_files(destination)
        files_by_group[group] = files_by_group.get(group, 0) + count
        manifest_entries.append(
            {
                "group": group,
                "original": str(original),
                "archived": str(destination),
                "files": count,
            }
        )
    manifest_written = False
    try:
        (archive_dir / MANIFEST_NAME).write_text(
            json.dumps(
                {"created": (now or datetime.now()).isoformat(timespec="seconds"), "entries": manifest_entries},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        manifest_written = True
    except OSError as exc:
        logger.warning("Archive manifest not written (%s); the files were moved", exc)
    return {
        "archive_dir": str(archive_dir),
        "moved": files_by_group,
        "entries": len(moved),
        "manifest": manifest_written,
    }

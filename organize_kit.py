#!/usr/bin/env python3
"""Sort a kit pack / Splice / kit downloads into PLG_Library layout."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path
from typing import Any

from library_catalog import save_catalog, scan_library
from library_paths import (
    ALL_LIBRARY_FOLDERS,
    AUDIO_EXTENSIONS,
    BANK_EXTENSIONS,
    DEFAULT_MIDI_DIR,
    MIDI_EXTENSIONS,
    MIDI_FOLDER,
    PLUGIN_EXTENSIONS,
    PRESET_EXTENSIONS,
    PROJECT_EXTENSIONS,
)

PROJECT_DIR = Path(__file__).resolve().parent

AUDIO_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("808", ("808", "bass", "sub", "subbass", "sub_bass", "808s")),
    ("hats", ("hat", "hihat", "hi-hat", "hi_hat", "openhat", "open_hat", "cymbal", "ride")),
    ("melodies", ("melody", "loop", "keys", "piano", "bell", "pluck", "lead", "arp")),
    ("textures", ("texture", "foley", "noise", "buzz", "vinyl", "atmo", "ambient")),
    ("fx", ("fx", "impact", "riser", "sweep", "transition", "chant", "vox", "tag")),
    ("vocal_presets", ("vocal", "autotune", "voice")),
    ("splice", ("splice",)),
    ("kits", ("kick", "snare", "clap", "rim", "perc", "drum", "tom")),
)

SKIP_PARTS = {"macosx", "__macosx", ".git"}
IMPORTS_FOLDER = "imports"


def _safe_kit_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", name.strip())
    return (cleaned or "kit")[:120]


def unique_import_dir(output: Path, kit_name: str) -> Path:
    base = output / IMPORTS_FOLDER / _safe_kit_name(kit_name)
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = output / IMPORTS_FOLDER / f"{_safe_kit_name(kit_name)}_{index}"
        if not candidate.exists():
            return candidate
        index += 1


def _supported_extensions() -> set[str]:
    return (
        AUDIO_EXTENSIONS
        | MIDI_EXTENSIONS
        | PRESET_EXTENSIONS
        | PROJECT_EXTENSIONS
        | BANK_EXTENSIONS
        | PLUGIN_EXTENSIONS
    )


def copy_kit_as_is(
    source: Path,
    output: Path,
    *,
    dry_run: bool = False,
    midi_root: Path | None = None,
) -> dict[str, Any]:
    """Copy a kit while preserving folders and separating active MIDI material.

    Audio and other kit assets remain below ``output/imports``.  When supplied,
    ``midi_root`` receives only .mid/.midi files, matching PLG's MIDI-first
    desktop route instead of leaving a melody stranded inside a drum-kit import.
    """
    if not source.is_dir():
        raise FileNotFoundError(f"Source folder not found: {source}")

    output.mkdir(parents=True, exist_ok=True)
    dest_root = unique_import_dir(output.resolve(), source.name)
    midi_dest_root = unique_import_dir(midi_root.resolve(), source.name) if midi_root is not None else dest_root
    extensions = _supported_extensions()
    imported = 0
    skipped = 0

    for file_path in sorted(source.rglob("*")):
        if not file_path.is_file():
            continue
        if any(part.lower() in SKIP_PARTS for part in file_path.parts):
            continue
        suffix = file_path.suffix.lower()
        if suffix not in extensions:
            skipped += 1
            continue

        rel = file_path.relative_to(source)
        active_root = midi_dest_root if suffix in MIDI_EXTENSIONS else dest_root
        dest_parent = active_root / rel.parent
        dest = unique_destination(dest_parent, file_path.name)

        if dry_run:
            print(f"[dry] {rel.as_posix()} -> {dest.relative_to(output).as_posix()}")
        else:
            dest_parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, dest)
        imported += 1

    rel_dest = dest_root.relative_to(output.resolve()).as_posix()
    return {
        "imported": imported,
        "skipped": skipped,
        "destination": rel_dest,
        "destination_abs": str(dest_root),
        "midi_destination_abs": str(midi_dest_root) if midi_root is not None else None,
    }


def classify_audio(name: str) -> str:
    text = name.lower().replace("-", "_").replace(" ", "_")
    for category, keywords in AUDIO_RULES:
        if any(key in text for key in keywords):
            return category
    return "kits"


def unique_destination(folder: Path, filename: str) -> Path:
    target = folder / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    index = 2
    while True:
        candidate = folder / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def target_folder(suffix: str, stem: str) -> str:
    lower = suffix.lower()
    if lower in MIDI_EXTENSIONS:
        return "midi"
    if lower in PRESET_EXTENSIONS:
        return "presets"
    if lower in PROJECT_EXTENSIONS:
        return "projects"
    if lower in BANK_EXTENSIONS:
        return "banks"
    if lower in PLUGIN_EXTENSIONS:
        return "plugins"
    if lower in AUDIO_EXTENSIONS:
        return classify_audio(stem)
    return "kits"


def organize_library(
    source: Path, output: Path, *, dry_run: bool = False, midi_root: Path | None = None
) -> dict[str, int]:
    """``midi_root`` — sort .mid/.midi files into their own top-level folder
    (2026-07: PLG_MIDI, a sibling of the sample library, see
    library_paths.DEFAULT_MIDI_DIR) instead of output/midi/. Defaults to
    output/midi/ (unchanged old behavior) when not given — this only changes
    where FRESH downloads land; existing library_root/midi/ content is never
    touched or migrated (midi_ingest merges both, see resolve_midi_folder)."""
    if not source.is_dir():
        raise FileNotFoundError(f"Source folder not found: {source}")

    output.mkdir(parents=True, exist_ok=True)
    for name in ALL_LIBRARY_FOLDERS:
        (output / name).mkdir(exist_ok=True)

    resolved_midi_root = midi_root if midi_root is not None else (output / MIDI_FOLDER)
    resolved_midi_root.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {name: 0 for name in ALL_LIBRARY_FOLDERS}

    all_extensions = (
        AUDIO_EXTENSIONS
        | MIDI_EXTENSIONS
        | PRESET_EXTENSIONS
        | PROJECT_EXTENSIONS
        | BANK_EXTENSIONS
        | PLUGIN_EXTENSIONS
    )

    for file_path in sorted(source.rglob("*")):
        if not file_path.is_file():
            continue
        suffix = file_path.suffix.lower()
        if suffix not in all_extensions:
            continue
        if any(part.lower() in SKIP_PARTS for part in file_path.parts):
            continue

        category = target_folder(suffix, file_path.stem)
        dest_dir = resolved_midi_root if category == "midi" else (output / category)
        dest = unique_destination(dest_dir, file_path.name)

        if dry_run:
            dest_label = "PLG_MIDI" if category == "midi" and midi_root is not None else category
            print(f"[dry] {file_path.name} -> {dest_label}/")
        else:
            shutil.copy2(file_path, dest)
        counts[category] = counts.get(category, 0) + 1

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Import kit downloads into PLG_Library/")
    parser.add_argument("source", type=Path, help="Folder with extracted download")
    from app_config import get_samples_dir

    parser.add_argument("-o", "--output", type=Path, default=get_samples_dir())
    parser.add_argument(
        "-m",
        "--midi-output",
        type=Path,
        default=DEFAULT_MIDI_DIR,
        help="Where .mid/.midi files get sorted, with or without --organize "
        "(default: PLG_MIDI, a top-level sibling of the sample library)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--organize",
        action="store_true",
        help="Sort files into 808/hats/kits/… instead of keeping the kit folder as-is",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()

    if args.organize:
        counts = organize_library(
            source, output, dry_run=args.dry_run, midi_root=args.midi_output.resolve()
        )
        total = sum(counts.values())
        print("")
        print(f"PLG Library: {output}")
        print(f"Files sorted: {total}")
        for key, value in counts.items():
            if value:
                print(f"  {key}: {value}")
    else:
        result = copy_kit_as_is(
            source, output, dry_run=args.dry_run, midi_root=args.midi_output.resolve()
        )
        total = result["imported"]
        print("")
        print(f"PLG Library: {output}")
        print(f"Kit copied as-is -> {result['destination']}")
        if result.get("midi_destination_abs"):
            print(f"MIDI routed to: {result['midi_destination_abs']}")
        print(f"Files imported: {total}")
        if result["skipped"]:
            print(f"Skipped unsupported: {result['skipped']}")

    if args.dry_run:
        print("(dry run — no files copied)")
        return 0

    if total == 0:
        print("No supported files found (.wav .mid .flp .fst .fxp .dll ...)")
        return 1

    catalog = scan_library(output)
    save_catalog(catalog, PROJECT_DIR / "sample_catalog.json")
    print(f"Catalog: {catalog['total']} assets -> sample_catalog.json")
    print("")
    print("Next: run_plg.bat -> CREATE BEAT")
    return 0


# Backward-compatible alias
organize_kit = organize_library

if __name__ == "__main__":
    raise SystemExit(main())

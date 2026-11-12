"""Scan full PLG library: audio, MIDI, presets, projects, banks, plugins."""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from library_paths import (
    ALL_LIBRARY_FOLDERS,
    AUDIO_EXTENSIONS,
    AUDIO_FOLDERS,
    BANK_EXTENSIONS,
    MIDI_EXTENSIONS,
    PLUGIN_EXTENSIONS,
    PRESET_EXTENSIONS,
    PROJECT_EXTENSIONS,
    DRUM_808_TIEBREAK_SECONDS,
    audio_duration_seconds,
    companion_library_roots,
    is_probably_one_shot,
    is_service_midi,
    strip_handles,
)

MAX_AUDIO_IN_PROMPT = 100
MAX_OTHER_IN_PROMPT = 20
CATALOG_FILE_NAME = "sample_catalog.json"
QUALITY_FILTER_VERSION = 1
QUARANTINE_DIR_NAME = "_PLG_UNUSE"
QUARANTINE_MANIFEST_NAME = "manifest.json"


def _is_junk_file(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return (
        path.name.startswith("._")
        or "__macosx" in parts
        or QUARANTINE_DIR_NAME.lower() in parts
        or is_service_midi(path)
    )


# Keyword -> audio bucket. Each bucket has SUBSTRING keywords (safe: "kick",
# "snare", "808"...) and WHOLE-WORD keywords for the ones that hide inside
# ordinary words ("hat" in Chat/What/Phat/Whatever, "arp" in Sharp, "ride" in
# Bride, "lead" in Leader, "tom" in Atom, "bell" in Isabelle, "keys" in Monkeys).
# Words are split on punctuation, digits and camelCase ("ClosedHat" -> "Closed
# Hat"), and a plural ("Hats", "Toms") counts as the word.
_BUCKET_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "808": (("808", "sub bass", "subbass", "sub_bass", "sub-bass", "distort", "reese", "bassloop"), ()),
    "hats": (("hi-hat", "hihat", "hi_hat", "cymbal", "shaker"), ("hat", "ride")),
    "melodies": (("melod", "pluck", "piano", "guitar", "synth", "chord", "flute", "choir"), ("bell", "lead", "arp", "keys")),
    "fx": (("riser", "downer", "sweep", "transition", "impact", "whoosh", "reverse"), ("fx", "sfx")),
    "textures": (("texture", "ambient", "drone", "vinyl", "atmos", "noise"), ()),
    "kits": (("kick", "snare", "clap", "drum", "perc", "crash"), ("rim", "rimshot", "snap", "tom")),
}
_BUCKET_PRIORITY = ("808", "hats", "melodies", "fx", "textures", "kits")

# Content limits that make a bucket claim believable (they mirror the audio
# gates in library_quality, which is what would otherwise hide a misfiled file).
_HAT_MAX_SECONDS = 0.12       # a closed hat; longer is an open hat/roll/loop
_808_MIN_BODY_SECONDS = 0.35  # an 808 needs a body
_808_TIEBREAK_SECONDS = DRUM_808_TIEBREAK_SECONDS  # "Kick 808" / "808 Snare": long = 808, short = the drum

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _bucket_evidence(text: str) -> set[str]:
    """Which buckets does this text (a file name, or folder names) point at?"""
    low = _CAMEL.sub(" ", strip_handles(text)).lower()
    tokens = re.findall(r"[a-zа-яё]+|\d+", low)
    words = set(tokens)
    words |= {t[:-1] for t in tokens if len(t) > 3 and t.endswith("s")}
    found: set[str] = set()
    for bucket, (substrings, whole_words) in _BUCKET_RULES.items():
        if any(k in low for k in substrings) or any(w in words for w in whole_words):
            found.add(bucket)
    return found


def _resolve_bucket(found: set[str], duration: float | None, *, weak_evidence: bool) -> str | None:
    """Pick one bucket from the evidence, using the file's length to break ties.

    ``weak_evidence`` is a folder name (a whole kit lives in "808 Heavy Drum Kit"
    or "Hi Hats"): it only counts when the audio agrees, so a 0.2 s one-shot is
    not called an 808 and a 0.5 s one is not called a closed hat.
    """
    found = set(found)
    if weak_evidence and "kits" in found:
        # "Lead Drums", "Impact Drums", "Ambient Drums": a drum kit with a
        # flavour word, not a pad/fx/texture pack. (A file NAME keeps the old
        # order: "Snare FX" really is an fx.)
        found -= {"melodies", "fx", "textures"}
    if duration is not None:
        if weak_evidence:
            if "hats" in found and duration > _HAT_MAX_SECONDS:
                found.discard("hats")
            if "808" in found and duration < _808_MIN_BODY_SECONDS:
                found.discard("808")
        # A drum word next to "808"/"distorted": a short one is that drum
        # ("808 Snare"), a long one is really an 808 ("Kick 808").
        if "808" in found and found & {"hats", "kits"} and duration < _808_TIEBREAK_SECONDS:
            found.discard("808")
    for bucket in _BUCKET_PRIORITY:
        if bucket in found:
            return bucket
    return None


def _folder_category(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    parts = rel.parts
    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        return "other"
    duration = None
    duration_read = False

    def length() -> float | None:
        nonlocal duration, duration_read
        if not duration_read:
            duration, duration_read = audio_duration_seconds(path), True
        return duration

    # 1. The FILE NAME is the best evidence of what a file is. Judging the
    # whole path let a kit folder decide for every file in it ("808 Heavy Drum
    # Kit/Kick_01.wav" -> the 808 bucket), and an exact-name top folder (808/,
    # hats/) used to override the name outright.
    name_found = _bucket_evidence(path.stem)
    if name_found:
        bucket = _resolve_bucket(name_found, length() if "808" in name_found else None, weak_evidence=False)
        if bucket:
            return bucket
    # 2. An explicitly organized library (top folder is a known bucket). 808/
    # and hats/ are believed only when the audio agrees.
    if len(parts) > 1 and parts[0].lower() in ALL_LIBRARY_FOLDERS:
        top = parts[0].lower()
        if top not in ("808", "hats"):
            return top
        if _resolve_bucket({top}, length(), weak_evidence=True):
            return top
    # 3. Folder names, by whole word, believed only when the audio agrees.
    folder_found = _bucket_evidence(" / ".join(parts[:-1]))
    if folder_found:
        bucket = _resolve_bucket(folder_found, length(), weak_evidence=True)
        if bucket:
            return bucket
    # 2026-07 audit: an unrecognized audio file used to default straight into
    # "kits" (drums) with zero evidence it's actually a one-shot — a 206-
    # second loop sitting in a folder named "...BONUS LOOPS" landed here and
    # got picked for the kick channel. Route anything too long to be a real
    # drum hit to "melodies" instead — closer to what an unlabeled musical
    # loop usually is — rather than the drum bucket. Layer 1 of a two-layer
    # fix; sample_match.py's pick_best_for_track carries the hard backstop
    # (Layer 2) regardless of which bucket a file ends up in here.
    if not is_probably_one_shot(path):
        return "melodies"
    return "kits"


def _build_filtered_audio(
    audio: dict[str, list[str]], root: Path
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Build a non-destructive clean view of the audio catalog.

    Every source file remains listed in ``audio``.  Only candidates with a
    measurable, lane-incompatible signal are omitted from ``filtered_audio``;
    ``rejected_audio`` explains each omission for diagnostics/UI.
    """
    from library_quality import audio_rejection_reason

    filtered: dict[str, list[str]] = {}
    rejected: dict[str, str] = {}
    for category, rel_paths in audio.items():
        accepted: list[str] = []
        for rel in rel_paths:
            reason = audio_rejection_reason(root / rel, category=category)
            if reason:
                rejected[rel] = reason
                continue
            accepted.append(rel)
        if accepted:
            filtered[category] = accepted
    return filtered, rejected


def _quarantine_rejected_audio(root: Path, rejected: dict[str, str]) -> dict[str, str]:
    """Move confirmed bad assets to a recoverable folder under the library.

    ``rejected`` paths are always scanner-produced relative paths.  The
    destination preserves that hierarchy under ``_PLG_UNUSE`` and a manifest
    records the original path and reason, so this is a reversible quarantine,
    never deletion or overwrite.
    """
    if not rejected:
        return {}

    root = root.resolve()
    quarantine = (root / QUARANTINE_DIR_NAME).resolve()
    moved: dict[str, str] = {}
    manifest_rows: list[dict[str, str]] = []
    manifest_path = quarantine / QUARANTINE_MANIFEST_NAME
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else []
        if not isinstance(existing, list):
            existing = []
    except (OSError, json.JSONDecodeError):
        existing = []

    for rel, reason in rejected.items():
        source = (root / rel).resolve()
        try:
            source.relative_to(root)
        except ValueError:
            continue
        if not source.is_file() or QUARANTINE_DIR_NAME.lower() in {part.lower() for part in source.parts}:
            continue

        destination = (quarantine / rel).resolve()
        try:
            destination.relative_to(quarantine)
        except ValueError:
            continue
        if destination.exists():
            # Preserve both files if a same-named source is imported later.
            suffix = 2
            while destination.exists():
                destination = destination.with_name(f"{destination.stem}__{suffix}{destination.suffix}")
                suffix += 1
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        except OSError:
            continue

        destination_rel = destination.relative_to(root).as_posix()
        moved[rel] = destination_rel
        manifest_rows.append(
            {
                "original_path": rel,
                "quarantine_path": destination_rel,
                "reason": reason,
                "quarantined_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    if manifest_rows:
        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(existing + manifest_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            # A moved file remains safely quarantined even if the diagnostic
            # write failed; the next scan never treats _PLG_UNUSE as audio.
            pass
    return moved


def restore_quarantined_audio(root: Path) -> dict[str, str]:
    """Put back every file an older PLG moved into ``_PLG_UNUSE``.

    PLG used to physically move "rejected" audio out of the user's kit. The
    rejection was often a false alarm (a "808 Snare" filed as an 808 and judged
    too short), and a scan no longer moves anything: the clean view
    (``filtered_audio``) already keeps rejected files away from the matcher, so a
    restored bad file stays invisible to it. Files go back to the path recorded
    in the manifest; if that path is taken they get a ``__restored`` suffix so
    nothing is overwritten. Returns ``{quarantine_rel: restored_rel}``.
    """
    root = Path(root).resolve()
    quarantine = root / QUARANTINE_DIR_NAME
    manifest_path = quarantine / QUARANTINE_MANIFEST_NAME
    if not manifest_path.is_file():
        return {}
    try:
        rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(rows, list):
        return {}

    restored: dict[str, str] = {}
    remaining: list[Any] = []
    for row in rows:
        try:
            source = (root / str(row["quarantine_path"])).resolve()
            dest = (root / str(row["original_path"])).resolve()
            source.relative_to(quarantine)
            dest.relative_to(root)
        except (KeyError, TypeError, ValueError, OSError):
            remaining.append(row)
            continue
        if not source.is_file():
            continue  # already gone (the user moved it): nothing to restore
        if QUARANTINE_DIR_NAME.lower() in {part.lower() for part in dest.parts}:
            remaining.append(row)
            continue
        if dest.exists():
            suffix = 1
            candidate = dest.with_name(f"{dest.stem}__restored{dest.suffix}")
            while candidate.exists():
                suffix += 1
                candidate = dest.with_name(f"{dest.stem}__restored{suffix}{dest.suffix}")
            dest = candidate
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(dest))
        except OSError:
            remaining.append(row)
            continue
        restored[source.relative_to(root).as_posix()] = dest.relative_to(root).as_posix()

    try:
        if remaining:
            manifest_path.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            manifest_path.unlink()
        for folder in sorted((p for p in quarantine.rglob("*") if p.is_dir()), reverse=True):
            try:
                folder.rmdir()  # only removes empty folders
            except OSError:
                pass
        quarantine.rmdir()
    except OSError:
        pass
    if restored:
        import logging

        logging.getLogger(__name__).info(
            "Restored %d file(s) an older PLG had moved to %s", len(restored), QUARANTINE_DIR_NAME
        )
    return restored


def _warm_sample_passports(root: Path) -> None:
    """Write-only side effect (sample_passport.py): measure every sample once.

    Adds nothing to the catalog dict and nobody reads the result yet."""
    try:
        from sample_passport import warm_for_scan

        warm_for_scan(root)
    except Exception as exc:  # noqa: BLE001 — a passport must never break a scan
        import logging

        logging.getLogger("plg.passport").info("Sample passport warm-up skipped (%s)", exc)


def _catalog_rel(file_path: Path, scan_root: Path, root: Path) -> str:
    """The catalog's path for a file: relative to ``root``, which every consumer
    joins back (``root / rel``). A file in the companion folder gets a
    ``../PLG_Sounds/...`` path, which resolves to the same file."""
    if scan_root == root:
        return file_path.relative_to(root).as_posix()
    return Path(os.path.relpath(file_path, root)).as_posix()


def scan_library(root: Path, *, quarantine: bool = False) -> dict[str, Any]:
    """Scan the library. Never touches the user's files unless ``quarantine=True``.

    When PLG_Library and PLG_Sounds both exist, both are scanned (see
    ``library_paths.companion_library_roots``); ``root`` is scanned first and
    wins duplicates. Quarantine only ever applies to ``root``.

    ``quarantine=True`` (explicit opt-in) physically moves rejected audio to
    ``_PLG_UNUSE``; nothing in PLG asks for it any more — see
    ``restore_quarantined_audio``. Rejected files are still left out of
    ``filtered_audio``, which is all the matcher reads.
    """
    if not root.is_dir():
        raise FileNotFoundError(f"Library folder not found: {root}")
    if not quarantine:
        restore_quarantined_audio(root)

    audio: dict[str, list[str]] = {name: [] for name in AUDIO_FOLDERS}
    midi: list[str] = []
    presets: list[str] = []
    projects: list[str] = []
    banks: list[str] = []
    plugins: list[str] = []
    # Kits often exist twice (re-download / copy with a mangled folder name).
    # Same filename + same size = the same sound; index it once, or every
    # downstream "variety" mechanism rotates between copies of one file.
    seen_files: set[tuple[str, int]] = set()

    companions = companion_library_roots(root)
    for scan_root in [root, *companions]:
        for file_path in sorted(scan_root.rglob("*")):
            if not file_path.is_file():
                continue
            if _is_junk_file(file_path):
                continue
            suffix = file_path.suffix.lower()
            rel = _catalog_rel(file_path, scan_root, root)

            if suffix in AUDIO_EXTENSIONS or suffix in MIDI_EXTENSIONS:
                try:
                    dedupe_key = (file_path.name.lower(), file_path.stat().st_size)
                except OSError:
                    dedupe_key = (file_path.name.lower(), -1)
                if dedupe_key in seen_files:
                    continue
                seen_files.add(dedupe_key)

            if suffix in AUDIO_EXTENSIONS:
                category = _folder_category(file_path, scan_root)
                if category not in audio:
                    category = "kits"
                audio[category].append(rel)
            elif suffix in MIDI_EXTENSIONS:
                midi.append(rel)
            elif suffix in PRESET_EXTENSIONS:
                presets.append(rel)
            elif suffix in PROJECT_EXTENSIONS:
                projects.append(rel)
            elif suffix in BANK_EXTENSIONS:
                banks.append(rel)
            elif suffix in PLUGIN_EXTENSIONS:
                plugins.append(rel)

    audio = {key: values for key, values in audio.items() if values}
    audio_total = sum(len(values) for values in audio.values())
    filtered_audio, rejected_audio = _build_filtered_audio(audio, root)
    quarantined_audio = (
        _quarantine_rejected_audio(
            root, {rel: why for rel, why in rejected_audio.items() if not rel.startswith("../")}
        )
        if quarantine
        else {}
    )
    if quarantined_audio:
        quarantined_paths = set(quarantined_audio)
        audio = {
            category: [rel for rel in rel_paths if rel not in quarantined_paths]
            for category, rel_paths in audio.items()
        }
        audio = {category: rel_paths for category, rel_paths in audio.items() if rel_paths}
        audio_total = sum(len(values) for values in audio.values())
        # The virtual clean view is already free of these paths, but rebuild
        # its total after a physical quarantine for a truthful scan report.
        filtered_audio = {
            category: [rel for rel in rel_paths if rel not in quarantined_paths]
            for category, rel_paths in filtered_audio.items()
        }
        filtered_audio = {category: rel_paths for category, rel_paths in filtered_audio.items() if rel_paths}
    filtered_audio_total = sum(len(values) for values in filtered_audio.values())
    total = audio_total + len(midi) + len(presets) + len(projects) + len(banks) + len(plugins)

    for scan_root in [root, *companions]:
        _warm_sample_passports(scan_root)
    return {
        "root": str(root.resolve()),
        "extra_roots": [str(path.resolve()) for path in companions],
        "total": total,
        "audio_total": audio_total,
        "audio": audio,
        # Matcher-facing clean view. The raw audio view above is preserved for
        # transparency and for any future, explicitly opted-in use case.
        "filtered_audio": filtered_audio,
        "filtered_audio_total": filtered_audio_total,
        "rejected_audio": rejected_audio,
        "quarantined_audio": quarantined_audio,
        "quality_filter_version": QUALITY_FILTER_VERSION,
        "midi": midi,
        "presets": presets,
        "projects": projects,
        "banks": banks,
        "plugins": plugins,
    }


def save_catalog(catalog: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")


def _limited_list(items: list[str], limit: int) -> tuple[list[str], int]:
    if len(items) <= limit:
        return items, 0
    return items[:limit], len(items) - limit


def format_library_for_prompt(catalog: dict[str, Any]) -> str:
    if catalog.get("total", 0) == 0:
        return "Library is empty."

    lines = [
        f"Library root: {catalog['root']}",
        f"Total assets: {catalog['total']} (audio: {catalog.get('audio_total', 0)})",
        "",
        "AUDIO — use in samples[] and note.sample (relative paths only):",
    ]

    audio_used = 0
    for category, files in catalog.get("audio", {}).items():
        lines.append(f"  [{category}] ({len(files)} files)")
        for rel in files:
            if audio_used >= MAX_AUDIO_IN_PROMPT:
                rest = catalog.get("audio_total", 0) - MAX_AUDIO_IN_PROMPT
                if rest > 0:
                    lines.append(f"  ... +{rest} more audio omitted")
                break
            lines.append(f"    - {rel}")
            audio_used += 1
        if audio_used >= MAX_AUDIO_IN_PROMPT:
            break

    for label, key in (
        ("MIDI — reference in library_refs[], optional manual_steps to drag into FL", "midi"),
        ("PRESETS — library_refs type=preset, tell user which channel to load", "presets"),
        ("PROJECTS — library_refs type=project, open as reference template", "projects"),
        ("BANKS — library_refs type=bank, load in sampler/FL browser", "banks"),
        ("PLUGINS — library_refs type=plugin, manual install only, never auto-load", "plugins"),
    ):
        items = catalog.get(key) or []
        if not items:
            continue
        shown, omitted = _limited_list(items, MAX_OTHER_IN_PROMPT)
        lines.extend(["", f"{label}:"])
        lines.extend(f"  - {item}" for item in shown)
        if omitted:
            lines.append(f"  ... +{omitted} more")

    return "\n".join(lines).strip()


# Backward-compatible aliases used across the project
def scan_samples_directory(root: Path) -> dict[str, Any]:
    catalog = scan_library(root)
    legacy_audio = catalog.get("audio") or {}
    return {
        "root": catalog["root"],
        "total": catalog.get("audio_total", 0),
        "categories": legacy_audio,
    }


def format_catalog_for_prompt(catalog: dict[str, Any]) -> str:
    if "audio" in catalog:
        return format_library_for_prompt(catalog)
    return format_library_for_prompt(
        {
            "root": catalog.get("root", ""),
            "total": catalog.get("total", 0),
            "audio_total": catalog.get("total", 0),
            "audio": catalog.get("categories", {}),
            "midi": [],
            "presets": [],
            "projects": [],
            "banks": [],
            "plugins": [],
        }
    )

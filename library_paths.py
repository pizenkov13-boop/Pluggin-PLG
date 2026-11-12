"""PLG library folders — maps kit-pack-style categories to disk layout."""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from plg_paths import app_dir

PROJECT_DIR = app_dir()
DEFAULT_LIBRARY_DIR = PROJECT_DIR / "PLG_Library"
LEGACY_LIBRARY_DIR = PROJECT_DIR / "PLG_Sounds"
# MIDI remains a separate, user-facing material entry point.  Audio can use
# the library/legacy sound root independently, but generation must also look
# in PLG_MIDI for the melody supplied by the user.
DEFAULT_MIDI_DIR = PROJECT_DIR / "PLG_MIDI"


# What PLG itself writes (the stems and the combined MIDI of every beat) lives in output_midi, next to the exe and
# apart from PLG_MIDI. Should a user-chosen folder ever contain it (a library root that is an ancestor of the exe
# folder), it still must never count as the user's material or feed the next beat.
SERVICE_MIDI_DIR = "output_midi"
SERVICE_MIDI_PREFIX = "plg_stems_"


_HANDLE = re.compile(r"@[\w\-]+")


def strip_handles(text: str) -> str:
    """A name without its producer handles: ``BD #11 @producerd.wav`` -> ``BD #11  .wav``.

    The word after ``@`` is somebody's nickname, not a description of the sound: ``@producerd`` contains "perc" and
    used to make a kick look like a percussion file (and a clap look like a kick-killer)."""
    return _HANDLE.sub(" ", text)


def is_service_midi(path: Path | str) -> bool:
    """True for a MIDI file PLG generated itself (output_midi/ and its PLG_Stems_* session folders)."""
    for part in Path(path).parts:
        lowered = part.lower()
        if lowered == SERVICE_MIDI_DIR or lowered.startswith(SERVICE_MIDI_PREFIX):
            return True
    return False


@lru_cache(maxsize=4096)
def _digest_cached(resolved_path: str, _mtime_ns: int, _size: int) -> str:
    try:
        return hashlib.sha1(Path(resolved_path).read_bytes()).hexdigest()
    except OSError:
        return "unreadable:" + resolved_path


def distinct_midi_files(paths: Iterable[Path]) -> list[Path]:
    """The user's MIDI without copies: files with identical content count (and are picked from) once.

    The same folder dropped twice, or a file saved as "x (1).mid", must not inflate the Home counter or give one
    melody a bigger share of the random pick. The first path in sorted order stands for its copies."""
    seen: set[str] = set()
    kept: list[Path] = []
    for path in sorted(set(Path(p) for p in paths)):
        try:
            stat = path.stat()
            key = _digest_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = "unreadable:" + str(path)
        if key in seen:
            continue
        seen.add(key)
        kept.append(path)
    return kept


def companion_library_roots(primary: Path) -> list[Path]:
    """The other default library folder, when it exists next to ``primary``.

    A fresh install creates PLG_Library; older installs and the repo use
    PLG_Sounds. Once both exist the user has put sounds in each, and picking
    only one made the other silently ignored (the project then fell back to the
    built-in sounds). ``primary`` stays the root the catalog is relative to and
    wins duplicates; only these two default folders pair up, so a root chosen
    through PLG_SAMPLES_DIR somewhere else stands alone."""
    try:
        here = Path(primary).resolve()
        pair = {DEFAULT_LIBRARY_DIR.resolve(), LEGACY_LIBRARY_DIR.resolve()}
    except OSError:
        return []
    if here not in pair:
        return []
    return [other for other in sorted(pair - {here}) if other.is_dir()]


def library_roots(primary: Path) -> list[Path]:
    """Every folder scanned for ``primary``: itself first, then its companion."""
    return [Path(primary), *companion_library_roots(Path(primary))]


def relative_in_library(path: Path, primary: Path) -> Path:
    """``path`` relative to the library folder that holds it.

    Replaces ``path.relative_to(primary)`` wherever the answer feeds scoring or a
    containment check: a file in the companion folder is judged by its own
    folder layout ("808/x.wav"), exactly as if that folder were the only
    library. Raises ValueError when no library folder holds the path."""
    resolved = Path(path).resolve()
    for root in library_roots(primary):
        try:
            return resolved.relative_to(root.resolve())
        except ValueError:
            continue
    raise ValueError(f"{path} is not inside the library")


# Audio — drum kits, samples, splice downloads
AUDIO_FOLDERS = ("808", "hats", "kits", "textures", "melodies", "fx", "vocal_presets", "splice", "references")

# Other pack categories
MIDI_FOLDER = "midi"
PRESETS_FOLDER = "presets"
PROJECTS_FOLDER = "projects"
BANKS_FOLDER = "banks"
PLUGINS_FOLDER = "plugins"

ALL_LIBRARY_FOLDERS = AUDIO_FOLDERS + (
    MIDI_FOLDER,
    PRESETS_FOLDER,
    PROJECTS_FOLDER,
    BANKS_FOLDER,
    PLUGINS_FOLDER,
)

AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".aif", ".aiff"}
MIDI_EXTENSIONS = {".mid", ".midi"}
PRESET_EXTENSIONS = {".fst", ".fxp", ".nmsv", ".vital", ".serumpreset", ".h2p"}
PROJECT_EXTENSIONS = {".flp"}
BANK_EXTENSIONS = {".sf2", ".sfz", ".zip"}
PLUGIN_EXTENSIONS = {".dll", ".vst3", ".exe"}

# 2026-07 audit: a 206-second melodic loop, sitting in an unrecognized folder
# ("...BONUS LOOPS"), fell through the keyword categorizer straight into
# "kits" and got picked for the kick channel — kick "sounded like a melody".
# Neither existing name-based guard caught it (no "kick"/"loop"/"bpm" in the
# FILENAME itself, only in its parent folder). Duration is a much harder
# signal to dodge than a filename convention: a real one-shot drum hit,
# even with a generous tail/reverb, does not run past a few seconds.
ONE_SHOT_MAX_SECONDS = 3.0

# A drum word next to "808" ("808 Snare", "Kick 808"): shorter than this it is that
# drum (TR-808 kits name every hit that way), longer it is really an 808. One value
# for the catalog (bucket) and the matcher (score) so they never disagree.
DRUM_808_TIEBREAK_SECONDS = 0.6


def audio_duration_seconds(path: Path) -> float | None:
    """Cheap duration probe: a .wav (PCM or IEEE float) or .flac reads its
    header only — no decode, no ffmpeg; other formats decode via pydub (already a project
    dependency — no new install).
    Cached per (path, mtime, size) so repeated scans/candidate scoring never
    re-probe the same file twice. Returns None when the file can't be read —
    callers should fail OPEN on None (never block a file we simply couldn't
    measure), matching this codebase's everywhere-else "never break
    generation over a best-effort check" convention."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return _duration_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=4096)
def _duration_cached(resolved_path: str, _mtime_ns: int, _size: int) -> float | None:
    path = Path(resolved_path)
    try:
        from audio_decode import can_decode, duration_seconds

        if can_decode(path):
            try:
                return duration_seconds(path)
            except (ValueError, EOFError, OSError):
                # Not a WAV/FLAC audio_decode understands (mp3 renamed .wav,
                # ADPCM, RF64...): decode through pydub below instead of
                # treating the duration as unknowable and bypassing quality
                # gates.
                pass
        from pydub_windows_compat import audio_segment_from_file

        segment = audio_segment_from_file(str(path))
        return len(segment) / 1000.0
    except Exception:  # noqa: BLE001 — a duration probe must never break a scan/generation
        return None


def is_probably_one_shot(path: Path, *, max_seconds: float = ONE_SHOT_MAX_SECONDS) -> bool:
    """True when duration is known and under the one-shot ceiling, OR
    duration couldn't be determined — fails open, see audio_duration_seconds."""
    duration = audio_duration_seconds(path)
    return duration is None or duration <= max_seconds


PACK_CATEGORY_HINTS = {
    "808": "drum kits / 808 bass",
    "hats": "drum kits / hi-hats",
    "kits": "drum kits / kicks snares claps",
    "textures": "samples / texture & noise",
    "melodies": "samples / loops & melodies",
    "fx": "samples / fx & transitions",
    "vocal_presets": "presets / vocal chains",
    "splice": "splice / one-shots & loops",
    "references": "references / drop snippets to reverse into MIDI",
    "midi": "midi / melody & drum MIDI",
    "presets": "presets / synth & mixer presets",
    "projects": "projects / reference FL templates",
    "banks": "banks / soundfonts & banks",
    "plugins": "plugins / install manually in FL",
}

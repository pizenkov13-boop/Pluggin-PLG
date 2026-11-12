"""Per-sample passports: what each library sample actually sounds like.

Phase 2 of the engine-consistency plan. A passport is a bag of MEASURED numbers
for one audio file — the features ``audio_features.analyze()`` already returns,
plus the "character" numbers (band energy shares, rolloff, flatness, level,
crest, decay-to-threshold times) from ``audio_features.analyze_with_character``.

Write-only, like the Style Card: passports are computed and stored, and no
sample picker, gate or audit reads them yet. Phase 3 (kit coherence) is the
first reader. Consequently:

* Numbers only. Labels such as "dark" / "bright" / "hard" depend on the lane and
  on the user's own library (a "low" centroid for a hat is not one for a kick),
  so they are derived at read time from per-lane percentiles, not stored.
* ``analyze()`` and ``sample_pitch_passport`` are called, never changed. The 808
  root is the expensive one (~0.3 s per file), so it is NOT computed during
  warm-up: ``root`` stays ``None`` until a reader asks via ``fill_root``.

Storage is a sidecar JSON in the user data dir, keyed by a hash of the file's
CONTENT, so a renamed, moved or re-downloaded copy keeps its passport. A
``(path, mtime, size) -> hash`` memo avoids re-hashing unchanged files on every
library scan. Every write is atomic and serialised by a lock.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import audio_features
from library_paths import AUDIO_EXTENSIONS
from plg_paths import user_data_dir

logger = logging.getLogger("plg.passport")

PASSPORT_FILE = user_data_dir() / "sample_passports.json"
PASSPORT_VERSION = 1
STORE_VERSION = 1

#: One scan may spend at most this long computing NEW passports; the rest is
#: finished by the next scan (the cache is resumable). A cold 850-file library
#: is a few seconds, but a user can point PLG at a much larger one.
WARM_BUDGET_S = 15.0

_FULL_HASH_LIMIT = 32 * 1024 * 1024
_EDGE_BYTES = 4 * 1024 * 1024
_CHUNK = 1024 * 1024

#: After a COMPLETE warm-up the same roots are not walked again for this long.
_RESCAN_INTERVAL_S = 30.0

_lock = threading.RLock()
_store: dict[str, Any] | None = None
_last_complete: dict[tuple[str, ...], float] = {}
_dirty = False  # computed in memory since the last write


# --- storage ---------------------------------------------------------------


def _fresh_store() -> dict[str, Any]:
    return {"version": STORE_VERSION, "by_hash": {}, "stat_index": {}}


def _load_store() -> dict[str, Any]:
    global _store
    with _lock:
        if _store is None:
            import json

            try:
                data = json.loads(PASSPORT_FILE.read_text(encoding="utf-8"))
                ok = (
                    isinstance(data, dict)
                    and data.get("version") == STORE_VERSION
                    and isinstance(data.get("by_hash"), dict)
                    and isinstance(data.get("stat_index"), dict)
                )
                _store = data if ok else _fresh_store()
            except (OSError, ValueError):
                _store = _fresh_store()
        return _store


def _save_store() -> bool:
    global _dirty
    import json

    with _lock:
        ok = audio_features.atomic_write_text(
            PASSPORT_FILE, json.dumps(_load_store(), separators=(",", ":"))
        )
        if ok:
            _dirty = False
        return ok


def flush_store() -> bool:
    """Persist anything computed with ``persist=False`` (one atomic write, and
    none when nothing changed)."""
    with _lock:
        if not _dirty:
            return True
    return _save_store()


def reset_memory_cache() -> None:
    """Forget the in-memory copy (tests / a changed ``PASSPORT_FILE``)."""
    global _store, _dirty
    with _lock:
        _store = None
        _dirty = False
        _last_complete.clear()


# --- hashing ---------------------------------------------------------------


def content_hash(path: Path | str) -> str | None:
    """Hash of the file's content; None when it cannot be read.

    Files over 32 MB (loops, full tracks) hash their first and last 4 MB plus
    the size, tagged ``p:`` — reading every byte of a long file on each scan
    would cost more than analysing it."""
    p = Path(path)
    try:
        size = p.stat().st_size
        digest = hashlib.blake2b(digest_size=16)
        with p.open("rb") as handle:
            if size <= _FULL_HASH_LIMIT:
                while chunk := handle.read(_CHUNK):
                    digest.update(chunk)
                return digest.hexdigest()
            digest.update(handle.read(_EDGE_BYTES))
            handle.seek(max(0, size - _EDGE_BYTES))
            digest.update(handle.read(_EDGE_BYTES))
            digest.update(str(size).encode())
            return "p:" + digest.hexdigest()
    except OSError:
        return None


def _key(p: Path) -> str:
    """Memo key. ``abspath`` rather than ``resolve``: it is ~5x cheaper on
    Windows and a scan asks for it once per file."""
    return os.path.normcase(os.path.abspath(p))


def _memo_hit(key: str, st: os.stat_result) -> str | None:
    with _lock:
        memo = _load_store()["stat_index"].get(key)
    if isinstance(memo, list) and len(memo) == 3 and memo[0] == st.st_mtime_ns and memo[1] == st.st_size:
        return str(memo[2])
    return None


def _resolve_hash(p: Path) -> tuple[str, str, int, int] | None:
    """(hash, path key, mtime_ns, size), reusing the stat memo when unchanged."""
    try:
        st = p.stat()
    except OSError:
        return None
    key = _key(p)
    known = _memo_hit(key, st)
    if known is not None:
        return known, key, st.st_mtime_ns, st.st_size
    digest = content_hash(p)
    if digest is None:
        return None
    global _dirty
    with _lock:
        _load_store()["stat_index"][key] = [st.st_mtime_ns, st.st_size, digest]
        _dirty = True
    return digest, key, st.st_mtime_ns, st.st_size


# --- passports -------------------------------------------------------------


def _build_passport(p: Path, digest: str, size: int) -> dict[str, Any]:
    try:
        analysed = audio_features.analyze_with_character(p)
    except Exception as exc:  # noqa: BLE001 — a bad file must not break a scan
        logger.info("Passport analysis failed for %s (%s)", p.name, exc)
        analysed = None
    if analysed is None:
        return {"v": PASSPORT_VERSION, "status": "unreadable", "hash": digest, "bytes": size}
    base, character = analysed
    return {
        "v": PASSPORT_VERSION,
        "status": "ok",
        "hash": digest,
        "bytes": size,
        "features": base,
        "character": character,
        "root": None,
    }


def passport_for(path: Path | str, *, persist: bool = True) -> dict[str, Any] | None:
    """The passport of ``path``, computed once per distinct content.

    Returns None only when the file cannot be read at all; an unreadable AUDIO
    file returns ``{"status": "unreadable"}`` (and is remembered, so it is not
    decoded again on every scan). Never raises."""
    try:
        p = Path(path)
        resolved = _resolve_hash(p)
        if resolved is None:
            return None
        digest, _key, _mtime, size = resolved
        with _lock:
            hit = _load_store()["by_hash"].get(digest)
        if isinstance(hit, dict) and hit.get("v") == PASSPORT_VERSION:
            return copy.deepcopy(hit)
        passport = _build_passport(p, digest, size)
        global _dirty
        with _lock:
            _load_store()["by_hash"][digest] = passport
            _dirty = True
        if persist:
            _save_store()
        return copy.deepcopy(passport)
    except Exception as exc:  # noqa: BLE001 — write-only data must never block a beat
        logger.info("Passport skipped for %s (%s)", path, exc)
        return None


def cached_passport(path: Path | str) -> dict[str, Any] | None:
    """The stored passport, or None — never computes."""
    try:
        p = Path(path)
        digest = _memo_hit(_key(p), p.stat())
        if digest is None:
            return None
        with _lock:
            hit = _load_store()["by_hash"].get(digest)
        return copy.deepcopy(hit) if isinstance(hit, dict) and hit.get("v") == PASSPORT_VERSION else None
    except OSError:
        return None


def _has_current(p: Path) -> bool:
    """Cheap 'is this file already done' test for the scan loop (no copies)."""
    try:
        digest = _memo_hit(_key(p), p.stat())
    except OSError:
        return False
    if digest is None:
        return False
    with _lock:
        hit = _load_store()["by_hash"].get(digest)
    return isinstance(hit, dict) and hit.get("v") == PASSPORT_VERSION


_ROOT_FIELDS = ("status", "root_trusted", "root_source", "detected_midi", "root_pc", "fl_root_note", "cents", "prominence")


def fill_root(path: Path | str) -> dict[str, Any] | None:
    """Add the 808 root to a passport via ``sample_pitch_passport`` (~0.3 s).

    Reader-driven on purpose: warm-up must stay cheap. The pitch passport's own
    logic is used as is — only a subset of its output is kept."""
    passport = passport_for(path, persist=False)
    if passport is None or passport.get("status") != "ok":
        return passport
    if passport.get("root") is not None:
        return passport
    try:
        from sample_pitch_passport import sample_pitch_passport

        raw = sample_pitch_passport(Path(path))
        passport["root"] = {field: raw.get(field) for field in _ROOT_FIELDS}
    except Exception as exc:  # noqa: BLE001
        passport["root"] = {"status": "unavailable", "reason": str(exc)}
    with _lock:
        _load_store()["by_hash"][passport["hash"]] = passport
    _save_store()
    return copy.deepcopy(passport)


# --- warm-up ---------------------------------------------------------------


def _audio_files(root: Path) -> Iterable[Path]:
    from library_catalog import _is_junk_file

    for file_path in sorted(root.rglob("*")):
        if file_path.suffix.lower() in AUDIO_EXTENSIONS and file_path.is_file() and not _is_junk_file(file_path):
            yield file_path


def warm_passports(roots: Iterable[Path | str], *, budget_s: float = WARM_BUDGET_S) -> dict[str, Any]:
    """Make sure every audio file under ``roots`` has a passport.

    One atomic write at the end. Stops computing NEW passports once ``budget_s``
    has elapsed (cached ones are always free) and reports ``complete=False`` so
    the next call carries on. After a complete pass over every root, memo entries
    of vanished files under those roots and passports nothing references any
    more are dropped, so the sidecar does not grow forever."""
    started = time.monotonic()
    summary: dict[str, Any] = {
        "files": 0, "computed": 0, "cached": 0, "unreadable": 0, "complete": True, "seconds": 0.0,
    }
    seen_keys: set[str] = set()
    walked_roots: list[str] = []
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        walked_roots.append(_key(root_path))
        for file_path in _audio_files(root_path):
            summary["files"] += 1
            seen_keys.add(_key(file_path))
            had = _has_current(file_path)
            if not had and time.monotonic() - started > budget_s:
                summary["complete"] = False
                continue
            if had:
                summary["cached"] += 1
                continue
            passport = passport_for(file_path, persist=False)
            if passport is None:
                continue
            summary["computed"] += 1
            if passport.get("status") != "ok":
                summary["unreadable"] += 1
    if summary["complete"] and walked_roots:
        _collect_garbage(walked_roots, seen_keys)
    _save_store()
    summary["seconds"] = round(time.monotonic() - started, 3)
    return summary


def _collect_garbage(roots: list[str], seen_keys: set[str]) -> None:
    prefixes = tuple(r.rstrip("\\/") + os.sep for r in roots)
    with _lock:
        store = _load_store()
        index = store["stat_index"]
        for key in [k for k in index if k.startswith(prefixes) and k not in seen_keys]:
            del index[key]
        referenced = {str(v[2]) for v in index.values() if isinstance(v, list) and len(v) == 3}
        for digest in [h for h in store["by_hash"] if h not in referenced]:
            del store["by_hash"][digest]


def warmup_enabled() -> bool:
    return os.environ.get("PLG_PASSPORT_WARMUP", "1").strip().lower() not in {"0", "false", "off", "no"}


def warm_for_scan(root: Path | str) -> dict[str, Any] | None:
    """The ``scan_library`` hook: the scanned library plus the starter kit."""
    if not warmup_enabled():
        return None
    from plg_paths import starter_runtime_dir

    roots: list[Path] = [Path(root)]
    starter = starter_runtime_dir()
    if starter.is_dir() and starter.resolve() != Path(root).resolve():
        roots.append(starter)
    # A generation scans the library several times in a few seconds; the second
    # look finds nothing new and would still stat every file.
    now = time.monotonic()
    signature = tuple(_key(r) for r in roots)
    with _lock:
        last = _last_complete.get(signature)
        if last is not None and now - last < _RESCAN_INTERVAL_S:
            return None
    summary = warm_passports(roots, budget_s=WARM_BUDGET_S)
    with _lock:
        if summary["complete"]:
            _last_complete[signature] = time.monotonic()
        else:
            _last_complete.pop(signature, None)
    logger.info("Sample passports: %s", summary)
    return summary


__all__ = [
    "PASSPORT_FILE",
    "cached_passport",
    "content_hash",
    "fill_root",
    "passport_for",
    "warm_for_scan",
    "warm_passports",
    "warmup_enabled",
]

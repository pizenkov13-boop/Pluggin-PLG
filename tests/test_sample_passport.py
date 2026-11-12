"""Sample passports (sample_passport.py) — Phase 2, write-only.

Passports are measured numbers per audio file, keyed by the file's CONTENT.
Nothing reads them yet; these tests also pin that, and that scan_library's
result is unchanged by the warm-up it now triggers.
"""
from __future__ import annotations

import json
import os
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

import audio_features
import library_catalog
import sample_passport as sp

SR = 44100


def _write_wav(path: Path, samples: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(data.tobytes())
    return path


def _sine(freq: float, seconds: float = 1.0, amp: float = 0.8) -> np.ndarray:
    return amp * np.sin(2 * np.pi * freq * np.arange(int(SR * seconds)) / SR)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PASSPORT_FILE", tmp_path / "state" / "sample_passports.json")
    sp.reset_memory_cache()
    yield sp.PASSPORT_FILE
    sp.reset_memory_cache()


@pytest.fixture()
def analysis_calls(monkeypatch):
    calls = []
    real = audio_features.analyze_with_character

    def counting(path):
        calls.append(Path(path).name)
        return real(path)

    monkeypatch.setattr(audio_features, "analyze_with_character", counting)
    return calls


@pytest.fixture()
def no_starter(tmp_path, monkeypatch):
    """Keep warm_for_scan away from the real starter kit."""
    import plg_paths

    empty = tmp_path / "no_starter"
    empty.mkdir()
    monkeypatch.setattr(plg_paths, "starter_runtime_dir", lambda: empty)
    return empty


# --- one passport ------------------------------------------------------------------


def test_passport_has_features_and_character_and_no_root_yet(store, tmp_path):
    wav = _write_wav(tmp_path / "k.wav", _sine(60))
    passport = sp.passport_for(wav)
    assert passport["status"] == "ok" and passport["v"] == 1
    assert passport["features"] == audio_features.analyze(wav)
    assert set(passport["character"]["bands"]) == {"sub", "low", "mid", "presence", "air"}
    assert passport["root"] is None
    assert json.loads(json.dumps(passport)) == passport


def test_a_second_request_is_served_from_the_cache(store, tmp_path, analysis_calls):
    wav = _write_wav(tmp_path / "k.wav", _sine(60))
    first = sp.passport_for(wav)
    second = sp.passport_for(wav)
    assert first == second and analysis_calls == ["k.wav"]


def test_returned_passports_are_copies(store, tmp_path):
    wav = _write_wav(tmp_path / "k.wav", _sine(60))
    sp.passport_for(wav)["features"]["centroid"] = -1
    assert sp.passport_for(wav)["features"]["centroid"] != -1


# --- keyed by content ------------------------------------------------------------------


def test_a_renamed_or_moved_copy_reuses_the_passport(store, tmp_path, analysis_calls):
    original = _write_wav(tmp_path / "a" / "kick.wav", _sine(60))
    moved = tmp_path / "elsewhere" / "renamed.wav"
    moved.parent.mkdir()
    shutil.copyfile(original, moved)
    first = sp.passport_for(original)
    second = sp.passport_for(moved)
    assert first["hash"] == second["hash"]
    assert analysis_calls == ["kick.wav"]


def test_touching_the_file_rehashes_but_does_not_reanalyse(store, tmp_path, analysis_calls):
    wav = _write_wav(tmp_path / "k.wav", _sine(60))
    sp.passport_for(wav)
    stat = wav.stat()
    os.utime(wav, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    sp.passport_for(wav)
    assert analysis_calls == ["k.wav"]


def test_changed_content_gets_a_new_passport(store, tmp_path, analysis_calls):
    wav = _write_wav(tmp_path / "k.wav", _sine(60))
    old = sp.passport_for(wav)
    _write_wav(wav, _sine(3000))
    new = sp.passport_for(wav)
    assert new["hash"] != old["hash"] and len(analysis_calls) == 2
    assert new["character"]["bands"]["presence"] > old["character"]["bands"]["presence"]


def test_large_files_hash_head_and_tail_only(store, tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_FULL_HASH_LIMIT", 1000)
    monkeypatch.setattr(sp, "_EDGE_BYTES", 100)
    data = bytes(range(256)) * 20
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(data)
    b.write_bytes(data[:2500] + b"X" + data[2501:])  # differs only in the middle
    assert sp.content_hash(a).startswith("p:")
    assert sp.content_hash(a) == sp.content_hash(b)


def test_content_hash_of_a_missing_file_is_none(tmp_path):
    assert sp.content_hash(tmp_path / "nope.wav") is None


# --- failure handling ------------------------------------------------------------------------


def test_an_undecodable_file_is_remembered_not_retried(store, tmp_path, analysis_calls):
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not audio at all")
    assert sp.passport_for(junk)["status"] == "unreadable"
    assert sp.passport_for(junk)["status"] == "unreadable"
    assert analysis_calls == ["junk.wav"]


def test_a_missing_file_returns_none_and_never_raises(store, tmp_path):
    assert sp.passport_for(tmp_path / "gone.wav") is None
    assert sp.cached_passport(tmp_path / "gone.wav") is None


def test_an_analysis_crash_is_contained(store, tmp_path, monkeypatch):
    wav = _write_wav(tmp_path / "k.wav", _sine(60))

    def boom(_path):
        raise RuntimeError("decoder exploded")

    monkeypatch.setattr(audio_features, "analyze_with_character", boom)
    assert sp.passport_for(wav)["status"] == "unreadable"


# --- persistence -------------------------------------------------------------------------------------


def test_passports_survive_a_restart_and_the_write_is_atomic(store, tmp_path, analysis_calls):
    wav = _write_wav(tmp_path / "k.wav", _sine(60))
    sp.passport_for(wav)
    assert [p.name for p in store.parent.iterdir()] == ["sample_passports.json"]
    sp.reset_memory_cache()
    assert sp.cached_passport(wav)["status"] == "ok"
    sp.passport_for(wav)
    assert analysis_calls == ["k.wav"]


def test_cached_passport_never_computes(store, tmp_path, analysis_calls):
    wav = _write_wav(tmp_path / "k.wav", _sine(60))
    assert sp.cached_passport(wav) is None
    assert analysis_calls == [] and not store.exists()


@pytest.mark.parametrize("content", ["{not json", "[]", '{"version": 99, "by_hash": {}, "stat_index": {}}'])
def test_a_corrupt_or_foreign_store_is_ignored_not_fatal(store, tmp_path, content):
    store.parent.mkdir(parents=True)
    store.write_text(content, encoding="utf-8")
    wav = _write_wav(tmp_path / "k.wav", _sine(60))
    assert sp.passport_for(wav)["status"] == "ok"


# --- warm-up -------------------------------------------------------------------------------------------------


def _library(tmp_path):
    lib = tmp_path / "lib"
    _write_wav(lib / "Kick" / "k1.wav", _sine(55))
    _write_wav(lib / "Hat" / "h1.wav", _sine(7000, 0.1))
    _write_wav(lib / "Hat" / "h2.wav", _sine(8000, 0.1))
    (lib / "readme.txt").write_text("not audio")
    (lib / "__MACOSX").mkdir()
    (lib / "__MACOSX" / "._k1.wav").write_bytes(b"junk")
    return lib


def test_warm_counts_and_skips_non_audio_and_junk(store, tmp_path):
    summary = sp.warm_passports([_library(tmp_path)])
    assert (summary["files"], summary["computed"], summary["cached"]) == (3, 3, 0)
    assert summary["complete"] is True
    again = sp.warm_passports([tmp_path / "lib"])
    assert (again["computed"], again["cached"]) == (0, 3)


def test_warm_writes_the_sidecar_once(store, tmp_path, monkeypatch):
    writes = []
    real = audio_features.atomic_write_text
    monkeypatch.setattr(audio_features, "atomic_write_text", lambda p, t: (writes.append(p), real(p, t))[1])
    sp.warm_passports([_library(tmp_path)])
    assert len(writes) == 1


def test_warm_is_resumable_under_a_time_budget(store, tmp_path):
    lib = _library(tmp_path)
    first = sp.warm_passports([lib], budget_s=-1)
    assert first["complete"] is False and first["computed"] == 0
    second = sp.warm_passports([lib])
    assert second["complete"] is True and second["computed"] == 3


def test_warm_ignores_missing_roots(store, tmp_path):
    assert sp.warm_passports([tmp_path / "nope"])["files"] == 0


def test_garbage_collection_drops_vanished_files_but_keeps_shared_content(store, tmp_path):
    lib = tmp_path / "lib"
    a = _write_wav(lib / "a.wav", _sine(100))
    b = lib / "b_copy.wav"
    shutil.copyfile(a, b)
    c = _write_wav(lib / "c.wav", _sine(2000))
    sp.warm_passports([lib])
    sp.reset_memory_cache()
    data = json.loads(store.read_text(encoding="utf-8"))
    assert len(data["stat_index"]) == 3 and len(data["by_hash"]) == 2

    a.unlink()
    c.unlink()
    sp.warm_passports([lib])
    data = json.loads(store.read_text(encoding="utf-8"))
    assert len(data["stat_index"]) == 1       # only the copy is left
    assert len(data["by_hash"]) == 1          # its passport survived; c's was collected


def test_garbage_collection_leaves_other_roots_alone(store, tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    _write_wav(one / "a.wav", _sine(100))
    _write_wav(two / "b.wav", _sine(2000))
    sp.warm_passports([one, two])
    sp.warm_passports([one])
    assert sp.cached_passport(two / "b.wav") is not None


# --- scan_library hook ----------------------------------------------------------------------------------------------


def test_warm_for_scan_is_off_in_the_test_environment(store, tmp_path, no_starter):
    assert sp.warmup_enabled() is False
    assert sp.warm_for_scan(_library(tmp_path)) is None
    assert not store.exists()


def test_scan_library_result_is_identical_with_and_without_the_warm_up(store, tmp_path, no_starter, monkeypatch):
    lib = _library(tmp_path)
    monkeypatch.setenv("PLG_PASSPORT_WARMUP", "0")
    without = library_catalog.scan_library(lib)
    assert not store.exists()
    monkeypatch.setenv("PLG_PASSPORT_WARMUP", "1")
    with_warmup = library_catalog.scan_library(lib)
    assert with_warmup == without
    assert store.exists() and sp.cached_passport(lib / "Kick" / "k1.wav") is not None


def test_scan_library_survives_a_failing_warm_up(store, tmp_path, no_starter, monkeypatch):
    monkeypatch.setenv("PLG_PASSPORT_WARMUP", "1")

    def boom(_root):
        raise RuntimeError("passport store is on fire")

    monkeypatch.setattr(sp, "warm_for_scan", boom)
    catalog = library_catalog.scan_library(_library(tmp_path))
    assert catalog["audio_total"] == 3


def test_a_second_scan_inside_the_interval_does_not_walk_again(store, tmp_path, no_starter, monkeypatch):
    monkeypatch.setenv("PLG_PASSPORT_WARMUP", "1")
    lib = _library(tmp_path)
    assert sp.warm_for_scan(lib)["computed"] == 3
    assert sp.warm_for_scan(lib) is None
    sp.reset_memory_cache()  # also clears the throttle
    assert sp.warm_for_scan(lib)["cached"] == 3


def test_an_incomplete_warm_up_is_not_throttled(store, tmp_path, no_starter, monkeypatch):
    monkeypatch.setenv("PLG_PASSPORT_WARMUP", "1")
    monkeypatch.setattr(sp, "WARM_BUDGET_S", 0.0)
    monkeypatch.setattr("time.monotonic", iter(range(0, 10_000)).__next__)
    lib = _library(tmp_path)
    first = sp.warm_for_scan(lib)
    assert first["complete"] is False
    assert sp.warm_for_scan(lib) is not None  # not throttled: it still has work


def test_the_starter_kit_is_warmed_alongside_the_scanned_library(store, tmp_path, no_starter, monkeypatch):
    monkeypatch.setenv("PLG_PASSPORT_WARMUP", "1")
    _write_wav(no_starter / "PLG_starter_kick.wav", _sine(50))
    summary = sp.warm_for_scan(_library(tmp_path))
    assert summary["files"] == 4


# --- root (reader-driven) --------------------------------------------------------------------------------------------------


def test_fill_root_uses_the_existing_pitch_passport_and_persists(store, tmp_path):
    wav = _write_wav(tmp_path / "bass.wav", _sine(55.0, seconds=2.0))
    filled = sp.fill_root(wav)
    assert filled["root"] is not None and filled["root"]["status"] != "unavailable"
    assert isinstance(filled["root"]["root_pc"], int)
    sp.reset_memory_cache()
    assert sp.cached_passport(wav)["root"] == filled["root"]


def test_fill_root_leaves_unreadable_files_alone(store, tmp_path):
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"nope")
    result = sp.fill_root(junk)
    assert result["status"] == "unreadable" and "root" not in result


# --- write-only ----------------------------------------------------------------------------------------------------------------


def test_samples_are_never_modified(store, tmp_path):
    lib = _library(tmp_path)
    before = {p: p.read_bytes() for p in lib.rglob("*.wav")}
    sp.warm_passports([lib])
    assert {p: p.read_bytes() for p in lib.rglob("*.wav")} == before


def test_only_the_scan_hook_and_the_kit_scorer_touch_passports():
    """Phase 2 wrote them (library_catalog); Phase 3 is the first reader
    (kit_coherence, behind PLG_KIT_COHERENCE). Nothing else may depend on them."""
    root = Path(__file__).resolve().parent.parent
    readers = []
    for path in root.glob("*.py"):
        if path.name in {"sample_passport.py", "library_catalog.py", "kit_coherence.py"}:
            continue
        if "sample_passport" in path.read_text(encoding="utf-8", errors="ignore"):
            readers.append(path.name)
    assert readers == []

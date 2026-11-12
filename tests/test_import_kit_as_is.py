"""Tests for as-is kit import."""

from __future__ import annotations

from pathlib import Path

import plg_api
from library_catalog import scan_library
from organize_kit import copy_kit_as_is, organize_library


def test_copy_kit_as_is_preserves_structure(tmp_path: Path):
    src = tmp_path / "MORE CHAOS"
    (src / "808s").mkdir(parents=True)
    (src / "Claps").mkdir(parents=True)
    (src / "808s" / "dark.wav").write_bytes(b"wav")
    (src / "Claps" / "hard_clap.wav").write_bytes(b"wav")
    lib = tmp_path / "library"
    lib.mkdir()

    result = copy_kit_as_is(src, lib)
    assert result["imported"] == 2
    assert result["destination"] == "imports/MORE CHAOS"
    assert (lib / "imports" / "MORE CHAOS" / "808s" / "dark.wav").is_file()
    assert (lib / "imports" / "MORE CHAOS" / "Claps" / "hard_clap.wav").is_file()

    catalog = scan_library(lib)
    assert catalog["audio_total"] == 2
    assert any("dark.wav" in path for paths in catalog["audio"].values() for path in paths)


def test_copy_kit_as_is_unique_folder(tmp_path: Path):
    src = tmp_path / "kit"
    src.mkdir()
    (src / "a.wav").write_bytes(b"wav")
    lib = tmp_path / "library"
    lib.mkdir()
    (lib / "imports" / "kit").mkdir(parents=True)
    (lib / "imports" / "kit" / "old.wav").write_bytes(b"old")

    result = copy_kit_as_is(src, lib)
    assert result["destination"] == "imports/kit_2"
    assert (lib / "imports" / "kit_2" / "a.wav").is_file()


def test_organize_library_still_sorts(tmp_path: Path):
    src = tmp_path / "kit"
    src.mkdir()
    (src / "dark_808.wav").write_bytes(b"wav")
    lib = tmp_path / "library"
    lib.mkdir()

    counts = organize_library(src, lib)
    assert counts.get("808", 0) == 1
    assert (lib / "808" / "dark_808.wav").is_file()


def test_import_kit_folder_default_as_is(tmp_path: Path, monkeypatch):
    src = tmp_path / "kit"
    src.mkdir()
    (src / "kick.wav").write_bytes(b"wav")
    out = tmp_path / "library"
    out.mkdir()

    monkeypatch.setattr(plg_api, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: str(out))

    result = plg_api.import_kit_folder(str(src))
    assert result["ok"] is True
    assert result["mode"] == "as_is"
    assert result["destination"] == "imports/kit"
    assert (out / "imports" / "kit" / "kick.wav").is_file()


def test_import_kit_folder_organize_mode(tmp_path: Path, monkeypatch):
    src = tmp_path / "kit"
    src.mkdir()
    (src / "dark_808.wav").write_bytes(b"wav")
    out = tmp_path / "library"
    out.mkdir()

    monkeypatch.setattr(plg_api, "CATALOG_FILE", tmp_path / "catalog.json")
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: str(out))

    result = plg_api.import_kit_folder(str(src), organize=True)
    assert result["ok"] is True
    assert result["mode"] == "organize"
    assert (out / "808" / "dark_808.wav").is_file()

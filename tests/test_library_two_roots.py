"""PLG_Library and PLG_Sounds are both scanned when both exist."""

import math
import struct
import wave
from pathlib import Path

import pytest

import library_paths
from library_catalog import scan_library
from library_paths import companion_library_roots, relative_in_library


def _wav(path: Path, seconds: float = 0.3, sr: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * 80 * i / sr))) for i in range(int(sr * seconds))
    )
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sr)
        out.writeframes(frames)


@pytest.fixture
def folders(tmp_path, monkeypatch):
    new_dir, old_dir = tmp_path / "PLG_Library", tmp_path / "PLG_Sounds"
    monkeypatch.setattr(library_paths, "DEFAULT_LIBRARY_DIR", new_dir)
    monkeypatch.setattr(library_paths, "LEGACY_LIBRARY_DIR", old_dir)
    return new_dir, old_dir


def _all_audio(catalog) -> list[str]:
    return [rel for rels in catalog["audio"].values() for rel in rels]


def test_both_folders_are_scanned_and_every_entry_joins_back_to_a_file(folders):
    new_dir, old_dir = folders
    _wav(new_dir / "808" / "new 808.wav")
    _wav(old_dir / "808" / "old 808.wav")
    catalog = scan_library(new_dir)
    rels = _all_audio(catalog)
    assert len(rels) == 2
    assert catalog["extra_roots"] == [str(old_dir.resolve())]
    for rel in rels:
        assert (new_dir / rel).resolve().is_file()
    assert any(rel.startswith("../PLG_Sounds/") for rel in rels)


def test_scan_from_the_other_folder_sees_both_too(folders):
    new_dir, old_dir = folders
    _wav(new_dir / "808" / "new 808.wav")
    _wav(old_dir / "808" / "old 808.wav")
    catalog = scan_library(old_dir)
    assert len(_all_audio(catalog)) == 2
    assert catalog["extra_roots"] == [str(new_dir.resolve())]


def test_the_same_sound_in_both_folders_is_listed_once_and_the_primary_wins(folders):
    new_dir, old_dir = folders
    _wav(new_dir / "808" / "same 808.wav")
    _wav(old_dir / "808" / "same 808.wav")
    rels = _all_audio(scan_library(new_dir))
    assert rels == ["808/same 808.wav"]


def test_a_companion_file_is_filed_by_its_own_folder_layout(folders):
    new_dir, old_dir = folders
    _wav(new_dir / "kits" / "kick one.wav")
    _wav(old_dir / "hats" / "closed hat.wav")
    catalog = scan_library(new_dir)
    assert set(catalog["audio"]) == {"kits", "hats"}


def test_a_single_existing_folder_behaves_as_before(folders):
    new_dir, old_dir = folders
    _wav(new_dir / "808" / "only 808.wav")
    catalog = scan_library(new_dir)
    assert catalog["extra_roots"] == []
    assert _all_audio(catalog) == ["808/only 808.wav"]


def test_a_custom_root_stands_alone(folders, tmp_path):
    new_dir, old_dir = folders
    _wav(old_dir / "808" / "old 808.wav")
    custom = tmp_path / "SomewhereElse"
    _wav(custom / "808" / "mine.wav")
    catalog = scan_library(custom)
    assert catalog["extra_roots"] == []
    assert _all_audio(catalog) == ["808/mine.wav"]
    assert companion_library_roots(custom) == []


def test_relative_in_library_uses_the_folder_that_holds_the_file(folders):
    new_dir, old_dir = folders
    _wav(new_dir / "808" / "new 808.wav")
    _wav(old_dir / "808" / "old 808.wav")
    assert relative_in_library(old_dir / "808" / "old 808.wav", new_dir).as_posix() == "808/old 808.wav"
    assert relative_in_library(new_dir / "808" / "new 808.wav", new_dir).as_posix() == "808/new 808.wav"
    with pytest.raises(ValueError):
        relative_in_library(new_dir.parent / "elsewhere.wav", new_dir)

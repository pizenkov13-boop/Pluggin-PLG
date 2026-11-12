"""A project opens after Temp is cleaned: its sounds live next to the .flp, in ``<project>_sounds\\``.

The tester's files loaded their sounds from ``%TEMP%\\PLG_Sounds_FL`` and ``%LOCALAPPDATA%\\PLG\\starter\\_warm`` (caches), so
clearing Temp, moving the library or opening the project on another computer left every channel empty. Now the .flp
loads ``Projects\\<name>_sounds\\<file>``; identical content is stored once (``Projects\\_sounds\\<sha1>``, hard-linked into each
project's folder, a copy where linking is impossible).
"""

from __future__ import annotations

import os
import shutil
import struct
import wave
from pathlib import Path

import pytest

import flp_writer
from flp_writer import _publish_project_sound, build_flp, project_sounds_dirs, write_flp_session


def _wav(path: Path, level: int = 1000, frames: int = 2205) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(44100)
        out.writeframes(b"".join(struct.pack("<h", level if i % 2 else -level) for i in range(frames)))
    return path


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.delenv("PLG_PROJECT_SOUNDS", raising=False)
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")


# --- one sound -----------------------------------------------------------------------------------------------------


def test_a_sound_is_copied_into_the_project_folder_and_stored_once(tmp_path):
    source = _wav(tmp_path / "cache" / "kick__warm_x.wav")
    sounds, store = tmp_path / "Projects" / "PLG_A__v01_sounds", tmp_path / "Projects" / "_sounds"
    out = _publish_project_sound(source, sounds, store)
    assert out == sounds / "kick__warm_x.wav" and out.read_bytes() == source.read_bytes()
    assert len(list(store.glob("*.wav"))) == 1
    assert os.path.samefile(out, next(store.glob("*.wav")))  # hard-linked: the bytes exist once


def test_identical_content_in_two_projects_is_stored_once_and_both_folders_are_complete(tmp_path):
    source = _wav(tmp_path / "cache" / "hi_hats__x.wav")
    store = tmp_path / "Projects" / "_sounds"
    a = _publish_project_sound(source, tmp_path / "Projects" / "PLG_A__v01_sounds", store)
    b = _publish_project_sound(source, tmp_path / "Projects" / "PLG_A__v02_sounds", store)
    assert a.parent != b.parent and a.is_file() and b.is_file()
    assert len(list(store.glob("*.wav"))) == 1


def test_a_second_write_of_the_same_project_changes_nothing(tmp_path):
    source = _wav(tmp_path / "cache" / "kick.wav")
    sounds, store = tmp_path / "P" / "x_sounds", tmp_path / "P" / "_sounds"
    first = _publish_project_sound(source, sounds, store)
    stamp = first.stat().st_mtime_ns
    assert _publish_project_sound(source, sounds, store) == first and first.stat().st_mtime_ns == stamp


def test_changed_content_replaces_the_file_in_the_project_folder(tmp_path):
    sounds, store = tmp_path / "P" / "x_sounds", tmp_path / "P" / "_sounds"
    out = _publish_project_sound(_wav(tmp_path / "cache" / "808.wav", 1000), sounds, store)
    old = out.read_bytes()
    _publish_project_sound(_wav(tmp_path / "cache" / "808.wav", 3000), sounds, store)
    assert out.read_bytes() != old and len(list(store.glob("*.wav"))) == 2  # the old content stays for projects that use it


def test_a_path_with_non_ascii_letters_keeps_the_old_path(tmp_path):
    source = _wav(tmp_path / "cache" / "kick.wav")
    assert _publish_project_sound(source, tmp_path / "Проекты" / "x_sounds", tmp_path / "Проекты" / "_sounds") == source
    assert project_sounds_dirs(tmp_path / "Проекты" / "PLG_A__v01.flp") is None
    assert project_sounds_dirs(tmp_path / "Projects" / "PLG_A__v01.flp") is not None


def test_when_linking_is_impossible_it_copies(tmp_path, monkeypatch):
    def no_link(*_a, **_k):
        raise OSError("another volume")

    monkeypatch.setattr(os, "link", no_link)
    out = _publish_project_sound(_wav(tmp_path / "cache" / "k.wav"), tmp_path / "P" / "x_sounds", tmp_path / "P" / "_sounds")
    assert out.is_file() and not os.path.samefile(out, next((tmp_path / "P" / "_sounds").glob("*.wav")))


def test_any_failure_keeps_the_old_path_and_never_raises(tmp_path, monkeypatch):
    source = _wav(tmp_path / "cache" / "k.wav")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    assert _publish_project_sound(source, tmp_path / "P" / "x_sounds", tmp_path / "P" / "_sounds") == source


# --- the whole .flp -------------------------------------------------------------------------------------------------


def _data(sounds: dict[str, Path]) -> dict:
    return {
        "bpm": 140,
        "plg_midi_melody": True,
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 100}],
            "kick": [{"time_step": 0.0, "note": "C3", "length": 0.5, "velocity": 100}],
            "hi_hats": [{"time_step": 0.25, "note": "C3", "length": 0.25, "velocity": 100}],
            "clap": [{"time_step": 1.0, "note": "C3", "length": 0.5, "velocity": 100}],
            "snare": [{"time_step": 1.0, "note": "C3", "length": 0.5, "velocity": 100}],
        },
        "plg_sound_paths": {lane: str(path) for lane, path in sounds.items()},
        "plg_sample_fallback": {"808_bass": {"tier": 0, "sample": None, "reasons": [], "user_files": 0}},
    }


def _loaded(flp: Path) -> list[Path]:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    from show_flp_sounds import read_channels

    return [Path(c["sample"]) for c in read_channels(flp) if c["sample"]]


def _cache_sounds(tmp_path: Path) -> dict[str, Path]:
    temp = tmp_path / "FakeTemp" / "PLG_Sounds_FL"
    return {
        "kick": _wav(temp / "kick__warm_BD.wav", 1000),
        "hi_hats": _wav(temp / "hi_hats__HH.wav", 2000),
        "clap": _wav(temp / "clap__CLP.wav", 3000),
        "snare": _wav(temp / "snare__SN.wav", 4000),
    }


def test_the_flp_loads_its_sounds_from_the_project_folder_and_survives_a_temp_clean(tmp_path):
    sounds = _cache_sounds(tmp_path)
    flp = tmp_path / "Projects" / "PLG_Sahara__v01.flp"
    write_flp_session(_data(sounds), flp, bundle_sounds=True)

    loaded = _loaded(flp)
    assert len(loaded) == 4
    assert all(p.parent == tmp_path / "Projects" / "PLG_Sahara__v01_sounds" for p in loaded)
    assert all(all(0x20 <= ord(ch) <= 0x7E for ch in str(p)) for p in loaded)  # FL loads plain ASCII paths only

    shutil.rmtree(tmp_path / "FakeTemp")  # "clear Temp"
    assert all(p.is_file() for p in loaded)


def test_the_project_folder_can_be_moved_with_its_sounds_and_the_store_can_go(tmp_path):
    flp = tmp_path / "Projects" / "PLG_Sahara__v01.flp"
    write_flp_session(_data(_cache_sounds(tmp_path)), flp, bundle_sounds=True)
    shutil.rmtree(tmp_path / "Projects" / "_sounds")  # the shared store is only an optimisation
    assert all(p.is_file() and p.stat().st_size > 0 for p in _loaded(flp))


def test_a_bare_write_or_the_switch_off_keeps_the_old_paths(tmp_path, monkeypatch):
    sounds = _cache_sounds(tmp_path)
    plain = tmp_path / "Projects" / "PLG_A__v01.flp"
    write_flp_session(_data(sounds), plain)
    assert not (tmp_path / "Projects" / "PLG_A__v01_sounds").exists()
    monkeypatch.setenv("PLG_PROJECT_SOUNDS", "off")
    off = tmp_path / "Projects" / "PLG_A__v02.flp"
    write_flp_session(_data(sounds), off, bundle_sounds=True)
    assert not (tmp_path / "Projects" / "PLG_A__v02_sounds").exists()
    assert all("FakeTemp" in str(p) for p in _loaded(off))


def test_the_record_names_the_project_files_and_build_flp_still_works_without_a_folder(tmp_path):
    data = _data(_cache_sounds(tmp_path))
    flp = tmp_path / "Projects" / "PLG_A__v01.flp"
    write_flp_session(data, flp, bundle_sounds=True)
    assert set(data["plg_project_sounds"]) == {"kick", "hi_hats", "clap", "snare"}
    assert isinstance(build_flp(_data(_cache_sounds(tmp_path))), bytes)  # the old call shape


def test_a_rewrite_of_the_same_project_reuses_the_folder(tmp_path):
    data = _data(_cache_sounds(tmp_path))
    flp = tmp_path / "Projects" / "PLG_A__v01.flp"
    write_flp_session(data, flp, bundle_sounds=True)
    before = sorted(p.name for p in (tmp_path / "Projects" / "PLG_A__v01_sounds").iterdir())
    write_flp_session(data, flp, bundle_sounds=True)  # Open in FL writes the same file again
    assert sorted(p.name for p in (tmp_path / "Projects" / "PLG_A__v01_sounds").iterdir()) == before
    assert len(list((tmp_path / "Projects" / "_sounds").glob("*.wav"))) == 4


def test_a_rewrite_with_other_sounds_leaves_no_old_files_in_the_project_folder(tmp_path):
    data = _data(_cache_sounds(tmp_path))
    flp = tmp_path / "Projects" / "PLG_A__v01.flp"
    write_flp_session(data, flp, bundle_sounds=True)
    other = dict(_cache_sounds(tmp_path))
    other["kick"] = _wav(tmp_path / "FakeTemp" / "PLG_Sounds_FL" / "kick__warm_OTHER.wav", 9000)
    write_flp_session(_data(other), flp, bundle_sounds=True)
    names = sorted(p.name for p in (tmp_path / "Projects" / "PLG_A__v01_sounds").iterdir())
    assert "kick__warm_OTHER.wav" in names and "kick__warm_BD.wav" not in names and len(names) == 4

""""Clear folders" moves the user's files into PLG_Archive; it never deletes one."""

import hashlib
from datetime import datetime
from pathlib import Path

import pytest

import material_archive
import plg_api
from material_archive import ArchiveError, archive_folders


def _fill(folder: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _hashes(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


MIDI_FILES = {"lead.mid": b"MThd-lead" * 20, "imports/drop-1/pad.mid": b"MThd-pad" * 30}
SOUND_FILES = {"808/sub.wav": b"RIFF-808" * 50, "kick.wav": b"RIFF-kick" * 40, "Hats/Op Hat/h.wav": b"RIFF-hat" * 10}


@pytest.fixture
def folders(tmp_path):
    midi, library = tmp_path / "PLG_MIDI", tmp_path / "PLG_Library"
    _fill(midi, MIDI_FILES)
    _fill(library, SOUND_FILES)
    return midi, library, tmp_path / "PLG_Archive"


def test_files_are_moved_with_identical_bytes_and_nothing_is_lost(folders):
    midi, library, base = folders
    midi_before, library_before = _hashes(midi), _hashes(library)

    result = archive_folders(
        [("midi", midi, "MIDI"), ("library", library, "Library/PLG_Library")],
        base=base,
        now=datetime(2026, 10, 22, 21, 5, 9),
    )

    archive = base / "2026-10-22_21-05-09"
    assert result["archive_dir"] == str(archive)
    assert _hashes(archive / "MIDI") == midi_before
    assert _hashes(archive / "Library" / "PLG_Library") == library_before
    assert result["moved"] == {"midi": len(MIDI_FILES), "library": len(SOUND_FILES)}
    # the folders stay, emptied
    assert midi.is_dir() and not any(midi.iterdir())
    assert library.is_dir() and not any(library.iterdir())
    assert (archive / "manifest.json").is_file() and result["manifest"] is True


def test_a_failure_part_way_puts_every_original_back(folders, monkeypatch):
    midi, library, base = folders
    midi_before, library_before = _hashes(midi), _hashes(library)
    real_rename = material_archive._rename
    calls = {"n": 0}

    def flaky(source, destination):
        calls["n"] += 1
        if calls["n"] == 3:
            raise PermissionError("file is open in another program")
        real_rename(source, destination)

    monkeypatch.setattr(material_archive, "_rename", flaky)

    with pytest.raises(ArchiveError) as caught:
        archive_folders(
            [("midi", midi, "MIDI"), ("library", library, "Library/PLG_Library")],
            base=base,
            now=datetime(2026, 10, 22, 21, 5, 9),
        )

    assert caught.value.error_type == "archive_failed"
    assert caught.value.left_in_archive == ()
    assert _hashes(midi) == midi_before
    assert _hashes(library) == library_before
    assert not base.exists(), "the empty archive folders this run created are removed"


def test_a_rollback_that_cannot_finish_says_which_entries_are_in_the_archive(folders, monkeypatch):
    midi, library, base = folders
    real_rename = material_archive._rename
    calls = {"n": 0}

    def flaky(source, destination):
        calls["n"] += 1
        if calls["n"] == 3:
            raise PermissionError("locked")
        if calls["n"] == 4:  # the first rename back
            raise PermissionError("locked again")
        real_rename(source, destination)

    monkeypatch.setattr(material_archive, "_rename", flaky)

    with pytest.raises(ArchiveError) as caught:
        archive_folders([("midi", midi, "MIDI"), ("library", library, "Library/PLG_Library")], base=base)

    assert len(caught.value.left_in_archive) == 1
    stuck = Path(caught.value.left_in_archive[0])
    assert stuck.exists(), "what could not be moved back is still safe in the archive"


def test_empty_folders_create_no_archive(tmp_path):
    midi = tmp_path / "PLG_MIDI"
    midi.mkdir()
    base = tmp_path / "PLG_Archive"

    result = archive_folders([("midi", midi, "MIDI")], base=base)

    assert result["archive_dir"] is None and result["entries"] == 0
    assert not base.exists()


def test_two_archives_in_the_same_second_do_not_collide(folders):
    midi, library, base = folders
    when = datetime(2026, 10, 22, 21, 5, 9)
    first = archive_folders([("midi", midi, "MIDI")], base=base, now=when)
    _fill(midi, {"again.mid": b"MThd-again"})
    second = archive_folders([("midi", midi, "MIDI")], base=base, now=when)

    assert first["archive_dir"] != second["archive_dir"]
    assert Path(second["archive_dir"]).name == "2026-10-22_21-05-09_2"


def test_another_drive_is_refused_before_anything_moves(folders, monkeypatch):
    midi, library, base = folders
    before = _hashes(midi)
    monkeypatch.setattr(material_archive, "_same_device", lambda a, b: False)

    with pytest.raises(ArchiveError) as caught:
        archive_folders([("midi", midi, "MIDI")], base=base)

    assert caught.value.error_type == "cross_device"
    assert _hashes(midi) == before and not base.exists()


# --- plg_api.archive_material: the guards and the rescan ---------------------


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    import library_paths

    midi, library, base = tmp_path / "PLG_MIDI", tmp_path / "PLG_Library", tmp_path / "PLG_Archive"
    _fill(midi, MIDI_FILES)
    _fill(library, SOUND_FILES)
    monkeypatch.setattr(library_paths, "DEFAULT_MIDI_DIR", midi)
    monkeypatch.setattr(library_paths, "DEFAULT_LIBRARY_DIR", library)
    monkeypatch.setattr(library_paths, "LEGACY_LIBRARY_DIR", tmp_path / "PLG_Sounds")
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: library)
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi)
    monkeypatch.setattr(plg_api, "CATALOG_FILE", tmp_path / "sample_catalog.json")
    monkeypatch.setattr(material_archive, "archive_base", lambda: base)
    monkeypatch.setattr(plg_api, "_jobs", {})
    monkeypatch.setattr(plg_api, "_material_import_roots", {})
    monkeypatch.setenv("PLG_PASSPORT_WARMUP", "0")
    return midi, library, base


def test_archive_material_clears_both_and_the_counters_follow(api_env):
    midi, library, base = api_env
    before_midi, before_library = _hashes(midi), _hashes(library)

    result = plg_api.archive_material(["midi", "library"])

    assert result["ok"] is True
    assert result["counts"] == {"audio": 0, "midi": 0, "material": 0}
    archive = Path(result["archive_dir"])
    assert _hashes(archive / "MIDI") == before_midi
    assert _hashes(archive / "Library" / "PLG_Library") == before_library


def test_archive_material_midi_only_leaves_the_sounds(api_env):
    midi, library, base = api_env
    before_library = _hashes(library)

    result = plg_api.archive_material(["midi"])

    assert result["ok"] is True and result["moved"] == {"midi": len(MIDI_FILES)}
    assert _hashes(library) == before_library
    assert not any(midi.iterdir())


@pytest.mark.parametrize("targets", [[], ["everything"], ["midi", "trash"], None])
def test_archive_material_rejects_an_unknown_choice(api_env, targets):
    midi, library, base = api_env
    before = (_hashes(midi), _hashes(library))

    result = plg_api.archive_material(targets)

    assert result["ok"] is False and result["error_type"] == "validation"
    assert (_hashes(midi), _hashes(library)) == before


def test_archive_material_is_refused_while_a_job_runs(api_env):
    midi, library, base = api_env
    before = (_hashes(midi), _hashes(library))
    plg_api._jobs["j1"] = plg_api._Job(id="j1", kind="beat")  # status "running" by default

    result = plg_api.archive_material(["midi", "library"])

    assert result["ok"] is False and result["error_type"] == "busy"
    assert (_hashes(midi), _hashes(library)) == before and not base.exists()


def test_archive_material_is_refused_during_an_import(api_env):
    midi, library, base = api_env
    plg_api._material_import_roots["batch"] = object()

    result = plg_api.archive_material(["library"])

    assert result["ok"] is False and result["error_type"] == "busy"


def test_archive_material_never_moves_a_folder_outside_the_default_ones(api_env, tmp_path, monkeypatch):
    midi, library, base = api_env
    mine = tmp_path / "MyOwnSamples"
    _fill(mine, {"x.wav": b"RIFF"})
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: mine)

    result = plg_api.archive_material(["library"])

    assert result["ok"] is False and result["error_type"] == "custom_folder"
    assert (mine / "x.wav").is_file()


def test_archive_material_reports_a_failed_move_and_keeps_the_files(api_env, monkeypatch):
    midi, library, base = api_env
    before = (_hashes(midi), _hashes(library))

    def boom(source, destination):
        raise PermissionError("locked")

    monkeypatch.setattr(material_archive, "_rename", boom)

    result = plg_api.archive_material(["midi", "library"])

    assert result["ok"] is False and result["error_type"] == "archive_failed"
    assert (_hashes(midi), _hashes(library)) == before


def test_archive_material_midi_also_takes_the_midi_kept_inside_the_library_folder(api_env):
    midi, library, base = api_env
    _fill(library, {"midi/old_layout.mid": b"MThd-old" * 12})
    before_sounds = _hashes(library / "808")

    result = plg_api.archive_material(["midi"])

    assert result["ok"] is True
    archive = Path(result["archive_dir"])
    assert (archive / "MIDI" / "PLG_Library_midi" / "old_layout.mid").is_file()
    assert not (library / "midi" / "old_layout.mid").exists()
    assert _hashes(library / "808") == before_sounds

"""Only the user's own MIDI counts and feeds a beat: never PLG's output, and a copy is the same file.

The tester-build report: Home said "MIDI 18" after one drop. Those 18 files were the user's own, but only two
different ones - the same two folders had come in twice (a drag-and-drop and the folder picker), and every copy
counted and had its own chance in the random pick. PLG's own output (output_midi/, the PLG_Stems_* session folders)
lives next to the exe, apart from PLG_MIDI, so a normal layout never mixes it in; these tests pin that it cannot
get in even when a chosen folder contains it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import library_catalog
import midi_corpus
import midi_ingest
import plg_api
from library_paths import distinct_midi_files, is_service_midi


def _mid(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MThd" + payload)
    return path


# --- what counts as PLG's own output ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "output_midi/PLG_Beat.mid",
        "C:/app/output_midi/kick.mid",
        "x/output_midi/PLG_Stems_141bpm_trap/Melody.mid",
        "x/PLG_Stems_150bpm_trap/Kick.mid",
        "OUTPUT_MIDI/808_bass.mid",
    ],
)
def test_generated_files_are_service_files(path):
    assert is_service_midi(path)


@pytest.mark.parametrize("path", ["PLG_MIDI/melody.mid", "PLG_MIDI/imports/Vibey/x.mid", "midi/my_stems.mid", "a/b.midi"])
def test_user_files_are_not_service_files(path):
    assert not is_service_midi(path)


# --- copies are one file ----------------------------------------------------------------------------------------


def test_identical_files_are_one_and_different_files_stay_two(tmp_path):
    a = _mid(tmp_path / "a" / "loop.mid", b"one")
    b = _mid(tmp_path / "b" / "loop (1).mid", b"one")
    c = _mid(tmp_path / "c" / "other.mid", b"two")
    kept = distinct_midi_files([a, b, c])
    assert len(kept) == 2 and c in kept and (a in kept) != (b in kept)


def test_an_edited_file_is_no_longer_a_copy(tmp_path):
    a = _mid(tmp_path / "loop.mid", b"one")
    b = _mid(tmp_path / "loop (1).mid", b"one")
    assert len(distinct_midi_files([a, b])) == 1
    b.write_bytes(b"MThd" + b"changed")
    assert len(distinct_midi_files([a, b])) == 2


# --- the counter ------------------------------------------------------------------------------------------------


def _count(monkeypatch, midi_root: Path) -> int:
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi_root)
    _audio, midi_total, _material = plg_api._material_counts_from_catalog({"audio_total": 0, "midi": [], "total": 0})
    return midi_total


def test_the_counter_counts_distinct_user_files_only(tmp_path, monkeypatch):
    root = tmp_path / "PLG_MIDI"
    _mid(root / "Sahara.mid", b"sahara")
    for folder in ("Vibey", "imports/Vibey"):  # the same folder dropped twice
        for name in ("a.mid", "a (1).mid", "b.mid"):
            _mid(root / folder / name, b"vibey-" + name[:1].encode())
    assert _count(monkeypatch, root) == 3  # Sahara + the two different Vibey files


def test_the_counter_ignores_generated_files_even_inside_the_folder(tmp_path, monkeypatch):
    root = tmp_path / "PLG_MIDI"
    _mid(root / "mine.mid", b"mine")
    _mid(root / "output_midi" / "PLG_Beat.mid", b"generated-1")
    _mid(root / "output_midi" / "PLG_Stems_141bpm_trap" / "Melody.mid", b"generated-2")
    assert _count(monkeypatch, root) == 1


def test_a_normal_layout_keeps_generated_midi_out_of_the_count(tmp_path, monkeypatch):
    """output_midi sits next to PLG_MIDI (app folder), not inside it."""
    _mid(tmp_path / "PLG_MIDI" / "mine.mid", b"mine")
    _mid(tmp_path / "output_midi" / "PLG_Beat.mid", b"generated")
    assert _count(monkeypatch, tmp_path / "PLG_MIDI") == 1


# --- what the next beat can be built from -----------------------------------------------------------------------


def test_the_input_pool_is_the_users_distinct_files(tmp_path):
    root = tmp_path / "PLG_MIDI"
    _mid(root / "melody.mid", b"melody")
    _mid(root / "imports" / "melody.mid", b"melody")  # a copy
    _mid(root / "output_midi" / "PLG_Beat.mid", b"generated-1")
    _mid(root / "PLG_Stems_150bpm_trap" / "Melody.mid", b"generated-2")
    pool = midi_ingest._find_midi_folder_candidates(root)
    assert [p.name for p in pool] == ["melody.mid"]


def test_a_chosen_folder_that_contains_the_output_never_feeds_the_next_beat(tmp_path):
    """The library root (or PLG_MIDI) set to a parent of the exe folder: output_midi is inside it."""
    _mid(tmp_path / "my_melody.mid", b"mine")
    _mid(tmp_path / "output_midi" / "PLG_Beat.mid", b"generated")
    _mid(tmp_path / "output_midi" / "melody_lead.mid", b"generated-lead")
    library = midi_ingest.find_library_midi(tmp_path)
    assert [p.name for p in library] == ["my_melody.mid"]
    assert [p.name for p in midi_ingest._find_midi_folder_candidates(tmp_path)] == ["my_melody.mid"]
    assert midi_ingest.has_available_midi_material(tmp_path, midi_root=tmp_path / "PLG_MIDI") is False


def test_only_generated_files_mean_no_material(tmp_path):
    _mid(tmp_path / "output_midi" / "PLG_Beat.mid", b"generated")
    assert midi_ingest.has_available_midi_material(tmp_path / "lib", midi_root=tmp_path) is False


def test_the_corpus_and_the_catalog_skip_generated_files(tmp_path):
    _mid(tmp_path / "mine.mid", b"mine")
    _mid(tmp_path / "output_midi" / "PLG_Beat.mid", b"generated")
    names = {entry["path"].name if hasattr(entry["path"], "name") else Path(entry["path"]).name for entry in midi_corpus.scan_midi_corpus(tmp_path)}
    assert names == {"mine.mid"}
    catalog = library_catalog.scan_library(tmp_path)
    assert catalog["midi"] == ["mine.mid"]

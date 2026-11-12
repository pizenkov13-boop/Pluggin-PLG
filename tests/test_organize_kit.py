"""organize_library's MIDI routing (2026-07): .mid/.midi files sort into their
own top-level midi_root (PLG_MIDI, a sibling of the sample library) when one
is given, instead of output/midi/ — but output/midi/ keeps working exactly as
before when midi_root is omitted, so nothing existing breaks."""

from __future__ import annotations

from pathlib import Path

from organize_kit import organize_library


def _write_file(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_midi_files_route_to_midi_root_when_given(tmp_path: Path):
    source = tmp_path / "download"
    _write_file(source / "cool_loop.mid")
    _write_file(source / "Kick_One.wav")

    output = tmp_path / "PLG_Library"
    midi_root = tmp_path / "PLG_MIDI"

    counts = organize_library(source, output, midi_root=midi_root)

    assert (midi_root / "cool_loop.mid").is_file()
    assert not (output / "midi" / "cool_loop.mid").is_file()
    assert counts["midi"] == 1
    # audio is unaffected — still sorted under output/ as before
    assert (output / "kits" / "Kick_One.wav").is_file()


def test_midi_files_stay_under_output_midi_when_midi_root_omitted(tmp_path: Path):
    """Opt-in: no midi_root given -> unchanged old behavior."""
    source = tmp_path / "download"
    _write_file(source / "cool_loop.mid")

    output = tmp_path / "PLG_Library"
    counts = organize_library(source, output)

    assert (output / "midi" / "cool_loop.mid").is_file()
    assert counts["midi"] == 1


def test_midi_root_directory_is_created(tmp_path: Path):
    source = tmp_path / "download"
    _write_file(source / "cool_loop.mid")

    output = tmp_path / "PLG_Library"
    midi_root = tmp_path / "PLG_MIDI"
    assert not midi_root.exists()

    organize_library(source, output, midi_root=midi_root)
    assert midi_root.is_dir()


def test_dry_run_does_not_copy_into_midi_root(tmp_path: Path):
    source = tmp_path / "download"
    _write_file(source / "cool_loop.mid")

    output = tmp_path / "PLG_Library"
    midi_root = tmp_path / "PLG_MIDI"
    counts = organize_library(source, output, midi_root=midi_root, dry_run=True)

    assert counts["midi"] == 1
    assert not (midi_root / "cool_loop.mid").exists()

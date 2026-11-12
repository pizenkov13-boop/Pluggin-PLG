from __future__ import annotations

from pathlib import Path

import plg_api


def test_status_counts_dedicated_plg_midi_without_moving_audio_library(tmp_path: Path, monkeypatch):
    """A MIDI in PLG_MIDI must unlock the UI even when audio is in PLG_Sounds."""
    sounds = tmp_path / "PLG_Sounds"
    midi_root = tmp_path / "PLG_MIDI"
    sounds.mkdir()
    midi_root.mkdir()
    (midi_root / "melody.mid").write_bytes(b"MThd")
    catalog = {
        "root": str(sounds),
        "audio_total": 145,
        "total": 145,
        "midi": [],
    }
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi_root)

    assert plg_api._material_counts_from_catalog(catalog) == (145, 1, 146)


def test_status_does_not_double_count_midi_when_roots_are_the_same(tmp_path: Path, monkeypatch):
    root = tmp_path / "PLG_Library"
    root.mkdir()
    (root / "melody.mid").write_bytes(b"MThd")
    catalog = {
        "root": str(root),
        "audio_total": 2,
        "total": 3,
        "midi": ["melody.mid"],
    }
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: root)

    assert plg_api._material_counts_from_catalog(catalog) == (2, 1, 3)

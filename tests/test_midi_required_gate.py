"""MIDI is mandatory for generation (2026-07 product decision): create_beat
must refuse with a clear "no_midi" error before spending quota/LLM calls when
no MIDI/reference material is available (no explicit upload, nothing in the
PLG_MIDI folder), and must proceed once material shows up either way."""

from __future__ import annotations

import mido

import plg_api
from midi_ingest import has_available_material, has_available_midi_material, merge_notes_into_pattern
from pattern_utils import parse_note_name


def _write_midi(path):
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    note = parse_note_name("C4")
    track.append(mido.Message("note_on", note=note, velocity=90, time=0))
    track.append(mido.Message("note_off", note=note, velocity=0, time=480))
    mid.save(str(path))


# --- has_available_material (unit) -----------------------------------------


def test_no_material_anywhere_is_false(tmp_path):
    library_root = tmp_path / "library"
    midi_root = tmp_path / "PLG_MIDI"
    library_root.mkdir()
    midi_root.mkdir()
    assert not has_available_material(library_root, midi_root=midi_root)


def test_explicit_midi_path_satisfies_it(tmp_path):
    library_root = tmp_path / "library"
    library_root.mkdir()
    midi_path = tmp_path / "upload.mid"
    _write_midi(midi_path)
    assert has_available_material(library_root, midi_path=midi_path)


def test_nonexistent_explicit_midi_path_does_not_satisfy_it(tmp_path):
    library_root = tmp_path / "library"
    library_root.mkdir()
    assert not has_available_material(library_root, midi_path=tmp_path / "ghost.mid")


def test_file_in_plg_midi_folder_satisfies_it(tmp_path):
    library_root = tmp_path / "library"
    midi_root = tmp_path / "PLG_MIDI"
    library_root.mkdir()
    midi_root.mkdir()
    _write_midi(midi_root / "core.mid")
    assert has_available_material(library_root, midi_root=midi_root)


def test_file_in_legacy_library_midi_folder_satisfies_it(tmp_path):
    library_root = tmp_path / "library"
    legacy = library_root / "midi"
    legacy.mkdir(parents=True)
    _write_midi(legacy / "core.mid")
    assert has_available_material(library_root, midi_root=tmp_path / "PLG_MIDI")


def test_midi_only_gate_excludes_reference_audio(tmp_path):
    library_root = tmp_path / "library"
    library_root.mkdir()
    audio = tmp_path / "reference.wav"
    audio.write_bytes(b"not parsed by the presence gate")

    assert has_available_material(library_root, reference_audio_path=audio)
    assert not has_available_midi_material(library_root)


def test_midi_only_gate_accepts_folder_midi(tmp_path):
    library_root = tmp_path / "library"
    midi_root = tmp_path / "PLG_MIDI"
    library_root.mkdir()
    midi_root.mkdir()
    _write_midi(midi_root / "core.mid")

    assert has_available_midi_material(library_root, midi_root=midi_root)


def test_imported_low_midi_notes_stay_on_the_synth_melody_lane():
    pattern = {"tracks": {}}
    notes = [
        {"time_step": 0.0, "note": "C2", "length": 1.0, "velocity": 90},
        {"time_step": 0.0, "note": "E4", "length": 1.0, "velocity": 90},
    ]

    assert merge_notes_into_pattern(pattern, notes, source="fixture.mid", name="fixture.mid")
    assert {note["note"] for note in pattern["tracks"]["melody_lead"]} == {"C2", "E4"}
    assert not pattern["tracks"].get("sub_808")


# --- create_beat's early gate (integration) ---------------------------------


def test_create_beat_blocks_with_no_midi(tmp_path, monkeypatch):
    library_root = tmp_path / "library"
    midi_root = tmp_path / "PLG_MIDI"
    library_root.mkdir()
    midi_root.mkdir()

    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: False)
    monkeypatch.setattr(plg_api, "has_api_key", lambda: True)
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: library_root)
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi_root)

    def _boom(*_a, **_kw):
        raise AssertionError("run_pipeline must not be called when no MIDI is available")

    monkeypatch.setattr(plg_api, "run_pipeline", _boom)
    monkeypatch.setattr(plg_api, "ensure_can_consume_beat", _boom)

    result = plg_api.create_beat("dark trap beat, 140 bpm")

    assert result["ok"] is False
    assert result["error_type"] == "no_midi"


def test_create_beat_proceeds_with_a_plg_midi_folder_file(tmp_path, monkeypatch):
    library_root = tmp_path / "library"
    midi_root = tmp_path / "PLG_MIDI"
    library_root.mkdir()
    midi_root.mkdir()
    _write_midi(midi_root / "core.mid")

    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: False)
    monkeypatch.setattr(plg_api, "has_api_key", lambda: True)
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: library_root)
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi_root)
    monkeypatch.setattr(plg_api, "ensure_can_consume_beat", lambda: None)
    monkeypatch.setattr(plg_api, "consume_beat", lambda: None)

    calls: list[str] = []

    def _fake_run_pipeline(prompt, **_kw):
        calls.append(prompt)
        return {"bpm": 140, "style": "dark trap", "tracks": {}}

    monkeypatch.setattr(plg_api, "run_pipeline", _fake_run_pipeline)

    result = plg_api.create_beat("dark trap beat, 140 bpm")

    assert calls, "run_pipeline should have been called once material was available"
    assert result.get("error_type") != "no_midi"


def test_create_beat_does_not_require_a_prompt_when_midi_exists(tmp_path, monkeypatch):
    library_root = tmp_path / "library"
    midi_root = tmp_path / "PLG_MIDI"
    library_root.mkdir()
    midi_root.mkdir()
    _write_midi(midi_root / "core.mid")

    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: False)
    monkeypatch.setattr(plg_api, "has_api_key", lambda: True)
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: library_root)
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi_root)
    monkeypatch.setattr(plg_api, "ensure_can_consume_beat", lambda: None)
    monkeypatch.setattr(plg_api, "consume_beat", lambda: None)
    monkeypatch.setattr(
        plg_api,
        "run_pipeline",
        lambda _prompt, **_kw: {"bpm": 140, "style": "trap", "tracks": {}},
    )

    result = plg_api.create_beat("")

    assert result.get("error_type") != "validation"


def test_create_beat_proceeds_with_explicit_midi_path(tmp_path, monkeypatch):
    library_root = tmp_path / "library"
    midi_root = tmp_path / "PLG_MIDI"
    library_root.mkdir()
    midi_root.mkdir()
    midi_path = tmp_path / "upload.mid"
    _write_midi(midi_path)

    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: False)
    monkeypatch.setattr(plg_api, "has_api_key", lambda: True)
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: library_root)
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi_root)
    monkeypatch.setattr(plg_api, "ensure_can_consume_beat", lambda: None)
    monkeypatch.setattr(plg_api, "consume_beat", lambda: None)

    calls: list[str] = []

    def _fake_run_pipeline(prompt, **_kw):
        calls.append(prompt)
        return {"bpm": 140, "style": "dark trap", "tracks": {}}

    monkeypatch.setattr(plg_api, "run_pipeline", _fake_run_pipeline)

    result = plg_api.create_beat("dark trap beat, 140 bpm", midi_path=str(midi_path))

    assert calls, "run_pipeline should have been called once material was available"
    assert result.get("error_type") != "no_midi"


def test_create_beat_cloud_mode_still_checks_the_plg_midi_folder(tmp_path, monkeypatch):
    """A curated PLG_MIDI folder file is local filesystem state — cloud mode
    still requires it before running, and reports `no_midi` (not the blanket
    `not_supported`, which now only fires for an explicit attached upload)."""
    library_root = tmp_path / "library"
    midi_root = tmp_path / "PLG_MIDI"
    library_root.mkdir()
    midi_root.mkdir()

    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: True)
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: library_root)
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi_root)
    monkeypatch.setattr(plg_api.plg_cloud, "is_signed_in", lambda: True)

    def _boom(*_a, **_kw):
        raise AssertionError("cloud_generate must not run when no MIDI is available")

    monkeypatch.setattr(plg_api.plg_cloud, "cloud_generate", _boom)

    result = plg_api.create_beat("dark trap beat, 140 bpm")

    assert result["ok"] is False
    assert result["error_type"] == "no_midi"

"""detect_midi_genre (new API method) and create_beat's genre_override
plumbing (2026-07, Feature 1): the pre-generation confirm/override UI needs
a fast, side-effect-free genre check before "Create beat" is ever clicked,
and the user's confirmed/overridden pick must bypass automatic detection
entirely, including the DETAILED_GENRES gate."""

from __future__ import annotations

import mido
import pytest

import plg_api
from pattern_utils import parse_note_name


def _write_west_coast_midi(path):
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(96), time=0))
    for name in ["D3", "F3", "G3", "A3", "C4", "A3", "G3", "F3"]:
        note = parse_note_name(name)
        track.append(mido.Message("note_on", note=note, velocity=90, time=0))
        track.append(mido.Message("note_off", note=note, velocity=0, time=960))
    mid.save(str(path))


# --- detect_midi_genre ---------------------------------------------------------


def test_detect_midi_genre_no_path_is_a_validation_error():
    result = plg_api.detect_midi_genre("")
    assert result["ok"] is False
    assert result["error_type"] == "validation"


def test_detect_midi_genre_missing_file_is_not_found():
    result = plg_api.detect_midi_genre("/does/not/exist.mid")
    assert result["ok"] is False
    assert result["error_type"] == "not_found"


def test_detect_midi_genre_confident_hit(tmp_path):
    path = tmp_path / "upload.mid"
    _write_west_coast_midi(path)
    result = plg_api.detect_midi_genre(str(path))
    assert result["ok"] is True
    assert result["genre"] == "west_coast"
    assert result["confidence"] is not None
    assert "west_coast" in result["available_genres"]
    assert len(result["available_genres"]) == 10


def test_detect_midi_genre_inconclusive_returns_none_genre(tmp_path):
    path = tmp_path / "tiny.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    note = parse_note_name("C4")
    track.append(mido.Message("note_on", note=note, velocity=90, time=0))
    track.append(mido.Message("note_off", note=note, velocity=0, time=480))
    mid.save(str(path))

    result = plg_api.detect_midi_genre(str(path))
    assert result["ok"] is True
    assert result["genre"] is None
    assert result["confidence"] is None


def test_detect_midi_genre_malformed_file_never_errors(tmp_path):
    path = tmp_path / "garbage.mid"
    path.write_bytes(b"not a real midi file")
    result = plg_api.detect_midi_genre(str(path))
    assert result["ok"] is True
    assert result["genre"] is None


# --- create_beat's genre_override bypasses detection, including the gate ------


def test_genre_override_forwarded_to_run_pipeline(monkeypatch):
    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: False)
    monkeypatch.setattr(plg_api, "has_api_key", lambda: True)
    monkeypatch.setattr(plg_api, "ensure_can_consume_beat", lambda: None)
    monkeypatch.setattr(plg_api, "consume_beat", lambda: None)
    monkeypatch.setattr(plg_api, "has_available_midi_material", lambda *a, **kw: True)

    seen: dict = {}

    def _fake_run_pipeline(prompt, **kwargs):
        seen.update(kwargs)
        return {"bpm": 96, "style": "west_coast", "tracks": {}}

    monkeypatch.setattr(plg_api, "run_pipeline", _fake_run_pipeline)

    plg_api.create_beat("some ambiguous prompt", genre_override="west_coast")

    assert seen.get("genre_override") == "west_coast"


def test_genre_override_skips_detection_in_the_gate(monkeypatch):
    """A confirmed override must never re-trigger resolve_generation_genre —
    it's already a DETAILED_GENRES member by construction (the UI's own
    dropdown only ever offers those), so the gate should trust it directly."""
    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: False)
    monkeypatch.setattr(plg_api, "has_api_key", lambda: True)
    monkeypatch.setattr(plg_api, "ensure_can_consume_beat", lambda: None)
    monkeypatch.setattr(plg_api, "consume_beat", lambda: None)
    monkeypatch.setattr(plg_api, "has_available_midi_material", lambda *a, **kw: True)
    monkeypatch.setenv("PLG_GENRE_GATE", "on")

    def _boom(*_a, **_kw):
        raise AssertionError("resolve_generation_genre must not run when genre_override is set")

    import genre_brain

    monkeypatch.setattr(genre_brain, "resolve_generation_genre", _boom)
    monkeypatch.setattr(plg_api, "run_pipeline", lambda prompt, **kwargs: {"bpm": 96, "style": "west_coast", "tracks": {}})

    result = plg_api.create_beat("some ambiguous prompt", genre_override="west_coast")
    assert result["ok"] is True


def test_genre_override_still_gated_if_somehow_not_detailed(monkeypatch):
    """Defense in depth: even if a caller passes a non-DETAILED_GENRES
    override directly (bypassing the UI's own menu), the honest-stub gate
    still catches it rather than silently generating a half-tuned genre."""
    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: False)
    monkeypatch.setattr(plg_api, "has_api_key", lambda: True)
    monkeypatch.setattr(plg_api, "ensure_can_consume_beat", lambda: None)
    monkeypatch.setattr(plg_api, "has_available_midi_material", lambda *a, **kw: True)
    monkeypatch.setenv("PLG_GENRE_GATE", "on")

    result = plg_api.create_beat("some prompt", genre_override="techno")
    assert result["ok"] is False
    assert result["error_type"] == "genre_not_ready"

"""Tests for pattern / MIDI validation."""

from pathlib import Path

import math
import struct
import wave

from midi_validate import enforce_midi_mode, validate_export, validate_midi_mode, validate_pattern
import pytest


@pytest.fixture(autouse=True)
def _section_patterns(monkeypatch):
    """These tests describe the sectioned layout (one pattern per lane PER SECTION); the default is one pattern per channel
    (tests/test_whole_song_patterns.py)."""
    monkeypatch.setenv("PLG_PATTERN_SECTIONS", "on")


def _wav(path: Path, *, seconds: float, hz: float = 80.0) -> Path:
    rate = 8000
    frames = max(1, int(rate * seconds))
    payload = b"".join(
        struct.pack("<h", int(9000 * math.sin(2 * math.pi * hz * index / rate)))
        for index in range(frames)
    )
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(payload)
    return path


def test_validate_pattern_ok() -> None:
    pattern = {
        "bpm": 140,
        "tracks": {
            "hi_hats": [{"note": "C5", "time_step": 0.0, "length": 0.25, "velocity": 90}],
            "sub_808": [{"note": "C3", "time_step": 0.0, "length": 1.0, "velocity": 127}],
            "melody_lead": [],
        },
    }
    assert validate_pattern(pattern) == []


def test_validate_pattern_empty() -> None:
    warnings = validate_pattern({"bpm": 120, "tracks": {}})
    assert any("no notes" in item for item in warnings)


def test_validate_export_without_files() -> None:
    pattern = {
        "bpm": 140,
        "tracks": {
            "hi_hats": [{"note": "C5", "time_step": 0.0, "length": 0.25, "velocity": 90}],
            "sub_808": [],
            "melody_lead": [],
        },
    }
    report = validate_export(pattern, midi_dir=Path("_missing_midi"))
    assert report["ok"] is False


def test_validate_export_uses_serialized_stem_total_for_combined_midi(tmp_path, monkeypatch) -> None:
    """Same-pitch overlap cleanup may make disk count lower than raw JSON."""
    from midi_export import STEM_FILENAMES

    stem = tmp_path / STEM_FILENAMES["kick"]
    combined = tmp_path / "PLG_Beat.mid"
    stem.write_bytes(b"stem")
    combined.write_bytes(b"combined")
    calls = []

    def fake_validate(path, *, min_notes=1):
        calls.append((Path(path).name, min_notes))
        notes = 1  # exporter normalized two overlapping raw kick notes to one
        return {"path": str(path), "notes": notes, "ok": notes >= min_notes}

    monkeypatch.setattr("midi_validate.validate_midi_file", fake_validate)
    pattern = {
        "bpm": 140,
        "tracks": {
            "kick": [
                {"note": "C1", "time_step": 0.0, "length": 2.0, "velocity": 110},
                {"note": "C1", "time_step": 1.0, "length": 1.0, "velocity": 110},
            ]
        },
    }

    report = validate_export(pattern, midi_dir=tmp_path, combined=combined)

    assert report["ok"] is True
    assert ("PLG_Beat.mid", 1) in calls


def test_enforce_midi_mode_removes_legacy_lanes_and_accepts_clean_samples(tmp_path):
    kick = _wav(tmp_path / "REAL Kick.wav", seconds=0.12, hz=90)
    bass = _wav(tmp_path / "REAL 808 Bass.wav", seconds=0.8, hz=55)
    hat = _wav(tmp_path / "Closed Hat.wav", seconds=0.08, hz=3000)
    note = {"note": "C4", "time_step": 0.0, "length": 0.25, "velocity": 100}
    pattern = {
        "bpm": 140,
        "plg_midi_melody": True,
        "plg_total_bars": 40,
        "plg_arrangement": [
            {"name": "intro", "start_bar": 0, "bars": 4},
            {"name": "verse", "start_bar": 4, "bars": 8},
            {"name": "hook", "start_bar": 12, "bars": 8},
            {"name": "verse", "start_bar": 20, "bars": 8},
            {"name": "drop", "start_bar": 28, "bars": 8},
            {"name": "outro", "start_bar": 36, "bars": 4},
        ],
        "tracks": {
            "melody_lead": [dict(note)],
            "kick": [{**note, "time_step": 16.0}],
            "808_bass": [{**note, "time_step": 16.0, "note": "C2"}],
            "hi_hats": [{**note, "time_step": 16.5}],
            "clap": [{**note, "time_step": 17.0}],
            "snare": [{**note, "time_step": 17.0}],
            "snare_layer": [{**note, "time_step": 17.0}],
            "sub_808": [dict(note)],
            "counter_melody": [dict(note)],
            "fx": [dict(note)],
            "chords": [dict(note)],
            "texture": [dict(note)],
            "cowbell": [dict(note)],
        },
        "plg_sound_paths": {
            "kick": str(kick),
            "808_bass": str(bass),
            "hi_hats": str(hat),
            "sub_808": str(bass),
        },
        "plg_sample_picks": {
            "kick": kick.name,
            "808_bass": bass.name,
            "hi_hats": hat.name,
            "sub_808": bass.name,
        },
    }

    report = enforce_midi_mode(pattern)

    assert report["ok"] is True
    assert "sub_808" not in pattern["tracks"]
    assert "fx" not in pattern["tracks"]
    assert "sub_808" not in pattern["plg_sound_paths"]
    assert validate_midi_mode(pattern) == []
    from flp_template import active_export_channels, active_pattern_keys

    expected = [
        "melody_lead", "kick", "808_bass", "hi_hats", "clap", "snare", "snare_layer",
    ]
    assert active_export_channels(pattern) == expected
    assert active_pattern_keys(pattern) == expected
    assert sorted({clip["playlist_track"] for clip in pattern["plg_playlist_clips"]}) == list(range(7))


def test_direct_flp_preparation_cannot_ship_generated_midi_extras():
    """The FLP boundary enforces the same seven-lane MIDI contract."""
    from flp_template import active_export_channels, active_pattern_keys
    from flp_writer import prepare_pattern_for_flp_export

    note = {"note": "C4", "time_step": 0.0, "length": 0.25, "velocity": 100}
    approved = (
        "melody_lead", "kick", "808_bass", "hi_hats", "clap", "snare", "snare_layer",
    )
    data = {
        "bpm": 140,
        "plg_midi_melody": True,
        "tracks": {
            **{lane: [dict(note)] for lane in approved},
            **{lane: [dict(note)] for lane in ("sub_808", "counter_melody", "fx", "chords", "texture", "cowbell")},
        },
    }

    prepare_pattern_for_flp_export(data)

    assert tuple(data["tracks"]) == approved
    assert active_export_channels(data) == list(approved)
    assert active_pattern_keys(data) == list(approved)


def test_midi_validator_accepts_sectioned_clips_on_same_lane(tmp_path):
    kick = _wav(tmp_path / "REAL Kick.wav", seconds=0.12, hz=90)
    bass = _wav(tmp_path / "REAL 808.wav", seconds=0.8, hz=60)
    hat = _wav(tmp_path / "Closed Hat.wav", seconds=0.08, hz=3000)
    note = {"note": "C4", "time_step": 0.0, "length": 0.25, "velocity": 100}
    pattern = {
        "bpm": 140,
        "plg_midi_melody": True,
        "plg_total_bars": 20,
        "plg_arrangement": [
            {"name": name, "start_bar": index * 4, "bars": 4}
            for index, name in enumerate(("intro", "verse", "hook", "drop", "outro"))
        ],
        "tracks": {
            "melody_lead": [dict(note), {**note, "time_step": 33.0}],
            "kick": [{**note, "time_step": 16.0}, {**note, "time_step": 33.0}],
            "808_bass": [
                {**note, "time_step": 16.0, "length": 17.5, "note": "C2"},
                {**note, "time_step": 34.0, "note": "C2"},
            ],
            "hi_hats": [{**note, "time_step": 16.5}, {**note, "time_step": 33.5}],
        },
        "plg_sound_paths": {
            "kick": str(kick),
            "808_bass": str(bass),
            "hi_hats": str(hat),
        },
    }

    report = enforce_midi_mode(pattern)

    assert report["ok"] is True
    assert len([c for c in pattern["plg_playlist_clips"] if c["track_key"] == "melody_lead"]) == 2
    assert len({c["pattern_iid"] for c in pattern["plg_playlist_clips"]}) == len(pattern["plg_playlist_clips"])


def test_midi_validator_blocks_click_named_as_808(tmp_path):
    kick = _wav(tmp_path / "REAL Kick.wav", seconds=0.12, hz=90)
    fake_808 = _wav(tmp_path / "Finger Click.wav", seconds=0.8, hz=1000)
    hat = _wav(tmp_path / "Closed Hat.wav", seconds=0.08, hz=3000)
    note = {"note": "C4", "time_step": 0.0, "length": 0.25, "velocity": 100}
    pattern = {
        "bpm": 140,
        "plg_midi_melody": True,
        "plg_arrangement": [
            {"name": name, "start_bar": index * 4, "bars": 4}
            for index, name in enumerate(("intro", "verse", "hook", "drop", "outro"))
        ],
        "tracks": {
            "melody_lead": [dict(note)],
            "kick": [dict(note)],
            "808_bass": [{**note, "note": "C2"}],
            "hi_hats": [dict(note)],
        },
        "plg_sound_paths": {
            "kick": str(kick),
            "808_bass": str(fake_808),
            "hi_hats": str(hat),
        },
    }

    enforce_midi_mode(pattern, strict=False)

    assert pattern["plg_midi_validation"]["ok"] is False
    assert any("invalid 808_bass sample" in error for error in pattern["plg_midi_validation"]["errors"])

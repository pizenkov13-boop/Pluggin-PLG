"""Explicit user reference-audio upload wired end-to-end through
_humanize_and_ingest (audit 2026-07, follow-up to test_midi_upload_pipeline.py):
same call-site priority as the MIDI upload — wins over corpus/library/
reference auto-match, protects the transcribed melody, respects the
pattern's own bpm, and (opt-in) the melody's own detected scale."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from backend_core import _humanize_and_ingest


def _base_pattern() -> dict:
    return {
        "bpm": 145,
        "style": "trap",
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 90}],
        },
    }


_FAKE_TRANSCRIPTION = (
    [{"time_step": 0.0, "note": "D5", "length": 4.0, "velocity": 100}],
    [{"time_step": 0.0, "note": "D2", "length": 8.0, "velocity": 110}],
    {"demucs": False, "sources": []},
)


def test_uploaded_reference_audio_wins_over_library_tiers(tmp_path: Path):
    (tmp_path / "midi").mkdir()
    upload_path = tmp_path / "my_upload.wav"
    upload_path.write_bytes(b"RIFF" + b"\x00" * 64)

    pattern = _base_pattern()
    with (
        patch("reference_reverse.basic_pitch_available", return_value=True),
        patch("reference_reverse.transcribe_reference_stems", return_value=_FAKE_TRANSCRIPTION),
    ):
        result = _humanize_and_ingest(
            pattern,
            library_root=tmp_path,
            user_prompt="trap beat",
            style="trap",
            reference_audio_path=upload_path,
        )

    assert result["plg_reference_upload_result"]["ok"] is True
    # register normalization downstream in humanize_pattern may shift the
    # octave, so check pitch class (D), not the exact octave
    assert result["tracks"]["melody_lead"][0]["note"].startswith("D")
    assert result["plg_total_bars"] == 40  # MIDI-owned song form ran on top of it


def test_midi_path_wins_over_reference_audio_path_when_both_given(tmp_path: Path):
    import mido

    midi_path = tmp_path / "upload.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("note_on", note=64, velocity=100, time=0))
    track.append(mido.Message("note_off", note=64, velocity=0, time=480))
    mid.save(str(midi_path))

    audio_path = tmp_path / "upload.wav"
    audio_path.write_bytes(b"RIFF" + b"\x00" * 64)

    def _boom(*_a, **_kw):
        raise AssertionError("reference audio transcription must not run when midi_path wins")

    pattern = _base_pattern()
    with patch("reference_reverse.transcribe_reference_stems", side_effect=_boom):
        result = _humanize_and_ingest(
            pattern,
            library_root=tmp_path,
            user_prompt="trap beat",
            style="trap",
            midi_path=midi_path,
            reference_audio_path=audio_path,
        )

    assert result["plg_midi_upload_result"]["ok"] is True
    assert "plg_reference_upload_result" not in result


def test_no_upload_paths_keeps_existing_behavior(tmp_path: Path):
    pattern = _base_pattern()
    result = _humanize_and_ingest(
        pattern,
        library_root=tmp_path,
        user_prompt="trap beat",
        style="trap",
    )
    assert "plg_reference_upload_result" not in result
    assert "plg_midi_upload_result" not in result


def test_uploaded_reference_audio_respects_uploaded_scale_flows_through(tmp_path: Path):
    upload_path = tmp_path / "melody.wav"
    upload_path.write_bytes(b"RIFF" + b"\x00" * 64)

    degrees = ["C4", "D4", "E4", "F4", "G4", "A4", "B4"]
    notes = [
        {"time_step": float(i), "note": n, "length": 0.9, "velocity": 100}
        for i, n in enumerate(degrees)
    ]

    pattern = _base_pattern()
    pattern["scale"] = "phrygian"
    with (
        patch("reference_reverse.basic_pitch_available", return_value=True),
        patch(
            "reference_reverse.transcribe_reference_stems",
            return_value=(notes, [], {"demucs": False, "sources": []}),
        ),
    ):
        result = _humanize_and_ingest(
            pattern,
            library_root=tmp_path,
            user_prompt="trap beat",
            style="trap",
            reference_audio_path=upload_path,
            respect_uploaded_scale=True,
        )

    assert result["plg_uploaded_scale_lock"]["scale"] == "major"


def test_invalid_reference_upload_falls_back_to_library_tiers(tmp_path: Path):
    (tmp_path / "midi").mkdir()

    pattern = _base_pattern()
    result = _humanize_and_ingest(
        pattern,
        library_root=tmp_path,
        user_prompt="trap beat",
        style="trap",
        reference_audio_path=tmp_path / "does_not_exist.wav",
    )
    assert result["plg_reference_upload_result"]["ok"] is False
    # pipeline still completed (fell through to the rest of the ingest chain)
    assert result["plg_total_bars"] == 52

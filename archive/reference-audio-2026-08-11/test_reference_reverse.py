from pathlib import Path
from unittest.mock import patch

import mido

from midi_ingest import read_midi_notes
from reference_reverse import (
    UPLOAD_AUDIO_MAX_FILE_BYTES,
    UPLOAD_AUDIO_RIGHTS_WARNING,
    basic_pitch_available,
    demucs_reverse_enabled,
    estimate_bpm_from_audio,
    find_reference_audio,
    ingest_reference_audio,
    ingest_reference_from_file,
    ingest_uploaded_reference_audio,
    merge_stem_notes_into_pattern,
    stage_reference_audio,
    transcribe_reference_stems,
    wants_reference_reverse,
)


def _write_midi(path: Path, notes, ticks_per_beat: int = 480) -> None:
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    events = []
    for note, start, dur, vel in notes:
        events.append((start, "on", note, vel))
        events.append((start + dur, "off", note, 0))
    events.sort(key=lambda e: e[0])
    last = 0
    for tick, kind, note, vel in events:
        delta = tick - last
        last = tick
        msg_type = "note_on" if kind == "on" else "note_off"
        track.append(mido.Message(msg_type, note=note, velocity=vel, time=delta))
    mid.save(str(path))


def test_wants_reference_reverse_triggers():
    assert wants_reference_reverse("make beat like the reference snippet", "")
    assert wants_reference_reverse("сделай как в референсе", "")
    assert not wants_reference_reverse("dark trap beat", "")


def test_find_reference_in_references_folder(tmp_path: Path):
    (tmp_path / "references").mkdir()
    wav = tmp_path / "references" / "hook.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 64)
    found = find_reference_audio(tmp_path, prompt="dark trap", style="")
    assert found == wav.resolve()


def test_ingest_reference_mocks_transcribe(tmp_path: Path, monkeypatch):
    (tmp_path / "references").mkdir()
    wav = tmp_path / "references" / "ref_hook.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 64)

    midi = tmp_path / "cache.mid"
    _write_midi(midi, [(60, 0, 480, 100), (36, 0, 960, 110)])

    pattern = {
        "bpm": 140,
        "style": "rage",
        "tracks": {"melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 90}]},
    }

    with (
        patch("reference_reverse.basic_pitch_available", return_value=True),
        patch(
            "reference_reverse.transcribe_reference_stems",
            return_value=(
                [{"time_step": 0.0, "note": "C5", "length": 4.0, "velocity": 100}],
                [{"time_step": 0.0, "note": "C2", "length": 8.0, "velocity": 110}],
                {"demucs": False, "sources": []},
            ),
        ),
    ):
        ok = ingest_reference_audio(
            pattern,
            library_root=tmp_path,
            prompt="copy melody from reference snippet",
            style="rage",
        )

    assert ok is True
    assert pattern["plg_reference_reverse"]["name"] == "ref_hook.wav"
    assert len(pattern["tracks"]["melody_lead"]) == 1
    assert pattern["tracks"]["melody_lead"][0]["note"] == "C5"
    assert len(pattern["tracks"]["sub_808"]) == 1


def test_ingest_skips_without_trigger(tmp_path: Path):
    (tmp_path / "kits").mkdir()
    (tmp_path / "kits" / "loop.wav").write_bytes(b"RIFF" + b"\x00" * 64)
    pattern = {"bpm": 120, "tracks": {}}
    with patch("reference_reverse.basic_pitch_available", return_value=True):
        ok = ingest_reference_audio(
            pattern,
            library_root=tmp_path,
            prompt="generic trap",
            style="",
        )
    assert ok is False


def test_basic_pitch_disabled(monkeypatch):
    monkeypatch.setenv("PLG_USE_BASIC_PITCH", "0")
    assert basic_pitch_available() is False


def test_demucs_reverse_disabled(monkeypatch):
    monkeypatch.setenv("PLG_USE_DEMUCS_REVERSE", "0")
    assert demucs_reverse_enabled() is False


def test_transcribe_reference_stems_demucs_path(tmp_path: Path):
    audio = tmp_path / "hook.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 64)
    bass_wav = tmp_path / "bass.wav"
    other_wav = tmp_path / "other.wav"
    bass_wav.write_bytes(b"RIFF" + b"\x01" * 64)
    other_wav.write_bytes(b"RIFF" + b"\x02" * 64)
    bass_midi = tmp_path / "bass.mid"
    other_midi = tmp_path / "other.mid"
    _write_midi(bass_midi, [(36, 0, 960, 110)])
    _write_midi(other_midi, [(60, 0, 480, 100)])

    with (
        patch("reference_reverse.demucs_reverse_enabled", return_value=True),
        patch(
            "reference_reverse.split_reference_stems",
            return_value={"bass": bass_wav, "other": other_wav},
        ),
        patch(
            "reference_reverse.transcribe_audio_to_midi",
            side_effect=[bass_midi, other_midi],
        ),
    ):
        melody, bass, meta = transcribe_reference_stems(audio, bpm=140)

    assert meta["demucs"] is True
    assert len(meta["sources"]) == 2
    assert len(melody) == 1
    assert melody[0]["note"] == "C5"
    assert len(bass) == 1
    assert bass[0]["note"] == "C3"


def test_ingest_reference_demucs_chain(tmp_path: Path):
    (tmp_path / "references").mkdir()
    wav = tmp_path / "references" / "ref_hook.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 64)
    pattern = {
        "bpm": 140,
        "style": "rage",
        "tracks": {"melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 90}]},
    }

    with (
        patch("reference_reverse.basic_pitch_available", return_value=True),
        patch(
            "reference_reverse.transcribe_reference_stems",
            return_value=(
                [{"time_step": 0.0, "note": "D5", "length": 4.0, "velocity": 100}],
                [{"time_step": 0.0, "note": "G1", "length": 8.0, "velocity": 110}],
                {
                    "demucs": True,
                    "sources": [
                        {"label": "bass", "notes": 1},
                        {"label": "melody", "notes": 1},
                    ],
                },
            ),
        ),
    ):
        ok = ingest_reference_audio(
            pattern,
            library_root=tmp_path,
            prompt="copy melody from reference snippet",
            style="rage",
        )

    assert ok is True
    assert pattern["plg_reference_reverse"]["transcriber"] == "demucs+basic-pitch"
    assert pattern["plg_reference_reverse"]["demucs"] is True
    assert pattern["tracks"]["melody_lead"][0]["note"] == "D5"
    assert pattern["tracks"]["sub_808"][0]["note"] == "G1"


def test_ingest_reference_audio_protects_melody_flag(tmp_path: Path):
    (tmp_path / "references").mkdir()
    wav = tmp_path / "references" / "ref_hook.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 64)
    pattern = {"bpm": 140, "style": "rage", "tracks": {}}

    with (
        patch("reference_reverse.basic_pitch_available", return_value=True),
        patch(
            "reference_reverse.transcribe_reference_stems",
            return_value=(
                [{"time_step": 0.0, "note": "C5", "length": 4.0, "velocity": 100}],
                [],
                {"demucs": False, "sources": []},
            ),
        ),
    ):
        ok = ingest_reference_audio(
            pattern,
            library_root=tmp_path,
            prompt="copy melody from reference snippet",
            style="rage",
        )

    assert ok is True
    assert pattern["plg_midi_melody"] is True
    # Scope boundary (2026-07 product decision): the library-keyword-matched
    # AUTO tier keeps its own variation — melody-preservation is only ever
    # set for the two EXPLICIT, user-pointed-at-one-file paths.
    assert "plg_preserve_melody_sections" not in pattern


def test_merge_stem_notes_preserves_existing_808(tmp_path: Path):
    pattern = {
        "tracks": {
            "sub_808": [{"time_step": 0.0, "note": "E1", "length": 4.0, "velocity": 100}],
        },
    }
    ok = merge_stem_notes_into_pattern(
        pattern,
        [{"time_step": 0.0, "note": "C5", "length": 4.0, "velocity": 90}],
        [{"time_step": 0.0, "note": "C2", "length": 8.0, "velocity": 110}],
        source="ref.wav",
        name="ref.wav",
    )
    assert ok is True
    assert pattern["tracks"]["melody_lead"][0]["note"] == "C5"
    assert pattern["tracks"]["sub_808"][0]["note"] == "E1"


def test_stage_reference_audio_copies_to_library(tmp_path: Path):
    src = tmp_path / "my_ref.mp3"
    src.write_bytes(b"ID3" + b"\x00" * 32)
    staged = stage_reference_audio(src, tmp_path)
    assert staged.is_file()
    assert staged.parent.name == "references"
    assert staged.name == "my_ref.mp3"


def test_ingest_reference_from_file_sets_bpm(tmp_path: Path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 64)
    pattern = {"bpm": 120, "tracks": {}}

    with (
        patch("reference_reverse.basic_pitch_available", return_value=True),
        patch("reference_reverse.estimate_bpm_from_audio", return_value=152.0),
        patch(
            "reference_reverse.transcribe_reference_stems",
            return_value=(
                [{"time_step": 0.0, "note": "A4", "length": 2.0, "velocity": 100}],
                [],
                {"demucs": False, "sources": []},
            ),
        ),
    ):
        meta = ingest_reference_from_file(pattern, audio)

    assert meta is not None
    assert pattern["bpm"] == 152.0
    assert pattern["tracks"]["melody_lead"][0]["note"] == "A4"


def test_estimate_bpm_returns_default_without_audio(tmp_path: Path):
    missing = tmp_path / "nope.wav"
    assert estimate_bpm_from_audio(missing) == 140.0


# --- ingest_reference_from_file: new protections (audit 2026-07) ----------


def test_ingest_reference_from_file_protects_melody_flag(tmp_path: Path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 64)
    pattern = {"bpm": 140, "tracks": {}}

    with (
        patch("reference_reverse.basic_pitch_available", return_value=True),
        patch(
            "reference_reverse.transcribe_reference_stems",
            return_value=(
                [{"time_step": 0.0, "note": "A4", "length": 2.0, "velocity": 100}],
                [],
                {"demucs": False, "sources": []},
            ),
        ),
    ):
        meta = ingest_reference_from_file(pattern, audio, bpm=140.0)

    assert meta is not None
    assert pattern["plg_midi_melody"] is True
    # 2026-07 product decision: both callers of this function (apply_
    # reference_track's user-picked clip, ingest_uploaded_reference_audio)
    # are explicit uploads — melody must be exempt from section transforms.
    assert pattern["plg_preserve_melody_sections"] is True


def test_ingest_reference_from_file_explicit_bpm_not_overwritten(tmp_path: Path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 64)
    pattern = {"bpm": 150, "tracks": {}}

    with (
        patch("reference_reverse.basic_pitch_available", return_value=True),
        patch("reference_reverse.estimate_bpm_from_audio") as mock_estimate,
        patch(
            "reference_reverse.transcribe_reference_stems",
            return_value=(
                [{"time_step": 0.0, "note": "A4", "length": 2.0, "velocity": 100}],
                [],
                {"demucs": False, "sources": []},
            ),
        ),
    ):
        meta = ingest_reference_from_file(pattern, audio, bpm=150.0)

    mock_estimate.assert_not_called()
    assert meta["bpm"] == 150.0
    assert pattern["bpm"] == 150.0


def test_ingest_reference_from_file_respect_uploaded_scale(tmp_path: Path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 64)
    pattern = {"bpm": 140, "scale": "phrygian", "tracks": {}}

    degrees = ["C4", "D4", "E4", "F4", "G4", "A4", "B4"]
    notes = [{"time_step": float(i), "note": n, "length": 0.9, "velocity": 100} for i, n in enumerate(degrees)]

    with (
        patch("reference_reverse.basic_pitch_available", return_value=True),
        patch(
            "reference_reverse.transcribe_reference_stems",
            return_value=(notes, [], {"demucs": False, "sources": []}),
        ),
    ):
        ingest_reference_from_file(pattern, audio, bpm=140.0, respect_uploaded_scale=True)

    assert pattern["scale"] == "major"
    assert pattern["plg_uploaded_scale_lock"]["scale"] == "major"


def test_ingest_reference_from_file_scale_untouched_by_default(tmp_path: Path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 64)
    pattern = {"bpm": 140, "scale": "phrygian", "tracks": {}}

    with (
        patch("reference_reverse.basic_pitch_available", return_value=True),
        patch(
            "reference_reverse.transcribe_reference_stems",
            return_value=(
                [{"time_step": 0.0, "note": "A4", "length": 2.0, "velocity": 100}],
                [],
                {"demucs": False, "sources": []},
            ),
        ),
    ):
        ingest_reference_from_file(pattern, audio, bpm=140.0)

    assert pattern["scale"] == "phrygian"
    assert "plg_uploaded_scale_lock" not in pattern


# --- ingest_uploaded_reference_audio: explicit upload wrapper -------------


def test_uploaded_reference_audio_rejects_unsupported_extension(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    pattern = {"bpm": 140, "tracks": {}}
    result = ingest_uploaded_reference_audio(pattern, path)
    assert result["ok"] is False
    assert result["warning"]


def test_uploaded_reference_audio_rejects_missing_file(tmp_path: Path):
    pattern = {"bpm": 140, "tracks": {}}
    result = ingest_uploaded_reference_audio(pattern, tmp_path / "missing.wav")
    assert result["ok"] is False
    assert result["warning"]


def test_uploaded_reference_audio_rejects_oversized_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("reference_reverse.UPLOAD_AUDIO_MAX_FILE_BYTES", 10)
    path = tmp_path / "clip.wav"
    path.write_bytes(b"RIFF" + b"\x00" * 64)
    pattern = {"bpm": 140, "tracks": {}}
    result = ingest_uploaded_reference_audio(pattern, path)
    assert result["ok"] is False
    assert "too large" in result["warning"]


def test_uploaded_reference_audio_missing_basic_pitch(tmp_path: Path):
    path = tmp_path / "clip.wav"
    path.write_bytes(b"RIFF" + b"\x00" * 64)
    pattern = {"bpm": 140, "tracks": {}}
    with patch("reference_reverse.basic_pitch_available", return_value=False):
        result = ingest_uploaded_reference_audio(pattern, path)
    assert result["ok"] is False
    assert "basic-pitch" in result["warning"]


def test_uploaded_reference_audio_success(tmp_path: Path):
    path = tmp_path / "my_melody.wav"
    path.write_bytes(b"RIFF" + b"\x00" * 64)
    pattern = {"bpm": 145, "tracks": {}}

    with (
        patch("reference_reverse.basic_pitch_available", return_value=True),
        patch(
            "reference_reverse.transcribe_reference_stems",
            return_value=(
                [{"time_step": 0.0, "note": "C5", "length": 4.0, "velocity": 100}],
                [{"time_step": 0.0, "note": "C2", "length": 8.0, "velocity": 110}],
                {"demucs": False, "sources": []},
            ),
        ) as mock_transcribe,
    ):
        result = ingest_uploaded_reference_audio(pattern, path)

    assert result["ok"] is True
    assert result["notes"] == 2
    assert result["warning"] == UPLOAD_AUDIO_RIGHTS_WARNING
    assert pattern["tracks"]["melody_lead"][0]["note"] == "C5"
    assert pattern["tracks"]["sub_808"][0]["note"] == "C2"
    assert pattern["plg_midi_melody"] is True
    assert pattern["plg_reference_uploaded"] is True
    # the pattern's own bpm (145) was passed through explicitly, not
    # auto-detected -- the user's requested tempo is never overwritten here
    assert pattern["bpm"] == 145
    assert mock_transcribe.call_args.kwargs.get("bpm") == 145.0

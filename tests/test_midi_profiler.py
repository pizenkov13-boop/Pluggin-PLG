from pathlib import Path

import mido

from midi_profiler import profile_midi_file


def _write_note_only_midi(path: Path) -> None:
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("note_on", note=60, velocity=100, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    track.append(mido.Message("note_on", note=64, velocity=100, time=480))
    track.append(mido.Message("note_off", note=64, velocity=0, time=480))
    mid.save(path)


def test_profiler_returns_read_only_passport_and_filename_tempo(tmp_path: Path):
    source = tmp_path / "Pluck_150_BPM_Cmin.mid"
    _write_note_only_midi(source)

    profile = profile_midi_file(source)

    assert profile["read_only"] is True
    assert profile["tempo"]["bpm"] == 150.0
    assert profile["tempo"]["source"] == "filename_bpm"
    assert profile["tempo"]["filename_bpm"] == 150.0
    assert profile["key"]["root_pc"] is not None
    assert profile["role"]["musical_role"] == "melodic_phrase"
    assert profile["structure"]["status"] == "ok"
    assert profile["instrument"]["declared_family"] == "unknown"
    assert profile["safety"]["tempo_reliable"] is True

import json
from pathlib import Path

import mido

from midi_export import (
    STEM_FILENAMES,
    export_combined_midi,
    export_pattern_to_midi,
    export_stem_session,
)


def test_stem_export_friendly_names(tmp_path: Path):
    data = {
        "bpm": 140,
        "style": "trap",
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 110}],
            "snare": [{"time_step": 1.0, "note": "D1", "length": 0.25, "velocity": 110}],
            "hi_hats": [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 100}],
            "sub_808": [{"time_step": 0.0, "note": "C2", "length": 1.0, "velocity": 127}],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 1.0, "velocity": 90}],
        },
    }
    session = export_stem_session(data, tmp_path)
    names = {p.name for p in session["stem_paths"]}
    assert "Kick.mid" in names
    assert "808_Bass.mid" in names
    assert "HiHats.mid" in names
    assert "Melody.mid" in names
    assert (session["session_dir"] / "PLG_Beat.mid").is_file()
    manifest = json.loads((session["session_dir"] / "stems_manifest.json").read_text())
    assert manifest["bpm"] == 140


def test_stem_filenames_cover_drum_tracks():
    assert STEM_FILENAMES["sub_808"] == "808_Bass.mid"
    assert STEM_FILENAMES["hi_hats"] == "HiHats.mid"


def test_export_overlapping_same_pitch_does_not_crash(tmp_path: Path):
    data = {
        "bpm": 145,
        "tracks": {
            "sub_808": [
                {"time_step": 0.0, "note": "C2", "length": 4.0, "velocity": 127},
                {"time_step": 1.0, "note": "C2", "length": 4.0, "velocity": 120},
                {"time_step": 2.0, "note": "C2", "length": 0.5, "velocity": 110},
            ],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 8.0, "velocity": 90}],
        },
        "pitch_bend_automation": [
            {"time_step": 7.5, "track": "melody_lead", "value": 8192},
            {"time_step": 7.92, "track": "melody_lead", "value": 0},
            {"time_step": 8.0, "track": "melody_lead", "value": 8192},
        ],
    }
    session = export_stem_session(data, tmp_path)
    assert session["combined_path"].is_file()
    assert all(p.stat().st_size > 0 for p in session["stem_paths"])


def test_export_with_pitch_bends_writes(tmp_path: Path):
    # Regression: pitch_bend_automation must serialize (addPitchWheelEvent arg
    # order was track/channel/time — a float time landed in the channel slot and
    # crashed writeFile with "unsupported operand type(s) for |: 'int' and 'float'").
    data = {
        "bpm": 144,
        "tracks": {"melody_lead": [{"time_step": 0.0, "note": "A4", "length": 32.0, "velocity": 90}]},
        "pitch_bend_automation": [
            {"time_step": 31.5, "track": "melody_lead", "value": 8192},
            {"time_step": 31.92, "track": "melody_lead", "value": 0},
            {"time_step": 32.0, "track": "melody_lead", "value": 8192},
        ],
    }
    stems = export_pattern_to_midi(data, tmp_path, friendly_names=True)
    assert any(p.name == "Melody.mid" and p.stat().st_size > 0 for p in stems)
    combined = export_combined_midi(data, tmp_path / "PLG_Beat.mid")
    assert combined.is_file() and combined.stat().st_size > 0


def test_counter_melody_pitch_bends_reach_its_own_stem(tmp_path: Path):
    # Regression: bends used to be hardcoded to melody_lead only, so a glide on
    # counter_melody (Dirkey "How To Make UNLIMITED Melodies" chord-change
    # glide) was silently dropped from every .mid file.
    data = {
        "bpm": 140,
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 8.0, "velocity": 90}],
            "counter_melody": [{"time_step": 0.0, "note": "E5", "length": 4.0, "velocity": 80}],
        },
        "pitch_bend_automation": [
            {"time_step": 3.5, "track": "counter_melody", "value": 8192},
            {"time_step": 3.98, "track": "counter_melody", "value": 16383},
            {"time_step": 4.02, "track": "counter_melody", "value": 8192},
        ],
    }
    stems = export_pattern_to_midi(data, tmp_path, friendly_names=True)
    counter_path = next(p for p in stems if p.name == STEM_FILENAMES["counter_melody"])
    mid = mido.MidiFile(str(counter_path))
    bend_values = [
        msg.pitch for track in mid.tracks for msg in track if msg.type == "pitchwheel"
    ]
    assert 16383 - 8192 in bend_values  # the +2 st bend made it into counter_melody's own file

    melody_path = next(p for p in stems if p.name == "Melody.mid")
    melody_mid = mido.MidiFile(str(melody_path))
    melody_bends = [
        msg for track in melody_mid.tracks for msg in track if msg.type == "pitchwheel"
    ]
    assert melody_bends == []  # counter_melody's bend must not leak into melody_lead

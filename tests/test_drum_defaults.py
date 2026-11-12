from drum_defaults import ensure_drum_tracks


def test_ensure_drum_tracks_adds_kick_snare_clap():
    pattern = {
        "bpm": 140,
        "tracks": {
            "hi_hats": [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 90}],
            "sub_808": [{"time_step": 0.0, "note": "C2", "length": 2.0, "velocity": 127}],
            "melody_lead": [],
        },
    }
    ensure_drum_tracks(pattern)
    tracks = pattern["tracks"]
    assert len(tracks["kick"]) >= 2
    assert len(tracks["snare"]) >= 2
    assert len(tracks["clap"]) >= 2


def test_ensure_adds_continuous_hats_and_beat3_clap():
    pattern = {"bpm": 140, "tracks": {"melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 90}]}}
    ensure_drum_tracks(pattern)
    tracks = pattern["tracks"]
    assert len(tracks["hi_hats"]) >= 8  # continuous 1/8 drive added
    # Half-time clap: every hit lands on beat 3 of its bar.
    assert all(abs((n["time_step"] % 4.0) - 2.0) < 1e-6 for n in tracks["clap"])

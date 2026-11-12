from validator import validate_and_sanitize_pattern


def test_sanitize_clamps_velocity():
    pattern = {
        "bpm": 140,
        "tracks": {
            "kick": [
                {"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 999},
            ],
        },
    }
    out, report = validate_and_sanitize_pattern(pattern)
    assert out["tracks"]["kick"][0]["velocity"] == 127
    assert report.fixes >= 1
    assert report.ok


def test_sanitize_trims_same_pitch_overlap():
    pattern = {
        "bpm": 140,
        "tracks": {
            "hi_hats": [
                {"time_step": 0.0, "note": "C5", "length": 0.5, "velocity": 90},
                {"time_step": 0.25, "note": "C5", "length": 0.5, "velocity": 100},
            ],
        },
    }
    out, report = validate_and_sanitize_pattern(pattern)
    notes = out["tracks"]["hi_hats"]
    assert len(notes) == 2
    assert report.fixes >= 1

"""Anti-clone guards — beats must not repeat recent grids or kits."""

from __future__ import annotations

from generation_variety import (
    append_beat_history,
    grid_signature,
    is_too_similar,
    load_beat_history,
)


def test_grid_signature_changes_with_kick():
    a = {
        "bpm": 140,
        "key": "F",
        "tracks": {"kick": [{"time_step": 0.0, "note": "C5", "length": 0.3, "velocity": 110}]},
        "plg_sample_picks": {"kick": "kick_a.wav"},
    }
    b = {
        "bpm": 140,
        "key": "F",
        "tracks": {"kick": [{"time_step": 2.0, "note": "C5", "length": 0.3, "velocity": 110}]},
        "plg_sample_picks": {"kick": "kick_b.wav"},
    }
    assert grid_signature(a) != grid_signature(b)


def test_is_too_similar_detects_clone(tmp_path, monkeypatch):
    hist_file = tmp_path / "beat_history.jsonl"
    monkeypatch.setattr("generation_variety._HISTORY_FILE", hist_file)
    pattern = {
        "bpm": 140,
        "key": "F",
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C5", "length": 0.3, "velocity": 110}],
            "snare": [{"time_step": 2.0, "note": "D5", "length": 0.3, "velocity": 100}],
        },
        "plg_sample_picks": {"kick": "kick_a.wav", "snare": "snare_a.wav"},
    }
    append_beat_history(pattern)
    assert is_too_similar(pattern)[0]


def test_history_roundtrip(tmp_path, monkeypatch):
    hist_file = tmp_path / "beat_history.jsonl"
    monkeypatch.setattr("generation_variety._HISTORY_FILE", hist_file)
    pattern = {
        "bpm": 132,
        "key": "G",
        "tracks": {"kick": [{"time_step": 1.0, "note": "C5", "length": 0.3, "velocity": 110}]},
        "plg_sample_picks": {"kick": "unique_kick.wav"},
    }
    append_beat_history(pattern)
    rows = load_beat_history()
    assert len(rows) == 1
    assert rows[0]["samples"] == ["unique_kick.wav"]

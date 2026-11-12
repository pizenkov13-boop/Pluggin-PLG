"""API tests for beat rating."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import plg_api


def test_record_beat_rating_no_beat(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(plg_api, "PATTERN_JSON", tmp_path / "missing.json")
    out = plg_api.record_beat_rating(1)
    assert out["ok"] is False


def test_record_beat_rating_ok(tmp_path: Path, monkeypatch):
    pattern = {
        "bpm": 140,
        "style": "rage",
        "tracks": {"kick": [{"time_step": 0.0, "note": "C1", "length": 0.4, "velocity": 110}]},
        "plg_beat_id": "abc123",
        "plg_quality": {"score": 72, "genre": "rage"},
    }
    path = tmp_path / "output_pattern.json"
    path.write_text(json.dumps(pattern), encoding="utf-8")
    monkeypatch.setattr(plg_api, "PATTERN_JSON", path)

    with (
        patch("beat_reward.reward_enabled", return_value=True),
        patch(
            "beat_reward.record_rating",
            return_value={"rating": 1, "total_ratings": 1, "model_trained": False},
        ),
        patch("beat_reward.model_status", return_value={"model_ready": False, "ratings": 1}),
    ):
        out = plg_api.record_beat_rating(1)

    assert out["ok"] is True
    assert out["rating"] == 1
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["plg_rating"] == 1

from __future__ import annotations

import json
from unittest.mock import MagicMock

from beat_data_snapshot import build_beat_sync_payload
from cloud.app.beat_ranker import build_global_reward_snapshot


def _pattern() -> dict:
    return {
        "plg_beat_id": "beat123",
        "bpm": 145,
        "style": "rage",
        "user_prompt": "dark rage beat",
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 110}],
            "hi_hats": [{"time_step": 0.5, "note": "C5", "length": 0.1, "velocity": 80}],
        },
        "samples": [{"path": r"C:\Users\me\secret\kick.wav"}],
        "plg_sound_paths": {"kick": r"C:\Users\me\secret\kick.wav"},
        "plg_sample_picks": {"kick": r"C:\Users\me\secret\kick.wav"},
        "sample_library": r"C:\Users\me\secret",
        "plg_quality": {"score": 72, "proxy_score": 70, "genre": "rage", "passed": True},
        "plg_pipeline": {"mode": "titans", "composer": "claude"},
    }


def test_beat_sync_payload_sanitizes_paths():
    payload = build_beat_sync_payload(_pattern(), prompt="dark rage beat", model="test-model")

    assert payload["beat_id"] == "beat123"
    assert payload["prompt_hash"]
    assert payload["sample_picks"] == {"kick": "kick.wav"}
    dumped = json.dumps(payload["pattern"])
    assert "C:\\Users\\me\\secret" not in dumped
    assert "kick.wav" in dumped
    assert payload["features"]["note_count"] == 2


def test_global_reward_snapshot_feature_weights():
    client = MagicMock()
    query = client.table.return_value.select.return_value.limit.return_value
    query.execute.return_value = MagicMock(
        data=[
            {"rating": 1, "genre": "rage", "features": {"density": 0.8, "proxy_score": 0.9}},
            {"rating": -1, "genre": "rage", "features": {"density": 0.2, "proxy_score": 0.4}},
        ]
    )

    out = build_global_reward_snapshot(client, min_samples=2)

    assert out["ok"] is True
    assert out["model_ready"] is True
    assert out["feature_weights"]["density"] > 0
    assert out["top_positive_features"][0]["feature"] in {"density", "proxy_score"}

"""Fleet learning — every user's likes teach every client (and the corpus)."""

from __future__ import annotations

from typing import Any

import pytest

import beat_reward
from pattern_features import FEATURE_ORDER


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(beat_reward, "_data_dir", lambda: tmp_path)
    monkeypatch.delenv("PLG_FLEET_REWARD", raising=False)


def _pattern() -> dict[str, Any]:
    return {
        "bpm": 142,
        "user_prompt": "dark trap",
        "plg_variety": {"genre": "trap"},
        "tracks": {
            "kick": [{"time_step": float(i), "note": "C1", "length": 0.3, "velocity": 110} for i in range(8)],
            "hi_hats": [{"time_step": i * 0.5, "note": "F#5", "length": 0.2, "velocity": 80 + (i % 3) * 10} for i in range(16)],
            "melody_lead": [{"time_step": float(i), "note": n, "length": 0.8, "velocity": 96}
                            for i, n in enumerate(("F5", "G#5", "C6", "D#5"))],
        },
    }


def _snapshot(weights: dict[str, float], **extra: Any) -> dict[str, Any]:
    return {"ok": True, "model_ready": True, "feature_weights": weights, **extra}


def test_fleet_score_guides_cold_start(monkeypatch):
    # no local sklearn model at all → the fleet prior scores the beat
    weights = {name: 1.0 for name in FEATURE_ORDER}
    monkeypatch.setattr(beat_reward, "fleet_snapshot", lambda genre=None: _snapshot(weights))
    monkeypatch.setattr(beat_reward, "_load_model", lambda: None)
    score = beat_reward.predict_reward_score(_pattern())
    assert score is not None
    assert 0.0 <= score <= 100.0


def test_local_model_beats_fleet(monkeypatch):
    class FakeModel:
        def predict_proba(self, vec):
            return [[0.1, 0.9]]

    monkeypatch.setenv("PLG_USE_BEAT_REWARD", "1")  # conftest defaults it to 0
    monkeypatch.setattr(beat_reward, "_load_model", lambda: (FakeModel(), {"samples": 99}))
    monkeypatch.setattr(beat_reward, "_sklearn_available", lambda: True)
    monkeypatch.setattr(
        beat_reward, "fleet_snapshot",
        lambda genre=None: pytest.fail("fleet must not be consulted when local model is trained"),
    )
    assert beat_reward.predict_reward_score(_pattern()) == 90.0


def test_fleet_kill_switch(monkeypatch):
    monkeypatch.setenv("PLG_FLEET_REWARD", "off")
    monkeypatch.setattr(beat_reward, "_load_model", lambda: None)
    assert beat_reward.predict_reward_score(_pattern()) is None


def test_snapshot_cached_after_first_fetch(monkeypatch):
    calls: list[str | None] = []

    def fake_status(genre=None):
        calls.append(genre)
        return _snapshot({"note_count": 1.0})

    import plg_cloud

    monkeypatch.setattr(plg_cloud, "cloud_reward_status", fake_status)
    first = beat_reward.fleet_snapshot("trap")
    second = beat_reward.fleet_snapshot("trap")
    assert first == second
    assert calls == ["trap"]  # second hit served from the local cache


def test_offline_negative_cache(monkeypatch):
    calls: list[int] = []

    def boom(genre=None):
        calls.append(1)
        raise RuntimeError("offline")

    import plg_cloud

    monkeypatch.setattr(plg_cloud, "cloud_reward_status", boom)
    assert beat_reward.fleet_snapshot("trap") is None
    assert beat_reward.fleet_snapshot("trap") is None
    assert len(calls) == 1  # offline result cached — no hammering


def test_progression_stats_fall_back_to_global(monkeypatch):
    def snap(genre=None):
        if genre == "trap":
            return {"ok": True, "model_ready": False, "progression_stats": {}}
        return {
            "ok": True,
            "model_ready": True,
            "progression_stats": {"i–VI–III–VII": {"up": 9, "down": 1, "votes": 10, "win_rate": 0.9}},
        }

    monkeypatch.setattr(beat_reward, "fleet_snapshot", snap)
    stats = beat_reward.global_progression_stats("trap")
    assert "i–VI–III–VII" in stats


# --- corpus bias -----------------------------------------------------------------


def test_fleet_boost_tilts_progression_pick(monkeypatch):
    import style_corpus

    winner = "i–VII–VI–VII"  # низкий базовый вес в trap-банке
    monkeypatch.setattr(
        style_corpus, "_fleet_progression_boost",
        lambda genre: {winner: 6.0},
    )
    picks = {
        style_corpus.style_dna("trap", key="F", scale="minor", seed=seed)["progression"]["name"]
        for seed in range(40)
    }
    assert winner in picks

    counts = sum(
        1 for seed in range(60)
        if style_corpus.style_dna("trap", key="F", scale="minor", seed=seed)["progression"]["name"] == winner
    )
    assert counts >= 15  # boosted well above its natural share


def test_dna_deterministic_with_stable_boost(monkeypatch):
    import style_corpus

    monkeypatch.setattr(style_corpus, "_fleet_progression_boost", lambda genre: {"i–VI–VII–i": 1.3})
    a = style_corpus.style_dna("trap", key="F", scale="minor", seed=77)
    b = style_corpus.style_dna("trap", key="F", scale="minor", seed=77)
    assert a == b


# --- data fields -----------------------------------------------------------------


def test_feature_snapshot_carries_progression_name():
    from beat_data_snapshot import build_feature_snapshot

    pattern = _pattern()
    pattern["plg_progression"] = {"name": "i–VI–III–VII", "roots_pc": [5, 5, 1, 1]}
    pattern["plg_quality"] = {"genre": "trap"}
    snap = build_feature_snapshot(pattern)
    assert snap["dna_progression"] == "i–VI–III–VII"
    assert snap["dna_genre"] == "trap"


def test_ranker_aggregates_progression_win_rates():
    from cloud.app.beat_ranker import build_global_reward_snapshot

    rows = [
        {"rating": 1, "genre": "trap", "features": {"dna_progression": "i–VI–III–VII", "note_count": 100}},
        {"rating": 1, "genre": "trap", "features": {"dna_progression": "i–VI–III–VII", "note_count": 120}},
        {"rating": -1, "genre": "trap", "features": {"dna_progression": "i–VII–VI–VII", "note_count": 80}},
    ]

    class _Resp:
        data = rows

    class _Query:
        def select(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def execute(self):
            return _Resp()

        def insert(self, *a, **k):
            return self

    class _Client:
        def table(self, name):
            return _Query()

    snap = build_global_reward_snapshot(_Client(), min_samples=2)
    stats = snap["progression_stats"]
    assert stats["i–VI–III–VII"]["up"] == 2
    assert stats["i–VI–III–VII"]["win_rate"] == 1.0
    assert stats["i–VII–VI–VII"]["down"] == 1

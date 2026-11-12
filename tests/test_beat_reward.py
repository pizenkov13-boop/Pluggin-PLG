"""Tests for local beat reward learning."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from beat_quality import evaluate
from beat_reward import (
    load_ratings,
    min_train_samples,
    model_status,
    predict_reward_score,
    record_rating,
    reward_enabled,
    taste_profile,
    train_model,
)
from pattern_features import FEATURE_ORDER, extract_pattern_features, features_vector


def _rage_like_pattern() -> dict:
    return {
        "bpm": 150,
        "style": "rage opium trap",
        "user_prompt": "rage dark trap",
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.4, "velocity": 110}],
            "snare": [{"time_step": 1.0, "note": "D1", "length": 0.35, "velocity": 105}],
            "clap": [{"time_step": 1.0, "note": "D1", "length": 0.25, "velocity": 90}],
            "hi_hats": [
                {"time_step": i * 0.5, "note": "C5", "length": 0.125, "velocity": 100}
                for i in range(8)
            ],
            "sub_808": [{"time_step": 0.0, "note": "A1", "length": 2.0, "velocity": 127}],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 2.0, "velocity": 100}],
        },
    }


@pytest.fixture
def reward_data_dir(tmp_path: Path, monkeypatch):
    root = tmp_path / "reward"
    root.mkdir()
    monkeypatch.setenv("PLG_USE_BEAT_REWARD", "1")
    monkeypatch.setattr("beat_reward._data_dir", lambda: root)
    return root


def test_features_vector_shape():
    pattern = _rage_like_pattern()
    feats = extract_pattern_features(pattern)
    vec = features_vector(pattern)
    assert len(vec) == len(FEATURE_ORDER)
    assert all(0.0 <= v <= 1.5 for v in vec)
    assert feats["proxy_score"] > 0


def test_record_and_train_model(reward_data_dir: Path):
    if not reward_enabled():
        pytest.skip("scikit-learn not installed")

    base = _rage_like_pattern()
    for idx, rating in enumerate([1, 1, 1, -1, -1, -1]):
        pattern = deepcopy(base)
        pattern["plg_beat_id"] = f"beat-{idx}"
        pattern["plg_quality"] = {"score": 70 + idx, "genre": "rage"}
        record_rating(pattern, rating)

    assert len(load_ratings()) == 6
    meta = train_model()
    assert meta is not None
    assert meta["samples"] >= min_train_samples()
    score = predict_reward_score(base)
    assert score is not None
    assert 0.0 <= score <= 100.0


def test_evaluate_blends_reward(reward_data_dir: Path, monkeypatch):
    if not reward_enabled():
        pytest.skip("scikit-learn not installed")

    pattern = _rage_like_pattern()
    monkeypatch.setenv("PLG_BEAT_REWARD_WEIGHT", "0.5")
    with patch("beat_quality.predict_reward_score", return_value=90.0), patch(
        "beat_quality.reward_enabled", return_value=True
    ):
        report = evaluate(pattern)
    assert report.reward_score == 90.0
    assert report.score > report.proxy_score


def test_reward_disabled(monkeypatch):
    monkeypatch.setenv("PLG_USE_BEAT_REWARD", "0")
    assert reward_enabled() is False


def test_taste_profile_and_model_status(reward_data_dir: Path):
    base = _rage_like_pattern()
    for idx, rating in enumerate([1, 1, -1]):
        pattern = deepcopy(base)
        pattern["plg_beat_id"] = f"taste-{idx}"
        pattern["plg_quality"] = {"score": 70, "genre": "rage"}
        record_rating(pattern, rating)

    taste = taste_profile()
    assert taste["total"] == 3
    assert taste["thumbs_up"] == 2
    assert taste["thumbs_down"] == 1
    assert taste["favorite_genre"] == "rage"
    assert taste["progress"] > 0

    status = model_status()
    assert status["ratings"] == 3
    assert status["thumbs_up"] == 2
    assert status["favorite_genre"] == "rage"
    assert "needs_both_polarities" in status

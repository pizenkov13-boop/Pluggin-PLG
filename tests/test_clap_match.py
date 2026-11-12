import os
from pathlib import Path
from unittest.mock import patch

import numpy as np

import clap_match
from clap_match import build_clap_query, _cosine_bonus
from sample_match import pick_best_for_track


def test_build_clap_query_includes_role_and_prompt():
    q = build_clap_query("dark rage 808", "opium trap", "sub_808")
    assert "dark rage 808" in q
    assert "808" in q.lower()


def test_cosine_bonus_scales_positive_similarity():
    a = np.array([1.0, 0.0])
    t = np.array([1.0, 0.0])
    assert _cosine_bonus(a, t) == clap_match.CLAP_BONUS_SCALE


def test_pick_uses_clap_bonus_when_mocked(tmp_path: Path):
    (tmp_path / "808").mkdir()
    for name in ("aaa.wav", "bbb.wav"):
        path = tmp_path / "808" / name
        path.write_bytes(b"RIFF" + b"\x00" * 128)

    catalog = {"audio": {"808": ["808/aaa.wav", "808/bbb.wav"]}}

    fake_text = np.array([1.0, 0.0])
    bonuses = {
        str((tmp_path / "808" / "aaa.wav").resolve()): 5,
        str((tmp_path / "808" / "bbb.wav").resolve()): 40,
    }

    with (
        patch.object(clap_match, "use_clap", return_value=True),
        patch.object(clap_match, "get_text_embedding", return_value=fake_text),
        patch.object(clap_match, "score_paths", return_value=bonuses),
    ):
        path, score = pick_best_for_track(
            catalog,
            "sub_808",
            tmp_path,
            prompt="dark distorted 808",
            use_clap=True,
        )

    assert path is not None
    assert path.name == "bbb.wav"
    assert score >= 40


def test_clap_disabled_by_env(monkeypatch):
    monkeypatch.setenv("PLG_USE_CLAP", "0")
    clap_match._import_failed = False
    assert clap_match.clap_available() is False

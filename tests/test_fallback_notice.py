"""Tell the user when a built-in sound stands in for one of their own files.

A synthetic 808/kick/hat used to replace a file that could not be used (for
example one that could not be decoded) without a word, so people assumed PLG
simply sounds like that. ``lane_fallback`` now records how many of the user's
own files were turned down, and the beat result lists the affected lanes.
"""

from __future__ import annotations

import logging

import pytest

import plg_api
from lane_fallback import fallback_lanes_for_user, resolve_lane_sample

PATTERN_NOTES = [{"note": "C2", "step": 0, "length": 1.0}]


@pytest.fixture(autouse=True)
def _isolated_user_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    # This file is about the notice for a synthetic stand-in, which exists only with substitutes on
    # (PLG_SUBSTITUTE_SOUNDS, sound_policy); by default a lane without a usable file is simply empty.
    monkeypatch.setenv("PLG_SUBSTITUTE_SOUNDS", "on")


def _library(tmp_path, folder: str, names: list[str]):
    """Files that exist but can never pass a gate (not audio), catalogued by folder."""
    root = tmp_path / "lib"
    root.mkdir()
    for name in names:
        (root / name).write_bytes(b"not audio" * 20)
    return root, {"audio": {folder: list(names)}}


def _pattern(lane: str) -> dict:
    return {"bpm": 140, "tracks": {lane: [dict(n) for n in PATTERN_NOTES]}, "plg_midi_melody": True}


def test_a_synthetic_808_standing_in_for_users_files_is_reported(tmp_path, caplog):
    root, catalog = _library(tmp_path, "808", ["bad one 808.wav", "bad two 808.wav"])
    pattern = _pattern("808_bass")
    with caplog.at_level(logging.WARNING):
        resolve_lane_sample("808_bass", pattern, {}, catalog, root)
    entry = pattern["plg_sample_fallback"]["808_bass"]
    assert entry["tier"] == 2 and entry["user_files"] == 2
    assert fallback_lanes_for_user(pattern) == ["808_bass"]
    assert plg_api._sample_fallback_lanes(pattern) == ["808_bass"]
    assert any("none of the user's 2 own file(s) could be used" in r.getMessage() for r in caplog.records)


def test_no_808_files_at_all_is_not_something_to_apologise_for(tmp_path):
    pattern = _pattern("808_bass")
    resolve_lane_sample("808_bass", pattern, {}, {"audio": {}}, tmp_path)
    assert pattern["plg_sample_fallback"]["808_bass"]["user_files"] == 0
    assert fallback_lanes_for_user(pattern) == []


def test_808_files_do_not_make_a_missing_kick_look_replaced(tmp_path):
    root, catalog = _library(tmp_path, "808", ["a 808.wav"])
    pattern = _pattern("kick")
    resolve_lane_sample("kick", pattern, {}, catalog, root)
    assert pattern["plg_sample_fallback"]["kick"]["user_files"] == 0
    assert fallback_lanes_for_user(pattern) == []


def test_files_that_no_longer_exist_are_not_counted(tmp_path):
    pattern = _pattern("808_bass")
    resolve_lane_sample("808_bass", pattern, {}, {"audio": {"808": ["gone 808.wav"]}}, tmp_path)
    assert pattern["plg_sample_fallback"]["808_bass"]["user_files"] == 0


def test_lanes_come_out_in_the_fixed_lane_order_and_tier_1_is_never_listed():
    pattern = {
        "plg_sample_fallback": {
            "hi_hats": {"tier": 2, "user_files": 3},
            "808_bass": {"tier": 1, "user_files": 5, "reasons": ["too_bright_for_808_bass"]},
            "kick": {"tier": 2, "user_files": 1},
        }
    }
    assert fallback_lanes_for_user(pattern) == ["kick", "hi_hats"]


@pytest.mark.parametrize("pattern", [{}, {"plg_sample_fallback": None}, {"plg_sample_fallback": "x"}, {"plg_sample_fallback": {"kick": None}}, None])
def test_missing_or_malformed_records_mean_no_notice_and_no_crash(pattern):
    assert fallback_lanes_for_user(pattern) == []
    assert plg_api._sample_fallback_lanes(pattern) == []

"""Audit 2026-07: two starter_kit.py call sites baked processed audio
regardless of PLG_RAW_SAMPLES — the sample-chop engine (finding #2, priority)
and the FX-lane riser/delay-accent bakes (finding #1). Both now check
raw_sample_mode_enabled(), matching every other per-sample DSP gate in this
file (saturation, Haas widen, hi_hats_reverse_open)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from starter_kit import attach_sounds_to_pattern, ensure_starter_kit


def _base_pattern(**overrides) -> dict:
    pattern = {
        "bpm": 140,
        "style": "trap",
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 110}],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 100}],
        },
        "plg_variety": {"genre": "trap"},
    }
    pattern.update(overrides)
    return pattern


def test_sample_chop_skipped_under_raw_sample_mode(monkeypatch):
    ensure_starter_kit()
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")

    def _boom(*_a, **_kw):
        raise AssertionError("sample-chop must not run under PLG_RAW_SAMPLES=1")

    with patch("sample_chop_engine.apply_sample_chop_to_pattern", side_effect=_boom):
        attach_sounds_to_pattern(_base_pattern(), None, prompt="dark trap beat")


def test_sample_chop_still_available_without_raw_sample_mode(monkeypatch):
    ensure_starter_kit()
    monkeypatch.delenv("PLG_RAW_SAMPLES", raising=False)
    calls = []

    def _spy(pattern, melody_path, *, prompt=""):
        calls.append((melody_path, prompt))
        return False  # no-op — should_chop_source would reject a short one-shot anyway

    with patch("sample_chop_engine.apply_sample_chop_to_pattern", side_effect=_spy):
        attach_sounds_to_pattern(_base_pattern(), None, prompt="dark trap beat")

    assert len(calls) == 1


def test_fx_lane_riser_bake_skipped_under_raw_sample_mode(monkeypatch):
    ensure_starter_kit()
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")

    def _boom(*_a, **_kw):
        raise AssertionError("reverse-chop bake must not run under PLG_RAW_SAMPLES=1")

    pattern = _base_pattern(
        plg_fx_kind="riser_from_chords",
        tracks={
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 110}],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 100}],
            "chords": [{"time_step": 0.0, "note": "A3", "length": 4.0, "velocity": 90}],
            "fx": [{"time_step": 28.0, "note": "C5", "length": 2.0, "velocity": 100}],
        },
    )
    with patch("sample_fx.process_file_reverse_chop", side_effect=_boom):
        result = attach_sounds_to_pattern(pattern, None, prompt="dark trap beat")

    assert "fx" not in pattern.get("tracks", {})
    assert "fx" not in result or result.get("fx") is None


def test_fx_lane_delay_accent_bake_skipped_under_raw_sample_mode(monkeypatch):
    ensure_starter_kit()
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")

    def _boom(*_a, **_kw):
        raise AssertionError("delay-accent bake must not run under PLG_RAW_SAMPLES=1")

    pattern = _base_pattern(
        plg_fx_kind="delay_accent",
        tracks={
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 110}],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 100}],
            "chords": [{"time_step": 0.0, "note": "A3", "length": 4.0, "velocity": 90}],
            "fx": [{"time_step": 28.0, "note": "C5", "length": 2.0, "velocity": 100}],
        },
    )
    with patch("sample_fx.process_file_delay_accent", side_effect=_boom):
        result = attach_sounds_to_pattern(pattern, None, prompt="dark trap beat")

    assert "fx" not in pattern.get("tracks", {})
    assert "fx" not in result or result.get("fx") is None


@pytest.fixture(autouse=True)
def _melody_sample_on(monkeypatch):
    """This file tests the sample chop of the melody sample: PLG_MELODY_SAMPLE is off by default (sound_policy),
    and the starter melody it chops is one of the substitute sounds (PLG_SUBSTITUTE_SOUNDS, also off by default)."""
    monkeypatch.setenv("PLG_MELODY_SAMPLE", "on")
    monkeypatch.setenv("PLG_SUBSTITUTE_SOUNDS", "on")

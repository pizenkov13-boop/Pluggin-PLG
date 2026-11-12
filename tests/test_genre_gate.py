"""The honest genre stub — create_beat must refuse to generate from a
not-yet-detailed genre instead of shipping a mediocre result, and must never
call the real (paid, slow) pipeline to do it."""

from __future__ import annotations

import plg_api
from genre_profiles import DETAILED_GENRES, is_genre_detailed


def test_detailed_genres_is_exactly_the_video_tuned_set():
    assert DETAILED_GENRES == {
        "trap", "rage", "opium", "drill", "west_coast",
        "underground", "pluggnb", "detroit", "hyperpop", "phonk",
    }


def test_is_genre_detailed():
    assert is_genre_detailed("trap") is True
    assert is_genre_detailed("house") is False
    assert is_genre_detailed("not_a_real_genre") is False


def _unblock_common_gates(monkeypatch):
    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: False)
    monkeypatch.setattr(plg_api, "has_api_key", lambda: True)
    monkeypatch.setattr(plg_api, "ensure_can_consume_beat", lambda: None)
    # MIDI is mandatory for generation (2026-07) — these tests exercise the
    # genre gate specifically, not the MIDI-availability gate, so pretend
    # material is always present regardless of what's on disk.
    monkeypatch.setattr(plg_api, "has_available_midi_material", lambda *a, **kw: True)


def test_no_detected_genre_can_reach_the_gate(monkeypatch):
    """2026-08-11: detection can no longer refuse to generate.

    HIPHOP_LANES used to admit six untuned lanes (toliver, kanye, boombap,
    lofi, jersey, minimal_trap). Those passed clamp_to_focus unchanged and then
    failed is_genre_detailed here, so a MIDI or prompt that read as e.g. lofi
    produced "Упс, этот жанр пока не доступен" instead of a beat — eight
    genres were affected once soul->lofi and funk->boombap are counted.

    HIPHOP_LANES is now exactly DETAILED_GENRES and every _FOCUS_FALLBACK
    target is tuned, so no genre in PROFILES can resolve to a gated lane.
    """
    from genre_profiles import PROFILES, clamp_to_focus

    for name in PROFILES:
        assert is_genre_detailed(clamp_to_focus(name)), name


def test_undetailed_genre_override_is_stubbed_without_calling_the_real_pipeline(monkeypatch):
    """The gate itself still works — reachable now only via an explicit
    genre_override that names a lane outside DETAILED_GENRES."""
    _unblock_common_gates(monkeypatch)

    def _boom(*_a, **_kw):
        raise AssertionError("run_pipeline must not be called for a gated genre")

    monkeypatch.setattr(plg_api, "run_pipeline", _boom)

    result = plg_api.create_beat("", genre_override="jersey")

    assert result["ok"] is False
    assert result["error_type"] == "genre_not_ready"
    assert result["genre"] == "jersey"
    assert result["available_genres"] == sorted(DETAILED_GENRES)
    assert result["error"]


def test_detailed_genre_is_not_blocked(monkeypatch):
    _unblock_common_gates(monkeypatch)
    monkeypatch.setattr(plg_api, "consume_beat", lambda: None)

    calls: list[str] = []

    def _fake_run_pipeline(prompt, **_kw):
        calls.append(prompt)
        return {"bpm": 140, "style": "dark trap", "tracks": {}}

    monkeypatch.setattr(plg_api, "run_pipeline", _fake_run_pipeline)

    result = plg_api.create_beat("dark trap beat, 140 bpm")

    assert calls, "run_pipeline should have been called for a detailed genre"
    assert result.get("error_type") != "genre_not_ready"


def test_genre_gate_kill_switch(monkeypatch):
    monkeypatch.setenv("PLG_GENRE_GATE", "off")
    _unblock_common_gates(monkeypatch)
    monkeypatch.setattr(plg_api, "consume_beat", lambda: None)

    calls: list[str] = []

    def _fake_run_pipeline(prompt, **_kw):
        calls.append(prompt)
        return {"bpm": 128, "style": "house", "tracks": {}}

    monkeypatch.setattr(plg_api, "run_pipeline", _fake_run_pipeline)

    result = plg_api.create_beat("house club banger, 128 bpm")

    assert calls, "gate is off — run_pipeline must still be called for house"
    assert result.get("error_type") != "genre_not_ready"


def test_ambiguous_prompt_does_not_get_falsely_blocked(monkeypatch):
    # A prompt with no confident genre match resolves genre=None — must not
    # be treated as "undetailed", or every vague prompt would get stubbed.
    _unblock_common_gates(monkeypatch)
    monkeypatch.setattr(plg_api, "consume_beat", lambda: None)

    calls: list[str] = []

    def _fake_run_pipeline(prompt, **_kw):
        calls.append(prompt)
        return {"bpm": 140, "style": "", "tracks": {}}

    monkeypatch.setattr(plg_api, "run_pipeline", _fake_run_pipeline)

    result = plg_api.create_beat("make it sound good")

    assert calls
    assert result.get("error_type") != "genre_not_ready"

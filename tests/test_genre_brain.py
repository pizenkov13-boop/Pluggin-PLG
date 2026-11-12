"""Genre brain — any free-form prompt routes to the right lane."""

from __future__ import annotations

import json
from typing import Any

import pytest

import genre_brain
from generation_variety import build_context, llm_addendum
from genre_profiles import PROFILES, profile_for
from style_corpus import bpm_range, style_text_for_matching


@pytest.fixture(autouse=True)
def _clear_brain_cache(monkeypatch):
    # Full-router semantics (every lane) — the shipping hip-hop focus gate is
    # covered by tests/test_lane_focus.py.
    monkeypatch.setenv("PLG_LANES", "all")
    genre_brain._classify_llm.cache_clear()
    yield
    genre_brain._classify_llm.cache_clear()


def _mock_llm(monkeypatch, payload: dict[str, Any], *, calls: list[int] | None = None):
    def fake(prompt, system, *, model, temperature=0.0, max_tokens=0):
        if calls is not None:
            calls.append(1)
        return payload

    import llm_client

    monkeypatch.setattr(llm_client, "openrouter_json_object", fake)
    monkeypatch.setenv("PLG_GENRE_BRAIN", "on")
    monkeypatch.setenv("PLG_GENRE_BRAIN_MODEL", "test-model")


# --- routing tiers ------------------------------------------------------------


def test_explicit_genre_word_skips_llm(monkeypatch):
    calls: list[int] = []
    _mock_llm(monkeypatch, {"genre": "house"}, calls=calls)
    info = genre_brain.resolve_generation_genre("uk drill 145 bpm")
    assert info["genre"] == "drill"
    assert info["source"] == "keyword"
    assert not calls  # free fast-path, no LLM


def test_artist_reference_skips_llm(monkeypatch):
    calls: list[int] = []
    _mock_llm(monkeypatch, {"genre": "house"}, calls=calls)
    info = genre_brain.resolve_generation_genre("сделай как у дуа липы")
    assert info["genre"] == "disco_pop"
    assert info["source"] == "artist"
    assert not calls


def test_free_form_prompt_routed_by_llm(monkeypatch):
    _mock_llm(monkeypatch, {"genre": "dark_pop", "mood": ["moody", "nocturnal"], "energy": 0.4})
    info = genre_brain.resolve_generation_genre("грустный бит для ночной поездки под дождём")
    assert info["genre"] == "dark_pop"
    assert info["source"] == "llm"
    assert info["mood"] == ["moody", "nocturnal"]
    assert info["energy"] == pytest.approx(0.4)


def test_unknown_lane_from_llm_is_ignored(monkeypatch):
    _mock_llm(monkeypatch, {"genre": "vaporwave-polka", "mood": ["weird"]})
    info = genre_brain.resolve_generation_genre("что-то очень странное без жанра")
    assert info["genre"] is None
    assert info["source"] is None


def test_llm_failure_falls_back_silently(monkeypatch):
    import llm_client

    def boom(*args, **kwargs):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(llm_client, "openrouter_json_object", boom)
    monkeypatch.setenv("PLG_GENRE_BRAIN", "on")
    info = genre_brain.resolve_generation_genre("просто какой-то вайб")
    assert info["genre"] is None


def test_brain_disabled_without_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("PLG_GENRE_BRAIN", "auto")
    assert not genre_brain.brain_enabled()
    monkeypatch.setenv("PLG_GENRE_BRAIN", "off")
    assert not genre_brain.brain_enabled()


def test_all_menu_lanes_exist_in_profiles():
    for name, _ in genre_brain._LANES:
        assert name in PROFILES, name


# --- lane drives the deterministic machine ------------------------------------


def test_build_context_follows_brain_lane():
    ctx = build_context("ночная поездка, дождь, грусть", genre_hint="dark_pop", mood=["moody"])
    lo, hi = bpm_range("dark_pop")
    assert ctx["genre"] == "dark_pop"
    assert lo <= ctx["bpm"] <= hi
    assert ctx["scale"] == PROFILES["dark_pop"].melody_scale
    assert ctx["mood"] == ["moody"]


def test_build_context_invalid_hint_falls_back_to_keywords():
    ctx = build_context("uk drill темный", genre_hint="not-a-genre")
    assert ctx["genre"] == "drill"


def test_llm_addendum_announces_lane():
    ctx = build_context("bass heavy vibe", genre_hint="dubstep", mood=["dark", "heavy"])
    text = llm_addendum(ctx)
    assert "dubstep" in text
    assert "mood: dark, heavy" in text


def test_profile_for_genre_hint_beats_keywords():
    profile = profile_for("", "просто вайб без жанровых слов", genre_hint="house")
    assert profile.name == "house"
    # keyword path unchanged when hint absent
    assert profile_for("", "uk drill").name == "drill"


def test_style_text_for_matching_includes_lane_and_mood():
    pattern = {
        "style": "night vibe",
        "plg_variety": {"genre": "dark_pop", "mood": ["moody", "dreamy"]},
    }
    text = style_text_for_matching(pattern)
    assert "dark pop" in text
    assert "moody" in text and "dreamy" in text


def test_humanize_uses_brain_lane_for_profile():
    from beat_humanize import humanize_pattern

    pattern = {
        "bpm": 122,
        "style": "",
        "user_prompt": "просто тёплый вечерний вайб",  # no genre words at all
        "plg_variety": {"seed": 7, "genre": "house", "key": "A", "scale": "dorian"},
        "plg_arrangement": [{"name": "hook", "start_bar": 0, "bars": 8}],
        "tracks": {
            "kick": [],
            "snare": [],
            "hi_hats": [],
            "melody_lead": [
                {"time_step": float(i * 2), "note": n, "length": 1.0, "velocity": 96}
                for i, n in enumerate(("A3", "C4", "E4", "G4"))
            ],
        },
    }
    out = humanize_pattern(pattern)
    assert out["plg_genre"] == "house"
    kicks = out["tracks"]["kick"]
    beats_in_bar = sorted({round(float(k["time_step"]) % 4, 2) for k in kicks})
    # Four-on-floor, not trap half-time, plus the one humanized flam the
    # first-party drum seed places beside a beat (2026-08-28, FLEX retirement).
    # WHICH beat the flam sits beside is a draw from the shared rng, so any
    # pass that consumes it differently moves it (it was 2.01 until the hat
    # swing fix of 2026-10-01): assert the structure, not the draw.
    on_beat = [b for b in beats_in_bar if b == round(b)]
    flams = [b for b in beats_in_bar if b != round(b)]
    assert on_beat == [0.0, 1.0, 2.0, 3.0]
    assert len(flams) == 1 and abs(flams[0] - round(flams[0])) <= 0.05, flams


# --- cloud subscription path (key lives server-side) ---------------------------


def test_cloud_mode_routes_brain_through_server(monkeypatch):
    """No local key + cloud mode on → the SERVER classifies (paying users keep
    the flagship any-prompt brain)."""
    import plg_cloud

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("PLG_GENRE_BRAIN", "auto")  # auto = off without local key
    calls: list[str] = []

    def fake_resolve(prompt: str):
        calls.append(prompt)
        return {"ok": True, "genre": "lofi", "mood": ["warm", "dusty"], "energy": 0.2}

    monkeypatch.setattr(plg_cloud, "is_cloud_mode", lambda: True)
    monkeypatch.setattr(plg_cloud, "resolve_genre", fake_resolve)
    genre_brain._cloud_classify_cached.cache_clear()

    info = genre_brain.resolve_generation_genre("дождливый вечер, учеба, чилл")
    assert info["genre"] == "lofi"
    assert info["source"] == "llm"
    assert info["mood"] == ["warm", "dusty"]
    assert calls  # server was consulted

    # cached: the second resolve must not hit the server again
    genre_brain.resolve_generation_genre("дождливый вечер, учеба, чилл")
    assert len(calls) == 1


def test_cloud_brain_failure_falls_back_silently(monkeypatch):
    import plg_cloud

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("PLG_GENRE_BRAIN", "auto")

    def boom(prompt: str):
        raise RuntimeError("cloud down")

    monkeypatch.setattr(plg_cloud, "is_cloud_mode", lambda: True)
    monkeypatch.setattr(plg_cloud, "resolve_genre", boom)
    genre_brain._cloud_classify_cached.cache_clear()

    info = genre_brain.resolve_generation_genre("просто какой-то вайб без слов")
    assert info["genre"] is None  # legacy trap default downstream, no crash


def test_cloud_result_clamped_to_focus(monkeypatch):
    """Server may run PLG_LANES=all; the CLIENT's focus still clamps."""
    import plg_cloud

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("PLG_GENRE_BRAIN", "auto")
    monkeypatch.setenv("PLG_LANES", "hiphop")
    monkeypatch.setattr(plg_cloud, "is_cloud_mode", lambda: True)
    monkeypatch.setattr(
        plg_cloud, "resolve_genre",
        lambda p: {"ok": True, "genre": "house", "mood": ["groovy"], "energy": 0.7},
    )
    genre_brain._cloud_classify_cached.cache_clear()

    info = genre_brain.resolve_generation_genre("тёплый вечерний грув без жанра")
    assert info["genre"] == "trap"  # house clamped into the hip-hop focus


def test_classify_prompt_server_entry(monkeypatch):
    _mock_llm(monkeypatch, {"genre": "drill", "mood": ["cold", "dark"], "energy": 0.8})
    result = genre_brain.classify_prompt("sliding 808s type vibe")
    assert result == {"genre": "drill", "mood": ["cold", "dark"], "energy": 0.8}


def test_classify_prompt_rejects_unknown_lane(monkeypatch):
    _mock_llm(monkeypatch, {"genre": "vaporwave-polka"})
    assert genre_brain.classify_prompt("something odd") is None


def test_json_payload_survives_cache_roundtrip(monkeypatch):
    _mock_llm(monkeypatch, {"genre": "lofi", "mood": ["warm"], "energy": 0.2})
    first = genre_brain.resolve_generation_genre("дождь за окном, учеба")
    second = genre_brain.resolve_generation_genre("дождь за окном, учеба")
    assert first == second == json.loads(json.dumps(first))


# --- MIDI-driven detection (2026-07 Feature 1): primary, not a tiebreaker ----


def _write_west_coast_midi(path):
    import mido

    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(96), time=0))
    from pattern_utils import parse_note_name

    names = ["D3", "F3", "G3", "A3", "C4", "A3", "G3", "F3"]
    for name in names:
        note = parse_note_name(name)
        track.append(mido.Message("note_on", note=note, velocity=90, time=0))
        track.append(mido.Message("note_off", note=note, velocity=0, time=960))
    mid.save(str(path))


def _write_short_midi(path, count=3):
    import mido

    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    from pattern_utils import parse_note_name

    for name in ["C4", "D4", "E4"][:count]:
        note = parse_note_name(name)
        track.append(mido.Message("note_on", note=note, velocity=90, time=0))
        track.append(mido.Message("note_off", note=note, velocity=0, time=480))
    mid.save(str(path))


def test_midi_path_wins_over_explicit_keyword_in_prompt(tmp_path, monkeypatch):
    """2026-07: once real material is uploaded, text-prompt genre words are
    no longer authoritative — MIDI detection is checked BEFORE keyword."""
    path = tmp_path / "upload.mid"
    _write_west_coast_midi(path)
    info = genre_brain.resolve_generation_genre("gimme a hard trap beat", midi_path=path)
    assert info["genre"] == "west_coast"
    assert info["source"] == "midi"
    assert info["confidence"] is not None


def test_midi_path_inconclusive_falls_back_to_keyword(tmp_path):
    """Too few notes -> detect_genre_from_midi returns None -> the existing
    keyword/artist/LLM chain still runs, unaffected."""
    path = tmp_path / "tiny.mid"
    _write_short_midi(path, count=3)
    info = genre_brain.resolve_generation_genre("uk drill 145 bpm", midi_path=path)
    assert info["genre"] == "drill"
    assert info["source"] == "keyword"


def test_midi_genre_detect_disabled_via_env_falls_back_to_text(tmp_path, monkeypatch):
    monkeypatch.setenv("PLG_MIDI_GENRE_DETECT", "off")
    path = tmp_path / "upload.mid"
    _write_west_coast_midi(path)
    info = genre_brain.resolve_generation_genre("uk drill 145 bpm", midi_path=path)
    assert info["genre"] == "drill"
    assert info["source"] == "keyword"


def test_no_midi_path_existing_callers_completely_unaffected(monkeypatch):
    """Regression safety: the 100% of callers not uploading MIDI must see
    byte-identical behavior to before this feature existed."""
    calls: list[int] = []
    _mock_llm(monkeypatch, {"genre": "house"}, calls=calls)
    info = genre_brain.resolve_generation_genre("uk drill 145 bpm")
    assert info["genre"] == "drill"
    assert info["source"] == "keyword"
    assert not calls


def test_unreadable_midi_path_falls_back_silently(tmp_path):
    path = tmp_path / "garbage.mid"
    path.write_bytes(b"not a real midi file")
    info = genre_brain.resolve_generation_genre("uk drill 145 bpm", midi_path=path)
    assert info["genre"] == "drill"
    assert info["source"] == "keyword"

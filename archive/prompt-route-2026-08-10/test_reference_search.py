"""Reference-search booster: gating, bullet-proof fallback, extraction, cache."""

from __future__ import annotations

from unittest.mock import patch

import reference_search as rs


def test_searches_every_prompt_by_default(monkeypatch):
    monkeypatch.delenv("PLG_SEARCH_MIN_WORDS", raising=False)
    # Default = search on any non-empty prompt, even short tag-style ones.
    assert rs.is_complex_prompt("808 rage") is True
    assert rs.is_complex_prompt("dark trap 140") is True
    assert rs.is_complex_prompt("") is False


def test_min_words_can_gate_short_prompts(monkeypatch):
    monkeypatch.setenv("PLG_SEARCH_MIN_WORDS", "7")
    assert rs.is_complex_prompt("808 rage") is False
    assert rs.is_complex_prompt(
        "make a dark melodic rage beat with distorted 808 glides and bells"
    ) is True


def _clear_all_keys(monkeypatch):
    for key in ("TAVILY_API_KEY", "PERPLEXITY_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def test_disabled_without_any_key(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("PLG_REFERENCE_SEARCH", "auto")
    assert rs.search_enabled() is False
    # Even a detailed prompt returns None when nothing is configured.
    assert rs.gather_reference_hints("a very detailed dark melodic rage trap beat please") is None


def test_gemini_grounding_enables_without_paid_key(monkeypatch):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")  # only a Gemini key, no paid search key
    monkeypatch.setenv("PLG_REFERENCE_SEARCH", "auto")
    assert rs.provider() == "gemini"
    assert rs.search_enabled() is True  # free Google Search grounding path


def test_gemini_full_flow(monkeypatch, tmp_path):
    _clear_all_keys(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    monkeypatch.setenv("PLG_REFERENCE_SEARCH", "on")
    monkeypatch.setenv("PLG_OFFLINE_MELODY", "off")
    monkeypatch.setattr(rs, "_cache_dir", lambda: tmp_path)

    extracted = {"bpm": 152, "scale": "phrygian", "drum_layout_rules": ["triplet hat rolls"]}
    with patch("llm_client.grounded_search", return_value="grounded web text about opium rage"):
        with patch("llm_client.complete_json", return_value=extracted):
            hints = rs.gather_reference_hints("detailed opium rage beat with eerie bells and 808 glides")
    assert hints == {"bpm": 152, "scale": "phrygian", "drum_layout_rules": ["triplet hat rolls"]}


def test_off_flag_disables_even_with_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.setenv("PLG_REFERENCE_SEARCH", "off")
    assert rs.search_enabled() is False


def test_search_failure_falls_back_to_none(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.setenv("PLG_REFERENCE_SEARCH", "on")
    with patch.object(rs, "_run_search", side_effect=RuntimeError("network down")):
        with patch.object(rs, "_cache_get", return_value=None):
            out = rs.gather_reference_hints("detailed dark melodic rage trap with bells and 808")
    assert out is None  # any error -> None, never raises


def test_validate_hints_clamps_and_filters():
    raw = {
        "bpm": 999,                       # out of range -> dropped
        "scale": "Phrygian",
        "drum_layout_rules": ["kick on 1", "", 123, "snare on 3"],
        "fx_chain": ["soft clip"],
        "junk": "ignored",
    }
    hints = rs._validate_hints(raw)
    assert "bpm" not in hints
    assert hints["scale"] == "phrygian"
    assert hints["drum_layout_rules"] == ["kick on 1", "123", "snare on 3"]
    assert hints["fx_chain"] == ["soft clip"]
    assert "junk" not in hints


def test_validate_hints_keeps_valid_bpm():
    assert rs._validate_hints({"bpm": 145})["bpm"] == 145
    assert rs._validate_hints({"bpm": "150"})["bpm"] == 150


def test_full_flow_with_mocked_search_and_llm(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.setenv("PLG_REFERENCE_SEARCH", "on")
    monkeypatch.setenv("PLG_OFFLINE_MELODY", "off")
    monkeypatch.setattr(rs, "_cache_dir", lambda: tmp_path)

    extracted = {
        "bpm": 146,
        "scale": "natural minor",
        "drum_layout_rules": ["half-time snare on beat 3"],
        "fx_chain": ["parallel saturation on 808"],
    }
    with patch.object(rs, "_run_search", return_value="some web text about rage beats"):
        with patch("llm_client.complete_json", return_value=extracted):
            hints = rs.gather_reference_hints("detailed dark melodic rage trap beat with 808 glides")

    assert hints is not None
    assert hints["bpm"] == 146 and hints["scale"] == "natural minor"
    # Second call hits the on-disk cache (no search/LLM needed).
    with patch.object(rs, "_run_search", side_effect=AssertionError("should not search again")):
        cached = rs.gather_reference_hints("detailed dark melodic rage trap beat with 808 glides")
    assert cached == hints


def test_opium_rage_skips_reference_search_by_default(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.setenv("PLG_REFERENCE_SEARCH", "on")
    monkeypatch.delenv("PLG_OFFLINE_MELODY", raising=False)
    with patch.object(rs, "_run_search", side_effect=AssertionError("search should be skipped")):
        assert rs.gather_reference_hints("dark opium rage trap 140 bpm") is None


def test_reference_addendum_formats_hints():
    text = rs.reference_addendum({
        "bpm": 150,
        "scale": "phrygian",
        "drum_layout_rules": ["kick on 1 and the 'and' of 2"],
        "fx_chain": ["soft clipper on master"],
    })
    assert "150" in text and "phrygian" in text
    assert "kick on 1" in text and "soft clipper" in text
    assert rs.reference_addendum({}) == ""

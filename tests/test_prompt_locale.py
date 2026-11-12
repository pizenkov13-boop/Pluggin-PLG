"""Tests for multilingual prompt bridge."""

from __future__ import annotations

from prompt_locale import detect_tags, prepare_prompt_for_llm


def test_detect_russian_rage_tags():
    tags = detect_tags("мрачный рейдж бит с жёстким 808", "ru")
    assert "RAGE" in tags
    assert any("MOOD" in t for t in tags)


def test_prepare_enriches_llm_prompt():
    prep = prepare_prompt_for_llm("dark rage beat hard 808", "en")
    assert "RAGE" in prep["plg_style_tags"]
    assert prep["user_prompt"] == "dark rage beat hard 808"
    assert "PLG tags" in prep["llm_prompt"]


def test_arabic_locale_preserved():
    prep = prepare_prompt_for_llm("رايج مظلم", "ar")
    assert prep["plg_locale"] == "ar"

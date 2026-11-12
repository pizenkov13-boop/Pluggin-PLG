"""generate_beat_pattern must degrade to a deterministic seed pattern when the
LLM step fails — never raise. The client engine (MIDI-first) fills the lanes."""

from __future__ import annotations

import cloud.app.llm_proxy as lp
from cloud.app.pattern_validate import validate_pattern


def _stub_heavy(monkeypatch):
    monkeypatch.setattr(lp, "load_user_profile", lambda: {})
    monkeypatch.setattr(lp, "build_system_instruction", lambda *a, **k: "sys")


def test_falls_back_when_llm_raises(monkeypatch):
    _stub_heavy(monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("OpenRouter 402: budget 0 руб.")

    monkeypatch.setattr(lp, "generate_pattern", _boom)

    pattern, meta = lp.generate_beat_pattern("custom drumkit beat", locale="ru")

    assert validate_pattern(pattern) is pattern  # contract-minimal, validate-clean
    assert pattern["tracks"] == {}
    assert 40.0 <= float(pattern["bpm"]) <= 220.0
    assert pattern.get("plg_llm_unavailable") is True
    assert meta["llm_fallback"] is True
    assert meta["model"] == "deterministic-fallback"
    assert meta["cost_usd"] == 0.0


def test_falls_back_when_llm_returns_junk(monkeypatch):
    _stub_heavy(monkeypatch)
    # validate_pattern raises HTTPException(502) on a missing key — also degrade.
    monkeypatch.setattr(lp, "generate_pattern", lambda *a, **k: {"bpm": 140})

    pattern, meta = lp.generate_beat_pattern("dark trap", locale="en")

    assert meta["llm_fallback"] is True
    assert pattern["tracks"] == {}


def test_uses_llm_output_when_it_works(monkeypatch):
    _stub_heavy(monkeypatch)
    monkeypatch.setattr(
        lp,
        "generate_pattern",
        lambda *a, **k: {"bpm": 150, "tracks": {"kick": {"notes": []}}, "build_order": ["kick"]},
    )

    pattern, meta = lp.generate_beat_pattern("dark trap", locale="en")

    assert float(pattern["bpm"]) == 150.0
    assert meta["llm_fallback"] is False
    assert meta["model"] != "deterministic-fallback"

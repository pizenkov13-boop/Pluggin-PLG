"""Composer resilience: a gateway timeout must retry once, not drop straight to
the emergency deterministic builder (that fallback IS the school-tier beat)."""

from __future__ import annotations

import pytest

import llm_client
import llm_pipeline


def _skeleton() -> dict:
    return {"bpm": 150, "key": "E", "scale": "phrygian", "style": "rage"}


def test_composer_retries_once_on_timeout(monkeypatch):
    monkeypatch.delenv("PLG_TITAN_COMPOSER_FALLBACK_MODEL", raising=False)
    calls: list[dict] = []

    def fake_json_object(user, system, *, model, temperature, max_tokens, timeout_ms=None):
        calls.append({"model": model, "max_tokens": max_tokens, "timeout_ms": timeout_ms})
        if len(calls) == 1:
            raise RuntimeError("The read operation timed out")
        return {"bpm": 150, "tracks": {"kick": []}, "build_order": ["kick"]}

    monkeypatch.setattr(llm_client, "openrouter_json_object", fake_json_object)

    result = llm_pipeline.run_composer("rage beat", "ctx", {}, _skeleton(), model="gpt-5.6-luna")

    assert result["tracks"] == {"kick": []}
    assert len(calls) == 2
    # Retry runs on a DIFFERENT provider's fast model with a tighter output
    # budget and its own leash — one provider's bad day can't kill the beat.
    assert calls[1]["model"] == llm_pipeline.COMPOSER_FALLBACK_MODEL_DEFAULT
    assert calls[1]["model"] != calls[0]["model"]
    assert calls[1]["max_tokens"] <= 3072
    assert calls[0]["timeout_ms"] == llm_pipeline._composer_timeout_ms()


def test_composer_retry_can_be_disabled(monkeypatch):
    monkeypatch.setenv("PLG_TITAN_COMPOSER_FALLBACK_MODEL", "off")
    calls: list[str] = []

    def fake_json_object(user, system, *, model, temperature, max_tokens, timeout_ms=None):
        calls.append(model)
        raise RuntimeError("boom")

    monkeypatch.setattr(llm_client, "openrouter_json_object", fake_json_object)

    with pytest.raises(RuntimeError, match="boom"):
        llm_pipeline.run_composer("rage beat", "ctx", {}, _skeleton(), model="qwen3.7-plus")
    assert calls == ["qwen3.7-plus"]


def test_composer_timeout_env_override(monkeypatch):
    monkeypatch.setenv("PLG_TITAN_COMPOSER_TIMEOUT_MS", "120000")
    assert llm_pipeline._composer_timeout_ms() == 120_000
    monkeypatch.delenv("PLG_TITAN_COMPOSER_TIMEOUT_MS")
    assert llm_pipeline._composer_timeout_ms() == 90_000

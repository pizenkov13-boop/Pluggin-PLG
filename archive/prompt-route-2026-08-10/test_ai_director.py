"""Stage-1 AI Director (DeepSeek-R1): gating, bullet-proof fallback, brief build."""

from __future__ import annotations

import json
from unittest.mock import patch

import ai_director as ad


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("PLG_USE_DIRECTOR", "auto")
    assert ad.director_enabled() is False
    assert ad.make_brief("dark bounce like a rage beat") is None


def test_off_flag_disables_even_with_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("PLG_USE_DIRECTOR", "off")
    assert ad.director_enabled() is False


def test_enabled_with_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("PLG_USE_DIRECTOR", "auto")
    assert ad.director_enabled() is True


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_make_brief_returns_director_plan(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("PLG_USE_DIRECTOR", "on")
    fake = {"choices": [{"message": {"content": "BPM 145, F# phrygian. 808 glides bars 4&8, mono, ~40%."}}]}
    with patch("urllib.request.urlopen", return_value=_FakeResp(fake)):
        brief = ad.make_brief("dark bounce like a rage beat", reference_text="reddit breakdown text")
    assert brief is not None
    assert "BPM 145" in brief and "phrygian" in brief


def test_make_brief_network_error_falls_back_to_none(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("PLG_USE_DIRECTOR", "on")
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        assert ad.make_brief("detailed dark rage beat") is None  # never raises


def test_brief_addendum_formats():
    text = ad.brief_addendum("BPM 145, dark phrygian, mono 808.")
    assert "PRODUCTION BRIEF" in text and "BPM 145" in text
    assert ad.brief_addendum("") == ""

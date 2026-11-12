"""create_beat's reference_audio_path plumbing at the API layer (audit
2026-07, follow-up to test_midi_upload_api.py): cloud generation doesn't
support it yet, rejected upfront; local mode threads it through."""

from __future__ import annotations

import plg_api


def test_reference_audio_upload_rejected_in_cloud_mode(monkeypatch):
    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: True)

    def _boom(*_a, **_kw):
        raise AssertionError("cloud generation must not run when reference_audio_path is rejected upfront")

    monkeypatch.setattr(plg_api.plg_cloud, "is_signed_in", _boom)
    monkeypatch.setattr(plg_api.plg_cloud, "cloud_generate", _boom)

    result = plg_api.create_beat("dark trap beat", reference_audio_path="/tmp/whatever.wav")

    assert result["ok"] is False
    assert result["error_type"] == "not_supported"


def test_reference_audio_upload_allowed_in_local_mode(monkeypatch):
    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: False)
    monkeypatch.setattr(plg_api, "has_api_key", lambda: True)
    monkeypatch.setattr(plg_api, "ensure_can_consume_beat", lambda: None)
    # This test exercises the reference_audio_path plumbing, not the
    # MIDI-availability gate itself — /tmp/upload.wav doesn't really exist.
    monkeypatch.setattr(plg_api, "has_available_midi_material", lambda *a, **kw: True)

    seen: dict = {}

    def _fake_run_pipeline(prompt, **kwargs):
        seen.update(kwargs)
        return {"bpm": 140, "style": "trap", "tracks": {}}

    monkeypatch.setattr(plg_api, "run_pipeline", _fake_run_pipeline)
    monkeypatch.setattr(plg_api, "consume_beat", lambda: None)

    plg_api.create_beat(
        "dark trap beat", reference_audio_path="/tmp/upload.wav", respect_uploaded_scale=True
    )

    assert seen.get("reference_audio_path") == "/tmp/upload.wav"
    assert seen.get("respect_uploaded_scale") is True

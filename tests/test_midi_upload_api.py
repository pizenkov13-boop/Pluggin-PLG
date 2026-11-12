"""create_beat's midi_path plumbing at the API layer (audit 2026-07):
cloud generation doesn't support MIDI upload yet, so it must be rejected with
a clear error BEFORE touching sign-in/quota/pipeline — never a silent
ignore of the user's uploaded file."""

from __future__ import annotations

import plg_api


def test_midi_upload_rejected_in_cloud_mode(monkeypatch):
    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: True)

    def _boom(*_a, **_kw):
        raise AssertionError("cloud generation must not run when midi_path is rejected upfront")

    monkeypatch.setattr(plg_api.plg_cloud, "is_signed_in", _boom)
    monkeypatch.setattr(plg_api.plg_cloud, "cloud_generate", _boom)

    result = plg_api.create_beat("dark trap beat", midi_path="/tmp/whatever.mid")

    assert result["ok"] is False
    assert result["error_type"] == "not_supported"


def test_midi_upload_allowed_in_local_mode(monkeypatch):
    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: False)
    monkeypatch.setattr(plg_api, "has_api_key", lambda: True)
    monkeypatch.setattr(plg_api, "ensure_can_consume_beat", lambda: None)
    # This test exercises the midi_path plumbing, not the MIDI-availability
    # gate itself — /tmp/upload.mid doesn't really exist on disk.
    monkeypatch.setattr(plg_api, "has_available_midi_material", lambda *a, **kw: True)

    seen: dict = {}

    def _fake_run_pipeline(prompt, **kwargs):
        seen.update(kwargs)
        return {"bpm": 140, "style": "trap", "tracks": {}}

    monkeypatch.setattr(plg_api, "run_pipeline", _fake_run_pipeline)
    monkeypatch.setattr(plg_api, "consume_beat", lambda: None)

    plg_api.create_beat("dark trap beat", midi_path="/tmp/upload.mid", respect_uploaded_scale=True)

    assert seen.get("midi_path") == "/tmp/upload.mid"
    assert seen.get("respect_uploaded_scale") is True

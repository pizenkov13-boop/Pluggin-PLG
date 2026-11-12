"""start_beat's midi_path/reference_audio_path/respect_uploaded_scale plumbing
(audit 2026-07, Library-attach UI): the async job wrapper must forward these
straight to create_beat, and existing callers (no attachment) must see
identical behavior to before — this is the exact function the Library page's
Attach flow calls through plg_webview.py -> web/src/api.ts."""

from __future__ import annotations

import plg_api
import plg_webview


def _sync_run_job(monkeypatch):
    """Replace _run_job with a synchronous call so start_beat's target
    lambda executes immediately, in-thread, for a deterministic assertion."""
    def _run(_kind, target):
        return target(lambda *_a, **_kw: None)

    monkeypatch.setattr(plg_api, "_run_job", _run)


def test_start_beat_forwards_upload_params_to_create_beat(monkeypatch):
    _sync_run_job(monkeypatch)
    seen: dict = {}

    def _fake_create_beat(prompt, **kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(plg_api, "create_beat", _fake_create_beat)

    plg_api.start_beat(
        "dark trap beat",
        midi_path="/tmp/upload.mid",
        reference_audio_path=None,
        respect_uploaded_scale=True,
    )

    assert seen.get("midi_path") == "/tmp/upload.mid"
    assert seen.get("reference_audio_path") is None
    assert seen.get("respect_uploaded_scale") is True


def test_start_beat_forwards_genre_override_to_create_beat(monkeypatch):
    _sync_run_job(monkeypatch)
    seen: dict = {}

    def _fake_create_beat(prompt, **kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(plg_api, "create_beat", _fake_create_beat)

    plg_api.start_beat(
        "dark trap beat",
        midi_path="/tmp/upload.mid",
        genre_override="west_coast",
    )

    assert seen.get("genre_override") == "west_coast"


def test_start_beat_without_attachment_matches_old_behavior(monkeypatch):
    _sync_run_job(monkeypatch)
    seen: dict = {}

    def _fake_create_beat(prompt, **kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(plg_api, "create_beat", _fake_create_beat)

    plg_api.start_beat("dark trap beat")

    assert seen.get("midi_path") is None
    assert seen.get("reference_audio_path") is None
    assert seen.get("respect_uploaded_scale") is False


def test_webview_bridge_forwards_upload_params_to_plg_api(monkeypatch):
    """The exact JS-facing method window.pywebview.api.start_beat(...) calls."""
    seen: dict = {}

    def _fake_start_beat(
        prompt, locale, smart_plan, midi_path, reference_audio_path, respect_uploaded_scale, genre_override
    ):
        seen.update(
            prompt=prompt,
            locale=locale,
            smart_plan=smart_plan,
            midi_path=midi_path,
            reference_audio_path=reference_audio_path,
            respect_uploaded_scale=respect_uploaded_scale,
            genre_override=genre_override,
        )
        return {"ok": True, "job_id": "abc"}

    monkeypatch.setattr(plg_webview.plg_api, "start_beat", _fake_start_beat)

    api = plg_webview.Api()
    api.start_beat(
        "dark trap beat",
        "ru",
        None,
        "/tmp/upload.mid",
        None,
        True,
        "west_coast",
    )

    assert seen == {
        "prompt": "dark trap beat",
        "locale": "ru",
        "smart_plan": None,
        "midi_path": "/tmp/upload.mid",
        "reference_audio_path": None,
        "respect_uploaded_scale": True,
        "genre_override": "west_coast",
    }


def test_webview_bridge_defaults_match_old_call_shape(monkeypatch):
    seen: dict = {}

    def _fake_start_beat(
        prompt, locale, smart_plan, midi_path, reference_audio_path, respect_uploaded_scale, genre_override
    ):
        seen.update(
            midi_path=midi_path,
            reference_audio_path=reference_audio_path,
            respect_uploaded_scale=respect_uploaded_scale,
            genre_override=genre_override,
        )
        return {"ok": True, "job_id": "abc"}

    monkeypatch.setattr(plg_webview.plg_api, "start_beat", _fake_start_beat)

    api = plg_webview.Api()
    api.start_beat("dark trap beat")

    assert seen == {
        "midi_path": None,
        "reference_audio_path": None,
        "respect_uploaded_scale": False,
        "genre_override": None,
    }

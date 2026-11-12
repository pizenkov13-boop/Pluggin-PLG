"""A beat is charged only when it is actually delivered.

Cloud (`/v1/generate`): the credit is reserved atomically before the LLM call
(so concurrent requests can't over-spend), and must be handed back on ANY
failure before the response is built — including a prompt rejected after the
reservation and post-generation bookkeeping errors, not just the LLM call.

Local (`create_beat`, non-cloud): the quota is charged only after the FLP is
on disk.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import plg_api


# --- cloud: /v1/generate -----------------------------------------------------


class _Insert:
    def __init__(self, fail: bool):
        self._fail = fail

    def insert(self, _row):
        return self

    def execute(self):
        if self._fail:
            raise RuntimeError("generation_logs insert failed")
        return None


def _wire_generate(monkeypatch, *, atomic=True, was_trial=False, gen_exc=None, log_fails=False):
    """Stub every collaborator of main.generate; return the call recorder."""
    from cloud.app import main

    calls = SimpleNamespace(reserve=0, refund=[], consume=0, generated=0)

    class _Client:
        def table(self, name):
            assert name == "generation_logs"
            return _Insert(log_fails)

    monkeypatch.setattr(main, "service_client", lambda: _Client())
    monkeypatch.setattr(main, "client_ip", lambda _req: "1.2.3.4")
    monkeypatch.setattr(main, "flag_enabled", lambda *a, **k: False)
    monkeypatch.setattr(main, "enforce_not_banned", lambda *a, **k: None)
    monkeypatch.setattr(main, "require_registered_device", lambda _c, _u, d: d or "dev-1")
    monkeypatch.setattr(main, "profile_row", lambda *_a, **_k: {"plan": "base"})
    monkeypatch.setattr(main, "roll_profile", lambda row: row)
    monkeypatch.setattr(main, "check_kill_switch", lambda *a, **k: None)
    monkeypatch.setattr(main, "check_generate_limits", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "quota_snapshot", lambda _row: {"remaining": 29})
    monkeypatch.setattr(main, "record_spend", lambda *a, **k: None)
    monkeypatch.setattr(main, "record_generation_ip", lambda *a, **k: None)
    monkeypatch.setattr(main, "mark_first_beat", lambda *a, **k: None)
    monkeypatch.setattr(main, "track_event", lambda *a, **k: None)
    monkeypatch.setattr(main, "touch_device", lambda *a, **k: None)

    def _reserve(_c, _u, _row):
        calls.reserve += 1
        return {"atomic": atomic, "was_trial": was_trial}

    def _refund(_c, _u, *, was_trial):
        calls.refund.append(was_trial)

    def _consume(*_a, **_k):
        calls.consume += 1

    def _gen(*_a, **_k):
        calls.generated += 1
        if gen_exc is not None:
            raise gen_exc
        return {"bpm": 140}, {"model": "m", "cost_usd": 0.01}

    monkeypatch.setattr(main, "reserve_beat", _reserve)
    monkeypatch.setattr(main, "refund_beat", _refund)
    monkeypatch.setattr(main, "consume_beat", _consume)
    monkeypatch.setattr(main, "generate_beat_pattern", _gen)
    return main, calls


def _call(main, prompt="dark trap"):
    body = main.GenerateBody(prompt=prompt)
    return main.generate(body, SimpleNamespace(), user_id="u1", x_plg_device="dev-1")


def test_success_keeps_the_reserved_credit(monkeypatch):
    main, calls = _wire_generate(monkeypatch)
    out = _call(main)
    assert out["ok"] is True
    assert calls.refund == []


def test_llm_failure_refunds_the_credit(monkeypatch):
    main, calls = _wire_generate(monkeypatch, was_trial=True, gen_exc=RuntimeError("upstream down"))
    with pytest.raises(RuntimeError):
        _call(main)
    assert calls.refund == [True]  # trial credit goes back as a trial credit


def test_bookkeeping_failure_after_generation_refunds_the_credit(monkeypatch):
    # The beat was generated but the request still fails (500) — the user got
    # nothing, so the credit must come back.
    main, calls = _wire_generate(monkeypatch, log_fails=True)
    with pytest.raises(RuntimeError, match="generation_logs"):
        _call(main)
    assert calls.generated == 1
    assert calls.refund == [False]


def test_rejected_prompt_never_reserves_a_credit(monkeypatch):
    # sanitize_prompt used to run AFTER reserve_beat, so a whitespace-only /
    # flagged prompt raised 400 with the credit already taken and not returned.
    main, calls = _wire_generate(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _call(main, prompt="   ")
    assert exc.value.status_code == 400
    assert calls.reserve == 0
    assert calls.refund == []
    assert calls.generated == 0


def test_legacy_path_charges_only_after_generation_succeeds(monkeypatch):
    main, calls = _wire_generate(monkeypatch, atomic=False, gen_exc=RuntimeError("upstream down"))
    with pytest.raises(RuntimeError):
        _call(main)
    assert calls.consume == 0  # nothing was charged, so nothing to refund
    assert calls.refund == []

    main, calls = _wire_generate(monkeypatch, atomic=False)
    _call(main)
    assert calls.consume == 1


# --- local: create_beat ------------------------------------------------------


def _wire_create_beat(monkeypatch, tmp_path, *, pipeline_exc=None, flp_written=True):
    import flp_writer

    library_root = tmp_path / "library"
    midi_root = tmp_path / "PLG_MIDI"
    library_root.mkdir()
    midi_root.mkdir()

    consumed: list[int] = []

    monkeypatch.setattr(plg_api, "_cloud_enabled", lambda: False)
    monkeypatch.setattr(plg_api, "has_api_key", lambda: True)
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: library_root)
    monkeypatch.setattr(plg_api, "resolve_midi_dir", lambda: midi_root)
    monkeypatch.setattr(plg_api, "has_available_midi_material", lambda *a, **k: True)
    monkeypatch.setattr(plg_api, "ensure_can_consume_beat", lambda: None)
    monkeypatch.setattr(plg_api, "consume_beat", lambda: consumed.append(1))
    monkeypatch.setattr(plg_api, "get_quota", lambda: {})
    monkeypatch.setattr(plg_api, "projects_dir", lambda: tmp_path)
    monkeypatch.setattr(plg_api, "PATTERN_JSON", tmp_path / "pattern.json")
    monkeypatch.setattr(plg_api, "_sync_generated_beat_async", lambda *a, **k: None)
    monkeypatch.setattr(plg_api, "_set_last_prompt", lambda *_a, **_k: None)

    def _pipeline(_prompt, **_kw):
        if pipeline_exc is not None:
            raise pipeline_exc
        return {"bpm": 140, "style": "trap", "tracks": {}}

    def _write(_pattern, target, **_kw):  # write_flp_session also takes bundle_sounds
        if not flp_written:
            raise OSError("disk full")
        return target

    monkeypatch.setattr(plg_api, "run_pipeline", _pipeline)
    monkeypatch.setattr(flp_writer, "flp_base_word", lambda _p: "core")
    monkeypatch.setattr(flp_writer, "next_versioned_flp_path", lambda d, w: d / f"PLG_{w}__v01.flp")
    monkeypatch.setattr(flp_writer, "write_flp_session", _write)
    return consumed


def test_local_beat_is_charged_once_the_flp_exists(tmp_path, monkeypatch):
    consumed = _wire_create_beat(monkeypatch, tmp_path)
    result = plg_api.create_beat("dark trap")
    assert result["ok"] is True and result["flp"]
    assert consumed == [1]


def test_local_pipeline_failure_costs_nothing(tmp_path, monkeypatch):
    consumed = _wire_create_beat(monkeypatch, tmp_path, pipeline_exc=RuntimeError("boom"))
    result = plg_api.create_beat("dark trap")
    assert result["ok"] is False
    assert consumed == []


def test_local_flp_write_failure_costs_nothing(tmp_path, monkeypatch):
    consumed = _wire_create_beat(monkeypatch, tmp_path, flp_written=False)
    result = plg_api.create_beat("dark trap")
    assert not result.get("flp")
    assert consumed == []

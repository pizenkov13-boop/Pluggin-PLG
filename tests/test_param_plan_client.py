"""Param Plan client (param_plan_client.py): modes, fallback, cache. No network anywhere."""
from __future__ import annotations

import copy
import json

import pytest

import param_plan
import param_plan_client as client
from genre_profiles import PROFILES

BASE = param_plan.baseline_plan(PROFILES["trap"])


def _pattern(shift=0.0, genre="trap"):
    notes = [
        {"time_step": i * 1.0 + shift, "note": "A3" if i % 2 else "C4", "length": 0.5, "velocity": 90}
        for i in range(16)
    ]
    return {
        "bpm": 140.0, "style": genre, "user_prompt": "",
        "plg_variety": {"genre": genre},
        "tracks": {"melody_lead": notes},
        "plg_melody_energy": {"busyness": 0.4, "pace": "driving", "onset_rate": 2.0},
        "plg_arrangement": [{"name": "hook", "start_bar": 0, "bars": 4, "energy": 0.9, "rolls": True}],
    }


GOOD = {"schema": 1, "hat_swing": 0.6, "melody_lag_ms": 4, "notes": "tight and busy",
        "roll": {"prob": {"hot": 0.5}}}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("PLG_PARAM_PLAN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("PLG_PARAM_PLAN", raising=False)
    monkeypatch.delenv("PLG_PARAM_PLAN_MODEL", raising=False)
    client.reset_memory_cache()
    yield
    client.reset_memory_cache()


class Ask:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, 0

    def __call__(self, document):
        self.calls += 1
        self.document = document
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.answer)


def _install(monkeypatch, **kw):
    ask = Ask(**kw)
    monkeypatch.setattr(client, "_ask", ask)
    return ask


# --- modes ----------------------------------------------------------------------------------


def test_default_and_garbage_modes_are_off(monkeypatch):
    assert client.mode() == "off"
    for raw in ("", "banana", "1"):
        monkeypatch.setenv("PLG_PARAM_PLAN", raw)
        assert client.mode() == "off"
    monkeypatch.setenv("PLG_PARAM_PLAN", " Shadow ")
    assert client.mode() == "shadow"


def test_off_makes_no_calls_writes_nothing_and_leaves_the_pattern_alone(monkeypatch, tmp_path):
    ask = _install(monkeypatch, answer=GOOD)
    pattern = _pattern()
    before = copy.deepcopy(pattern)
    assert client.plan_for(pattern) is None
    client.attach(pattern)
    assert ask.calls == 0 and pattern == before
    assert not (tmp_path / "cache").exists()


def test_shadow_records_the_validated_plan_on_the_pattern(monkeypatch):
    monkeypatch.setenv("PLG_PARAM_PLAN", "shadow")
    ask = _install(monkeypatch, answer=GOOD)
    pattern = _pattern()
    client.attach(pattern)
    plan = pattern[client.PLAN_KEY]
    assert ask.calls == 1 and plan["source"] == "llm" and plan["ok"] is True
    assert plan["values"]["hat_swing"] == 0.6 and plan["values"]["roll"]["prob"]["hot"] == 0.5
    assert plan["values"]["roll"]["prob"]["quiet"] == BASE["roll"]["prob"]["quiet"]
    assert plan["notes"] == "tight and busy" and plan["model"] == client.MODEL_DEFAULT
    assert json.loads(json.dumps(plan)) == plan  # the output_pattern.json writer must survive it
    # the request carried the decider input and nothing else about the song
    assert ask.document["baseline"] == {k: v for k, v in BASE.items() if k not in param_plan.DRUM_VELOCITY_KNOBS}
    assert "tracks" not in ask.document


def test_the_model_is_configurable_and_part_of_the_cache_key(monkeypatch):
    pattern = _pattern()
    first = client.cache_key(pattern, "trap")
    monkeypatch.setenv("PLG_PARAM_PLAN_MODEL", "other-model")
    assert client.cache_key(pattern, "trap") != first


# --- fallback -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error", [TimeoutError("timed out"), ConnectionError("down"), OSError("no key"), ValueError("bad json")]
)
def test_any_failure_returns_the_baseline_and_never_raises(monkeypatch, error):
    monkeypatch.setenv("PLG_PARAM_PLAN", "shadow")
    _install(monkeypatch, error=error)
    plan = client.plan_for(_pattern())
    assert plan["source"] == "fallback" and plan["values"] == BASE and plan["ok"] is False
    assert type(error).__name__ in plan["reason"]


@pytest.mark.parametrize("answer", [[], "text", {"schema": 7}, {}, None])
def test_an_invalid_answer_is_a_baseline_and_is_not_cached(monkeypatch, answer):
    monkeypatch.setenv("PLG_PARAM_PLAN", "shadow")
    ask = _install(monkeypatch, answer=answer)
    plan = client.plan_for(_pattern())
    assert plan["source"] == "fallback" and plan["values"] == BASE
    client.reset_memory_cache()
    client.plan_for(_pattern())
    assert ask.calls == 2  # nothing was written to the disk cache


def test_out_of_range_answer_is_clamped(monkeypatch):
    monkeypatch.setenv("PLG_PARAM_PLAN", "shadow")
    _install(monkeypatch, answer={"schema": 1, "hat_swing": 9, "melody_lag_ms": 500, "mystery": 1})
    plan = client.plan_for(_pattern())
    assert plan["values"]["hat_swing"] == 1.28 and plan["values"]["melody_lag_ms"] == 25.0
    assert "hat_swing" in plan["clamped"] and "mystery" in plan["dropped"]


def test_a_broken_pattern_still_does_not_raise(monkeypatch):
    monkeypatch.setenv("PLG_PARAM_PLAN", "shadow")
    _install(monkeypatch, answer=GOOD)
    for pattern in ({}, {"tracks": None, "bpm": "x"}):
        client.plan_for(pattern)  # must not raise; the result is not the point


# --- cache ------------------------------------------------------------------------------------------


def test_the_cache_prevents_a_second_call_for_the_same_midi(monkeypatch):
    monkeypatch.setenv("PLG_PARAM_PLAN", "shadow")
    ask = _install(monkeypatch, answer=GOOD)
    first = client.plan_for(_pattern())
    second = client.plan_for(_pattern())
    assert ask.calls == 1 and second["source"] == "cache"
    assert second["values"] == first["values"]


def test_the_cache_survives_a_restart_via_the_disk_file(monkeypatch, tmp_path):
    monkeypatch.setenv("PLG_PARAM_PLAN", "shadow")
    ask = _install(monkeypatch, answer=GOOD)
    client.plan_for(_pattern())
    assert (tmp_path / "cache" / client.CACHE_FILE).is_file()
    client.reset_memory_cache()  # a new process
    again = client.plan_for(_pattern())
    assert ask.calls == 1 and again["source"] == "cache" and again["values"]["hat_swing"] == 0.6


def test_a_different_melody_or_genre_is_a_different_entry(monkeypatch):
    monkeypatch.setenv("PLG_PARAM_PLAN", "shadow")
    ask = _install(monkeypatch, answer=GOOD)
    client.plan_for(_pattern())
    client.plan_for(_pattern(shift=0.25))
    client.plan_for(_pattern(genre="drill"))
    assert ask.calls == 3


def test_a_failure_is_remembered_for_this_run_only(monkeypatch):
    """Three clone rounds must not mean three timeouts; the next run retries."""
    monkeypatch.setenv("PLG_PARAM_PLAN", "shadow")
    ask = _install(monkeypatch, error=TimeoutError("slow"))
    for _ in range(3):
        assert client.plan_for(_pattern())["source"] == "fallback"
    assert ask.calls == 1
    client.reset_memory_cache()
    client.plan_for(_pattern())
    assert ask.calls == 2


def test_cached_answers_are_validated_again_on_every_read(monkeypatch):
    monkeypatch.setenv("PLG_PARAM_PLAN", "shadow")
    _install(monkeypatch, answer={"schema": 1, "hat_swing": 1.0})
    client.plan_for(_pattern())
    monkeypatch.setitem(param_plan.LIMITS, "hat_swing", (0.0, 0.5))
    assert client.plan_for(_pattern())["values"]["hat_swing"] == 0.5


def test_the_disk_cache_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setenv("PLG_PARAM_PLAN", "shadow")
    monkeypatch.setattr(client, "CACHE_MAX_ENTRIES", 3)
    _install(monkeypatch, answer=GOOD)
    for i in range(6):
        client.plan_for(_pattern(shift=i * 0.1))
    saved = json.loads((tmp_path / "cache" / client.CACHE_FILE).read_text(encoding="utf-8"))
    assert len(saved) == 3


# --- the sound cannot change --------------------------------------------------------------------------


def test_humanize_ignores_the_recorded_plan():
    """Shadow: the key rides on the pattern; the humanized notes are identical without it."""
    import random

    from beat_humanize import humanize_pattern

    def source():
        snares = [{"time_step": b * 4 + 2.0, "note": "D1", "length": 0.25, "velocity": 110} for b in range(8)]
        hats = [
            {"time_step": b * 4 + s * 0.5, "note": "C5", "length": 0.1, "velocity": 80}
            for b in range(8) for s in range(8)
        ]
        return {"bpm": 140.0, "style": "trap", "tracks": {"snare": snares, "hi_hats": hats}}

    random.seed(1)
    plain = humanize_pattern(source())
    shadowed = source()
    shadowed[client.PLAN_KEY] = {"source": "llm", "values": {"hat_swing": 0.0, "melody_lag_ms": 25.0}}
    random.seed(1)
    other = humanize_pattern(shadowed)
    assert other["tracks"] == plain["tracks"]


def test_backend_hook_runs_between_arrange_and_humanize_and_is_guarded():
    import inspect

    import backend_core

    source = inspect.getsource(backend_core._humanize_and_ingest)
    for var in ("pattern", "source"):
        arrange = source.index(f"arrange_song({var})")
        hook = source.index(f"_record_param_plan({var})")
        humanize = source.index("humanize_pattern(", arrange)
        assert arrange < hook < humanize
    guard = inspect.getsource(backend_core._record_param_plan)
    assert "except Exception" in guard


def test_the_default_model_is_gpt_6_luna_pro_and_it_is_the_only_quoted_model_id():
    import inspect

    assert client.MODEL_DEFAULT == "gpt-6-luna-pro" and client.model_name() == "gpt-6-luna-pro"
    assert inspect.getsource(client).count('"gpt-') == 1

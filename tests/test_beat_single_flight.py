"""Only one beat may be generating at a time.

A generation costs a credit (reserved server-side in cloud mode, charged locally
otherwise). A second ``start_beat`` used to start a second pipeline in parallel —
a second credit for what the user sees as one beat. It now attaches to the job
that is already running. See the project issue log (not included) 0ap.
"""

from __future__ import annotations

import threading
import time

import pytest

import plg_api


@pytest.fixture(autouse=True)
def _clean_registry():
    with plg_api._jobs_lock:
        plg_api._jobs.clear()
    yield
    with plg_api._jobs_lock:
        plg_api._jobs.clear()


@pytest.fixture
def slow_create_beat(monkeypatch):
    """create_beat that blocks until released, counting how often it really ran."""
    gate = threading.Event()
    calls: list[str] = []

    def _create_beat(prompt, **_kwargs):
        calls.append(prompt)
        assert gate.wait(10), "test never released the generation"
        return {"ok": True}

    monkeypatch.setattr(plg_api, "create_beat", _create_beat)
    yield gate, calls
    gate.set()


def _wait_done(job_id, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if plg_api.get_job(job_id)["status"] != "running":
            return
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_a_second_start_attaches_to_the_running_beat(slow_create_beat):
    gate, calls = slow_create_beat
    first = plg_api.start_beat("one")
    second = plg_api.start_beat("two")

    assert second["job_id"] == first["job_id"]
    assert second.get("reused") is True and not first.get("reused")
    gate.set()
    _wait_done(first["job_id"])
    assert calls == ["one"]  # the pipeline (and its credit) ran once


def test_simultaneous_starts_still_run_one_pipeline(slow_create_beat):
    gate, calls = slow_create_beat
    handles: list[dict] = []
    barrier = threading.Barrier(8)

    def _click():
        barrier.wait()
        handles.append(plg_api.start_beat("x"))

    threads = [threading.Thread(target=_click) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({h["job_id"] for h in handles}) == 1
    gate.set()
    _wait_done(handles[0]["job_id"])
    assert calls == ["x"]


def test_a_new_beat_can_start_after_the_last_one_finished(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(plg_api, "create_beat", lambda prompt, **_kw: calls.append(prompt) or {"ok": True})

    first = plg_api.start_beat("one")
    _wait_done(first["job_id"])
    second = plg_api.start_beat("two")
    _wait_done(second["job_id"])

    assert second["job_id"] != first["job_id"]
    assert calls == ["one", "two"]


def test_a_failed_beat_does_not_block_the_retry(monkeypatch):
    results = iter([{"ok": False, "error": "boom", "error_type": "cloud"}, {"ok": True}])
    monkeypatch.setattr(plg_api, "create_beat", lambda prompt, **_kw: next(results))

    first = plg_api.start_beat("one")
    _wait_done(first["job_id"])
    assert plg_api.get_job(first["job_id"])["status"] == "error"

    second = plg_api.start_beat("two")
    _wait_done(second["job_id"])
    assert second["job_id"] != first["job_id"]
    assert plg_api.get_job(second["job_id"])["status"] == "done"


def test_a_crashed_beat_does_not_block_the_retry(monkeypatch):
    attempts = iter([RuntimeError("engine crashed"), {"ok": True}])

    def _create_beat(_prompt, **_kw):
        outcome = next(attempts)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(plg_api, "create_beat", _create_beat)
    first = plg_api.start_beat("one")
    _wait_done(first["job_id"])
    second = plg_api.start_beat("two")
    _wait_done(second["job_id"])
    assert plg_api.get_job(second["job_id"])["status"] == "done"


def test_a_job_presumed_dead_does_not_lock_generation_forever(slow_create_beat, monkeypatch):
    gate, calls = slow_create_beat
    first = plg_api.start_beat("hung")
    with plg_api._jobs_lock:
        plg_api._jobs[first["job_id"]].started_at = time.time() - plg_api._JOB_STALE_S - 1

    second = plg_api.start_beat("fresh")

    assert second["job_id"] != first["job_id"]
    gate.set()
    _wait_done(second["job_id"])
    assert set(calls) == {"hung", "fresh"}


def test_opening_fl_is_not_blocked_by_a_running_beat(slow_create_beat, monkeypatch):
    gate, _calls = slow_create_beat
    monkeypatch.setattr(plg_api, "open_in_fl", lambda: {"ok": True})
    beat = plg_api.start_beat("one")
    fl = plg_api.start_open_in_fl()

    assert fl["job_id"] != beat["job_id"] and not fl.get("reused")
    gate.set()
    _wait_done(beat["job_id"])
    _wait_done(fl["job_id"])

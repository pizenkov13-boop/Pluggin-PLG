"""A failed generation always reaches the screen as plain words, and a finished one says where the file is.

The tester-build report: press Create, the loader runs, Home comes back - no beat, no message. The beat existed
(``Projects\\PLG_*__vNN.flp`` next to the exe); the screen just never said so, because the tester build does not open
FL Studio on its own and Home had nothing to show after a success. The same screen could also stay silent on a
failure: a beat whose ``.flp`` could not be written still came back as ``ok``, ``plg_session.log`` held no line about a
generation at all, and engine errors were shown as raw English developer text (one even pointed at an API gateway).

Pinned here: the .flp failure is an error; every job leaves lines in ``plg_session.log``; every error_type the backend
can return maps to a localized sentence (checked with the real ``web/src/errors.ts`` and locale files under Node).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import plg_api
from test_generation_quota_rollback import _wire_create_beat

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_registry():
    with plg_api._jobs_lock:
        plg_api._jobs.clear()
    yield
    with plg_api._jobs_lock:
        plg_api._jobs.clear()


def _wait(job_id: str, timeout: float = 5.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        snap = plg_api.get_job(job_id)
        if snap.get("status") in ("done", "error"):
            return snap
        time.sleep(0.01)
    raise AssertionError("job did not finish")


# --- the .flp that was not written is an error, not a quiet "ok" ---------------------------------------------------


def test_flp_write_failure_is_an_error_the_screen_can_show(tmp_path, monkeypatch):
    consumed = _wire_create_beat(monkeypatch, tmp_path, flp_written=False)
    result = plg_api.create_beat("dark trap")
    assert result["ok"] is False
    assert result["error_type"] == "flp_write_failed"
    assert not result.get("flp")
    assert consumed == []


# --- plg_session.log says what happened to every job --------------------------------------------------------------


@pytest.fixture
def session_lines(monkeypatch):
    import plg_log

    lines: list[str] = []
    monkeypatch.setattr(plg_log, "log_event", lines.append)
    return lines


def test_a_finished_job_is_logged(session_lines):
    handle = plg_api._run_job("probe", lambda _prog: {"ok": True, "flp": r"C:\x\Projects\PLG_core__v03.flp"})
    _wait(handle["job_id"])
    assert any("started" in line for line in session_lines)
    assert any("done: PLG_core__v03.flp" in line for line in session_lines)


def test_a_failed_job_is_logged_with_its_type(session_lines):
    result = {"ok": False, "error": "Every attempt failed.\nmore detail", "error_type": "quality_gate"}
    handle = plg_api._run_job("probe", lambda _prog: result)
    snap = _wait(handle["job_id"])
    assert snap["status"] == "error"
    failed = [line for line in session_lines if "FAILED" in line]
    assert failed and "type=quality_gate" in failed[0] and "Every attempt failed." in failed[0]
    assert "more detail" not in failed[0]  # one line per job


def test_a_crashing_job_is_logged_and_still_finishes(session_lines):
    def boom(_prog):
        raise RuntimeError("kaboom")

    handle = plg_api._run_job("probe", boom)
    snap = _wait(handle["job_id"])
    assert snap["status"] == "error" and snap["error_type"] == "unknown"
    assert any("CRASHED" in line for line in session_lines)


def test_a_blank_error_text_does_not_break_the_log_line(session_lines):
    handle = plg_api._run_job("probe", lambda _prog: {"ok": False, "error": "  ", "error_type": "unknown"})
    snap = _wait(handle["job_id"])
    assert snap["status"] == "error"
    assert any("FAILED type=unknown" in line for line in session_lines)


def test_a_broken_log_never_breaks_a_job(monkeypatch):
    import plg_log

    def broken(_message):
        raise OSError("disk full")

    monkeypatch.setattr(plg_log, "log_event", broken)
    handle = plg_api._run_job("probe", lambda _prog: {"ok": True})
    assert _wait(handle["job_id"])["status"] == "done"


# --- every error the backend can return becomes a localized sentence ----------------------------------------------

NODE = shutil.which("node")

_NODE_SCRIPT = r"""
import { pathToFileURL } from "node:url";
const root = process.argv[1];
const types = JSON.parse(process.argv[2]);
const load = (p) => import(pathToFileURL(root + "/web/src/" + p).href);
const { beatErrorMessage } = await load("errors.ts");
const { en } = await load("i18n/locales/en.ts");
const { ru } = await load("i18n/locales/ru.ts");
const lookup = (pack) => (key, params = {}) => {
  let node = pack;
  for (const part of key.split(".")) node = node?.[part];
  if (typeof node !== "string") return key;
  return node.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ""));
};
const out = {};
for (const type of types) {
  const raw = "RAW English developer text " + type;
  const snap = { status: "error", error: raw, error_type: type, result: { ok: false, error: raw, error_type: type } };
  out[type] = { raw, en: beatErrorMessage(snap, lookup(en)), ru: beatErrorMessage(snap, lookup(ru)) };
}
const noType = { status: "error", error: "RAW", error_type: null, result: null };
out["(none)"] = { raw: "RAW", en: beatErrorMessage(noType, lookup(en)), ru: beatErrorMessage(noType, lookup(ru)) };
process.stdout.write(JSON.stringify(out));
"""


def _backend_error_types() -> list[str]:
    types = {error_type for _needle, error_type, _template in plg_api._ENGINE_ERROR_RULES}
    # Types create_beat / _pipeline_error / the job runner return besides the rules above.
    types |= {"missing_file", "llm", "unknown", "no_api_key", "flp_write_failed", "validation", "quota", "some_future_type"}
    return sorted(types)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_every_backend_error_type_has_a_plain_sentence_in_both_languages():
    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", _NODE_SCRIPT, str(REPO), json.dumps(_backend_error_types())],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    messages = json.loads(proc.stdout)
    assert len(messages) >= len(_backend_error_types())
    for type_, found in messages.items():
        for lang in ("en", "ru"):
            text = found[lang]
            assert text.strip(), (type_, lang)
            assert "RAW English developer text" not in text, f"{type_}/{lang} shows the raw backend text"
            assert not text.startswith(("errors.", "home.")), f"{type_}/{lang} is a missing translation key"
        assert any("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in found["ru"]), f"{type_}: the Russian text is not Russian"
    # The gateway/API-key wording of the old fallback never reaches the screen.
    for type_ in ("llm", "no_api_key", "unknown"):
        assert "gateway" not in messages[type_]["en"].lower() and "api key" not in messages[type_]["en"].lower()


def test_the_screen_wiring_is_in_place():
    """Static guards for the parts Node cannot run: errors are shown, a success shows NOTHING."""
    app = (REPO / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "beatErrorMessage(final, t)" in app, "a failed beat job must go through the localized mapping"
    assert "!result.flp" in app and "errors.genNoFlp" in app, "a done job with no project file must show an error"
    # Product decision: after a successful beat the screen says nothing (FL Studio opens by itself); no "beat ready"
    # text, banner or button may come back without the owner's say-so.
    hero = (REPO / "web" / "src" / "components" / "HomeHero.tsx").read_text(encoding="utf-8")
    for source in (app, hero):
        assert "beatDone" not in source and "showFolder" not in source and "beatReady" not in source.replace("beat_ready", "")
    assert 'role="status"' not in hero


def test_a_missing_fl_studio_is_silent_in_the_tester_build():
    """Auto-open after a beat: with FL Studio missing the tester build shows nothing (the beat is in Projects)."""
    app = (REPO / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "onOpenInFl(Boolean(status?.tester_build))" in app
    assert 'final.status === "error" && quiet' in app and "if (!quiet) setError(" in app

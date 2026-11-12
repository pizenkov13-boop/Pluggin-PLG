"""The MIDI route's retry budget, and the error text when it is exhausted.

Measured 2026-08-15: ~1 in 45 generations on the shipping corpus died with
"No generation passed the final kick/808 symbiosis gate; nothing was
exported." — a press-Generate-get-an-error for a buyer. Two defects behind it:

  * the MIDI route ran with clone_rounds=1 AND retries=0, so a single
    hard fail was unrecoverable (`best_pattern` is only assigned when a
    candidate is NOT a hard fail);
  * the message named the kick/808 gate unconditionally, though
    `beat_quality._symbiosis_failures` also gates on the full-kit audit and
    the anti-clone guard reaches the same raise without consulting an audit
    at all.

See the project issue log (not included) §0k.
"""
from __future__ import annotations

import pytest

import backend_core


class _Report:
    def __init__(self, *, score=60.0, passed=True, hard_fail=False, warnings=None):
        self.score = score
        self.passed = passed
        self.hard_fail = hard_fail
        self.warnings = warnings or []
        self.proxy_score = score
        self.reward_score = None
        self.genre = "trap"


# --------------------------------------------------------------------------
# _hard_fail_summary — report the gating warnings, not the cosmetic ones
# --------------------------------------------------------------------------

def test_summary_keeps_only_the_warnings_that_gate():
    report = _Report(
        hard_fail=True,
        warnings=[
            "hi_hats feel a little sparse",           # cosmetic, never gates
            "kick/808 symbiosis gate: off_scale_808",  # gating
            "full-kit symbiosis gate: no backbeat",    # gating
        ],
    )
    summary = backend_core._hard_fail_summary(report)
    assert "off_scale_808" in summary
    assert "no backbeat" in summary
    assert "sparse" not in summary, "cosmetic advice must not bury the real cause"


def test_summary_falls_back_to_everything_when_nothing_matches():
    report = _Report(hard_fail=True, warnings=["something unexpected"])
    assert backend_core._hard_fail_summary(report) == "something unexpected"


def test_summary_handles_a_report_with_no_warnings():
    assert backend_core._hard_fail_summary(_Report(hard_fail=True)) == ""


@pytest.mark.parametrize("marker", [
    "kick/808 symbiosis gate: context:808_not_on_midi_root",
    "full-kit symbiosis gate: drop has no hats",
    "no notes",
    "missing bpm",
    "tracks missing",
])
def test_every_hard_fail_marker_is_recognised(marker):
    report = _Report(hard_fail=True, warnings=["cosmetic note", marker])
    assert marker in backend_core._hard_fail_summary(report)


# --------------------------------------------------------------------------
# the diagnostics that must outlive the failing run
# --------------------------------------------------------------------------

def test_give_up_diagnostics_are_logged_before_the_exception(caplog):
    with caplog.at_level("ERROR"):
        backend_core._log_give_up_diagnostics(
            ["round 0: hard fail (score 41.0) — kick/808 symbiosis gate: off_scale_808"],
            rounds=3,
            clone_blocked_rounds=0,
        )
    text = caplog.text
    assert "gave up after 3 round" in text
    assert "off_scale_808" in text


def test_diagnostics_call_out_the_anti_clone_guard_separately(caplog):
    with caplog.at_level("ERROR"):
        backend_core._log_give_up_diagnostics(
            ["round 0: anti-clone guard (91% similar to recent history)"],
            rounds=3,
            clone_blocked_rounds=3,
        )
    text = caplog.text
    assert "anti-clone guard" in text
    assert "beat history" in text, "the operator needs the actionable hint"


def test_diagnostics_survive_having_no_reasons(caplog):
    with caplog.at_level("ERROR"):
        backend_core._log_give_up_diagnostics([], rounds=3, clone_blocked_rounds=0)
    assert "none recorded" in caplog.text


# --------------------------------------------------------------------------
# the budget itself
# --------------------------------------------------------------------------

def test_midi_route_no_longer_runs_with_a_single_round():
    """Guards the actual regression: clone_rounds=1 made one hard fail fatal."""
    import inspect

    src = inspect.getsource(backend_core.run_pipeline)
    assert "clone_rounds = 3 if midi_first else 5" in src, (
        "the MIDI route must keep a retry budget; with 1 round a single "
        "hard fail is an unrecoverable error for the user"
    )


def test_error_text_no_longer_blames_the_kick_808_gate_unconditionally():
    import inspect

    src = inspect.getsource(backend_core.run_pipeline)
    assert "No generation passed the final kick/808 symbiosis gate" not in src
    assert "No generation succeeded after" in src
    assert "Reasons —" in src

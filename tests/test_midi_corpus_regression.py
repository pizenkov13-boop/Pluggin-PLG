"""Local development exam for the curated PLG_MIDI regression corpus.

The corpus is intentionally user-owned and is not required in a clean CI
checkout.  When present, every MIDI exercises the same full path users hear;
no files are exported or modified by this test.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from arranger import arrange_song
from beat_humanize import humanize_pattern
from beat_quality import evaluate
from midi_ingest import ingest_uploaded_midi


CORPUS_ROOT = Path(__file__).resolve().parents[1] / "PLG_MIDI"


def _corpus_files() -> list[Path]:
    return sorted({*CORPUS_ROOT.rglob("*.mid"), *CORPUS_ROOT.rglob("*.midi")}) if CORPUS_ROOT.is_dir() else []


@pytest.mark.parametrize("midi_path", _corpus_files(), ids=lambda path: path.name)
def test_curated_midi_survives_full_beat_pipeline(midi_path: Path):
    """A future rhythm/arrangement edit must pass every real MIDI case."""
    if not midi_path.is_file():
        pytest.skip("local MIDI corpus is unavailable")
    logging.disable(logging.CRITICAL)
    try:
        pattern = {"bpm": 140, "style": "dark trap", "user_prompt": "dark trap", "tracks": {}}
        result = ingest_uploaded_midi(pattern, midi_path)
        assert result["ok"], result.get("warning")
        arrange_song(pattern)
        output = humanize_pattern(pattern)
        audit = output.get("plg_kick808_audit") or {}
        fullkit = output.get("plg_fullkit_symbiosis_audit") or {}
        global_symbiosis = output.get("plg_global_symbiosis_audit") or {}
        quality = evaluate(output)
    finally:
        logging.disable(logging.NOTSET)

    bpm = float(output["bpm"])
    duration_seconds = float(output["plg_total_bars"]) * 4.0 * 60.0 / bpm
    passport = output.get("plg_midi_passport") or {}
    expected_bpm = (passport.get("tempo") or {}).get("bpm")
    if expected_bpm is not None:
        assert bpm == pytest.approx(float(expected_bpm), abs=0.01)
    # The arranger rounds a 120s target to a whole 4-bar section, so the best
    # achievable error is half a section. That is ~3.4s at 140 BPM but ~7.1s
    # at 68 — the old fixed 114-126s window was derived at 140 BPM and, below
    # ~76 BPM, demanded a duration no whole 4-bar form can produce. Express
    # the tolerance the way the rounding actually works; this is *tighter*
    # than the old window above 160 BPM and only wider where the musical
    # quantum genuinely is.
    from arranger import BEATS_PER_BAR, TARGET_SONG_SECONDS

    half_section = (BEATS_PER_BAR * 2) * 60.0 / bpm
    assert abs(duration_seconds - TARGET_SONG_SECONDS) <= half_section + 0.05, (
        f"{duration_seconds:.1f}s at {bpm} BPM "
        f"({output['plg_total_bars']} bars); target {TARGET_SONG_SECONDS}s "
        f"+/- {half_section:.1f}s"
    )
    assert audit.get("passed") is True, audit.get("reasons")
    assert fullkit.get("passed") is True, fullkit.get("reasons")
    assert global_symbiosis.get("passed") is True, global_symbiosis.get("reasons")
    assert quality.passed is True, quality.warnings


def test_curated_midi_corpus_is_present_for_local_regression():
    """Avoid silently claiming coverage when a developer removed the corpus."""
    if not CORPUS_ROOT.exists():
        pytest.skip("clean checkout without local PLG_MIDI corpus")
    assert len(_corpus_files()) >= 11

"""Moved out of tests/test_midi_upload_pipeline.py on 2026-08-11.

These two tests exercise the reference-audio / midi_corpus / library-MIDI
fallback tier that `_humanize_and_ingest` no longer has. That tier was
already unreachable from the desktop route before it was deleted — see this
folder's README. `_humanize_and_ingest` now raises
"The selected MIDI could not be ingested" instead of falling through.

Not collected by `pytest` (pytest.ini sets testpaths = tests).
"""

from __future__ import annotations

from pathlib import Path

from backend_core import _humanize_and_ingest

from tests.test_midi_upload_pipeline import _base_pattern  # type: ignore


def test_no_midi_path_keeps_existing_behavior(tmp_path: Path):
    pattern = _base_pattern()
    result = _humanize_and_ingest(
        pattern,
        library_root=tmp_path,
        user_prompt="trap beat",
        style="trap",
    )
    assert "plg_midi_upload_result" not in result


def test_invalid_upload_with_empty_midi_folder_still_falls_back_to_library_tiers(tmp_path: Path):
    (tmp_path / "midi").mkdir()  # exists but empty — resolve_midi_folder must no-op

    pattern = _base_pattern()
    result = _humanize_and_ingest(
        pattern,
        library_root=tmp_path,
        user_prompt="dark melody trap beat",
        style="trap",
        midi_path=tmp_path / "does_not_exist.mid",
    )
    assert result["plg_midi_upload_result"]["ok"] is False
    assert "plg_midi_ingest" not in result

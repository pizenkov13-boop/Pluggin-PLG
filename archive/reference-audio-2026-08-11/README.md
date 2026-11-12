# Archived: reference-audio reversal (basic-pitch)

Archived 2026-08-11 by product decision — "back to it separately, later".

## Contents

| File | Was |
|---|---|
| `reference_reverse.py` | Demucs + basic-pitch transcription of a reference track into a melody / 808 the pipeline could ingest |
| `test_reference_reverse.py`, `test_reference_upload_api.py`, `test_reference_upload_pipeline.py` | its tests |

## Why it went

Same shape as stem splitting: complete backend, job runner, bridge method,
typed helper — and no UI entry point. The in-app file picker that would have
fed it never worked (`LibraryView` ignored its `onAttach` prop), so
`reference_audio_path` was always `null` in practice.

## What changed in the live tree

- `backend_core._humanize_and_ingest`: the `reference_audio_path` branch and
  the whole fallback tier below it — `ingest_reference_audio` →
  `midi_corpus.ingest_from_corpus` → `midi_ingest.ingest_library_midi` — were
  removed. That tier was **already unreachable**: `run_pipeline` always sets
  `plg_midi_first`, and the `raise ValueError("The selected MIDI could not be
  ingested…")` above it fires first, because `has_available_midi_material` and
  `resolve_midi_folder` read the same candidate list. It is gone rather than
  left looking active.
- `plg_api`: `apply_reference_track`, `reference_reverse_status`,
  `start_reference_reverse` removed; the reference probe dropped from
  `get_status`; `plg_reference_reverse` dropped from the export meta.
- `plg_webview.Api`, `web/src/api.ts`, `devBridge.ts`: matching entries removed.

`reference_audio_path` stays in the `create_beat` / `run_pipeline` /
`start_beat` signatures as inert compatibility plumbing.

`midi_corpus.py` and `midi_ingest.ingest_library_midi` were **not** archived:
`midi_corpus` is still imported by `flex_melody_map`, `melody_sample_bank` and
`midi_profiler`, and `ingest_library_midi` remains part of `midi_ingest`'s
public surface with its own tests.

# Archived: stem splitting (Demucs)

Archived 2026-08-11 by product decision — "back to it separately, later".

## Contents

`stem_split.py` — Demucs wrapper that split an audio file into 4 stems, with a
real progress callback and a graceful "Demucs is not installed" path.

## Why it went

Complete backend, a job runner in `plg_api`, a bridge method in
`plg_webview.Api`, typed helpers in `web/src/api.ts` — and **no UI entry point
anywhere in `web/src`**. Nothing a user could click ever reached it.

## What changed in the live tree

- `plg_api`: `split_stems_file`, `start_stem_split`, `stems_status` removed;
  the `demucs_available` field dropped from `get_app_info`.
- `plg_webview.Api`: `start_stem_split`, `stems_status` removed.
- `web/src/api.ts` + `devBridge.ts`: `startStemSplit`, `stemsStatus`, the
  `stem_split` job branch and their `PywebviewApi` declarations removed.
- `plg_app.py` (tkinter fallback): the "Split Stems from File…" menu item, its
  worker and its two queue handlers removed.
- `PLG.spec`: the torch/demucs `excludes` stay — they now guard against an
  accidental transitive import rather than an intentional optional feature.

`requirements-optional.txt` still lists demucs; harmless, and it documents what
to reinstall if this comes back.

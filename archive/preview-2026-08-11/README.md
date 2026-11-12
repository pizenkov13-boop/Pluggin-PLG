# Archived: in-app beat preview

Archived 2026-08-11 by product decision.

## What is here

| File | Was |
|---|---|
| `beat_preview.py` | numpy/pydub renderer that baked the pattern + chosen one-shots into a short stereo WAV |
| `test_beat_preview.py` | its 5 tests (all passing at archive time) |
| `Waveform.tsx` / `Waveform.css` | the React waveform player that would have shown it |

## Why

The product opens FL Studio automatically the moment a beat is generated, so an
extra listening step inside PLG is redundant. The user hears the beat in the
DAW, on the real channels, with the real samples.

## Category

This was **not** dead code by neglect — it was complete, tested, and listed in
`PLG.spec` `hiddenimports`, but never wired to `plg_api` / `plg_webview.Api` /
`web/src/api.ts`. It was removed because the feature is unwanted, not because
it was broken.

## Removed alongside it

- `beat_preview` dropped from `PLG.spec` `hiddenimports`.

Nothing in the live engine imported `beat_preview`, so nothing else changed.
`beat_preview.render_pattern` was also documented as the render engine for
`slowed_reverb_export.py`, which is not part of this folder either.

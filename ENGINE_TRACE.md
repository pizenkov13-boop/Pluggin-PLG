# newPLUG: engine execution trace

> [PROJECT_MAP.md](PROJECT_MAP.md) §2–§3 carries the same route with current
> file:line anchors and the in-order call table for `humanize_pattern`. Use that
> for navigation; use this file for the export-ownership reasoning.

This document records the verified live route as of 2026-08-09. It is a
decision aid for future changes: do not infer ownership from filenames, and do
not copy MIDI notes or exact mix values from a reference FLP.

## Normal desktop route

```text
React UI (web/src)
  -> pywebview.Api.start_beat
  -> plg_api.start_beat / create_beat
  -> backend_core.run_pipeline (local, MIDI required)
  -> _humanize_and_ingest
  -> arranger.arrange_song
  -> beat_humanize.humanize_pattern
  -> attach_sounds_to_pattern + MIDI exports + output_pattern.json
  -> flp_writer.write_flp_session
  -> PLG_Session.flp
```

`run_plg.bat -> PLG.pyw -> plg_webview.py` is the regular desktop entry. The
tkinter `plg_app.py` is a failure-only launcher fallback; it is not the normal
webview route. The separate cloud service is not part of this local
material-first generation route.

## Export ownership

1. `create_beat` writes exactly the finalized, validated pattern to
   `PLG_Session.flp`; it does not create alternative mutations or re-bake it.
2. Later `Open in FL` reloads `output_pattern.json` and preserves its saved
   kit whenever every active core sound path still exists. It invokes the
   sample selector only as a missing-file fallback.
3. `prepare_pattern_for_flp_export` is called after sound/mix preparation and
   before JSON, MIDI and FLP export in normal generation and Open-in-FL.
   It applies final 808 register, MIDI-only kick-pair and space-mix rules once
   to the canonical pattern. `flp_writer.build_flp` keeps the same idempotent
   call only as a compatibility guard for direct legacy callers.

Do not use generated FLPs as code evidence. The normal architecture now creates
one explicit, saved final pattern before every export; MIDI and FLP are views
of that same prepared data.

## Musical ownership found in the active path

### Melody

`midi_ingest` is the only melody owner. Every imported MIDI note stays on
`melody_lead`; no LLM, hook composer, symbolic composer or secondary melody
lane may replace or extend it.

### Drums and hats

- `drum_blueprint` / MIDI phrase planning generate the base kit.
- Symbiosis and validation may adjust timing, density and dynamics, but may
  not create a new melodic lane.

### Low end

`808_bass` is the **only** low-end lane, in every genre, permanently. It is
generated deterministically and sealed against the final kick grid.

`sub_808` is not a lane. It is not an option, a per-genre choice, or a future
possibility — the decision has been made and re-confirmed (2026-08-11). It is
rejected by `midi_validate.MIDI_DISABLED_LANES`, absent from the FL bridge
scripts, and must not be reintroduced in code, tests, docs or FLP output.
`sub_808_gen.py` survives only because `midi_validate` imports its register and
length constants in order to *reject* sub_808 material.

## Golden FLP: safe structural observations

The user reference is `C:\PLUG.FLP\newmixer123.flp`, SHA-256
`0EE9E1C9CB861A25638094F728D7756637C01806FD63B0748CEA615EF2769E47`.
It is a valid FL Studio 25.2.2 project with seven channels: melody/FLEX, kick,
`808_bass`, hats, clap, snare and snare layer. It does not contain a visible
`sub_808` channel. Its 808 pattern is sparse and kick-paired, with notes in a
low register; hats, clap and snare use separate lanes.

These are comparison constraints only. Never copy the reference's notes,
arrangement, filenames, exact velocities or exact fader values into a new
generation. The existing hard-coded mixer values in `mix_gain_staging.py` were
documented as measured from that reference and must be reviewed as ranges or
sample-aware rules during the consolidation stage.

The bundled fallback 808 assets are short (about 0.42 s for the `808_bass`
fallback). The tail guard correctly rejects them when a generated
sustained/transposed 808 would outlast the source.

**This is no longer only a design note — it is a live failure.** Confirmed by a
real run on 2026-08-11: with no reachable user library, the guard drops the
`808_bass` lane and `midi_validate.enforce_midi_mode` then blocks the whole
export. Supply compatible longer fallback samples or constrain the fallback
composition. Do not silently disable the tail guard. See the project issue log (not included) §1.4.

## Confirmed work still required

1. Choose and enforce one melody owner for each genre/profile.
2. Ship a bundled `808_bass` fallback long enough to survive the tail guard, so
   an empty library degrades instead of failing (the project issue log (not included) §1.4).

Closed since this document was written:

- The `sub_808` questions (which writer owns it, may it coexist with
  `808_bass`) — **closed**: there is one low-end lane, `808_bass`, always.
- ✅ The non-trap genre regressions in `tests/test_any_genre.py` — **fixed
  2026-08-11.** All 14 failures had one root cause: the synthesized genre
  bassline went to `sub_808`, a lane `midi_validate` deletes. That file is now
  fully green (the project issue log (not included) §6). Other files still fail; the suite as a
  whole is not green.

## Corrected during this audit

The final two-way kick/808 pairing seal used to add a support kick for every
unpaired generated 808, even when the bar already had two kicks. That could
turn the engine's own legal sparse pocket into three kicks in a bar, after
which the final quality audit rejected it as `too_many_kicks_per_bar`.

`_ensure_808_on_every_kick` now preserves the two-kicks-per-bar cap: an extra,
unpaired 808 is discarded instead of creating a third kick. The focused music
test set passes 49/49, including a new regression test for this exact case.

`PLG_Session.flp` is now the canonical generated session rather than a hidden
alias for V1, and Open-in-FL keeps its saved kit instead of re-picking samples
on every launch. The focused export/launch/music tests pass 24/24.

The final FLP prerequisites are now applied before the canonical JSON and MIDI
are saved, rather than appearing only while FLP bytes are being serialized.
The focused verification set passes 65/67. The two remaining failures are
pre-existing mixer-layout expectations reproduced in the original workspace;
they are recorded for a separate mixer consolidation, not treated as a
migration regression.

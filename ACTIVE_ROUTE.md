# Active generation route

> New to this codebase? Read
> [internal notes (not included)](internal notes (not included)) and
> [PROJECT_MAP.md](PROJECT_MAP.md) first. This file is the *contract*;
> `PROJECT_MAP.md` tells you which file and function to open.

This is the only route a coding agent may change when the task concerns beat
generation:

`internal material library (MIDI + kit audio) -> midi_ingest -> arranger -> beat_humanize ->
melody_beat_symbiosis/FLEX -> midi_validate -> flp_writer`

## Drum density comes from the MIDI, not the genre

`drum_density.build_drum_density_card` decides how busy kick/808 and hats may
be, from the uploaded melody's own onset rate and energy. `GenreProfile` is a
bounded stylistic multiplier on top of it (0.94-1.14 across the ten lanes), not
the source. Do not add a fixed per-bar density constant to the pocket, the
section floor, the gap filler or the export audits — that is exactly what made
a sparse MIDI ungeneratable on every genre (the project issue log (not included) §0.1).
`midi_lowend_director.LowendRootMap` is likewise the one owner of which root an
808 attack takes; never re-derive a private fallback for it.

The final FLP may contain only these lanes:

`melody_lead`, `kick`, `808_bass`, `hi_hats`, `clap`, `snare`, `snare_layer`.

`melody_lead` is the user's synth MIDI.  It is not a drum or bass source.
The kick, 808 and drums are generated deterministically around it.  The LLM
may choose a compatible FLEX role/preset, but it must not compose notes,
arrange extra melodic layers, infer a beat from a prompt, or create a second
bass lane.

One further, optional LLM use exists: the Param Plan (`PLG_PARAM_PLAN=off|shadow|on`, default `off`). It may only
choose bounded humanize numbers (hat swing/velocity, roll probability/depth, melody lag) that the deterministic
engine applies; it never places a note. See the owner map (not included), "Param Plan".

The public `prompt` parameters are compatibility plumbing only.  They are not
musical input.  Cloud generation, reference-audio reversal, prompt planning,
web reference search, Re-bake and Regenerate are outside this active route and
must not be restored as a fallback.

Before editing a generator, trace the request from `plg_api.create_beat` and
keep the lane contract in `midi_validate.py` passing (seven lanes, plus the
`chords` / `counter_melody` role lanes that `midi_roles` splits out of the
user's own notes).

## Genre lanes

PLG is a hip-hop/trap generator. Ten lanes are tuned and generate:

`trap`, `rage`, `opium`, `drill`, `west_coast`, `underground`, `pluggnb`,
`detroit`, `hyperpop`, `phonk`

They are `genre_profiles.DETAILED_GENRES`, and since 2026-08-11
`HIPHOP_LANES == DETAILED_GENRES`. Every other `GenreProfile` is a leftover
from an early "more genres is better" phase — never tuned to the same depth
(≈7 field overrides and 12 test references each, against ≈17 and 97 for the
ten) — and `clamp_to_focus` redirects it to a tuned lane before generation.
`_FOCUS_FALLBACK` targets must always be in `DETAILED_GENRES`.

Do not add a genre. Do not widen `HIPHOP_LANES`. `PLG_LANES=all` exists for
offline experiments only and is not a supported product mode.

## The single low-end lane

`808_bass` is the only low-end lane, always. `sub_808` is retired.

For the ten tuned lanes, `_apply_808_bass_lane` owns `808_bass`.
For every other profile, `beat_humanize._apply_sub_808_lane` publishes the
idiomatic genre bassline there instead (`_publish_low_end`). Before
2026-08-11 that bassline went to `sub_808`, which `midi_validate` deletes, so
those genres generated with no low end and died in `enforce_midi_mode`.

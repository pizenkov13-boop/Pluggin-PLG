# FLP export component map

> Broader navigation: [PROJECT_MAP.md](PROJECT_MAP.md) §4.9. Mandatory reading
> before new code: [internal notes (not included)](internal notes (not included)).

This map is for coding agents changing the active MIDI-first export route.
It is backed by source and focused tests; generated `.flp` files are only
visual verification artifacts, never implementation evidence.

## Active route

```text
PLG_MIDI + PLG_Sounds
  -> backend_core.run_pipeline
  -> _humanize_and_ingest
  -> arranger.arrange_song
  -> beat_humanize.humanize_pattern / melody_beat_symbiosis
  -> midi_validate.enforce_midi_mode
  -> flp_template.build_playlist_clips
  -> flp_writer._arrangement_events
  -> flp_writer._playlist_pattern_clip
  -> flp_writer.write_flp_session
```

The user MIDI is `melody_lead`. It is not a kick, 808, or drum source.
The final MIDI-mode FLP may contain only:

`melody_lead`, `kick`, `808_bass`, `hi_hats`, `clap`, `snare`, `snare_layer`.

## Ownership and edit targets

| User-visible problem | First source to inspect | Export boundary / proof |
| --- | --- | --- |
| MIDI melody notes or its FLEX sound | `midi_ingest.py`, `melody_beat_symbiosis.py`, `flex_*` | `midi_validate.py`, `tests/test_flex_*.py` |
| kick/808 relationship | `beat_humanize.py`, `midi_phrase_drums.py`, `midi_lowend_director.py` | kick/808 tests, then `flp_writer.py` |
| hi-hat rhythm | hat pipeline and `beat_humanize.py` | hat tests, MIDI validation |
| clap/snare placement | `midi_phrase_drums.py`, placement symbiosis | `midi_validate.py` |
| Playlist row, clip position, visual width, or FLP bytes | `flp_template.py`, then `flp_writer.py` | `tests/test_flp_writer.py` plus a fresh visual FL check |

Do not change an earlier generic generator when the final owner above replaces
its values later in the route.

## Playlist identity rules

`flp_template.build_playlist_clips()` creates the per-export clip list.
Every `PlaylistClip` has these fields:

- `track_key`: stable lane identity, e.g. `snare_layer`.
- `playlist_track`: visual FL Playlist row; it is compacted dynamically in
  MIDI mode.
- `start_bar` and `length_bars`: source-level visual placement.
- `pattern_iid`: export-local pattern identifier.
- `pattern_name`: display label such as `PLG Snare Layer Main 5`.

`pattern_iid` and the numeric suffix in a display name are **not global
identifiers**. They can change when the arrangement changes or when identical
section patterns are deduplicated. Never hard-code `Main 5 = iid 55`.

For a bug reported against a visible FL clip, first inspect the generated
`plg_playlist_clips` metadata and identify it by the combination of
`track_key`, `start_bar`, and section/pattern name. Then add a focused test
using an equivalent source arrangement.

## Snare Layer terminal-clip rule

The historical visual-tail bug was not a note-generation bug. It was an FLP
Playlist-boundary issue affecting the final `snare_layer` clip in the song.

Current implementation:

1. `_arrangement_events()` finds the `snare_layer` clip with the greatest
   `start_bar`.
2. `_playlist_pattern_clip(..., explicit_trim=True)` is used only for that
   terminal clip.
3. Its Playlist record keeps the normal 96-PPQ length field and stores an
   explicit end bound in project ticks.
4. All other clips retain the normal untrimmed Playlist offsets.

For the observed arrangement, the terminal clip was displayed as `PLG Snare
Layer Main 5`. That label is evidence for that arrangement only; the rule is
the terminal `snare_layer` clip, not a permanent magic name or IID.

Regression proof: `tests/test_flp_writer.py::test_last_playlist_clip_uses_96_ppq_and_explicit_trim`.

## Change rule

Before changing FLP export behavior:

1. Read this file, `ACTIVE_ROUTE.md`, and the owner map (not included).
2. Trace the requested lane to its final owner.
3. Add or update a focused test for the exact visible failure.
4. Run that test before making a fresh FLP for visual confirmation.
5. Do not alter unrelated lanes, playlist records, or generated artifacts to
   make a test appear to pass.

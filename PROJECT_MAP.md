# PROJECT MAP — where to change what

**Read this first if you are an AI agent or a new developer.** It answers one
question: *"I need to change X — which file, which function?"*

Before you write any new code, read
[internal notes (not included)](internal notes (not included)). This project has been
damaged before by agents adding a second generator next to an existing one. The
protocol exists to stop that.

> **Line numbers below were verified on 2026-08-12.** They drift. The *function
> name* is the identifier; the line number is a hint. If a line number is wrong,
> grep the function name and fix the number — don't assume the map is wrong about
> ownership.

---

## 1. What PLG is, in four lines

A Windows desktop tool. The user drags a **MIDI melody** and a **drum kit** onto
Home; PLG copies both into its internal material library, then generates a
finished FL Studio project `PLG_Session.flp`.

The melody is the user's. Everything else — kick, 808, hats, clap, snare — is
generated deterministically *around* that melody. There is no prompt box. The
LLM's one allowed musical decision was which FLEX preset plays the melody —
inert since 2026-08-27: the captured FLEX blobs were removed and the melody
exports as a Sampler channel (the project issue log (not included) §0ag, §4.7 below).

The final FLP contains exactly seven lanes:

`melody_lead` · `kick` · `808_bass` · `hi_hats` · `clap` · `snare` · `snare_layer`

---

## 2. The spine — one request, end to end

Every generation follows this and only this. If your change isn't on this path,
it does not affect the product.

```text
run_plg.bat
 └─ PLG.pyw ─────────────────────────────── 3-line launcher
    └─ plg_webview.py  class Api ────────── pywebview JS↔Python bridge
       └─ plg_api.start_beat:1336 ───────── async job wrapper
          └─ plg_api.create_beat:967 ────── quota → genre gate → engine → FLP
             └─ backend_core.run_pipeline:650
                ├─ has_available_midi_material ── no MIDI ⇒ hard error, no fallback
                ├─ scan_library ───────────────── internal material library → catalog
                ├─ _humanize_and_ingest:479
                │   ├─ drum_defaults.ensure_drum_tracks
                │   ├─ midi_ingest.ingest_uploaded_midi / resolve_midi_folder
                │   ├─ melody_beat_symbiosis.ensure_early_flex_and_energy:158
                │   ├─ arranger.arrange_song:771
                │   └─ beat_humanize.humanize_pattern:5381   ← the big one, §3
                ├─ beat_quality.evaluate ──────── hard-fail gate on kick/808
                ├─ starter_kit.attach_sounds_to_pattern:637 ── wav per lane
                └─ _export_pattern_artifacts:613
                    ├─ midi_validate.enforce_midi_mode:214 ── seven-lane contract
                    ├─ mix_gain_staging.attach_vocal_ready_mix
                    ├─ flp_writer.prepare_pattern_for_flp_export:1806
                    └─ midi_export.export_stem_session → output_midi/
             └─ flp_writer.write_flp_session:2014  (plg_api.py:1103) → PLG_Session.flp
```

Second entry point: **Open in FL** (`plg_api.open_in_fl:1161` →
`fl_launch.open_beat_in_fl`) reloads `output_pattern.json` and rewrites the FLP.
It does not regenerate music. See [FL_BRIDGE.md](FL_BRIDGE.md).

---

## 3. Inside `beat_humanize.humanize_pattern` (the 1,100-line core)

`beat_humanize.py` is 7,153 lines and holds most of the musical decisions.
`humanize_pattern` starts at **:5917**. This is its execution order — the single
most useful thing in this document, because *later stages overwrite earlier
ones*. Line numbers for stages 22-33 re-verified 2026-08-26; 1-21 on 2026-08-21.

| # | Line | Call | Owns |
|---|---|---|---|
| 1 | 6010 | `melody_beat_symbiosis.build_melody_energy_card` | how energetic the melody is |
| 2 | **6022** | **`drum_density.build_drum_density_card`** | **how busy the kit may get** |
| 3 | 6045 | `midi_structure_analysis.attach_midi_structure` | phrase/section map of the melody |
| 4 | 6053 | `midi_beat_planner.attach_midi_beat_plan` | beat plan |
| 5 | 6060 | `midi_phrase_rhythm_plan.attach_phrase_rhythm_plan` | phrase rhythm plan |
| 6 | 6067 | `midi_fullkit_director.attach_fullkit_plan` | full-kit plan (and the per-bar brief) |
| 7 | 6078 | `producer_move_director.choose_moves` | which producer moves are on ⚠️ returns the same four on every corpus file — the project issue log (not included) §0v.4 |
| 8 | **6117** | **`midi_phrase_drums.apply_phrase_drums`** | **source kick grid + the whole backbeat**, via `_backbeat_figure` |
| 9 | 6125 | `drum_blueprint.apply_drum_blueprint` | ⚠️ *fallback only* — see §6 |
| 10 | 6150+ | hat branch (`hihat_rolls` / `symbolic_composer`) | base hat pattern — **reads the backbeat** for roll targets |
| 11 | 6206 | `drum_density.thin_generated_hats` | density trim on hats |
| 12 | 6221 | `midi_fullkit_director.apply_generated_hat_contract` | clears hats in MIDI rests |
| 13 | 6231 | `apply_pre_snare_shift` + `humanize_velocities` | backbeat 2-5 ms early, velocity |
| 14 | 6247 | `add_pre_clap_roll` | clap lead-in micro-notes ⚠️ not section-aware — §0v.3 |
| 15 | **6279** | **`duplicate_clap_layer`** + `add_pre_snare_pickup` | **the layer** — both read `plg_backbeat_figures` |
| 16 | 6304 | `midi_fullkit_director.apply_strong_phrase_answers` | kit answers melody phrases |
| 17 | 6305 | `midi_fullkit_director.apply_section_pulse_switch` | per-section pulse |
| 18 | 6441 | `apply_midi_arrangement_transitions` → `enforce_midi_section_density` | **deletes** any lane outside its section's `active_tracks` |
| 19 | 6444 | `_apply_sub_808_lane` | ⚠️ *only when not MIDI or not `bass808_enabled`* |
| 20 | **6445** | **`_apply_808_bass_lane`** | **final kick + 808 pocket** |
| 21 | 6768 | `melody_beat_symbiosis.apply_melody_beat_symbiosis` | limited melodic modifiers; `lock_drums_to_melody_accents` is the largest hat writer |
| 22 | 7075 | `midi_placement_symbiosis.apply_midi_placement_symbiosis` | placement nudges — **last owner of `time_step`** |
| 22a | 7083 | **`_apply_section_dynamics`** | **section level for `clap`/`snare`/`snare_layer` + the melody's octave layer** — last dynamics writer before the "nothing after this may alter dynamics" line. Reads `arranger.section_velocity_scales`; `hi_hats` deliberately excluded — the project issue log (not included) §0aa |
| 22b | 7086 | `_apply_outro_fade` | four-bar taper on every lane except kick/808 (their normalizer does its own) |
| **23** | **7118** | **`_seal_midi_kick808_pocket`** | **last musical owner of kick/808** |
| 24 | 7119 | `_ensure_808_on_every_kick` | pairing seal (respects 2-kick/bar cap) |
| 25 | 7136 | `_cap_midi_kicks_per_bar` | max 2 kicks per bar |
| 25a | 7139 | `_normalize_kick808_dynamics` | kick/808 dynamics — the lane base times the SECTION's level, plus the fade and the optional ±6 dust. Re-run after any later pass that ADDS a low-end note |
| 26 | 7140 | `_audit_kick808_symbiosis` | **gating** audit — checks against `_expected_kick808_velocity`, the same function the normalizer writes from |
| 27 | 7142 | `_correct_off_scale_808_notes` | one allowed harmonic repair |
| 28 | 7147 | `_audit_midi_kick808_context` | **gating** audit |
| 29 | 7167 | `_repair_midi_kick808_context` | one allowed repair — re-runs the dynamics normalizer first, the project issue log (not included) §0aa.3 |
| 30 | 7192 | `_correct_808_vertical_clashes` | vertical 808 vs sounding melody — **final 808 pitch**, post-audit by design (2026-08-15) |
| 31 | 7224 | `_audit_hat_symbiosis` | advisory only, never blocks |
| 32 | 7231 | `midi_fullkit_director.audit_fullkit_contract` | advisory |
| 32a | 7383 | **`_apply_section_melody_arrangement`** | **which melody notes a SECTION plays** — the last writer of `melody_lead`, deliberately after the whole kick/808 chain so no drum owner can see it. Removes and only removes; reads `arranger.section_melody_plan`. the project issue log (not included) §0ac |
| 33 | 7235 | `midi_fullkit_director.audit_global_midi_symbiosis` | final seven-lane audit |

**The rule this table encodes:** editing step 8 to change the final KICK is
pointless — step 20 rebuilds it and step 23 seals it. Always change the *latest*
owner of the thing you care about.

**The backbeat is the exception, and it cuts the other way.** `clap` / `snare` /
`snare_layer` have no later generator at all: step 8 is their only source, and
steps 13-18 and 21-22 are modifiers that can move, thin or delete but never
re-place them. That is why a backbeat decision belongs in
`midi_phrase_drums._backbeat_figure` and nowhere else — and why step 10 matters
when you touch it, since `hihat_rolls` picks its roll targets from the snares
step 8 wrote, and the roll↔kick chain (the project issue log (not included) §0m.3) then moves the
low end by an amount no single owner controls.

---

## 4. Topic index

### 4.1 Kick

| I want to change… | File · function |
|---|---|
| where kicks land (source grid) | `midi_phrase_drums.apply_phrase_drums:535`, `_kick_note:59` |
| the final kick pocket | `beat_humanize._apply_808_bass_lane:3645` |
| the last word on kick timing | `beat_humanize._seal_midi_kick808_pocket:5097` |
| whether a hi-hat roll may delete a kick | `midi_lowend_director.roll_take_verdict` — **the single owner** (2026-08-19); floor from `drum_density.min_kicks_per_4bar`, law constant `ROLL_THINNING_FLOOR_PER_4BAR`; the roll's side of the yield is `midi_fullkit_director.apply_lowend_roll_yield` |
| minimum spacing between kicks | `midi_phrase_drums.MIN_LOWEND_GAP_BEATS:20` (= 1.0) ⚠️ `beat_humanize._seal_midi_kick808_pocket` still hard-codes `1.0` at `:5141`/`:5171` instead of importing it — change **both** or they disagree (the project issue log (not included) §0.2a) |
| max kicks per bar | `beat_humanize._cap_midi_kicks_per_bar:4852` (cap = 2) |
| how full a bar the MANUFACTURED passes may leave | `midi_phrase_drums._lowend_bar_allowance` — **the single owner** (2026-08-20); one map, built once, read by `_enforce_section_pocket` AND `_fill_time_based_lowend_gaps` so they cannot disagree. Composes the section ceiling, the phrase-exit lift and the bounded rest |
| whether the melody firmly chose the accent, or the sort order did | `melody_beat_symbiosis.melody_accent_firmness` → `plg_melody_accent_firmness` — **the single owner** (2026-08-25); scores through `_accent_candidates`, shared with `extract_melody_accents`. Read via `midi_lowend_director._phrase_is_firm` (UNANIMOUS per phrase — a majority rule is a closed decision against, the project issue log (not included) §0x.1) |
| whether the quiet-section cell may be thinned | `drum_density.scaled_shape`'s `firm` flag (2026-08-25) — the step-down is skipped where the material decided every contested bar of the phrase. It took the "1" off 23 of Baton Rouge's 48 verse bars before this, the project issue log (not included) §0x |
| hook/drop vs verse density | `drum_density.section_pocket_ceiling` — **the single owner** (2026-08-20). Contrast is made by SUBTRACTION in the quiet sections; the hook has no headroom (44-70 bars/song already sit on the 2-kick cap). The old tempo-gated `_enforce_section_pocket` drive branch was deleted with it |
| whether the bar opens on the "1" | `midi_phrase_drums._displaced_anchor_point` (2026-08-20) — a body bar may open on a melody accent inside its first beat instead. Never in a phrase entry/exit bar, never in a peak section |
| a kick anticipating the backbeat | `midi_phrase_drums._pull_onto_backbeat_push` (2026-08-20) — the positive half of `_prefer_clear_of_backbeat`. An EIGHTH before, never a sixteenth (same fusion measurement as the project issue log (not included) §0s) |
| varying a repeated 4-bar low end | `beat_humanize._vary_repeated_lowend_cycle:4645`, driven by `_audit_midi_kick808_context`'s **non-gating** `repeated_blocks` meter (2026-08-20). The `identical_4bar_cycle` REASON is untouched — widening it would turn a never-firing check into a hard gate |
| how many kicks a busy/calm MIDI gets | `drum_density.build_drum_density_card` — **not** a per-genre constant |
| melody onsets in the half of the bar the backbeat lands in | `drum_density.melody_onsets_per_bar(..., from_beat_in_bar=2.0)` → card field `melody_onsets_late_per_bar` → brief field `melody_onsets_late` (2026-08-21). The whole-bar count is FLAT on most real material (Graveyard and BatonRouge sit at exactly 6 in every bar), so a median split on it is dead |
| which melody moments the drums answer | `melody_beat_symbiosis.extract_melody_accents` → `plg_melody_accents`; positional bonuses `_ACCENT_DOWNBEAT_BONUS` / `_ACCENT_HALF_BAR_BONUS` (2026-08-19). Read by 7 kick/808 passes, both gates, and `_local_score`'s highest-weighted term |
| which of two equally-scored candidates wins the bar | `melody_beat_symbiosis._crowds_the_backbeat` (2026-08-25) — a **tie-break, never a bonus**: it sits after the score in the sort key, so it is silent on any bar whose material separates its candidates (Baton Rouge and Graveyard are byte-identical). It demotes the stretch in front of the backbeat, read from `midi_phrase_drums._BACKBEAT_BAR_OFFSET`. Ranking "after the backbeat" above "in unison" is a closed decision against — it fails `incomplete_kick_coverage` on the last bar, the project issue log (not included) §0w.2 |
| whether a MANUFACTURED filler may land at all | `midi_phrase_drums._smears_into_melody` + `_fill_onto_the_melody` (2026-08-25) — a slot a sixteenth in front of a melody attack is moved onto that attack, or given up. Landing ON the melody or in a rest is untouched; window `_MELODY_FUSION_BEATS`, the same fusion reading as the project issue log (not included) §0s.2 |
| where a MANUFACTURED filler kick lands | `midi_phrase_drums._grid_fill_point` — quantises at birth and refuses the sixteenth right after a beat (2026-08-19); the density passes run AFTER `_quantize_lowend_onsets`, so they must honour the grid themselves |
| making one MIDI produce one beat | `generation_variety.pinned_seed` — `PLG_VARIETY_SEED=<int>`, switches off all four anti-clone mechanisms and the history write (2026-08-19) |
| kick syncopation / rolls | `beat_humanize.add_kick_syncopation:3275`, `_clear_kicks_from_hat_rolls:4498` |
| the deliberate double hit (kick-only pickup) | `midi_phrase_drums._plan_intentional_doubles` — the owner (2026-08-20); tag semantics live in `bass808_gen.is_ghost_kick` / `is_double_main` (role-consistent — read tags ONLY through them); the ghost rides `_intentional_solo_lowend`, the project issue log (not included) §0s |
| the FLP kick channel | `flp_template.active_export_channels:391` → `flp_writer` |

Tests: `tests/test_midi_phrase_drums.py`, `tests/test_drum_density.py`.

### 4.2 808 (`808_bass`)

`808_bass` is the **only** low-end lane, in every genre, permanently.

| I want to change… | File · function |
|---|---|
| 808 note placement | `beat_humanize._apply_808_bass_lane:3645` |
| which root an 808 attack takes | `midi_lowend_director.LowendRootMap:69` — **the single owner**; built by `beat_humanize._lowend_root_map:5152` |
| which **degree** an 808 attack takes (root / third / fifth) | `midi_lowend_director.LowendRootMap.tone_at` — **the call every 808 pitch consumer makes** (2026-08-16); slots planned in `_build_color_slots`, bounds in the module constants, master switch `CHORD_VOICING_ENABLED`; telemetry `plg_808_voicing` |
| **which role (bass / chords / lead / counter) a note of the imported MIDI plays** | `midi_roles` — **the single owner** (2026-10). One track, one channel is the normal MIDI, so it reads the notes: chord stabs and held notes vs the line, rhythmically. Written as `plg_role` on each note (and `plg_midi_roles` counts) by `midi_ingest.merge_notes_into_pattern`; **information only** — every note stays in `melody_lead`. `PLG_MIDI_ROLES=off` writes nothing. Tests: `tests/test_midi_roles.py` |
| **the `chords` and `counter_melody` lanes of a MIDI beat** | `midi_roles.split_into_lanes` — last step of `humanize_pattern` (after every audit read the whole melody as one lane), then `sanitize_midi_mode`. A lane is real only if `midi_roles.role_lane_live` (the split recorded it AND every note is tagged), so a generator that copies the melody cannot open one. Constant `midi_validate.MIDI_ROLE_LANES`; sound by `starter_kit` (chords/counter picks); channel style `flp_writer._MELODY_SAMPLER_KEYS`. **Lanes are OFF by default since 2026-10-27**: `midi_roles.lanes_enabled` (`PLG_MIDI_ROLE_LANES=on`) is the switch, so by default the whole melody stays in `melody_lead` and the tags are only evidence. `PLG_MIDI_ROLES=off`: no tags, no split, FLP byte-identical to before. Tests: `tests/test_midi_role_lanes.py` |
| chord quality (minor / major / open / dim) per window | `midi_structure_analysis._chord_quality` — measured after the window merge, so window geometry is unchanged. Beside `quality` / `chord_pcs` it also gives `sus` (sus2/sus4) and `symbol` (`Em7`, `Cmaj7`, `A5`; `chord_symbol`). Per window, also measured and never used to cut or elect anything: `bass_pc` (bass-role notes), `harmony` (the window read from the chord + bass role notes alone, `same_root_as_window`), `mid_bar_changes` (a different confident root in a bar's second half). Tests: `tests/test_midi_chord_reading.py` |
| where a chord change happens | `beat_humanize._lowend_harmonic_windows:5117` |
| the progression's anchor key (notes vs filename) | `beat_humanize._resolve_style_dna_key:5505` — **notes > embedded key_signature > filename hint** (2026-08-15) |
| the harmony chain's scale name | `beat_humanize._progression_scale_name:5133` — uploaded lock > pinned progression > `pattern["scale"]` (2026-08-15) |
| 808 vs the melody sounding at the same instant | `beat_humanize._correct_808_vertical_clashes:4484` — **final 808 pitch owner**, post-audit; telemetry `plg_808_vertical` (2026-08-15). Its last resort draws from `midi_lowend_director.LowendRootMap.harmony_pcs`, not the whole key (2026-08-20, the project issue log (not included) §0t) |
| which notes this song's harmony actually uses | `midi_lowend_director.LowendRootMap.harmony_pcs` — **the single owner** (2026-08-20); confident-window roots + their measured chord tones. Narrower than `scale_pcs`; empty when no window is confident. Never rebuild it locally |
| 808 onset composition | `midi_lowend_director.compose_phrase_onsets:403` |
| off-scale repair | `beat_humanize._correct_off_scale_808_notes:4317` |
| register / octave | `beat_humanize.apply_808_register_lift:1559`, `add_808_ascent:1503` |
| glides and slides | `beat_humanize.add_808_bar_slides:1674`, `build_chord_change_glides:782` |
| ducking under the kick | `beat_humanize.duck_808_under_kick:1753`, `_duck_808_bass_lane:5331` |
| the LLM 808 branch | `bass808_llm.py` — only when the phrase plan doesn't cover the song |
| 808 sample rejection ("no 808 lane!") | `library_quality.bass_808_tail_rejection_reason:27` |
| which 808 wav is picked | `starter_kit._healthy_808_replacement:580` → `sample_match.pick_best_for_track` (reachable pool on the dev library = 4 samples) |
| **no sample passes for kick / 808_bass / hi_hats — what plays instead** | `lane_fallback.resolve_lane_sample` (tier 1 user file failing only soft reasons, tier 2 `fallback_samples`), final arbiter `lane_fallback.ensure_required_lane_samples` called at the end of `starter_kit.attach_sounds_to_pattern`; recorded in `pattern["plg_sample_fallback"]`; export verdict `sample_match.rejection_reason_for_pattern`. **Default since 2026-10: no tier 2 — `resolve_lane_sample` returns None and records tier 0 "absent" (`lane_is_absent`), `flp_template.midi_hidden_keys` leaves that lane out of the .flp (channel, pattern, playlist row; the snare layer follows the snare); `midi_validate` accepts that record instead of a sample; `PLG_SUBSTITUTE_SOUNDS=on` restores tier 2.** the project issue log (not included) §0al, §0bb |
| **which bucket (808/hats/kits/fx/melodies/textures) a file is filed in** — decides which audio gate can hide it | `library_catalog._folder_category` (`_bucket_evidence`, `_resolve_bucket`); scans never move files (`restore_quarantined_audio` undoes the old behaviour). the project issue log (not included) §0am, §0an |
| **which folders make up the sample library** | `library_paths.companion_library_roots` / `library_roots` / `relative_in_library` — **the single owner** (2026-10-22). `app_config.get_samples_dir` still picks the primary root (env override → `PLG_Library` → `PLG_Sounds`); when `PLG_Library` AND `PLG_Sounds` both exist `library_catalog.scan_library` scans both, primary first (wins duplicates). Companion files are catalogued as `../PLG_Sounds/…` so `root / rel` still works; turn a resolved path back into a relative one ONLY through `relative_in_library`. A root set elsewhere through `PLG_SAMPLES_DIR` stands alone. Tests: `tests/test_library_two_roots.py` |
| **moving the user's MIDI / sounds out of the folders ("Clear folders")** | `material_archive.archive_folders` (the only mover: renames each top-level entry into `PLG_Archive/<date-time>/MIDI` or `/Library/<folder>`, rolls everything back if one rename fails, writes `manifest.json`); guards and the rescan in `plg_api.archive_material` (refused while a job/import runs, and for a folder that is not one of the default PLG_* folders). Never deletes a file. Tests: `tests/test_material_archive.py` |
| **what a file SOUNDS like when its name says nothing** (`sample_01.wav`) | `sound_type.identifies` / `conflicts` / `fit` — the single owner (2026-10-27); read by `sample_match._collect_rejections` (hard reason `sound_conflicts_with_track`, no `missing_track_identity` for a file that sounds like the lane), `lane_fallback._best_user_file` (rank by fit, rotate by variety seed), `starter_kit._wrong_type_for_lane`. Never overrules a labelled file; `PLG_SOUND_TYPE=off`. the project issue log (not included) §0av. Tests: `tests/test_sound_type.py` |
| how a file NAME is matched to a lane (score, avoid, identity) | `sample_match._kw_in` / `_words_of` (whole-word keys), `score_candidate(duration=...)`, `_extra_identity`. the project issue log (not included) §0ao |
| the sample's root note / usable register | `sample_pitch_passport.sample_pitch_passport:314` → `plg_808_register` |
| **the 808's actual AUDIO as FL plays it** | `wav_compat.convert_to_pcm24:180`, published by `flp_writer._ensure_fl_safe_sample:982` |
| whether the published copy is the RIGHT audio, not merely a loadable WAV | `wav_compat.published_content_mismatch` — **the single owner** (2026-08-25), built from the publisher's own decode so there is no second opinion about the correct bytes. Gate: `flp_writer._verify_published_sample`; evidence: `_preserve_bad_sample` (marker `WRONG AUDIO`). the project issue log (not included) §0y.1 |

⚠️ **The FLP's 808 channel does not point at the library wav.** It points at a
generated copy, `PLG_Sounds_FL/PLG_808_<sha1(src path)[:10]>_pcm24.wav`. That
file is the only 808 audio FL ever plays, it is re-published on every export,
and every previously exported `.flp` references it by absolute path. When
debugging *how the 808 sounds* (as opposed to which notes it plays), compare
that file — comparing `PLG_Sounds/` proves nothing. See the project issue log (not included) §0j.

**Never** re-derive your own harmonic fallback. Three disagreeing fallbacks are
exactly what made sparse MIDI ungeneratable (the project issue log (not included) §0.1).

Tests: `tests/test_midi_lowend_director.py`, `tests/test_sub_808_glide_and_lift.py`,
`tests/test_lowend_root_map.py`, `tests/test_808_chord_voicing.py`,
`tests/test_808_vertical_repair.py`.

### 4.3 Kick + 808 symbiosis (the pairing)

This is the most heavily guarded part of the engine. Read
[the owner map (not included)](the owner map (not included)) before touching it.

| Stage | Function |
|---|---|
| build the paired pocket | `midi_phrase_drums.build_phrase_kick_808_pocket:570` |
| coverage repair | `midi_phrase_drums.repair_active_kick808_coverage:707` |
| section floor | `midi_phrase_drums._enforce_section_pocket:293` |
| gap filler | `midi_phrase_drums._fill_time_based_lowend_gaps:365` |
| **final seal** | `beat_humanize._seal_midi_kick808_pocket:5097` |
| pairing guarantee | `beat_humanize._ensure_808_on_every_kick:4833` |
| micro-timing | `beat_humanize._apply_kick808_micro_timing:4673` |
| dynamics | `beat_humanize._normalize_kick808_dynamics` — writes, `_expected_kick808_velocity` decides (one owner for the writer AND the export gate; carries the section's level since 2026-08-26, the project issue log (not included) §0aa) |
| **the gate that can reject a whole generation** | `beat_humanize._audit_kick808_symbiosis:3992` + `_audit_midi_kick808_context:4238`, consumed by `beat_quality._symbiosis_failures:46` |

If generation dies with `RuntimeError: No generation succeeded after N
attempts`, that error comes from `backend_core.run_pipeline` and **the message
now carries the real per-round reasons** — read them rather than assuming the
kick/808 audits. Three different things reach that raise: a kick/808 audit hard
fail, a **full-kit** audit hard fail (`beat_quality._symbiosis_failures:46`
gates on both), or `generation_variety.is_too_similar` blocking every round
before any audit runs. `backend_core._log_give_up_diagnostics` also writes the
reasons to the log before the exception. Until 2026-08-15 the text blamed the
kick/808 gate unconditionally and the MIDI route had **zero** retries, so one
transient hard fail was fatal — the project issue log (not included) §0k.

### 4.4 Hi-hats

| I want to change… | File · function |
|---|---|
| **hat variation per generation** (ghost sixteenths, shifted eighths, accents) | `hihat_rolls.vary_hats` — called by `beat_humanize.humanize_pattern` just before `_apply_section_dynamics` (own rng from the variety seed; `PLG_HAT_VARIATION=off`); ghosts carry `hat_ghost`, ignored as rolls by `midi_lowend_director.rhythmic_anchors`. the project issue log (not included) §0ay. Tests: `tests/test_hat_variation.py` |
| base hat pattern (trap rolls) | `hihat_rolls.apply_trap_hi_hats:124` |
| ML/Markov hat path | `symbolic_composer.apply_drum_symbolic_pipeline` |
| roll engine | `hat_roll_engine.inject_snare_hat_rolls:125` (+ `snare_hit_steps:26`); the dead `apply_hat_rolling_engine` wrapper was deleted 2026-08-15 |
| **density trim from the MIDI** | `drum_density.thin_generated_hats` — reads `hat_multiplier`, **inverse** in melody busyness since 2026-08-19. Its structural protection classifies by NEAREST sixteenth after de-lagging, never by grid equality (2026-08-26): the pass runs after swing/forward-lag, so the old exact tests matched 0 notes of 513-643. The roll test runs FIRST by necessity — the project issue log (not included) §0v.10 |
| **how many hats one bar may carry** | `drum_density.hat_floor_per_bar` — **the single owner** (2026-08-19); read by `enforce_midi_section_density`'s rebuild floor AND verse ceiling (both were a hard-coded 8) and by `lock_drums_to_melody_accents`' per-accent adds |
| **clearing hats in melody rests** | `midi_fullkit_director.apply_generated_hat_contract:91` ← the biggest single influence on final hat count |
| swing / lag / humanize | `beat_humanize.apply_hi_hat_swing:901`, `apply_hat_forward_lag:933` |
| open-hat choke | `beat_humanize.apply_open_hat_choke:239`, `add_open_hat_before_snare:2651` |
| panning | `beat_humanize.apply_hat_panning:1021` |
| level vs backbeat | `beat_humanize.apply_six_db_hat_rule:205` |
| densify in melody windows | `melody_beat_symbiosis.densify_hats_in_windows:907` |
| **a hat added on a melody accent** | `melody_beat_symbiosis.lock_drums_to_melody_accents` — the largest single writer of the lane. Occupancy is a TIME comparison at `_ACCENT_HAT_MIN_GAP` (0.08 beats), never an exact key: this pass runs after swing/choke/forward-lag, so every existing hat is already off-grid. Its velocity comes from the lane's own median, not a constant — it appends after `apply_six_db_hat_rule` has already run and nothing levels it afterwards (2026-08-21, the project issue log (not included) §0v.2) |
| **what velocity a hat appended AFTER the leveller may carry** | `drum_density.late_hat_velocity` — **the single owner** (2026-08-25). `apply_six_db_hat_rule` runs at stage 10 and four passes append after it; each used to write its own literal, shipping 33-157 notes per file at 122-127 against lane medians of 37-45. The ceiling is this owner's; the shape inside it stays the writer's. ⚠️ The arrangement transition's `_fill` is deliberately NOT a caller — the project issue log (not included) §0v.8/§0v.9 |
| **the song's FORM** | `arranger.MIDI_SECTIONS` — `intro → hook → verse → drop → outro` since 2026-08-26, issued at a block size `arranger.midi_sections_for_target` picks from the duration target (16-bar blocks at ≥ 56 bars, 8-bar below — the 16-bar form cannot shrink under 40 and a slow tempo wants fewer). ⚠️ The second peak is called `drop` on purpose: seven readers key off the NAME. the project issue log (not included) §0ab.1 |
| **where the song CHANGES** | `arranger._midi_transitions_for` — one transition per section landing plus a midpoint breath in sections ≥ 16 bars. Two kinds only; a third must be taught to `apply_midi_arrangement_transitions` AND `_reapply_midi_kick_808_clears` or its window grows back. Measured into `plg_section_events` by `beat_humanize._measure_section_events` |
| **how much of the MELODY a section plays** | `arranger.section_melody_plan` — **the single owner** (2026-08-26) of the budget; `beat_humanize._apply_section_melody_arrangement` owns which notes go. Two axes: `layer_keep` (register) and `base_cut` (space), drawn `inner_voice` → `offbeat` → `bar_rest` because a single rule is dead on some material (BatonRouge is monophonic, Tragic is entirely on the beat). **Subtraction only** — product decision 2026-07, enforced by object identity in the tests. ⚠️ It runs LAST for a reason: the same lane perturbed before `humanize_pattern` moves kick on three of five files (the project issue log (not included) §0ab.8). Telemetry `plg_melody_arrangement`. the project issue log (not included) §0ac |
| **how loud a SECTION is** | `arranger.section_velocity_scales` — **the single owner** (2026-08-26). The section's `energy` over the LOUDEST section's, clamped to `[0.82, 1.0]`, so it can only ever subtract. Read by `beat_humanize._expected_kick808_velocity` (kick/808, writer AND export gate) and `_apply_section_dynamics` (clap/snare/snare_layer + the melody octave layer). ⚠️ `hi_hats` is deliberately NOT a reader — it has its own levelling chain and a fifth multiplier is the compounding §0v.2/§0v.8 exist to prevent. the project issue log (not included) §0aa |
| the hat audit | `beat_humanize._audit_hat_symbiosis:4742` — **advisory, never blocks export** |

⚠️ When the user's own MIDI already contains hats (`plg_midi_hats`), the pipeline
takes a short branch at `beat_humanize.py:5607` that deliberately **preserves the
user's timing**. Do not add timing edits into that branch.

Open issue: `drop_hats_too_sparse` on calm material — the project issue log (not included) §0.5.

Tests: `tests/test_hat_quality.py`, `tests/test_hat_roll_engine.py`.

### 4.5 Snare and clap

| I want to change… | File · function |
|---|---|
| **what a bar's backbeat plays** | `midi_phrase_drums._backbeat_figure` — **the single owner** (2026-08-21). Four decisions per bar: which lanes stack on the anchor, whether a layer pickup leads in, whether a phrase-turn ghost answers late, whether the bar is deliberately silent. Published as `plg_backbeat_figures`, read ONLY through `backbeat_figure_for_bar` |
| where snare/clap land | `midi_phrase_drums.apply_phrase_drums` — walks the per-bar brief (2026-08-19), not a phrase-relative shape. The instant is always `_BACKBEAT_BAR_OFFSET`; the palette chooses *which lanes*, never *where* |
| hook/drop vs verse on the backbeat | `_backbeat_figure`'s stack decision. Contrast is made by SUBTRACTION in the verse — there is no headroom above clap+snare+layer. ⚠️ `add_pre_clap_roll` is not section-aware and loads the (longer) verse with extra clap onsets, which is what still caps the ratio — the project issue log (not included) §0v.3 |
| a deliberately silent bar before a drop | `_backbeat_figure`'s skip. Keyed on the **section** boundary from the arrangement, not on the phrase plan's `strong_entry` (they do not coincide on long-phrase material) |
| which bars may carry a backbeat / the low end at all | `midi_fullkit_director._bar_brief` → read via `bar_brief`; stamped as `plg_midi_fullkit_plan["bars"]`. **The shared per-bar contract** — section, phrase position (`entry`/`body`/`exit`), role, tension, melody onsets, response anchor, lane permission |
| phrase answers | `midi_fullkit_director.apply_strong_phrase_answers` — its `clap_stops` branch measures 0 and provably cannot fire; the deliberate skip lives in `_backbeat_figure` instead (the project issue log (not included) §0v.7) |
| snare layer (derived from clap) | `beat_humanize.duplicate_snare_layer`, `duplicate_clap_layer` — unchanged generators; **which bars they run on** comes from `_backbeat_palette_hits` reading the palette (2026-08-21). They no longer double `add_pre_clap_roll`'s own micro-notes |
| pre-snare pickup / rolls | `beat_humanize.add_pre_snare_pickup:2233`, `add_pre_clap_roll:2295` |
| delayed clap | `beat_humanize.add_delayed_clap_hit:2389` |
| snare riser | `beat_humanize.build_snare_riser:3307` |
| clap sample choice | `clap_match.py` |
| final placement nudges | `midi_placement_symbiosis.apply_midi_placement_symbiosis:53` |
| kick/backbeat arriving milliseconds apart | `midi_placement_symbiosis`, `_KICK_FLAM_TOLERANCE` (2026-08-19) — a backbeat hit sharing a sixteenth with a kick gives up its Rule 22 push; the kick never moves |

⚠️ Snare/clap are **deliberately not density-scaled** — the backbeat must stay
steady. See the owner map (not included), "Drum density owner".

### 4.6 MIDI analysis and ingest

| I want to change… | File · function |
|---|---|
| reading a `.mid` | `midi_ingest.read_midi_notes:59` |
| ingesting an uploaded file | `midi_ingest.ingest_uploaded_midi:375` |
| picking a MIDI from the internal material library | `midi_ingest.resolve_midi_folder:520`, `_pick_best_midi_in_folder:494` |
| "no MIDI" gate | `midi_ingest.has_available_midi_material:623` |
| BPM from the MIDI/filename, then a deterministic note-grid estimate (key: demoted to last-resort fallback for the progression anchor, 2026-08-15) | `midi_ingest._attach_midi_file_meta:168`, `_estimate_bpm_from_notes` |
| phrase / section structure | `midi_structure_analysis.attach_midi_structure` |
| melody profiling | `midi_profiler.py`, `melody_sample_bank.midi_melody_profile_for_pattern:208` |
| genre detection from MIDI | `midi_genre_detect.py` (⚠️ detection does **not** drive generation — §5a of the project issue log (not included)) |
| the seven-lane contract | `midi_validate.MIDI_ALLOWED_LANES:21`, `MIDI_DISABLED_LANES:30` |
| enforcing it | `midi_validate.enforce_midi_mode:214`, `sanitize_midi_mode:34` |
| stem/MIDI export | `midi_export.export_stem_session` |

Tests: `tests/test_midi_ingest.py`, `tests/test_midi_validate.py`,
`tests/test_midi_structure_analysis.py`.

### 4.7 FLEX (which sound plays the melody)

> **Inert at export since 2026-08-27** (the project issue log (not included) §0ag): the captured
> FLEX blobs were removed from the tree and the bundle, so nothing below ever
> becomes a Native channel in a generated FLP — the melody exports via the
> Sampler path. The machinery still runs and stamps `plg_flex_presets`; the
> table remains the map of that code.

This is the **only** place the LLM makes a musical choice, and it may only pick
from a curated installed shortlist.

| I want to change… | File · function |
|---|---|
| the whole FLEX entry point | `melody_beat_symbiosis.ensure_early_flex_and_energy:158` |
| preset selection | `flex_melody_map.pick_flex_preset:3878` |
| stamping the pick onto the pattern | `flex_melody_map.attach_flex_pick:4305` |
| role resolution (pad / pluck / bell…) | `flex_melody_map.resolve_melody_role:2306`, `resolve_melody_vibe:3001` |
| melody geometry → DNA | `flex_melody_map.analyze_melody_geometry:4328`, `resolve_geometry_dna:1315` |
| scoring a preset against the MIDI | `flex_melody_map.score_preset_for_midi_shape:3097`, `score_preset_for_context:2026` |
| anti-repeat between generations | `flex_melody_map.recency_penalty:3503` inside `_pick_from_bank:3527` ranking; memory owner `generation_variety.recent_flex_picks:86` (the `melody` field of `beat_history.jsonl`, written by `append_beat_history`) |
| **the LLM role call** | `flex_role_brain.maybe_stamp_llm_flex_role:455` (called from `midi_ingest.py:232`) |
| **the LLM preset call** | `flex_role_brain.choose_flex_preset_finalist:113` |
| curated shortlist | `flex_curated_shortlist.py` |
| which packs are allowed | `flex_sound_banks.hiphop_pack_allowed:375`, `is_denied:764` |
| hard compatibility filters | `flex_compat.hard_compat_filter:250`, `seat_sanity_filter:176` |
| character matching | `flex_character.resolve_melody_character:191` |
| the FLP-side re-pick | `flp_writer.py:1520` (fallback when the early pick is missing) |

`flex_melody_map.py` is 154 KB — mostly preset data tables, not logic. Read the
function you need, not the file.

⚠️ 2026-08-13 FLEX accessibility work, verified:
- genre lane names (`genre_profiles.DETAILED_GENRES`) are **never** vibe
  evidence — `flex_character._strip_genre_lane_words` strips them from every
  `source_names` entry (the `style` field re-enters the vibe blob through
  `_midi_source_names`); the free-text prompt is not stripped;
- the curated pool is enforced by BOTH gates in
  `flex_sound_banks.pool_from_library`: the hip-hop pack ban and the user
  deny list (`is_denied`). An all-banned/all-denied slot redirects to a
  sibling vibe of the same role, then falls through to the open scan — it
  never ships a banned or denied preset;
- `_CURATED` in `flex_curated_shortlist.py` was reconciled with the ban:
  every Melodic Techno / Arksun Cityscape title and every `_DENY_FX_ONLY`
  title was replaced with an installed ban-safe preset. When adding a curated
  title, keep it (a) installed, (b) not denied, (c) in a pack that passes
  `hiphop_pack_allowed` for its role. The Melodic Techno ban stands without
  exceptions (confirmed by the user, 2026-08-13).

⚠️ 2026-08-13 FLEX shape-discrimination work (second session), verified:
- the coarse boolean flags in `score_preset_for_midi_shape` used to collapse
  different MIDIs onto byte-identical score vectors (measured 4 corpus files
  on 0.94/-17.50). A bounded **continuous shape layer** now reads the real
  passport values: articulation (`legato_overlap` + new `note_gap_med` field:
  overlap → sustaining seats, end-to-end → keys/guitars, gaps → percussive),
  release-vs-BPM (decay budget = one beat = `60/BPM` s, stretched to the
  material's `median_len`; FLEX has no envelope API — the sanctioned
  `_LONG_TAIL`/`_FAST_ATTACK` name proxy gives the nominal release),
  continuous register vs name-register class, continuous attack fit
  (`onset_rate`/`median_len`), chord density (`poly` × stack/thin). The
  function takes an optional `bpm` keyword; `pick_flex_preset` passes it;
- the perpetual `Supersaw Stack`/`Simple Saws` tie in the synth wall branch
  is broken by continuous median pitch (fat stack seats low/mid walls, the
  lighter saws higher ones); the pluck branch carries a register × flavour
  term (crystal/glass/kalimba sit high; muted/cave/wooden sit low);
- `_keyword_hits_distinct` counts a keyword family once: "synpluck X" is one
  pluck hit, not "pluck" + "synpluck". The substring double count scored
  Synpluck names +13/+11 against +6.5/+5.5 for single-word plucks and locked
  the pluck ranking before any material signal was read;
- sweet_dark pools widened by 12 installed ban-safe not-denied titles
  (pluck +3, synth +2, key +2, piano +1, strings +2, thick_pad +2) — corpus
  reachability went 13/51 → 29/60. Residual same-family convergence (e.g.
  Synpluck Wish on spark pluck material) is the curated seat catalog
  (`flex_preset_seats`) working as owned, not a scoring collapse.

⚠️ 2026-08-14 anti-repeat between generations (variant 5), verified:
- the shipped melody pick (`pack/preset/role/vibe`) is journaled into the
  EXISTING `beat_history.jsonl` owner (`generation_variety.append_beat_history`,
  additive `melody` field — legacy readers all use `.get()` and ignore it);
  `generation_variety.recent_flex_picks` reads the last 3 back;
- `_pick_from_bank` subtracts a BOUNDED penalty from pool scores before
  ranking: exact repeat −6, same pack + name family (first word) −3, max over
  the window, never a sum. Pure subtraction — it never filters the pool, so a
  fully penalized pool still elects its best member and the ban/deny gates
  stay intact. On tiny pools (e.g. pluck/rage = 1 preset) rotation hits the
  ceiling and degrades softly: the penalized only-candidate still wins;
- the recent picks also ride along into `choose_flex_preset_finalist`
  (LLM card + one system-prompt line); offline fallback is unaffected;
- vibe/role/gates are untouched — the penalty only ranks inside the already
  legal pool. `MUTATION_CHAOS` bypasses ranking by design, as before;
- measured: 10 sequential generations of one pluck MIDI went from 1/10
  unique picks (all `Synpluck Wish`) to a 4-way cycle
  Wish → Storm → Classic → Lucky Pluck (4/10 unique, zero adjacent repeats;
  the remaining pool members stay below the −3 family line on this material —
  fit still dominates, the penalty only softens). Artifacts:
  `PLG_Beats_Recency_BEFORE/` / `PLG_Beats_Recency_AFTER/`.

Tests: `tests/test_flex_*.py` (11 files).

### 4.8 Genre profiles

| I want to change… | File · function |
|---|---|
| a genre's musical parameters | `genre_profiles.PROFILES:236` (dataclass `GenreProfile:25`) |
| **tuned overrides that beat the code defaults** | `assets/genre_profiles.json` (Optuna overlay, loaded by `_apply_profile_overrides:697`) — edit **both** or your change won't apply |
| the ten shipping lanes | `genre_profiles.DETAILED_GENRES:658` |
| lane gating | `genre_profiles.HIPHOP_LANES:607`, `clamp_to_focus:668`, `is_genre_detailed:664` |
| untuned-genre redirects | `genre_profiles._FOCUS_FALLBACK:616` — every target must be in `DETAILED_GENRES` |
| genre routing (LLM/keyword) | `genre_brain.resolve_generation_genre:284`, `_LANES:37` |
| per-genre drum cells | `style_corpus.DRUM_CELLS` |
| per-genre mix | `mix_blueprint.py`, `mix_gain_staging.py` |

**Ten lanes ship:** `trap`, `rage`, `opium`, `drill`, `west_coast`, `underground`,
`pluggnb`, `detroit`, `hyperpop`, `phonk`. Do not add an eleventh. The other 29
profiles are inert data behind the lane filter — see the project issue log (not included) §5.

⚠️ **Genre does not currently drive generation.** `backend_core.py:752` sets
`genre_hint = genre_override or "trap"` and `genre_override` is always `None`.
Every beat generates as `trap`. This is a known open decision — the project issue log (not included)
§5a. Don't "fix" it as a side effect of another change.

### 4.9 FLP export

Read [EXPORT_COMPONENT_MAP.md](EXPORT_COMPONENT_MAP.md) for the visual/Playlist
debugging rules, and [FLP_PROVENANCE.md](FLP_PROVENANCE.md) before touching any
byte constant in the writer — it records which structures are ours, which are
public format facts, and which are captured from a real FL install.

| I want to change… | File · function |
|---|---|
| which channels exist in the FLP | `flp_template.active_export_channels:391` |
| Playlist clip list | `flp_template.build_playlist_clips:582` |
| clip start / length / row | `flp_template.PlaylistClip:165`, `playlist_track_map_for_export:412` |
| pattern dedup | `flp_template._apply_pattern_bank:302` |
| hiding a lane | `flp_template.midi_hidden_keys:357`, `_bass808_lane_visible:373` |
| final pre-export rules | `flp_writer.prepare_pattern_for_flp_export:1806` |
| the actual bytes | `flp_writer.build_flp:1831`, `write_flp_session:2014` |
| the sample copies FL loads (FL-safe ASCII + 24-bit PCM) | `flp_writer._ensure_fl_safe_sample:982` → `wav_compat.convert_to_pcm24:180` |
| the integrity tripwire on those copies | `flp_writer._quarantine_if_damaged` + `_verify_published_sample`; detector `wav_compat.describe_wav_damage`. Damaged copies are preserved to `%LOCALAPPDATA%\PLG\damaged_samples\` — **look there first** if an audio artifact is ever reported again (the project issue log (not included) §0j.4) |
| arrangement events / terminal clip trim | `flp_writer._arrangement_events`, `_playlist_pattern_clip` |
| **whether PLG touches FL's mixer at all** | `flp_writer.legacy_mix_enabled` — **the single owner** (2026-10-22). Default: the mixer keeps its structure (inserts named and coloured per lane, every channel routed to its own insert) and nothing else: no plugins or Soft Clipper, no gain-staging block, no sidechain, no channel preamp/clip, notes centred, insert faders and channel volumes at FL's defaults. `PLG_LEGACY_MIX=1` restores the earlier automation below. Gated callers: `backend_core._export_pattern_artifacts`, the sidechain block in `backend_core`, `fl_launch.open_beat_in_fl`, `flp_writer._mixer_events*` / `build_flp` / `_note_struct`. Tests: `tests/test_default_mixer.py`; tests of the kept legacy code carry `@pytest.mark.legacy_mix` |
| mixer gain staging (legacy, `PLG_LEGACY_MIX=1` only) | `mix_gain_staging.attach_vocal_ready_mix` |
| template FLP | `assets/plg_template.flp`, `flp_template.ensure_template_file:684` |

⚠️ **Two different PPQ scales.** Notes/patterns use `flp_writer.PPQ = 960`
(`:62`); Playlist *clip lengths* use `_PLAYLIST_LENGTH_PPQ = 96` (`:149`). Do not
unify them — that made every Playlist cube 10× too wide.

⚠️ `pattern_iid` and display-name suffixes (`PLG Snare Layer Main 5`) are
**export-local**, not global identifiers. Never hard-code them.

Tests: `tests/test_flp_writer.py` (2 known pre-existing mixer failures).

### 4.10 UI / frontend

| I want to change… | File |
|---|---|
| the JS↔Python bridge | `plg_webview.py` `class Api` (~line 62 onward) |
| the API surface it exposes | `plg_api.py` public functions |
| TS side of the bridge | `web/src/api.ts`, `web/src/devBridge.ts` |
| app shell / routing | `web/src/App.tsx` |
| the generate screen | `web/src/components/HomeHero.tsx`, `HomeView.tsx` |
| **tester build (local only, expires, badge)** | `app_config.build_flavor / tester_build / tester_days_left` (flag = `build_flavor.json` stamped into that exe by `build_plg.bat tester`), `local_only` (network guards), `plg_api` (`tester_*` status, expiry refusal), `PLG.spec` (`PLG_BUILD_FLAVOR_FILE`), `tools/make_build_flavor.py`, `tools/verify_build_flavor.py`; UI badge in `web/src/App.tsx`. the project issue log (not included) §0be. Tests: `tests/test_tester_build.py`, `test_tester_api.py`, `test_local_only.py`, `test_build_flavor_tools.py` |
| **which source files a beat was really built from** (dev tool) | `python tools/show_flp_sounds.py [beat.flp]` - per channel the baked file the .flp loads and the source file in the user's library (`plg_sample_origins` `path`, `plg_explicit_lanes`); tests `tests/test_show_flp_sounds.py` |
| **the user picks sounds by hand (sound browser, cells, preview)** | `pinned_kit` (`pin_by_hand`, `explicit` marks, `PIN_LANES`), `plg_api.list_sounds / pin_sound / unpin_sound`, `sound_preview` (localhost audio server), gates yield in `starter_kit` (808 block), `lane_fallback._keep_explicit`, `flp_writer._assert_exportable_808`; UI `web/src/components/SoundBrowser.tsx`. the project issue log (not included) §0bc. Tests: `tests/test_explicit_pins.py`, `tests/test_sound_preview.py`, `tests/test_pinned_kit.py` |
| **which sounds PLG may put in a project** (melody sample, substitute sounds) | `sound_policy` (`PLG_MELODY_SAMPLE` default off, `PLG_SUBSTITUTE_SOUNDS` default off) — the single owner; read by `starter_kit.attach_sounds_to_pattern`. the project issue log (not included) §0ba, §0bb. Tests: `tests/test_melody_sample_flag.py`, `tests/test_empty_lanes.py` |
| **PLG's own melody one-shots (which one a MIDI melody gets, how it is prepared)** | `melody_oneshots` — the single owner (2026-11-04; flag `sound_policy.melody_oneshots_enabled` = `PLG_MELODY_ONESHOTS`, default ON, `off` switches it off), called from `starter_kit.attach_sounds_to_pattern` after the `PLG_MELODY_SAMPLE` pop: `melody_features` → `choose_group` (thresholds are ESTIMATES, module constants) → `choose` (seed = `plg_variety.seed`) → `prepared_copy` (DC out, 20 Hz high-pass, fades, -3 dB). No automatic pick from the user's folder sits on the melody; only a hand pin (`pinned_kit.EXPLICIT_ONLY_LANES`) does and it wins. Files in `assets/melody_oneshots/<type>/` (`_rejected/` and `sources.txt` never chosen, never in the exe: `tools/melody_oneshots_bundle.py` ← `PLG.spec`). Channel root: `assets/melody_oneshots/root_offsets.json` → `melody_oneshots.root_offset` → `flp_writer._melody_oneshot_root_note` (written beside the 808's root, same `_CH_ROOT_NOTE` event; default 60 + offset). Dev tools: `tools/check_oneshots.py` (pitch / length / peak / DC vs MIDI 60; `--write-offsets` makes the json), `tools/show_flp_sounds.py` (features, group, seed / type / file). the project issue log (not included) §0bo. Tests: `tests/test_melody_oneshots.py` |
| **the status must not wait on the server** | `plg_api._network_probe` / `_network_status` (one background thread, `_NET_BUDGET_S`), `get_status` fields `network_checking` / `network_online: null`; UI re-poll in `web/src/App.tsx`; banner `components/OfflineBanner.tsx`. the project issue log (not included) §0az. Tests: `tests/test_status_budget.py` |
| **running from source with no server** | `app_config.offline_dev` (`PLG_OFFLINE_DEV=1`) — the single owner; ignored in a frozen exe and in a release build. Read by `app_config.get_cloud_mode`, `plg_cloud.is_cloud_mode`, `beat_quota._skip_limit`, `plg_data_sync._cloud_ready`, `plg_api.create_beat` / `get_status`; UI badge in `App.tsx`. the project issue log (not included) §0ax. Tests: `tests/test_offline_dev.py` |
| progress UI | `web/src/components/GeneratingState.tsx` |
| left nav | `web/src/components/Sidebar.tsx` |
| Library / Tools / Settings / Help pages | `LibraryView.tsx`, `ToolsView.tsx`, `SettingsView.tsx`, `HelpView.tsx` |
| account / billing (cloud mode) | `AccountView.tsx`, `AuthView.tsx`, `web/src/billing.ts` |
| error text mapping | `web/src/errors.ts` (unknown `error_type` falls through to the server message) |
| translations (9 locales) | `web/src/i18n/locales/*.ts` |

**Never edit `web/dist/`** — it is build output. Run `npm run build` in `web/`.

Adding a Python function to `plg_api.py` does *not* expose it to the UI. It must
also be added to the `Api` class in `plg_webview.py`.

### 4.11 Style Card (write-only, added 2026-10-04)

| I want to change… | File · function |
|---|---|
| what the card contains / how `mood` and `kit_target` are derived | `style_card.build_style_card` (thresholds are module constants, uncalibrated) |
| where it is stamped | `backend_core._stamp_style_card`, called right after each `humanize_pattern` in `_humanize_and_ingest` |
| reading it | `style_card.style_card(pattern)` — **no production code does yet** |

`plg_style_card` collects tempo, key, melody energy, drum density, role,
sections, plus coarse `mood` / `kit_target` / `confidence`. It is Phase 1 of the
engine-consistency plan and is deliberately write-only: byte-identical FLP with
and without it. It must stay free of RNG and of genre lanes. Next: Phase 2 —
sample passports in `library_catalog`; Phase 3 — the first reader (kit
coherence). See the owner map (not included) → "Style Card". Tests:
`tests/test_style_card.py`.

#### Kit coherence (Phase 3, ON by default since 2026-10-04)

| I want to change… | File · function |
|---|---|
| the on/off/shadow switch | env `PLG_KIT_COHERENCE` = `on` (**default since 2026-10-04**) / `shadow` / `off` — `kit_coherence.coherence_mode`, `DEFAULT_MODE`. A value it does not understand means `off` |
| how a kit is judged (terms, weights) | `kit_coherence.Context.score`, `WEIGHTS` |
| how whole kits are searched | `kit_coherence.plan_kit` (shortlist per lane → beam → seeded draw among near-best) |
| the full ranked pool of one lane | `sample_match.ranked_pool_for_track` (same scoring as `pick_best_for_track`, no RNG) |
| the per-candidate scoring loop | `sample_match._score_track_pool` — moved out of `pick_best_for_track`; **its random-draw order is a contract**, locked by `tests/test_pick_best_for_track_golden.py` |
| where it plugs in | `starter_kit._kit_coherence_stage` (after `merge_llm_sample_picks`), `_stamp_kit_coherence` (before the bakes); the Perfect Pair kick re-pick is skipped when a coherent kit was applied |
| what happened on a run | `pattern["plg_kit_coherence"]` — both kits, per-term points, alternatives |
| **the kit the user pinned with "Random from library"** | `pinned_kit` — pins in `%LOCALAPPDATA%\PLG\pinned_kit.json`, drawn from the user's catalog only (`pick_random_kit`); applied in `starter_kit.attach_sounds_to_pattern` BEFORE kit coherence, which skips the pinned lanes (`_kit_coherence_stage(..., pinned)`) and the paired-kick re-pick skips a pinned kick. A pin is a preference: the 808 gates and `lane_fallback` may still swap it; the swap is stamped in `plg_pinned_kit["replaced"]`. API: `plg_api.random_kit` / `clear_pinned_kit`. Tests: `tests/test_pinned_kit.py` |
| blind A/B pairs for listening | `python tools/make_kit_pairs.py --stage 0\|1` |

Lanes: 808_bass, kick, snare, clap, snare_layer, hi_hats. **`melody_lead` is out of scope.**
`off` imports nothing and is byte-identical to before; `shadow` only records; `on`
replaces the six lanes. Terms: pack/set cohesion, brightness (percentile inside the
lane's own pool, against the Style Card `kit_target`), tails against the tempo,
levels (only the five lanes whose fader is fixed — `flp_writer` already lifts the
808 and the melody per file), snare/clap role clash, two-subs, clean-vs-dirty, and a
small 808-root-vs-key term. Compatibility, not sameness. Tests:
`tests/test_kit_coherence.py`, `tests/test_kit_coherence_modes.py`.

#### Sample passports (write-only, added 2026-10-04)

| I want to change… | File · function |
|---|---|
| what is measured per sample | `audio_features._character_from_array` (new numbers) — `analyze()` is untouched, its thresholds feed `sample_match` / `library_quality` |
| the per-sample store, hashing, warm-up | `sample_passport.passport_for`, `warm_passports`, `warm_for_scan` |
| when passports are computed | `library_catalog.scan_library` → `_warm_sample_passports` (scan root + starter kit, 15 s budget, 30 s re-scan throttle; off with `PLG_PASSPORT_WARMUP=0`, which `tests/conftest.py` sets) |
| the 808 root of a sample | `sample_passport.fill_root` → `sample_pitch_passport` (unchanged, ~0.3 s/file, so NOT done in warm-up) |
| the audio-feature cache file | `audio_features._save_cache` (atomic, locked), `cache_batch()` to coalesce bulk writes |

Passports live in `%LOCALAPPDATA%\PLG\sample_passports.json`, keyed by a hash of
the file CONTENT (a renamed/moved/copied sample keeps its passport; 845 library
files = 685 distinct contents on the dev machine). Numbers only — "dark /
bright / hard" labels are lane-relative and belong to the reader (Phase 3).
**No production code reads them yet**, and `scan_library`'s return value is
unchanged. Tests: `tests/test_sample_passport.py`,
`tests/test_audio_cache_and_character.py`.

---

## 5. Supporting systems (not on the musical spine)

| Area | Files |
|---|---|
| sample matching / kit selection | `sample_match.py`, `starter_kit.py`, `library_catalog.py`, `library_quality.py`, `kit_variety.py`, `lane_fallback.py`, `fallback_samples.py` |
| sample processing | `sample_fx.py`, `sample_chop_engine.py`, `sample_saturate.py`, `sample_stereo.py`, `sample_pitch_passport.py` |
| arrangement / song form | `arranger.py` (`arrange_song:771`, `_target_song_bars:37`, `_shrink_sections_to_duration:118`) |
| quality gate | `beat_quality.py`, `pattern_score.py`, `beat_reward.py` (inert without scikit-learn) |
| variety / anti-clone | `generation_variety.py` |
| mixing | `mix_blueprint.py`, `mix_gain_staging.py`, `mix_insert_fx.py`, `sidechain_curve.py` |
| FL discovery & launch | `fl_detect.py`, `fl_launch.py`, `fl_setup.py` |
| FL piano-roll scripts | `fl_scripts/*.pyscript`, `plugin_script.py` (data, never imported) |
| LLM transport | `llm_client.py` (`format_llm_error` — see the project issue log (not included) §1.2) |
| cloud service | `cloud/app/` (separate FastAPI service, `cloud/README.md`) |
| desktop plumbing | `app_config.py`, `plg_paths.py`, `plg_log.py`, `plg_sentry.py`, `beat_quota.py` |
| tkinter fallback UI | `plg_app.py`, `plg_theme.py` — only when pywebview fails to start |

---

## 6. Traps — modules that look active but are not

**Check this list before editing a file whose name matches your task.** Every one
of these has burned an agent before.

| File / function | Looks like | Actually |
|---|---|---|
| `sub_808_gen.py` | the 808 generator | **Retired lane.** Only imported by `midi_validate` to *reject* sub_808 material. `808_bass` is the one low-end lane, permanently |
| `beat_humanize._apply_sub_808_lane:3335` | the 808 writer | Runs only for non-MIDI or non-`bass808_enabled` profiles; publishes to `808_bass` via `_publish_low_end:3380` |
| `drum_blueprint.apply_drum_blueprint` | the drum generator | Fallback only. On the MIDI route `apply_phrase_drums` runs instead, and `_apply_808_bass_lane` overwrites its kick anyway |
| `backend_core.generate_pattern:384` | the LLM composer | **Provably dead.** `run_pipeline` raises before reaching it; `system_instruction` is never assigned — it would `NameError` |
| `backend_core.build_system_instruction:342` | prompt building | Only reachable from that dead branch. Same for `fl_knowledge.py`, `producer_brain.py`, `sample_catalog.format_catalog_for_prompt` |
| `run_pipeline_from_pattern:996` | a pipeline | Cloud-only; `create_beat` returns `not_supported` in cloud mode first |
| `artist_styles.py` | style selection | Reachable only from the retired prompt route |
| `llm_pipeline.py`, `LLM_PIPELINE.md` | the generation pipeline | Historical Titans route. Not authority for the desktop MIDI route |
| ~~`groovae_humanize.py`~~, `validator.py` | humanization | `groovae_humanize.py` was **retired 2026-08-27** (the project issue log (not included)): `use_groovae()` returned `False` even in `auto`, its Magenta/TensorFlow backend was never packaged, and the checkpoint it fetched had no version pin, no checksum and no recorded licence. `validator.py` is unchanged and still runs. |
| `midi_ingest.ingest_library_midi:337`, `midi_corpus.py` | melody fallback | Unreachable on the live route; the `plg_midi_first` guard raises first |
| `prompt` / `midi_path` / `genre_override` / `reference_audio_path` params | user input | Inert compatibility plumbing. The shipped UI passes none of them |
| `texture`, `fx`, `cowbell` lanes; GENERATED `counter_melody` / `chords` | active lanes | Stripped by `midi_validate.MIDI_DISABLED_LANES`. The two melodic ones are open only as ROLE lanes (the user's own notes, `midi_roles.split_into_lanes`) |
| `archive/**` | old but usable code | Retired by product decision. Restoring any of it is a regression |

**Alive but not exercised by a normal trap run — do not archive:** `bass808_llm`,
`chord_bed`, `melody_transform`, `fx_layer`, `symbolic_composer`, `fl_launch`,
`fl_setup`, `plg_app`, `plg_cloud`, `organize_kit`, `beat_planner`. Full list with
trigger conditions: the project issue log (not included) §4.

---

## 7. Document index

| Document | Answers |
|---|---|
| **PROJECT_MAP.md** (this file) | where do I change X? |
| [internal notes (not included)](internal notes (not included)) | **mandatory** — how to check what exists before writing code |
| [internal notes (not included)](internal notes (not included)) | the hard rules and the working boundary |
| [ACTIVE_ROUTE.md](ACTIVE_ROUTE.md) | the generation contract; what the LLM may and may not do |
| [the owner map (not included)](the owner map (not included)) | who is the *final* owner of each lane |
| [the project issue log (not included)](the project issue log (not included)) | verified defects, closed decisions, unwired features, test status |
| [EXPORT_COMPONENT_MAP.md](EXPORT_COMPONENT_MAP.md) | Playlist/FLP visual bugs |
| [ENGINE_TRACE.md](ENGINE_TRACE.md) | verified execution trace and export ownership |
| [internal notes (not included)](internal notes (not included)) | what AI is allowed to author |
| [FL_BRIDGE.md](FL_BRIDGE.md) | how Open-in-FL works; the FLP byte format |
| [FL_SCRIPTS.md](FL_SCRIPTS.md) · [FL_VERSIONS.md](FL_VERSIONS.md) · [FL_WORKFLOWS.md](FL_WORKFLOWS.md) | piano-roll scripts, FL version support, manual mixing guide |
| [internal notes (not included)](internal notes (not included)) | end-user / release operator guide (RU) |
| [internal notes (not included)](internal notes (not included)) | why is X missing / where did it go? |
| [internal notes (not included)](internal notes (not included)) | historical single-session evidence (2026-08-09) |
| [LLM_PIPELINE.md](LLM_PIPELINE.md) | ⚠️ historical Titans route — **not** the active path |
| [SECURITY.md](SECURITY.md) · [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) | release and security posture |
| [docs/DEPENDENCY_LICENSE_INVENTORY.md](docs/DEPENDENCY_LICENSE_INVENTORY.md) · [the asset inventory (not included)](the asset inventory (not included)) | DD: every dependency and bundled asset, with its license/provenance class |
| [THEMES.md](THEMES.md) | FL Studio colour themes |

---

## 8. Running things

```bash
python -m pytest tests -q
```

⚠️ The suite is **not green** — 49 known failures as of 2026-08-11
(the project issue log (not included) §6). Compare FAILED *lists* before and after your change, never
totals.

```bash
python -m pytest tests/test_midi_phrase_drums.py tests/test_drum_density.py -q
```

Focused runs are the normal workflow. Pick the test file matching your topic from
§4.

```bash
python midi_validate.py
```

Validates the seven-lane contract against `output_pattern.json`.

```bash
python tools/refund_lookup.py customer@example.com
```

Read-only support lookup for voluntary refund eligibility. It uses the
existing `profiles.current_payment_event_id`, `generation_logs.payment_event_id`
and `payment_events` linkage; it never performs a refund or writes database
rows.

```bash
cd web && npm run build
```

Rebuilds the UI bundle. Required after any `web/src/` change.

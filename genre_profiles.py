"""Genre router — per-genre humanize profiles.

One deterministic profile per genre drives the producer brain so the same
engine does justice to country, rage, phonk, drill, hyperpop, jersey,
k-pop, R&B, pop and (only on request) grind / anti-music — instead of forcing
trap micro-timing onto everything. Each profile only flips knobs the humanize
layer actually controls; the LLM still writes the notes.

The "trap" default reproduces the engine's original behaviour exactly, so the
rage path and its tests are unchanged.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, fields, replace
from pathlib import Path

logger = logging.getLogger("plg.genre")


@dataclass(frozen=True)
class GenreProfile:
    name: str
    melody_scale: str | None  # snap target; None = leave melody pitches alone
    eight08: bool = True  # process the 808 line at all
    eight08_slides: bool = True  # high-register slide stabs
    hat_rolls: bool = True  # trap 1/32–1/64 rolls + pitch creep
    hat_swing: float = 1.0  # multiplier on the swing shift (0 = grid-locked)
    drop_tension: bool = False  # wipe kick+808 before phrase drops
    humanize_drum_velocity: bool = True  # randomize snare/clap velocity
    counter_melody: bool = True  # off-beat answer line
    stereo_drop: bool = True  # narrow verse → wide drop
    # Master soft-clip hint. NOTE (2026-08-12): on the shipping route this is
    # metadata only — flp_writer._master_soft_clip_enabled returns True for any
    # pattern whose plg_use_template is not False, and nothing on the active
    # route ever sets it False, so the template's Master Soft Clipper ships
    # regardless. Verified in the exported bytes: drill/west_coast/detroit FLPs
    # (all soft_clip=False at the time) each contain exactly one "Fruity Soft
    # Clipper". It is still kept accurate per genre so the metadata does not
    # lie to the template-less export path or to mix_blueprint's guide text.
    soft_clip: bool = False
    drive: float = 0.5  # 0..1 distortion / aggression hint
    melody_lag_ms: float = 0.0  # lazy groove-lag: drag melody notes late off the grid
    voicing_spread: float = 0.0  # 0..1 chance to invert a chord tone an octave for width
    kick_syncopation: float = 0.0  # 0..1 probability of extra off-grid kick hits (never on clap)
    markov_hats: bool = False  # Markov velocity state-machine for non-monotonous hats
    snare_riser: bool = False  # accelerating 1/4→1/32 snare roll + pitch-up before each drop
    # drum_blueprint.py's transition fill — 4 consecutive 16th-note snare hits
    # crescendoing into every chorus/hook/drop landing. Used to fire for every
    # genre except a small exclusion list (2026-07 audit: it felt "always on"
    # because it effectively was). Now genre-gated like everything else here.
    # Never combine with snare_riser=True on the same genre — both build into
    # the same landing and would stack two different roll techniques.
    transition_fill: bool = False
    kick_volume: int = 12800  # channel-rack level (FL 0..12800, 10000 = 0 dB)
    sub_808_volume: int = 12800
    melody_volume_db: float = -6.0  # melody lead / counter melody vs 0 dB reference
    melody_insert_db: float = -6.0  # PLG MIDI mixer insert fader
    master_fader_db: float = -3.5  # master insert headroom before soft clipper
    melody_low_cut_hz: float = 165.0  # built-in insert EQ low cut (150–180 Hz)
    max_active_sounds: int = 10  # commercial-simplicity cap: total non-empty lanes (drums + melody + ear-candy)
    # Chord-bed voicing options (Dirkey "How To Make UNLIMITED Chords") — all
    # default to a no-op so no existing genre's sound changes until opted in.
    chord_sus_mode: str | None = None  # "sus2" | "sus4" — replace the chord's 3rd with this scale degree
    chord_power_chord: bool = False  # root+5 only, no 3rd — the simplest, always-safe voicing
    chord_root_octave_down: bool = False  # bass-double the chord bed's root an octave down for weight
    chord_voicing_spread: float = 0.0  # 0..1 chance to octave-invert a chord-bed tone (openness)
    chord_compact_wide_voicings: bool = False  # pull the outlier note an octave closer if the voicing spans too wide
    chord_section_variation: bool = False  # thin the color tones (7th/9th/cluster) on repeat section occurrences
    chorus_density_ramp: bool = False  # thin ear-candy lanes for the first bar of chorus/drop so it isn't instant-full
    # Per-generation variability, NOT a fixed switch: probability the hook
    # arrives via a real rhythm-section buildup (2-bar melody-only intro, then
    # 2 bars of kick+808 only, THEN the full band lands) instead of the
    # "cold open" (full band from bar 0 of the hook — the engine's original
    # instant-impact default). Rolled once per generation in
    # generation_variety.py::build_context() off the same seeded RNG as every
    # other per-generation choice. Default 0.4 = real variety for most genres;
    # override down for genres whose identity IS the instant full-band hit
    # (rage/opium/phonk/underground — all drive>=0.9 "heavy hitting" already),
    # up for the floating/atmospheric worlds (toliver/kanye/minimal_trap).
    hook_buildup_chance: float = 0.4
    # "Fix Your Sound Selection" (Dirkey) — all default to a no-op.
    sound_gap_fill: bool = False  # bias counter_melody/chords pick away from melody_lead's occupied spectral band
    sound_family_pairing: bool = False  # bias counter_melody/chords pick toward a paired instrument family (piano/EP -> orchestral)
    background_bus_eq: bool = False  # push-to-back EQ on texture/cowbell (background ear-candy), front lanes untouched
    # Gross Beat "half-speed + octave-down layer" (trap-specific move) — off by default.
    melody_octave_down_layer: bool = False  # full melody_lead copy, -12 semitones, on PLG Texture lane
    # Broadband kick->808 duck, baked as real note splits (no automation clip needed) — off by default.
    kick_808_duck: bool = False  # sub_808 sustains cut+retrigger under a mid-sustain kick hit
    # "Trust the Process" — drums answer melody density per section (postprocess, no LLM reorder) — off by default.
    melody_drum_density_inverse: bool = False  # thin kick_sync/hat_roll extras under busier-than-average melody sections
    # Deon/Infinite — don't open the hook on the tonic — off by default.
    melody_avoid_root_start: bool = False  # nudge the melody's first note off the root (5th/4th degree)
    # Jay Cactus — pad sits out the first chorus/hook pass, enters on the repeat — off by default.
    chorus_pad_holdback: bool = False  # removes chords-lane notes from the first chorus/hook occurrence
    # Dirkey Destroy Lonely — root opens late and off the scale — off by default.
    melody_root_delay_tension: bool = False  # delays + chromatically detunes the melody's opening root note
    # Dirkey Destroy Lonely — chords lane reversed + re-chopped with melody mixed in — off by default.
    chords_reverse_chop: bool = False  # bakes reverse_chop into the chords one-shot (opt-in FX layer)
    # Mike Dean (Travis Scott) — the arp/answer line uses the SAME preset as the chord bed — off by default.
    counter_melody_mirrors_chords: bool = False  # counter_melody's sample is overridden to match chords
    # Sam Tsui (Post Malone-style pop) — a quiet relative-major duplicate of the
    # chord bed rides counter_melody instead of its usual off-beat answer line.
    chords_major_duplicate_layer: bool = False  # counter_melody = chords +3 semitones, quiet
    # Mike Dean: counter_melody = diatonic third above melody_lead, in-scale
    # (not a fixed chromatic interval). See materialize_melody_harmonizer.
    melody_harmonizer_layer: bool = False
    # Mike Dean: an occasional baked delay-tail accent into SOME chorus/hook/
    # drop landings (PLG FX lane) — never a constant effect. See
    # materialize_delay_accent_notes.
    delay_accent_lane: bool = False
    # prod gas ("How To Make Pluggnb") — one fixed clap hit per 2-bar cell lands
    # a quarter-beat late instead of on its usual grid slot — off by default.
    delayed_clap_hit: bool = False
    # prod gas — "no two 8-bars are identical": the melody gets a note added or
    # dropped at every 8-bar boundary so repeats always develop — off by default.
    melody_8bar_variation: bool = False
    # GOYXRD ("Pluggnb" build) — the "bell cheat code": counter_melody becomes a
    # full octave-up duplicate of melody_lead (own independently-picked preset,
    # not melody_lead's own sample) instead of the usual off-beat answer line.
    bell_octave_counter_melody: bool = False
    # Jay Cactus (Detroit) — not yet enabled on any profile, waiting on a second
    # source before a real "detroit" GenreProfile exists (see minimal_trap/
    # west_coast for the two-source pattern). Ready to flip on when it does.
    # Piano top-line: only the melody's upper half survives (lower notes muted
    # before render), pitched up an octave, on counter_melody's own preset.
    melody_top_layer_muted_lows: bool = False
    # The sub bass alternates: chorus/hook/drop move sub_808's notes to the fx
    # lane instead (a genuinely different "spins" bass sample), never both at once.
    alternating_bass_sections: bool = False
    # Mike Dean/Travis: live-sounding bass on chorus/hook/drop (PLG Texture
    # lane), synth 808 elsewhere — never both at once. See
    # apply_live_bass_texture_sections.
    live_bass_texture_sections: bool = False
    # An open hat lands just before each snare hit, velocity pulled back — the
    # existing open-hat choke naturally clips its tail into a rhythmic "cut".
    open_hat_before_snare_cut: bool = False
    # The whole melody_lead layer transposes up a full octave for the intro
    # section only, for variety before the beat locks in at full pitch.
    intro_melody_octave_up: bool = False
    # Lollipopbeatz (Detroit, 2nd source) — "808 hits harder tuned higher":
    # shifts the whole one-octave sub_808 register band up (F2-F3 instead of
    # C2-C3), not a fixed pitch class — stays correct in whatever key.
    sub_808_register_high: bool = False
    # Felix (hyperpop/100 gecs-style tutorial) — intentionally chaotic, wide-
    # interval melody contour (the opposite of smooth voice-leading).
    melody_chaotic_wide_jumps: bool = False
    # Felix — the 808 cuts off exactly on the next snare hit (an ADSR envelope
    # gate, not a duck), leaving a hard silence before the next 808 note.
    sub_808_snare_gate_stutter: bool = False
    # nightiger (digicore, hyperpop 2nd source) — "the most common progression
    # in digicore": melody drawn predominantly from root/5th/7th scale degrees.
    melody_root_fifth_seventh_bias: bool = False
    # nightiger — sidechain depth depends on the bass TYPE: a long/sustained
    # 808 wants a deeper pump, a short bass-pluck wants shallow/none.
    sidechain_depth_bass_type_aware: bool = False
    # Drift Phonk tutorial — the melody moves chromatically note-to-note
    # (deliberate passing tones OUTSIDE the scale). Every other genre in this
    # system stays strictly in-scale; here, moving off-scale is the point.
    melody_chromatic_walk: bool = False
    # Drift Phonk tutorial — the kick is placed WHERE the 808 hits (reinforcing
    # it), not generated independently of the bass line.
    kick_synced_to_808: bool = False
    # Mike Dean (Travis Scott) — a reversed-chords swell leads into chorus/hook/drop — off by default.
    fx_riser_from_chords: bool = False  # fx-lane note is a reversed chords one-shot, placed the bar before
    # Dirkey "How To Make Underground Beats" — the 808's opening note, same move as
    # melody_root_delay_tension but on the bass — off by default.
    sub_808_root_delay_tension: bool = False  # delays + chromatically detunes the 808's opening root note
    # Dirkey "How To Make Underground Beats" — reversed open-hat swells as an accent — off by default.
    hi_hats_reverse_open: bool = False  # bakes a pure reverse into the hi-hat one-shot
    # Blood-style rage — controlled, "still clean" distortion on hats, not the default zero-processing — off by default.
    hi_hats_light_saturation: bool = False  # small tanh warmth on the hi-hat one-shot (SATURATION default is (0,0,0))
    # Richie Souf ("New Atlanta Sound") — counter_melody follows melody_lead's direction, not against it — off by default.
    counter_melody_matches_direction: bool = False  # mirrors counter_melody's phrase contour on direction conflicts
    # Nettspend ("Early Life Crisis") — melodies trend up, not neutral/random — off by default.
    melody_upward_bias: bool = False  # mirrors flat/descending melody phrases so they rise
    # Nettspend — melody_lead and counter_melody share one register band
    # (normalize_registers); off by default. See apply_register_collision_avoidance.
    register_collision_avoidance: bool = False
    # Minimal dark trap — quiet octave-UP shimmer (opposite direction from melody_octave_down_layer) — off by default.
    melody_octave_up_layer_quiet: bool = False  # full melody_lead copy, +12 semitones, quiet, on PLG Texture lane
    # Minimal dark trap — a quiet "snap" pickup accent right before each snare hit — off by default.
    pre_snare_pickup: bool = False  # adds a note to snare_layer shortly before each snare, not simultaneous
    # 2026-07 "sub 808 as its own instrument" audit — real FL portamento
    # between every root change on the sub line (see beat_humanize.
    # apply_sub_808_glide / flp_writer's flags=8 wiring) instead of
    # retriggering the sample at each kick. Off by default — a floaty/
    # sustained identity, not every genre's 808 character.
    sub_808_glide: bool = False
    # 2026-07 — the sub jumps up an octave for one note at hook/chorus/drop's
    # landing bar (see beat_humanize.apply_808_register_lift), the classic
    # "screaming" 808 rise into a hook. Off by default.
    sub_808_register_lift: bool = False
    # 2026-07 Part B — independent "808 Bass" lane (bass808_gen.py): short,
    # punchy, retriggered-every-hit notes locked to the kick pattern, a second
    # instrument sitting alongside sub_808 (which stays long/glide/harmony-
    # following). Off for every genre until a dedicated listening/tuning pass
    # picks which genres actually want the layered sub+punch 808 sound.
    bass808_enabled: bool = False
    # 2026-07 — after 3+ rounds of purely deterministic kick/808_bass rules
    # (density laws, call-and-response weighting, 8-bar structural repeat)
    # kept passing every formal invariant test but still not landing on real
    # listens, kick/808_bass placement can be routed through an LLM composer
    # call instead (bass808_llm.py) — real musical judgement instead of
    # hardcoded probabilities, with the deterministic bass808_gen engine kept
    # as an automatic, instant fallback on ANY failure (timeout, invalid
    # JSON, degenerate output, missing API key). Requires bass808_enabled.
    # Was on for the whole bass808_enabled family from 2026-07; switched OFF for
    # every lane on 2026-10-06 (the project issue log (not included) 0au): an LLM must not place notes,
    # and an API key being present was enough to make this branch live.
    llm_kick808_enabled: bool = False  # OFF for every lane since 2026-10-06 (the project issue log (not included) 0au); beat_humanize.KICK808_LLM_ALLOWED is the hard stop
    # 2026-07 — kick/808_bass placement (density, pairing, root pitch) and
    # dynamics were architecturally locked rigid by design: onsets come from
    # a small fixed offset table and are never perturbed afterward, velocity
    # is force-reasserted to one exact constant per lane as the literal last
    # step of the pipeline. That rigidity read as "mechanical"/lifeless next
    # to hi_hats/snare/clap, which already stack several independent micro-
    # timing (apply_hi_hat_swing, apply_hat_forward_lag, apply_pre_snare_shift)
    # and velocity-randomization (Markov hat dynamics, humanize_velocities)
    # passes. This flag adds the same CLASS of subtle jitter to kick/808_bass
    # — a few ms of timing dust and a small +/- velocity range — without
    # touching placement/pairing/density/root-pitch. Off by default and
    # opted into per genre, same rollout pattern as bass808_enabled itself.
    kick808_micro_humanize: bool = False


DEFAULT = GenreProfile(
    "trap", None, soft_clip=True, melody_octave_down_layer=True, kick_808_duck=True,
    melody_drum_density_inverse=True, counter_melody_mirrors_chords=True, fx_riser_from_chords=True,
    counter_melody_matches_direction=True,
)

PROFILES: dict[str, GenreProfile] = {
    # Optuna-tuned (2026-07 audit decision: JSON values are the truth for trap) —
    # kept in sync with assets/genre_profiles.json; DEFAULT stays the untuned
    # fallback for unknown genres.
    "trap": GenreProfile(
        "trap", None, hat_swing=1.28, drop_tension=True, stereo_drop=False,
        soft_clip=True, melody_lag_ms=13.82, voicing_spread=0.02, kick_syncopation=0.24,
        melody_octave_down_layer=True, kick_808_duck=True,
        melody_drum_density_inverse=True, counter_melody_mirrors_chords=True,
        fx_riser_from_chords=True, counter_melody_matches_direction=True,
        transition_fill=True, sub_808_register_lift=True,
        bass808_enabled=True, llm_kick808_enabled=False, kick808_micro_humanize=True, max_active_sounds=11,
        # live_bass_texture_sections dropped (2026-07 audit): it and
        # melody_octave_down_layer both claim the Texture lane, and
        # materialize_melody_octave_down_layer runs first in beat_humanize.py
        # — live_bass_texture_sections silently never fired for trap. Kept
        # octave-down-layer (a generic "fatten the hook" trick) over the
        # live-bass double, which is a Mike Dean/Travis-specific flourish
        # that already has a home in toliver/opium's space-mix identity.
    ),
    # Carti/Yeat hats are MECHANICALLY dense, not swung — low swing on purpose.
    # chord_voicing_spread/chord_section_variation: Dirkey "Eternal Atake 2" —
    # "no right or wrong, every beat was very experimental" — wider chord-bed
    # randomization than the other trap-family genres, none of which opt into
    # these two flags (first genre to use them).
    "rage": GenreProfile(
        "rage", "phrygian", hat_swing=0.4, drop_tension=True, humanize_drum_velocity=False,
        soft_clip=True, drive=0.9, melody_lag_ms=12.0, voicing_spread=0.15,
        kick_syncopation=0.35, markov_hats=True, melody_octave_down_layer=True, kick_808_duck=True,
        melody_drum_density_inverse=True, chord_voicing_spread=0.2, chord_section_variation=True,
        hi_hats_light_saturation=True, counter_melody_matches_direction=True,
        hook_buildup_chance=0.2, bass808_enabled=True, llm_kick808_enabled=False, kick808_micro_humanize=True, max_active_sounds=11,
    ),
    "opium": GenreProfile(
        "opium", "phrygian", hat_swing=0.35, drop_tension=True, humanize_drum_velocity=False,
        soft_clip=True, drive=0.9, melody_lag_ms=12.0, voicing_spread=0.15,
        kick_syncopation=0.35, markov_hats=True, melody_octave_down_layer=True, kick_808_duck=True,
        melody_drum_density_inverse=True, melody_root_delay_tension=True, chords_reverse_chop=True,
        counter_melody_matches_direction=True, hook_buildup_chance=0.2,
        bass808_enabled=True, llm_kick808_enabled=False, kick808_micro_humanize=True, max_active_sounds=11,
    ),
    # Don Toliver world: floating, airy, reverb tails on everything; the bass
    # anchors, drums are almost an apology. Slow (130-136), soft, call-response.
    "toliver": GenreProfile(
        "toliver", "natural_minor", hat_swing=0.9, drop_tension=True,
        drive=0.5, melody_lag_ms=14.0, voicing_spread=0.2, kick_syncopation=0.2,
        markov_hats=True, counter_melody=True, melody_volume_db=-5.0,
        melody_harmonizer_layer=True, delay_accent_lane=True, hook_buildup_chance=0.65,
        sub_808_glide=True,
    ),
    # Kanye world: sample-FIRST soul flip. Dorian gospel harmony, slow & warm,
    # tape/vinyl character, chops swing hard — NONE of the trap aggression.
    "kanye": GenreProfile(
        "kanye", "dorian", eight08_slides=False, hat_rolls=False, hat_swing=1.1,
        drop_tension=False, humanize_drum_velocity=True, counter_melody=True,
        drive=0.4, melody_lag_ms=16.0, voicing_spread=0.3, kick_syncopation=0.15,
        melody_volume_db=-4.0, melody_low_cut_hz=140.0, chord_root_octave_down=True,
        hook_buildup_chance=0.65,
    ),
    # Phonk is NOT a trap-family derivative — a separate electronic tradition
    # (Memphis rap roots, now its own scene: cowbell ostinato, distorted 808,
    # chopped/screwed vocals — see style_family.py's PHONK world). Every value
    # below is independently reasoned for phonk, not inherited from rage/opium
    # (this profile used to be a near-copy of rage's numbers — fixed).
    "phonk": GenreProfile(
        "phonk", "natural_minor", eight08_slides=False, hat_rolls=False, hat_swing=0.65,
        drop_tension=True, humanize_drum_velocity=False, soft_clip=True, drive=0.95,
        melody_lag_ms=0.0, voicing_spread=0.0, kick_syncopation=0.15, markov_hats=False,
        melody_octave_down_layer=False, kick_808_duck=True,
        melody_drum_density_inverse=True, counter_melody_matches_direction=False,
        melody_chromatic_walk=True, kick_synced_to_808=True, hook_buildup_chance=0.2,
        bass808_enabled=True, llm_kick808_enabled=False, kick808_micro_humanize=True, max_active_sounds=11,
    ),
    # Optuna-tuned (2026-07 audit decision: JSON values are the truth for drill).
    "drill": GenreProfile(
        "drill", "phrygian", hat_swing=1.28, drop_tension=True, soft_clip=True,
        humanize_drum_velocity=True, stereo_drop=False, drive=0.7, melody_lag_ms=13.82,
        voicing_spread=0.02, kick_syncopation=0.24, markov_hats=False,
        melody_octave_down_layer=True, kick_808_duck=True,
        melody_drum_density_inverse=True, counter_melody_matches_direction=True,
        transition_fill=True, sub_808_register_lift=True,
        bass808_enabled=True, llm_kick808_enabled=False, kick808_micro_humanize=True, max_active_sounds=11,
    ),
    # Dirkey "How To Make Underground Beats": fast, heavy-hitting, deliberately
    # crunchy 808 (clip-driven distortion is the point, not a flaw) — natural
    # minor, not phrygian, distinguishing it from rage/opium's world.
    "underground": GenreProfile(
        "underground", "natural_minor", hat_swing=0.4, drop_tension=True,
        humanize_drum_velocity=False, soft_clip=True, drive=0.9, melody_lag_ms=10.0,
        kick_syncopation=0.3, markov_hats=True, kick_808_duck=True,
        melody_drum_density_inverse=True, sub_808_root_delay_tension=True,
        hi_hats_reverse_open=True, counter_melody_matches_direction=True,
        melody_upward_bias=True, register_collision_avoidance=True, hook_buildup_chance=0.2,
        bass808_enabled=True, llm_kick808_enabled=False, kick808_micro_humanize=True, max_active_sounds=11,
    ),
    # Minimal dark trap: slow (~110 BPM, written literally — the felt "55 BPM"
    # is a half-time reading of the same grid, not a separate tempo), sparse,
    # atmospheric. Deliberately NOT named "dark_trap" — that phrase is already
    # rage's keyword for the fast/aggressive world; this is a different niche.
    "minimal_trap": GenreProfile(
        "minimal_trap", "natural_minor", eight08_slides=False, hat_rolls=False,
        hat_swing=0.6, drive=0.2, melody_lag_ms=16.0, voicing_spread=0.15,
        counter_melody=False, stereo_drop=False,
        melody_octave_up_layer_quiet=True, pre_snare_pickup=True, hook_buildup_chance=0.65,
    ),
    # Summrs-style pluggnb (prod gas + GOYXRD "How To Make Pluggnb") — mellow,
    # melody-led plugg/rnb hybrid. Dorian (softer than opium/rage's phrygian).
    # The 808 sits back (lower sub_808_volume) and lets the melody lead.
    "pluggnb": GenreProfile(
        "pluggnb", "dorian", eight08_slides=False, hat_rolls=False, hat_swing=0.5,
        soft_clip=True,
        drive=0.3, melody_lag_ms=8.0, voicing_spread=0.15, kick_syncopation=0.1,
        sub_808_volume=8500, melody_avoid_root_start=True,
        delayed_clap_hit=True, melody_8bar_variation=True, bell_octave_counter_melody=True,
        bass808_enabled=True, llm_kick808_enabled=False, kick808_micro_humanize=True, max_active_sounds=11,
    ),
    "hyperpop": GenreProfile(
        "hyperpop", "lydian", hat_swing=0.8, drop_tension=True, soft_clip=True, drive=0.95,
        melody_chaotic_wide_jumps=True, sub_808_snare_gate_stutter=True,
        melody_root_fifth_seventh_bias=True, sidechain_depth_bass_type_aware=True,
        hook_buildup_chance=0.2, bass808_enabled=True, llm_kick808_enabled=False, kick808_micro_humanize=True, max_active_sounds=11,
    ),
    "jersey": GenreProfile(
        "jersey", "natural_minor", eight08_slides=False, hat_rolls=False,
        hat_swing=0.5, drive=0.4,
    ),
    "kpop": GenreProfile(
        "kpop", "major", eight08_slides=False, hat_rolls=False, hat_swing=0.3, drive=0.2,
    ),
    "rnb": GenreProfile(
        "rnb", "dorian", eight08_slides=False, hat_rolls=False, hat_swing=0.5, drive=0.15,
        melody_lag_ms=10.0, voicing_spread=0.15,
    ),
    "pop": GenreProfile(
        "pop", "major", eight08_slides=False, hat_rolls=False, hat_swing=0.3, drive=0.25,
        voicing_spread=0.12, chords_major_duplicate_layer=True,
    ),
    "grind": GenreProfile(
        "grind", "locrian", eight08=False, eight08_slides=False, hat_rolls=False,
        hat_swing=0.0, counter_melody=False, stereo_drop=False, soft_clip=True, drive=1.0,
    ),
    # Dark synth-pop — Minor 9th colour (wide voicings), smooth behind-the-beat
    # pocket, no trap rolls, gentle.
    "dark_pop": GenreProfile(
        "dark_pop", "dorian", eight08_slides=False, hat_rolls=False, hat_swing=0.4,
        humanize_drum_velocity=True, soft_clip=False, drive=0.2,
        melody_lag_ms=14.0, voicing_spread=0.22,
    ),
    # Disco-pop / nu-disco — straight grid, off-beat hats, funky syncopated bass,
    # wide bright chords, no rolls.
    "disco_pop": GenreProfile(
        "disco_pop", "dorian", eight08_slides=False, hat_rolls=False, hat_swing=0.2,
        humanize_drum_velocity=True, soft_clip=False, drive=0.2,
        kick_syncopation=0.45, voicing_spread=0.2,
    ),
    # Festival / big-room EDM — minor, layered leads, and an accelerating snare
    # riser (1/4→1/32) with pitch-up before each drop.
    # Optuna-tuned (2026-07 audit decision: JSON values are the truth for edm).
    "edm": GenreProfile(
        "edm", "natural_minor", eight08=False, eight08_slides=False, hat_rolls=False,
        hat_swing=1.34, drop_tension=True, stereo_drop=False, soft_clip=True, drive=0.5,
        melody_lag_ms=6.41, voicing_spread=0.3, kick_syncopation=0.54,
        snare_riser=True, markov_hats=True,
    ),
    # Mainstream pop-rap — trap drum machinery with brighter, chart-ready hooks.
    "pop_rap": GenreProfile(
        "pop_rap", "natural_minor", drop_tension=True, hat_swing=0.8, drive=0.45,
        kick_syncopation=0.25, markov_hats=True, voicing_spread=0.15,
    ),
    # --- Club / electronic ---
    "house": GenreProfile(
        "house", "dorian", eight08=False, eight08_slides=False, hat_rolls=False,
        hat_swing=0.35, drive=0.3, counter_melody=False, voicing_spread=0.2,
        transition_fill=True,
    ),
    "techno": GenreProfile(
        "techno", "natural_minor", eight08=False, eight08_slides=False, hat_rolls=False,
        hat_swing=0.15, drive=0.55, counter_melody=False, stereo_drop=False,
        transition_fill=True,
    ),
    # trance/hardstyle already build into the drop via snare_riser (a longer,
    # pitch-rising 1/4->1/32 roll) — transition_fill would stack a second,
    # shorter roll onto the same landing. Deliberately not opted in here.
    "trance": GenreProfile(
        "trance", "natural_minor", eight08=False, eight08_slides=False, hat_rolls=False,
        hat_swing=0.2, drop_tension=True, drive=0.45, voicing_spread=0.25,
        snare_riser=True,
    ),
    "hardstyle": GenreProfile(
        "hardstyle", "phrygian", eight08=False, eight08_slides=False, hat_rolls=False,
        hat_swing=0.1, drop_tension=True, soft_clip=True, drive=1.0,
        counter_melody=False, snare_riser=True,
    ),
    "dnb": GenreProfile(
        "dnb", "natural_minor", eight08=False, eight08_slides=False, hat_rolls=False,
        hat_swing=0.25, drop_tension=True, drive=0.6, counter_melody=False,
        transition_fill=True,
    ),
    "dubstep": GenreProfile(
        "dubstep", "phrygian", eight08=False, eight08_slides=False, hat_rolls=False,
        hat_swing=0.3, drop_tension=True, soft_clip=True, drive=0.85,
        counter_melody=False, transition_fill=True,
    ),
    "garage": GenreProfile(
        "garage", "dorian", eight08=False, eight08_slides=False, hat_rolls=False,
        hat_swing=1.15, drive=0.35, voicing_spread=0.18,
        transition_fill=True,
    ),
    # --- Hip-hop lineage ---
    "lofi": GenreProfile(
        "lofi", "dorian", eight08_slides=False, hat_rolls=False, hat_swing=0.9,
        drive=0.15, melody_lag_ms=18.0, voicing_spread=0.2, counter_melody=False,
        stereo_drop=False,
    ),
    "boombap": GenreProfile(
        "boombap", "natural_minor", eight08_slides=False, hat_rolls=False,
        hat_swing=0.85, drive=0.35, melody_lag_ms=14.0,
    ),
    # West Coast — modern G-funk/trap fusion (DJ Mustard/YG/Mustard-era laid-back
    # bounce, not the pre-808 90s original): whiny dorian lead, funky syncopated
    # kick, gritty-not-clipped drive, laid-back pocket. Opens off the tonic
    # (Deon/Infinite) — the one concrete move from that video.
    "west_coast": GenreProfile(
        "west_coast", "dorian", eight08_slides=False, hat_rolls=False, soft_clip=True,
        hat_swing=0.75, drive=0.35, melody_lag_ms=10.0, voicing_spread=0.2,
        kick_syncopation=0.2, melody_avoid_root_start=True, chorus_pad_holdback=True,
        bass808_enabled=True, llm_kick808_enabled=False, kick808_micro_humanize=True, max_active_sounds=11,
    ),
    # Jay Cactus + Lollipopbeatz (Detroit) — two sources. Simple/repetitive by
    # design (the pedal-heavy progression above does the heavy lifting): one
    # short melodic phrase, a higher-tuned 808, 3-4 layers max.
    "detroit": GenreProfile(
        "detroit", "natural_minor", eight08_slides=False, hat_rolls=False, hat_swing=0.7,
        soft_clip=True,
        drive=0.35, melody_lag_ms=10.0, voicing_spread=0.05, kick_syncopation=0.15,
        melody_top_layer_muted_lows=True, alternating_bass_sections=True,
        open_hat_before_snare_cut=True, intro_melody_octave_up=True,
        sub_808_register_high=True, bass808_enabled=True, llm_kick808_enabled=False, kick808_micro_humanize=True, max_active_sounds=11,
    ),
    # --- Global / rhythm-first ---
    "reggaeton": GenreProfile(
        "reggaeton", "natural_minor", eight08_slides=False, hat_rolls=False,
        hat_swing=0.25, drive=0.35,
    ),
    "dancehall": GenreProfile(
        "dancehall", "natural_minor", eight08_slides=False, hat_rolls=False,
        hat_swing=0.35, drive=0.4,
    ),
    "afrobeats": GenreProfile(
        "afrobeats", "dorian", eight08_slides=False, hat_rolls=False,
        hat_swing=0.7, drive=0.25, melody_lag_ms=10.0,
    ),
    "amapiano": GenreProfile(
        "amapiano", "dorian", eight08=False, eight08_slides=False, hat_rolls=False,
        hat_swing=0.6, drive=0.3, melody_lag_ms=12.0,
    ),
    # --- Organic / band ---
    "funk": GenreProfile(
        "funk", "dorian", eight08=False, eight08_slides=False, hat_rolls=False,
        hat_swing=0.8, drive=0.3, voicing_spread=0.25, melody_lag_ms=8.0,
    ),
    "soul": GenreProfile(
        "soul", "dorian", eight08=False, eight08_slides=False, hat_rolls=False,
        hat_swing=0.6, drive=0.15, melody_lag_ms=16.0, voicing_spread=0.3,
        stereo_drop=False,
    ),
    "rock": GenreProfile(
        "rock", "natural_minor", eight08=False, eight08_slides=False, hat_rolls=False,
        hat_swing=0.2, soft_clip=True, drive=0.8, counter_melody=False,
        stereo_drop=False,
    ),
    "cinematic": GenreProfile(
        "cinematic", "natural_minor", eight08=False, eight08_slides=False,
        hat_rolls=False, hat_swing=0.0, drive=0.4, voicing_spread=0.25,
    ),
}

_BUILTIN_PROFILES: dict[str, GenreProfile] = {name: replace(p) for name, p in PROFILES.items()}

# Detection keywords (EN + RU). Checked in PRIORITY order so e.g. "k-pop" and
# "hyperpop" win over a bare "pop" substring.
# Generic genre / style descriptors only — no artist, producer or label names.
GENRE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "grind": ("grindcore", "grind core", "anti-music", "anti music", "антимузык", "гриндкор", "blastbeat", "blast beat"),
    "opium": ("opium", "опium"),
    # NB: bare "gospel"/"госпел" belongs to soul (EN/RU parity) — kanye only
    # owns the sample-flip phrasings ("gospel sample"/"госпел сэмпл").
    "kanye": ("soul flip", "sample flip", "gospel sample", "chipmunk soul", "госпел сэмпл", "соул сэмпл", "сэмпл флип", "kanye", "канье", "chop"),
    "toliver": ("houston bounce", "floaty trap", "плавающий бит", "toliver"),
    "rage": ("rage", "дарк трэп", "dark trap", "jerk", "sigma", "хорор", "horror trap"),
    # Summrs-style pluggnb (prod gas, GOYXRD) — mellow, melody-led plugg/rnb
    # hybrid. Deliberately split off from "rage" (see above) which used to own
    # these keywords but is the wrong, aggressive-tempered fallback for this.
    "pluggnb": ("pluggnb", "plugg", "plugg rnb", "plugg r&b", "melodic plugg", "плагг", "плагг рнб", "мелодик плагг"),
    "underground": (
        "underground beats", "underground trap", "underground hip hop", "underground hiphop",
        "андеграунд", "андерграунд", "подземный",
    ),
    # Deliberately NOT "dark trap"/"дарк трэп" — those already route to rage
    # (fast/aggressive). This is the slow, sparse, atmospheric niche instead.
    "minimal_trap": (
        "minimal trap", "atmospheric trap", "sparse trap", "night trap",
        "минимал трэп", "минималистичный трэп", "ночной трэп", "атмосферный трэп",
    ),
    "phonk": ("phonk", "memphis", "cowbell", "фонк", "дрифт", "drift"),
    "drill": ("drill", "дрилл", "uk drill", "ny drill"),
    "hyperpop": ("hyperpop", "hyper pop", "гиперпоп", "glitchcore", "glitch core", "digicore", "digi core", "диджикор"),
    "jersey": ("jersey", "jersey club", "джерси", "bed squeak"),
    "kpop": ("kpop", "k-pop", "к-поп", "кпоп", "idol pop"),
    "rnb": ("rnb", "r&b", "rhythm and blues", "neo soul", "neo-soul", "neosoul", "эрэнби"),
    "amapiano": ("amapiano", "амапиано", "log drum", "лог драм"),
    "hardstyle": ("hardstyle", "хардстайл", "rawstyle", "gabber", "габбер", "hardcore techno"),
    "dnb": ("drum and bass", "drum & bass", "drum'n'bass", "dnb", "днб", "драм-н-бейс", "драмнбейс", "jungle", "liquid funk", "neurofunk"),
    "dubstep": ("dubstep", "дабстеп", "brostep", "riddim", "wobble"),
    "garage": ("uk garage", "ukg", "2-step", "2 step", "two step", "garage", "гараж"),
    "house": ("deep house", "tech house", "bass house", "afro house", "progressive house", "house", "хаус"),
    "techno": ("techno", "техно"),
    "trance": ("psytrance", "trance", "транс"),
    "reggaeton": ("reggaeton", "реггетон", "dembow", "дембоу", "perreo", "latin urbano"),
    "dancehall": ("dancehall", "дансхолл"),
    "afrobeats": ("afrobeats", "afrobeat", "afro beat", "afroswing", "afropop", "афробит"),
    "boombap": ("boom bap", "boombap", "бум бэп", "бумбэп", "old school hip hop", "90s hip hop", "golden era"),
    "west_coast": (
        "west coast", "westcoast", "west-coast", "g-funk", "gfunk", "g funk", "westside",
        "вест-кост", "вест кост", "джи-фанк", "джи фанк",
    ),
    "detroit": ("detroit", "detroit type beat", "детройт", "детройт тайп бит", "detroit trap"),
    "lofi": ("lofi", "lo-fi", "lo fi", "лоуфай", "лофай", "chillhop", "чилхоп", "study beat"),
    "cinematic": ("cinematic", "кинематограф", "orchestral", "оркестр", "epic trailer", "trailer music", "soundtrack", "саундтрек", "film score"),
    "rock": ("rock", "рок", "metal", "метал", "punk", "панк", "grunge", "гранж", "guitar", "гитар"),
    "funk": ("funk", "фанк", "funky"),
    "soul": ("soul", "соул", "motown", "gospel", "госпел"),
    "pop_rap": ("pop rap", "pop-rap", "поп рэп", "поп-рэп", "mainstream rap", "female rap", "club rap"),
    "edm": ("edm", "big room", "bigroom", "festival", "future bounce", "main stage"),
    "disco_pop": ("nu-disco", "nu disco", "disco pop", "dance pop", "disco", "диско"),
    "dark_pop": ("dark pop", "synthwave pop", "synthwave", "80s pop", "alt pop", "synth-pop"),
    "pop": ("pop", "поп", "synthpop", "synth pop"),
}
# NB: "garage" must be checked before "rage" ("uk gaRAGE" substring collision).
# NB: "west_coast" must be checked before "funk" ("g-FUNK" substring collision).
# NB: "underground" must be checked before "rage" ("underground TRAP" used to be a rage keyword).
PRIORITY = (
    "grind", "garage", "underground", "minimal_trap", "pluggnb", "rage", "opium", "toliver", "kanye", "phonk", "drill", "hyperpop", "jersey",
    "kpop", "rnb", "amapiano", "hardstyle", "dnb", "dancehall", "dubstep",
    "house", "techno", "trance", "reggaeton", "afrobeats", "boombap", "west_coast", "detroit", "lofi",
    "cinematic", "rock", "funk", "soul", "pop_rap", "edm", "disco_pop", "dark_pop", "pop",
)


# ---------------------------------------------------------------------------
# Lane focus — MVP ships hip-hop only. Every other lane's code and data stays
# (tested, reversible), but routing clamps to the hip-hop family so tuning,
# corpus and marketing all concentrate on one audience. PLG_LANES=all restores
# the full router.
# ---------------------------------------------------------------------------

# The lanes a generation may actually run on.
#
# 2026-08-11: narrowed to DETAILED_GENRES (below). This used to also admit
# toliver, kanye, boombap, lofi, jersey and minimal_trap — lanes that pass this
# filter unchanged but then fail `is_genre_detailed` in plg_api.create_beat's
# gate, so a MIDI detected as any of them produced "Упс, этот жанр пока не
# доступен" instead of a beat. Eight genres were affected (those six plus soul
# and funk, whose fallbacks landed on lofi/boombap). Every lane now resolves to
# a genre that is both allowed and detailed, so detection can no longer refuse
# to generate.
#
# The product is a hip-hop/trap generator. Every other GenreProfile is a
# leftover from an early "more genres = better" phase, was never tuned to the
# same depth (avg 7 field overrides and 12 test references, vs 17 and 97 for
# the ten below), and never reaches generation.
HIPHOP_LANES = frozenset({
    "trap", "rage", "opium", "drill", "west_coast", "underground",
    "pluggnb", "detroit", "hyperpop", "phonk",
})

# Nearest tuned cousin for every gated lane, so any input still lands well:
# an R&B ask becomes melodic trap, EDM energy becomes rage, boom-bap/lofi warmth
# becomes west_coast. Every value here MUST be in DETAILED_GENRES — a fallback
# onto an untuned lane is what caused the block described above.
_FOCUS_FALLBACK: dict[str, str] = {
    # pop / r&b family -> melodic trap
    "rnb": "trap", "pop_rap": "trap", "pop": "trap", "kpop": "trap",
    "disco_pop": "trap", "dark_pop": "trap",
    # laid-back, sample-driven hip-hop -> west coast
    "boombap": "west_coast", "lofi": "west_coast", "soul": "west_coast",
    "funk": "west_coast", "kanye": "west_coast",
    # melodic/atmospheric hip-hop -> opium
    "toliver": "opium",
    # club/bounce hip-hop -> drill
    "jersey": "drill",
    # trap variants
    "minimal_trap": "trap",
    # high-energy electronic -> rage
    "hardstyle": "rage", "dubstep": "rage", "grind": "rage", "rock": "rage",
    # four-on-the-floor / broken beat / world -> trap
    "edm": "trap", "house": "trap", "techno": "trap", "trance": "trap",
    "dnb": "trap", "garage": "trap", "cinematic": "trap", "amapiano": "trap",
    "afrobeats": "trap", "dancehall": "trap", "reggaeton": "trap",
}


def lane_focus() -> str:
    return os.environ.get("PLG_LANES", "hiphop").strip().lower() or "hiphop"


def allowed_lanes() -> frozenset[str]:
    if lane_focus() in ("all", "any", "off", "0", "false"):
        return frozenset(PROFILES)
    return HIPHOP_LANES


# 2026-07: genres that actually went through a detailed, video-tutorial-driven
# tuning pass (real per-genre opt-in flags: melody development, drum pocket,
# sample-selection bias — not just a name in PROFILES). Everything else is a
# plain GenreProfile with at most 1-2 tweaked defaults; generating from one of
# those directly (PLG_LANES=all, or a HIPHOP_LANES member with no fallback —
# toliver/kanye/boombap/lofi/jersey/minimal_trap) risks a mediocre result the
# user never asked to compare against something better. Most OTHER genres
# never reach this at all under the default hiphop lane focus above — they
# get redirected by _FOCUS_FALLBACK to a working substitute (house -> trap,
# dubstep -> rage, hyperpop -> rage under the default...) before generation.
DETAILED_GENRES = frozenset({
    "trap", "rage", "opium", "drill", "west_coast", "underground",
    "pluggnb", "detroit", "hyperpop", "phonk",
})


def is_genre_detailed(genre: str) -> bool:
    return genre in DETAILED_GENRES


def clamp_to_focus(genre: str) -> str:
    """Map a detected lane into the active focus (identity when PLG_LANES=all)."""
    if genre in allowed_lanes():
        return genre
    return _FOCUS_FALLBACK.get(genre, "trap")


def detect_genre(style: str = "", prompt: str = "") -> str:
    text = f"{style} {prompt}".lower()
    for genre in PRIORITY:
        if any(keyword in text for keyword in GENRE_KEYWORDS[genre]):
            return clamp_to_focus(genre)
    # No explicit genre word — "type beat" fallback: resolve artist references
    # ("nessa barrett vibe", «как у дуа липы») to their home genre. Local only;
    # artist names never reach the LLM (see artist_styles.sanitize_artist_names).
    try:
        from artist_styles import resolve_artist_style

        hit = resolve_artist_style(text)
        if hit and hit["genre"] in PROFILES:
            return clamp_to_focus(str(hit["genre"]))
    except Exception:  # noqa: BLE001 — detection must never break generation
        pass
    return "trap"


_ASSET_JSON = Path(__file__).resolve().parent / "assets" / "genre_profiles.json"


def _apply_profile_overrides() -> None:
    """Overlay preset values from JSON so genres can be retuned (or served from
    the cloud) without code changes. Source: PLG_GENRE_PROFILES_JSON env path, or
    the bundled assets/genre_profiles.json. Built-in PROFILES stay the fallback;
    any error is swallowed so a bad file can never break generation."""
    try:
        path = os.getenv("PLG_GENRE_PROFILES_JSON") or (
            str(_ASSET_JSON) if _ASSET_JSON.is_file() else ""
        )
        if not path:
            return
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        data = raw.get("profiles") if isinstance(raw, dict) and "profiles" in raw else raw
        valid = {f.name for f in fields(GenreProfile)}
        for name, fields_dict in (data or {}).items():
            if not isinstance(fields_dict, dict):
                continue
            if "filth" in fields_dict and "drive" not in fields_dict:
                fields_dict = dict(fields_dict)
                fields_dict["drive"] = fields_dict["filth"]
            patch = {
                k: v
                for k, v in fields_dict.items()
                if k in valid and k not in ("name", "filth")
            }
            if name in PROFILES:
                PROFILES[name] = replace(PROFILES[name], **patch)
            else:
                PROFILES[name] = GenreProfile(name=name, **patch)
    except Exception as exc:  # noqa: BLE001 — never let presets break the engine
        logger.warning("genre profile overrides ignored: %s", exc)


_apply_profile_overrides()


def builtin_profile(genre: str) -> GenreProfile:
    """Shipped defaults (ignores JSON overlay) — used by offline tuner."""
    return _BUILTIN_PROFILES.get(genre, DEFAULT)


def profile_for(
    style: str = "",
    prompt: str = "",
    *,
    max_drive: bool = False,
    genre_hint: str | None = None,
) -> GenreProfile:
    if genre_hint and genre_hint in PROFILES:
        profile = PROFILES[genre_hint]
    else:
        profile = PROFILES.get(detect_genre(style, prompt), DEFAULT)
    if max_drive:
        profile = replace(
            profile,
            drive=1.0,
            soft_clip=True,
            drop_tension=True,
            humanize_drum_velocity=False,
            hat_swing=max(profile.hat_swing, 1.5),
        )
    return profile

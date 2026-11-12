"""Arrange a short core loop into a full song — structure AND per-section development.

LLMs reliably write only a few bars, so PLG generates a strong CORE and then
deterministically develops it across a real arrangement with verse↔chorus
alternation. Crucially, each section does NOT just gate which tracks play — it
*re-develops* the beat so the chorus genuinely hits different from the verse:

  • verse  — steady 1/8 hats only, softer, no rolls (the "breathing" section)
  • chorus — denser hats + extra driving kicks + hat rolls (the drop)
  • hook   — full identity loop, rolls on
  • outro  — wind-down, steady hats

Hook-first: full beat + melody from bar 0 (no long drumless intro). Each track
tiles by its own loop length, then the per-section development runs on top.

Runs before humanize, so swing / hat-rolls / drop-tension / register layout all
apply across the whole song. The result is one long pattern whose notes span the
full timeline — FL plays the entire song on open (no playlist surgery needed).
Hat rolls are gated by section (see ROLL_SECTIONS) so the humanizer only fires
machine-gun rolls in the chorus/hook, never over the steady verse.
"""

from __future__ import annotations

import math
import os
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pattern_utils import TRACK_KEYS, track_notes

BEATS_PER_BAR = 4.0
TARGET_SONG_SECONDS = 120.0


def _target_song_bars(pattern: dict[str, Any]) -> int:
    """Return a bar count close to two minutes at the session tempo.

    Arrangement used to mean a fixed 40/52 bars, which accidentally meant a
    one-minute export at 160 BPM.  Duration is a listening-time requirement,
    so derive the form from tempo and keep it on a whole four-bar boundary.

    2026-08-12: this used to round the bar count and then apply a hard
    ``max(40, ...)`` floor.  That floor is a tempo-blind constant — it binds
    below roughly 76 BPM, where it overrode the duration target outright: a
    68 BPM session asked for 34 bars, got floored to 40 and exported 141 s
    against a 120 s target.  The form is now chosen by which whole four-bar
    length lands CLOSEST to the target duration, which is what the target
    actually means.
    """
    try:
        bpm = float(pattern.get("bpm", 140) or 140)
    except (TypeError, ValueError):
        bpm = 140.0
    bpm = max(60.0, min(220.0, bpm))
    raw_bars = TARGET_SONG_SECONDS * bpm / (60.0 * BEATS_PER_BAR)
    nearest = max(1, int(round(raw_bars / 4.0)))
    candidates = [step * 4 for step in (nearest - 1, nearest, nearest + 1) if step >= 1]
    # A target sitting exactly between two four-bar forms is a genuine tie
    # (at 140 BPM, 120 s is two bars from both 68 and 72). The old expression
    # resolved those through Python's round-half-to-even, which happens to
    # pick the longer form at 140 BPM and the shorter one at 180. That is
    # arbitrary — but it is also shipped behaviour on tempos that were never
    # broken, so ties keep deferring to it and only the previously floored
    # range below ~76 BPM changes at all. At a fixed tempo
    # |bars - raw_bars| is proportional to |seconds - target|, so comparing
    # bar error is comparing duration error.
    legacy = nearest * 4
    best = min(
        candidates, key=lambda bars: (abs(bars - raw_bars), bars != legacy, -bars)
    )
    # 16 bars is a sanity floor for an absurd input, not a form decision: the
    # clamped 60-220 BPM range never reaches it. Do not restore a floor large
    # enough to override the duration target again.
    return max(16, min(160, best))


def _extend_sections_to_duration(
    sections: tuple[tuple[str, int, tuple[str, ...]], ...], target_bars: int
) -> tuple[tuple[str, int, tuple[str, ...]], ...]:
    """Extend the middle of a form while preserving its intro and real outro."""
    if not sections or sum(bars for _name, bars, _tracks in sections) >= target_bars:
        return sections
    outro = sections[-1] if sections[-1][0] == "outro" else None
    body = list(sections[:-1] if outro else sections)
    current = sum(bars for _name, bars, _tracks in body) + (outro[1] if outro else 0)
    # Reuse actual verse/drop material from this genre's form; never invent a
    # new static loop or silently change the beginning/end sections.
    cycle = [section for section in body if section[0] in ("verse", "chorus", "drop")]
    if not cycle:
        return sections
    cursor = 0
    while current < target_bars:
        name, bars, tracks = cycle[cursor % len(cycle)]
        remaining = target_bars - current
        take = min(bars, remaining)
        if take < bars:
            # Never make a tiny final chorus/drop: that would incorrectly
            # become the song's "final chorus" and weaken the actual peak.
            # The leftover is a short verse-like breath into the outro.
            verse = next((section for section in cycle if section[0] == "verse"), (name, bars, tracks))
            name, _verse_bars, tracks = verse
        # A last four-bar fragment is a deliberate short transition before
        # the outro, not a duration overshoot.
        body.append((name, take, tracks))
        current += take
        cursor += 1
    if outro:
        body.append(outro)
    return tuple(body)


_MIN_SECTION_BARS = 4
_PEAK_SECTION_NAMES = ("hook", "chorus", "drop")


def _shrink_sections_to_duration(
    sections: tuple[tuple[str, int, tuple[str, ...]], ...], target_bars: int
) -> tuple[tuple[str, int, tuple[str, ...]], ...]:
    """Trim the middle of a form that overshoots the target duration.

    The counterpart to _extend_sections_to_duration, and the reason a slow
    session used to run long: the arranger could only ever grow a form. Below
    roughly 76 BPM the target is fewer bars than the base MIDI form already
    holds (40), so nothing acted on it and a 68 BPM ballad exported 141 s
    against a 120 s target.

    What it will not touch, in order of importance: the intro, the real outro,
    and the FIRST peak section (hook/chorus/drop) — that one is the song
    stating its identity. Everything else is trimmed in whole four-bar steps,
    later repeats before earlier ones and plain verses before peaks, never
    below a four-bar section and never by removing a section outright. Like
    the extend side this is best-effort: if the protected sections alone
    already exceed the target, the form is returned as-is rather than
    mutilated.
    """
    if not sections:
        return sections
    total = sum(bars for _name, bars, _tracks in sections)
    if total <= target_bars:
        return sections

    protected = {0, len(sections) - 1}
    for index, (name, _bars, _tracks) in enumerate(sections):
        if name in _PEAK_SECTION_NAMES:
            protected.add(index)
            break

    order = sorted(
        (index for index in range(len(sections)) if index not in protected),
        # verses first (0), then peak repeats (1); later occurrences before
        # earlier ones so the song loses its tail repeats, not its opening.
        key=lambda index: (sections[index][0] in _PEAK_SECTION_NAMES, -index),
    )
    if not order:
        return sections

    bars_by_index = {index: bars for index, (_n, bars, _t) in enumerate(sections)}
    excess = total - target_bars
    trimmed_any = True
    while excess > 0 and trimmed_any:
        trimmed_any = False
        for index in order:
            if excess <= 0:
                break
            if bars_by_index[index] - 4 < _MIN_SECTION_BARS:
                continue
            bars_by_index[index] -= 4
            excess -= 4
            trimmed_any = True

    return tuple(
        (name, bars_by_index[index], tracks)
        for index, (name, _bars, tracks) in enumerate(sections)
    )


def _beat_switch_enabled() -> bool:
    return (os.environ.get("PLG_BEAT_SWITCH", "auto").strip().lower()
            not in ("0", "false", "no", "off"))


def _melody_section_variation_enabled() -> bool:
    """hook stays the untouched identity statement either way — this only
    gates whether verse/chorus/intro/outro get real motif development
    (sequence/retrograde/fragmentation, see melody_transform.py) instead of
    the exact same core tiled verbatim everywhere."""
    return (os.environ.get("PLG_MELODY_SECTION_VARIATION", "auto").strip().lower()
            not in ("0", "false", "no", "off"))


def _hook_buildup_enabled() -> bool:
    return (os.environ.get("PLG_HOOK_BUILDUP", "auto").strip().lower()
            not in ("0", "false", "no", "off"))

# Hook = melody-only intro on the playlist. The FLP writer derives each drum
# clip's real first bar from the arranged notes; this must not be coupled to a
# fixed "bar 9" export offset.
_HOOK_TRACKS = ("melody_lead", "counter_melody")

# Tracks that play in the lighter sections (keep the core, drop the extras so the
# verse breathes and the chorus opens up by comparison).
_VERSE_TRACKS = ("melody_lead", "kick", "hi_hats", "clap", "sub_808", "snare", "snare_layer")
_OUTRO_TRACKS = ("melody_lead", "hi_hats", "sub_808", "kick")

# Song shape with real verse↔chorus alternation so the beat genuinely changes
# across the track instead of one loop running start to finish. 52 bars.
# Press-play-and-it-knocks: only a SHORT 4-bar melody intro, then the full
# beat states its identity — 8 bars of naked melody made first impressions
# feel like the project "opened and does nothing".
DEFAULT_SECTIONS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("intro", 4, _HOOK_TRACKS),
    ("hook", 8, TRACK_KEYS),
    ("verse", 8, _VERSE_TRACKS),
    ("chorus", 8, TRACK_KEYS),
    ("verse", 8, _VERSE_TRACKS),
    ("chorus", 8, TRACK_KEYS),
    ("outro", 8, _OUTRO_TRACKS),
)

# West Coast (Jay Cactus "How To Make Hard West-Coast Beats From Scratch"):
# the intro is piano (melody_lead) + bass (sub_808) only — no drums yet, the
# beat "opens" instead of hitting immediately. Everything else matches
# DEFAULT_SECTIONS (the pad/"roads" holdback on the first chorus pass is a
# separate note-removal postprocess — see apply_west_coast_pad_holdback in
# beat_humanize.py, since "chords" isn't materialized until after arrangement).
_WC_INTRO_TRACKS = ("melody_lead", "sub_808")
WEST_COAST_SECTIONS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("intro", 4, _WC_INTRO_TRACKS),
    ("hook", 8, TRACK_KEYS),
    ("verse", 8, _VERSE_TRACKS),
    ("chorus", 8, TRACK_KEYS),
    ("verse", 8, _VERSE_TRACKS),
    ("chorus", 8, TRACK_KEYS),
    ("outro", 8, _OUTRO_TRACKS),
)

# Buildup variant of DEFAULT_SECTIONS (audit follow-up, 2026-07): the cold-open
# hook (full band from bar 0 of the hook) is ONE legal outcome, not the only
# one — reference producers (Dirkey/Slayr/Nick Mira/"Still Countin") stagger
# the rhythm section in before the hook lands instead of slamming it all in at
# once. Same 4-bar budget as DEFAULT_SECTIONS' intro, just split into two
# 2-bar waves: melody-only, THEN kick+808 join (still no snare/clap/hats)
# before the hook finally lands full-band. Chosen per-generation by
# GenreProfile.hook_buildup_chance — see resolution in arrange_song().
_PREHOOK_TRACKS = _HOOK_TRACKS + ("kick", "sub_808")
BUILDUP_SECTIONS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("intro", 2, _HOOK_TRACKS),
    ("prehook", 2, _PREHOOK_TRACKS),
    ("hook", 8, TRACK_KEYS),
    ("verse", 8, _VERSE_TRACKS),
    ("chorus", 8, TRACK_KEYS),
    ("verse", 8, _VERSE_TRACKS),
    ("chorus", 8, TRACK_KEYS),
    ("outro", 8, _OUTRO_TRACKS),
)

# MIDI-owned production has one intentionally small, predictable song form.
# The uploaded MIDI remains the only melodic voice; PLG develops only the
# rhythm section around it.
_MIDI_ALLOWED_TRACKS = (
    "melody_lead",
    "kick",
    "808_bass",
    "hi_hats",
    "clap",
    "snare",
    "snare_layer",
)
_MIDI_VERSE_TRACKS = (
    "melody_lead",
    "kick",
    # Uploaded-MIDI sessions render their actual bass through 808_bass.
    # Keeping the retired sub_808 key here made the arrangement describe a
    # different lane than the one later written by the low-end composer.
    "808_bass",
    "hi_hats",
    "clap",
    "snare",
    "snare_layer",
)
_MIDI_OUTRO_TRACKS = ("melody_lead", "hi_hats", "kick")
# The reference five-part shape — changed 2026-08-26, the project issue log (not included) §0ab.
#
#   intro → hook → verse → hook-again → (short switch) → outro
#
# The old form opened on a verse, stated the hook ONCE at bar 12 and never
# returned to it, and then let `_extend_sections_to_duration` fill the rest
# from a cycle of `("verse", "chorus", "drop")` — which the hook is not a
# member of. Measured on the shipped corpus: hook x1, verse 56-60 % of the
# song, and 5-6 consecutive verses in the tail.
#
# **The second peak is named `drop`, not `hook`, and that is load-bearing.**
# Five confirmed mechanisms key off the section NAME — `drum_density.
# section_pocket_ceiling` (2 low-end onsets against 1), `scaled_shape`,
# `midi_phrase_drums._backbeat_figure`'s pre-drop skip, `producer_move_
# director.choose_moves`' hook/chorus/drop gate, and `ROLL_SECTIONS`. Two more
# read the name indirectly: `arrange_song`'s `chorus_names = ("chorus", "drop")`
# picks the final chorus (and with it `plg_beat_switch`), and `half_bar_stop`
# lands on drops. A form whose second peak is called "hook" was modelled and
# measured: `final_chorus_idx` becomes -1 on ALL five corpus files, the beat
# switch disappears, and `half_bar_stop` never fires — three silent losses. As
# `drop`, every one of those readers keeps working with no edit at all, and the
# listener still hears the hook return, louder. Musically that IS a drop.
#
# Block length is 16 bars, not 8: at 120-160 BPM that is the 25-32 s the
# reference asks for per part, and it keeps the form at 5-6 sections instead
# of 9-11.
MIDI_SECTIONS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("intro", 8, ("melody_lead",)),
    ("hook", 16, _MIDI_ALLOWED_TRACKS),
    ("verse", 16, _MIDI_VERSE_TRACKS),
    ("drop", 16, _MIDI_ALLOWED_TRACKS),
    ("outro", 8, _MIDI_OUTRO_TRACKS),
)
# Below roughly 112 BPM the 16-bar form does not fit the 120 s target without
# being mutilated. Two separate reasons, both measured:
#
#   * under ~80 BPM it cannot be shrunk far enough at all.
#     `_shrink_sections_to_duration` protects the intro, the outro and the
#     FIRST peak, so its floor is 8 + 16 + 4 + 4 + 8 = 40 bars while a 68 BPM
#     ballad wants 34 -- which is exactly the "141 s at 68 BPM" regression that
#     function was written to close, walking back in through the front door;
#   * between 80 and 110 BPM it technically fits, but only by trimming verse
#     and drop to the 4-bar minimum: `in8 ho16 ve4 dr4 ou8` is a four-bar drop
#     after a sixteen-bar hook, which is not the reference shape in any
#     meaningful sense.
#
# So the block size is chosen from the target instead of being fixed.
_MIDI_SECTIONS_MIN_BARS_FOR_LONG_BLOCKS = 56


def _halved(
    sections: tuple[tuple[str, int, tuple[str, ...]], ...],
) -> tuple[tuple[str, int, tuple[str, ...]], ...]:
    return tuple(
        (name, max(_MIN_SECTION_BARS, bars // 2), tracks)
        for name, bars, tracks in sections
    )


def midi_sections_for_target(target_bars: int) -> tuple[tuple[str, int, tuple[str, ...]], ...]:
    """The five-part form at a block size the target can actually hold.

    Same shape either way — intro, hook, verse, drop, outro — only shorter
    blocks at a slow tempo. Nothing else in the engine sees a difference,
    because every consumer keys off the section NAME, not its length.
    """
    if target_bars >= _MIDI_SECTIONS_MIN_BARS_FOR_LONG_BLOCKS:
        return MIDI_SECTIONS
    return _halved(MIDI_SECTIONS)


# The form as it stood before 2026-08-26, kept as the A/B arm and as the thing
# a revert restores. Not read by production code.
LEGACY_MIDI_SECTIONS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("intro", 4, ("melody_lead",)),
    ("verse", 8, _MIDI_VERSE_TRACKS),
    ("hook", 8, _MIDI_ALLOWED_TRACKS),
    ("verse", 8, _MIDI_VERSE_TRACKS),
    ("drop", 8, _MIDI_ALLOWED_TRACKS),
    ("outro", 4, _MIDI_OUTRO_TRACKS),
)

# Per-genre section templates — resolved automatically in arrange_song() when
# the caller doesn't pass ``sections`` explicitly (every real call site).
# west_coast keeps its own dedicated (always-on) bass-first intro — its
# hook_buildup_chance plays no role since this override wins outright.
_GENRE_SECTIONS: dict[str, tuple[tuple[str, int, tuple[str, ...]], ...]] = {
    "west_coast": WEST_COAST_SECTIONS,
}

# Sections where the humanizer's hat-roll engine is allowed to fire. The verse /
# outro stay on steady hats so the chorus rolls land as a real lift.
ROLL_SECTIONS: tuple[str, ...] = ("hook", "chorus", "drop")


@dataclass(frozen=True)
class SectionStyle:
    """How a section develops the core loop — its energy + drum contrast."""

    energy: float       # velocity scaler for the section's drums / 808
    hat_feel: str       # "straight" = steady 1/8 (no rolls); "rolls" = busy + rolls
    hat_density: float  # multiplier on hat hits inside a "rolls" section (>1 adds fills)
    kick_drive: float   # 0..1 extra driving kicks per bar (chorus momentum); 0 = none


# Per-section character. Verse breathes; chorus drops; hook states the identity.
_STYLES: dict[str, SectionStyle] = {
    "hook": SectionStyle(energy=0.92, hat_feel="rolls", hat_density=1.0, kick_drive=0.20),
    "verse": SectionStyle(energy=0.70, hat_feel="straight", hat_density=0.5, kick_drive=0.0),
    "chorus": SectionStyle(energy=1.00, hat_feel="rolls", hat_density=1.3, kick_drive=0.5),
    "drop": SectionStyle(energy=1.00, hat_feel="rolls", hat_density=1.4, kick_drive=0.6),
    "outro": SectionStyle(energy=0.60, hat_feel="straight", hat_density=0.7, kick_drive=0.0),
    "intro": SectionStyle(energy=0.65, hat_feel="straight", hat_density=0.6, kick_drive=0.0),
    # BUILDUP_SECTIONS only — kick+808 easing in, still restrained (no drive,
    # no hats to speak of), a notch hotter than the naked intro before it.
    "prehook": SectionStyle(energy=0.75, hat_feel="straight", hat_density=0.6, kick_drive=0.0),
}
_DEFAULT_STYLE = SectionStyle(energy=0.85, hat_feel="rolls", hat_density=1.0, kick_drive=0.0)

# Velocity-scaled tracks (melody is left to the humanizer's verse-duck / chorus-widen).
_ENERGY_KEYS = ("kick", "snare", "snare_layer", "clap", "sub_808", "hi_hats")

# Weak-beat pushes for chorus drive kicks: clear of the clap (2.0) and the
# pre-drop tension gap (~3.5–4.0) so the humanizer's drop_tension still reads.
_DRIVE_OFFSETS = (0.75, 2.5)


# --- Section dynamics: ONE owner for "how loud is this section" -------------
#
# `_develop_section` below already scales `_ENERGY_KEYS` by `style.energy` at
# ARRANGE time, but on the MIDI route kick/808 (stages 20/23), clap/snare
# (stage 8) and the hats (stage 18) are all rebuilt afterwards, so that
# scaling is overwritten before it reaches the file. Measured on the five
# corpus files 2026-08-26: kick carried exactly two velocities in a whole song
# (124 and the 87 ghost) and 808_bass exactly one (116), in every section.
#
# The late lane normalizers therefore read the section's level from here
# instead of re-deriving it: `beat_humanize._normalize_kick808_dynamics` and
# its export audit `_audit_kick808_symbiosis` for kick/808, and
# `beat_humanize._apply_section_dynamics` for clap/snare/snare_layer and the
# melody's octave layer. Two copies of this arithmetic is the disagreement
# the project issue log (not included) 0.1 is about; on the kick/808 pair it is fatal rather than
# latent, because the audit rejects the whole generation on velocity_drift.
#
# The scale is the section's own energy over the LOUDEST section's, so the
# peak keeps its tuned level exactly and every other section steps down from
# it. That direction is not cosmetic: the project issue log (not included) 0u measured that the hook
# has no headroom left to raise (clap/snare already sit at 127 from the second
# verse to the second drop on three of five files), so section contrast can
# only be made by subtraction.
_SECTION_VELOCITY_FLOOR = 0.82


def section_velocity_scales(arrangement: Any) -> list[tuple[float, float, float]]:
    """``plg_arrangement`` as sorted ``(start_beat, end_beat, scale)`` ranges.

    ``scale`` is clamped to ``[_SECTION_VELOCITY_FLOOR, 1.0]``, so applying it
    can only ever take level away and never push a lane past its tuned base.
    Returns ``[]`` for a pattern with no arrangement (unit fixtures, the
    single-pattern route) — `section_velocity_scale_at` then answers 1.0 and
    every caller behaves exactly as it did before this existed.
    """
    if not isinstance(arrangement, list):
        return []
    spans: list[tuple[float, float, float]] = []
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        try:
            start = float(section["start_bar"]) * BEATS_PER_BAR
            bars = float(section["bars"])
            energy = float(section.get("energy", 1.0) or 1.0)
        except (KeyError, TypeError, ValueError):
            continue
        if bars <= 0 or energy <= 0:
            continue
        spans.append((start, start + bars * BEATS_PER_BAR, energy))
    if not spans:
        return []
    peak = max(energy for _start, _end, energy in spans)
    if peak <= 0:
        return []
    return sorted(
        (start, end, max(_SECTION_VELOCITY_FLOOR, min(1.0, energy / peak)))
        for start, end, energy in spans
    )


def section_velocity_scale_at(
    scales: list[tuple[float, float, float]], step: float
) -> float:
    """The scale covering ``step``; 1.0 outside every section."""
    for start, end, scale in scales:
        if start <= step < end:
            return scale
    return 1.0


# --- Section melody arrangement: ONE owner for "how much of the melody does
#     this section play" ----------------------------------------------------
#
# Measured on the shipped batch 2026-08-26, before this existed: the base
# melody of hook, verse and drop is the SAME block, note for note. Graveyard's
# hook and verse#1 are byte-identical; Shuttle's hook and drop are; Sahara,
# Tragic and deyflo play verse#1 and verse#2 identically. BatonRouge carries
# 6.5 melody notes per bar in the intro, the hook, the verse, the drop AND the
# outro — one number for the whole song. `_tile` reproduces the core verbatim
# and `_develop_section` gives `melody_lead` exactly one treatment (the hook's
# `_octave_lift`); `melody_lead` is not even in `_ENERGY_KEYS`.
#
# The one thing that differentiates a section today does so BY ACCIDENT:
# `_extend_midi_octave_layer`'s hardcoded bars 12..59 happen to end mid-song,
# so a late verse gets less doubling than an early one. That is
# the project issue log (not included) §0ab.6, and it is NOT fixed here — see the note below.
#
# Product decision 2026-07 stands untouched: an explicit upload is finished
# material. So this plan can only ever SUBTRACT — never a new note, never an
# altered pitch, never a moved one. Two independent axes, because measuring a
# single flat budget showed the verse paying its whole allowance out of the
# octave layer on six of eight files, which leaves the base RHYTHM still the
# hook's, note for note:
#
#   layer_keep  what fraction of this section's octave doubling survives
#               — the register/colour axis
#   base_cut    what fraction of this section's own melody notes are removed
#               — the space axis, and the only one that changes the rhythm
#
# ⚠️ Why the 12..59 window is not fixed in the same change. The layer lives in
# `melody_lead`, so it is written BEFORE `humanize_pattern` and every melody
# reader sees it. Measured directly (PLG_Beats_MelodyArr/probe_layer_
# visibility.py, five files, pinned seed): deleting the layer outright moves
# kick on deyflo 99->96, Graveyard 85->82 and Tragic 97->94, moves 808 and clap
# on three files and hats on all five. Widening the window is the same class of
# perturbation. Changing it therefore moves the low end, which this round is
# explicitly not allowed to do — a reader that only SUBTRACTS late (after the
# kick/808 seal) is safe, an early writer is not.
_MELODY_SECTION_PLAN: dict[str, tuple[float, float]] = {
    # name: (layer_keep, base_cut)
    "intro": (0.0, 0.30),
    "hook": (1.0, 0.00),     # the statement — the song's face, never trimmed
    "chorus": (1.0, 0.12),
    "drop": (1.0, 0.12),     # "the hook again": full doubling, a thinner body
    "verse": (0.0, 0.25),
    "outro": (0.0, 0.40),
}
_MELODY_DEFAULT_PLAN = (0.5, 0.25)
# Each later occurrence of a repeatable section leans further back. Real forms
# reach verse#3 and drop#2 (swept across 60-220 BPM through the live sizing
# helpers), and without this the extend cycle's repeats are copies of each
# other — the defect this whole round exists to remove, reintroduced one level
# down.
_MELODY_REPEAT_STEP = 0.15
_MELODY_MAX_CUT = 0.50


def section_melody_plan(arrangement: Any) -> list[tuple[float, float, dict[str, Any]]]:
    """``plg_arrangement`` as sorted ``(start_beat, end_beat, plan)`` ranges.

    ``plan`` carries ``layer_keep`` (0..1), ``base_cut`` (0..`_MELODY_MAX_CUT`)
    and ``phase``. Returns ``[]`` for a pattern with no arrangement (unit
    fixtures, the single-pattern route); `section_melody_plan_at` then answers
    "keep everything" and every caller behaves exactly as it did before this
    existed.

    ``phase`` is what stops two same-sized sections from being the same edit.
    drop#1 and drop#2 both give up 12 %, but a different 12 %, so the pair
    reads as two passes at the same idea instead of one clip pasted twice.
    The final chorus never escalates: it is the song's peak, and thinning the
    peak because it happens to be a repeat is backwards.
    """
    if not isinstance(arrangement, list):
        return []
    spans: list[tuple[float, float, dict[str, Any]]] = []
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        try:
            start = float(section["start_bar"]) * BEATS_PER_BAR
            bars = float(section["bars"])
        except (KeyError, TypeError, ValueError):
            continue
        if bars <= 0:
            continue
        name = str(section.get("name") or "")
        occurrence = max(1, int(section.get("occurrence", 1) or 1))
        layer_keep, base_cut = _MELODY_SECTION_PLAN.get(name, _MELODY_DEFAULT_PLAN)
        if base_cut > 0 and not section.get("final_chorus"):
            base_cut = min(_MELODY_MAX_CUT,
                           base_cut + _MELODY_REPEAT_STEP * (occurrence - 1))
        spans.append((start, start + bars * BEATS_PER_BAR, {
            "section": name,
            "occurrence": occurrence,
            "layer_keep": max(0.0, min(1.0, float(layer_keep))),
            "base_cut": max(0.0, min(_MELODY_MAX_CUT, float(base_cut))),
            "phase": occurrence - 1,
        }))
    return sorted(spans, key=lambda span: span[0])


_MELODY_PLAN_KEEP_ALL: dict[str, Any] = {
    "section": "", "occurrence": 1, "layer_keep": 1.0, "base_cut": 0.0, "phase": 0,
}


def section_melody_plan_at(
    plans: list[tuple[float, float, dict[str, Any]]], step: float
) -> dict[str, Any]:
    """The plan covering ``step``; "keep everything" outside every section."""
    for start, end, plan in plans:
        if start <= step < end:
            return plan
    return _MELODY_PLAN_KEEP_ALL


def _section_style(name: str) -> SectionStyle:
    return _STYLES.get(name, _DEFAULT_STYLE)


def _section_style_for_pattern(name: str, pattern: dict[str, Any]) -> SectionStyle:
    """Section style amplified by melody energy card (verse quieter, chorus hotter)."""
    base = _section_style(name)
    card = pattern.get("plg_melody_energy")
    if not isinstance(card, dict):
        return base
    try:
        from melody_beat_symbiosis import melody_modulated_section_style

        energy, hat_density, kick_drive = melody_modulated_section_style(
            name,
            base.energy,
            base.hat_density,
            base.kick_drive,
            card,
        )
    except Exception:  # noqa: BLE001 — arrangement must never fail on symbiosis
        return base
    return SectionStyle(
        energy=energy,
        hat_feel=base.hat_feel,
        hat_density=hat_density,
        kick_drive=kick_drive,
    )


def _track_core_beats(notes: list[dict[str, Any]]) -> float:
    maxend = 0.0
    for entry in notes:
        maxend = max(maxend, float(entry.get("time_step", 0)) + float(entry.get("length", 0.25)))
    bars = max(1, math.ceil(maxend / BEATS_PER_BAR)) if maxend > 0 else 1
    return bars * BEATS_PER_BAR


def _tile(
    core_notes: list[dict[str, Any]],
    start_beat: float,
    length_beats: float,
    core_beats: float,
    section: str,
    *,
    core_offset: float = 0.0,
) -> list[dict[str, Any]]:
    """Read ``length_beats`` worth of notes out of the core, repeated forever
    (as if the core played on an infinite loop), starting at ``core_offset``
    beats into that infinite loop rather than always at 0.

    ``core_offset=0.0`` (every call site until the long-core cursor below)
    reproduces the old behavior exactly: a plain tile-from-the-top. Callers
    that pass a running, per-track ``core_offset`` (advanced by
    ``length_beats`` after each section) get a sequential slice of the core
    each time instead of the same leading window repeated — this is how a
    core longer than a single section (a real uploaded/transcribed take, not
    the LLM's short repeating motif) gets its middle and tail actually heard
    instead of only ever exposing bars [0, length_beats)."""
    out: list[dict[str, Any]] = []
    window_start = core_offset
    window_end = core_offset + length_beats
    first_lap = int(window_start // core_beats)
    last_lap = int((window_end - 1e-9) // core_beats)
    for lap in range(first_lap, last_lap + 1):
        lap_base = lap * core_beats
        for entry in core_notes:
            step = float(entry.get("time_step", 0))
            if step >= core_beats:
                continue
            abs_pos = lap_base + step
            if abs_pos < window_start - 1e-6 or abs_pos >= window_end - 1e-6:
                continue
            note = deepcopy(entry)
            note["time_step"] = round(start_beat + (abs_pos - window_start), 5)
            note["section"] = section  # downstream dynamics key off this
            out.append(note)
    return out


def _uses_full_core_material(
    pattern: dict[str, Any], core_beats: float, length_beats: float
) -> bool:
    """Long real material (uploaded MIDI / reference-reverse transcription,
    flagged by ``plg_midi_melody`` — see reference_reverse.py /
    midi_ingest.py) gets sequentially sliced across sections instead of
    tiled-from-zero every time, so its middle isn't structurally unreachable.
    The LLM's short composed motif (no flag, or a core that already fits the
    section) keeps the exact existing tile/repeat + melody_transform
    behavior — this only changes behavior for a core that is both real AND
    longer than the section it's being poured into."""
    return bool(pattern.get("plg_midi_melody")) and core_beats > length_beats + 1e-6


def _anchor_melody_start(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull the core melody hook to beat 0 so it never starts late in the loop."""
    if not notes:
        return notes
    min_step = min(float(n.get("time_step", 0)) for n in notes)
    if min_step <= 0.12:
        return notes
    return [
        {**deepcopy(n), "time_step": round(float(n.get("time_step", 0)) - min_step, 5)}
        for n in notes
    ]


# --- Per-section development -------------------------------------------------


def _scale_velocity(notes: list[dict[str, Any]], factor: float) -> list[dict[str, Any]]:
    if factor == 1.0:
        return notes
    for note in notes:
        vel = int(note.get("velocity", 100))
        note["velocity"] = max(1, min(127, int(round(vel * factor))))
    return notes


def _hat_note_name(notes: list[dict[str, Any]]) -> str:
    for note in notes:
        name = note.get("note")
        if name:
            return str(name)
    return "C5"


def _steady_hats(
    notes: list[dict[str, Any]],
    start_beat: float,
    length_beats: float,
    section: str,
    *,
    offbeat: bool = False,
) -> list[dict[str, Any]]:
    """Rebuild the section's hats as steady 1/8s — the 'verse = steady hats only'
    feel. The humanizer still adds swing + Markov dynamics on top, but no rolls
    (verse snares are excluded from the roll engine), so the verse stays open.

    ``offbeat=True`` (2nd verse beat-switch): open-feel hats on the off-beats
    only, so the repeat of the verse reads as a different beat, not a copy."""
    name = _hat_note_name(notes)
    out: list[dict[str, Any]] = []
    end = start_beat + length_beats
    t = start_beat + (0.5 if offbeat else 0.0)
    stride = 1.0 if offbeat else 0.5
    i = 0
    while t < end - 1e-6:
        out.append({
            "time_step": round(t, 5),
            "note": name,
            "length": 0.2,
            # gentle 2-step accent so even the steady part is not dead-flat
            "velocity": 96 + (6 if i % 2 == 0 else -4),
            "section": section,
            "steady_hat": True,
        })
        t += stride
        i += 1
    return out


# Verse hat archetype per genre: straight sections read as the genre's pocket,
# not trap 1/8s everywhere. Chorus/hook keep the tiled + densified machinery.
_HAT_ARCHETYPES: dict[str, str] = {
    "house": "offbeat_open", "techno": "offbeat_open", "trance": "offbeat_open",
    "edm": "offbeat_open", "disco_pop": "offbeat_open", "garage": "offbeat_open",
    "hardstyle": "offbeat_open",
    "afrobeats": "shaker16", "amapiano": "shaker16", "reggaeton": "shaker16",
    "dancehall": "shaker16", "funk": "shaker16", "west_coast": "shaker16",
}

# Jay Cactus: the ride/shaker plays "only in some passes", not every straight
# section — unlike the other shaker16 genres above (unconditional every pass).
# Odd occurrences (1st, 3rd verse...) get the shaker; even ones fall back to
# steady8 so the pattern visibly varies pass to pass.
_SHAKER_SELECTIVE_GENRES = frozenset({"west_coast"})


def _shaker_hats(
    notes: list[dict[str, Any]],
    start_beat: float,
    length_beats: float,
    section: str,
) -> list[dict[str, Any]]:
    """1/16 shaker-feel grid with a rolling velocity wave (afro/latin pocket)."""
    name = _hat_note_name(notes)
    out: list[dict[str, Any]] = []
    end = start_beat + length_beats
    t = start_beat
    i = 0
    wave = (58, 72, 64, 80)
    while t < end - 1e-6:
        out.append({
            "time_step": round(t, 5),
            "note": name,
            "length": 0.12,
            "velocity": wave[i % 4],
            "section": section,
            "steady_hat": True,
            "shaker": True,
        })
        t += 0.25
        i += 1
    return out


def _densify_hats(notes: list[dict[str, Any]], density: float) -> list[dict[str, Any]]:
    """Add ghost hats between existing hits in busy sections (chorus) so the drop
    rolls feel fuller. Never splits an existing roll or a long rest."""
    ordered = sorted((deepcopy(n) for n in notes), key=lambda n: float(n.get("time_step", 0)))
    if density <= 1.0:
        return ordered
    extra: list[dict[str, Any]] = []
    add_ratio = density - 1.0
    carry = 0.0
    for first, second in zip(ordered, ordered[1:]):
        if first.get("hat_roll") or second.get("hat_roll"):
            continue
        ta = float(first.get("time_step", 0))
        tb = float(second.get("time_step", 0))
        gap = tb - ta
        if gap <= 0.18 or gap > 1.0:  # skip tight rolls and long rests
            continue
        carry += add_ratio
        if carry < 1.0:
            continue
        carry -= 1.0
        extra.append({
            "time_step": round(ta + gap / 2.0, 5),
            "note": first.get("note", "C5"),
            "length": round(min(0.2, gap / 2.0), 5),
            "velocity": max(1, int(int(first.get("velocity", 96)) * 0.7)),
            "section": first.get("section"),
            "hat_fill": True,
        })
    return sorted(ordered + extra, key=lambda n: float(n.get("time_step", 0)))


def _drive_kicks(
    notes: list[dict[str, Any]],
    style: SectionStyle,
    start_beat: float,
    length_beats: float,
    section: str,
) -> list[dict[str, Any]]:
    """Push extra kicks on weak beats so the chorus drives harder than the verse.
    Deterministic, clear of the clap and the pre-drop gap; the humanizer's
    drop_tension still wipes the pre-drop window afterward."""
    out = list(notes)
    existing = {round(float(n.get("time_step", 0)), 3) for n in notes}
    bars = max(1, int(round(length_beats / BEATS_PER_BAR)))
    stride = 1 if style.kick_drive >= 0.5 else 2
    for bar in range(bars):
        if bar % stride != 0:
            continue
        base = start_beat + bar * BEATS_PER_BAR
        for off in _DRIVE_OFFSETS:
            t = round(base + off, 5)
            if any(abs(t - e) < 0.2 for e in existing):
                continue
            out.append({
                "time_step": t,
                "note": "C1",
                "length": 0.4,
                "velocity": 96,
                "section": section,
                "section_drive": True,
            })
            existing.add(t)
    return sorted(out, key=lambda n: float(n.get("time_step", 0)))


def _octave_lift(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Final-chorus lift: double melody notes an octave up (quiet layer).

    Only notes that stay inside the melody register get a double, so
    normalize_registers never folds the layer back into unison."""
    from pattern_utils import parse_note_name

    doubles: list[dict[str, Any]] = []
    for entry in notes:
        try:
            midi = parse_note_name(str(entry.get("note", "")))
        except (ValueError, TypeError):
            continue
        if midi > 71:  # +12 would leave the C5..B6 melody register
            continue
        double = deepcopy(entry)
        double["note"] = f"{str(entry['note'])[:-1]}{int(str(entry['note'])[-1]) + 1}"
        double["velocity"] = max(1, int(entry.get("velocity", 96)) - 20)
        double["switch_octave"] = True
        doubles.append(double)
    return sorted(notes + doubles, key=lambda n: float(n.get("time_step", 0)))


def _extend_midi_octave_layer(
    notes: list[dict[str, Any]], *, total_bars: int
) -> list[dict[str, Any]]:
    """Keep the proven octave colour through the middle of a MIDI song.

    Playlist bars are one-based to the producer: bar 13 through bar 59 means
    the zero-based beat window [48, 236).  It deliberately stays out of the
    intro and outro, so the arrangement still has somewhere to grow and end.
    The layer doubles only eligible existing melody attacks; it never writes a
    new motif or changes the supplied MIDI rhythm.
    """
    if not notes:
        return notes
    from pattern_utils import parse_note_name

    start = 12 * BEATS_PER_BAR
    end = min(float(total_bars) * BEATS_PER_BAR, 59 * BEATS_PER_BAR)
    if end <= start:
        return notes
    existing = {
        (round(float(note.get("time_step", 0.0)), 4), str(note.get("note") or ""))
        for note in notes if isinstance(note, dict)
    }
    # At most one top note per onset: chord MIDI gets a clean, readable upper
    # voice rather than every chord member doubled into a thick wall.
    onsets: dict[float, dict[str, Any]] = {}
    for note in notes:
        if not isinstance(note, dict) or note.get("switch_octave"):
            continue
        step = float(note.get("time_step", 0.0) or 0.0)
        if not start <= step < end:
            continue
        try:
            midi = parse_note_name(str(note.get("note") or ""))
        except (TypeError, ValueError):
            continue
        if midi > 71:
            continue
        previous = onsets.get(round(step, 5))
        if previous is None:
            onsets[round(step, 5)] = note
            continue
        try:
            if parse_note_name(str(note.get("note"))) > parse_note_name(str(previous.get("note"))):
                onsets[round(step, 5)] = note
        except (TypeError, ValueError):
            pass
    added: list[dict[str, Any]] = []
    for step, source in onsets.items():
        midi = parse_note_name(str(source["note"]))
        octave_note = deepcopy(source)
        octave_note["note"] = f"{str(source['note'])[:-1]}{int(str(source['note'])[-1]) + 1}"
        octave_note["velocity"] = max(1, int(source.get("velocity", 96) or 96) - 22)
        octave_note["switch_octave"] = True
        octave_note["midi_sustained_octave_layer"] = True
        key = (round(step, 4), str(octave_note["note"]))
        if key not in existing:
            added.append(octave_note)
            existing.add(key)
    return sorted(notes + added, key=lambda note: float(note.get("time_step", 0.0) or 0.0))


def _simplify_808_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mike Dean: the intro/outro bass is simple, the drop is spicier — keep
    only the first 808 hit per bar, dropping any syncopated/walking extras
    that section already had tiled in. Removal-only (never adds notes), same
    safety rule as every other section postprocess helper in this file. Chorus
    /hook/verse are untouched, so they read as "spicier" purely by contrast."""
    if not notes:
        return notes
    ordered = sorted(notes, key=lambda n: float(n.get("time_step", 0)))
    kept: list[dict[str, Any]] = []
    last_bar: int | None = None
    for note in ordered:
        bar = int(float(note.get("time_step", 0)) // BEATS_PER_BAR)
        if bar == last_bar:
            continue
        kept.append(note)
        last_bar = bar
    return kept


def _develop_section(
    key: str,
    notes: list[dict[str, Any]],
    style: SectionStyle,
    start_beat: float,
    length_beats: float,
    section: str,
    *,
    occurrence: int = 1,
    is_final_chorus: bool = False,
    genre: str | None = None,
) -> list[dict[str, Any]]:
    """Apply a section's character to one track's tiled notes.

    ``occurrence`` makes repeats evolve (beat switch): the 2nd verse rides
    off-beat hats instead of straight 1/8s; the final chorus gets denser hats
    and an octave-up melody layer so the song peaks instead of looping.
    ``genre`` picks the straight-section hat archetype (off-beat open hats for
    four-on-floor lanes, 1/16 shaker feel for afro/latin lanes)."""
    if not notes:
        return notes
    switch = _beat_switch_enabled()
    if key == "hi_hats":
        if style.hat_feel == "straight":
            archetype = _HAT_ARCHETYPES.get(genre or "", "steady8")
            use_shaker = archetype == "shaker16" and (
                genre not in _SHAKER_SELECTIVE_GENRES or occurrence % 2 == 1
            )
            if use_shaker:
                notes = _shaker_hats(notes, start_beat, length_beats, section)
            else:
                notes = _steady_hats(
                    notes, start_beat, length_beats, section,
                    offbeat=archetype == "offbeat_open"
                    or (
                        switch
                        and occurrence >= 2
                        and style.hat_density < 1.0
                    ),
                )
        else:
            density = style.hat_density + (0.15 if switch and is_final_chorus else 0.0)
            notes = _densify_hats(notes, density)
    elif key == "kick" and style.kick_drive > 0:
        notes = _drive_kicks(notes, style, start_beat, length_beats, section)
    elif key == "melody_lead" and switch and section in ("hook", "chorus") and occurrence == 1:
        notes = _octave_lift(notes)
    elif key == "sub_808" and section in ("intro", "outro", "prehook"):
        notes = _simplify_808_notes(notes)
    if key in _ENERGY_KEYS and style.energy != 1.0:
        energy = min(1.08, style.energy + (0.05 if switch and is_final_chorus else 0.0))
        notes = _scale_velocity(notes, energy)
    return notes


# Which landing gets which transition. The two peak rows are exactly what this
# list has always emitted and are deliberately untouched; the third row is new
# (2026-08-26) and is the whole of "something changes every 8-16 bars".
#
# Before it, a landing that was not a hook or a drop produced NO event at all,
# so a verse following a verse — the commonest boundary in an extended form —
# was inaudible. Measured on the shipped corpus: 4-5 marked events in a 60-80
# bar song, with a 23-28 bar hole between bar 28 and bar 51.
#
# `kick_breath_fill` is not a new mechanism: it is the kind the hook has always
# landed on (one bar without kick/808, then a snare+hat pickup). Reusing it
# means `beat_humanize.apply_midi_arrangement_transitions` AND
# `_reapply_midi_kick_808_clears` already know how to hold the window open
# after the kick/808 pocket rebuilds over it. A new kind would have had to be
# taught to both, and a kind only the first one knows is a window that silently
# grows back.
_MIDI_TRANSITION_KINDS: dict[str, str] = {
    "hook": "kick_breath_fill",
    "chorus": "half_bar_stop",
    "drop": "half_bar_stop",
    "verse": "kick_breath_fill",
    "outro": "kick_breath_fill",
}


# A section at least this long gets one extra breath at its midpoint. The
# five-part form's blocks are 16 bars, so its landings alone would leave a
# 16-bar gap -- the very edge of the requirement, i.e. a 16-bar hook in which
# the only thing that ever happens is its own entrance. Measured: this halves
# the worst gap on every corpus file.
_MIDI_MIDPOINT_MIN_BARS = 16


def _midi_transitions_for(
    arrangement: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One transition per section landing, plus a midpoint breath in long
    sections. Sorted by bar, because both appliers walk the list in order."""
    plans: list[dict[str, Any]] = []
    for section in arrangement:
        name = str(section.get("name", ""))
        try:
            landing = int(section["start_bar"])
            bars = int(section["bars"])
        except (KeyError, TypeError, ValueError):
            continue
        kind = _MIDI_TRANSITION_KINDS.get(name)
        if kind is None:
            continue
        if landing > 0:
            plans.append({"target": name, "landing_bar": landing, "kind": kind})
        if bars >= _MIDI_MIDPOINT_MIN_BARS and name != "outro":
            # Always the breath, never the half-bar stop: a full rhythm-section
            # stop mid-section reads as a landing that never arrives.
            plans.append({
                "target": "%s_mid" % name,
                "landing_bar": landing + bars // 2,
                "kind": "kick_breath_fill",
            })
    return sorted(plans, key=lambda plan: plan["landing_bar"])


def arrange_song(
    pattern: dict[str, Any],
    *,
    sections: tuple[tuple[str, int, tuple[str, ...]], ...] | None = None,
) -> dict[str, Any]:
    """Develop the core loop into a full multi-section song (in place)."""
    tracks = pattern.get("tracks")
    if not isinstance(tracks, dict):
        return pattern

    core = {key: [deepcopy(n) for n in track_notes(pattern, key)] for key in TRACK_KEYS}
    if core.get("melody_lead"):
        core["melody_lead"] = _anchor_melody_start(core["melody_lead"])
    if not any(core.values()):
        return pattern

    core_beats = {key: _track_core_beats(notes) for key, notes in core.items() if notes}

    melody_root_pc: int | None = None
    melody_scale = "natural_minor"
    # Product decision (2026-07): an explicit MIDI/reference-audio upload is
    # finished material — never run it through sequence/retrograde/fragment
    # (melody_transform.py) regardless of PLG_MELODY_SECTION_VARIATION. Set
    # unconditionally by midi_ingest.ingest_uploaded_midi and reference_
    # reverse.ingest_reference_from_file (both explicit, user-pointed-at-
    # one-file paths — never the corpus/library AUTO-match tiers, which keep
    # their own variation). melody_root_pc staying None here is enough: the
    # per-section branch below falls through to plain _tile (short core) or
    # the cursor-tiling path (long core) either way, both already verbatim.
    if (
        core.get("melody_lead")
        and _melody_section_variation_enabled()
        and not pattern.get("plg_preserve_melody_sections")
    ):
        from music_theory import detect_root_pc

        melody_root_pc = detect_root_pc(core["melody_lead"])
        melody_scale = str(pattern.get("scale") or "natural_minor")

    sections_were_explicit = sections is not None
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    genre = variety.get("genre") if isinstance(variety.get("genre"), str) else None
    if sections is None:
        if pattern.get("plg_midi_melody"):
            sections = midi_sections_for_target(_target_song_bars(pattern))
        else:
            sections = _GENRE_SECTIONS.get(genre or "", DEFAULT_SECTIONS)
        # Cold-open vs buildup: the choice itself is rolled once per generation
        # in generation_variety.py::build_context() (off the same seeded RNG as
        # every other variety pick, weighted by GenreProfile.hook_buildup_chance)
        # and handed down as plg_variety["hook_entry"] — arrange_song only acts
        # on it, it never rolls its own dice, so calls without that key (every
        # existing test, any caller that hasn't opted in) keep today's exact
        # cold-open behavior. Genres with their own dedicated section template
        # (west_coast) are untouched — that override already wins above.
        if (
            not pattern.get("plg_midi_melody")
            and
            sections is DEFAULT_SECTIONS
            and _hook_buildup_enabled()
            and variety.get("hook_entry") == "buildup"
        ):
            sections = BUILDUP_SECTIONS

    # Every generated form targets ~120 seconds instead of inheriting a fixed
    # bar count. Explicit caller-supplied sections remain exact (useful for
    # tests and deliberate custom forms); the product defaults scale by BPM.
    if not sections_were_explicit and not pattern.get("plg_explicit_arrangement"):
        target_bars = _target_song_bars(pattern)
        sections = _extend_sections_to_duration(sections, target_bars)
        # Growing a form was never enough: at a slow tempo the target is FEWER
        # bars than the base form already holds, and without this the session
        # simply ran long (measured: 141 s at 68 BPM against a 120 s target).
        sections = _shrink_sections_to_duration(sections, target_bars)

    new_tracks: dict[str, list[dict[str, Any]]] = {key: [] for key in TRACK_KEYS}
    bar_cursor = 0
    arrangement: list[dict[str, Any]] = []
    # Per-track running position inside a long real core (see
    # _uses_full_core_material) — advanced by each section's length so the
    # NEXT section picks up where this one left off instead of restarting
    # from bar 0. Sections whose own length already covers the whole core
    # don't need to advance it (nothing was left unheard).
    core_cursor: dict[str, float] = {}

    # Beat switch bookkeeping: which occurrence of each section this is, and
    # where the FINAL chorus starts (it gets the peak treatment + a 1-bar
    # drum cut right before it, applied late in humanize).
    chorus_names = ("chorus", "drop")
    final_chorus_idx = max(
        (i for i, (name, _, _) in enumerate(sections) if name in chorus_names),
        default=-1,
    )
    occurrences: dict[str, int] = {}

    for index, (name, bars, gated) in enumerate(sections):
        occurrences[name] = occurrences.get(name, 0) + 1
        is_final_chorus = index == final_chorus_idx
        style = _section_style_for_pattern(name, pattern)
        start_beat = bar_cursor * BEATS_PER_BAR
        length_beats = bars * BEATS_PER_BAR
        for key in gated:
            if not core.get(key):
                continue
            cb = core_beats[key]
            long_real_core = _uses_full_core_material(pattern, cb, length_beats)
            if key == "melody_lead" and melody_root_pc is not None and not long_real_core:
                from melody_transform import melody_variant_for_section

                variant_notes, variant_core_beats = melody_variant_for_section(
                    core[key], cb,
                    section=name, occurrence=occurrences[name],
                    is_final_chorus=is_final_chorus,
                    root_pc=melody_root_pc, scale=melody_scale,
                )
                tiled = _tile(variant_notes, start_beat, length_beats, variant_core_beats, name)
            elif long_real_core:
                offset = core_cursor.get(key, 0.0)
                tiled = _tile(core[key], start_beat, length_beats, cb, name, core_offset=offset)
                core_cursor[key] = offset + length_beats
            else:
                tiled = _tile(core[key], start_beat, length_beats, cb, name)
            tiled = _develop_section(
                key, tiled, style, start_beat, length_beats, name,
                occurrence=occurrences[name],
                is_final_chorus=is_final_chorus,
                genre=genre,
            )
            new_tracks[key].extend(tiled)
        section_entry = {
            "name": name,
            "start_bar": bar_cursor,
            "bars": bars,
            "energy": style.energy,
            "hat_feel": style.hat_feel,
            "rolls": name in ROLL_SECTIONS,
            "occurrence": occurrences[name],
            "final_chorus": is_final_chorus,
        }
        if pattern.get("plg_midi_melody"):
            section_entry["active_tracks"] = list(gated)
        arrangement.append(section_entry)
        if is_final_chorus and bar_cursor > 0 and _beat_switch_enabled():
            # Cut the drums for the bar before the final chorus — the classic
            # "pull everything out, then the beat comes back bigger" moment.
            # Applied at the END of humanize so drum rebuilds can't refill it.
            pattern["plg_beat_switch"] = {
                "cut_bar": bar_cursor - 1,
                "cut_beats": 2.0 if pattern.get("plg_midi_melody") else BEATS_PER_BAR,
            }
        bar_cursor += bars

    if pattern.get("plg_midi_melody") and new_tracks.get("melody_lead"):
        new_tracks["melody_lead"] = _extend_midi_octave_layer(
            new_tracks["melody_lead"], total_bars=bar_cursor
        )
    pattern["tracks"] = {key: notes for key, notes in new_tracks.items() if notes}
    pattern["plg_arrangement"] = arrangement
    pattern["plg_total_bars"] = bar_cursor
    # This is consumed by the final humanize pass.  Four bars is long enough
    # to read as a musical ending, while the preceding outro already removes
    # the dense section layers so it never feels like an abrupt mute.
    outro_entry = next((entry for entry in reversed(arrangement) if entry["name"] == "outro"), None)
    if outro_entry is not None:
        outro_end = (int(outro_entry["start_bar"]) + int(outro_entry["bars"])) * BEATS_PER_BAR
        pattern["plg_outro_fade"] = {
            "enabled": True,
            "start_beat": max(float(outro_entry["start_bar"]) * BEATS_PER_BAR, outro_end - 4 * BEATS_PER_BAR),
            "end_beat": float(outro_end),
            "target_seconds": TARGET_SONG_SECONDS,
        }
    if pattern.get("plg_midi_melody"):
        pattern["plg_midi_transitions"] = _midi_transitions_for(arrangement)

    from flp_template import build_playlist_clips

    build_playlist_clips(pattern)
    return pattern

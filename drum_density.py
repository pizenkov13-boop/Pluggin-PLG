"""MIDI-derived drum density — one card the whole kit reads.

Before this module the kick/808 pocket's density came from three places that
never looked at the uploaded MIDI at all:

* ``midi_phrase_drums._POCKET_SHAPES`` — a fixed 4-5 onsets per 8-beat cell,
  keyed only by the phrase's role;
* ``midi_phrase_drums._enforce_section_pocket`` — a hard floor of one paired
  hit in EVERY active bar;
* ``midi_phrase_drums._fill_time_based_lowend_gaps`` — a fixed 0.75 s maximum
  gap, which at a slow tempo clamps UP to one beat and tops every bar out at
  the two-per-bar cap.

The measured result on a calm 68 BPM piano ballad (12 source notes over 40
bars) was exactly two pairs in every single active bar — including the bars
where the melody is silent.  Those bars have no confident harmonic window
either, so the 808 landed on a root nothing in the material supports and the
final kick/808 gate rejected the whole generation.  No genre could produce a
beat from that file.

This card computes the density the *material* asks for, from the melody
energy signals ``melody_beat_symbiosis.build_melody_energy_card`` already
measures.  ``GenreProfile`` stays in the picture, but only as a bounded
stylistic multiplier on top of a correct MIDI-derived base — never as the
source of truth.

The card is a pure reader: it stamps ``plg_drum_density`` and mutates nothing
else.  Every consumer (generator, repair pass and export audit alike) must
read its floors from here rather than re-deriving them, the same discipline
``beat_humanize._lowend_harmonic_windows`` already applies to chord windows.

Deliberately absent: a snare/clap multiplier.  Measured across the control
set, the backbeat already tracks the material without help — the calm ballad
lands at 0.75-1.52 snare and 0.72-1.15 clap per bar against 1.00-1.01 and
1.01-1.51 on the busy files — and it is the one lane that has to stay steady
to be a backbeat.  There is nothing there for a density rule to improve.
"""
from __future__ import annotations

import statistics
from typing import Any

from pattern_utils import track_notes

BEATS_PER_BAR = 4.0

# What a hip-hop low end stays inside whatever the material does. The ceiling
# is the two-per-bar cap the pocket already enforced; the floor keeps a beat a
# beat (roughly one pair every two bars) instead of letting a near-empty MIDI
# argue the drums away entirely.
MIN_PAIRS_PER_BAR = 0.55
MAX_PAIRS_PER_BAR = 2.0

# Melody onset clusters per bar at which the material is considered fully
# busy. The 2026-08-15 audit measured the whole shipping corpus (ten real
# marketplace MIDIs) at 4.0-9.5 onsets/bar, all of it sitting on the old 3.0
# knee — the card could not tell a 4-onset file from a 9.5-onset one and the
# kit came out identical. 10.0 stretches the scale across the real range:
# quiet loops now read noticeably below full drive, the densest shipping
# material still reaches it.
FULL_DRIVE_ONSET_RATE = 10.0

# The 4-bar window ceiling the export audit has always enforced. Density may
# relax the FLOOR for sparse material; it may not raise this.
MAX_PAIRS_PER_4BAR = 8

# The sections a song is supposed to peak in. Named here, once, because two
# different passes used to key off their own copy of this set.
PEAK_SECTIONS = frozenset({"hook", "chorus", "drop"})


#: Bounds of the genre multiplier, shared by the profile reading and any caller-supplied value.
GENRE_MULTIPLIER_MIN = 0.75
GENRE_MULTIPLIER_MAX = 1.25


def _profile_multiplier(profile: Any) -> float:
    """Genre as a stylistic nudge, never the density itself.

    ``drive`` is the existing 0..1 aggression hint on ``GenreProfile``. Across
    the ten tuned lanes this spans roughly 0.94 (pluggnb) to 1.14 (rage) — a
    seasoning on an already-correct MIDI-derived base, which is the whole
    point of the change.
    """
    drive = getattr(profile, "drive", None)
    if not isinstance(drive, (int, float)):
        return 1.0
    return max(GENRE_MULTIPLIER_MIN, min(GENRE_MULTIPLIER_MAX, 0.85 + 0.30 * float(drive)))


def _bar_span(pattern: dict[str, Any]) -> int:
    bars = 0
    arrangement = pattern.get("plg_arrangement")
    if isinstance(arrangement, list):
        for section in arrangement:
            if isinstance(section, dict):
                bars = max(
                    bars,
                    int(section.get("start_bar", 0) or 0) + int(section.get("bars", 0) or 0),
                )
    if bars:
        return bars
    last = max(
        (float(n.get("time_step", 0.0) or 0.0) for n in track_notes(pattern, "melody_lead")),
        default=0.0,
    )
    return int(last // BEATS_PER_BAR) + 1


def melody_onsets_per_bar(
    pattern: dict[str, Any], bars: int, *, from_beat_in_bar: float = 0.0
) -> list[int]:
    """Onset CLUSTERS per bar of the final, arranged melody.

    Clustered at 0.08 beats so a chord counts as one musical event, matching
    ``melody_character._chord_onset_rate``'s reading — a four-note chord is one
    thing the drums answer, not four.

    ``from_beat_in_bar`` counts only the part of the bar at or after that
    beat. The clustering runs over the WHOLE melody either way, so a chord
    straddling the boundary still counts once. Added 2026-08-21 for the
    backbeat palette: the whole-bar count is flat on most real material
    (Graveyard and BatonRouge sit at exactly 6 onsets in every bar, Tragic at
    4), so a median split on it is always false and the lever it feeds is
    dead. The second half of the bar is where the backbeat itself lands, and
    it measures 3-8 distinct values per file on the same corpus.
    """
    counts = [0] * max(1, bars)
    steps = sorted(
        float(n.get("time_step", 0.0) or 0.0) for n in track_notes(pattern, "melody_lead")
    )
    previous: float | None = None
    for step in steps:
        if previous is not None and abs(step - previous) <= 0.08:
            continue
        previous = step
        if from_beat_in_bar and (step % BEATS_PER_BAR) < from_beat_in_bar - 1e-9:
            continue
        bar = int(step // BEATS_PER_BAR)
        if 0 <= bar < len(counts):
            counts[bar] += 1
    return counts


def active_lowend_bars(pattern: dict[str, Any], bars: int) -> list[bool]:
    """Bars the arrangement actually permits the kick+808 pocket in."""
    arrangement = pattern.get("plg_arrangement")
    if not isinstance(arrangement, list):
        return [True] * max(1, bars)
    active = [False] * max(1, bars)
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        tracks = section.get("active_tracks")
        if not isinstance(tracks, list) or "kick" not in tracks:
            continue
        if not ({"808_bass", "sub_808"} & set(tracks)):
            continue
        start = int(section.get("start_bar", 0) or 0)
        for bar in range(start, start + max(0, int(section.get("bars", 0) or 0))):
            if 0 <= bar < len(active):
                active[bar] = True
    return active


# How fast the hat lane gets out of the melody's way as the material thickens.
#
# The hat multiplier used to READ 0.5 + 0.55 * drive — proportional, so the
# busier the melody, the more hats it got. Measured 2026-08-19 across the
# five-file listening corpus, that is backwards: the file the user judged the
# only good-sounding one (Sahara, 8.8 melody onsets/bar) received multiplier
# 0.791 and had 275 hats thinned, while the worst (Shuttle, 22.3 onsets/bar)
# received 0.970 — above thin_generated_hats' own >= 0.95 no-op cliff, so it
# was thinned by exactly ZERO. Final hats/bar landed at 8.9 vs 10.3 across a
# 2.5x range of melody density: the kit did not answer the melody at all, and
# where it did, it answered the wrong way.
#
# The slope is anchored on that one measured good result, deliberately and
# with its limits stated: it is chosen so Sahara's own drive (0.530) maps back
# to 0.788 — the value it already had, within 0.4 % — so the file that works
# does not move, and everything busier than it now thins harder instead of
# softer. ONE positively-verified example is thin calibration; the output
# range stays inside the [0.5, 1.0] band every consumer already handles, and
# nothing here touches `drive` itself, so the kick/808 chain and the KNOWN
# ISSUES 0i.1/0i.2 tempo calibration are byte-identical.
#
# This reverses the direction the project issue log (not included) 0i.2 recorded ("busier melody ->
# busier kit"). That entry's two causes were both TEMPO artifacts and are kept
# intact; the axis inverted here is transient competition, which it never
# measured. See the project issue log (not included) 0n.
HAT_CROWDING_SLOPE = 0.40


def build_drum_density_card(
    pattern: dict[str, Any], *, profile: Any = None, genre_multiplier: float | None = None
) -> dict[str, Any]:
    """Compute and stamp ``plg_drum_density`` from the real MIDI energy.

    ``genre_multiplier`` replaces the profile's own stylistic multiplier
    (``_profile_multiplier``, 0.94-1.14 across the ten lanes) when given, clamped to the
    same 0.75-1.25 bounds; ``None`` (the default) keeps the profile's value exactly.
    """
    energy = pattern.get("plg_melody_energy")
    energy = energy if isinstance(energy, dict) else {}
    onset_rate = float(energy.get("onset_rate", 0.0) or 0.0)
    busyness = float(energy.get("busyness", 0.0) or 0.0)
    pace = str(energy.get("pace") or "pocket")

    bars = _bar_span(pattern)
    per_bar = melody_onsets_per_bar(pattern, bars)
    # The half of the bar the backbeat itself lands in. Second reading, same
    # owner and same clustering — see melody_onsets_per_bar for why the
    # whole-bar count could not carry this.
    per_bar_late = melody_onsets_per_bar(pattern, bars, from_beat_in_bar=BEATS_PER_BAR / 2.0)
    active = active_lowend_bars(pattern, bars)

    # Onset rate is the primary reading — it is literally "how many musical
    # events per bar the drums have to answer". Busyness carries the shorter/
    # syncopated/sharp character the rate alone cannot see.
    onset_drive = max(0.0, min(1.0, onset_rate / FULL_DRIVE_ONSET_RATE))
    drive = max(0.0, min(1.0, 0.65 * onset_drive + 0.35 * busyness))

    base_pairs = MIN_PAIRS_PER_BAR + (MAX_PAIRS_PER_BAR - MIN_PAIRS_PER_BAR) * drive
    if genre_multiplier is None:
        multiplier = _profile_multiplier(profile)
    else:
        multiplier = max(GENRE_MULTIPLIER_MIN, min(GENRE_MULTIPLIER_MAX, float(genre_multiplier)))
    pairs_per_bar = max(MIN_PAIRS_PER_BAR, min(MAX_PAIRS_PER_BAR, base_pairs * multiplier))

    # Average spacing the target implies, with a little room to breathe before
    # the gap filler steps in. The 0.8 factor is calibrated so busy material
    # lands on the same gap the old fixed 0.75 s rule produced at 135-160 BPM
    # — dense MIDI keeps the pocket it already had.
    #
    # 2026-08-15: the gap is a TIME budget, so it is normalized against the
    # session tempo (140 BPM reference, the middle of the trap corpus). The
    # old tempo-blind beat count spanned ~0.6 s at 180 BPM but ~5 s at 68,
    # and the gap filler's "wider wins" rule then let the tempo-blind value
    # override the old 0.75 s rule at slow tempos. Same physical gap at every
    # tempo now.
    try:
        bpm = float(pattern.get("bpm") or 140.0)
    except (TypeError, ValueError):
        bpm = 140.0
    bpm = max(60.0, min(220.0, bpm))
    max_gap_beats = max(
        1.0, min(6.0, BEATS_PER_BAR / pairs_per_bar * 0.8 * bpm / 140.0)
    )

    # Onsets per 8-beat phrase cell, for the pocket shapes. Two is the
    # smallest reading that still states a phrase. The 2.5 factor is
    # calibrated against the existing shapes (4-5 onsets per cell): at full
    # drive it resolves to 5, so busy material keeps every shape it already
    # had and only genuinely sparse material is thinned.
    pocket_onsets_per_phrase = max(2, int(round(pairs_per_bar * 2.5)))

    # Export-gate floor. The ceiling stays exactly where it was: sparse
    # material may relax how EMPTY a window is allowed to be, never how full.
    min_pairs_per_4bar = int(max(2, min(3, round(pairs_per_bar * BEATS_PER_BAR * 0.55))))

    card = {
        "version": 1,
        "onset_rate": round(onset_rate, 4),
        "busyness": round(busyness, 4),
        "pace": pace,
        "bars": bars,
        "melody_onsets_per_bar": per_bar,
        "melody_onsets_late_per_bar": per_bar_late,
        "active_lowend_bars": active,
        "melody_bar_coverage": round(
            sum(1 for i, c in enumerate(per_bar) if c and (i >= len(active) or active[i]))
            / max(1, sum(1 for flag in active if flag)),
            4,
        ),
        "drive": round(drive, 4),
        "genre": getattr(profile, "name", None),
        "genre_multiplier": round(multiplier, 4),
        "base_pairs_per_bar": round(base_pairs, 4),
        "pairs_per_bar": round(pairs_per_bar, 4),
        "max_gap_beats": round(max_gap_beats, 4),
        "pocket_onsets_per_phrase": pocket_onsets_per_phrase,
        "min_pairs_per_4bar": min_pairs_per_4bar,
        "max_pairs_per_4bar": MAX_PAIRS_PER_4BAR,
        # INVERSE in the melody's own busyness — see HAT_CROWDING_SLOPE. Every
        # other field on this card stays proportional to drive; only the hats
        # compete with the melody for the same transient space.
        "hat_multiplier": round(max(0.5, min(1.0, 1.0 - HAT_CROWDING_SLOPE * drive)), 4),
        # A bar the melody says nothing in gets no manufactured root while the
        # material is this sparse; on busy material every active bar is still
        # anchored exactly as before.
        #
        # Deliberately keyed to the MIDI-derived BASE, not the genre-multiplied
        # value: this decides whether the engine invents harmony in a bar the
        # material is silent in, and a genre label must never be able to flip
        # that. (Measured: an 0.90 base reading crossed 1.0 purely because a
        # drive=0.9 profile multiplied it by 1.12, which would have restored
        # the exact behaviour this change exists to remove.)
        "anchor_every_active_bar": base_pairs >= 1.0,
    }
    pattern["plg_drum_density"] = card
    return card


def density_card(pattern: dict[str, Any]) -> dict[str, Any] | None:
    """Read the stamped card, or None when this run never built one."""
    card = pattern.get("plg_drum_density")
    return card if isinstance(card, dict) and card.get("version") else None


# The recorded minimum a roll-thinning pass may leave in a 4-bar window. This
# is a LAW with its own test, not a density decision: hard rule 4 forbids
# fixed per-bar density constants, and this is neither per-bar nor a target —
# it is the floor below which the low end reads as a hole. The material's card
# may raise it; nothing lowers it.
ROLL_THINNING_FLOOR_PER_4BAR = 3


# The transient budget one bar can carry before the kit starts masking the
# melody instead of supporting it.
#
# Counted in this card's own unit: onset CLUSTERS (melody_onsets_per_bar,
# 0.08-beat clustering, so a chord is one event the drums answer). Measured
# 2026-08-19 on the five-file listening corpus, the only file the user judged
# good (Sahara) carries 18.8 events per bar all lanes together: melody 4.3 +
# hats 8.9 + kick 1.7 + backbeat 3.9. The four judged bad run 20.4 / 21.1 /
# 23.1 / 25.0, and every one of them is over budget on the MELODY side alone
# (5.8-9.0 clusters/bar against Sahara's 4.3) while the kit answered with the
# SAME 7-10 hats/bar regardless.
#
# The hat lane is the part that gives way, because it competes with the melody
# for the same transient register. The kick/808 sits in its own band and the
# backbeat must stay steady to be a backbeat, so both are excluded from the
# budget's give — they are subtracted as a fixed reservation instead.
#
# ONE positively-verified reference is thin calibration, and this is the knob
# to turn when there are more: HAT_BUDGET_FLOOR_PER_BAR deliberately stops the
# lane well above the 2 hats/bar the raw arithmetic asks for on the densest
# material, because a hat lane that thin has never been heard on this corpus.
TRANSIENT_BUDGET_PER_BAR = 18.8
LOWEND_BACKBEAT_RESERVATION = 5.6   # kick 1.7 + snare/clap/layer 3.9, measured
HAT_BUDGET_FLOOR_PER_BAR = 4        # never thinner than this; the raw arithmetic asks for less
HAT_BUDGET_CEILING_PER_BAR = 9      # above the 1/8 skeleton's own 8, deliberately:
                                    # material that leaves this much room is not
                                    # constrained by the budget at all. Sahara's
                                    # own share computes to 8.9 and clamping it to
                                    # 8 cost the one good-sounding file 15 % of its
                                    # hats — the control must not move.


def hat_floor_per_bar(card: dict[str, Any] | None) -> int:
    """How many hats a bar may carry before the kit starts masking the melody.

    ``enforce_midi_section_density`` used to hard-code this as 8 in two
    places at once — a rebuild that FILLS every empty 1/8 slot (a floor) and a
    verse cap that trims back to 8 (a ceiling) — both gated only on
    ``pace == "racing"``, which measured true on all five corpus files
    including the calmest. Floor and ceiling being the same constant is why
    the final hat lane sat at 8-9 per bar no matter what the melody did, and
    why thinning earlier in the chain was simply refilled: measured after the
    hat multiplier was inverted, the rebuild's own contribution rose from +43
    to +99 (Graveyard) and +58 to +168 (Detroit), cancelling it.

    Returns the ceiling of the 1/8 grid for material that leaves room, and
    less for material that does not.
    """
    if not isinstance(card, dict):
        return HAT_BUDGET_CEILING_PER_BAR
    per_bar = melody_attacks_per_bar(card)
    if per_bar <= 0:
        return HAT_BUDGET_CEILING_PER_BAR
    budget = TRANSIENT_BUDGET_PER_BAR - per_bar - LOWEND_BACKBEAT_RESERVATION
    return int(max(HAT_BUDGET_FLOOR_PER_BAR, min(HAT_BUDGET_CEILING_PER_BAR, round(budget))))


def melody_attacks_per_bar(card: dict[str, Any] | None) -> float:
    """The melody's own attack density, straight off the card's per-bar list."""
    if not isinstance(card, dict):
        return 0.0
    per = card.get("melody_onsets_per_bar")
    if not isinstance(per, list) or not per:
        return 0.0
    live = [int(c) for c in per if isinstance(c, int) and c > 0]
    return sum(live) / len(live) if live else 0.0


def min_kicks_per_4bar(card: dict[str, Any] | None) -> int:
    """The low-end floor for one 4-bar window, from the material's own card.

    Two numbers already on the card answer different questions.
    ``min_pairs_per_4bar`` is the EXPORT GATE's minimum (2-3) — the point below
    which a window is broken. ``pairs_per_bar`` is what the material actually
    asks for. A pass that thins the low end must respect the second, not the
    first: measured 2026-08-19 on Graveyard, the gate floor of 3 never binds
    (real windows carry 5-8) while the card asks for 1.67/bar = 6.7 per
    window, so a thinning pass reading only the gate floor is unconstrained in
    practice.

    Returned as the wider of the two, so this can only ever be stricter than
    the gate — never looser. With no card at all the recorded law stands on
    its own: "Density floor wins over the roll rule: 3 kicks per 4 bars is the
    min" (tests/test_808_production_laws.py::
    test_hat_roll_clearing_never_thins_a_block_to_a_hole). The card may raise
    that floor for busy material; nothing may lower it.
    """
    if not isinstance(card, dict):
        return ROLL_THINNING_FLOOR_PER_4BAR
    try:
        gate = int(card.get("min_pairs_per_4bar") or ROLL_THINNING_FLOOR_PER_4BAR)
    except (TypeError, ValueError):
        gate = ROLL_THINNING_FLOOR_PER_4BAR
    try:
        implied = int(float(card.get("pairs_per_bar") or 0.0) * BEATS_PER_BAR)
    except (TypeError, ValueError):
        implied = 0
    return max(ROLL_THINNING_FLOOR_PER_4BAR, gate, implied)


def bar_has_melody(card: dict[str, Any] | None, bar: int) -> bool:
    """Does the uploaded material actually say something in this bar?"""
    if not isinstance(card, dict):
        return True
    per_bar = card.get("melody_onsets_per_bar")
    if not isinstance(per_bar, list) or not 0 <= bar < len(per_bar):
        return True
    try:
        return int(per_bar[bar]) > 0
    except (TypeError, ValueError):
        return True


def section_pocket_ceiling(section: str | None) -> int:
    """How many low-end onsets a MANUFACTURED pass may leave in one bar here.

    The single owner of the hook-vs-verse contrast, 2026-08-20. It bounds the
    density passes only — never the phrase composer, so a bar the material
    genuinely says two things in still gets both wherever it is in the song.

    Why the contrast is made by SUBTRACTION in the quiet sections rather than
    by addition in the loud ones: ``beat_humanize._cap_midi_kicks_per_bar``
    caps every bar at two non-ghost kicks, and measured on the five-file
    listening corpus 44-70 of every song's bars already sit ON that cap, with
    the hook pinned at exactly 2.00 kicks/bar on four of the five. There is no
    headroom above the verse to make a hook out of; the only place a contrast
    can come from is the padding the verse did not ask for.

    Reads ``MAX_PAIRS_PER_BAR`` — the ceiling this module already owns — so
    the peak keeps today's behaviour byte for byte and only the step down is
    new. Never returns less than one: the bar floor is a different owner's
    decision (``_enforce_section_pocket``), not this one's.
    """
    ceiling = max(1, int(MAX_PAIRS_PER_BAR))
    if str(section or "").strip().lower() in PEAK_SECTIONS:
        return ceiling
    return max(1, ceiling - 1)


def scaled_shape(
    shape: tuple[float, ...], card: dict[str, Any] | None, *, section: str | None = None,
    firm: bool = False,
) -> tuple[float, ...]:
    """Thin a fixed pocket shape down to the card's target onset count.

    Positions are never invented: this only drops the least structural
    offsets (keeping the cell's opening and its latest answer) so a sparse
    MIDI gets the same groove stated more simply, not a different one.

    ``section`` states this cell one onset more simply outside the peak
    sections (2026-08-20) — the composer's half of the same contrast
    ``section_pocket_ceiling`` owns on the manufacturing side. Bounded to one
    onset and floored at two, so the cell keeps its opening and its latest
    answer whatever the section is. Omitting it restores the old behaviour
    exactly.

    ``firm`` switches that step-down off for a phrase whose melody chose its
    own accents (2026-08-25, ``melody_beat_symbiosis.melody_accent_firmness``).
    The floor of two is not the protection it reads as: the cell spans a whole
    phrase, so dropping one onset costs one BAR its hit, and on Baton Rouge —
    100 % of bars decided by the material, and the file judged best — that
    landed on the "1" of 23 of 48 verse bars, every second bar of a two-bar
    loop. Measured with the step-down off there: kicks on the "1" 68 % -> 100 %.
    The cost is that file's hook-vs-verse contrast (1.28 -> 1.06), which is the
    trade this flag exists to make only where the material earns it.
    """
    if not isinstance(card, dict) or len(shape) <= 2:
        return shape
    target = int(card.get("pocket_onsets_per_phrase") or len(shape))
    if (
        section is not None
        and not firm
        and str(section).strip().lower() not in PEAK_SECTIONS
    ):
        target = max(2, target - 1)
    if target >= len(shape):
        return shape
    ordered = sorted(shape)
    keep = [ordered[0]]
    remaining = ordered[1:]
    # Farthest-first keeps the cell spread instead of front-loading it.
    while remaining and len(keep) < target:
        point = max(remaining, key=lambda value: min(abs(value - held) for held in keep))
        keep.append(point)
        remaining.remove(point)
    return tuple(sorted(keep))


# A hat run tighter than this reads as a roll rather than a pulse — same
# constant midi_lowend_director uses to find rolls for the low end.
HAT_ROLL_GAP = 0.19

# How far humanisation can push a hat off its own sixteenth, once the lane's
# one constant forward lag has been subtracted. Derived, not chosen:
# `beat_humanize.apply_hi_hat_swing` draws up to PPQ_SWING_MAX (9/96 beats)
# scaled by an intensity the caller caps at 1.15, so 0.108 beats is the most
# swing alone can reach -- comfortably inside the 0.125 half-sixteenth that
# would make "which sixteenth is this?" ambiguous. Measured at the point
# thin_generated_hats actually runs, the corpus sits at 0.0724-0.0728.
#
# Not imported from beat_humanize on purpose: this module is a leaf and
# beat_humanize imports IT. test_drum_density keeps the two in step instead,
# so retuning the swing fails a test rather than silently widening this.
_HAT_SWING_REACH_BEATS = 0.11

# How far above the lane's own median a late accent hat may sit. It is an
# accent, so it is allowed to be heard; it is not allowed to be the loudest
# thing in the lane. Bounded by the lane's 90th percentile as well.
LATE_HAT_ACCENT_LIFT = 12


def late_hat_velocity(
    lane: list[dict[str, Any]] | None,
    literal: int,
    *,
    peak: int | None = None,
    default: int = 88,
) -> int:
    """Bring a velocity written AFTER the leveller into the lane's own band.

    ``beat_humanize.apply_six_db_hat_rule`` is the hat lane's only leveller and
    it runs at stage 10, but four passes append hats after it: the motif answer
    and the boundary flick (``midi_fullkit_director``, stage 16), the 1/8
    rebuild (``beat_humanize.enforce_midi_section_density``, stage 18) and the
    accent lock (``melody_beat_symbiosis``, stage 21). Nothing levels them
    afterwards, so each one used to ship a literal -- 108/103/98, 84/76, 76 --
    against lane medians of 37-45, and both later multipliers
    (``_scale_lane_velocity`` 1.04-1.08, ``_boost_section_velocity`` 1.12)
    then scaled the whole lane alike, so the disproportion was baked in here
    and amplified afterwards. Measured on the five-file corpus, 33-157 notes
    per file shipped at a median of 77-96 and a maximum of 122-127 -- the
    backbeat's own level -- while the leveller's own ceiling was 63.

    The rule is the one `the project issue log (not included)` 0v.2 established for the accent
    lock, and this is that code MOVED rather than copied: the loudest literal
    a writer uses lands on the lane's median plus ``LATE_HAT_ACCENT_LIFT``,
    never above the lane's 90th percentile, and the writer's other literals
    keep their own proportions against it. The ceiling is this function's
    decision; the shape inside it stays the writer's.

    ``peak`` is the loudest literal that writer uses, so one writer's palette
    scales together (the boundary flick stays below the motif answer it
    belongs to). It defaults to ``literal`` -- a writer with a single value
    gets the ceiling itself.
    """
    top = int(peak) if peak else int(literal)
    if top <= 0:
        return max(1, int(literal))
    vels = sorted(
        int(note.get("velocity", 80) or 80)
        for note in (lane or [])
        if isinstance(note, dict)
    )
    if not vels:
        ceiling = default
    else:
        median = int(statistics.median(vels))
        p90 = vels[max(0, int(len(vels) * 0.9) - 1)]
        ceiling = max(1, min(p90, median + LATE_HAT_ACCENT_LIFT))
    return max(1, min(127, int(round(ceiling * int(literal) / top))))


# Tags a later pass put there on purpose (motif answers, boundary flicks).
# Thinning is allowed to remove chatter, never another writer's statement.
_PROTECTED_HAT_TAGS = (
    "midi_motif_answer",
    "midi_boundary_fill",
    "hat_fill",
    "midi_section_pulse_switch",
)


def thin_generated_hats(
    hats: list[dict[str, Any]],
    card: dict[str, Any] | None,
    *,
    floor_per_bar: int = 2,
    swing_reach: float = _HAT_SWING_REACH_BEATS,
) -> tuple[list[dict[str, Any]], int]:
    """Bring a generated hat lane down to the density the melody implies.

    Measured on a calm 68 BPM ballad (about 3 melodic onsets per bar): the hat
    lane ran at 6.1 notes per bar overall and 12-23 in the hook, because roll
    generation and the one-more-sixteenth densifier answer the SECTION, not the
    material. The same code on a busy 135 BPM file produces roughly one hat per
    melodic onset, which is the ratio worth keeping.

    Removal order is least-structural first — roll interiors, then sixteenth
    chatter, then off-beat eighths. Downbeats, on-beat hats, the first hat of
    every bar and anything another pass deliberately tagged are never removed,
    and no bar is taken below ``floor_per_bar``, so the full-kit contract's
    "every non-breathe phrase has hats" invariant still holds.

    A busy card (multiplier at or above 0.95) is an explicit no-op.
    """
    if not isinstance(card, dict) or not hats:
        return list(hats), 0
    try:
        multiplier = float(card.get("hat_multiplier") or 1.0)
    except (TypeError, ValueError):
        return list(hats), 0
    if multiplier >= 0.95:
        return list(hats), 0

    ordered = sorted(hats, key=lambda note: float(note.get("time_step", 0.0) or 0.0))
    steps = [float(note.get("time_step", 0.0) or 0.0) for note in ordered]
    wanted_removals = len(ordered) - int(round(len(ordered) * multiplier))
    if wanted_removals <= 0:
        return ordered, 0

    in_roll = [False] * len(ordered)
    roll_opener = [False] * len(ordered)
    for index in range(len(ordered) - 1):
        if steps[index + 1] - steps[index] <= HAT_ROLL_GAP:
            if not in_roll[index]:
                roll_opener[index] = True
            in_roll[index] = in_roll[index + 1] = True

    per_bar: dict[int, int] = {}
    first_in_bar: dict[int, int] = {}
    for index, step in enumerate(steps):
        bar = int(step // BEATS_PER_BAR)
        per_bar[bar] = per_bar.get(bar, 0) + 1
        first_in_bar.setdefault(bar, index)

    # This function runs at stage 11, AFTER apply_hi_hat_swing and
    # apply_hat_forward_lag have moved every hat off the grid, so the exact
    # comparisons the classifier below used to make -- abs(step - round(step))
    # < 1e-6 -- matched nothing at all. Measured on the five-file corpus at
    # the point this runs: ZERO notes exactly on a beat and zero exactly on an
    # eighth, out of 513-643, while 205-256 of them sat within 0.10 beats of a
    # beat. So the pulse protection could never fire, and the sixteenth test
    # was true for every note, collapsing three tiers into "roll interiors,
    # then everything else in time order". That is why 41-62 of the 124-163
    # notes removed per file sat on a QUARTER. the project issue log (not included) 0v.10.
    #
    # The honest question is which sixteenth a note is CLOSEST to, asked after
    # removing the single lane-wide offset apply_hat_forward_lag draws once
    # per track. The lag is estimated from the PULSE alone: a 1/32 roll note
    # sits 0.125 from the nearest sixteenth by construction and would drag the
    # estimate towards itself.
    pulse_offsets = [
        steps[index] - round(steps[index] * 4.0) / 4.0
        for index in range(len(ordered))
        if not in_roll[index]
    ]
    lane_lag = statistics.median(pulse_offsets) if pulse_offsets else 0.0

    def _grid_cell(index: int) -> int | None:
        """Which sixteenth this note is closest to, or None if it is too far
        from any of them for the answer to mean anything."""
        cells = (steps[index] - lane_lag) * 4.0
        nearest = round(cells)
        if abs(cells - nearest) * 0.25 > swing_reach:
            return None
        return int(nearest)

    def _priority(index: int) -> int:
        note = ordered[index]
        bar = int(steps[index] // BEATS_PER_BAR)
        if first_in_bar.get(bar) == index:
            return 0
        if any(note.get(tag) for tag in _PROTECTED_HAT_TAGS):
            return 0
        # Ordered BEFORE the grid tests, and it has to be: 10-46 roll notes
        # per file land within 0.125 of a beat, so a tolerant pulse test that
        # ran first would protect part of every roll at random -- worse than
        # either the old behaviour or the new one.
        if in_roll[index] and not roll_opener[index]:
            return 3
        cell = _grid_cell(index)
        if cell is None:
            # Off the sixteenth grid by more than humanisation can explain.
            # Fall through to the pre-2026-08-26 reading rather than claim it
            # is structural: this branch can only ever remove protection it
            # was never entitled to give.
            return 2
        if cell % 4 == 0:  # on a beat: the pulse itself
            return 0
        if cell % 2:  # sixteenth chatter
            return 2
        return 1

    candidates = sorted(
        (index for index in range(len(ordered)) if _priority(index) > 0),
        key=lambda index: (-_priority(index), steps[index]),
    )

    removed: set[int] = set()
    for index in candidates:
        if len(removed) >= wanted_removals:
            break
        bar = int(steps[index] // BEATS_PER_BAR)
        if per_bar.get(bar, 0) <= max(1, floor_per_bar):
            continue
        removed.add(index)
        per_bar[bar] -= 1

    if not removed:
        return ordered, 0
    return [note for index, note in enumerate(ordered) if index not in removed], len(removed)


__all__ = [
    "BEATS_PER_BAR",
    "HAT_ROLL_GAP",
    "MAX_PAIRS_PER_4BAR",
    "MAX_PAIRS_PER_BAR",
    "MIN_PAIRS_PER_BAR",
    "PEAK_SECTIONS",
    "section_pocket_ceiling",
    "active_lowend_bars",
    "bar_has_melody",
    "min_kicks_per_4bar",
    "HAT_CROWDING_SLOPE",
    "hat_floor_per_bar",
    "LATE_HAT_ACCENT_LIFT",
    "late_hat_velocity",
    "melody_attacks_per_bar",
    "ROLL_THINNING_FLOOR_PER_4BAR",
    "build_drum_density_card",
    "density_card",
    "melody_onsets_per_bar",
    "scaled_shape",
    "thin_generated_hats",
]

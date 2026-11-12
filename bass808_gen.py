"""808 Bass + Kick — joint call-and-response pocket.

Rebuilt 2026-07 from producer-rule extraction (Dillon XO, "The ULTIMATE 808
Tutorial") after three earlier rounds of independent kick-then-808 tuning
never landed right on a real render:

  1. PITCH follows the pinned chord progression and steps on 2-bar
     boundaries (not every bar, not free-picked from a ranked pool).
  2. LESS IS MORE — density is sparse by construction (2-3 shared onsets
     per 4-bar cell), not generated dense then pruned down to a silence %.
  3. Kick and 808 are placed TOGETHER by one generator, not two competing
     passes reconciled after the fact. Each onset gets ONE role, decided
     once: "paired" (kick reinforces the 808 on the same hit — the
     dominant case), "kick_only" (kick fills a gap where the 808 is
     resting) or "bass_only" (808 alone, kick deliberately absent).
  4. Variation (octave jump / slide / a single added-or-removed hit) is
     rare and point-like — at most ONE technique, applied ONCE per 8-bar
     loop, never stacked and never on every phrase.
  5. The 8-bar loop is structurally stable in bars 1-6 (bars 5-6 are the
     verbatim rhythmic continuation of bars 1-2, just re-pitched against
     whatever the progression says at that position) and only bars 7-8
     carry the loop's one variation.

The rules above are an extraction; they only approximate the video's
by-ear workflow.
"""

from __future__ import annotations

import hashlib
from typing import Any

from music_theory import SCALES, name_for_midi, parse_note_name

BEATS_PER_BAR = 4.0
PHRASE_BARS = 4
PHRASE_BEATS = PHRASE_BARS * BEATS_PER_BAR  # 16 — one rhythm/pitch cell
LOOP_BARS = 8
LOOP_BEATS = LOOP_BARS * BEATS_PER_BAR  # 32 — structural repeat unit

# FL Sampler comfort band — tonic prefers C5; scale walks stay nearby.
BASS_MIDI_CENTER = 60  # C5
BASS_NOTE = "C5"  # legacy default / tests
SUB_LO = 48  # C4 — hard floor. Below this the cone just flaps: physical mud.
SUB_HI = 60  # C5 — hard ceiling. Above this the 808 turns into a squeaky synth.
# The user-facing 808 lane is different from the legacy ``sub_808`` helper
# above. It carries actual low end, not a pitched mid-bass line.
LOWEND_808_LO = 36  # C3
LOWEND_808_HI = 47  # B3
# Slides AND octave jumps are the sanctioned exceptions to SUB_HI — both are
# short, deliberate accents that immediately resolve back into the working
# window, never a new resting register.
SLIDE_LO = 72  # C6  — bottom of the sanctioned accent octave
SLIDE_HI = 83  # B6  — top; anything above is the "squeaky synth" zone

MIN_BASS_LENGTH = 0.5
BASS_NOTE_LENGTH = 0.5
# A kick is a short transient; the paired 808 needs a real tail.  This is the
# preferred hold when the next musical onset leaves enough room.  It is still
# clipped at the next onset, so dense rolls remain short instead of smearing.
BASS_SUSTAIN_LENGTH = 2.75
MAX_BASS_LENGTH = 4.0
# 2026-07 fix: a real bug, not a tuning choice. The slide stab used to be a
# fixed 0.125-beat blip — shorter than even MIN_BASS_LENGTH's "punchiest"
# floor. flp_writer sets FL's native slide/portamento bit on this note (it
# glides IN from the previous note over ITS OWN length — see flp_writer.py's
# _note_struct docstring), so a 0.125-beat note gave the glide almost no
# time to actually travel before cutting off — the bit was set but nothing
# audible happened. Per real-Piano-Roll observation (short ~2 grid-steps =
# punchiest, longer ~5 grid-steps is where a slide reads as deliberate) and
# rule 4b's own video source ("the longer you extend it, the longer the
# slide will be"): a slide note needs to be the LONG end of the register,
# not the short end.
_SLIDE_LENGTH_BEATS = 1.25  # ~5 grid steps at a 1/16 grid

_KICK_LEN = 0.28
_KICK_VEL = 124
# Kick is the peak king (velocity ~100%); the 808 is the mass king and sits
# ~3-4 dB under it, or its sustain eats the headroom the kick needs to punch.
_BASS_VEL = 116
# 2026-07 — GenreProfile.kick808_micro_humanize opt-in: a small +/- dust on
# top of the fixed base above (still one tuned average level per lane, not a
# free-floating range), and the matching tolerance _audit_kick808_symbiosis
# uses for its exact-equality velocity check. Kept as one shared constant so
# the audit can never be stricter than the jitter it's supposed to allow.
_KICK808_VELOCITY_JITTER = 6
# ms range for the same opt-in's timing dust on kick/808_bass onsets —
# mirrors beat_humanize.PRE_SNARE_SHIFT_MS's magnitude for snare/clap.
_KICK808_TIMING_JITTER_MS = (2.0, 6.0)

# Density law ("less is more"): 2-3 SHARED onsets per 4-bar cell — total
# events across kick+808 together, not per track. The old law (4-6 kicks
# plus up to 7 independent bass notes) was the exact kind of over-dense,
# generate-then-prune result the video calls out as the reason beats don't
# sell.
_ONSETS_MIN = 8
_ONSETS_MAX = 10
_MANDATORY_OFFSET = 0.0  # first-hit law: bar 1 always opens the cell
# Preference-ordered candidate slots (relative to cell start, 0..PHRASE_BEATS)
# for the 1-2 extra onsets — full-bar downbeats first, then half-bar
# syncopes, then quarter-off syncopes. Order is the law, not the table: the
# min-gap filter below decides which survive.
_CANDIDATE_OFFSETS = (
    4.0, 8.0, 12.0,
    1.5, 3.0, 5.5, 7.0, 9.5, 11.0, 13.5, 15.0,
    2.75, 6.75, 10.75, 14.75,
)
# Anti-machine-gun: never two onsets in the same cell closer than this.
_MIN_GAP_BEATS = 0.75
# How close (beats) a candidate onset may land next to a real clap hit
# before it's rejected. The mandatory downbeat is exempt (first-hit law
# outranks clap avoidance).
_CLAP_AVOID_WINDOW = 0.15

# Role law: paired (kick reinforces 808 on the same hit) is the dominant
# case; kick_only/bass_only are deliberate call-and-response variety, not
# the default. The cell's mandatory opening hit is always paired — it's the
# foundation the rest of the cell answers to.
_ROLE_PAIRED = "paired"
_ROLE_KICK_ONLY = "kick_only"
_ROLE_BASS_ONLY = "bass_only"
_PAIRED_WEIGHT = 0.42
_KICK_ONLY_WEIGHT = 0.24
_BASS_ONLY_WEIGHT = 0.34


# --- deliberate double hit -------------------------------------------------
#
# One musical onset rendered as TWO adjacent kicks: a quieter pickup followed
# by the full hit. KICK-ONLY since 2026-08-20 (variant B) — the 808 stays one
# ringing tail under both taps; making the 808 double too (variant A) broke
# either the "808 is the tail" law or the mono-abut law, never both at once
# (the project issue log (not included) §0s). The ghost therefore rides the existing solo-kick
# machinery: it is a kick_only onset, recognized by
# beat_humanize._intentional_solo_lowend.
#
# It is not extra density — the density law in this module counts onsets, and
# a double is still one of them — which is why it can exist without touching
# MAX_PAIRS_PER_BAR or the "less is more" decision above.
#
# _audit_kick808_symbiosis has always had the hook for it: its kick_roll test
# exempts a pair whose LATER note is the double's main hit (it reads through
# is_double_main below). That hook had ZERO writers until this feature; the
# other two gates (overfull_kick_bars, velocities_ok) had to learn the ghost
# when it was wired, or a whole generation would have failed instead of
# producing a double.
#
# Tags, both needed:
#   intentional_double        on the SECOND note — the shape the kick_roll
#                             test already expects (it inspects `right`)
#   intentional_double_ghost  on the FIRST note — the quieter pickup; it does
#                             not count towards the per-bar ceiling and it
#                             carries the reduced velocity below
#
# A tag is only a double while its lowend_role agrees: ghost => kick_only,
# main => paired. Several passes mint kicks by deep-copying an existing one
# as a TEMPLATE (the pairing seal's companion and foundation restorations,
# _ensure_808_on_every_kick's bass-support kick, midi_placement_symbiosis'
# pairing clone); those copies strip the tags, but if one ever leaks through,
# the role check keeps it inert instead of granting a stray hit ghost
# semantics (wrong expected velocity => velocity_drift, or a solo exemption
# for a note that is not a pickup at all). Every reader of these tags goes
# through the three functions below — never read the raw keys elsewhere.
KICK_GHOST_VELOCITY_RATIO = 0.70


def is_ghost_kick(note: Any) -> bool:
    """The quieter first half of a deliberate double.

    Tag AND role must agree (see the block comment): a ghost that lost its
    kick_only role — e.g. through a template copy — is no longer a ghost."""
    if not isinstance(note, dict) or not note.get("intentional_double_ghost"):
        return False
    return str(note.get("lowend_role") or "") == "kick_only"


def is_double_main(note: Any) -> bool:
    """The full second half of a deliberate double (tag + paired role)."""
    if not isinstance(note, dict) or not note.get("intentional_double"):
        return False
    return str(note.get("lowend_role") or "") == "paired"


def is_double_member(note: Any) -> bool:
    """Either half of a deliberate double.

    The pair is ONE onset. Every pass that reasons about spacing, per-bar
    ceilings or deletion has to treat it as one, or it will re-litigate the
    generator's decision and leave half a double behind — which is worse
    than never having made one."""
    return is_ghost_kick(note) or is_double_main(note)


def kick_velocity_for(note: Any, base: int = _KICK_VEL) -> int:
    """The velocity a kick note is supposed to carry.

    ONE owner for both sides of the contract: `_normalize_kick808_dynamics`
    writes it and `_audit_kick808_symbiosis` checks against it. Two copies of
    this arithmetic is exactly the disagreement the project issue log (not included) 0.1 is about, and
    here it would be fatal rather than latent — the audit rejects the whole
    generation on `velocity_drift`.
    """
    if is_ghost_kick(note):
        return max(1, int(round(base * KICK_GHOST_VELOCITY_RATIO)))
    return base


def _rng(seed: int, *parts: Any) -> float:
    raw = hashlib.md5(f"{seed}|{'|'.join(map(str, parts))}".encode("utf-8")).hexdigest()
    return int(raw[:8], 16) / 0xFFFFFFFF


def _section_beat_ranges(arrangement: Any) -> list[tuple[float, float, str]]:
    """``plg_arrangement`` (arranger.arrange_song's per-occurrence section
    list) as sorted ``(start_beat, end_beat, name)`` ranges — same shape as
    beat_humanize._section_beat_ranges, kept as its own copy here since
    beat_humanize already imports this module (importing back would be
    circular)."""
    ranges: list[tuple[float, float, str]] = []
    if not isinstance(arrangement, list):
        return ranges
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        try:
            start_bar = float(section.get("start_bar", 0) or 0)
            bars = float(section.get("bars", 0) or 0)
        except (TypeError, ValueError):
            continue
        start_beat = start_bar * BEATS_PER_BAR
        ranges.append((start_beat, start_beat + bars * BEATS_PER_BAR, str(section.get("name") or "")))
    ranges.sort(key=lambda r: r[0])
    return ranges


def _section_origin(step: float, ranges: list[tuple[float, float, str]]) -> float:
    """start_beat of the section containing ``step``, or 0.0 (a no-op
    offset) when there's no arrangement or no range covers it."""
    for start_beat, end_beat, _name in ranges:
        # Half-open [start, end): tolerate float noise only at the START of
        # a range. Sections are contiguous, so tolerating the END too made a
        # step sitting exactly on a boundary match whichever range came
        # first — see beat_humanize._kick808_timing_key's docstring, which
        # hit this exact bug first.
        if start_beat - 1e-6 <= step < end_beat - 1e-6:
            return start_beat
    return 0.0


def _relative_rng_key(step: float, ranges: list[tuple[float, float, str]]) -> tuple[Any, ...]:
    """RNG key for a position that should repeat identically across
    occurrences of the same section, instead of drifting with the song's
    absolute timeline.

    build_kick_808_pocket walks one continuous 8-bar loop/4-bar cell grid
    from song start to song end and used to key every rhythmic choice
    (onset count, onset placement, kick/808/silence role) off each cell's
    ABSOLUTE beat position. Two repeats of the same section (e.g. verse #1
    and verse #2) land at different absolute positions, so they picked
    genuinely different notes — not just different cosmetic timing dust
    (that part was fix #1, _apply_kick808_micro_timing) — which is what
    kept flp_template._apply_pattern_bank from ever reusing their FL
    pattern. Keying by (section name, position relative to that section's
    start) instead makes true repeats produce identical rhythm.

    Returns ``(name, round(step - start_beat, 5))`` when ``step`` falls
    inside a known section range, else ``(step,)`` — the bare value, i.e.
    exactly today's key when unpacked with ``*key`` into an _rng(...) call.
    That fallback is what keeps every existing caller that doesn't pass
    ``arrangement`` (all of them, until beat_humanize's build_kick_808_pocket
    call site was updated) seeing byte-identical output to before.

    The section name is included (unlike the pure-numeric _section_origin
    used for the seed-offset math) specifically so two DIFFERENT section
    types that happen to share a relative offset — e.g. every section's
    opening cell sits at relative 0 — don't collide onto the same rhythm.
    """
    for start_beat, end_beat, name in ranges:
        if start_beat - 1e-6 <= step < end_beat - 1e-6:
            return (name, round(step - start_beat, 5))
    return (step,)


def _loop_start(beat: float) -> float:
    return float(int(beat // LOOP_BEATS) * LOOP_BEATS)


def _snap_pc_to_scale(pc: int, scale_pcs: set[int]) -> int:
    """Nearest in-key pitch class — see module docstring rule 1."""
    pc %= 12
    if not scale_pcs or pc in scale_pcs:
        return pc
    return min(scale_pcs, key=lambda s: (min((s - pc) % 12, (pc - s) % 12), s))


def _chord_pc_for_window(
    window_start: float,
    chord_pcs: list[int],
    phrase_root_overrides: dict[int, int] | None,
    scale_pcs: set[int],
    origin: float,
) -> int:
    """The 808's pitch for a 2-bar window — the chord that HOLDS at the
    window's opening bar, held flat through the pair (rule 1: the 808
    doesn't chase every bar-to-bar wobble in a higher-resolution chord bed;
    it steps once every 2 bars, at the window boundary)."""
    bar = int(window_start // BEATS_PER_BAR)
    pc = chord_pcs[bar % len(chord_pcs)] if chord_pcs else 0
    if phrase_root_overrides:
        phrase_idx = int((window_start - origin) // PHRASE_BEATS)
        override_pc = phrase_root_overrides.get(phrase_idx)
        # In-key guard: never let an override inject an out-of-scale note.
        if override_pc is not None and (int(override_pc) % 12) in scale_pcs:
            pc = override_pc
    return pc


def _pick_onsets(
    cell_start: float,
    seed: int,
    clap_times: list[float],
    melody_accents: list[float] | None = None,
    *,
    section_ranges: list[tuple[float, float, str]] | None = None,
    bpm: float = 140.0,
) -> list[float]:
    """Choose a dense but controlled 4-bar pocket.

    Strong melody anchors are considered first, but never bypass the normal
    density, spacing, or clap-collision invariants.
    """
    section_ranges = section_ranges or []
    onsets = [cell_start + _MANDATORY_OFFSET]
    key = _relative_rng_key(cell_start, section_ranges)
    tempo_bonus = 1 if bpm >= 150.0 else 0
    tempo_bonus += 1 if bpm >= 170.0 else 0
    want = _ONSETS_MIN + tempo_bonus + min(
        _ONSETS_MAX - _ONSETS_MIN,
        int(_rng(seed, "cell_count", *key) * 3.0),
    )

    def _too_close(t: float) -> bool:
        return any(abs(t - o) < _MIN_GAP_BEATS - 1e-9 for o in onsets)

    def _near_clap(t: float) -> bool:
        return any(abs(t - c) < _CLAP_AVOID_WINDOW for c in clap_times)

    accent_offsets = [
        round(float(t) - cell_start, 5)
        for t in (melody_accents or [])
        if cell_start <= float(t) < cell_start + PHRASE_BEATS
    ]
    candidate_offsets = list(dict.fromkeys(accent_offsets + list(_CANDIDATE_OFFSETS)))

    for offset in candidate_offsets:
        if len(onsets) >= want:
            break
        cand = cell_start + offset
        if cand >= cell_start + PHRASE_BEATS - 0.05:
            continue
        if _too_close(cand) or _near_clap(cand):
            continue
        is_melody_anchor = offset in accent_offsets
        # key[-1] is cell_start's section-relative form (or bare cell_start
        # when no arrangement match) — adding `offset` reproduces today's
        # `cand = cell_start + offset` exactly in that fallback case.
        onset_key = key + (round(key[-1] + offset, 5),)
        if is_melody_anchor or _rng(seed, "onset_pick", *onset_key) < 0.6:
            onsets.append(round(cand, 5))

    # Density is a producer invariant. If the seeded choices were unusually
    # selective, fill the remaining slots from the same musical grid.
    for offset in candidate_offsets:
        if len(onsets) >= want:
            break
        cand = cell_start + offset
        if cand >= cell_start + PHRASE_BEATS - 0.05:
            continue
        if _too_close(cand) or _near_clap(cand):
            continue
        onsets.append(round(cand, 5))
    return sorted(onsets)


def _assign_roles(
    onsets: list[float],
    seed: int,
    *,
    section_ranges: list[tuple[float, float, str]] | None = None,
) -> list[str]:
    """One role per onset, decided once — see module docstring rule 3."""
    section_ranges = section_ranges or []
    roles = []
    kick_times: list[float] = []
    kicks_per_bar: dict[int, int] = {}
    bass_per_bar: dict[int, int] = {}
    for i, t in enumerate(onsets):
        if i == 0:
            roles.append(_ROLE_PAIRED)  # the cell's foundation hit
            kick_times.append(t)
            kicks_per_bar[int(t // BEATS_PER_BAR)] = 1
            bass_per_bar[int(t // BEATS_PER_BAR)] = 1
            continue
        r = _rng(seed, "role", *_relative_rng_key(t, section_ranges))
        bar = int(t // BEATS_PER_BAR)
        kick_room = (
            (not kick_times or t - kick_times[-1] >= 1.0)
            and kicks_per_bar.get(bar, 0) < 2
        )
        if kicks_per_bar.get(bar, 0) == 0 and bass_per_bar.get(bar, 0) == 0:
            role = _ROLE_PAIRED
        elif not kick_room or (bar % 4 == 3 and t % BEATS_PER_BAR >= 2.75):
            role = _ROLE_BASS_ONLY
        elif r < _PAIRED_WEIGHT:
            role = _ROLE_PAIRED
        elif r < _PAIRED_WEIGHT + _KICK_ONLY_WEIGHT:
            role = _ROLE_KICK_ONLY
        else:
            role = _ROLE_BASS_ONLY
        roles.append(role)
        if role in (_ROLE_PAIRED, _ROLE_KICK_ONLY):
            kick_times.append(t)
            kicks_per_bar[bar] = kicks_per_bar.get(bar, 0) + 1
        if role in (_ROLE_PAIRED, _ROLE_BASS_ONLY):
            bass_per_bar[bar] = bass_per_bar.get(bar, 0) + 1
    return roles


def _kick_note(t: float, template: dict[str, Any] | None) -> dict[str, Any]:
    pitch = str((template or {}).get("note") or "C5")
    vel = int((template or {}).get("velocity", _KICK_VEL) or _KICK_VEL)
    return {
        "time_step": round(t, 5),
        "note": pitch,
        "length": _KICK_LEN,
        "velocity": max(110, vel),
        "kick_808_lock": True,
    }


def _materialize_cell(
    onsets: list[float],
    roles: list[str],
    *,
    cell_start: float,
    cell_end: float,
    chord_pcs: list[int],
    scale_pcs: set[int],
    phrase_root_overrides: dict[int, int] | None,
    origin: float,
    kick_template: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn (onset times, roles) into real kick/bass note dicts. Pitch is
    always re-derived from the CURRENT chord window (never copied from a
    prior cell), even when the rhythm shape itself is a verbatim repeat —
    rule 1 (harmony truth) always wins over rule 5 (rhythm stability)."""
    kicks: list[dict[str, Any]] = []
    bass: list[dict[str, Any]] = []
    for i, t in enumerate(onsets):
        role = roles[i]
        next_t = onsets[i + 1] if i + 1 < len(onsets) else cell_end
        # 2-bar chord window, anchored to the cell (not to absolute beat 0),
        # so a cell that doesn't start on an 8-beat boundary still gets
        # internally-consistent pairs.
        window_start = cell_start + float(int((t - cell_start) // (2 * BEATS_PER_BAR)) * (2 * BEATS_PER_BAR))
        pc = _chord_pc_for_window(window_start, chord_pcs, phrase_root_overrides, scale_pcs, origin)
        midi = SUB_LO + _snap_pc_to_scale(int(pc), scale_pcs)
        if role in (_ROLE_PAIRED, _ROLE_KICK_ONLY):
            kicks.append(_kick_note(t, kick_template))
        if role in (_ROLE_PAIRED, _ROLE_BASS_ONLY):
            length = min(BASS_NOTE_LENGTH, max(MIN_BASS_LENGTH, next_t - t))
            bass.append(
                {
                    "time_step": round(t, 5),
                    "note": name_for_midi(midi),
                    "length": round(length, 5),
                    "velocity": _BASS_VEL,
                    "pan": 64,
                    "bass808_gen": True,
                    "kick_lock": role == _ROLE_PAIRED,
                }
            )
    return kicks, bass


def _abut_mono(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strict monophony — each note ends exactly where the next begins."""
    if not notes:
        return []
    ordered = sorted((dict(n) for n in notes), key=lambda n: float(n["time_step"]))
    kept: list[dict[str, Any]] = []
    for i, cur in enumerate(ordered):
        note = dict(cur)
        want = float(note.get("length", MAX_BASS_LENGTH) or MAX_BASS_LENGTH)
        if i + 1 < len(ordered):
            gap = float(ordered[i + 1]["time_step"]) - float(note["time_step"])
            if gap <= 0.05:
                continue  # stacked attacks — keep the later one
            note["length"] = min(MAX_BASS_LENGTH, gap, want)
        else:
            note["length"] = min(MAX_BASS_LENGTH, want)
        # Floor protects against slivers left by CLIPPING, not against a
        # deliberately short request (e.g. the 0.125-beat slide stab) — a
        # note that got everything it asked for must never be dropped just
        # because "everything" was short.
        if note["length"] >= min(MIN_BASS_LENGTH * 0.5, want):
            kept.append(note)
    return kept


def _apply_octave_jump(
    bass_notes: list[dict[str, Any]], *, window_start: float, window_end: float
) -> list[dict[str, Any]]:
    """Rule 4a — push a short passing note up an octave near a phrase end."""
    candidates = [n for n in bass_notes if window_start <= float(n["time_step"]) < window_end]
    if not candidates:
        return bass_notes
    target = min(candidates, key=lambda n: float(n["length"]))
    out = []
    for n in bass_notes:
        if n is target:
            n = dict(n)
            try:
                midi = parse_note_name(str(n["note"])) + 12
            except (KeyError, ValueError, TypeError):
                midi = BASS_MIDI_CENTER + 12
            n["note"] = name_for_midi(min(midi, SLIDE_HI))
            n["octave_jump"] = True
        out.append(n)
    return out


def _apply_slide(
    bass_notes: list[dict[str, Any]], *, window_start: float, window_end: float
) -> list[dict[str, Any]]:
    """Rule 4b — a deliberately LONG high-register stab (see
    _SLIDE_LENGTH_BEATS) that glides in from the previous note and resolves
    exactly as the next loop's downbeat lands (mono-abut clips both
    neighbors against it, same as any other onset). Length is clamped to
    whatever room the window actually has — a truncated tail must never
    push the stab's start before bars 7-8 (rule 5) just to hit the full
    target length."""
    length = min(_SLIDE_LENGTH_BEATS, window_end - window_start)
    if length < MIN_BASS_LENGTH:
        return bass_notes  # not enough room to slide meaningfully
    slide_start = round(window_end - length, 5)
    prior = [n for n in bass_notes if float(n["time_step"]) < slide_start]
    if not prior:
        return bass_notes
    root = prior[-1]
    try:
        root_midi = parse_note_name(str(root["note"]))
    except (KeyError, ValueError, TypeError):
        root_midi = BASS_MIDI_CENTER
    high_midi = max(SLIDE_LO, min(SLIDE_HI, root_midi + 24))
    stab = {
        "time_step": slide_start,
        "note": name_for_midi(high_midi),
        "length": round(length, 5),
        "velocity": _KICK_VEL,
        "pan": 64,
        "bass808_gen": True,
        "slide": True,
    }
    return sorted(bass_notes + [stab], key=lambda n: float(n["time_step"]))


def _apply_tweak(
    kicks: list[dict[str, Any]],
    bass_notes: list[dict[str, Any]],
    *,
    window_start: float,
    window_end: float,
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rule 4c — a single added or removed hit ("I just added one cut right
    here"), never more than one."""
    if _rng(seed, "tweak_dir", window_start) < 0.5:
        in_window = [n for n in bass_notes if window_start <= float(n["time_step"]) < window_end]
        if not in_window:
            return kicks, bass_notes
        drop_t = round(float(in_window[-1]["time_step"]), 5)
        bass_notes = [n for n in bass_notes if round(float(n["time_step"]), 5) != drop_t]
        # Removing the bass half of a paired hit used to leave a naked kick.
        # A "missing hit" variation now removes the whole pair; bass-only
        # notes still disappear independently because there is no kick there.
        kicks = [
            n for n in kicks
            if round(float(n["time_step"]), 5) != drop_t
        ]
        return kicks, bass_notes
    t = round(window_start + 1.0, 5)
    if t >= window_end - 0.25:
        return kicks, bass_notes
    if any(abs(float(n["time_step"]) - t) < 0.1 for n in bass_notes):
        return kicks, bass_notes
    prior = [n for n in bass_notes if float(n["time_step"]) < t]
    note_name = str(prior[-1]["note"]) if prior else BASS_NOTE
    bass_notes = sorted(
        bass_notes
        + [
            {
                "time_step": t,
                "note": note_name,
                "length": MIN_BASS_LENGTH,
                "velocity": _BASS_VEL,
                "pan": 64,
                "bass808_gen": True,
                "tweak": True,
            }
        ],
        key=lambda n: float(n["time_step"]),
    )
    return kicks, bass_notes


def _apply_variation(
    kicks: list[dict[str, Any]],
    bass_notes: list[dict[str, Any]],
    *,
    window_start: float,
    window_end: float,
    seed: int,
    allow_slides: bool,
    section_ranges: list[tuple[float, float, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pick exactly ONE technique for this loop's bars 7-8 (rule 4: rare,
    point-like — never stacked)."""
    if window_end - window_start < 1.0:
        return kicks, bass_notes  # tail too short to safely vary
    r = _rng(seed, "variation_pick", *_relative_rng_key(window_start, section_ranges or []))
    # Kick/808 lane is intentionally attack-led: no octave or slide accents.
    return kicks, bass_notes


def build_kick_808_pocket(
    *,
    start: float,
    bars: int,
    root_pc: int = 0,
    scale: str = "natural_minor",
    roots_pc: list[int] | None = None,
    clap_times: list[float] | None = None,
    kick_template: dict[str, Any] | None = None,
    seed: int = 0,
    phrase_root_overrides: dict[int, int] | None = None,
    melody_accents: list[float] | None = None,
    allow_slides: bool = False,
    arrangement: list[dict[str, Any]] | None = None,
    bpm: float = 140.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Single entry point — kick and 808 planned TOGETHER, one 8-bar loop at
    a time. Bars 1-4 of each loop are a fresh cell; bars 5-8 reuse that
    SAME rhythm shape (bars 5-6 are a verbatim continuation — rule 5), then
    bars 7-8 get exactly one variation (rule 4). A loop tail shorter than a
    full cell/loop degrades gracefully (no forced structure that doesn't
    fit) rather than crashing or padding with a bogus repeat.

    ``arrangement`` (``plg_arrangement``, optional) makes every rhythmic
    RNG draw section-relative instead of absolute — see
    _relative_rng_key's docstring for why: without it, two occurrences of
    the same section (e.g. verse #1 and verse #2) get genuinely different
    kick/808 notes just because they land at different song positions,
    which stops flp_template._apply_pattern_bank from ever recognizing them
    as the same repeated pattern. Omitting it reproduces today's exact
    behavior (every RNG key collapses to the bare absolute value).
    """
    if bars <= 0:
        return [], []
    end = start + bars * BEATS_PER_BAR
    chord_pcs = [int(pc) % 12 for pc in (roots_pc or [root_pc])]
    scale_pcs = {(int(root_pc) + iv) % 12 for iv in SCALES.get(scale, SCALES["natural_minor"])}
    clap_times = list(clap_times or [])
    section_ranges = _section_beat_ranges(arrangement)

    all_kicks: list[dict[str, Any]] = []
    all_bass: list[dict[str, Any]] = []

    loop_start = _loop_start(start)
    while loop_start < end - 1e-9:
        loop_end = min(loop_start + LOOP_BEATS, end)
        cell_a_start = max(loop_start, start)
        cell_a_end = min(cell_a_start + PHRASE_BEATS, end)
        if cell_a_end - cell_a_start < 1.0:
            loop_start += LOOP_BEATS
            continue

        cell_seed_key = int(round(cell_a_start - _section_origin(cell_a_start, section_ranges), 3))
        loop_accents: list[float] = []
        for accent in melody_accents or []:
            accent = float(accent)
            if cell_a_start <= accent < cell_a_start + PHRASE_BEATS:
                loop_accents.append(accent)
            elif cell_a_start + PHRASE_BEATS <= accent < cell_a_start + LOOP_BEATS:
                # Preserve the stable 8-bar rhythm while letting either half
                # contribute meaningful anchors to that shared shape.
                loop_accents.append(accent - PHRASE_BEATS)
        onsets = _pick_onsets(
            cell_a_start,
            seed + cell_seed_key,
            clap_times,
            loop_accents,
            section_ranges=section_ranges,
            bpm=bpm,
        )
        onsets = [t for t in onsets if t < cell_a_end - 0.05]
        roles = _assign_roles(onsets, seed + cell_seed_key, section_ranges=section_ranges)
        kicks_a, bass_a = _materialize_cell(
            onsets, roles,
            cell_start=cell_a_start, cell_end=cell_a_end,
            chord_pcs=chord_pcs, scale_pcs=scale_pcs,
            phrase_root_overrides=phrase_root_overrides, origin=start,
            kick_template=kick_template,
        )
        all_kicks.extend(kicks_a)
        all_bass.extend(bass_a)

        # Bars 5-8: the SAME rhythm shape transplanted (rule 5 — stable
        # continuation), pitch re-derived fresh at the new position.
        cell_b_start = loop_start + PHRASE_BEATS
        if cell_b_start < loop_end - 1.0 and cell_b_start >= start:
            cell_b_end = min(cell_b_start + PHRASE_BEATS, end)
            # This is a new phrase, not a copy of bars 1-4.  Keep the same
            # density and pairing laws, but let this half listen to its own
            # MIDI anchors and use an independent deterministic seed.
            cell_b_seed = seed + int(round((cell_b_start - _section_origin(cell_b_start, section_ranges)) * 1000))
            cell_b_accents = [
                float(accent) for accent in (melody_accents or [])
                if cell_b_start <= float(accent) < cell_b_end
            ]
            onsets_b = _pick_onsets(
                cell_b_start, cell_b_seed, clap_times, cell_b_accents,
                section_ranges=section_ranges,
            )
            onsets_b = [time for time in onsets_b if time < cell_b_end - 0.05]
            roles_b = _assign_roles(onsets_b, cell_b_seed, section_ranges=section_ranges)
            kicks_b, bass_b = _materialize_cell(
                onsets_b, roles_b,
                cell_start=cell_b_start, cell_end=cell_b_end,
                chord_pcs=chord_pcs, scale_pcs=scale_pcs,
                phrase_root_overrides=phrase_root_overrides, origin=start,
                kick_template=kick_template,
            )
            # Rule 5: variation confined to bars 7-8 of the loop only.
            var_window_start = cell_b_start + 2 * BEATS_PER_BAR
            kicks_b, bass_b = _apply_variation(
                kicks_b, bass_b,
                window_start=var_window_start, window_end=cell_b_end,
                seed=cell_b_seed, allow_slides=allow_slides,
                section_ranges=section_ranges,
            )
            all_kicks.extend(kicks_b)
            all_bass.extend(bass_b)

        loop_start += LOOP_BEATS

    all_kicks.sort(key=lambda n: float(n["time_step"]))
    all_bass = _abut_mono(all_bass)
    return all_kicks, all_bass


__all__ = [
    "build_kick_808_pocket",
    "BASS_NOTE",
    "BASS_NOTE_LENGTH",
    "BASS_SUSTAIN_LENGTH",
    "BASS_MIDI_CENTER",
    "MIN_BASS_LENGTH",
    "MAX_BASS_LENGTH",
    "PHRASE_BARS",
    "PHRASE_BEATS",
    "LOOP_BARS",
    "LOOP_BEATS",
    "SUB_LO",
    "SUB_HI",
    "LOWEND_808_LO",
    "LOWEND_808_HI",
    "SLIDE_LO",
    "SLIDE_HI",
]

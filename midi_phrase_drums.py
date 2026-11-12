"""Phrase-owned drum/808 composition for confident uploaded-MIDI structure."""
from __future__ import annotations

from typing import Any

from bass808_gen import BASS_NOTE_LENGTH, MIN_BASS_LENGTH, LOWEND_808_LO, _BASS_VEL, _KICK_VEL
from music_theory import SCALES, name_for_midi

BEATS_PER_BAR = 4.0
PHRASE_BEATS = 8.0

# Minimum spacing between two kick attacks. The phrase reader has always
# enforced it (_assign_lowend_roles) and the export audit has always rejected
# anything tighter as a kick_roll (beat_humanize._audit_kick808_symbiosis uses
# 0.95), but the two density passes below used to place notes without
# consulting it — they only checked "is this exact point taken" and "is the bar
# full". While every bar was saturated that never showed; with a sparse pocket
# a gap filler could land a quarter-beat before the next bar's foundation and
# fail the export gate. One constant, applied by every writer in this module.
MIN_LOWEND_GAP_BEATS = 1.0

_SNARE_VEL = 112
_CLAP_VEL = 104

# Where a backbeat sits inside its own bar. This is not a new decision: every
# entry of _DRUM_SHAPES below has always put the snare on phrase-relative 2.0
# and 6.0, i.e. beat 3 of each of the phrase's first two bars. It is also the
# one thing the 2026-08-21 backbeat audit found already RIGHT — 100 % of
# permitted bars, on all five listening files — so _backbeat_figure chooses
# which lanes sound here and never moves the instant itself.
_BACKBEAT_BAR_OFFSET = 2.0
# Where a phrase's own turn is answered — beat 4.5 of its last bar. The offset
# is unchanged since it was phrase-relative 7.5; who gets it is not. It used to
# be gated on the phrase ROLE being `lift` or `landing`, and the role `lift`
# does not occur once across the ten-file corpus, so two of the five listening
# files shipped no phrase-turn gesture at all while the other three got exactly
# three each. _backbeat_figure decides it from phrase geometry now, and the
# role tuple that guarded it is gone.
_PHRASE_EXIT_OFFSET = 3.5

# How close a MANUFACTURED low-end onset may sit to a backbeat hit before a
# clear grid slot elsewhere is preferred. Not a new idea: bass808_gen — the
# other low-end generator, on the non-MIDI route — has carried
# _CLAP_AVOID_WINDOW = 0.15 since the start, with the note that the
# mandatory downbeat is exempt because the first-hit law outranks clap
# avoidance. This route never had it, and it is the only one of the two
# that does not. Measured 2026-08-19, kicks landing within 40 ms of a clap:
# Sahara 4 %, Graveyard 6 %, Detroit 14 %, Shuttle 21 %, deyflo 35 %.
#
# Applied as a PREFERENCE between otherwise-legal grid slots, never as a
# rejection that could strand a filler: midi_placement_symbiosis already
# turns a true coincidence into one unison attack, so a kick that ends up
# on the clap is no longer the flam it used to be. This only stops the
# arithmetic from putting one there when a clear slot was equally available.
_BACKBEAT_AVOID_WINDOW = 0.15

# How close in front of a melody attack a MANUFACTURED kick may land.
#
# Not a new opinion about fusion — the same one the project issue log (not included) 0s.2 already
# measured and shipped: a sixteenth apart, two attacks smear into one
# thickened event instead of reading as two, and an eighth apart they do not,
# which is why the deliberate double moved from 0.25 to 0.5. So the window is
# a sixteenth with a small margin, in BEATS, and it tracks tempo the way that
# measurement did. A fixed 120 ms was tried first and missed Graveyard's case
# by 17 ms — at 120 BPM a sixteenth is already 125 ms.
#
# A gap filler is chosen by arithmetic on a time budget, so it lands wherever
# that budget falls; on Tragic it fell 109-112 ms in front of the melody's own
# attack in 12 bars (the project issue log (not included) 0w).
_MELODY_FUSION_BEATS = 0.3

# The sections the low end may not rest in, and may not be displaced off the
# bar line in. Kept as names here because _active_lowend_bars hands this module
# the arrangement's own label; drum_density.PEAK_SECTIONS is the same set and
# the same owner — this is a local alias for the string form, not a second
# definition.
_PEAK_SECTION_NAMES = frozenset({"hook", "chorus", "drop"})

# How far into a bar a melody accent may pull the manufactured foundation off
# the bar line. One beat: far enough to read as a displaced downbeat, close
# enough that the bar still opens on the low end rather than on nothing.
# Measured 2026-08-20 before this existed: 83-100 % of every song's bars
# opened with a kick exactly on the "1", and BatonRouge was at 100 %.
_ANCHOR_DISPLACE_WINDOW = 1.0

_DRUM_SHAPES: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {
    "statement": ((0.0, 1.5, 3.0, 5.5), (2.0, 6.0)),
    "answer": ((0.0, 0.75, 3.25, 4.75, 6.75), (2.0, 6.0)),
    "space": ((0.0, 2.5, 4.0, 6.0), (2.0, 6.0)),
    "lift": ((0.0, 1.5, 3.0, 5.5, 7.0), (2.0, 6.0, 7.5)),
    "landing": ((0.0, 0.75, 2.5, 4.0, 6.5), (2.0, 6.0, 7.5)),
}

_POCKET_SHAPES: dict[str, tuple[float, ...]] = {
    "statement": (0.0, 1.5, 3.0, 5.5),
    "answer": (0.0, 0.75, 3.25, 4.75, 6.75),
    "space": (0.0, 2.5, 4.0, 6.0),
    "lift": (0.0, 1.5, 3.0, 5.5, 7.0),
    "landing": (0.0, 0.75, 2.5, 4.0, 6.5),
}


def _too_close(point: float, kick_times: list[float]) -> bool:
    """Would a kick here read as a roll against one already placed?"""
    return any(abs(float(point) - other) < MIN_LOWEND_GAP_BEATS - 1e-6 for other in kick_times)


def _phrases(plan: dict[str, Any]) -> list[dict[str, Any]]:
    raw = plan.get("phrases")
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, dict)]


def _role(phrase: dict[str, Any]) -> str:
    role = str(phrase.get("role") or "statement")
    return role if role in _POCKET_SHAPES else "statement"


def _kick_note(t: float, role: str, template: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "time_step": round(t, 5),
        "note": str((template or {}).get("note") or "C5"),
        "length": 0.28,
        "velocity": _KICK_VEL,
        "kick_808_lock": True,
        "phrase_role": role,
        "midi_phrase_composer": True,
    }


def _scale_pcs(root_pc: int, scale: str) -> set[int]:
    return {(int(root_pc) + iv) % 12 for iv in SCALES.get(scale, SCALES["natural_minor"])}


def _snap_pc(pc: int, pcs: set[int]) -> int:
    pc %= 12
    if not pcs or pc in pcs:
        return pc
    return min(pcs, key=lambda s: (min((s - pc) % 12, (pc - s) % 12), s))


def _pc_at_time(
    t: float,
    root_pc: int,
    roots_pc: list[int],
    pcs: set[int],
    harmonic_rhythm: list[dict[str, Any]] | None = None,
    root_map: Any = None,
) -> int:
    """The agreed harmonic target for an 808 attack at ``t``.

    ``root_map`` (midi_lowend_director.LowendRootMap) is the shared resolver
    every consumer — generator, corrector and both export audits — now reads,
    so a bar with no confident window can no longer mean three different
    roots. The window-then-progression reading below is kept verbatim as the
    fallback for callers that predate the map.

    ``tone_at`` rather than ``root_at`` since 2026-08-16: the map owns the
    degree as well as the harmony, and every one of those consumers reads the
    same method. Without the map (the legacy fallback below) the answer stays
    the root, exactly as before.
    """
    if root_map is not None:
        pc, _tier, _degree = root_map.tone_at(t)
        if pc is not None:
            return int(pc) % 12
    for window in harmonic_rhythm or []:
        if not isinstance(window, dict):
            continue
        try:
            start = float(window["start"])
            end = float(window["end"])
            local_root = int(window["root_pc"]) % 12
            confidence = float(window.get("confidence", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if start <= t < end and confidence >= 0.48:
            return _snap_pc(local_root, pcs)
    roots = [int(pc) % 12 for pc in (roots_pc or [root_pc])]
    bar = max(0, int(t // BEATS_PER_BAR))
    return _snap_pc(roots[bar % len(roots)], pcs)


def _bass_note(
    t: float,
    next_t: float,
    role: str,
    *,
    root_pc: int,
    roots_pc: list[int],
    scale_pcs: set[int],
    harmonic_rhythm: list[dict[str, Any]] | None = None,
    root_map: Any = None,
) -> dict[str, Any]:
    pc = _pc_at_time(t, root_pc, roots_pc, scale_pcs, harmonic_rhythm, root_map)
    length = min(BASS_NOTE_LENGTH, max(MIN_BASS_LENGTH, next_t - t))
    return {
        "time_step": round(t, 5),
        "note": name_for_midi(LOWEND_808_LO + pc),
        "length": round(length, 5),
        "velocity": _BASS_VEL,
        "pan": 64,
        "kick_lock": True,
        "bass808_gen": True,
        "phrase_role": role,
        "midi_phrase_composer": True,
    }


def _snap_onsets_to_melody(
    onsets: list[tuple[float, str]],
    melody_accents: list[float] | None,
    *,
    start: float,
    end: float,
) -> list[tuple[float, str]]:
    if not onsets or not melody_accents:
        return onsets
    accents = sorted(
        round(float(t), 5)
        for t in melody_accents
        if isinstance(t, (int, float)) and start <= float(t) < end
    )
    if not accents:
        return onsets

    out: list[tuple[float, str]] = []
    used: set[float] = set()
    for t, role in sorted(onsets, key=lambda item: item[0]):
        nearest = min(accents, key=lambda a: abs(a - t))
        snapped = nearest if abs(nearest - t) <= 0.12 and nearest not in used else t
        if not any(abs(snapped - prev) < 0.45 for prev, _prev_role in out):
            out.append((round(snapped, 5), role))
            used.add(round(snapped, 5))
    return out


def _assign_lowend_roles(
    ordered: list[tuple[float, str]],
    phrases: list[dict[str, Any]],
    *,
    anchors: dict[str, Any] | None = None,
    melody_accents: list[float] | None = None,
) -> list[tuple[float, str, str]]:
    """Turn phrase attacks into one kick+808 skeleton.

    Most attacks are paired — the pair is the sound of this genre. The
    exceptions are the two moments where a real producer splits them, and both
    require an audible event from the ALREADY-FINAL kit to justify them
    (``anchors`` — see midi_lowend_director.rhythmic_anchors):

    * ``bass_only`` — the 808 moves under a snare/clap hit. The backbeat masks
      the change, which is what makes it read as a chord move rather than a
      stray bass note. Also kept for the old late-answer-of-a-sentence case.

    Capped at one per 4-bar window so the pocket keeps its identity and the
    export gates' density/pairing floors are never approached.

    Deliberately NOT emitted here: a ``kick_only`` push (kick with no 808).
    The kick and the 808 start together by contract — the 808's job is to keep
    ringing THROUGH the next kick, which the sustained tail already does, so
    splitting the attack buys nothing and costs the pair its weight. The rest
    of the pipeline still understands the role (see
    beat_humanize._intentional_solo_lowend) for engines that do emit it.
    """
    phrase_starts = [round(float(p.get("start", 0.0) or 0.0), 3) for p in phrases]
    boundaries = list((anchors or {}).get("boundary_times") or [])
    result: list[tuple[float, str, str]] = []
    kick_times: list[float] = []
    kicks_per_bar: dict[int, int] = {}
    solo_per_window: dict[int, int] = {}
    for time, phrase_role in ordered:
        time = float(time)
        bar = max(0, int(time // BEATS_PER_BAR))
        window = int(time // 16.0)
        is_phrase_start = any(abs(time - start) <= 0.16 for start in phrase_starts)
        is_four_bar_answer = bar % 4 == 3 and time % BEATS_PER_BAR >= 2.75
        on_boundary = any(abs(time - point) <= 0.15 for point in boundaries)
        has_solo_room = solo_per_window.get(window, 0) == 0
        kick_room = (
            kicks_per_bar.get(bar, 0) < 2
            and (not kick_times or time - kick_times[-1] >= MIN_LOWEND_GAP_BEATS)
        )
        if is_phrase_start or kicks_per_bar.get(bar, 0) == 0:
            lowend_role = "paired"
        elif is_four_bar_answer:
            lowend_role = "bass_only"
        elif on_boundary and has_solo_room and kicks_per_bar.get(bar, 0) >= 1:
            lowend_role = "bass_only"
        elif kick_room:
            lowend_role = "paired"
        else:
            continue
        if lowend_role == "paired":
            kick_times.append(time)
            kicks_per_bar[bar] = kicks_per_bar.get(bar, 0) + 1
        if lowend_role != "paired":
            solo_per_window[window] = solo_per_window.get(window, 0) + 1
        result.append((time, phrase_role, lowend_role))
    return result


def _off_drag_sixteenth(point: float, taken: set[float]) -> float:
    """Move a low-end onset off the sixteenth that drags behind a beat.

    The one rule both writers of a manufactured low-end position share.
    Anticipating a strong beat is a groove ("x.75"); arriving just behind it
    reads as a late kick. See _grid_fill_point for the corpus measurement that
    established it — deyflo shipped 48 of 121 kicks that way and the user heard
    that file as wrong rather than dull.

    ``taken`` is every position already claimed, so this never stacks two
    onsets on one instant; when the beat before is occupied the drag position
    stays, because a duplicate would be worse than a late kick.
    """
    inside = round((float(point) % 1.0) * 4.0) / 4.0
    if abs(inside - _DRAG_SIXTEENTH) > 1e-9:
        return point
    on_beat = round(float(point) - _DRAG_SIXTEENTH, 5)
    return on_beat if on_beat >= 0.0 and on_beat not in taken else point


def _quantize_lowend_onsets(onsets: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Keep kick/808 on a musical sixteenth-note grid.

    The melody can be played loosely; the low-end cannot inherit arbitrary
    0.03-beat offsets from it. Sixteenth-note resolution keeps bounce/fills
    without producing visibly or audibly random kick positions.
    """
    quantized: dict[float, str] = {}
    for time, role in sorted(onsets, key=lambda row: float(row[0])):
        point = round(round(float(time) * 4.0) / 4.0, 5)
        # The grid alone is not enough: quantising a loosely played
        # onset can land it on the sixteenth that drags behind a beat,
        # which is the one position measured to sound like a mistake
        # rather than a groove. Same rule the gap filler applies.
        point = _off_drag_sixteenth(point, set(quantized))
        quantized.setdefault(point, role)
    return sorted(quantized.items(), key=lambda row: row[0])


def _section_for_time(arrangement: list[dict[str, Any]] | None, time: float) -> dict[str, Any] | None:
    """Return the real song section owning ``time`` (not a source-MIDI guess)."""
    for section in arrangement or []:
        if not isinstance(section, dict):
            continue
        start = float(section.get("start_bar", 0) or 0) * BEATS_PER_BAR
        end = start + float(section.get("bars", 0) or 0) * BEATS_PER_BAR
        if start <= time < end:
            return section
    return None


def _active_lowend_bars(arrangement: list[dict[str, Any]] | None, start: float, end: float) -> list[tuple[int, str]]:
    """Bars where the arrangement explicitly permits the kick+808 pocket.

    This prevents the later low-end pass from silently refilling an intro or
    outro after the arranger deliberately left it empty.  Old sessions used
    ``sub_808`` while new MIDI sessions use ``808_bass``; accept either while
    the project migrates, but require kick as the shared impact voice.
    """
    # A standalone unit composition has no song-form authority.  Keep its
    # original phrase result untouched; the density contract is an export-time
    # arrangement feature, not a hidden global grid.
    if not isinstance(arrangement, list):
        return []
    rows: list[tuple[int, str]] = []
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        active = section.get("active_tracks")
        if not isinstance(active, list) or "kick" not in active or not ({"808_bass", "sub_808"} & set(active)):
            continue
        first = int(section.get("start_bar", 0) or 0)
        count = int(section.get("bars", 0) or 0)
        name = str(section.get("name") or "verse")
        for bar in range(first, first + max(0, count)):
            if start <= bar * BEATS_PER_BAR < end:
                rows.append((bar, name))
    return rows


def _lowend_bar_allowance(
    *, arrangement: list[dict[str, Any]] | None, briefs: list[dict[str, Any]] | None,
    start: float, end: float, density: dict[str, Any] | None,
    placed: list[tuple[float, str, str]] | None = None,
) -> dict[int, int]:
    """How full the MANUFACTURED passes may leave each bar — including zero.

    The single owner of that question, 2026-08-20, because two passes need the
    same answer and they run one after the other: ``_enforce_section_pocket``
    decides whether a bar is anchored at all, ``_fill_time_based_lowend_gaps``
    decides how far it may then be padded. When they disagreed the second one
    simply refilled what the first had deliberately left alone.

    Three readings compose here, none of them new:

    * the section, via ``drum_density.section_pocket_ceiling`` — one onset
      outside hook/chorus/drop, two inside;
    * the phrase position, via the shared per-bar brief — the bar that ENDS a
      phrase gets the peak allowance wherever it is in the song. That is the
      phrase arc the 2026-08-20 audit measured as absent: the kick's change
      rate was flat across every position in the 4-bar block (BatonRouge
      100/100/100/100, Tragic 94/100/100/94), i.e. every bar was decided in
      isolation. The backbeat has had this gesture since the start
      (_PHRASE_EXIT_OFFSET); the low end never did;
    * a bounded REST — a body bar the pass may leave silent, so the pattern
      breathes. Measured before this existed: 0 empty bars out of 52-72 on all
      five corpus files, i.e. the kick never once stopped.

    The rest is deliberately hard to earn. Never in a peak section, never in a
    phrase entry or exit bar, never twice in a row, never in the first active
    bar, and never when it would take the 4-bar window's own allowance below
    four — one above ``min_pairs_per_4bar``, so the export gate keeps a margin
    rather than being walked up to its edge. On material sparse enough that
    the card switched ``anchor_every_active_bar`` off, no rest is added at
    all: that path already leaves bars empty for its own reason and does not
    need a second opinion.

    ``placed`` is the composer's own reading so far, and the rest may only
    fall in a bar it left empty. Measured while building this: without that
    condition the six bars chosen on Sahara were all bars the composer had
    already spoken in, so the rest silenced nothing and the feature read as
    dead. A bar the material says something in is not the bar to stop in —
    the one worth stopping in is the one that would otherwise carry a
    manufactured tick and nothing else.
    """
    from drum_density import section_pocket_ceiling

    rows = _active_lowend_bars(arrangement, start, end)
    if not rows:
        return {}
    by_bar = {int(row.get("bar", -1)): row for row in (briefs or []) if isinstance(row, dict)}
    anchor_all = True if density is None else bool(density.get("anchor_every_active_bar", True))

    spoken: dict[int, int] = {}
    for time, _role, kind in (placed or []):
        if kind in ("paired", "kick_only"):
            bar = int(float(time) // BEATS_PER_BAR)
            spoken[bar] = spoken.get(bar, 0) + 1
    peak_ceiling = section_pocket_ceiling("hook")

    allowance: dict[int, int] = {}
    history: list[int] = []
    for bar, section_name in sorted(rows):
        ceiling = section_pocket_ceiling(section_name)
        brief = by_bar.get(bar) or {}
        position = str(brief.get("phrase_position") or "")
        if ceiling < peak_ceiling:
            # The quiet sections alternate instead of sitting flat at one
            # (2026-08-21). A flat ceiling made "kick on the one, nothing
            # else" the single most common bar of four of the five listening
            # files — 24-37 % of each song — so the contrast the section
            # ceiling bought was paid for with a new monotony. The bar is held
            # at the low ceiling while the material said nothing in it and the
            # section has not already been quiet for two bars running; on the
            # third it opens back up. The section step itself is unchanged:
            # the peak never drops, so the hook keeps every hit it had.
            #
            # TWO bars, not one. A one-bar alternation was tried first and
            # measured on 2026-08-21: it lifted the verse to 1.52-1.62 and
            # took the hook-vs-verse contrast to 1.12 on Tragic and 1.21 on
            # deyflo, under the 1.25 floor this work exists to hold. Two bars
            # keeps the mean near 1.33 and the contrast above it.
            #
            # A bar the material DID speak in is allowed exactly what the
            # material said — not the peak ceiling. Lifting it all the way was
            # the first attempt and it measured wrong: on the two files whose
            # composer fills nearly every verse bar (deyflo 1.06 onsets/bar,
            # Tragic 1.59) that clause fired everywhere, the filler padded
            # each of those bars back up to two, and the contrast collapsed to
            # 1.21 and 1.12. This map bounds the MANUFACTURED passes; letting
            # a composer onset raise their ceiling inverted that.
            if bar in spoken:
                ceiling = max(ceiling, min(peak_ceiling, spoken[bar]))
            elif all(value != peak_ceiling for value in history[-2:]):
                ceiling = peak_ceiling
        if position == "exit" and int(brief.get("phrase_bars", 0) or 0) >= 2:
            ceiling = max(ceiling, peak_ceiling)
        allowance[bar] = ceiling
        history.append(ceiling)

    if not anchor_all:
        return allowance

    ordered = sorted(allowance)
    first = ordered[0]
    for bar in ordered:
        if bar == first or bar in spoken:
            continue
        brief = by_bar.get(bar) or {}
        if str(brief.get("phrase_position") or "") != "body":
            continue
        if str(brief.get("section") or "") in _PEAK_SECTION_NAMES:
            continue
        if allowance.get(bar - 1, 1) == 0:
            continue
        window = (bar // 4) * 4
        planned = sum(allowance.get(b, 0) for b in range(window, window + 4))
        if planned - allowance[bar] < 4:
            continue
        allowance[bar] = 0
    return allowance


def _enforce_section_pocket(
    roles: list[tuple[float, str, str]], *, arrangement: list[dict[str, Any]] | None,
    start: float, end: float, density: dict[str, Any] | None = None,
    allowance: dict[int, int] | None = None,
    briefs: list[dict[str, Any]] | None = None,
    melody_accents: list[float] | None = None,
) -> list[tuple[float, str, str]]:
    """Guarantee a low-end foundation in every active bar. Nothing else.

    At 160 BPM a single kick every two bars sounds half-time.  Each active bar
    therefore gets one paired foundation if the phrase reading left it empty.

    This pass used to ALSO add an alternating kick-only push in hook/drop bars
    above 150 BPM, as its half of the hook-vs-verse contrast. That was the
    second owner of a decision ``drum_density.section_pocket_ceiling`` now owns
    alone (2026-08-20), and it was the worse of the two: locked behind a tempo
    gate, it fired 10/6/2 times on the three fast corpus files and ZERO times
    on the two slow ones, so half the corpus had no section contrast at all.
    Removed with its ``bpm`` argument, which nothing else here used.

    The per-bar floor is unconditional only while the material can carry it.
    On genuinely sparse MIDI it was the direct cause of a reproduced export
    failure: a bar the melody is silent in has no confident harmonic window
    either, so the manufactured foundation put an 808 on a root nothing in the
    material supports and the final gate rejected the whole generation. When
    ``density`` says the material is that sparse, a silent bar is left to the
    already-ringing 808 unless the pocket has genuinely drifted past its own
    maximum gap. Busy material keeps the old behaviour exactly.
    """
    from drum_density import bar_has_melody

    result = list(roles)
    occupied = {round(float(time), 5) for time, _role, _kind in result}
    kick_by_bar: dict[int, int] = {}
    for time, _role, kind in result:
        if kind in ("paired", "kick_only"):
            bar = int(float(time) // BEATS_PER_BAR)
            kick_by_bar[bar] = kick_by_bar.get(bar, 0) + 1

    anchor_all = True if density is None else bool(density.get("anchor_every_active_bar", True))
    try:
        max_gap = float((density or {}).get("max_gap_beats") or 0.0)
    except (TypeError, ValueError):
        max_gap = 0.0
    if max_gap <= 0.0:
        max_gap = BEATS_PER_BAR
    lowend_times = sorted(
        float(time) for time, _role, kind in result if kind in ("paired", "kick_only")
    )

    by_bar = {int(row.get("bar", -1)): row for row in (briefs or []) if isinstance(row, dict)}
    accents = sorted(
        float(point) for point in (melody_accents or []) if isinstance(point, (int, float))
    )

    for bar, _section_name in _active_lowend_bars(arrangement, start, end):
        bar_start = bar * BEATS_PER_BAR
        # Always anchor the bar.  If the MIDI has an onset near this point it
        # will already occupy it; otherwise this is the intentional drummer's
        # downbeat, not a random extra note.
        if kick_by_bar.get(bar, 0) == 0:
            # A bar the shared allowance marked as a rest is left silent, and
            # the gap filler reads the same map, so it cannot refill it.
            if allowance is not None and allowance.get(bar, 1) <= 0:
                continue
            previous = max((time for time in lowend_times if time < bar_start), default=None)
            drifted = previous is None or (bar_start - previous) >= max_gap
            if anchor_all or drifted or bar_has_melody(density, bar):
                point = _displaced_anchor_point(
                    bar_start,
                    brief=by_bar.get(bar),
                    accents=accents,
                    kick_times=lowend_times,
                )
                if point not in occupied and not _too_close(point, lowend_times):
                    result.append((point, "foundation", "paired"))
                    occupied.add(point)
                    kick_by_bar[bar] = 1
                    lowend_times.append(point)
                    lowend_times.sort()
    return sorted(result, key=lambda row: row[0])


def _displaced_anchor_point(
    bar_start: float, *, brief: dict[str, Any] | None,
    accents: list[float], kick_times: list[float],
) -> float:
    """Where this bar's manufactured foundation lands — the "1", or near it.

    The bar line is the default and stays the default. A BODY bar of a phrase
    may instead open on a melody accent inside its first beat, which is the
    displaced downbeat a drummer plays rather than the metronome tick this
    pass used to emit in every bar it touched.

    Deliberately never in a phrase ENTRY or EXIT bar: those two are where the
    form is heard, and the backbeat's own phrase gesture already keys off the
    exit. Deliberately never off the sixteenth grid, and never on the drag
    sixteenth — the same rule ``_grid_fill_point`` applies to the other kind of
    manufactured onset, read from the same owner rather than restated.
    """
    home = round(float(bar_start), 5)
    if not accents:
        return home
    position = str((brief or {}).get("phrase_position") or "")
    if position != "body":
        return home
    if str((brief or {}).get("section") or "") in _PEAK_SECTION_NAMES:
        return home
    # Quantise BEFORE testing the window, not after. The accent list carries
    # the melody's own humanisation — measured 2026-08-20, every corpus file's
    # accents sit 0.024-0.042 beats late — so an accent on beat two reads as
    # 1.03 and a raw window test of (0, 1.0] found literally zero candidates
    # on all five files. This is the same order the rest of the module works
    # in: _grid_fill_point quantises at birth for exactly this reason.
    window = [
        round(round(point * 4.0) / 4.0, 5) for point in accents
        if bar_start - 0.25 < point <= bar_start + _ANCHOR_DISPLACE_WINDOW + 0.25
    ]
    window = [
        point for point in window
        if bar_start + 1e-6 < point <= bar_start + _ANCHOR_DISPLACE_WINDOW + 1e-6
    ]
    if not window:
        return home
    candidate = min(window)
    if _off_drag_sixteenth(candidate, set()) != candidate:
        return home
    if _too_close(candidate, kick_times):
        return home
    return candidate


# The one sixteenth a manufactured kick must not land on: the one immediately
# AFTER a beat. Anticipating a strong beat is a groove ("x.75"); arriving just
# behind it reads as a late kick, not as syncopation.
_DRAG_SIXTEENTH = 0.25


def _prefer_clear_of_backbeat(
    point: float, kick_times: list[float], backbeat_times: list[float] | None,
    occupied_times: list[float] | None = None,
    accents: list[float] | None = None,
) -> float:
    """Nudge a manufactured onset off a backbeat hit when a clear slot fits.

    A preference, not a rule — see _BACKBEAT_AVOID_WINDOW. The bar downbeat
    is exempt: the first-hit law outranks clap avoidance, the same exemption
    bass808_gen states for its own window.
    """
    if not backbeat_times:
        return point
    if abs(float(point) % BEATS_PER_BAR) <= 1e-9:
        return point
    def _clashes(candidate: float) -> bool:
        return any(abs(candidate - u) <= _BACKBEAT_AVOID_WINDOW for u in backbeat_times)
    if not _clashes(point):
        return _pull_onto_backbeat_push(point, kick_times, backbeat_times, occupied_times, accents)
    # An eighth earlier reads as the anticipation the technique describes;
    # later would drag, which _off_drag_sixteenth exists to prevent.
    for step in (0.5, 0.25):
        candidate = round(float(point) - step, 5)
        if candidate <= 0.0 or _clashes(candidate):
            continue
        if _off_drag_sixteenth(candidate, set()) != candidate:
            continue
        if not _too_close(candidate, kick_times):
            return candidate
    return point


# How far ahead of a backbeat the anticipation sits, and how far a
# manufactured onset may be moved to reach it.
#
# An EIGHTH, not a sixteenth, and the reason is already measured and already
# shipped: at 140-160 BPM a sixteenth is 94-107 ms, inside the window where a
# kick's own decay is still sounding and the ear fuses the two attacks into one
# thickened hit. That is exactly why the deliberate double hit moved from 0.25
# to 0.5 in variant B (the project issue log (not included) 0s) after being listened to in FL. The same
# arithmetic applies to a kick anticipating a snare, so the same interval is
# used rather than a second opinion about it.
_BACKBEAT_PUSH_OFFSET = 0.5
_BACKBEAT_PUSH_REACH = 1.0


def _pull_onto_backbeat_push(
    point: float, kick_times: list[float], backbeat_times: list[float] | None,
    occupied_times: list[float] | None = None,
    accents: list[float] | None = None,
) -> float:
    """Turn a free-floating manufactured onset into an anticipation (2026-08-20).

    The other half of ``_prefer_clear_of_backbeat``. That function has always
    known how to move a kick OFF a backbeat it collided with; it had no opinion
    at all about a kick that was merely near one, so manufactured onsets landed
    wherever the gap arithmetic put them. Measured on the five-file listening
    corpus before this existed: 61-87 % of every song's kicks sat more than an
    eighth from any backbeat — "independent" — and deliberate anticipations ran
    at 1-8 % on four of the five files.

    A preference between legal slots, never a new onset and never a rejection.
    The bar downbeat is exempt (the caller checks it), a kick already sitting
    on the backbeat is left to the collision half above, and the push is
    refused when it would land on the drag sixteenth, break minimum spacing, or
    cross into another backbeat's own window.

    Two things this has to get right, both found by the corpus regression the
    first version broke (Detroit, 2026-08-20):

    * the backbeat's OWN time is humanised — apply_pre_snare_shift moves it a
      few ms — so the anticipation is measured from the backbeat's grid
      position, not from its shifted one. Subtracting an eighth from 73.98896
      put a manufactured kick at 73.48896, off every grid this module works on;
    * ``occupied_times`` is every low-end onset already placed, not just the
      kicks. A bass_only onset carries no kick yet, but a later pass can pair
      one to it, and a push that landed an eighth away from one shipped a
      kick_roll the export gate rejects.
    """
    if not backbeat_times:
        return point
    ahead = [
        beat for beat in backbeat_times
        if point + 1e-6 < beat <= point + _BACKBEAT_PUSH_REACH + 1e-6
    ]
    if not ahead:
        return point
    target = round(round(min(ahead) * 4.0) / 4.0, 5)
    crowded = any(
        0.05 < abs(point - beat) < _BACKBEAT_PUSH_OFFSET - 1e-6 for beat in backbeat_times
    )
    others = [t for t in (occupied_times or []) if abs(t - point) > 1e-6]

    def _usable(value: float) -> bool:
        if value <= 0.0 or abs(value - point) <= 1e-6:
            return False
        if abs(value % BEATS_PER_BAR) <= 1e-9:
            return False
        if any(abs(value - beat) <= _BACKBEAT_AVOID_WINDOW for beat in backbeat_times):
            return False
        if _off_drag_sixteenth(value, set()) != value:
            return False
        return not _too_close(value, kick_times) and not _too_close(value, others)

    # Exactly one step: the eighth before the backbeat, or nothing.
    #
    # A second step (a whole beat before) was written, measured and REMOVED on
    # 2026-08-21. It was for a real case — on Graveyard the clap owns the
    # eighth before the snare in 23 % of bars, and in five of them a filler is
    # left stranded on the sixteenth before the stack with 125 ms of air on
    # each side. But moving a filler a whole beat earlier leaves the gap
    # walker more room behind it, so it manufactures another onset: BatonRouge
    # gained 15 kicks, its verse went 1.54 -> 1.81 a bar and its hook-vs-verse
    # contrast collapsed from 1.30 to 1.10. One rescued bar is not worth a
    # file's section contrast. Do not re-add it without new numbers; the five
    # stranded bars are recorded in the project issue log (not included) 0u.
    candidate = round(target - _BACKBEAT_PUSH_OFFSET, 5)
    if not _usable(candidate):
        return point
    # The push must have a reason, and there are exactly two (2026-08-21).
    #
    # It shipped with neither. Measured on Graveyard: all 16 of the pushes it
    # made were manufactured fillers, and 12 of them landed where the melody
    # has no accent at all — the low end moved to the eighth before the
    # backbeat purely because a backbeat was there. That is the defect class
    # commit e844d91 is named after ("stop the low end landing where nothing
    # happens"), in a milder form, reintroduced by me.
    #
    # 1. the target carries a melody accent — the positive, musical case. The
    #    0.13 tolerance is the one every other low-end owner already uses
    #    (_vary_repeated_lowend_cycle, _cap_midi_kicks_per_bar,
    #    roll_take_verdict); this reads it rather than inventing a fourth.
    # 2. the onset ALREADY sits closer than an eighth to the backbeat — the
    #    defensive case. Moving out of the fusion zone is an improvement
    #    whether or not the melody says anything, and this clause is what
    #    keeps Graveyard's sixteenth-before-the-backbeat collision at zero.
    on_accent = any(abs(candidate - float(a)) <= 0.13 for a in (accents or ()))
    if not on_accent and not crowded:
        return point
    return candidate


_MELODY_HIT_TOLERANCE = 0.06


def _smears_into_melody(
    point: float, melody_onsets: list[float] | None, fusion_beats: float,
    melody_spans: list[tuple[float, float]] | None = None,
) -> bool:
    """Would a manufactured kick here be heard as a jab at nothing?

    Three questions in order, because the first two are the reasons a filler
    IS wanted and only the third is the defect:

    * does it land ON a melody attack — the kit answering the material;
    * does it land where the melody is silent — the hole this pass exists for;
    * otherwise, does it land in the stretch just IN FRONT of an attack, where
      the two fuse and the kick reads as a smear rather than a hit.

    Only the third is refused. An earlier version asked the third question
    alone and was measured too blunt: deyflo has no unsupported kick at all and
    still lost 23 % of its lane to it, Graveyard 22 % for nothing.
    """
    if not melody_onsets or fusion_beats <= 0.0:
        return False
    if any(abs(point - o) <= _MELODY_HIT_TOLERANCE for o in melody_onsets):
        return False
    if melody_spans is not None and not any(
        start - _MELODY_HIT_TOLERANCE <= point < end - 0.02 for start, end in melody_spans
    ):
        return False
    for onset in melody_onsets:
        ahead = onset - point
        if 1e-6 < ahead <= fusion_beats:
            return True
        if ahead > fusion_beats:
            break
    return False


def _fill_onto_the_melody(
    point: float, melody_onsets: list[float] | None, fusion_beats: float,
    kick_times: list[float], backbeat_times: list[float] | None,
    melody_spans: list[tuple[float, float]] | None = None,
) -> float | None:
    """Move a smearing filler forward ONTO the attack it was crowding.

    Refusing outright was measured first and is too blunt: the pass exists to
    stop the low end going silent, and simply dropping every smearing slot cost
    deyflo 23 % of its kicks and Graveyard 22 %. The melody attack a beat ahead
    is a better place for the hit than the arithmetic's own slot anyway — the
    kick then lands WITH the material instead of a breath in front of it — so
    take that when it is legal, and only give the slot up when it is not.

    Returns the moved point, or None when nothing legal is within reach.
    """
    if not melody_onsets:
        return None
    ahead = [o for o in melody_onsets if 1e-6 < o - point <= fusion_beats]
    if not ahead:
        return None
    candidate = round(round(min(ahead) * 4.0) / 4.0, 5)
    if candidate <= 0.0 or _too_close(candidate, kick_times):
        return None
    if _off_drag_sixteenth(candidate, set()) != candidate:
        return None
    if backbeat_times and any(
        abs(candidate - beat) <= _BACKBEAT_AVOID_WINDOW for beat in backbeat_times
    ):
        return None
    if _smears_into_melody(candidate, melody_onsets, fusion_beats, melody_spans):
        return None
    return candidate


def _grid_fill_point(raw: float, kick_times: list[float],
                     backbeat_times: list[float] | None = None,
                     occupied_times: list[float] | None = None,
                     accents: list[float] | None = None) -> float:
    """Put a manufactured gap-filler on the musical grid, off the drag slot.

    ``_quantize_lowend_onsets`` is this module's owner of "kick/808 live on a
    sixteenth grid", and its docstring says why: the melody may be played
    loosely, the low end may not inherit arbitrary offsets from it. But it
    runs BEFORE the two density passes, so everything they manufacture
    afterwards skips it. ``_enforce_section_pocket`` survives that by luck —
    it only ever writes ``bar_start`` and ``bar_start + 1.5``, both on the
    grid already — while the gap filler writes ``cursor + max_gap_beats``,
    and ``max_gap_beats`` is a TIME budget in seconds converted to beats. Which
    subdivision it lands on is pure arithmetic.

    Measured 2026-08-19 across the listening corpus, that is exactly the
    difference between a file that merely sounds dull and one that sounds
    broken:

        Sahara     max_gap 2.600 -> 2.50   the "and" of beat 3   (fine)
        Graveyard  max_gap 1.643 -> 1.75   anticipates beat 3    (fine)
        Detroit    max_gap 1.846 -> 1.75                         (fine)
        Shuttle    max_gap 1.533 -> 1.50                         (fine)
        deyflo     max_gap 2.212 -> 2.25   a 16th LATE           (broken)

    deyflo shipped 48 of its 121 kicks that way, only 55 % of its bars had a
    kick on beat one at all, and none of those late kicks coincided with
    anything the melody plays — while the melody WAS playing on the beat they
    had left. The user heard that file as wrong rather than boring.

    So: quantise here rather than leaving it to the export seal (which rounds
    to the NEAREST sixteenth and therefore preserved 2.25), and when the
    result is the drag slot, take the beat just before it instead. Falling
    back to the drag position is allowed when the beat is inside another
    kick's minimum spacing — a slightly late kick beats a spacing violation
    the export gate would reject.
    """
    point = round(round(float(raw) * 4.0) / 4.0, 5)
    moved = _off_drag_sixteenth(point, set())
    if moved == point:
        return _prefer_clear_of_backbeat(point, kick_times, backbeat_times, occupied_times, accents)
    # Spacing is this writer's own extra guard: a filler dropped onto
    # the beat must still clear the kicks already placed.
    return moved if moved > 0.0 and not _too_close(moved, kick_times) else point


def _fill_time_based_lowend_gaps(
    roles: list[tuple[float, str, str]], *, arrangement: list[dict[str, Any]] | None,
    start: float, end: float, bpm: float, density: dict[str, Any] | None = None,
    backbeat_times: list[float] | None = None,
    allowance: dict[int, int] | None = None,
    accents: list[float] | None = None,
    melody_onsets: list[float] | None = None,
    melody_spans: list[tuple[float, float]] | None = None,
) -> list[tuple[float, str, str]]:
    """Keep the *combined* kick/808 pocket moving in real elapsed time.

    A bar-count rule makes the same four-beat gap twice as long in a fast
    production as it feels in a slower one.  The limit here is 0.75 seconds,
    converted to beats only at export time.  Fillers belong to the 808 alone:
    The filler is a paired push, never a disconnected bass note.

    The 0.75 s rule knows the tempo but not the material, and at a slow tempo
    it clamps UP to a full beat — which is how a 68 BPM ballad ended up with a
    kick every bar it could fit one in. ``density`` supplies the spacing the
    melody actually implies, and the wider of the two wins: this pass may
    become sparser than the old fixed rule, never denser, so every busy file
    keeps the pocket it already had.

    How full a bar this pass may leave is ``_lowend_bar_allowance`` (2026-08-20)
    — the shared per-bar map ``_enforce_section_pocket`` decided the bar's
    foundation from. It used to be the literal 2 here, which is exactly why
    the verse ended up as full as the hook, and why a bar the anchor pass
    deliberately left silent would be refilled a moment later.
    """
    if not isinstance(arrangement, list):
        return sorted(roles, key=lambda row: row[0])
    max_gap_beats = max(1.0, min(3.0, 0.75 * max(60.0, float(bpm)) / 60.0))
    try:
        from_density = float((density or {}).get("max_gap_beats") or 0.0)
    except (TypeError, ValueError):
        from_density = 0.0
    max_gap_beats = max(max_gap_beats, from_density)
    fusion_beats = _MELODY_FUSION_BEATS
    result = list(roles)
    occupied = {round(float(time), 5) for time, _role, _kind in result}
    kick_by_bar: dict[int, int] = {}
    for time, _role, kind in result:
        if kind == "paired":
            bar = int(float(time) // BEATS_PER_BAR)
            kick_by_bar[bar] = kick_by_bar.get(bar, 0) + 1
    # Walking forward in fixed steps can leave the last filler a fraction of a
    # beat before the onset it was walking towards. Measured: a filler at
    # 107.25 against the next bar's foundation at 108.0 — 0.75 apart, which the
    # export gate rejects as a kick_roll. Track every kick so the filler obeys
    # the same minimum spacing as the rest of the module.
    kick_times = [float(time) for time, _role, kind in result if kind in ("paired", "kick_only")]
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        active = section.get("active_tracks")
        if not isinstance(active, list) or "kick" not in active or not ({"808_bass", "sub_808"} & set(active)):
            continue
        lo = max(start, float(section.get("start_bar", 0) or 0) * BEATS_PER_BAR)
        hi = min(end, lo + float(section.get("bars", 0) or 0) * BEATS_PER_BAR)
        if hi <= lo + 0.5:
            continue
        section_times = sorted(float(time) for time, _role, _kind in result if lo <= float(time) < hi)
        cursor = lo
        for point in section_times + [hi]:
            while point - cursor > max_gap_beats + 1e-6:
                # On the grid at birth — see _grid_fill_point. The old
                # `cursor + max_gap_beats` inherited a time budget's
                # arbitrary fraction and skipped _quantize_lowend_onsets
                # entirely, because that owner runs before this pass.
                fill = _grid_fill_point(
                    cursor + max_gap_beats, kick_times, backbeat_times,
                    # Every low-end onset already placed, kicks and bass-only
                    # alike — see _pull_onto_backbeat_push for why bass-only
                    # counts here.
                    [float(time) for time, _role, _kind in result],
                    accents,
                )
                fill_bar = int(fill // BEATS_PER_BAR)
                # The shared per-bar map, not a literal 2 and not this pass's
                # own idea of the section: a bar the anchor pass rested on
                # reads 0 here and can never be refilled (2026-08-20).
                bar_ceiling = 2 if allowance is None else allowance.get(fill_bar, 0)
                # The pass exists to stop the low end going silent for too
                # long — not to fill at any cost. A slot chosen by arithmetic
                # that happens to land a breath in front of a melody attack
                # buys no pocket and costs a smear, so it is simply not taken.
                if _smears_into_melody(fill, melody_onsets, fusion_beats, melody_spans):
                    moved = _fill_onto_the_melody(
                        fill, melody_onsets, fusion_beats, kick_times, backbeat_times,
                        melody_spans,
                    )
                    if moved is None:
                        # Nothing legal within reach: give the slot up rather
                        # than jab at a moment the material never asked for.
                        cursor = fill
                        continue
                    fill = moved
                    fill_bar = int(fill // BEATS_PER_BAR)
                    bar_ceiling = 2 if allowance is None else allowance.get(fill_bar, 0)
                if (
                    fill not in occupied
                    and kick_by_bar.get(fill_bar, 0) < bar_ceiling
                    and not _too_close(fill, kick_times)
                ):
                    result.append((fill, "time_pocket", "paired"))
                    occupied.add(fill)
                    kick_by_bar[fill_bar] = kick_by_bar.get(fill_bar, 0) + 1
                    kick_times.append(fill)
                cursor = fill
            cursor = max(cursor, point)
    return sorted(result, key=lambda row: row[0])


def _accent_driven_onsets(
    *,
    phrase_start: float,
    phrase_end: float,
    role: str,
    melody_accents: list[float] | None,
    window_start: float,
    window_end: float,
    density: dict[str, Any] | None = None,
) -> list[tuple[float, str]]:
    from drum_density import scaled_shape

    shape = scaled_shape(_POCKET_SHAPES[role], density)
    fallback = [
        round(phrase_start + float(offset), 5)
        for offset in shape
        if window_start <= phrase_start + float(offset) < min(phrase_end, window_end) - 1e-6
    ]
    if not melody_accents:
        return [(t, role) for t in fallback]

    target = len(fallback) or len(shape)
    accents = sorted(
        round(float(t), 5)
        for t in melody_accents
        if isinstance(t, (int, float))
        and max(window_start, phrase_start) <= float(t) < min(phrase_end, window_end) - 1e-6
    )
    def _complete(seed: list[float]) -> list[float]:
        chosen: list[float] = []
        for point in seed + fallback:
            if len(chosen) >= target:
                break
            if any(abs(point - prev) < 0.75 for prev in chosen):
                continue
            chosen.append(point)
        return sorted(chosen)

    # Candidate A preserves the proven direct reading of an expressive MIDI:
    # early/obvious attacks can be the whole point of a phrase.
    direct = _complete(accents)

    # Candidate B is a phrase-aware interpretation.  It searches around the
    # role's natural pocket positions, but only adopts a real MIDI accent when
    # it is close enough to still sound intentional.  This saves MIDIs whose
    # first four notes all happen in beat one from yielding a front-loaded
    # kick pattern for every two-bar phrase.
    shaped_seed: list[float] = []
    unused = list(accents)
    for pocket_point in fallback:
        nearby = [accent for accent in unused if abs(accent - pocket_point) <= 1.0]
        if not nearby:
            continue
        chosen = min(nearby, key=lambda accent: (abs(accent - pocket_point), accent))
        shaped_seed.append(chosen)
        unused.remove(chosen)
    shaped = _complete(shaped_seed)

    # Candidate C deliberately spreads sparse strong onsets through the
    # phrase.  It is not a new static grid: every selected point still comes
    # from the uploaded melody.  Farthest-first avoids the generic failure of
    # turning a busy arpeggio's opening into all of the kick pattern.
    spread_seed: list[float] = []
    remaining = list(accents)
    while remaining and len(spread_seed) < target:
        if not spread_seed:
            point = min(remaining, key=lambda accent: (abs(accent - phrase_start), accent))
        else:
            point = max(
                remaining,
                key=lambda accent: (min(abs(accent - existing) for existing in spread_seed), -accent),
            )
        if all(abs(point - existing) >= 0.75 for existing in spread_seed):
            spread_seed.append(point)
        remaining.remove(point)
    spread = _complete(spread_seed)

    def _score(candidate: list[float]) -> float:
        if not candidate:
            return -999.0
        # Accent ownership is mandatory; phrase coverage and role-pocket
        # closeness decide between otherwise legal interpretations.
        accent_hits = sum(any(abs(point - accent) <= 1e-5 for accent in accents) for point in candidate)
        bins = len({min(3, int((point - phrase_start) // 2.0)) for point in candidate})
        pocket_distance = sum(min(abs(point - grid) for grid in fallback) for point in candidate) / len(candidate)
        has_opening = any(point < phrase_start + 0.40 for point in candidate)
        has_late_answer = any(point >= phrase_end - 2.0 for point in candidate)
        return (
            accent_hits * 2.0
            + bins * 0.8
            + (0.35 if has_opening else 0.0)
            + (0.25 if has_late_answer else 0.0)
            - pocket_distance * 0.25
        )

    # Stable tie order keeps already-good MIDIs bit-identical; an alternate
    # reading only wins when it is objectively better distributed.
    chosen = max((direct, shaped, spread), key=_score)
    return [(t, role) for t in chosen]


# The backbeat palette. One bar's figure is four independent decisions, and
# their combinations cover the ten schemes the brief was written against:
#
#   stack   which lanes sound on the anchor   -> schemes 1, 2, 3, 7
#   pickup  a layer ghost a 16th before it    -> scheme 4
#   tail    a ghost late in the bar           -> schemes 5, 6, 8, 10
#   skip    no anchor in this bar at all      -> scheme 9
#
# THE ANCHOR NEVER MOVES. Wherever a backbeat sounds it sounds on
# _BACKBEAT_BAR_OFFSET, because that is the one thing the 2026-08-21 audit
# found already right: 100 % of permitted bars on all five listening files.
# What was wrong is that it was the ONLY thing the lane ever did — the clap's
# position vocabulary over a whole song measured 1-2, and hook/drop came out
# 0.60-1.42x the verse, THINNER than the verse on three of five files.
#
# Keyed on phrase geometry and the material's own melody density, deliberately
# NOT on the section label or the tension curve: measured across the five
# files, the dominant section's share differs between files by 7 points and
# the whole corpus's tension spans 0.086-0.120, so a chooser reading either
# would hand every file the same handwriting — the §0u.5 failure, one lane up.
# phrase_position and phrase_bars are the live discriminators (spread 25
# points; BatonRouge and Tragic have two-bar phrases and no `body` bar at all,
# Sahara and Graveyard are 75 % body).
_BACKBEAT_STACK_FULL = ("clap", "snare", "layer")
_BACKBEAT_STACK_PAIR = ("clap", "snare")
_BACKBEAT_STACK_CLAP = ("clap",)


def _backbeat_figure(
    row: dict[str, Any], *, late_floor: int | None = None
) -> dict[str, Any]:
    """What this bar's backbeat plays. The single owner of that decision.

    ``late_floor`` is the song's own median melody onsets IN THE SECOND HALF
    OF THE BAR, not an absolute number: the five files sit at 4-6 whole-bar
    onsets and an absolute threshold would fire on every bar of one file and
    none of another. Same self-calibrating rule
    ``apply_melody_drum_density_inverse`` states in its own docstring.

    The whole-bar count was tried here first and removed: Graveyard and
    BatonRouge carry exactly 6 onsets in every bar and Tragic exactly 4, so a
    median split on it is false in every bar of four files out of five — a
    dead lever of the same kind as the tension curve.
    """
    section = str(row.get("section") or "verse").strip().lower()
    peak = section in _PEAK_SECTION_NAMES
    position = str(row.get("phrase_position") or "body")
    phrase_bars = int(row.get("phrase_bars") or 1)
    into = int(row.get("bars_into_phrase") or 0)
    role = str(row.get("role") or "statement")
    onsets = row.get("melody_onsets_late")
    onsets = len(onsets) if isinstance(onsets, list) else int(onsets or 0)

    # --- A. the stack -------------------------------------------------------
    # A peak section and a phrase's own last bar are the two moments the brief
    # calls for the full weight. Everywhere else the trio is subtracted from,
    # not added to — the same "contrast is made by subtraction" finding the
    # kick round recorded, for the same reason: there is no headroom above the
    # full stack, so the quiet places have to give something up.
    # `phrase_bars >= 3`, not `>= 2`: in a two-bar phrase every second bar is
    # an exit, so the lift would land on half the song — measured at the >= 2
    # threshold, BatonRouge and Tragic (both two-bar-phrase files) carried the
    # full trio in 64-65 % of their bars. A short phrase's exit still gets its
    # gesture, but as the tail ghost below, which is what the brief actually
    # asks for at a phrase turn ("лучше добавить один доп. hit в конце 2-го
    # такта"), not as the full weight of a hook.
    if peak or (position == "exit" and phrase_bars >= 3):
        stack = _BACKBEAT_STACK_FULL
    elif late_floor is not None and onsets > late_floor and position == "body":
        # "если clap уже жирный, main snare может быть тише или вообще не
        # нужен" — where the melody crowds the half of the bar the backbeat
        # lands in, the backbeat gets out of its way instead of stacking a
        # third transient into the same register.
        stack = _BACKBEAT_STACK_CLAP
    else:
        stack = _BACKBEAT_STACK_PAIR

    # --- B. the pickup ------------------------------------------------------
    # A quiet layer ghost a 16th before the anchor. The brief files this under
    # "специи, не основа"; it was measured firing in 90-100 % of every song's
    # bars, which is what made the layer 57-62 % independent of the main hit.
    # It needs two things to be a gesture rather than a texture: a phrase turn
    # to lead into ("акцент только в конце 4-го или 8-го такта"), and air in
    # the half of the bar it sits in.
    # `phrase_bars >= 3` for the same reason the stack carries it: in a
    # two-bar phrase every second bar is an exit, and keying the pickup on
    # that alone put it in 50 % of BatonRouge's and Tragic's bars while their
    # layer's own hits sat at 33 %, leaving the lane MORE independent of the
    # main hit than before, not less (measured 38-40 % coincident, down from
    # 47-48 %). Short-phrase material gets its phrase-turn gesture from the
    # tail below instead.
    #
    # The second clause counts in the SONG's bars, not the phrase's: "акцент
    # только в конце 4-го или 8-го такта" is a position in the form, and
    # `bars_into_phrase % 4` can never reach 3 inside a two-bar phrase.
    quiet_late = late_floor is None or onsets <= late_floor
    bar_index = int(row.get("bar", 0) or 0)
    pickup = quiet_late and (
        (position == "exit" and phrase_bars >= 3) or (bar_index % 4) == 3
    )

    # --- C. the tail --------------------------------------------------------
    # A quiet snare answer late in the bar — "лучше добавить один доп. snare
    # hit в конце 2-го или 4-го такта". This replaces the `lift`/`landing`
    # ROLE whitelist that used to guard the same offset: measured across the
    # ten-file corpus the role `lift` never occurs at all, so Sahara and
    # Graveyard shipped 0 phrase-exit gestures while the other three got
    # exactly 3 each. Phrase geometry decides it now.
    #
    # `bar % 4 == 3` on top of the exit for the third time in this function,
    # and for the third time for the same reason: without it a two-bar phrase
    # would take a tail in every second bar of the song.
    tail = (
        "snare"
        if position == "exit" and phrase_bars >= 2 and (bar_index % 4) == 3
        else None
    )
    return {
        "bar": bar_index,
        "stack": stack,
        "pickup": pickup,
        "tail": tail,
        "skip": False,
    }


def snare_gate_enabled() -> bool:
    import os

    return os.environ.get("PLG_SNARE_SECTIONS", "").strip().lower() not in ("0", "off", "false", "no")


def _gate_snare(
    figure: dict[str, Any], row: dict[str, Any], index: int, section_bars: int, seed: int | None
) -> dict[str, Any]:
    """Simpler, and different from section to section: WHICH bars of a section carry the snare.

    The anchor never moves (the clap keeps beat 3 in every permitted bar); only the snare - and the layer that doubles
    it - steps out of some bars, by the kind of section: an intro has none until its last bar, a verse every second bar
    (which parity moves with the seed), a hook or drop three bars of four (the bar left out moves with the seed, never
    the last bar of the four, which keeps its fill), an outro two bars. Before this, 56 of 72 bars carried the same
    snare on beat 3. Without a seed (or ``PLG_SNARE_SECTIONS=off``) nothing changes."""
    if seed is None or not snare_gate_enabled() or "snare" not in figure["stack"]:
        return figure
    section = str(row.get("section") or "verse").strip().lower()
    if section == "intro":
        keep = index == section_bars - 1
    elif section == "outro":
        keep = index < 2
    elif section in _PEAK_SECTION_NAMES:
        keep = (index % 4) != (seed // 2) % 3
    else:
        keep = (index + seed) % 2 == 0
    if keep:
        return figure
    stack = tuple(lane for lane in figure["stack"] if lane not in ("snare", "layer")) or _BACKBEAT_STACK_CLAP
    tail = None if figure.get("tail") == "snare" else figure.get("tail")
    return {**figure, "stack": stack, "tail": tail}


def _backbeat_bars(
    pattern: dict[str, Any], phrases: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Every bar that may carry a backbeat, as its own brief row.

    Returns the brief's rows verbatim (filtered to the bars the arrangement
    permits) rather than a 4-tuple, because ``_backbeat_figure`` reads five of
    their fields and re-packing them here would be a second, drifting copy of
    the per-bar contract.

    The offsets in ``_DRUM_SHAPES`` are phrase-relative and were written for a
    two-bar phrase (``PHRASE_BEATS``). They are never tiled, so on real
    material whose phrases run four or eight bars the backbeat stopped after
    the phrase's second bar and the rest of the phrase shipped with none at
    all — measured 2026-08-19 at 52 of 72 bars (Sahara) and 41 of 56
    (Graveyard), including four-bar stretches of a drop. The kick never showed
    this because ``_enforce_section_pocket`` rebuilds it per bar afterwards;
    snare/clap have no later owner.

    Permission comes from the shared per-bar brief
    (``midi_fullkit_director.bar_brief``), which reads it from the
    arrangement's own ``active_tracks`` — the same authority
    ``_active_lowend_bars`` uses for the low end. This pass therefore fills
    the gap between what the arrangement permitted and what the composer
    wrote; it does not decide for itself where a backbeat belongs.
    """
    from midi_fullkit_director import bar_brief

    brief = bar_brief(pattern)
    if brief:
        return [dict(row) for row in brief if row.get("backbeat_allowed")]
    # No brief: a standalone unit pattern with no song form and no full-kit
    # contract. Walk the phrases' own bars — there is no arrangement authority
    # to consult, so nothing is gated. The synthesised rows carry the same
    # keys the brief does, so _backbeat_figure has one shape to read.
    rows: list[dict[str, Any]] = []
    for phrase in phrases:
        start = float(phrase.get("start", 0.0) or 0.0)
        end = float(phrase.get("end", start + PHRASE_BEATS) or start + PHRASE_BEATS)
        role = _role(phrase)
        first = int(start // BEATS_PER_BAR)
        last = int(max(0.0, end - 1e-6) // BEATS_PER_BAR)
        span = max(1, last - first + 1)
        for bar in range(first, last + 1):
            if bar == first:
                position = "entry"
            elif bar == last:
                position = "exit"
            else:
                position = "body"
            rows.append({
                "bar": bar,
                "role": role,
                "phrase_position": position if span >= 2 else "exit",
                "phrase_bars": span,
                "section": "verse",
                "melody_onsets": 0,
                "strong_entry": False,
            })
    return rows


def apply_phrase_drums(pattern: dict[str, Any]) -> bool:
    plan = pattern.get("plg_phrase_rhythm_plan")
    if not isinstance(plan, dict) or not plan.get("enabled"):
        return False
    phrases = _phrases(plan)
    if not phrases:
        return False

    tracks = pattern.setdefault("tracks", {})
    arrangement = pattern.get("plg_arrangement")
    arrangement = arrangement if isinstance(arrangement, list) else None
    kick: list[dict[str, Any]] = []
    snare: list[dict[str, Any]] = []
    clap: list[dict[str, Any]] = []
    for phrase in phrases:
        start = float(phrase.get("start", 0.0) or 0.0)
        end = float(phrase.get("end", start + PHRASE_BEATS) or start + PHRASE_BEATS)
        role = _role(phrase)
        # Only the kick half of _DRUM_SHAPES is live. Its snare offsets were
        # superseded by the per-bar walk in 2026-08-19 and by the palette in
        # 2026-08-21; they are kept as the record of where the backbeat used
        # to come from, not read.
        kick_offsets = _DRUM_SHAPES[role][0]
        for offset in kick_offsets:
            t = start + float(offset)
            if t < end - 1e-6:
                kick.append(_kick_note(t, role, None))

    # The backbeat walks BARS, not one phrase-relative shape. See
    # _backbeat_bars for why, and for where the permission comes from.
    rows = _backbeat_bars(pattern, phrases)
    # The song's own middle, so "dense" means dense FOR THIS MATERIAL. Bars
    # with no melody at all are excluded from the median: they are rests, not
    # a quiet half of the distribution.
    onset_counts = sorted(
        n for n in (
            len(r["melody_onsets_late"]) if isinstance(r.get("melody_onsets_late"), list)
            else int(r.get("melody_onsets_late") or 0)
            for r in rows
        ) if n > 0
    )
    late_floor = onset_counts[len(onset_counts) // 2] if onset_counts else None

    # --- D. the skip -------------------------------------------------------
    # "иногда убрать clap на 1 удар перед дропом — сильнее, чем добавить
    # новый." The bar immediately before a strong entry gives up its backbeat
    # so the entry lands harder. Chosen here rather than in
    # apply_strong_phrase_answers' `clap_stops`, which owns the same idea and
    # has never once fired: it clears claps in [phrase_start - 0.5,
    # phrase_start), the clap only ever sounds on _BACKBEAT_BAR_OFFSET, and a
    # phrase always starts on a bar line — so its window covers a place a clap
    # cannot be. It also runs AFTER the layer is built and after the hats have
    # already chosen their roll targets from the backbeat, so removing a clap
    # there would strand a layer double and a roll pointing at nothing.
    #
    # Bounded: never the first bar of its section (there is nothing
    # established yet to take away), never two in a row, never the bar the
    # arranger's own beat switch already cuts, and at most one per section
    # occurrence.
    skip_bars: set[int] = set()
    by_bar = {int(r.get("bar", 0) or 0): r for r in rows}
    switch = pattern.get("plg_beat_switch")
    cut_bar = int(switch.get("cut_bar", -99)) if isinstance(switch, dict) else -99
    section_first: dict[tuple[str, int], int] = {}
    for row in rows:
        key = (str(row.get("section") or ""), int(row.get("section_occurrence") or 1))
        bar = int(row.get("bar", 0) or 0)
        if key not in section_first or bar < section_first[key]:
            section_first[key] = bar
    spent: set[tuple[str, int]] = set()
    for row in rows:
        bar = int(row.get("bar", 0) or 0)
        nxt = by_bar.get(bar + 1)
        if nxt is None:
            continue
        here = str(row.get("section") or "").strip().lower()
        there = str(nxt.get("section") or "").strip().lower()
        # The arrival is a SECTION boundary, read from the arrangement. The
        # phrase plan's own `strong_entry` was tried here and is the wrong
        # authority: it marks the first PHRASE whose section differs from the
        # previous phrase's, and on long-phrase material the two boundaries do
        # not coincide — on Sahara the hook starts at bar 12 while the phrase
        # that notices it starts at 16, so the skip landed in bar 15, four
        # bars INSIDE the hook, taking weight out of exactly the section it
        # exists to set up.
        if there not in _PEAK_SECTION_NAMES or here in _PEAK_SECTION_NAMES:
            continue
        key = (str(row.get("section") or ""), int(row.get("section_occurrence") or 1))
        if key in spent or section_first.get(key) == bar:
            continue
        if bar - 1 in skip_bars or bar in (cut_bar, cut_bar + 1):
            continue
        skip_bars.add(bar)
        spent.add(key)

    section_size: dict[tuple[str, int], int] = {}
    for row in rows:
        key = (str(row.get("section") or ""), int(row.get("section_occurrence") or 1))
        section_size[key] = section_size.get(key, 0) + 1
    try:
        from hihat_rolls import _variation_seed

        gate_seed = _variation_seed(pattern)
    except Exception:  # noqa: BLE001 - no seed, no gate
        gate_seed = None

    placed: set[float] = set()
    figures: dict[str, Any] = {}
    for row in rows:
        figure = _backbeat_figure(row, late_floor=late_floor)
        row_key = (str(row.get("section") or ""), int(row.get("section_occurrence") or 1))
        figure = _gate_snare(
            figure, row, int(row.get("bar", 0) or 0) - section_first.get(row_key, 0), section_size.get(row_key, 1), gate_seed
        )
        if figure["bar"] in skip_bars:
            figure = {**figure, "skip": True}
        bar = figure["bar"]
        role = str(row.get("role") or "statement")
        figures[str(bar)] = {
            "stack": list(figure["stack"]),
            "pickup": bool(figure["pickup"]),
            "tail": figure["tail"],
            "skip": bool(figure["skip"]),
        }
        if figure["skip"]:
            continue
        # (offset, which lanes sound there). The anchor carries the stack; the
        # tail is a single quiet ghost on one lane.
        events: list[tuple[float, tuple[str, ...], bool]] = [
            (_BACKBEAT_BAR_OFFSET, figure["stack"], False)
        ]
        if figure["tail"]:
            events.append((_PHRASE_EXIT_OFFSET, (figure["tail"],), True))
        for offset, lanes, is_ghost in events:
            t = round(bar * BEATS_PER_BAR + offset, 5)
            if t in placed:
                continue
            placed.add(t)
            # Same tag the kick/808 pocket stamps (_section_for_time): the
            # hat-roll eligibility gate keys off it to find tagged snares.
            # Without it beat_humanize never passed roll_windows and the
            # inverse-density roll design was dead on the MIDI route
            # (2026-08-15 audit).
            section = _section_for_time(arrangement, t)
            section_name = str(section.get("name") or "verse") if section is not None else None
            if "snare" in lanes:
                note = {"time_step": t, "note": "D1", "length": 0.28,
                        "velocity": _SNARE_VEL, "phrase_role": role}
                if is_ghost:
                    note["ghost"] = True
                    note["velocity"] = int(_SNARE_VEL * 0.55)
                if section_name is not None:
                    note["section"] = section_name
                snare.append(note)
            if "clap" in lanes:
                note = {"time_step": t, "note": "E1", "length": 0.22,
                        "velocity": _CLAP_VEL, "phrase_role": role}
                if is_ghost:
                    note["ghost"] = True
                    note["velocity"] = int(_CLAP_VEL * 0.55)
                if section_name is not None:
                    note["section"] = section_name
                clap.append(note)

    tracks["kick"] = sorted(kick, key=lambda n: float(n["time_step"]))
    tracks["snare"] = sorted(snare, key=lambda n: float(n["time_step"]))
    tracks["clap"] = sorted(clap, key=lambda n: float(n["time_step"]))
    # Published for the two later backbeat modifiers to READ rather than
    # re-decide: beat_humanize's layer block asks it whether this bar's stack
    # includes the layer, and whether the bar takes a pickup. They stay the
    # functions they were (duplicate_clap_layer / add_pre_snare_pickup); this
    # is only where the per-bar answer comes from.
    pattern["plg_backbeat_figures"] = figures
    pattern["plg_drum_grid"] = "midi_phrase_composer"
    return True


# The deliberate double: one onset rendered as a quieter pickup an EIGHTH
# before the full hit (0.5 beats; 200 ms at 120 BPM). The pickup started life
# at 0.25 — byte-exact in the FLP, but listened to in FL on four corpus files
# it read as a smeared retrigger, not a push: at 140-160 BPM a sixteenth is
# 94-107 ms, inside the window where a kick's own decay is still sounding and
# the ear fuses two attacks into one thickened hit. An eighth is outside that
# window and is trap's own classic anticipation figure, so the two taps read
# as two intentional hits. KICK-ONLY by design (2026-08-20, variant B): the
# 808 stays ONE ringing tail under both taps — a second 808 attack would
# break either the "808 is the tail" law or the mono-abut law (the two are
# mutually exclusive at double spacing; variant A tried to serve both and
# could not — see the project issue log (not included) §0s). The ghost therefore enters the roles
# list as "kick_only" and rides the existing solo-kick machinery:
# beat_humanize._intentional_solo_lowend recognizes it, so the pairing seal
# never rebuilds an 808 under it, _ensure_808_on_every_kick never mints one,
# and the pairing_ratio audit counts it as an intentional solo.
# See bass808_gen for the tags and why every spacing / ceiling / deletion
# owner had to learn them first.
#
# Deliberately NOT a fixed position or a fixed rate. It is offered only where
# the material already says something, and it is bounded twice — never in
# consecutive bars, and never in more than a third of the song's bars. The
# density law this module answers to counts ONSETS, and a double is still one
# of them, so this does not touch MAX_PAIRS_PER_BAR or the "less is more"
# decision recorded in bass808_gen.
_DOUBLE_GHOST_OFFSET = 0.5
_DOUBLE_MAX_BAR_SHARE = 1.0 / 3.0

# The same bound expressed in the unit the EAR uses (2026-08-21). A double is
# rare relative to how many low-end hits there are, not relative to how many
# bars the song has, and the two only agree while the pocket stays at a fixed
# density. When the 2026-08-20 placement work thinned the lane by 18-27 %, the
# bar-share budget did not move, so the number of doubles did not move either
# — and their share of what is actually heard went UP: measured across the
# five listening files, the fraction of all kick attacks belonging to a double
# rose from 26-29 % to 29-37 %, with the ceiling itself untouched at 1/3 of
# bars. The user heard a constant figure where the docstring promises a rare
# accent.
#
# 0.10 is one double per ten low-end onsets, chosen so the measured attack
# share lands in 15-20 % across the corpus rather than by taste. The bar-share
# bound above is KEPT and the tighter of the two wins: it is the documented
# ceiling for dense material and removing it would be a separate decision.
_DOUBLE_MAX_ONSET_SHARE = 0.10


def _plan_intentional_doubles(
    roles: list[tuple[float, str, str]],
    *,
    melody_accents: list[float] | None,
    backbeat_times: list[float] | None,
    density: dict[str, Any] | None,
    start: float,
    end: float,
    max_onset_share: float = _DOUBLE_MAX_ONSET_SHARE,
) -> tuple[list[tuple[float, str, str]], set[float], set[float]]:
    """Choose which onsets become doubles. Returns (roles, ghosts, mains).

    ``max_onset_share`` is the share of low-end onsets that may become doubles
    (default ``_DOUBLE_MAX_ONSET_SHARE``); the bar-share bound still applies on top.

    Conditions, all read from signals that already exist:

    * the hit lands on a melody accent — after the 2026-08-19 accent fix those
      are the material's own moments, not the constant "beats one and three"
      they used to be;
    * the bar has ROOM: its melody onset count is at or below the card's own
      average, the same "the kit answers with space" reading the hat budget
      uses. A crowded bar gets no double;
    * the ghost slot is on the grid, is not the sixteenth that drags behind a
      beat, and is clear of the backbeat and of the previous onset;
    * the MAIN hit is itself clear of the backbeat (same 0.15 window the
      ghost avoids) — doubling onto a snare/clap stack stacks the pickup on
      the layer's and reads as mush (2026-08-20, Graveyard Alt);
    * never two bars running, never more than a third of the bars AND never
      more than a tenth of the low-end onsets (2026-08-21 — the bar bound
      alone does not survive the pocket's density changing under it);
    * never the same ghost-to-main figure twice running (2026-08-21).
    """
    from drum_density import melody_attacks_per_bar

    if not roles or not melody_accents:
        return roles, set(), set()

    accents = sorted(round(float(a), 5) for a in melody_accents if isinstance(a, (int, float)))
    if not accents:
        return roles, set(), set()
    backbeat = [float(b) for b in (backbeat_times or [])]
    per_bar = (density or {}).get("melody_onsets_per_bar")
    per_bar = per_bar if isinstance(per_bar, list) else []
    room_ceiling = melody_attacks_per_bar(density)

    times = sorted(float(t) for t, _r, _lr in roles)
    occupied = {round(t, 5) for t in times}
    bars_available = max(1, len({int(t // BEATS_PER_BAR) for t in times}))
    # Tighter of the two bounds wins — see _DOUBLE_MAX_ONSET_SHARE for why one
    # counted in bars is not enough once the pocket's density can move.
    budget = min(
        int(bars_available * _DOUBLE_MAX_BAR_SHARE),
        int(len(times) * max_onset_share),
    )
    if budget <= 0:
        return roles, set(), set()

    ghosts: set[float] = set()
    mains: set[float] = set()
    last_double_bar = -99
    last_figure: tuple[int, int, bool] | None = None
    for index, (t, phrase_role, lowend_role) in enumerate(sorted(roles, key=lambda r: r[0])):
        if len(mains) >= budget:
            break
        if lowend_role not in ("paired", "kick_only"):
            continue
        point = float(t)
        bar = int(point // BEATS_PER_BAR)
        if bar <= last_double_bar + 1:
            # Never two bars running. Written `<= last_double_bar` first,
            # which only blocked a second double in the SAME bar and let
            # 9-12 adjacent pairs through on every corpus file.
            continue
        if not any(abs(point - a) <= 0.09 for a in accents):
            continue  # the material did not ask for anything here
        if any(abs(point - b) <= _BACKBEAT_AVOID_WINDOW for b in backbeat):
            # The MAIN hit itself must not double onto the backbeat: a
            # pickup a half-beat before a snare/clap stack lands in the
            # layer's pickup slot and the whole cluster reads as mush
            # (2026-08-20, Graveyard Alt). Same window the ghost avoids.
            continue
        if room_ceiling > 0 and 0 <= bar < len(per_bar):
            try:
                if int(per_bar[bar]) > room_ceiling:
                    continue  # this bar is already crowded
            except (TypeError, ValueError):
                pass
        ghost = round(point - _DOUBLE_GHOST_OFFSET, 5)
        if ghost <= start + 1e-6 or ghost >= end - 1e-6:
            continue
        if round(ghost, 5) in occupied:
            continue
        if _off_drag_sixteenth(ghost, set()) != ghost:
            continue  # the pickup would itself land on the drag sixteenth
        if any(abs(ghost - b) <= _BACKBEAT_AVOID_WINDOW for b in backbeat):
            continue
        previous = [u for u in times if u < ghost - 1e-9]
        if previous and ghost - previous[-1] < MIN_LOWEND_GAP_BEATS - 1e-6:
            continue  # the PAIR still owes the previous onset its full spacing
        # Not the same figure twice running (2026-08-21). A double is a
        # gesture, and a gesture repeated verbatim is a pattern: measured on
        # the five listening files, 57-100 % of every song's doubles were the
        # identical "pickup on the a of four, hit on the next bar's one", and
        # on Graveyard it was 13 out of 13. The budget alone would only have
        # made the same figure rarer, not less monotonous. A figure is free
        # again once a different one has been played.
        figure = (
            int(round((ghost % BEATS_PER_BAR) * 4.0)),
            int(round((point % BEATS_PER_BAR) * 4.0)),
            int(point // BEATS_PER_BAR) != int(ghost // BEATS_PER_BAR),
        )
        if figure == last_figure:
            continue
        last_figure = figure
        ghosts.add(ghost)
        mains.add(round(point, 5))
        occupied.add(ghost)
        last_double_bar = bar
        # kick_only, never paired: the double lives on the kick lane alone.
        # The builder below excludes kick_only onsets from bass_onsets, so no
        # 808 is born under the pickup — the bar's existing 808 tail keeps
        # ringing through both taps.
        roles = roles + [(ghost, phrase_role, "kick_only")]
    return sorted(roles, key=lambda row: row[0]), ghosts, mains


def build_phrase_kick_808_pocket(
    *,
    plan: dict[str, Any],
    start: float,
    end: float,
    root_pc: int,
    scale: str,
    roots_pc: list[int] | None,
    kick_template: dict[str, Any] | None = None,
    melody_accents: list[float] | None = None,
    melody_onsets: list[float] | None = None,
    melody_spans: list[tuple[float, float]] | None = None,
    accent_firmness: dict[str, Any] | None = None,
    director_meta: dict[str, Any] | None = None,
    pocket_mode: str = "general",
    harmonic_rhythm: list[dict[str, Any]] | None = None,
    bpm: float = 140.0,
    arrangement: list[dict[str, Any]] | None = None,
    anchors: dict[str, Any] | None = None,
    density: dict[str, Any] | None = None,
    root_map: Any = None,
    bar_briefs: list[dict[str, Any]] | None = None,
    double_max_onset_share: float = _DOUBLE_MAX_ONSET_SHARE,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Compose kick and 808 from phrase roles, with exact one-to-one pairing.

    ``double_max_onset_share`` caps the share of onsets that may become a deliberate
    double (see ``_plan_intentional_doubles``); the default is the long-standing 0.10.

    ``density`` is ``drum_density``'s card — how busy the uploaded MIDI
    actually is. Every density decision in this module reads it instead of a
    fixed shape/tempo constant; passing None restores the old fixed behaviour
    exactly, so a failure to build the card degrades rather than breaks.

    ``bar_briefs`` is ``midi_fullkit_director.bar_brief``'s stamped per-bar
    contract — the same rows the backbeat walks. The low end reads it for the
    phrase position only (2026-08-20, see ``_lowend_bar_allowance``); passing
    None keeps the section-only behaviour, with no phrase arc and no rests.

    ``melody_onsets`` is every melody attack, sorted — the manufactured gap
    filler reads it so it never drops a kick a breath in front of one
    (2026-08-25, ``_smears_into_melody``); passing None keeps the old
    behaviour.

    ``accent_firmness`` is ``melody_beat_symbiosis.melody_accent_firmness``'s
    card — how firmly the melody chose its own accents, bar by bar. The cell
    thinning reads it so a file whose material states a rhythm is not thinned
    out of it (2026-08-25); passing None restores the previous behaviour
    exactly.
    """
    if not isinstance(plan, dict) or not plan.get("enabled"):
        return None
    phrases = _phrases(plan)
    if not phrases:
        return None

    pcs = _scale_pcs(root_pc, scale)
    roots = [int(pc) % 12 for pc in (roots_pc or [root_pc])]
    try:
        from midi_lowend_director import compose_phrase_onsets

        onsets, audit = compose_phrase_onsets(
            phrases=phrases,
            pocket_shapes=_POCKET_SHAPES,
            melody_accents=melody_accents,
            start=start,
            end=end,
            pocket_mode=pocket_mode,
            bpm=bpm,
            # Kick/808 support the MIDI; they must not transcribe each dense
            # melodic phrase as a separate low-end rhythm.
            allow_direct_melody_reading=False,
            anchors=anchors,
            density_card=density,
            accent_firmness=accent_firmness,
        )
        if director_meta is not None:
            director_meta.update(audit)
    except Exception:
        # A director failure must never leave the user with silence.  The
        # local phrase composer below remains the deterministic safe route.
        onsets = []
        if director_meta is not None:
            director_meta.update({"version": 1, "passed": False, "reason": "director_exception"})
    if not onsets:
        for phrase in phrases:
            phrase_start = float(phrase.get("start", 0.0) or 0.0)
            phrase_end = float(phrase.get("end", phrase_start + PHRASE_BEATS) or phrase_start + PHRASE_BEATS)
            if phrase_end <= start + 1e-6 or phrase_start >= end - 1e-6:
                continue
            role = _role(phrase)
            onsets.extend(
                _accent_driven_onsets(
                    phrase_start=phrase_start,
                    phrase_end=phrase_end,
                    role=role,
                    melody_accents=melody_accents,
                    window_start=start,
                    window_end=end,
                    density=density,
                )
            )
    if not onsets:
        return None

    onsets = _snap_onsets_to_melody(onsets, melody_accents, start=start, end=end)
    onsets = _quantize_lowend_onsets(onsets)
    unique: dict[float, str] = {}
    for t, role in sorted(onsets, key=lambda item: item[0]):
        unique.setdefault(t, role)
    ordered = sorted(unique.items(), key=lambda item: item[0])

    roles = _assign_lowend_roles(
        ordered, phrases, anchors=anchors, melody_accents=melody_accents
    )
    active_bars = _active_lowend_bars(arrangement, start, end)
    if active_bars:
        allowed = {bar for bar, _section_name in active_bars}
        # Phrase analysis spans the melody through the outro, while the song
        # form intentionally mutes 808 there.  Filter source-derived onsets
        # before the density pass so forbidden notes can never be resurrected.
        roles = [
            row for row in roles
            if int(float(row[0]) // BEATS_PER_BAR) in allowed
        ]
    # Built once and handed to both density passes, so the second one cannot
    # disagree with the first about how full a bar is allowed to be.
    allowance = _lowend_bar_allowance(
        arrangement=arrangement, briefs=bar_briefs, start=start, end=end, density=density,
        placed=roles,
    )
    roles = _enforce_section_pocket(
        roles, arrangement=arrangement, start=start, end=end, density=density,
        allowance=allowance, briefs=bar_briefs, melody_accents=melody_accents,
    )
    roles = _fill_time_based_lowend_gaps(
        roles, arrangement=arrangement, start=start, end=end, bpm=float(bpm),
        density=density,
        backbeat_times=list((anchors or {}).get("boundary_times") or []),
        allowance=allowance,
        accents=[float(a) for a in (melody_accents or []) if isinstance(a, (int, float))],
        melody_onsets=melody_onsets,
        melody_spans=melody_spans,
    )
    roles, double_ghosts, double_mains = _plan_intentional_doubles(
        roles,
        melody_accents=melody_accents,
        backbeat_times=list((anchors or {}).get("boundary_times") or []),
        density=density,
        start=start,
        end=end,
        max_onset_share=double_max_onset_share,
    )
    kicks: list[dict[str, Any]] = []
    bass: list[dict[str, Any]] = []
    bass_onsets = [t for t, _phrase_role, lowend_role in roles if lowend_role != "kick_only"]
    for idx, (t, role, lowend_role) in enumerate(roles):
        if lowend_role in ("paired", "kick_only"):
            kick = _kick_note(t, role, kick_template)
            kick["lowend_role"] = lowend_role
            kick["kick_808_lock"] = lowend_role == "paired"
            # The two halves carry different tags: kick_roll inspects the
            # LATER note of a pair, the per-bar ceiling and the velocity
            # owner inspect the ghost. See bass808_gen.
            if round(t, 5) in double_ghosts:
                kick["intentional_double_ghost"] = True
            elif round(t, 5) in double_mains:
                kick["intentional_double"] = True
            section = _section_for_time(arrangement, t)
            if section is not None:
                kick["section"] = str(section.get("name") or "verse")
            kicks.append(kick)
        if lowend_role in ("paired", "bass_only"):
            later = [point for point in bass_onsets if point > t]
            bass_next = later[0] if later else end
            note = _bass_note(
                t, bass_next, role, root_pc=root_pc, roots_pc=roots,
                scale_pcs=pcs, harmonic_rhythm=harmonic_rhythm, root_map=root_map,
            )
            note["lowend_role"] = lowend_role
            note["kick_lock"] = lowend_role == "paired"
            section = _section_for_time(arrangement, t)
            if section is not None:
                note["section"] = str(section.get("name") or "verse")
            bass.append(note)

    return kicks, bass


def repair_active_kick808_coverage(
    *, kicks: list[dict[str, Any]], bass: list[dict[str, Any]], arrangement: list[dict[str, Any]] | None,
    root_pc: int, roots_pc: list[int] | None, scale: str, kick_template: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Fill only empty active 4-bar windows with paired legal pocket hits."""
    if not isinstance(arrangement, list):
        return kicks, bass, 0
    pcs = _scale_pcs(root_pc, scale)
    roots = [int(pc) % 12 for pc in (roots_pc or [root_pc])]
    result_kicks, result_bass, repairs = list(kicks), list(bass), 0
    # Same restrained 8-beat reading already used by the phrase composer.
    offsets = (0.0, 1.5, 3.0, 5.5, 8.0, 9.5, 11.0, 13.5)
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        active = section.get("active_tracks")
        if not isinstance(active, list) or "kick" not in active:
            continue
        start_bar = int(section.get("start_bar", 0) or 0)
        section_bars = int(section.get("bars", 0) or 0)
        for bar in range(start_bar, start_bar + section_bars, 4):
            window_start = bar * BEATS_PER_BAR
            window_end = min((bar + 4) * BEATS_PER_BAR, (start_bar + section_bars) * BEATS_PER_BAR)
            present = sum(window_start <= float(note.get("time_step", 0.0)) < window_end for note in result_kicks)
            if present >= 7:
                continue
            for offset in offsets:
                point = window_start + offset
                if point >= window_end - 1e-6 or present >= 7:
                    continue
                if any(abs(float(note.get("time_step", 0.0)) - point) < 0.35 for note in result_kicks):
                    continue
                result_kicks.append({**_kick_note(point, "coverage", kick_template), "midi_coverage_repair": True})
                result_bass.append({**_bass_note(point, point + 0.5, "coverage", root_pc=root_pc, roots_pc=roots, scale_pcs=pcs), "midi_coverage_repair": True})
                present += 1
                repairs += 1
    return (sorted(result_kicks, key=lambda note: float(note["time_step"])), sorted(result_bass, key=lambda note: float(note["time_step"])), repairs)


def backbeat_figure_for_bar(pattern: dict[str, Any], bar: int) -> dict[str, Any] | None:
    """This bar's backbeat figure, or None when the palette never ran.

    The only reader API for ``plg_backbeat_figures``. Later backbeat
    modifiers call it instead of re-deriving "should the layer sound here?"
    from the section or the phrase — that re-derivation is exactly how the
    lane ended up with five writers and no owner.
    """
    figures = pattern.get("plg_backbeat_figures")
    if not isinstance(figures, dict):
        return None
    row = figures.get(str(int(bar)))
    return row if isinstance(row, dict) else None


__all__ = [
    "apply_phrase_drums",
    "backbeat_figure_for_bar",
    "build_phrase_kick_808_pocket",
    "repair_active_kick808_coverage",
]

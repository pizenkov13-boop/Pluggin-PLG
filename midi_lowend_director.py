"""Song-level MIDI director for the paired kick/808 pocket.

This module deliberately makes decisions over the *whole arrangement*, not
one two-bar cell at a time.  Every candidate onset is still either an actual
melody accent or a legal role fallback; the director only decides which of
those legal readings produces the strongest evolving pocket.
"""
from __future__ import annotations

from typing import Any

PHRASE_BEATS = 8.0

# A hi-hat run this tight reads as a roll, not a pulse; a kick landing inside
# one is masked by it. Tolerance is how close an onset must be to count.
HAT_ROLL_GAP = 0.19
HAT_ROLL_TOLERANCE = 0.13
BOUNDARY_TOLERANCE = 0.25

BEATS_PER_BAR = 4.0

# --- 808 chord voicing (2026-08-16) -----------------------------------------
# Master switch for LowendRootMap's non-root slots. Off restores the pure
# root-on-every-attack behaviour exactly; see LowendRootMap._build_color_slots
# for why each bound below is what it is.
CHORD_VOICING_ENABLED = True
# A window shorter than this is a fragment (usually left by boundary snapping)
# and never gets a colour.
COLOR_MIN_WINDOW_BEATS = 3.0
# The colour lives in the window's last two beats...
COLOR_SLOT_BEATS = 2.0
# ...and never inside its first two, so the chord enters on its own root.
COLOR_LEAD_IN_BEATS = 2.0
# If those two rules leave less than this, there is no usable weak tail.
COLOR_MIN_SLOT_BEATS = 0.5
# How close to a bar line still counts as the downbeat.
DOWNBEAT_TOLERANCE = 0.13


def _step(note: dict[str, Any]) -> float:
    return float(note.get("time_step", 0.0) or 0.0)


def rhythmic_anchors(
    *,
    hats: list[dict[str, Any]] | None = None,
    snare: list[dict[str, Any]] | None = None,
    clap: list[dict[str, Any]] | None = None,
    melody: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read-only rhythmic map of the already-final kit, for the low end to answer.

    hats/snare/clap are generated and humanized several passes before kick/808
    (see beat_humanize.humanize_pattern) and are never modified from here —
    this only reads their onsets so the pocket can place itself around them:
    ``roll_times`` are hat-roll onsets a kick must not collide with,
    ``boundary_times`` are snare/clap hits that mark a phrase edge (where an
    808 root change is masked), ``pause_windows`` are the melody's own breaths.
    """
    # A ghost sixteenth (hihat_rolls.vary_hats) is air between eighths, not a roll: it must
    # not change where a kick may sit, so the low end reads the same line without it.
    ordered_hats = sorted((n for n in hats or [] if isinstance(n, dict) and not n.get("hat_ghost")), key=_step)
    roll_times: set[float] = set()
    for left, right in zip(ordered_hats, ordered_hats[1:]):
        if _step(right) - _step(left) <= HAT_ROLL_GAP:
            roll_times.update((round(_step(left), 5), round(_step(right), 5)))

    boundary_times = sorted(
        round(_step(n), 5)
        for n in list(snare or []) + list(clap or [])
        if isinstance(n, dict)
    )

    pause_windows: list[tuple[float, float]] = []
    if melody:
        try:
            from melody_beat_symbiosis import find_melody_micro_pauses

            pause_windows = find_melody_micro_pauses(melody)
        except Exception:  # noqa: BLE001
            pause_windows = []

    return {
        "roll_times": sorted(roll_times),
        "boundary_times": boundary_times,
        "pause_windows": pause_windows,
    }


class LowendRootMap:
    """The one agreed harmonic target for every 808 attack in a generation.

    ``_lowend_harmonic_windows`` already made generation, correction and the
    audits agree on *where* a chord change happens. What stayed private to
    each caller was the answer BELOW a confident window, and they disagreed:

    * the generator (``midi_phrase_drums._pc_at_time``) fell straight through
      to ``roots_pc[bar % len]``;
    * the corrector (``beat_humanize._correct_off_scale_808_notes``) carried
      the last confirmed root forward;
    * both audits read the lowest melody note in the bar, then
      ``roots_pc[bar % len]``.

    On busy material almost every bar is covered by a confident window, so the
    divergence never showed. On a sparse MIDI it is fatal: in a bar where the
    melody is silent the generator wrote one root, the corrector "fixed" it to
    another and the audit demanded a third, so the kick/808 gate could never
    pass and the whole generation was rejected — measured as exactly 8 of 64
    attacks, every one of them in a melody-silent bar.

    The tiers, in order:

    1. a confident chord window covering the attack;
    2. the lowest melody note sounding in that bar;
    3. the most recent of either, carried forward — the 808 keeps ringing the
       harmony that is actually sounding rather than inventing a new one;
    4. the pinned progression, for a lead-in before any material exists.

    ``tier`` is returned alongside the pitch class because the export audit
    stays deliberately more lenient on tier 4 (in-scale rather than on-root),
    exactly as it was before.

    ``tone_at`` (2026-08-16) is the same answer one step further: which DEGREE
    of that harmony the attack takes. See _build_color_slots.
    """

    __slots__ = (
        "_windows", "_bar_pc", "_roots", "_scale_pcs", "_events", "_colors",
        "_harmony_pcs",
    )

    def __init__(
        self,
        *,
        harmonic_windows: list[dict[str, Any]] | None,
        melody: list[dict[str, Any]] | None,
        roots_pc: list[int] | None,
        scale_pcs: set[int] | None = None,
        confidence_floor: float = 0.48,
        allow_chord_colors: bool = True,
    ) -> None:
        from music_theory import parse_note_name

        self._scale_pcs = {int(pc) % 12 for pc in (scale_pcs or ())}
        self._roots = [int(pc) % 12 for pc in (roots_pc or [])]

        confident: list[tuple[float, float, int, dict[str, Any]]] = []
        for window in harmonic_windows or []:
            if not isinstance(window, dict):
                continue
            try:
                if float(window.get("confidence", 0.0) or 0.0) < confidence_floor:
                    continue
                confident.append(
                    (
                        float(window["start"]),
                        float(window["end"]),
                        int(window["root_pc"]) % 12,
                        window,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        confident.sort(key=lambda row: (row[0], row[1], row[2]))
        self._windows: list[tuple[float, float, int]] = [
            (start, end, pc) for start, end, pc, _raw in confident
        ]
        self._colors: list[tuple[float, float, int, str]] = (
            self._build_color_slots(confident)
            if allow_chord_colors and CHORD_VOICING_ENABLED
            else []
        )

        # The pitch classes this map's own harmony actually uses: every
        # confident window's root plus the chord tones the analysis MEASURED
        # in that window. Built unconditionally — this is the harmonic model,
        # not the colour plan, so it does not follow CHORD_VOICING_ENABLED.
        # Read by beat_humanize._correct_808_vertical_clashes (2026-08-20) so
        # that a vetoed root cannot be replaced by a pitch class the harmony
        # never chose. See the harmony_pcs docstring.
        harmony: set[int] = set()
        for _start, _end, root_pc, raw in confident:
            harmony.add(root_pc)
            tones = raw.get("chord_pcs")
            if not isinstance(tones, dict):
                continue
            for name in ("third", "fifth"):
                tone = self._usable_tone(tones, name, root_pc)
                if tone is not None:
                    harmony.add(tone)
        self._harmony_pcs = harmony

        self._bar_pc: dict[int, int] = {}
        for note in melody or []:
            if not isinstance(note, dict):
                continue
            try:
                pitch = parse_note_name(str(note.get("note") or "C3"))
                bar = int(float(note.get("time_step", 0.0) or 0.0) // 4.0)
            except (TypeError, ValueError):
                continue
            if bar not in self._bar_pc or pitch < self._bar_pc[bar]:
                self._bar_pc[bar] = pitch
        self._bar_pc = {bar: pitch % 12 for bar, pitch in self._bar_pc.items()}

        events = [(start, pc) for start, _end, pc in self._windows]
        events.extend((bar * 4.0, pc) for bar, pc in self._bar_pc.items())
        self._events = sorted(events)

    def _usable_tone(self, tones: dict[str, Any], name: str, root_pc: int) -> int | None:
        """A chord tone this map is allowed to offer the 808, or None.

        Refused: a tone the analysis did not hear (None), the root itself, and
        anything outside the agreed key — the measurement says the melody
        really sounds it, the key says the 808 should not be the instrument
        that introduces it.
        """
        value = tones.get(name)
        if value is None:
            return None
        pc = int(value) % 12
        if pc == root_pc:
            return None
        if self._scale_pcs and pc not in self._scale_pcs:
            return None
        return pc

    def _build_color_slots(
        self, confident: list[tuple[float, float, int, dict[str, Any]]]
    ) -> list[tuple[float, float, int, str]]:
        """At most one non-root slot per chord window (2026-08-16).

        The 808 sitting on the root of every single attack is why the low end
        reads as correct-but-flat: the analysis knows a bar is A minor, and the
        bass says only "A". A producer says A ... A ... C on the way out. This
        grants exactly that, under conditions strict enough that the root line
        stays the recognizable one:

        * only inside a CONFIDENT chord window — a melody-bar / carry-forward /
          pinned-progression reading is already a guess, and colouring a guess
          is how the 808 starts sounding like it is playing a different song;
        * only a tone the melody ACTUALLY sounds in that window (measured by
          midi_structure_analysis._chord_quality, above its support floor) AND
          in the agreed key — never a chord symbol inferred from a template;
        * only in the window's weak tail (its last two beats, never within the
          first two), so the chord always enters on its own root;
        * never on a bar downbeat (enforced at query time in tone_at);
        * never in two consecutive windows, so a colour is an event rather
          than a texture.

        Which tone: the THIRD when the next window is a real root change — the
        classic hand-over, the bass leaving before the harmony does. Otherwise
        the FIFTH, the safest interior lift. Never the flat fifth (a tritone
        against the root) and never the seventh, which reads as a chord change
        rather than a colour on a lane that has no other harmony instrument.
        """
        slots: list[tuple[float, float, int, str]] = []
        previous_colored = False
        for index, (start, end, root_pc, raw) in enumerate(confident):
            if previous_colored:
                previous_colored = False
                continue
            if end - start < COLOR_MIN_WINDOW_BEATS:
                continue
            tones = raw.get("chord_pcs")
            if not isinstance(tones, dict):
                continue
            next_root = confident[index + 1][2] if index + 1 < len(confident) else None
            color_pc = degree = None
            if next_root is not None and next_root != root_pc:
                third = self._usable_tone(tones, "third", root_pc)
                if third is not None:
                    color_pc, degree = third, "third"
            if color_pc is None:
                fifth = self._usable_tone(tones, "fifth", root_pc)
                if fifth is not None:
                    color_pc, degree = fifth, "fifth"
            if color_pc is None or degree is None:
                continue

            slot_start = max(start + COLOR_LEAD_IN_BEATS, end - COLOR_SLOT_BEATS)
            if end - slot_start < COLOR_MIN_SLOT_BEATS:
                continue
            slots.append((slot_start, end, color_pc, degree))
            previous_colored = True
        return slots

    def _snap(self, pc: int) -> int:
        pc %= 12
        if not self._scale_pcs or pc in self._scale_pcs:
            return pc
        return min(
            self._scale_pcs,
            key=lambda candidate: (min((candidate - pc) % 12, (pc - candidate) % 12), candidate),
        )

    def root_at(self, step: float) -> tuple[int | None, str]:
        step = float(step)
        for start, end, pc in self._windows:
            if start <= step < end:
                return self._snap(pc), "window"
        bar = max(0, int(step // 4.0))
        if bar in self._bar_pc:
            return self._snap(self._bar_pc[bar]), "melody_bar"
        prior = [pc for time, pc in self._events if time <= step]
        if prior:
            return self._snap(prior[-1]), "carry"
        if self._roots:
            return self._snap(self._roots[bar % len(self._roots)]), "progression"
        return None, "unknown"

    def tone_at(self, step: float) -> tuple[int | None, str, str]:
        """The agreed 808 pitch class at ``step`` — root, or its one colour.

        THE call every 808 pitch consumer makes: the generator
        (midi_phrase_drums._pc_at_time), the pairing seal
        (beat_humanize._ensure_808_on_every_kick), the off-scale corrector,
        both export audits and the vertical repair. ``root_at`` is left intact
        underneath it and still answers "what harmony is this?" — tone_at
        answers "what note does this attack play?", and those are the same
        thing everywhere except inside a colour slot.

        Reading root_at here instead of duplicating its tier chain is the
        point: a consumer that called root_at while the generator called
        tone_at would reject the engine's own note, which is exactly the
        failure mode the project issue log (not included) §0.1 records.
        """
        pc, tier = self.root_at(step)
        if pc is None or tier != "window" or not self._colors:
            return pc, tier, "root"
        step = float(step)
        offset = step % BEATS_PER_BAR
        if min(offset, BEATS_PER_BAR - offset) <= DOWNBEAT_TOLERANCE:
            # Bar one belongs to the root, always. A colour here would not read
            # as a passing tone; it would read as the wrong bass note.
            return pc, tier, "root"
        for slot_start, slot_end, color_pc, degree in self._colors:
            if slot_start <= step < slot_end:
                return color_pc, tier, degree
        return pc, tier, "root"

    @property
    def color_slots(self) -> tuple[tuple[float, float, int, str], ...]:
        """Read-only view of the planned colour slots, for telemetry only."""
        return tuple(self._colors)

    @property
    def scale_pcs(self) -> frozenset[int]:
        """Read-only view of the agreed in-scale pitch classes (2026-08-15).

        New consumers of 808 pitch (the vertical consonance repair in
        ``beat_humanize._correct_808_vertical_clashes``) must read the same
        scale set the corrector and the audits use — never re-derive their
        own (`the project issue log (not included)` §0.1: disagreeing fallbacks are the most
        expensive bug class in this project's history).
        """
        return frozenset(self._scale_pcs)

    @property
    def harmony_pcs(self) -> frozenset[int]:
        """The pitch classes this map's harmony actually uses (2026-08-20).

        ``scale_pcs`` answers "is this note in the key?" — a seven-note set
        that says nothing about the chord under the attack. ``harmony_pcs``
        answers the narrower question "did this song's harmony ever choose
        this note?": the root of every confident window plus the third and
        fifth the analysis measured there (filtered by ``_usable_tone``, so
        never a chord symbol the melody does not sound and never out of key).

        Added for ``beat_humanize._correct_808_vertical_clashes``, whose last
        resort used to pick the nearest consonant SCALE pc to the note being
        replaced — a selector with no harmonic term at all, which is how a
        vetoed F# root shipped as G# under an F# window (`the project issue log (not included)`
        §0t). Consumers must read this set rather than rebuilding one from
        the windows themselves: a private chord-tone fallback next to this
        one is exactly the bug class §0.1 records.

        Empty when no window clears the confidence floor — callers must keep
        their existing behaviour in that case rather than refusing to choose.
        """
        return frozenset(self._harmony_pcs)


BOUNDARY_SNAP_BEATS = 0.75


def snap_harmonic_windows_to_boundaries(
    harmonic_rhythm: list[dict[str, Any]] | None,
    boundary_times: list[float] | None,
    *,
    max_shift: float = BOUNDARY_SNAP_BEATS,
) -> list[dict[str, Any]]:
    """Move each chord change onto the snare/clap hit that masks it.

    A root change landing in open air is heard as the bass wandering; the same
    change under a clap is heard as the arrangement turning a corner. Only the
    window edge moves — never a root — and only onto a real hit within
    ``max_shift``, so a chord with no percussive edge nearby keeps its
    analysed timing exactly.

    EVERY consumer of a harmonic window must apply this, generators and audits
    alike: a shifted change that generation plays but an audit still measures
    against the raw analysed edge reads as an off-root 808 (the same audit/
    enforcement-target discipline documented on
    beat_humanize._correct_off_scale_808_notes).
    """
    windows: list[dict[str, Any]] = [
        dict(window) for window in harmonic_rhythm or [] if isinstance(window, dict)
    ]
    points = sorted(float(t) for t in boundary_times or [])
    if len(windows) < 2 or not points:
        return windows
    try:
        windows.sort(key=lambda window: float(window["start"]))
    except (KeyError, TypeError, ValueError):
        return windows
    for index in range(1, len(windows)):
        previous, current = windows[index - 1], windows[index]
        try:
            edge = float(current["start"])
            previous_start = float(previous["start"])
            previous_end = float(previous["end"])
            current_end = float(current["end"])
        except (KeyError, TypeError, ValueError):
            continue
        nearest = min(points, key=lambda point: abs(point - edge))
        if abs(nearest - edge) > max_shift:
            continue
        # Never collapse or invert a window by dragging its edge past its own
        # far side (or past the previous window's start).
        if not (previous_start < nearest < current_end):
            continue
        current["start"] = nearest
        if abs(previous_end - edge) <= 1e-6:
            previous["end"] = nearest
    return windows


def in_hat_roll(time: float, roll_times: list[float] | set[float] | None) -> bool:
    return any(abs(float(time) - roll) <= HAT_ROLL_TOLERANCE for roll in roll_times or ())


def roll_may_take_kick(
    time: float,
    *,
    roll_times: list[float] | set[float] | None,
    accents: set[float] | list[float] | None,
    kicks_in_window: int = 0,
    min_kicks_in_window: int = 0,
    beats_per_bar: float = 4.0,
    accent_tolerance: float = 0.13,
) -> bool:
    """The single owner of "may this hi-hat roll delete this kick?".

    "Hats own the pulse": a kick buried inside a machine-gun roll is masked by
    it and reads as a stumble. That is a real rule and it stays — but it used
    to be written out separately in three places
    (``beat_humanize._seal_midi_kick808_pocket``,
    ``beat_humanize._clear_kicks_from_hat_rolls`` and, as a scoring penalty,
    ``compose_phrase_onsets``), each carrying its own copy of the exception
    list. Three copies of one decision is how §0.1 happened; this is the one
    they now call.

    It also gained the term none of the copies had. The rule was calibrated
    when the backbeat was sparse, so rolls were rare: ``hihat_rolls`` picks
    roll candidates FROM the backbeat, so tripling the backbeat triples the
    rolls. Measured 2026-08-19 on Graveyard after the per-bar backbeat landed:
    ``roll_times`` 60 -> 97, kick 98 -> 85, and bars left carrying a single
    kick 6 -> 19. Neutralising roll_times alone put the kick back at exactly
    98 with the new backbeat intact, so the whole cost was this rule firing
    more often, not the backbeat itself.

    A masked kick is a subtler groove; a 4-bar window thinned below what the
    material asks for is a hole, and no roll is worth one. The floor comes
    from ``drum_density.min_kicks_per_4bar`` — the card, never a constant.

    A per-BAR version of that term ("never take the last kick in a bar") was
    written here first and removed: measured across the corpus it never fired
    once, and it contradicted the recorded law
    ``tests/test_808_production_laws.py::
    test_kick_buried_in_a_hat_roll_is_cleared_with_its_808``, whose fixture is
    exactly a lone kick in its bar. The caller is expected to make the roll
    yield instead
    (``midi_fullkit_director.apply_lowend_roll_yield``), which keeps the
    audibility promise this rule exists for.
    """
    return roll_take_verdict(
        time,
        roll_times=roll_times,
        accents=accents,
        kicks_in_window=kicks_in_window,
        min_kicks_in_window=min_kicks_in_window,
        beats_per_bar=beats_per_bar,
        accent_tolerance=accent_tolerance,
    ) == "take"


def roll_take_verdict(
    time: float,
    *,
    roll_times: list[float] | set[float] | None,
    accents: set[float] | list[float] | None,
    kicks_in_window: int = 0,
    min_kicks_in_window: int = 0,
    beats_per_bar: float = 4.0,
    accent_tolerance: float = 0.13,
) -> str:
    """Why the roll may or may not have this kick. See roll_may_take_kick.

    ``take`` / ``no_roll`` / ``downbeat`` / ``accent`` / ``density_floor``.

    The reason matters to the caller: ``downbeat`` and ``accent`` kicks were
    never the roll's to take and have coexisted with rolls since this rule was
    written — nothing needs to change around them. ``density_floor`` is the new term, and it is the only one where the roll
    is being asked to give something up, so it is the only one that triggers
    ``midi_fullkit_director.apply_lowend_roll_yield``.
    """
    if not in_hat_roll(time, roll_times):
        return "no_roll"
    if abs(float(time) % beats_per_bar) <= 1e-5:
        return "downbeat"  # a downbeat carries its own meaning
    if any(abs(float(time) - float(accent)) <= accent_tolerance for accent in accents or ()):
        return "accent"  # so does a kick sitting on a melody accent
    if min_kicks_in_window and int(kicks_in_window) <= int(min_kicks_in_window):
        return "density_floor"
    return "take"


def at_phrase_boundary(
    time: float, boundary_times: list[float] | None, *, tolerance: float = BOUNDARY_TOLERANCE
) -> bool:
    return any(abs(float(time) - point) <= tolerance for point in boundary_times or ())


def in_melody_pause(time: float, pause_windows: list[tuple[float, float]] | None) -> bool:
    return any(start <= float(time) < end for start, end in pause_windows or ())


# Fast tempo alone may not resurrect onsets the material's density card just
# removed: additions require the card to agree (genuinely busy material).
# 2026-08-15 audit: quiet 150+ BPM files got the support onsets anyway, which
# inverted the density ordering (a sparse fast file ended up busier than a
# dense slow one). A missing card keeps the pre-card behaviour.
TEMPO_DENSE_MIN_DRIVE = 0.8


def _tempo_dense_fallback(
    shape: tuple[float, ...], bpm: float, card: dict[str, Any] | None = None
) -> list[float]:
    """Add a controlled support onset when an 8-beat phrase runs very fast."""
    points = list(shape)
    if bpm < 150.0:
        return points
    if isinstance(card, dict):
        try:
            drive = float(card.get("drive", 1.0) or 1.0)
        except (TypeError, ValueError):
            drive = 1.0
        if drive < TEMPO_DENSE_MIN_DRIVE:
            return points
    for candidate in (4.25, 7.0, 2.0, 6.0):
        if all(abs(candidate - point) >= 0.75 for point in points):
            points.append(candidate)
            break
    if bpm >= 170.0:
        for candidate in (2.0, 6.0, 1.0, 5.0):
            if all(abs(candidate - point) >= 0.75 for point in points):
                points.append(candidate)
                break
    return sorted(points)


def _phrase_fallback_points(
    *, phrase_start: float, phrase_end: float, shape: tuple[float, ...], start: float, end: float
) -> list[float]:
    """Repeat an 8-beat pocket shape across a genuinely longer phrase.

    The source analyser is allowed to find 4-bar phrases.  Treating its
    16-beat span as though it were the old 8-beat cell cut the kick/808
    density in half and created silent active sections on chord-heavy MIDI.
    """
    points: list[float] = []
    cursor = phrase_start
    while cursor < phrase_end - 1e-6:
        for offset in shape:
            point = cursor + offset
            if start <= point < min(cursor + PHRASE_BEATS, phrase_end, end) - 1e-6:
                points.append(round(point, 5))
        cursor += PHRASE_BEATS
    return points


def _spaced(points: list[float], fallback: list[float], target: int) -> list[float]:
    chosen: list[float] = []
    for point in points + fallback:
        if len(chosen) >= target:
            break
        if any(abs(point - prior) < 0.75 for prior in chosen):
            continue
        chosen.append(round(point, 5))
    return sorted(chosen)


def _candidates(
    *, start: float, end: float, fallback: list[float], accents: list[float], allow_direct: bool = True
) -> list[tuple[str, list[float]]]:
    target = len(fallback)
    # A dense melody is not a drum grid.  Letting a six-note arpeggio fill
    # every low-end slot made kick+808 sound like they had been copied from a
    # different beat.  When accents outnumber the legal phrase slots, only
    # anchors close to the pre-composed groove may steer the direct reading;
    # the remaining accents still inform the shaped/spread candidates below.
    dense_accents = len(accents) > target
    direct_seed = accents
    if dense_accents:
        direct_seed = [
            point for point in accents
            if any(abs(point - pocket) <= 0.50 for pocket in fallback)
        ]
    direct = _spaced(direct_seed, fallback, target)

    remaining = list(accents)
    shaped_seed: list[float] = []
    for pocket in fallback:
        nearby = [point for point in remaining if abs(point - pocket) <= 1.0]
        if nearby:
            point = min(nearby, key=lambda value: (abs(value - pocket), value))
            shaped_seed.append(point)
            remaining.remove(point)
    shaped = _spaced(shaped_seed, fallback, target)

    remaining = list(accents)
    spread_seed: list[float] = []
    while remaining and len(spread_seed) < target:
        if not spread_seed:
            point = min(remaining, key=lambda value: (abs(value - start), value))
        else:
            point = max(
                remaining,
                key=lambda value: (min(abs(value - old) for old in spread_seed), -value),
            )
        remaining.remove(point)
        if all(abs(point - old) >= 0.75 for old in spread_seed):
            spread_seed.append(point)
    spread = _spaced(spread_seed, fallback, target)

    # In a dense phrase a direct melody transcription is explicitly not a
    # legal low-end reading.  Keep only the groove-aware candidates.
    rows = ([] if dense_accents or not allow_direct else [("direct", direct)]) + [("shaped", shaped), ("spread", spread)]
    # Preserve the candidate names for audit but collapse identical outcomes.
    seen: set[tuple[float, ...]] = set()
    return [(name, points) for name, points in rows if points and not (tuple(points) in seen or seen.add(tuple(points)))]


def roll_penalty_points(
    points: list[float],
    *,
    roll_times: list[float] | set[float] | None,
    accents: set[float] | list[float] | None,
    beats_per_bar: float = 4.0,
) -> int:
    """How many onsets of a candidate reading a hi-hat roll would really take.

    ``_local_score`` used to count this inline, and its comment said why: "a
    later pass deletes it anyway unless it carries its own meaning. Prefer a
    reading that never needed it." That makes the penalty a PROXY for
    ``beat_humanize._seal_midi_kick808_pocket``'s rule — and a proxy that had
    drifted from the thing it proxies. It exempted an accent only on an EXACT
    match (1e-5) where the seal allows 0.13, and it knew nothing about the
    seal's other exemptions, so it charged the candidate for onsets the seal
    would never have removed.

    That cost real notes once the backbeat became per-bar: rolls are picked
    from the backbeat, so tripling the backbeat tripled ``roll_times``, and
    the inflated penalty pushed the beam onto sparser readings. Measured
    2026-08-19 on Graveyard: the seal itself takes 3 kicks, but neutralising
    ``roll_times`` entirely returns 10 — the other 7 were never composed.

    The 4-bar density floor is deliberately NOT applied here. A candidate is
    one phrase; a window is four bars it does not cover, so the count would be
    partial and would exempt nearly everything, silently disabling a
    calibrated term.
    """
    if not points:
        return 0
    return sum(
        roll_take_verdict(
            point,
            roll_times=roll_times,
            accents=accents,
            beats_per_bar=beats_per_bar,
        )
        == "take"
        for point in points
    )


def _local_score(
    points: list[float],
    *,
    start: float,
    end: float,
    fallback: list[float],
    accents: list[float],
    pocket_mode: str,
    role: str = "statement",
    roll_times: list[float] | None = None,
    pause_windows: list[tuple[float, float]] | None = None,
) -> float:
    if not points:
        return -999.0
    accent_hits = sum(any(abs(point - accent) <= 1e-5 for accent in accents) for point in points)
    bins = len({min(3, int((point - start) // 2.0)) for point in points})
    pocket_distance = sum(min(abs(point - grid) for grid in fallback) for point in points) / len(points)
    opening = any(point < start + 0.4 for point in points)
    late_answer = any(point >= end - 2.0 for point in points)
    # Modes only rank the same legal candidates.  They never invent an onset,
    # alter the paired kick/808 contract, or affect a passport that failed its
    # safety checks.
    weights = {
        "general": (2.0, 0.8, 0.35, 0.25, 0.25),
        "sustained_space": (1.6, 0.9, 0.35, 0.20, 0.45),
        "chordal_roots": (1.5, 0.8, 0.30, 0.20, 0.55),
        "phrase_answer": (2.35, 0.75, 0.35, 0.65, 0.18),
    }
    accent_w, bins_w, opening_w, late_w, distance_w = weights.get(pocket_mode, weights["general"])
    # Hats own the pulse: a kick buried inside a roll is masked by it, and a
    # later pass (beat_humanize._seal_midi_kick808_pocket) deletes it anyway
    # unless it carries its own meaning. Prefer a reading that never needed it.
    # roll_penalty_points asks that pass's own owner which onsets it would
    # really take, instead of keeping a fourth copy of its exception list.
    roll_hits = roll_penalty_points(
        points, roll_times=roll_times, accents=accents, beats_per_bar=4.0
    )
    # A phrase whose job is space/lift answers the melody's own breath rather
    # than talking over an active passage.
    pause_bonus = (
        sum(in_melody_pause(point, pause_windows) for point in points) * 0.30
        if role in ("space", "lift")
        else 0.0
    )
    return (
        accent_hits * accent_w
        + bins * bins_w
        + (opening_w if opening else 0.0)
        + (late_w if late_answer else 0.0)
        - pocket_distance * distance_w
        - roll_hits * 1.20
        + pause_bonus
    )


def _signature(points: list[float], start: float) -> tuple[float, ...]:
    return tuple(round(point - start, 2) for point in points)


def _phrase_is_firm(
    firmness: dict[str, Any] | None, phrase_start: float, phrase_end: float
) -> bool:
    """Did the melody choose its own accents across the bars of this phrase?

    UNANIMOUS, not a majority: every contested bar in the phrase must have been
    decided by the material. Bars where only one position was ever on offer are
    not contested and count on neither side — they are neither a decision nor a
    coin-flip.

    A majority rule was measured first and rejected. It fired on phrases that
    were only half-decided, and on deyflo — one of the two files judged best —
    that moved the kick off the "1" in 11 % of bars (83 % -> 72 %) while
    Baton Rouge gained nothing extra from the looser rule (100 % either way).
    Shuttle is the one file that loses by this choice (90 % -> 81 %, against a
    79 % baseline); it is not in the listening corpus, and not damaging a file
    the user has approved outranks improving one nobody has heard.
    """
    margins = (firmness or {}).get("margins")
    if not isinstance(margins, dict) or not margins:
        return False
    from melody_beat_symbiosis import ACCENT_FIRMNESS_FLOOR

    lo, hi = int(phrase_start // 4.0), int((phrase_end - 1e-6) // 4.0)
    seen = [
        float(v) for k, v in margins.items()
        if lo <= int(k) <= hi
    ]
    if not seen:
        return False
    return all(m >= ACCENT_FIRMNESS_FLOOR for m in seen)


def compose_phrase_onsets(
    *,
    phrases: list[dict[str, Any]],
    pocket_shapes: dict[str, tuple[float, ...]],
    melody_accents: list[float] | None,
    start: float,
    end: float,
    pocket_mode: str = "general",
    bpm: float = 140.0,
    allow_direct_melody_reading: bool = True,
    anchors: dict[str, Any] | None = None,
    density_card: dict[str, Any] | None = None,
    accent_firmness: dict[str, Any] | None = None,
) -> tuple[list[tuple[float, str]], dict[str, Any]]:
    """Choose a coherent full-song kick/808 onset sequence with beam search.

    ``density_card`` is ``drum_density``'s card for this generation. It only
    ever limits how many onsets a phrase cell may state; the candidate
    readings, their scoring and the beam search are unchanged, and a missing
    card leaves every shape exactly as it was. (Named ``density_card`` rather
    than ``density`` because the beam-search loop below already binds a local
    ``density`` for a phrase's onset count.)
    """
    from drum_density import scaled_shape
    roll_times = list((anchors or {}).get("roll_times") or [])
    pause_windows = list((anchors or {}).get("pause_windows") or [])
    accents_all = sorted({round(float(point), 5) for point in melody_accents or [] if isinstance(point, (int, float))})
    phrase_rows: list[dict[str, Any]] = []
    for phrase in phrases:
        phrase_start = float(phrase.get("start", 0.0) or 0.0)
        phrase_end = float(phrase.get("end", phrase_start + PHRASE_BEATS) or phrase_start + PHRASE_BEATS)
        if phrase_end <= start + 1e-6 or phrase_start >= end - 1e-6:
            continue
        role = str(phrase.get("role") or "statement")
        if role not in pocket_shapes:
            role = "statement"
        # Keep the original single phrase-cell reading.  Longer phrase
        # expansion is deliberately held back until it has listening approval;
        # coverage is still measured by the final gate but must not make the
        # 808 busier by itself.
        #
        # The material decides how many onsets this cell may state before the
        # tempo rule gets its say (drum_density.scaled_shape). Thinning first
        # and adding tempo support afterwards is deliberate: a busy MIDI's
        # shape is left untouched, so a fast dense file keeps exactly the
        # pocket it had, while a sparse one is not handed a fast-tempo grid it
        # never asked for.
        # The section is passed so a cell outside hook/chorus/drop states the
        # same groove one onset more simply (2026-08-20). It is the composer's
        # half of the contrast drum_density.section_pocket_ceiling owns on the
        # manufacturing side — one owner, read from both places, never a second
        # rule of its own.
        section_name = str(phrase.get("section") or "verse")
        # The section step-down states a quiet cell one onset more simply. It
        # is the right move on material that is not saying anything — and it
        # is an override on material that is. Baton Rouge lost the "1" in 23
        # of 48 verse bars to it, every second bar of a two-bar loop, on the
        # file the user judged best (the project issue log (not included) §0x). So the phrase keeps
        # its full cell when the melody firmly chose its own accents across
        # the bars this phrase covers.
        shape = _tempo_dense_fallback(
            scaled_shape(
                pocket_shapes[role],
                density_card,
                section=section_name,
                firm=_phrase_is_firm(accent_firmness, phrase_start, phrase_end),
            ),
            float(bpm),
            density_card,
        )
        fallback = [
            round(phrase_start + offset, 5)
            for offset in shape
            if start <= phrase_start + offset < min(phrase_end, end) - 1e-6
        ]
        accents = [point for point in accents_all if max(start, phrase_start) <= point < min(end, phrase_end) - 1e-6]
        choices = _candidates(
            start=phrase_start,
            end=phrase_end,
            fallback=fallback,
            accents=accents,
            allow_direct=allow_direct_melody_reading,
        )
        if not choices:
            choices = [("fallback", fallback)]
        phrase_rows.append({"start": phrase_start, "end": phrase_end, "role": role, "section": section_name, "fallback": fallback, "accents": accents, "choices": choices})

    # state = score, choices, previous signature, previous section, previous
    # density, pending half-window signature, previous full-window signature
    states: list[
        tuple[float, list[tuple[str, list[float]]], tuple[float, ...] | None, str, int, tuple[float, ...], tuple[float, ...] | None]
    ] = [(0.0, [], None, "", 0, (), None)]
    for row in phrase_rows:
        next_states = []
        window_base = float(int(row["start"] // 16.0) * 16.0)
        starts_window = abs(row["start"] - window_base) <= 1e-6
        for score, chosen, previous_sig, previous_section, previous_density, pending_half, previous_window in states:
            for name, points in row["choices"]:
                value = score + _local_score(
                    points,
                    start=row["start"],
                    end=row["end"],
                    fallback=row["fallback"],
                    accents=row["accents"],
                    pocket_mode=pocket_mode,
                    role=row["role"],
                    roll_times=roll_times,
                    pause_windows=pause_windows,
                )
                sig = _signature(points, row["start"])
                density = len(points)
                # A repeated phrase may repeat, but a static grid cannot own
                # an entire song when the MIDI supplied alternative accents.
                if previous_sig == sig and row["accents"]:
                    value -= 1.15
                # The export gate judges sameness over 4 bars, not 2 (see
                # beat_humanize._audit_midi_kick808_context's identical_4bar_
                # cycle): two phrases that each differ from their neighbour can
                # still tile one unchanging 16-beat loop across a whole song.
                window_sig = tuple(round(point - window_base, 2) for point in points)
                if starts_window:
                    next_pending, completed = window_sig, None
                else:
                    next_pending, completed = (), tuple(pending_half) + window_sig
                if completed is not None and previous_window == completed and row["accents"]:
                    value -= 1.60
                next_window = completed if completed is not None else previous_window
                if previous_section != row["section"] and row["section"] in ("hook", "chorus", "drop"):
                    value += 0.35 if density >= previous_density else -0.65
                next_states.append((value, chosen + [(name, points)], sig, row["section"], density, next_pending, next_window))
        states = sorted(next_states, key=lambda item: item[0], reverse=True)[:8]

    if not states:
        return [], {"version": 1, "passed": False, "reason": "no_phrase_choices"}
    score, selected, _sig, _section, _density, _pending, _window = states[0]
    onsets: list[tuple[float, str]] = []
    decisions: list[dict[str, Any]] = []
    for row, (name, points) in zip(phrase_rows, selected):
        onsets.extend((point, row["role"]) for point in points)
        decisions.append({"start": round(row["start"], 5), "role": row["role"], "section": row["section"], "reading": name, "onsets": points})
    return onsets, {"version": 1, "passed": bool(onsets), "score": round(score, 4), "phrases": decisions, "accent_count": len(accents_all), "pocket_mode": pocket_mode, "bpm": round(float(bpm), 3), "tempo_density": "fast" if bpm >= 150 else "normal"}


__all__ = [
    "CHORD_VOICING_ENABLED",
    "LowendRootMap",
    "at_phrase_boundary",
    "compose_phrase_onsets",
    "in_hat_roll",
    "roll_may_take_kick",
    "roll_take_verdict",
    "roll_penalty_points",
    "in_melody_pause",
    "rhythmic_anchors",
    "snap_harmonic_windows_to_boundaries",
]

"""Deterministic structural analysis for uploaded melody MIDI.

This module is intentionally read-only: it turns note dictionaries into a
stable musical map but never edits notes or drums.  Later beat planners may
consume the map only after its individual signals have been listening-tested.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from typing import Any

import midi_roles
from music_theory import SCALES, detect_root_pc, detect_scale
from pattern_utils import NOTE_NAMES, parse_note_name, track_notes

BEATS_PER_BAR = 4.0
_EPS = 1e-6


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _q(value: float, grid: float = 0.25) -> float:
    return round(round(value / grid) * grid, 5)


def _safe_midi(note: Any) -> int | None:
    try:
        return parse_note_name(str(note))
    except (TypeError, ValueError):
        return None


def _events(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for note in notes:
        if not isinstance(note, dict):
            continue
        midi = _safe_midi(note.get("note"))
        if midi is None:
            continue
        start = round(float(note.get("time_step", 0.0) or 0.0), 3)
        grouped[start].append(
            {
                "midi": midi,
                "length": max(0.03, float(note.get("length", 0.25) or 0.25)),
                "velocity": max(1, min(127, int(note.get("velocity", 100) or 100))),
            }
        )
    out: list[dict[str, Any]] = []
    for start, members in sorted(grouped.items()):
        pitches = sorted({member["midi"] for member in members})
        out.append(
            {
                "time": start,
                "pitches": pitches,
                "lead_pitch": max(pitches),
                "bass_pitch": min(pitches),
                "centroid": sum(pitches) / len(pitches),
                "length": max(member["length"] for member in members),
                "velocity": round(
                    sum(member["velocity"] for member in members) / len(members), 2
                ),
                "polyphony": len(pitches),
            }
        )
    return out


def _pause_map(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    if len(events) < 2:
        return [], 0.75
    onset_gaps = [
        events[index + 1]["time"] - event["time"]
        for index, event in enumerate(events[:-1])
        if events[index + 1]["time"] > event["time"]
    ]
    median_gap = statistics.median(onset_gaps) if onset_gaps else 0.5
    threshold = max(0.5, min(2.0, median_gap * 1.75))
    pauses: list[dict[str, Any]] = []
    for current, nxt in zip(events, events[1:]):
        release = current["time"] + current["length"]
        gap = nxt["time"] - release
        if gap < threshold - _EPS:
            continue
        bar_proximity = min(nxt["time"] % BEATS_PER_BAR, BEATS_PER_BAR - nxt["time"] % BEATS_PER_BAR)
        strength = _clamp(
            0.50 * gap / max(threshold, 0.01)
            + 0.25 * (1.0 if bar_proximity <= 0.15 else 0.0)
            + 0.25 * min(1.0, gap / 2.0)
        )
        pauses.append(
            {
                "start": round(release, 5),
                "end": round(nxt["time"], 5),
                "length": round(gap, 5),
                "next_onset": round(nxt["time"], 5),
                "strength": round(strength, 4),
                "phrase_boundary_likelihood": round(_clamp(0.25 + 0.75 * strength), 4),
            }
        )
    return pauses, threshold


def _bar_signature(events: list[dict[str, Any]], bar: int) -> tuple[tuple[Any, ...], ...]:
    start = bar * BEATS_PER_BAR
    selected = [event for event in events if start <= event["time"] < start + BEATS_PER_BAR]
    if not selected:
        return ()
    anchor = selected[0]["lead_pitch"]
    return tuple(
        (
            _q(event["time"] - start),
            max(-24, min(24, event["lead_pitch"] - anchor)),
            _q(min(2.0, event["length"])),
            min(4, event["polyphony"]),
        )
        for event in selected
    )


def _signature_similarity(
    left: tuple[tuple[Any, ...], ...],
    right: tuple[tuple[Any, ...], ...],
) -> float:
    if not left or not right:
        return 1.0 if left == right else 0.0
    left_rhythm = {(row[0], row[2], row[3]) for row in left}
    right_rhythm = {(row[0], row[2], row[3]) for row in right}
    union = left_rhythm | right_rhythm
    rhythm = len(left_rhythm & right_rhythm) / max(1, len(union))
    paired = min(len(left), len(right))
    contour = sum(
        max(0.0, 1.0 - abs(int(left[i][1]) - int(right[i][1])) / 7.0)
        for i in range(paired)
    ) / max(len(left), len(right))
    count_fit = min(len(left), len(right)) / max(len(left), len(right))
    return _clamp(0.45 * rhythm + 0.40 * contour + 0.15 * count_fit)


def _repeat_period(events: list[dict[str, Any]], bars: int) -> tuple[int, float]:
    signatures = [_bar_signature(events, bar) for bar in range(bars)]
    best_period, best_score = 4, 0.0
    for period in (1, 2, 4, 8):
        if bars < period * 2:
            continue
        scores = [
            _signature_similarity(signatures[bar], signatures[bar + period])
            for bar in range(bars - period)
        ]
        score = statistics.mean(scores) if scores else 0.0
        adjusted = score - 0.025 * math.log2(period)
        if adjusted > best_score:
            best_period, best_score = period, adjusted
    return best_period, round(_clamp(best_score), 4)


def _phrase_map(
    events: list[dict[str, Any]],
    pauses: list[dict[str, Any]],
    bars: int,
    repeat_period: int,
    repeat_confidence: float,
) -> list[dict[str, Any]]:
    if not events:
        return []
    first_time = events[0]["time"]
    song_start = 0.0 if first_time < 0.25 else first_time
    raw_end = max(event["time"] + event["length"] for event in events)
    nearest_bar_end = round(raw_end / BEATS_PER_BAR) * BEATS_PER_BAR
    musical_end = nearest_bar_end if abs(raw_end - nearest_bar_end) <= 0.125 else raw_end
    song_end = max(bars * BEATS_PER_BAR, musical_end)
    boundaries: dict[float, tuple[str, float]] = {
        song_start: ("start", 1.0),
        song_end: ("end", 1.0),
    }
    last_boundary = song_start
    for pause in pauses:
        boundary = float(pause["next_onset"])
        if boundary - last_boundary < BEATS_PER_BAR:
            continue
        if pause["strength"] >= 0.60:
            boundaries[boundary] = ("pause", float(pause["phrase_boundary_likelihood"]))
            last_boundary = boundary

    # A one-bar loop is a motif, not a complete musical phrase.  Two bars is
    # the minimum phrase unit even when bar-level repetition is extremely high.
    grid_bars = max(2, repeat_period) if repeat_confidence >= 0.58 else 4
    grid_beats = max(BEATS_PER_BAR, grid_bars * BEATS_PER_BAR)
    cursor = math.floor(events[0]["time"] / grid_beats) * grid_beats + grid_beats
    while cursor < song_end - 0.5:
        if all(abs(cursor - point) >= BEATS_PER_BAR for point in boundaries):
            boundaries[cursor] = ("repeat_grid", max(0.45, repeat_confidence))
        cursor += grid_beats

    # No phrase may become an unhelpful 9+ bar wall.
    points = sorted(boundaries)
    expanded = [points[0]]
    for point in points[1:]:
        while point - expanded[-1] > 8 * BEATS_PER_BAR:
            expanded.append(expanded[-1] + 4 * BEATS_PER_BAR)
            boundaries[expanded[-1]] = ("max_length", 0.55)
        expanded.append(point)

    phrases: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(zip(expanded, expanded[1:])):
        members = [event for event in events if start <= event["time"] < end]
        if not members:
            continue
        contour_delta = members[-1]["lead_pitch"] - members[0]["lead_pitch"]
        contour = "rising" if contour_delta >= 3 else "falling" if contour_delta <= -3 else "level"
        reason, boundary_conf = boundaries.get(end, ("inferred", 0.45))
        phrases.append(
            {
                "id": f"P{len(phrases) + 1}",
                "start": round(start, 5),
                "end": round(end, 5),
                "start_bar": round(start / BEATS_PER_BAR, 3),
                "bars": round((end - start) / BEATS_PER_BAR, 3),
                "note_events": len(members),
                "contour": contour,
                "pitch_span": max(event["lead_pitch"] for event in members)
                - min(event["lead_pitch"] for event in members),
                "end_boundary_reason": reason,
                "confidence": round(_clamp(0.45 + 0.45 * boundary_conf), 4),
            }
        )
    return phrases


def _motif_key(window: list[dict[str, Any]]) -> tuple[Any, ...]:
    first_pitch = window[0]["lead_pitch"]
    first_time = window[0]["time"]
    return (
        tuple(max(-24, min(24, event["lead_pitch"] - first_pitch)) for event in window),
        tuple(_q(event["time"] - first_time) for event in window),
        tuple(_q(min(1.5, event["length"])) for event in window),
    )


def _motifs(events: list[dict[str, Any]], phrases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], list[float]] = defaultdict(list)
    window_size = 5
    for index in range(max(0, len(events) - window_size + 1)):
        window = events[index : index + window_size]
        if window[-1]["time"] - window[0]["time"] > 2 * BEATS_PER_BAR:
            continue
        buckets[_motif_key(window)].append(window[0]["time"])
    repeated = [
        (key, starts)
        for key, starts in buckets.items()
        if len(starts) >= 2 and max(starts) - min(starts) >= BEATS_PER_BAR
    ]
    repeated.sort(key=lambda item: (-len(item[1]), item[1][0]))
    out: list[dict[str, Any]] = []
    occupied: list[float] = []
    for key, starts in repeated:
        unique_starts = [
            start
            for start in starts
            if all(abs(start - prior) >= 0.5 for prior in occupied)
        ]
        if len(unique_starts) < 2:
            continue
        pitch_shape, rhythm_shape, duration_shape = key
        occurrences = []
        for start in unique_starts[:16]:
            phrase_id = next(
                (
                    phrase["id"]
                    for phrase in phrases
                    if phrase["start"] <= start < phrase["end"]
                ),
                None,
            )
            occurrences.append({"start": round(start, 5), "phrase_id": phrase_id})
        out.append(
            {
                "id": f"M{len(out) + 1}",
                "event_count": len(pitch_shape),
                "interval_shape": list(pitch_shape),
                "rhythm_shape": list(rhythm_shape),
                "duration_shape": list(duration_shape),
                "occurrences": occurrences,
                "repeat_count": len(occurrences),
                "confidence": round(_clamp(0.48 + 0.10 * len(occurrences)), 4),
            }
        )
        occupied.extend(unique_starts)
        if len(out) >= 12:
            break
    return out


def _pc_weights(
    events: list[dict[str, Any]],
    start: float,
    end: float,
) -> Counter:
    """Duration x velocity weight per pitch class over ``[start, end)``.

    Extracted from _root_candidates so the chord-quality pass measures the
    window with the SAME weighting the root was elected with — a second
    weighting scheme here would let a window's root and its own third
    disagree about which notes are actually sounding.
    """
    weights: Counter[int] = Counter()
    for event in events:
        overlap = max(
            0.0,
            min(end, event["time"] + event["length"]) - max(start, event["time"]),
        )
        if overlap <= 0:
            continue
        for pitch in event["pitches"]:
            weights[pitch % 12] += overlap * event["velocity"]
    return weights


# A chord tone counts as SOUNDING only above this share of the root's own
# weight. Below it the pitch class is a passing brush, not a member of the
# chord, and the 808 must not be allowed to sit on it.
_TONE_SUPPORT_FLOOR = 0.22

# Mid-bar change (measured, never used to cut a window): a bar is read as two
# halves of this many beats, and a half only counts when its root beats the
# runner-up by this share of the top-three score.
MID_BAR_SPLIT = 2.0
_MID_BAR_MARGIN = 0.12


def chord_symbol(root_pc: int, quality: str, chord_pcs: dict[str, Any], sus: str | None = None) -> str:
    """A readable symbol (``Em7``, ``Cmaj7``, ``G7``, ``Asus4``, ``A5``) for one
    measured window. Pure: it only names what ``_chord_quality`` found."""
    root = NOTE_NAMES[int(root_pc) % 12]
    seventh = chord_pcs.get("seventh")
    interval = None if seventh is None else (int(seventh) - int(root_pc)) % 12
    if quality == "dim":
        return root + ("m7b5" if interval == 10 else "dim")
    if quality == "minor":
        return root + {10: "m7", 11: "mMaj7"}.get(interval, "m")
    if quality == "major":
        return root + {10: "7", 11: "maj7"}.get(interval, "")
    if sus:
        return root + ("7" if interval == 10 else "") + sus
    return root + ("5" if chord_pcs.get("fifth") is not None else "")


def _chord_quality(
    events: list[dict[str, Any]],
    start: float,
    end: float,
    root_pc: int,
) -> dict[str, Any]:
    """Which chord tones the melody actually sounds over ``[start, end)``.

    This is deliberately measurement, not chord-symbol recognition: it reports
    the third / fifth / seventh that are really there, with how strongly, so a
    consumer can tell a real minor triad from a bare octave-and-fifth or from a
    single sustained root. ``quality`` is a label for humans and telemetry; the
    machine-readable answer is ``chord_pcs`` + ``tone_support``.
    """
    weights = _pc_weights(events, start, end)
    root_pc = int(root_pc) % 12
    reference = weights[root_pc]
    if reference <= 0:
        reference = max(weights.values(), default=0.0)
    if reference <= 0:
        return {
            "quality": "open",
            "chord_pcs": {"root": root_pc, "third": None, "fifth": None, "seventh": None},
            "tone_support": {"third": 0.0, "fifth": 0.0, "seventh": 0.0},
            "sus": None,
            "symbol": NOTE_NAMES[root_pc],
        }

    def support(interval: int) -> float:
        return round(weights[(root_pc + interval) % 12] / reference, 4)

    minor_third, major_third = support(3), support(4)
    fifth, flat_fifth = support(7), support(6)
    minor_seventh, major_seventh = support(10), support(11)

    third_pc: int | None = None
    third_support = 0.0
    third_name = ""
    if max(minor_third, major_third) >= _TONE_SUPPORT_FLOOR:
        # Ties resolve minor — the darker reading, consistent with
        # music_theory.detect_root_pc's own tie rule.
        if minor_third >= major_third:
            third_pc, third_support, third_name = (root_pc + 3) % 12, minor_third, "minor"
        else:
            third_pc, third_support, third_name = (root_pc + 4) % 12, major_third, "major"

    fifth_pc: int | None = None
    fifth_support = 0.0
    diminished = False
    if fifth >= _TONE_SUPPORT_FLOOR:
        fifth_pc, fifth_support = (root_pc + 7) % 12, fifth
    elif flat_fifth >= _TONE_SUPPORT_FLOOR and third_name == "minor":
        # No perfect fifth but a flat one over a minor third reads diminished.
        # The b5 is NOT offered as an 808 target (it is a tritone against the
        # root); only the label changes.
        diminished = True
        fifth_support = flat_fifth

    seventh_pc: int | None = None
    seventh_support = 0.0
    if max(minor_seventh, major_seventh) >= _TONE_SUPPORT_FLOOR:
        if minor_seventh >= major_seventh:
            seventh_pc, seventh_support = (root_pc + 10) % 12, minor_seventh
        else:
            seventh_pc, seventh_support = (root_pc + 11) % 12, major_seventh

    # No third but a second or a fourth over the root reads suspended. ``quality``
    # stays "open" (what every existing consumer keys on); the label is extra.
    sus: str | None = None
    if not third_name:
        second, fourth = support(2), support(5)
        if max(second, fourth) >= _TONE_SUPPORT_FLOOR:
            sus = "sus4" if fourth >= second else "sus2"

    if diminished:
        quality = "dim"
    elif third_name:
        quality = third_name
    elif fifth_pc is not None:
        quality = "open"  # root + fifth, no third — power-chord / bare reading
    else:
        quality = "open"

    chord_pcs = {
        "root": root_pc,
        "third": third_pc,
        "fifth": fifth_pc,
        "seventh": seventh_pc,
    }
    return {
        "quality": quality,
        "chord_pcs": chord_pcs,
        "tone_support": {
            "third": round(third_support, 4),
            "fifth": round(fifth_support, 4),
            "seventh": round(seventh_support, 4),
        },
        "sus": sus,
        "symbol": chord_symbol(root_pc, quality, chord_pcs, sus),
    }


def _root_candidates(
    events: list[dict[str, Any]],
    start: float,
    end: float,
    global_root: int,
) -> list[dict[str, Any]]:
    weights = _pc_weights(events, start, end)
    bass_weights: Counter[int] = Counter()
    for event in events:
        overlap = max(
            0.0,
            min(end, event["time"] + event["length"]) - max(start, event["time"]),
        )
        if overlap <= 0:
            continue
        bass_weights[event["bass_pitch"] % 12] += overlap * event["velocity"]
    if not weights:
        return []
    scores: list[tuple[int, float]] = []
    for root in range(12):
        score = (
            1.0 * weights[root]
            + 0.66 * weights[(root + 7) % 12]
            + 0.58 * max(weights[(root + 3) % 12], weights[(root + 4) % 12])
            + 0.18 * weights[(root + 10) % 12]
            + 0.45 * bass_weights[root]
            + (0.08 * sum(weights.values()) if root == global_root else 0.0)
        )
        scores.append((root, score))
    scores.sort(key=lambda item: (-item[1], item[0]))
    total = sum(score for _, score in scores[:3]) or 1.0
    return [
        {"pitch_class": root, "score": round(score / total, 4)}
        for root, score in scores[:3]
    ]


def _role_events(
    notes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    """Events of the chord + bass role notes, and of the bass role alone.

    ``(None, None)`` when the notes carry no ``plg_role`` tags (roles off, or a
    file that was never ingested), so every reading below falls back to the whole
    melody exactly as before.
    """
    tagged = [n for n in notes if isinstance(n, dict) and n.get(midi_roles.ROLE_KEY) in midi_roles.ROLES]
    if not tagged:
        return None, None
    harmony = _events([n for n in tagged if n[midi_roles.ROLE_KEY] in (midi_roles.CHORDS, midi_roles.BASS)])
    bass = _events([n for n in tagged if n[midi_roles.ROLE_KEY] == midi_roles.BASS])
    return harmony or None, bass or None


def _bass_pc(bass_events: list[dict[str, Any]] | None, start: float, end: float) -> int | None:
    """The pitch class the bass-role notes sound most over ``[start, end)``."""
    if not bass_events:
        return None
    weights = _pc_weights(bass_events, start, end)
    if not weights:
        return None
    return min(weights, key=lambda pc: (-weights[pc], pc))


def _role_harmony(
    harmony_events: list[dict[str, Any]] | None, item: dict[str, Any], global_root: int
) -> dict[str, Any] | None:
    """The window read from the chord + bass role notes alone (no melody)."""
    if not harmony_events:
        return None
    candidates = _root_candidates(harmony_events, item["start"], item["end"], global_root)
    if not candidates:
        return None
    root = candidates[0]["pitch_class"]
    reading = _chord_quality(harmony_events, item["start"], item["end"], root)
    return {
        "root_pc": root,
        "quality": reading["quality"],
        "symbol": reading["symbol"],
        "sus": reading["sus"],
        "chord_pcs": reading["chord_pcs"],
        "tone_support": reading["tone_support"],
        "same_root_as_window": root == item["root_pc"],
    }


def _mid_bar_changes(
    events: list[dict[str, Any]], start: float, end: float, global_root: int
) -> list[dict[str, Any]]:
    """Chord changes INSIDE a bar of ``[start, end)``: first half vs second half.

    Measurement only. The window keeps its bar geometry; this just reports that
    its second half sounds a different, confidently elected root.
    """
    changes: list[dict[str, Any]] = []
    bar = start
    while bar < end - _EPS:
        halves: list[tuple[int, str, float] | None] = []
        for half_start in (bar, bar + MID_BAR_SPLIT):
            candidates = _root_candidates(events, half_start, half_start + MID_BAR_SPLIT, global_root)
            margin = candidates[0]["score"] - candidates[1]["score"] if len(candidates) > 1 else 0.0
            if not candidates or margin < _MID_BAR_MARGIN:
                halves.append(None)
                continue
            root = candidates[0]["pitch_class"]
            symbol = _chord_quality(events, half_start, half_start + MID_BAR_SPLIT, root)["symbol"]
            halves.append((root, symbol, margin))
        first, second = halves
        if first and second and first[0] != second[0]:
            changes.append(
                {
                    "at": round(bar + MID_BAR_SPLIT, 5),
                    "bar_start": round(bar, 5),
                    "from_root": first[0],
                    "to_root": second[0],
                    "from_symbol": first[1],
                    "to_symbol": second[1],
                    "confidence": round(min(first[2], second[2]), 4),
                }
            )
        bar += BEATS_PER_BAR
    return changes


def _harmonic_map(
    events: list[dict[str, Any]],
    bars: int,
    global_root: int,
    *,
    harmony_events: list[dict[str, Any]] | None = None,
    bass_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for bar in range(bars):
        start = bar * BEATS_PER_BAR
        candidates = _root_candidates(events, start, start + BEATS_PER_BAR, global_root)
        if not candidates:
            continue
        margin = candidates[0]["score"] - candidates[1]["score"] if len(candidates) > 1 else 1.0
        raw.append(
            {
                "start": start,
                "end": start + BEATS_PER_BAR,
                "root_pc": candidates[0]["pitch_class"],
                "root_candidates": candidates,
                "confidence": round(_clamp(0.35 + 1.8 * margin), 4),
            }
        )
    merged: list[dict[str, Any]] = []
    for item in raw:
        if (
            merged
            and merged[-1]["root_pc"] == item["root_pc"]
            and abs(merged[-1]["end"] - item["start"]) <= _EPS
        ):
            previous_bars = int(round((merged[-1]["end"] - merged[-1]["start"]) / BEATS_PER_BAR))
            merged[-1]["end"] = item["end"]
            merged[-1]["confidence"] = round(
                (merged[-1]["confidence"] * previous_bars + item["confidence"])
                / (previous_bars + 1),
                4,
            )
        else:
            merged.append(dict(item))
    # Chord quality is measured AFTER the merge, over each final window's own
    # span, and never participates in the merge decision (2026-08-16). Merging
    # still keys on root_pc alone, so window boundaries, roots, confidences and
    # change_strength stay byte-identical to before this pass existed — the
    # entire kick/808 chain is calibrated against that geometry.
    for item in merged:
        item.update(_chord_quality(events, item["start"], item["end"], item["root_pc"]))
    # More readings of the SAME windows, added beside the fields above and never
    # feeding the merge, the roots or the confidences (2026-10, midi_roles):
    # the bass-role pitch class, the window read from the chord/bass-role notes
    # alone, and any chord change inside a bar.
    for item in merged:
        item["bass_pc"] = _bass_pc(bass_events, item["start"], item["end"])
        item["harmony"] = _role_harmony(harmony_events, item, global_root)
        item["mid_bar_changes"] = _mid_bar_changes(
            harmony_events or events, item["start"], item["end"], global_root
        )
    for index, item in enumerate(merged):
        item["change_strength"] = (
            0.0
            if index == 0
            else round(
                _clamp(
                    0.35
                    + 0.35 * item["confidence"]
                    + 0.15 * (
                        min(
                            (item["root_pc"] - merged[index - 1]["root_pc"]) % 12,
                            (merged[index - 1]["root_pc"] - item["root_pc"]) % 12,
                        )
                        / 6.0
                    )
                ),
                4,
            )
        )
    return merged


def _tension_curve(
    events: list[dict[str, Any]],
    bars: int,
    root_pc: int,
    scale: str,
) -> list[dict[str, Any]]:
    scale_pcs = {(root_pc + interval) % 12 for interval in SCALES.get(scale, SCALES["natural_minor"])}
    global_median = statistics.median(event["centroid"] for event in events) if events else 60.0
    rows: list[dict[str, Any]] = []
    for bar in range(bars):
        start = bar * BEATS_PER_BAR
        members = [event for event in events if start <= event["time"] < start + BEATS_PER_BAR]
        if not members:
            rows.append({"bar": bar, "value": 0.0, "components": {"silence": 1.0}})
            continue
        pitches = [pitch for event in members for pitch in event["pitches"]]
        chromatic = sum(pitch % 12 not in scale_pcs for pitch in pitches) / max(1, len(pitches))
        dissonant = 0
        pairs = 0
        for event in members:
            for i, left in enumerate(event["pitches"]):
                for right in event["pitches"][i + 1 :]:
                    pairs += 1
                    if abs(right - left) % 12 in (1, 2, 6, 10, 11):
                        dissonant += 1
        dissonance = dissonant / max(1, pairs)
        density = _clamp(len(members) / 16.0)
        syncopation = sum(
            abs((event["time"] % 0.5) - 0.25) <= 0.08 for event in members
        ) / len(members)
        height = _clamp((statistics.mean(event["centroid"] for event in members) - global_median + 12) / 24)
        ending_distance = min(
            (members[-1]["lead_pitch"] % 12 - root_pc) % 12,
            (root_pc - members[-1]["lead_pitch"] % 12) % 12,
        ) / 6.0
        value = _clamp(
            0.26 * chromatic
            + 0.22 * dissonance
            + 0.18 * density
            + 0.14 * syncopation
            + 0.10 * height
            + 0.10 * ending_distance
        )
        rows.append(
            {
                "bar": bar,
                "value": round(value, 4),
                "components": {
                    "chromaticity": round(chromatic, 4),
                    "dissonance": round(dissonance, 4),
                    "density": round(density, 4),
                    "syncopation": round(syncopation, 4),
                    "register": round(height, 4),
                    "unresolved_ending": round(ending_distance, 4),
                },
            }
        )
    return rows


def _cadences(
    phrases: list[dict[str, Any]],
    harmonic: list[dict[str, Any]],
    tension: list[dict[str, Any]],
    pauses: list[dict[str, Any]],
    root_pc: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    dominant = (root_pc + 7) % 12
    for phrase in phrases:
        end = float(phrase["end"])
        before = [item for item in harmonic if item["start"] < end - _EPS]
        if not before:
            continue
        target = before[-1]
        previous = before[-2] if len(before) >= 2 else None
        pause_strength = max(
            (
                pause["strength"]
                for pause in pauses
                if abs(float(pause["end"]) - end) <= 0.35
            ),
            default=0.0,
        )
        end_bar = max(0, min(len(tension) - 1, int(max(0.0, end - 0.01) // BEATS_PER_BAR)))
        prior_tension = tension[max(0, end_bar - 1)]["value"] if tension else 0.0
        end_tension = tension[end_bar]["value"] if tension else 0.0
        tension_drop = max(0.0, prior_tension - end_tension)
        cadence_type = None
        if target["root_pc"] == root_pc and previous and previous["root_pc"] == dominant:
            cadence_type = "authentic_resolution"
        elif (
            target["root_pc"] == root_pc
            and previous
            and previous["root_pc"] != root_pc
        ):
            cadence_type = "tonic_resolution"
        elif (
            target["root_pc"] == root_pc
            and (pause_strength >= 0.70 or tension_drop >= 0.12)
        ):
            cadence_type = "tonic_release"
        elif target["root_pc"] == dominant and previous and previous["root_pc"] != dominant:
            cadence_type = "half_cadence"
        elif pause_strength >= 0.65 and tension_drop >= 0.08:
            cadence_type = "phrase_release"
        if cadence_type is None:
            continue
        confidence = _clamp(
            0.35
            + 0.30 * target["confidence"]
            + 0.20 * pause_strength
            + 0.30 * tension_drop
        )
        out.append(
            {
                "time": round(end, 5),
                "phrase_id": phrase["id"],
                "type": cadence_type,
                "target_root_pc": target["root_pc"],
                "previous_root_pc": previous["root_pc"] if previous else None,
                "tension_drop": round(tension_drop, 4),
                "confidence": round(confidence, 4),
            }
        )
    return out


def analyze_midi_structure(
    notes: list[dict[str, Any]],
    *,
    bpm: float = 140.0,
    root_pc: int | None = None,
    scale: str | None = None,
) -> dict[str, Any]:
    """Return a JSON-serializable structural map without mutating ``notes``."""
    events = _events(notes)
    if not events:
        return {
            "version": 1,
            "status": "empty",
            "confidence": 0.0,
            "phrases": [],
            "motifs": [],
            "pauses": [],
            "harmonic_rhythm": [],
            "tension_curve": [],
            "cadences": [],
        }
    inferred_root = int(root_pc) % 12 if root_pc is not None else detect_root_pc(notes)
    inferred_scale = str(scale or detect_scale(notes, inferred_root) or "natural_minor")
    end = max(event["time"] + event["length"] for event in events)
    # Humanized MIDI often ends a few milliseconds past the exact bar line.
    bars = max(1, math.ceil(max(0.0, end - 0.125) / BEATS_PER_BAR))
    pauses, pause_threshold = _pause_map(events)
    repeat_period, repeat_confidence = _repeat_period(events, bars)
    phrases = _phrase_map(events, pauses, bars, repeat_period, repeat_confidence)
    motifs = _motifs(events, phrases)
    harmony_events, bass_events = _role_events(notes)
    harmonic = _harmonic_map(
        events, bars, inferred_root, harmony_events=harmony_events, bass_events=bass_events
    )
    tension = _tension_curve(events, bars, inferred_root, inferred_scale)
    cadences = _cadences(phrases, harmonic, tension, pauses, inferred_root)
    phrase_conf = statistics.mean(p["confidence"] for p in phrases) if phrases else 0.0
    harmonic_conf = (
        statistics.mean(item["confidence"] for item in harmonic) if harmonic else 0.0
    )
    confidence = _clamp(
        0.25
        + 0.25 * phrase_conf
        + 0.20 * harmonic_conf
        + 0.15 * min(1.0, len(events) / 24)
        + 0.15 * min(1.0, len(motifs) / 3)
    )
    return {
        "version": 1,
        "status": "ok",
        "confidence": round(confidence, 4),
        "bpm": round(float(bpm), 3),
        "root_pc": inferred_root,
        "scale": inferred_scale,
        "span": {
            "start": round(events[0]["time"], 5),
            "end": round(end, 5),
            "bars": bars,
            "event_count": len(events),
            "note_count": len(notes),
        },
        "phrase_grid": {
            "repeat_period_bars": repeat_period,
            "repeat_confidence": repeat_confidence,
            "pause_threshold_beats": round(pause_threshold, 4),
        },
        "phrases": phrases,
        "motifs": motifs,
        "pauses": pauses,
        "harmonic_rhythm": harmonic,
        "tension_curve": tension,
        "cadences": cadences,
        "summary": {
            "phrase_count": len(phrases),
            "motif_count": len(motifs),
            "pause_count": len(pauses),
            "harmonic_changes": max(0, len(harmonic) - 1),
            "mid_bar_changes": sum(len(item["mid_bar_changes"]) for item in harmonic),
            "cadence_count": len(cadences),
            "tension_peak_bar": max(
                tension,
                key=lambda item: item["value"],
                default={"bar": 0},
            )["bar"],
        },
    }


def attach_midi_structure(pattern: dict[str, Any]) -> dict[str, Any]:
    """Analyze ``melody_lead`` and attach ``plg_midi_structure`` metadata."""
    from music_theory import detect_root_pc

    notes = track_notes(pattern, "melody_lead")
    key_meta = pattern.get("plg_midi_key")
    key_meta = key_meta if isinstance(key_meta, dict) else {}
    uploaded = pattern.get("plg_uploaded_scale_lock")
    uploaded = uploaded if isinstance(uploaded, dict) else {}
    # The real sounding notes are the source of truth for the root; the
    # embedded key_signature / explicit scale lock are fallbacks for material
    # too thin to analyse.  ``pattern["scale"]`` must NOT feed in here: it is
    # generation_variety's per-run anti-clone roll, and leaking it used to
    # make ``plg_midi_structure.scale`` flip run-to-run on identical MIDI
    # (measured 2026-08-15: C# minor vs C# phrygian on the same file).  When
    # no scale survives below, ``analyze_midi_structure`` detects one from
    # the notes itself — deterministic per input.
    if len(notes) >= 4:
        root = detect_root_pc(notes)
    else:
        root = key_meta.get("root_pc")
        if root is None:
            root = uploaded.get("root_pc")
    scale = key_meta.get("scale") or uploaded.get("scale")
    analysis = analyze_midi_structure(
        notes,
        bpm=float(pattern.get("bpm", 140.0) or 140.0),
        root_pc=int(root) if root is not None else None,
        scale=str(scale) if scale else None,
    )
    pattern["plg_midi_structure"] = analysis
    return analysis


__all__ = ["analyze_midi_structure", "attach_midi_structure"]

"""Within-role character layer: vibe tags, BPM attack, velocity, frequency pocket.

Role sieve picks the FLEX family; this module ranks *which* preset in that family
fits the beat — close-close, not 1:1.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Any, Iterable

from melody_sample_bank import _note_intervals_beats

# Scientific MIDI: C4=48, C5=60 (frequency pocket mid band).
_POCKET_LO = 48
_POCKET_HI = 72

_DARK_TOKENS = (
    "dark", "ambient", "rage", "hard", "evil", "melancholic", "sad", "minor",
    "trap", "drill", "opium", "gothic", "goth", "haunt", "plugg", "pluggnb",
    "shadow", "void", "cold", "horror", "drone", "wash", "distort", "lofi",
    "lo-fi", "emo", "night",
)
_LIGHT_TOKENS = (
    "light", "happy", "rnb", "r&b", "pop", "emotional", "soul", "bright",
    "major", "guitar", "summer", "shine", "sparkle", "sunny", "clean", "uplifting",
)
_RAGE_TOKENS = (
    "rage", "distort", "scream", "anger", "crush", "aggressive", "phonk", "hardstyle",
)

# Genre-specific preset-name affinity — a soft nudge on top of the existing
# role/vibe/register/tempo scoring, only for the genres with a distinguishable
# enough sonic signature that a token table adds real signal (2026-07 scope:
# west_coast/phonk/drill/hyperpop). The other DETAILED_GENRES already lean on
# the vibe/mood system (sweet_dark/rage/bright) for this — a separate table
# there would just re-derive what that system already covers, not add
# anything new. Positive-only: absence of a genre word doesn't mean "wrong
# genre" (most generic plucks/pads never mention a genre by name), so there's
# no penalty side — this only nudges the pick when the pool is otherwise tied,
# it never fights role/vibe/register/tempo fit (those score in the +/-3 to
# +/-8 range; this stays well under that).
_GENRE_TOKENS: dict[str, tuple[str, ...]] = {
    "west_coast": ("warm", "live", "analog", "funk", "clean", "guitar", "bass", "soul"),
    "phonk": ("dusty", "lo-fi", "lofi", "cowbell", "detuned", "vintage", "tape", "memphis"),
    "drill": ("ominous", "dark", "strings", "piano", "sliding", "cold", "menace"),
    "hyperpop": ("digital", "chip", "square", "detuned", "glitch", "distort", "synth", "chaotic"),
}
_GENRE_WEIGHT = 1.75  # same class as tempo_band's +/-1.5, well under role fit's +/-6-8

# Preset-name tags for attack / tone / dynamics.
TAG_SHARP = ("sharp", "fast", "pluck", "punch", "aggress", "short", "staccato", "tight", "saw pluck")
TAG_LUSH = ("warm", "soft", "space", "lush", "lo-fi", "lofi", "slow", "ambient", "long", "pad", "nebula")
TAG_MUTED = ("muted", "dark", "thin", "dull", "mellow", "soft", "suitcase", "detuned")
TAG_BRIGHT = ("bright", "rich", "full", "wide", "grand", "shine", "crystal", "glass")
TAG_DYNAMIC = ("dynamic", "velocity", "expressive", "sensitive", "live", "close grand", "upright")
TAG_DIGITAL = ("digital", "solid", "compress", "synth", "chip", "square", "saw", "synpluck")
TAG_DARK = ("dark", "evil", "rage", "distort", "lo-fi", "lofi", "atmosphere", "shadow", "sqdark")
TAG_LIGHT = ("bright", "clean", "happy", "crystal", "glass", "music box", "upright", "glock")


@dataclass(frozen=True)
class MelodyCharacter:
    tempo_band: str  # slow | mid | fast
    attack: str  # sharp | lush | neutral
    vel_mode: str  # dynamic | digital | neutral
    pocket_muted: bool
    dissonant: bool
    min_onset_gap: float


def _blob(*parts: str) -> str:
    return " ".join(re.sub(r"[_\-.\[\],]+", " ", p or "").lower() for p in parts if p)


def _keyword_hits(text: str, tokens: Iterable[str]) -> float:
    hits = 0.0
    for tok in tokens:
        if tok in text:
            hits += 1.0
    return hits


def _strip_genre_lane_words(text: str) -> str:
    """Genre lane names are never vibe evidence, from any source.

    2026-08-13: the 2026-08-11 fix excluded the ``style`` PARAMETER from the
    vibe blob, but the MIDI source names re-add
    ``pattern["style"]`` to ``source_names`` (its docstring even calls
    prompt/style "vibe signals"), and ``resolve_vibe_plan_ab`` builds the
    blob from ``source_names`` — so on the live route the lane word ("trap",
    always) came straight back in and forced ``sweet_dark`` for every
    generation, major-scale material included (measured: 10/10 corpus files
    sweet_dark in LLM-off mode). All ten lane names are stripped here,
    wherever they appear (filename, track name, prompt); real mood words in
    the same text survive ("dark trap love" keeps "dark"). Lane set owner:
    ``genre_profiles.DETAILED_GENRES`` — read it, never re-list it locally.
    """
    from genre_profiles import DETAILED_GENRES  # local: keep this module import-light

    for lane in DETAILED_GENRES:
        token = re.escape(str(lane).strip().lower().replace("_", " "))
        text = re.sub(rf"(?<![a-z0-9]){token}(?![a-z0-9])", " ", text)
    return text


def _min_onset_gap_beats(intervals: list[tuple[float, float, int]]) -> float:
    starts = sorted({round(s, 5) for s, _e, _p in intervals})
    if len(starts) < 2:
        return 999.0
    gaps = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
    gaps = [g for g in gaps if g > 1e-4]
    return float(min(gaps)) if gaps else 999.0


def _dissonance_frac(intervals: list[tuple[float, float, int]]) -> float:
    """Fraction of chord clusters containing a semitone (dirty harmony)."""
    if not intervals:
        return 0.0
    ordered = sorted(intervals, key=lambda t: t[0])
    clusters: list[list[tuple[float, float, int]]] = []
    for item in ordered:
        if not clusters or abs(item[0] - clusters[-1][0][0]) > 0.06:
            clusters.append([item])
        else:
            clusters[-1].append(item)
    dirty = 0
    checks = 0
    for group in clusters:
        pitches = sorted({p for _s, _e, p in group})
        if len(pitches) < 2:
            continue
        checks += 1
        if any(pitches[i + 1] - pitches[i] == 1 for i in range(len(pitches) - 1)):
            dirty += 1
    return dirty / max(1, checks)


def _track_pitch_set(notes: list[dict[str, Any]]) -> set[int]:
    from pattern_utils import parse_note_name

    out: set[int] = set()
    for n in notes:
        try:
            out.add(int(parse_note_name(str(n.get("note", "C5")))))
        except (TypeError, ValueError):
            continue
    return out


def _pocket_collision(
    melody_notes: list[dict[str, Any]],
    pattern: dict[str, Any] | None,
    *,
    melody_track: str,
) -> bool:
    """True when another bed already occupies the same mid octave pocket."""
    if not pattern:
        return False
    mel_pitches = _track_pitch_set(melody_notes)
    mel_mid = {p for p in mel_pitches if _POCKET_LO <= p <= _POCKET_HI}
    if len(mel_mid) < 2:
        return False
    tracks = pattern.get("tracks")
    if not isinstance(tracks, dict):
        return False
    bed_keys = (
        "pad", "chords", "chord_bed", "keys", "atmosphere", "texture",
        "counter_melody", "melody_pad",
    )
    for key, raw in tracks.items():
        if key == melody_track or not isinstance(raw, list) or len(raw) < 2:
            continue
        key_l = str(key).lower()
        if not any(b in key_l for b in bed_keys) and key_l not in bed_keys:
            # Still check dense polyphonic beds.
            if len(raw) < 8:
                continue
        other = _track_pitch_set(raw)
        other_mid = {p for p in other if _POCKET_LO <= p <= _POCKET_HI}
        if len(other_mid) < 3:
            continue
        overlap = mel_mid & other_mid
        if len(overlap) >= 3 or (len(overlap) / max(1, len(mel_mid))) >= 0.45:
            return True
    return False


def resolve_melody_character(
    notes: list[dict[str, Any]],
    *,
    bpm: float = 140.0,
    pattern: dict[str, Any] | None = None,
    melody_track: str = "melody_lead",
) -> MelodyCharacter:
    intervals = _note_intervals_beats(notes)
    min_gap = _min_onset_gap_beats(intervals) if intervals else 999.0
    dissonant = _dissonance_frac(intervals) >= 0.22 if intervals else False

    if bpm <= 110:
        tempo_band = "slow"
    elif bpm >= 130:
        tempo_band = "fast"
    else:
        tempo_band = "mid"

    # Rhythmic compression: dense 1/16-ish → sharp; lazy 1/2–1/4 → lush.
    if min_gap <= 0.35:
        attack = "sharp"
    elif min_gap >= 1.8 or (tempo_band == "slow" and min_gap >= 1.0):
        attack = "lush"
    elif tempo_band == "fast" and min_gap <= 0.55:
        attack = "sharp"
    elif tempo_band == "slow":
        attack = "lush"
    else:
        attack = "neutral"

    vels: list[float] = []
    for n in notes:
        try:
            vels.append(float(n.get("velocity", 100)))
        except (TypeError, ValueError):
            continue
    if len(vels) >= 4:
        stdev = statistics.pstdev(vels)
        unique_ratio = len({round(v) for v in vels}) / len(vels)
        if stdev >= 14.0 and unique_ratio >= 0.25:
            vel_mode = "dynamic"
        elif stdev <= 3.0 or unique_ratio <= 0.08:
            vel_mode = "digital"
        else:
            vel_mode = "neutral"
    else:
        vel_mode = "neutral"

    pocket = _pocket_collision(notes, pattern, melody_track=melody_track)
    return MelodyCharacter(
        tempo_band=tempo_band,
        attack=attack,
        vel_mode=vel_mode,
        pocket_muted=pocket,
        dissonant=dissonant,
        min_onset_gap=min_gap,
    )


def resolve_vibe_plan_ab(
    *,
    scale: str | None = None,
    source_names: Iterable[str] = (),
    prompt: str = "",
    style: str = "",
    notes: list[dict[str, Any]] | None = None,
) -> str:
    """Plan A tags → Plan B scale/dissonance → sweet_dark / rage / bright / neutral.

    2026-08-11: ``style`` is deliberately EXCLUDED from the text-token blob.
    In the live MIDI-first route ``style`` is always one of the ten internal
    genre-lane names (genre_profiles.DETAILED_GENRES), never user-authored
    text — and six of those ten names ("trap", "drill", "opium", "pluggnb",
    "rage", "phonk") are themselves _RAGE_TOKENS/_DARK_TOKENS entries.
    Feeding the lane label into the blob meant the vibe was decided by the
    genre tag before the MIDI's own scale/dissonance was ever read: every
    trap-lane generation forced "sweet_dark" regardless of what the melody
    actually was (measured: 4 of 63 curated presets reachable). Real free
    text — an actual user prompt, or words already in the MIDI's own
    filename/track name via ``source_names`` — is still legitimate signal
    and stays in the blob. Do not add ``style`` back in.

    2026-08-13: that exclusion was silently undone on the live route —
    ``_midi_source_names`` appends ``pattern["style"]`` to ``source_names``,
    which feeds this blob. ``_strip_genre_lane_words`` now removes all ten
    lane names from every ``source_names`` entry (filename, track name,
    style). The user prompt is deliberately NOT stripped: it is free text,
    and "rage"/"drill" there are mood words, not lane labels (this route
    ships with an empty prompt anyway).
    """
    blob = _blob(
        *(_strip_genre_lane_words(str(name)) for name in source_names),
        prompt,
    )
    scale_l = (scale or "").lower()
    minor = "minor" in scale_l or scale_l in ("aeolian", "dorian", "phrygian")
    major = "major" in scale_l or scale_l in ("ionian", "lydian", "mixolydian")

    if any(t in blob for t in _RAGE_TOKENS):
        return "rage"
    if any(t in blob for t in _DARK_TOKENS):
        return "sweet_dark"
    if any(t in blob for t in _LIGHT_TOKENS):
        return "bright"

    dissonant = False
    if notes and len(notes) >= 4:
        dissonant = _dissonance_frac(_note_intervals_beats(notes)) >= 0.22

    if dissonant:
        return "sweet_dark"
    if minor:
        return "sweet_dark"
    if major:
        return "bright"
    return "neutral"


def score_character_bonus(
    preset_text: str,
    character: MelodyCharacter,
    *,
    vibe: str,
) -> float:
    """Soft +/- inside an already role-filtered pool."""
    text = preset_text.lower()
    score = 0.0

    if character.attack == "sharp":
        score += 3.5 * _keyword_hits(text, TAG_SHARP)
        score -= 4.0 * _keyword_hits(
            text,
            (
                "slow",
                "lush",
                "warm pad",
                "soft rhodes",
                "stage rhodes",
                "suitcase rhodes",
                "epiano life",
                "mellow",
                "nebula",
                "atmosphere",
            ),
        )
    elif character.attack == "lush":
        score += 3.5 * _keyword_hits(text, TAG_LUSH)
        score -= 3.0 * _keyword_hits(text, ("sharp", "pluck", "punch", "short", "staccato", "synpluck anger"))

    if character.tempo_band == "slow":
        score += 1.5 * _keyword_hits(text, ("warm", "soft", "space", "lush", "lo-fi", "lofi"))
        score -= 1.5 * _keyword_hits(text, ("aggress", "rage", "fast", "punch"))
    elif character.tempo_band == "fast":
        score += 1.5 * _keyword_hits(text, ("sharp", "fast", "pluck", "punch", "aggress", "808"))
        score -= 1.5 * _keyword_hits(text, ("slow", "lush", "nebula", "choir aah"))

    if character.pocket_muted:
        score += 3.0 * _keyword_hits(text, TAG_MUTED)
        score -= 2.5 * _keyword_hits(text, TAG_BRIGHT)

    if character.vel_mode == "dynamic":
        score += 2.5 * _keyword_hits(text, TAG_DYNAMIC)
        score -= 1.5 * _keyword_hits(text, ("synpluck", "chip", "square wave", "solid"))
    elif character.vel_mode == "digital":
        score += 2.5 * _keyword_hits(text, TAG_DIGITAL)
        score -= 2.0 * _keyword_hits(text, ("close grand", "upright", "acoustic piano", "nylon"))

    if character.dissonant or vibe in ("sweet_dark", "rage"):
        score += 1.5 * _keyword_hits(text, TAG_DARK)
    if vibe == "bright":
        score += 1.5 * _keyword_hits(text, TAG_LIGHT)

    return score


def score_genre_bonus(preset_text: str, genre_key: str | None) -> float:
    """Soft +/- inside an already role/vibe-filtered pool — see _GENRE_TOKENS
    docstring above for scope (west_coast/phonk/drill/hyperpop only) and why
    it's positive-only. ``genre_key`` is the already-resolved genre lane
    (e.g. plg_variety["genre"] from genre_brain.resolve_generation_genre) —
    trusted as-is, no confidence weighting."""
    if not genre_key:
        return 0.0
    tokens = _GENRE_TOKENS.get(str(genre_key).strip().lower())
    if not tokens:
        return 0.0
    return _GENRE_WEIGHT * _keyword_hits(preset_text.lower(), tokens)


# ---------------------------------------------------------------------------
# MIDI geometry analytics (relocated 2026-08-28)
#
# These were defined in flex_melody_map.py, which existed to score FLEX presets.
# The scoring is gone, but the geometry itself is first-party analysis of the
# user's own MIDI -- onset rate, syncopation, span, velocity behaviour -- and it
# still feeds the melody energy card that drum programming answers. It lives
# here now so nothing about the drums depends on a synth-preset catalogue.
# ---------------------------------------------------------------------------

from melody_sample_bank import _note_intervals_beats, _polyphony_fraction, _rest_fraction


def _onset_clusters(intervals: list[tuple[float, float, int]], *, tol: float = 0.06) -> list[list[tuple[float, float, int]]]:
    """Group notes that start together (one vertical / strum cluster)."""
    ordered = sorted(intervals, key=lambda t: t[0])
    clusters: list[list[tuple[float, float, int]]] = []
    for note in ordered:
        if not clusters or abs(note[0] - clusters[-1][0][0]) > tol:
            clusters.append([note])
        else:
            clusters[-1].append(note)
    return clusters


@dataclass(frozen=True)
class _SieveFeatures:
    """MIDI geometry sieve features (density / tails / span / rhythm / vel)."""

    median_pitch: float
    min_pitch: float
    max_pitch: float
    pitch_span: float
    poly: float
    dyad_poly: float
    median_len: float
    long_bar_frac: float
    short_frac: float
    ultra_short_frac: float
    vel_chaos: float
    vel_robot: float
    vel_soft: float
    grid_score: float
    strum_score: float
    strum_down_score: float
    coverage: float
    onset_rate: float
    mono: bool
    double_hop_frac: float
    power_chord_frac: float
    palm_mute: bool
    legato_overlap: float
    rare_onset: bool
    chord_span_med: float
    chord_span_max: float
    hand_gap_med: float
    downbeat_frac: float
    syncopation: float
    median_step: float
    pitch_dispersion: float
    # Median signed gap between one note's END and the next note's START
    # (beats). Negative = notes bleed into each other (legato/overlap),
    # ~0 = end-to-end, positive = silence between notes (staccato/phrased).
    # 2026-08-13: articulation input for the continuous scoring layer.
    note_gap_med: float = 0.0


def _note_velocities(notes: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for n in notes:
        try:
            out.append(float(n.get("velocity", 100)))
        except (TypeError, ValueError):
            continue
    return out


def _coverage_fraction(intervals: list[tuple[float, float, int]]) -> float:
    """Fraction of the timeline where ≥1 note is sounding (pad wall ≈ 1.0)."""
    from melody_sample_bank import _rest_fraction

    if not intervals:
        return 0.0
    return max(0.0, min(1.0, 1.0 - float(_rest_fraction(intervals))))


def _chord_onset_rate(intervals: list[tuple[float, float, int]]) -> float:
    """Unique chord-onset clusters per bar (4 beats). Low = held wall, high = stabs."""
    if not intervals:
        return 0.0
    starts = sorted(s for s, _e, _p in intervals)
    clusters: list[float] = []
    for s in starts:
        if not clusters or abs(s - clusters[-1]) > 0.08:
            clusters.append(s)
    span = max(1e-6, max(e for _s, e, _p in intervals) - intervals[0][0])
    bars = max(span / 4.0, 0.25)
    return len(clusters) / bars


def _downbeat_and_syncopation(starts: list[float]) -> tuple[float, float]:
    """Downbeat attachment vs off-beat syncopation (bar = 4 beats)."""
    if not starts:
        return 0.0, 0.0
    uniq = sorted({round(s, 4) for s in starts})
    on_strong = 0
    on_weak = 0
    for s in uniq:
        pos = s % 4.0
        if pos <= 0.08 or pos >= 3.92:
            on_strong += 1
        elif 0.35 <= pos <= 3.65:
            # Off the 1 — eighth/syncop pocket (not near bar line).
            on_weak += 1
    n = max(1, len(uniq))
    downbeat_frac = on_strong / n
    # Empty-1 signature: bars where beat 1 is vacant but weak beats fire.
    bars_with_notes: dict[int, list[float]] = {}
    for s in uniq:
        bars_with_notes.setdefault(int(s // 4.0), []).append(s % 4.0)
    empty1 = 0
    bar_n = 0
    for positions in bars_with_notes.values():
        bar_n += 1
        has_1 = any(p <= 0.08 or p >= 3.92 for p in positions)
        has_weak = any(0.35 <= p <= 3.65 for p in positions)
        if has_weak and not has_1:
            empty1 += 1
    empty1_frac = empty1 / max(1, bar_n)
    syncopation = max(0.0, min(1.0, 0.55 * (on_weak / n) + 0.70 * empty1_frac))
    return downbeat_frac, syncopation


def _grid_quantization_score(starts: list[float]) -> float:
    """1.0 = every onset sits on 1/4 or 1/8 grid; lower = human micro-shift."""
    if not starts:
        return 0.0
    errs: list[float] = []
    for s in starts:
        # Distance to nearest 1/8-note grid line.
        grid = round(s * 8.0) / 8.0
        errs.append(abs(s - grid))
    mean_err = statistics.fmean(errs)
    # Perfect robot ≤ 1/64 beat; live playing often 0.02–0.08.
    return max(0.0, min(1.0, 1.0 - mean_err / 0.06))


def _legato_overlap_fraction(intervals: list[tuple[float, float, int]]) -> float:
    """Rhodes: notes bleed slightly into the next onset (legato)."""
    if len(intervals) < 2:
        return 0.0
    ordered = sorted(intervals, key=lambda t: t[0])
    overlaps = 0
    checks = 0
    for a, b in zip(ordered, ordered[1:]):
        if abs(a[2] - b[2]) < 1:  # same pitch re-trigger — skip
            continue
        checks += 1
        # Mild overlap: previous note still sounding 0.02–0.4 beats into next.
        bleed = a[1] - b[0]
        if 0.02 <= bleed <= 0.45:
            overlaps += 1
    return overlaps / max(1, checks)


def _median_melodic_step(intervals: list[tuple[float, float, int]]) -> float:
    """Median |Δpitch| between successive onset clusters (melody step size)."""
    clusters = _onset_clusters(intervals, tol=0.06)
    if len(clusters) < 2:
        return 0.0
    # Representative pitch = median of the cluster (chord → mid voice).
    reps = [float(statistics.median([p for _s, _e, p in g])) for g in clusters]
    steps = [abs(reps[i + 1] - reps[i]) for i in range(len(reps) - 1)]
    steps = [s for s in steps if s > 0.01]
    if not steps:
        return 0.0
    return float(statistics.median(steps))


def _pitch_dispersion(pitches: list[int]) -> float:
    """Stdev of pitch — pads sit tight; arps jump octaves."""
    if len(pitches) < 2:
        return 0.0
    return float(statistics.pstdev(pitches))


def _velocity_signals(velocities: list[float]) -> tuple[float, float, float]:
    """Return (vel_chaos, vel_robot, vel_soft) in [0, 1]."""
    if len(velocities) < 2:
        return 0.0, 0.0, 0.0
    mean = statistics.fmean(velocities)
    stdev = statistics.pstdev(velocities)
    unique_ratio = len({round(v) for v in velocities}) / len(velocities)
    # Live piano: stdev ~15–30+, nearly every note a different velocity.
    chaos = max(0.0, min(1.0, (stdev - 8.0) / 22.0)) * max(0.0, min(1.0, (unique_ratio - 0.35) / 0.55))
    # Robot keys/pluck: tiny stdev, few distinct values (often one value).
    robot = max(0.0, min(1.0, (4.0 - stdev) / 4.0)) * max(0.0, min(1.0, (0.25 - unique_ratio) / 0.25 + 0.5))
    if unique_ratio <= 0.08 and stdev <= 2.0:
        robot = 1.0
    # Soft Rhodes pocket: mean ~70–85, modest stdev.
    soft = 0.0
    if 65.0 <= mean <= 90.0 and stdev <= 12.0:
        soft = max(0.0, min(1.0, 1.0 - abs(mean - 75.0) / 20.0)) * (1.0 - min(1.0, stdev / 14.0))
    return chaos, robot, soft


def _strum_scores(intervals: list[tuple[float, float, int]]) -> tuple[float, float]:
    """Guitar strum ladders: (upstroke ascending, downstroke descending)."""
    if len(intervals) < 4:
        return 0.0, 0.0
    # Wide window (~0.1 beat) so 4-string fan-outs stay one chord.
    clusters = _onset_clusters(intervals, tol=0.10)
    up_hits = 0
    down_hits = 0
    chord_windows = 0
    for group in clusters:
        if len(group) < 3:
            continue
        chord_windows += 1
        ordered = sorted(group, key=lambda t: (t[0], t[2]))
        starts = [t[0] for t in ordered]
        span = starts[-1] - starts[0]
        # Strum ladder: 3+ notes, total fan-out 0.01–0.18 beats (not a brick).
        if 0.008 <= span <= 0.18:
            pitches = [t[2] for t in ordered]
            asc = sum(1 for a, b in zip(pitches, pitches[1:]) if b >= a)
            desc = sum(1 for a, b in zip(pitches, pitches[1:]) if b <= a)
            need = max(1, len(pitches) - 2)
            if asc >= need:
                up_hits += 1
            if desc >= need:
                down_hits += 1
    if chord_windows == 0:
        return 0.0, 0.0
    return up_hits / chord_windows, down_hits / chord_windows


def _double_hop_fraction(starts: list[float]) -> float:
    """Pizzicato marker: two ultra-fast onsets in a row, then a gap."""
    if len(starts) < 3:
        return 0.0
    hops = 0
    checks = 0
    ordered = sorted(starts)
    i = 0
    while i < len(ordered) - 1:
        checks += 1
        gap = ordered[i + 1] - ordered[i]
        if gap <= 0.2:
            nxt = ordered[i + 2] - ordered[i + 1] if i + 2 < len(ordered) else 1.0
            if nxt >= 0.35:
                hops += 1
                i += 2
                continue
        i += 1
    return hops / max(1, checks)


def _chord_span_and_hand_gap(
    intervals: list[tuple[float, float, int]],
) -> tuple[float, float, float]:
    """Median/max simultaneous chord span (semitones) + median hand-hole gap."""
    clusters = _onset_clusters(intervals, tol=0.06)
    spans: list[float] = []
    gaps: list[float] = []
    for group in clusters:
        if len(group) < 2:
            continue
        pitches = sorted({p for _s, _e, p in group})
        if len(pitches) < 2:
            continue
        spans.append(float(pitches[-1] - pitches[0]))
        if len(pitches) >= 3:
            internal = [pitches[i + 1] - pitches[i] for i in range(len(pitches) - 1)]
            gaps.append(float(max(internal)))
    if not spans:
        return 0.0, 0.0, 0.0
    return (
        float(statistics.median(spans)),
        float(max(spans)),
        float(statistics.median(gaps)) if gaps else 0.0,
    )


def _power_chord_and_palm(
    intervals: list[tuple[float, float, int]],
) -> tuple[float, bool]:
    """Electric-guitar signals: fifth/dyad stacks + uniform short palm-mute."""
    if not intervals:
        return 0.0, False
    # Cluster simultaneous notes.
    clusters: list[list[tuple[float, float, int]]] = []
    ordered = sorted(intervals, key=lambda t: t[0])
    for item in ordered:
        if not clusters or abs(item[0] - clusters[-1][0][0]) > 0.06:
            clusters.append([item])
        else:
            clusters[-1].append(item)
    power = 0
    dyads = 0
    short_uniform = 0
    for group in clusters:
        if len(group) != 2:
            continue
        dyads += 1
        pitches = sorted(p for _s, _e, p in group)
        interval = pitches[1] - pitches[0]
        if interval in (5, 7, 12):  # fourth / fifth / octave power shapes
            power += 1
        lengths = [e - s for s, e, _p in group]
        if all(L <= 0.35 for L in lengths) and abs(lengths[0] - lengths[1]) <= 0.05:
            short_uniform += 1
    power_frac = power / max(1, dyads) if dyads else 0.0
    palm = dyads >= 3 and short_uniform / max(1, dyads) >= 0.55
    return power_frac, palm


def _extract_sieve_features(notes: list[dict[str, Any]]) -> _SieveFeatures | None:
    intervals = _note_intervals_beats(notes)
    if not intervals:
        return None
    lengths = [e - s for s, e, _p in intervals]
    pitches = [p for _s, _e, p in intervals]
    starts = [s for s, _e, _p in intervals]
    velocities = _note_velocities(notes)
    median_len = float(statistics.median(lengths))
    median_pitch = float(statistics.median(pitches))
    min_pitch = float(min(pitches))
    max_pitch = float(max(pitches))
    poly = _polyphony_fraction(intervals, threshold=3)
    dyad_poly = _polyphony_fraction(intervals, threshold=2)
    vel_chaos, vel_robot, vel_soft = _velocity_signals(velocities)
    power_frac, palm = _power_chord_and_palm(intervals)
    onset_rate = _chord_onset_rate(intervals)
    strum_up, strum_down = _strum_scores(intervals)
    span_med, span_max, hand_gap = _chord_span_and_hand_gap(intervals)
    downbeat_frac, syncopation = _downbeat_and_syncopation(starts)
    return _SieveFeatures(
        median_pitch=median_pitch,
        min_pitch=min_pitch,
        max_pitch=max_pitch,
        pitch_span=max_pitch - min_pitch,
        poly=poly,
        dyad_poly=dyad_poly,
        median_len=median_len,
        long_bar_frac=sum(1 for L in lengths if L >= 4.0) / max(1, len(lengths)),
        short_frac=sum(1 for L in lengths if L <= 0.5) / max(1, len(lengths)),
        ultra_short_frac=sum(1 for L in lengths if L <= 0.25) / max(1, len(lengths)),
        vel_chaos=vel_chaos,
        vel_robot=vel_robot,
        vel_soft=vel_soft,
        grid_score=_grid_quantization_score(starts),
        strum_score=max(strum_up, strum_down),
        strum_down_score=strum_down,
        coverage=_coverage_fraction(intervals),
        onset_rate=onset_rate,
        mono=poly < 0.05 and dyad_poly < 0.12,
        double_hop_frac=_double_hop_fraction(starts),
        power_chord_frac=power_frac,
        palm_mute=palm,
        legato_overlap=_legato_overlap_fraction(intervals),
        rare_onset=onset_rate <= 0.7,
        chord_span_med=span_med,
        chord_span_max=span_max,
        hand_gap_med=hand_gap,
        downbeat_frac=downbeat_frac,
        syncopation=syncopation,
        median_step=_median_melodic_step(intervals),
        pitch_dispersion=_pitch_dispersion(pitches),
        note_gap_med=(
            float(
                statistics.median(
                    intervals[i + 1][0] - intervals[i][1]
                    for i in range(len(intervals) - 1)
                )
            )
            if len(intervals) >= 2
            else 0.0
        ),
    )


# ---------------------------------------------------------------------------
# DNA tag matching (relocated 2026-08-28 from flex_melody_map.py)
#
# Classifies an instrument family from a filename or prompt string. This is
# our own keyword table over the user's own MIDI metadata -- it named FLEX
# presets once, but it never needed them. midi_genre_detect still uses it.
# ---------------------------------------------------------------------------

_DNA_NAME_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Electric Guitar", ("electric guitar", "e-guitar", "eguitar", "distortion guitar",
                         "muted guitar", "chorus guitar", "funk guitar", "jazz guitar")),
    ("Pizzicato", ("pizzicato", "pizz")),
    ("Rhodes", ("rhodes", "e-piano", "epiano", "electric piano", "detuned ep", "dx ep")),
    ("Piano", ("piano", "grand", "honky tonk", "honky-tonk")),
    ("Strings", ("strings", "string ", "violin", "cello", "viola", "ensemble", "orchestra")),
    ("Pluck", ("pluck", "synpluck", "plectrum", "harp", "kalimba", "pizz", "arp", "arpeggio")),
    ("Guitar", ("guitar", "nylon", "acoustic gtr", "steel string")),
    ("Bell", ("bell", "celeste", "celesta", "chime", "glock", "musicbox", "music box",
              "carillon", "tubular")),
    ("Pad", ("pad", "atmosphere", "ambient", "wash", "drone", "space pad", "choir aah",
             "voice ooh", "bowed glass")),
    ("Bass", ("bass", "sub bass", "bassline", "reese")),
    ("Synth", ("synth", "lead", "saw ", "square", "analog", "digital", "chip", "syn ",
               "supsaw", "supersaw", "pwm", "fx lead", "buzz", "sawwave", "saw wave",
               # Marketplace melody loops (ExamplePack Ethnic / oriental leads).
               "ethnic", "oriental", "arabic", "middle eastern")),
    ("Key", (" key", "keys", "clav", "clavi", "organ", "harpsi", "keys ", "key_")),
)


def _token_in_blob(token: str, low: str) -> bool:
    """Match alias/DNA tokens without substring traps (bell⊂rebellion)."""
    alias = (token or "").strip().lower()
    if not alias:
        return False
    # Multi-word phrases keep simple containment inside padded blob.
    if " " in alias or "-" in alias:
        return alias in low
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", low))


def dna_from_text(text: str) -> str | None:
    """Match one of the 12 DNA tags inside a filename / prompt string."""
    cleaned = re.sub(r"[_\-.]+", " ", text).lower()
    low = f" {cleaned} "
    for dna, aliases in _DNA_NAME_ALIASES:
        if any(_token_in_blob(alias, low) for alias in aliases):
            return dna
        # Word-boundary only — bare "bell" must not hit "rebellion".
        if _token_in_blob(dna.lower(), low):
            return dna
    return None

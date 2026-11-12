"""Symbolic ML composer — Markov hi-hats + drop generator (Trap/Rage MIDI logic).

Produces a probability-driven drum grid. Pipeline:

    symbolic_composer → validator → flp_writer

(An optional GrooVAE humanization step used to sit between the composer and the
validator; it was retired 2026-08-27 — see the project issue log (not included).)

Enable with ``PLG_USE_SYMBOLIC=1`` or genre profiles that set ``markov_hats``.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import os
import random
from typing import Any

from drum_defaults import _bar_count
from genre_profiles import GenreProfile, profile_for
from pattern_utils import parse_note_name, track_notes

logger = logging.getLogger("plg.symbolic")

BEATS_PER_BAR = 4.0
HAT_NOTE = "C5"
SNARE_NOTE = "D1"
EIGHTH_STEP = 0.5
MIN_NOTE_LEN = 0.0625

# Markov transition matrix — PLG Hi-Hats (Producer Brain TZ)
# standard → 80% stay on 1/8 grid, 20% explode into a hat roll cascade.
HAT_MARKOV: dict[str, dict[str, float]] = {
    "standard": {"standard": 0.80, "roll": 0.20},
    "roll": {"standard": 1.00},
}

# 1/32 and 1/24 of a bar, expressed in beats (4/4 bar = 4 beats).
ROLL_STEP_CHOICES = (BEATS_PER_BAR / 32.0, BEATS_PER_BAR / 24.0)
# Roll span: 1/4 bar (1 beat) or 1/2 bar (2 beats).
ROLL_DURATION_CHOICES = (1.0, 2.0)

# Drop generator — 8th bar vocal pocket before the square-loop drop.
DROP_BAR_1IDX = 8
DROP_PROBABILITY = 0.90
DROP_CLEAR_TRACKS = ("hi_hats", "clap", "snare_layer", "snare")
SUB_808_TRACK_KEYS = frozenset({"sub_808", "808"})
SNARE_ROLL_VEL_MIN = int(127 * 0.30)  # 30 %
SNARE_ROLL_VEL_MAX = 127  # 100 %
SNARE_ROLL_STEP = BEATS_PER_BAR / 32.0

TRAP_RAGE_GENRES = frozenset({"rage", "opium", "phonk", "drill", "trap", "edm"})

# --- Opium / Dark Rage melody (Ken Carson / F1lthy — 100% local, no web/API) ---
OPIUM_LOOP_BARS = 4
OPIUM_PHRASE_BARS = 8
SIXTEENTH_GRID = 0.25  # 1.0 time_step = 1 beat → 16th = 0.25 beat
HALF_NOTE_BEATS = 8 * SIXTEENTH_GRID   # 2.0 beats (8 grid steps)
WHOLE_NOTE_BEATS = 16 * SIXTEENTH_GRID  # 4.0 beats (16 grid steps)
OPIUM_SUSTAIN_LENGTHS = (HALF_NOTE_BEATS, WHOLE_NOTE_BEATS)
OPIUM_MELODY_LO = 64   # E4
OPIUM_MELODY_HI = 79   # G5
COUNTER_MIDI_LO = 84   # C6
COUNTER_MIDI_HI = 96   # C7
MELODY_VEL_LO = 100
MELODY_VEL_HI = 120

# 4-note syncopated hooks — sustained half / whole notes only (8 or 16 sixteenth steps).
OPIUM_HOOK_BLUEPRINTS: tuple[tuple[tuple[float, float], ...], ...] = (
    ((0.0, WHOLE_NOTE_BEATS), (4.5, HALF_NOTE_BEATS), (8.0, HALF_NOTE_BEATS), (12.0, WHOLE_NOTE_BEATS)),
    ((0.0, WHOLE_NOTE_BEATS), (5.0, HALF_NOTE_BEATS), (8.0, WHOLE_NOTE_BEATS), (12.0, HALF_NOTE_BEATS)),
    ((0.0, HALF_NOTE_BEATS), (2.0, HALF_NOTE_BEATS), (8.0, WHOLE_NOTE_BEATS), (12.0, WHOLE_NOTE_BEATS)),
    ((1.0, HALF_NOTE_BEATS), (4.0, WHOLE_NOTE_BEATS), (9.0, HALF_NOTE_BEATS), (12.0, WHOLE_NOTE_BEATS)),
    ((0.5, HALF_NOTE_BEATS), (4.0, WHOLE_NOTE_BEATS), (8.5, HALF_NOTE_BEATS), (13.0, HALF_NOTE_BEATS)),
    ((0.0, HALF_NOTE_BEATS), (3.5, WHOLE_NOTE_BEATS), (8.0, HALF_NOTE_BEATS), (12.5, HALF_NOTE_BEATS)),
    ((2.0, HALF_NOTE_BEATS), (6.0, HALF_NOTE_BEATS), (10.0, WHOLE_NOTE_BEATS), (14.0, HALF_NOTE_BEATS)),
    ((0.0, WHOLE_NOTE_BEATS), (6.0, HALF_NOTE_BEATS), (10.5, HALF_NOTE_BEATS), (14.0, HALF_NOTE_BEATS)),
    ((1.5, HALF_NOTE_BEATS), (5.0, HALF_NOTE_BEATS), (9.0, WHOLE_NOTE_BEATS), (13.5, HALF_NOTE_BEATS)),
    ((0.0, HALF_NOTE_BEATS), (4.0, HALF_NOTE_BEATS), (8.0, WHOLE_NOTE_BEATS), (12.0, HALF_NOTE_BEATS)),
    ((3.0, HALF_NOTE_BEATS), (7.0, WHOLE_NOTE_BEATS), (11.0, HALF_NOTE_BEATS), (15.0, HALF_NOTE_BEATS)),
    ((0.0, WHOLE_NOTE_BEATS), (4.0, HALF_NOTE_BEATS), (8.0, HALF_NOTE_BEATS), (12.0, WHOLE_NOTE_BEATS)),
)

# Carson octave jumps land in bars 3–4 of each 4-bar loop (beats 8–16 mod loop).
CARSON_JUMP_BAR_START = 2  # 0-indexed bar within loop
CARSON_JUMP_BAR_END = 4


def local_opium_melody_mode(prompt: str = "", style: str = "", *, genre: str | None = None) -> bool:
    """True when Opium/Rage melody is composed locally (skip web reference search).

    ``genre`` is the ALREADY-RESOLVED genre when the caller has one (e.g. from
    plg_variety / the UI picker) — text re-detection falls back to "trap" for
    any prompt without a recognizable keyword, which used to let this engine
    hijack minimal_trap/pluggnb/detroit/west_coast patterns whose profile was
    resolved elsewhere. Text detection remains only for callers that genuinely
    have nothing but text."""
    flag = _env_flag("PLG_OFFLINE_MELODY", "auto")
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    if genre:
        return str(genre).strip().lower() in TRAP_RAGE_GENRES
    prof = profile_for(str(style or ""), str(prompt or ""))
    return prof.name in TRAP_RAGE_GENRES


def _env_flag(name: str, default: str = "auto") -> str:
    return os.environ.get(name, default).strip().lower()


def _rng_for(pattern: dict[str, Any]) -> random.Random:
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    seed_src = "|".join(
        [
            "symbolic",
            str(pattern.get("user_prompt", "")),
            str(pattern.get("bpm", "")),
            str(pattern.get("style", "")),
            str(variety.get("seed", "")),
            str(variety.get("drum_variant", "")),
            str(variety.get("melody_variant", "")),
        ]
    )
    digest = hashlib.sha256(seed_src.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def _note_step(entry: dict[str, Any]) -> float:
    return float(entry.get("time_step", 0.0))


def _note_end(entry: dict[str, Any]) -> float:
    return _note_step(entry) + float(entry.get("length", 0.25))


def should_apply_symbolic(
    pattern: dict[str, Any],
    profile: GenreProfile | None = None,
) -> bool:
    """True when Symbolic ML should replace legacy hat-roll generation."""
    flag = _env_flag("PLG_USE_SYMBOLIC", "auto")
    if flag in ("0", "false", "no", "off"):
        return False
    prof = profile or profile_for(
        str(pattern.get("style", "")),
        str(pattern.get("user_prompt", "")),
    )
    if flag in ("1", "true", "yes", "on"):
        return prof.markov_hats or prof.name in TRAP_RAGE_GENRES
    return prof.markov_hats


def clear_steps(
    pattern: dict[str, Any],
    start_beat: float,
    end_beat: float,
    track_keys: tuple[str, ...],
) -> int:
    """Remove notes overlapping ``[start_beat, end_beat)`` — vocal pocket / drop gap."""
    tracks = pattern.setdefault("tracks", {})
    if not isinstance(tracks, dict):
        return 0
    removed = 0
    for key in track_keys:
        raw = tracks.get(key)
        if not isinstance(raw, list):
            continue
        kept: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            if _note_end(entry) <= start_beat or _note_step(entry) >= end_beat:
                kept.append(entry)
            else:
                removed += 1
        tracks[key] = kept
    return removed


def _is_808_track(track: str) -> bool:
    return track in SUB_808_TRACK_KEYS


def _kick_hit_times(kick: list[dict[str, Any]]) -> list[float]:
    return sorted({round(_note_step(k), 5) for k in kick})


def compose_kick_locked_808(
    pattern: dict[str, Any],
    *,
    melody: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Deterministic 808 — one sustain per kick, length until the next kick only.

    Markov hat rolls must never touch this lane. Notes land only on kick steps;
    no overlaps so sidechain / ducking does not swallow the sub.
    """
    from music_theory import name_for_midi
    from sub_808_gen import SUB_HI, SUB_LO, _bar_root_midi, _clamp_sub_midi

    kick = track_notes(pattern, "kick")
    if not kick:
        return []

    melody = melody if melody is not None else track_notes(pattern, "melody_lead")
    bars = max(1, _bar_count(pattern))
    song_end = bars * BEATS_PER_BAR
    kick_times = _kick_hit_times(kick)
    notes: list[dict[str, Any]] = []

    from style_corpus import progression_roots_pc

    roots_pc = progression_roots_pc(pattern)

    for index, hit in enumerate(kick_times):
        next_hit = kick_times[index + 1] if index + 1 < len(kick_times) else song_end
        length = round(next_hit - hit, 5)
        if length < MIN_NOTE_LEN:
            continue
        bar = max(0, int(hit // BEATS_PER_BAR))
        root_midi = _clamp_sub_midi(_bar_root_midi(melody, bar, roots_pc=roots_pc))
        if root_midi < SUB_LO or root_midi > SUB_HI:
            root_midi = _clamp_sub_midi(root_midi)
        notes.append(
            {
                "time_step": hit,
                "note": name_for_midi(root_midi),
                "length": length,
                "velocity": 127,
                "sub_808_gen": True,
                "kick_locked": True,
            }
        )

    return notes


def rebuild_sub_808_from_kick(pattern: dict[str, Any]) -> int:
    """Replace contaminated / roll-style 808 with kick-locked sustains."""
    if pattern.get("plg_midi_808"):
        return 0  # a real 808 line from the user's MIDI corpus owns the lane
    tracks = pattern.setdefault("tracks", {})
    if not isinstance(tracks, dict):
        return 0
    before = len(track_notes(pattern, "sub_808"))
    bass = compose_kick_locked_808(pattern)
    tracks["sub_808"] = bass
    removed = max(0, before - len(bass))
    if before != len(bass):
        logger.info(
            "808 kick-lock: replaced %d notes with %d kick sustains",
            before,
            len(bass),
        )
    return removed


def _build_hat_roll(
    start_beat: float,
    duration_beats: float,
    rng: random.Random,
    *,
    base_velocity: int = 92,
) -> list[dict[str, Any]]:
    """1/32 or 1/24 cascade for ``duration_beats`` (1/4 or 1/2 bar)."""
    step = rng.choice(ROLL_STEP_CHOICES)
    end_beat = start_beat + duration_beats
    notes: list[dict[str, Any]] = []
    t = start_beat
    count = max(2, int(duration_beats / step))
    index = 0
    while t < end_beat - 1e-6:
        prog = index / max(1, count - 1)
        velocity = int(base_velocity + prog * 35) + rng.randint(-4, 6)
        note_len = min(step * 0.92, end_beat - t)
        notes.append(
            {
                "time_step": round(t, 5),
                "note": HAT_NOTE,
                "length": round(max(MIN_NOTE_LEN, note_len), 5),
                "velocity": max(52, min(127, velocity)),
                "hat_roll": True,
                "symbolic_ml": True,
            }
        )
        t += step
        index += 1
    return notes


def compose_markov_hats(
    bars: int,
    rng: random.Random,
    *,
    base_velocity: int = 90,
) -> list[dict[str, Any]]:
    """Generate hi-hats via Markov transitions (standard 1/8 vs hat-roll bursts)."""
    total_beats = bars * BEATS_PER_BAR
    notes: list[dict[str, Any]] = []
    state = "standard"
    t = 0.0

    while t < total_beats - 1e-6:
        if state == "standard":
            notes.append(
                {
                    "time_step": round(t, 5),
                    "note": HAT_NOTE,
                    "length": round(EIGHTH_STEP * 0.4, 5),
                    "velocity": max(52, min(118, base_velocity + rng.randint(-6, 8))),
                    "hat_steady": True,
                    "symbolic_ml": True,
                }
            )
            roll = rng.random() < HAT_MARKOV["standard"]["roll"]
            if roll:
                duration = rng.choice(ROLL_DURATION_CHOICES)
                roll_end = min(total_beats, t + duration)
                notes.extend(_build_hat_roll(t, roll_end - t, rng, base_velocity=base_velocity))
                t = roll_end
                state = "roll"
            else:
                t += EIGHTH_STEP
                state = "standard"
        else:
            t += EIGHTH_STEP
            state = "standard"

    return sorted(notes, key=_note_step)


def generate_snare_roll(
    start_beat: float,
    end_beat: float,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Snare roll with velocity ladder 30 % → 100 % across the drop pocket."""
    span = max(SNARE_ROLL_STEP, end_beat - start_beat)
    count = max(2, int(span / SNARE_ROLL_STEP))
    notes: list[dict[str, Any]] = []
    t = start_beat
    for index in range(count):
        if t >= end_beat - 1e-6:
            break
        prog = index / max(1, count - 1)
        velocity = int(
            SNARE_ROLL_VEL_MIN + prog * (SNARE_ROLL_VEL_MAX - SNARE_ROLL_VEL_MIN)
        )
        note_len = min(SNARE_ROLL_STEP * 0.9, end_beat - t)
        notes.append(
            {
                "time_step": round(t, 5),
                "note": SNARE_NOTE,
                "length": round(max(MIN_NOTE_LEN, note_len), 5),
                "velocity": max(1, min(127, velocity + rng.randint(-2, 4))),
                "snare_roll": True,
                "symbolic_ml": True,
            }
        )
        t += SNARE_ROLL_STEP
    return notes


def _semitone_distance(a: int, b: int) -> int:
    """Absolute semitone distance ignoring octave register."""
    raw = abs(a - b)
    return min(raw % 12, 12 - (raw % 12)) if raw else 0


def _scale_midi_pool(root_pc: int, scale: str, lo: int, hi: int) -> list[int]:
    from music_theory import SCALES

    intervals = SCALES.get(scale, SCALES["natural_minor"])
    pcs = {(root_pc + i) % 12 for i in intervals}
    return sorted(m for m in range(lo, hi + 1) if m % 12 in pcs)


def _pick_strict_semitone_step(
    prev_midi: int,
    pool: list[int],
    rng: random.Random,
    *,
    history: list[int] | None = None,
) -> int:
    """Dark stepwise motion WITH pitch variety.

    The old semitone-only rule collapsed sparse pools into a two-note F↔F#
    see-saw for the whole song — unlistenable. Now: mostly small scale steps
    (1–2 semitones), occasional darker reaches (3–5), and a hard ban on
    immediate A-B-A-B ping-pong, so a 4-note hook lands 3–4 distinct pitches
    while keeping the sinister half-step colour."""
    history = history or []
    banned: set[int] = set()
    if len(history) >= 2 and history[-2] != prev_midi:
        # returning to where we just came from twice in a row = see-saw
        banned.add(history[-2])
    if len(history) >= 3 and history[-1] == history[-3]:
        banned.add(history[-2])

    def _candidates(max_dist: int) -> list[int]:
        return [
            cand for cand in pool
            if cand != prev_midi
            and cand not in banned
            and _semitone_distance(prev_midi, cand) <= max_dist
            and abs(cand - prev_midi) <= 7  # no wide register leaps
        ]

    near = _candidates(2)
    far = [c for c in _candidates(5) if c not in near]
    choices: list[int] = []
    weights: list[float] = []
    for cand in near:
        choices.append(cand)
        weights.append(3.0 if _semitone_distance(prev_midi, cand) == 1 else 2.0)
    for cand in far:
        choices.append(cand)
        weights.append(1.0)
    if choices:
        return rng.choices(choices, weights=weights, k=1)[0]
    # Everything banned/empty — octave of the same pc, else stay.
    pc = prev_midi % 12
    octave_neighbors = [cand for cand in pool if cand % 12 == pc and cand != prev_midi]
    if octave_neighbors:
        return min(octave_neighbors, key=lambda m: abs(m - prev_midi))
    return prev_midi


def _hook_start_midi(key: dict[str, Any], pool: list[int], rng: random.Random) -> int:
    """Root, phrygian b2, or fifth — never a random out-of-key spawn."""
    root_pc = int(key["root_pc"])
    fifth_pc = (root_pc + 7) % 12
    phrygian_b2 = (root_pc + 1) % 12
    anchors = {root_pc, fifth_pc}
    if str(key.get("scale")) == "phrygian":
        anchors.add(phrygian_b2)
    starters = [m for m in pool if m % 12 in anchors]
    return rng.choice(starters or pool[:3])


def _key_cfg_from_pattern(pattern: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Map plg_variety key/scale (or pattern fields) to an Opium hook key."""
    from music_theory import key_label
    from pattern_utils import NOTE_INDEX

    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    key_raw = str(variety.get("key") or pattern.get("key") or "E").strip().split()[0]
    scale_raw = str(variety.get("scale") or pattern.get("scale") or "minor").lower()
    scale_map = {
        "minor": "natural_minor",
        "natural_minor": "natural_minor",
        "harmonic_minor": "natural_minor",
        "phrygian": "phrygian",
    }
    scale_id = scale_map.get(scale_raw, "natural_minor")
    root_pc = NOTE_INDEX.get(key_raw)
    if root_pc is None and len(key_raw) >= 2:
        root_pc = NOTE_INDEX.get(key_raw[:2])
    if root_pc is None:
        dark_roots = (4, 6, 9, 11, 1, 3)  # E, F#, A, B, C#, D#
        root_pc = dark_roots[rng.randint(0, len(dark_roots) - 1)]
    return {"root_pc": int(root_pc), "scale": scale_id, "label": key_label(int(root_pc), scale_id)}


def _melody_needs_opium_hook(pattern: dict[str, Any], *, min_notes: int = 4) -> bool:
    """True when melody_lead is empty or too thin for a stadium hook."""
    melody = track_notes(pattern, "melody_lead")
    if not melody:
        return True
    bars = max(1, _bar_count(pattern))
    window_beats = min(bars, 8) * BEATS_PER_BAR
    in_window = [n for n in melody if _note_step(n) < window_beats]
    if len(in_window) < min_notes:
        return True
    sustained = sum(1 for n in in_window if float(n.get("length", 0.0)) >= 2.0)
    return sustained < 2


def compose_opium_melody_hook(
    rng: random.Random,
    *,
    key_cfg: dict[str, Any] | None = None,
    melody_variant: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """4-bar syncopated hook — 4 sustained half/whole notes, strict semitone motion."""
    from music_theory import name_for_midi

    if key_cfg is None:
        raise RuntimeError("compose_opium_melody_hook requires key_cfg")
    key = key_cfg
    pool = _scale_midi_pool(
        int(key["root_pc"]),
        str(key["scale"]),
        OPIUM_MELODY_LO,
        OPIUM_MELODY_HI,
    )
    if not pool:
        raise RuntimeError("Opium melody pool empty — invalid dark key config.")

    variant = int(melody_variant) if melody_variant is not None else rng.randint(0, 999)
    midi = _hook_start_midi(key, pool, rng)
    if midi in pool:
        start_idx = (pool.index(midi) + (variant // len(OPIUM_HOOK_BLUEPRINTS))) % len(pool)
        midi = pool[start_idx]
    blueprint = OPIUM_HOOK_BLUEPRINTS[variant % len(OPIUM_HOOK_BLUEPRINTS)]
    notes: list[dict[str, Any]] = []
    walked: list[int] = []
    for start_beat, length in blueprint:
        length = float(length)
        if length not in OPIUM_SUSTAIN_LENGTHS:
            length = HALF_NOTE_BEATS if length < WHOLE_NOTE_BEATS else WHOLE_NOTE_BEATS
        notes.append(
            {
                "time_step": round(start_beat, 5),
                "note": name_for_midi(midi),
                "length": round(length, 5),
                "velocity": rng.randint(MELODY_VEL_LO, MELODY_VEL_HI),
                "symbolic_ml": True,
                "opium_hook": True,
                "plg_melody_lane": "main",
            }
        )
        walked.append(midi)
        midi = _pick_strict_semitone_step(midi, pool, rng, history=walked)
    # A hook that landed on <3 distinct pitches is a see-saw — force the last
    # note onto a fresh chord-adjacent pitch (fifth/fourth of the root).
    if len({parse_note_name(str(n["note"])) for n in notes}) < 3 and len(notes) >= 3:
        root_pc = int(key["root_pc"])
        for anchor_pc in ((root_pc + 7) % 12, (root_pc + 5) % 12, (root_pc + 3) % 12):
            fresh = [m for m in pool if m % 12 == anchor_pc and m not in walked]
            if fresh:
                notes[-1]["note"] = name_for_midi(
                    min(fresh, key=lambda m: abs(m - walked[-1]))
                )
                break
    return notes, key


def tile_melody_loop(
    hook: list[dict[str, Any]],
    total_bars: int,
    *,
    loop_bars: int = OPIUM_LOOP_BARS,
) -> list[dict[str, Any]]:
    """Tile a 4-bar hook across the full arrangement."""
    loop_len = loop_bars * BEATS_PER_BAR
    song_end = total_bars * BEATS_PER_BAR
    out: list[dict[str, Any]] = []
    offset = 0.0
    while offset < song_end - 1e-6:
        for entry in hook:
            t = _note_step(entry) + offset
            if t >= song_end:
                continue
            note = copy.deepcopy(entry)
            note["time_step"] = round(t, 5)
            note["length"] = round(
                min(float(entry.get("length", 2.0)), song_end - t),
                5,
            )
            out.append(note)
        offset += loop_len
    return sorted(out, key=_note_step)


def apply_opium_octave_jumps(
    notes: list[dict[str, Any]],
    rng: random.Random,
    *,
    loop_bars: int = OPIUM_LOOP_BARS,
) -> list[dict[str, Any]]:
    """Bars 3–4 of each loop: duplicate 1–2 hook tones +12 or +24 (soaring Opium synth)."""
    from music_theory import name_for_midi
    from pattern_utils import parse_note_name

    if not notes:
        return []

    out = [copy.deepcopy(n) for n in notes]
    loop_len = loop_bars * BEATS_PER_BAR
    bar3_start = CARSON_JUMP_BAR_START * BEATS_PER_BAR
    bar4_end = CARSON_JUMP_BAR_END * BEATS_PER_BAR
    song_end = max(_note_end(n) for n in notes)

    offset = 0.0
    while offset < song_end - 1e-6:
        window_start = offset + bar3_start
        window_end = offset + bar4_end
        candidates = [
            n
            for n in notes
            if window_start <= _note_step(n) < window_end and n.get("opium_hook")
        ]
        if candidates:
            pick_count = min(len(candidates), rng.randint(1, 2))
            for src in rng.sample(candidates, k=pick_count):
                try:
                    base_midi = parse_note_name(str(src.get("note", "E4")))
                except ValueError:
                    continue
                jump = rng.choice((12, 24))
                jump_midi = base_midi + jump
                if jump_midi > 100:
                    jump_midi = base_midi + 12
                layer = {
                    "time_step": round(_note_step(src), 5),
                    "note": name_for_midi(jump_midi),
                    "length": round(float(src.get("length", 2.0)), 5),
                    "velocity": max(
                        72,
                        min(127, int(int(src.get("velocity", 108)) * 0.82)),
                    ),
                    "symbolic_ml": True,
                    "opium_hook": True,
                    "opium_octave_jump": True,
                    "plg_melody_lane": "main",
                    "carson_jump": True,
                }
                out.append(layer)
        offset += loop_len

    return sorted(out, key=_note_step)


def compose_opium_counter_melody(
    rng: random.Random,
    key_cfg: dict[str, Any],
    *,
    total_bars: int,
) -> list[dict[str, Any]]:
    """Sparse C6–C7 accents — bar 4 and 8 of each 8-bar phrase only."""
    from music_theory import name_for_midi, snap_pc_to_scale

    counter: list[dict[str, Any]] = []
    for bar_1idx in range(1, total_bars + 1):
        if bar_1idx % OPIUM_PHRASE_BARS not in (4, 0):
            continue
        bar_start = (bar_1idx - 1) * BEATS_PER_BAR
        hit = bar_start + rng.choice((0.0, 2.5, 3.5))
        midi = rng.randint(COUNTER_MIDI_LO, COUNTER_MIDI_HI)
        midi = snap_pc_to_scale(midi, int(key_cfg["root_pc"]), str(key_cfg["scale"]))
        while midi < COUNTER_MIDI_LO:
            midi += 12
        while midi > COUNTER_MIDI_HI:
            midi -= 12
        counter.append(
            {
                "time_step": round(hit, 5),
                "note": name_for_midi(midi),
                "length": round(rng.choice((HALF_NOTE_BEATS * 0.5, HALF_NOTE_BEATS)), 5),
                "velocity": rng.randint(72, 95),
                "pan": rng.choice((40, 88)),
                "symbolic_ml": True,
                "opium_counter": True,
                "plg_melody_lane": "counter",
            }
        )
    return sorted(counter, key=_note_step)


def apply_opium_melody(
    pattern: dict[str, Any],
    rng: random.Random,
    *,
    bars: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Build tiled PLG MIDI + Counter Melody for Dark Rage / Opium."""
    total_bars = bars if bars is not None else max(8, _bar_count(pattern))
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    melody_variant = variety.get("melody_variant")
    key_cfg = _key_cfg_from_pattern(pattern, rng)
    hook, key_cfg = compose_opium_melody_hook(
        rng,
        key_cfg=key_cfg,
        melody_variant=int(melody_variant) if melody_variant is not None else None,
    )
    melody = tile_melody_loop(hook, total_bars)
    melody = apply_opium_octave_jumps(melody, rng)
    counter = compose_opium_counter_melody(rng, key_cfg, total_bars=total_bars)
    return melody, counter, key_cfg


def ensure_opium_melody_if_needed(
    pattern: dict[str, Any],
    *,
    profile: GenreProfile | None = None,
    rng: random.Random | None = None,
) -> bool:
    """Inject a tiled Opium hook when melody_lead is empty or not hook-shaped.

    Runs even when Markov hats / symbolic drums are off (e.g. generic trap
    profile) so every dark-trap beat gets a real sustained hook, not random plinks.
    """
    if pattern.get("plg_symbolic_opium_melody"):
        return False
    if pattern.get("plg_midi_melody"):
        return False  # the user's own (varied) MIDI line owns the lead
    prof = profile or profile_for(
        str(pattern.get("style", "")),
        str(pattern.get("user_prompt", "")),
    )
    if not local_opium_melody_mode(
        str(pattern.get("user_prompt", "")),
        str(pattern.get("style", "")),
        genre=prof.name,
    ):
        return False
    if not _melody_needs_opium_hook(pattern):
        return False

    roll_rng = rng or _rng_for(pattern)
    bars = max(8, _bar_count(pattern))
    lead, counter, key_cfg = apply_opium_melody(pattern, roll_rng, bars=bars)
    tracks = pattern.setdefault("tracks", {})
    if isinstance(tracks, dict):
        tracks["melody_lead"] = lead
        tracks["counter_melody"] = counter
    pattern["plg_symbolic_opium_melody"] = True
    pattern["plg_offline_melody"] = True
    pattern["plg_melody_repaired"] = True
    pattern["plg_key"] = str(key_cfg["label"])
    rebuild_sub_808_from_kick(pattern)
    meta = pattern.setdefault("metadata", {})
    if isinstance(meta, dict):
        meta["symbolic_opium_melody"] = len(lead)
        meta["symbolic_opium_counter"] = len(counter)
        meta["symbolic_opium_key"] = pattern.get("plg_key")
    logger.info(
        "Opium melody repair: %d lead / %d counter notes, key=%s, genre=%s",
        len(lead),
        len(counter),
        pattern.get("plg_key"),
        prof.name,
    )
    return True


def drop_generator(
    pattern: dict[str, Any],
    rng: random.Random,
    *,
    drop_bar: int = DROP_BAR_1IDX,
    probability: float = DROP_PROBABILITY,
) -> bool:
    """Bar-8 vocal pocket: clear hats/percussion + snare roll (90 % by default)."""
    bars = _bar_count(pattern)
    if bars < drop_bar:
        return False
    if rng.random() > probability:
        return False

    start = (drop_bar - 1) * BEATS_PER_BAR
    end = drop_bar * BEATS_PER_BAR
    removed = clear_steps(pattern, start, end, DROP_CLEAR_TRACKS)
    roll = generate_snare_roll(start, end, rng)

    tracks = pattern.setdefault("tracks", {})
    if not isinstance(tracks, dict):
        return False
    snare = tracks.get("snare")
    if not isinstance(snare, list):
        snare = []
    tracks["snare"] = sorted(snare + roll, key=_note_step)

    meta = pattern.setdefault("metadata", {})
    if isinstance(meta, dict):
        meta["symbolic_drop"] = True
        meta["symbolic_drop_bar"] = drop_bar
        meta["symbolic_drop_cleared"] = removed
        meta["symbolic_snare_roll_notes"] = len(roll)

    logger.info(
        "Symbolic drop: bar %d cleared %d notes, injected %d snare-roll hits",
        drop_bar,
        removed,
        len(roll),
    )
    return True


def apply_symbolic_composition(
    pattern: dict[str, Any],
    *,
    profile: GenreProfile | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Apply Trap/Rage Symbolic ML — Markov hats + optional bar-8 drop."""
    out = copy.deepcopy(pattern)
    prof = profile or profile_for(
        str(out.get("style", "")),
        str(out.get("user_prompt", "")),
    )
    if not should_apply_symbolic(out, prof):
        return out

    roll_rng = rng or _rng_for(out)
    bars = max(8, _bar_count(out))
    hats = compose_markov_hats(bars, roll_rng)
    tracks = out.setdefault("tracks", {})
    if isinstance(tracks, dict):
        tracks["hi_hats"] = hats
        rebuild_sub_808_from_kick(out)

    drop_generator(out, roll_rng)
    rebuild_sub_808_from_kick(out)

    melody_notes = 0
    counter_notes = 0
    # 2026-07 audit: this used to overwrite melody_lead/counter_melody
    # unconditionally for rage/opium — silently replacing the user's own
    # uploaded MIDI with a composed-from-scratch hook, bypassing the
    # MIDI-required architecture entirely (unlike ensure_opium_melody_if_needed,
    # which already had this same guard — this call site was simply missed).
    if prof.name in TRAP_RAGE_GENRES and not out.get("plg_midi_melody"):
        lead, counter, key_cfg = apply_opium_melody(out, roll_rng, bars=bars)
        if isinstance(tracks, dict):
            tracks["melody_lead"] = lead
            tracks["counter_melody"] = counter
        out["plg_symbolic_opium_melody"] = True
        out["plg_offline_melody"] = True
        out["plg_key"] = str(key_cfg["label"])
        melody_notes = len(lead)
        counter_notes = len(counter)
        rebuild_sub_808_from_kick(out)

    meta = out.setdefault("metadata", {})
    if isinstance(meta, dict):
        meta["symbolic_ml"] = True
        meta["symbolic_markov_hats"] = len(hats)
        meta["symbolic_genre"] = prof.name
        if melody_notes:
            meta["symbolic_opium_melody"] = melody_notes
            meta["symbolic_opium_counter"] = counter_notes
            meta["symbolic_opium_key"] = out.get("plg_key")

    out["plg_symbolic_composed"] = True
    out["plg_hat_rolls"] = sum(1 for n in hats if n.get("hat_roll"))
    logger.info(
        "Symbolic ML: %d hat notes (%d rolls), %d melody / %d counter, genre=%s",
        len(hats),
        out["plg_hat_rolls"],
        melody_notes,
        counter_notes,
        prof.name,
    )
    return out


def apply_drum_symbolic_pipeline(
    pattern: dict[str, Any],
    *,
    profile: GenreProfile | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Full pre-FLP drum chain: Symbolic ML → validator.

    ``temperature`` is accepted and ignored: it only ever fed the GrooVAE
    humanization step, retired 2026-08-27 (the project issue log (not included)). Callers still pass
    it, so the signature stays put.
    """
    out = apply_symbolic_composition(pattern, profile=profile)

    from validator import validate_and_sanitize_pattern

    out, report = validate_and_sanitize_pattern(out)
    rebuild_sub_808_from_kick(out)
    meta = out.setdefault("metadata", {})
    if isinstance(meta, dict):
        meta["validator_ok"] = report.ok
        meta["validator_fixes"] = report.fixes
    return out

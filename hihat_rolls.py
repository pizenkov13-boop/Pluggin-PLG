"""Hi-hat generator — steady 1/8 grid with sparse 1/32 rolls + calm Markov velocity."""

from __future__ import annotations

import os
import random
from copy import deepcopy
from typing import Any

from drum_defaults import _bar_count
from hat_roll_engine import inject_snare_hat_rolls, snare_hit_steps
from pattern_utils import track_notes

BEATS_PER_BAR = 4.0
HAT_NOTE = "C5"
EIGHTH_LEN = 0.08  # short gate — Cut Itself + short sample, no metallic hang
ROLL_PROB = 0.30  # ~30% of backbeats explode into a machine-gun run (Yeat pocket)
ROLL_DEPTH = 0.3125  # 5 sixteenths before snare
# Hot / quiet-section values that used to be inline literals in apply_trap_hi_hats.
ROLL_PROB_HOT = 0.42
ROLL_PROB_QUIET = 0.15
ROLL_DEPTH_HOT = 0.375
HAT_VEL_MIN = 44
HAT_VEL_MAX = 100

_HAT_STATES = ("ghost", "normal", "accent")
_HAT_VEL_FACTOR = {"ghost": 0.50, "normal": 0.78, "accent": 0.92}
# Steady trap line — stay on "normal", rolls stay hot.
_HAT_TRANSITIONS = {
    "ghost": (0.08, 0.78, 0.14),
    "normal": (0.12, 0.72, 0.16),
    "accent": (0.18, 0.62, 0.20),
}


def _note_step(entry: dict[str, Any]) -> float:
    return float(entry.get("time_step", 0))


def build_steady_eighths(bars: int, *, velocity: int = 78) -> list[dict[str, Any]]:
    """Straight 1/8 hi-hat line — no melody bleed, no random gaps."""
    return [
        {
            "time_step": round(bar * BEATS_PER_BAR + step * 0.5, 5),
            "note": HAT_NOTE,
            "length": EIGHTH_LEN,
            "velocity": velocity,
            "hat_steady": True,
        }
        for bar in range(bars)
        for step in range(8)
    ]


def base_hat_line(pattern: dict[str, Any], bars: int) -> list[dict[str, Any]]:
    """The base line the hat pipeline starts from: a rhythm from ``hat_patterns`` (picked per section by the generation's
    variety seed and the melody's busyness), or straight eighths when there is no seed / the library is off."""
    try:
        from hat_patterns import build_line

        line = build_line(pattern, bars)
    except Exception:  # noqa: BLE001 - the library is a choice of base line, never a reason to fail a beat
        line = None
    return line if line else build_steady_eighths(bars)


# A calm line (library-based) rolls less in front of the backbeat: a roll before EVERY backbeat in the same place was
# the other half of "one pattern".
_CELL_ROLL_SCALE = 0.5


# --- per-generation variation (2026-10) ---------------------------------------
# The base line is one hard-wired skeleton (straight eighths): five beats on one
# MIDI shared ~91 % of their hat onsets. ``vary_hats`` re-voices the FINISHED hat
# line per 2-bar block from the generation's variety seed: ghost sixteenths
# between eighths, a weak eighth played a sixteenth early (a "shift"), accent
# shifts. Still straight eighths (no style change): beat one and the backbeat
# eighth are never dropped and at least five of the eight eighths of a bar stay.
#
# Where it runs matters. beat_humanize calls it as the LAST step of the hat block,
# after every pass that draws from the shared generation rng (rolls, dynamics,
# swing, register, panning, forward lag), and it draws from its OWN rng seeded
# from the variety seed: the shared stream, and so every other lane's micro-timing
# and velocity, is byte-identical with and without it. Ghosts and shifted notes
# carry ``hat_ghost`` so the low end does not read them as a roll
# (midi_lowend_director.rhythmic_anchors).
#
# Why additive, and why a shift keeps the note count: a LATER stage
# (beat_humanize.enforce_midi_section_density) refills any bar of a verse / hook
# below the density card's hat floor with the plain eighth skeleton, which would
# put a plainly dropped eighth straight back and erase the variation (measured:
# the first 16 bars of a quiet melody were identical across seeds after it). A
# bar that keeps its note count is left alone.
# ``PLG_HAT_VARIATION=off`` and a run without a seed leave the line untouched.
_FEEL_WEIGHTS = {
    "hot": {"straight": 0.35, "ghost": 5.0, "shift": 3.0, "accent": 1.0},
    "verse": {"straight": 0.5, "ghost": 3.5, "shift": 4.0, "accent": 1.0},
    "calm": {"straight": 0.35, "ghost": 3.5, "shift": 4.0, "accent": 0.5},
}
_MIN_EIGHTHS_PER_BAR = 5
GHOST_VEL = (34, 48)
ACCENT_BOOST = 9


def variation_enabled() -> bool:
    return os.environ.get("PLG_HAT_VARIATION", "").strip().lower() not in ("0", "off", "false", "no")


def _variation_seed(pattern: dict[str, Any]) -> int | None:
    """The generation's variety seed: on the pattern when it is stamped, else the
    active generation context (``plg_variety`` is only stamped after the humanizer)."""
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    seed = variety.get("seed")
    if isinstance(seed, int) and not isinstance(seed, bool):
        return int(seed)
    try:
        from generation_variety import active_seed

        return active_seed()
    except Exception:  # noqa: BLE001 - no seed means no variation, never an error
        return None


def _ghost(at: float, velocity: int, template: dict[str, Any] | None = None) -> dict[str, Any]:
    note = deepcopy(template) if template else {"note": HAT_NOTE, "length": round(EIGHTH_LEN * 0.8, 5)}
    note.update({"time_step": round(at, 5), "velocity": int(velocity), "hat_ghost": True})
    note.pop("hat_roll", None)
    return note


def vary_hats(hats: list[dict[str, Any]], pattern: dict[str, Any]) -> list[dict[str, Any]]:
    """Re-voice the finished hat line per 2-bar block; see the block comment above."""
    seed = _variation_seed(pattern)
    if seed is None or not variation_enabled() or not hats:
        return hats
    if any(n.get("hat_cell") for n in hats):
        return hats  # a library line is already per-seed and calm: no ghost / shift pass on top
    rng = random.Random(f"hat-variation|{seed}")
    intensity = rng.uniform(0.75, 1.0)
    arrangement = pattern.get("plg_arrangement") if isinstance(pattern.get("plg_arrangement"), list) else None
    steady = [n for n in hats if n.get("hat_steady") and not n.get("hat_roll") and not n.get("hat_ghost")]
    if not steady:
        return hats
    by_bar: dict[int, list[dict[str, Any]]] = {}
    for note in steady:
        by_bar.setdefault(int(_note_step(note) // BEATS_PER_BAR), []).append(note)

    def feel_for(block_bar: int) -> str:
        section = _section_for_beat(arrangement, block_bar * BEATS_PER_BAR)
        kind = "hot" if section in ("hook", "chorus", "drop") else ("verse" if section == "verse" else "calm")
        weights = {name: (w if name == "straight" else w * intensity) for name, w in _FEEL_WEIGHTS[kind].items()}
        names = list(weights)
        return rng.choices(names, weights=[weights[n] for n in names])[0]

    removed: set[int] = set()
    accented: dict[int, dict[str, Any]] = {}  # the input notes are never edited in place
    added: list[dict[str, Any]] = []
    feel = "straight"
    for bar in sorted(by_bar):
        if bar % 2 == 0:
            feel = feel_for(bar)
        slot = {
            int(round((_note_step(n) % BEATS_PER_BAR) * 2)) % 8: n
            for n in sorted(by_bar[bar], key=_note_step)
        }
        if feel == "ghost":
            candidates = sorted(slot)  # a sparse (phrase-thinned) bar still has its on-beat eighths
            rng.shuffle(candidates)
            for i in candidates[: rng.choice((1, 2, 2, 3))]:
                added.append(_ghost(_note_step(slot[i]) + 0.25, rng.randint(*GHOST_VEL)))
        elif feel == "shift" and len(slot) >= 4:
            # A weak eighth is played a sixteenth early instead: the bar keeps its
            # note count, the groove leans differently.
            pool = [i for i in (1, 2, 3, 5, 6, 7) if i in slot]
            rng.shuffle(pool)
            for i in pool[: rng.choice((1, 2))]:
                removed.add(id(slot[i]))
                added.append(_ghost(_note_step(slot[i]) - 0.25, int(slot[i].get("velocity", 70)), slot[i]))
        elif feel == "accent":
            for i, note in slot.items():
                if i % 2 == 1:
                    louder = dict(note)
                    louder["velocity"] = min(HAT_VEL_MAX, int(note.get("velocity", 78)) + ACCENT_BOOST)
                    accented[id(note)] = louder
    out = [accented.get(id(n), n) for n in hats if id(n) not in removed]
    occupied = [_note_step(n) for n in out]
    for note in added:
        if not any(abs(_note_step(note) - t) < 0.2 for t in occupied):
            out.append(note)
            occupied.append(_note_step(note))
    out.sort(key=_note_step)
    return out


def apply_markov_hat_dynamics(
    notes: list[dict[str, Any]],
    rng: random.Random,
    *,
    peak: int = 92,
    vel_min: int = HAT_VEL_MIN,
    vel_max: int = HAT_VEL_MAX,
) -> list[dict[str, Any]]:
    """Markov velocity — mostly even 1/8s; roll bursts keep their ramp."""
    ordered = sorted((deepcopy(n) for n in notes), key=_note_step)
    state = "normal"
    for note in ordered:
        if note.get("hat_roll") or note.get("hat_ghost"):
            continue  # a roll keeps its ramp, a ghost its quiet level
        state = rng.choices(_HAT_STATES, weights=_HAT_TRANSITIONS[state])[0]
        vel = int(peak * _HAT_VEL_FACTOR[state]) + rng.randint(-3, 3)
        note["velocity"] = max(vel_min, min(vel_max, vel))
        note["hat_state"] = state
    return ordered


def _section_for_beat(arrangement: list[dict[str, Any]] | None, beat: float) -> str:
    if not arrangement:
        return ""
    bar = int(beat // BEATS_PER_BAR)
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        start = int(section.get("start_bar", 0))
        count = int(section.get("bars", 0))
        if start <= bar < start + count:
            return str(section.get("name", ""))
    return ""


def _punch_gaps(
    hats: list[dict[str, Any]],
    pattern: dict[str, Any],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Drop 1–2 eighths per bar in cold/verse sections; keep denser on hot/hook."""
    energy = pattern.get("plg_melody_energy") if isinstance(pattern.get("plg_melody_energy"), dict) else {}
    try:
        busyness = float(energy.get("busyness") or 0.0)
    except (TypeError, ValueError):
        busyness = 0.0
    pace = str(energy.get("pace") or "")
    arrangement = pattern.get("plg_arrangement") if isinstance(pattern.get("plg_arrangement"), list) else None
    by_bar: dict[int, list[dict[str, Any]]] = {}
    for note in hats:
        bar = int(_note_step(note) // BEATS_PER_BAR)
        by_bar.setdefault(bar, []).append(note)
    kept: list[dict[str, Any]] = []
    for bar, notes in sorted(by_bar.items()):
        section = _section_for_beat(arrangement, bar * BEATS_PER_BAR)
        hot = (
            section in ("hook", "chorus", "drop")
            or pace in ("racing", "driving")
            or busyness >= 0.65
        )
        if hot:
            kept.extend(notes)
            continue
        # Verse/intro: leave air — drop 1 or 2 off-beat eighths (never step 0).
        candidates = [n for n in notes if abs((_note_step(n) % BEATS_PER_BAR) - 0.0) > 0.01]
        drop_n = 1 if section in ("intro", "outro") or busyness < 0.35 else rng.choice((1, 2))
        drop_n = min(drop_n, max(0, len(candidates) - 2))
        if drop_n <= 0:
            kept.extend(notes)
            continue
        drop_set = {id(n) for n in rng.sample(candidates, k=drop_n)}
        for n in notes:
            if id(n) in drop_set:
                continue
            kept.append(n)
    return kept


def apply_trap_hi_hats(
    pattern: dict[str, Any],
    rng: random.Random,
    *,
    roll_sections: tuple[str, ...] | None = None,
    roll_windows: list[tuple[float, float]] | None = None,
    roll_prob: float = ROLL_PROB,
    roll_prob_hot: float = ROLL_PROB_HOT,
    roll_prob_quiet: float = ROLL_PROB_QUIET,
    roll_depth: float = ROLL_DEPTH,
    roll_depth_hot: float = ROLL_DEPTH_HOT,
    peak: int = 92,
    vel_min: int = HAT_VEL_MIN,
    vel_max: int = HAT_VEL_MAX,
) -> list[dict[str, Any]]:
    """Build 1/8 hats with verse air + section/energy-aware pre-snare rolls.

    Every keyword after ``roll_windows`` defaults to the value this function
    used before it was parametrized, so omitting them changes nothing.
    """
    bars = _bar_count(pattern)
    hats = base_hat_line(pattern, bars)
    cell_based = any(n.get("hat_cell") for n in hats)
    if cell_based:
        roll_prob, roll_prob_hot, roll_prob_quiet = (
            roll_prob * _CELL_ROLL_SCALE,
            roll_prob_hot * _CELL_ROLL_SCALE,
            roll_prob_quiet * _CELL_ROLL_SCALE,
        )
    else:
        hats = _punch_gaps(hats, pattern, rng)  # a library line has its own air

    energy = pattern.get("plg_melody_energy") if isinstance(pattern.get("plg_melody_energy"), dict) else {}
    try:
        busyness = float(energy.get("busyness") or 0.0)
    except (TypeError, ValueError):
        busyness = 0.0
    pace = str(energy.get("pace") or "")
    arrangement = pattern.get("plg_arrangement") if isinstance(pattern.get("plg_arrangement"), list) else None

    snare_steps = snare_hit_steps(pattern, roll_sections=roll_sections, roll_windows=roll_windows)
    backbeats = [s for s in snare_steps if abs((s % BEATS_PER_BAR) - 2.0) < 0.02]
    # Occasional off-backbeat accent for variety (~12% of non-backbeat snares).
    offbeats = [s for s in snare_steps if abs((s % BEATS_PER_BAR) - 2.0) >= 0.02]
    roll_targets = list(backbeats)
    for s in offbeats:
        if rng.random() < 0.12:
            roll_targets.append(s)

    for snare_at in roll_targets:
        section = _section_for_beat(arrangement, snare_at)
        hot = (
            section in ("hook", "chorus", "drop")
            or pace in ("racing", "driving")
            or busyness >= 0.6
        )
        section_prob = roll_prob_hot if hot else (roll_prob_quiet if section in ("verse", "intro", "outro") else roll_prob)
        depth = roll_depth_hot if hot else roll_depth
        if rng.random() > section_prob:
            continue
        hats = inject_snare_hat_rolls(
            hats,
            [snare_at],
            rng,
            roll_depth=depth,
            every_nth=1,
        )

    return apply_markov_hat_dynamics(hats, rng, peak=peak, vel_min=vel_min, vel_max=vel_max)

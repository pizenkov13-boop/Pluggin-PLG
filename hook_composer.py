"""Progression-following hook composer — real melodies for every non-trap lane.

The Opium engine (symbolic_composer) owns the trap family. Everywhere else the
melody used to be whatever the LLM emitted — often thin plinks. This engine
writes a proper hook the way a session writer would:

- a 1-bar rhythmic motif (genre-family cell) repeated with a call/response
  answer cell — recognizable, singable;
- every bar re-pitched onto THAT bar's chord from ``plg_progression``
  (voice-led: nearest chord tone to the previous note), passing tones from the
  scale — the melody follows the changes instead of ignoring them;
- 2-bar arch contour, a variation every 4th bar, phrase ends land on the
  chord root with a longer note.

Deterministic per rng. Runs only when the LLM melody is weak (few notes / no
pitch variety), so a good LLM hook is never overwritten.

Env:
    PLG_HOOK_COMPOSER=auto|on|off   (auto = on)
"""

from __future__ import annotations

import logging
import os
import random
from typing import Any

from music_theory import SCALES, key_label, name_for_midi
from pattern_utils import NOTE_INDEX, track_notes

logger = logging.getLogger("plg.hook_composer")

BEATS_PER_BAR = 4.0
# Match beat_humanize.MELODY_REGISTER so normalize_registers never folds us.
HOOK_LO = 60
HOOK_HI = 83
_CENTER = 70

# 1-bar rhythm cells per genre family: (offset_beats, length_beats) tuples.
_CELLS: dict[str, tuple[tuple[tuple[float, float], ...], ...]] = {
    "anthem": (
        ((0.0, 0.75), (1.0, 0.5), (1.75, 0.75), (2.5, 1.25)),
        ((0.0, 0.5), (0.75, 0.5), (1.5, 1.0), (3.0, 0.75)),
        ((0.5, 0.5), (1.5, 0.5), (2.0, 1.5)),
    ),
    "smooth": (
        ((0.5, 1.25), (2.0, 1.5)),
        ((0.0, 1.0), (1.75, 0.5), (2.5, 1.25)),
        ((0.75, 0.75), (2.25, 1.0), (3.5, 0.5)),
    ),
    "pluck": (
        ((0.0, 0.4), (0.5, 0.4), (1.5, 0.4), (2.0, 0.4), (3.0, 0.4), (3.5, 0.4)),
        ((0.0, 0.4), (1.0, 0.4), (1.5, 0.4), (2.5, 0.4), (3.5, 0.4)),
    ),
    "groove": (
        ((0.0, 0.4), (0.75, 0.35), (1.5, 0.5), (2.25, 0.4), (3.0, 0.6)),
        ((0.5, 0.4), (1.25, 0.4), (2.0, 0.5), (2.75, 0.4), (3.5, 0.4)),
    ),
    "epic": (
        tuple((i * 0.5, 0.45) for i in range(8)),
        ((0.0, 1.5), (2.0, 2.0)),
    ),
}

_FAMILY_OF_GENRE: dict[str, str] = {
    "pop": "anthem", "kpop": "anthem", "disco_pop": "anthem", "hyperpop": "anthem",
    "pop_rap": "anthem", "edm": "anthem", "trance": "anthem", "hardstyle": "anthem",
    "rnb": "smooth", "soul": "smooth", "lofi": "smooth", "dark_pop": "smooth",
    "boombap": "smooth",
    "house": "pluck", "techno": "pluck", "garage": "pluck", "dnb": "pluck",
    "dubstep": "pluck", "jersey": "pluck",
    "funk": "groove", "afrobeats": "groove", "amapiano": "groove",
    "reggaeton": "groove", "dancehall": "groove",
    "cinematic": "epic", "rock": "epic", "grind": "epic",
}


def _env_flag(name: str, default: str = "auto") -> str:
    return os.environ.get(name, default).strip().lower()


def hook_composer_enabled() -> bool:
    return _env_flag("PLG_HOOK_COMPOSER", "auto") not in ("0", "false", "no", "off")


def _scale_pool(root_pc: int, scale: str) -> list[int]:
    intervals = SCALES.get(scale, SCALES["natural_minor"])
    pcs = {(root_pc + i) % 12 for i in intervals}
    return [m for m in range(HOOK_LO, HOOK_HI + 1) if m % 12 in pcs]

def _nearest(pool: list[int], target: int, *, bias: int = 0) -> int:
    if not pool:
        return target
    return min(pool, key=lambda m: (abs(m - target - bias), abs(m - _CENTER)))


def _chord_pool(pool: list[int], tones_pc: list[int]) -> list[int]:
    chord = [m for m in pool if m % 12 in {int(pc) % 12 for pc in tones_pc}]
    return chord or pool


def _cell_variation(cell: tuple[tuple[float, float], ...]) -> tuple[tuple[float, float], ...]:
    """4th-bar answer: drop the last hit, add a pickup into the next bar."""
    base = cell[:-1] if len(cell) > 2 else cell
    return tuple(base) + ((3.75, 0.25),)


def compose_progression_hook(
    rng: random.Random,
    *,
    root_pc: int,
    scale: str,
    roots_pc: list[int],
    chord_tones_pc: list[list[int]],
    genre: str,
    bars: int = 8,
) -> list[dict[str, Any]]:
    family = _FAMILY_OF_GENRE.get(genre, "anthem")
    cells = _CELLS[family]
    call = cells[rng.randrange(len(cells))]
    answer = cells[rng.randrange(len(cells))]
    if answer == call and len(cells) > 1:
        answer = cells[(cells.index(call) + 1) % len(cells)]

    pool = _scale_pool(root_pc, scale)
    if not pool:
        return []

    n_bars = max(2, int(bars))
    tones0 = chord_tones_pc[0] if chord_tones_pc else [root_pc]
    prev = _nearest(_chord_pool(pool, list(tones0)), _CENTER)
    notes: list[dict[str, Any]] = []

    for bar in range(n_bars):
        cell = call if bar % 2 == 0 else answer
        if bar % 4 == 3:
            cell = _cell_variation(cell)
        tones = list(chord_tones_pc[bar % len(chord_tones_pc)]) if chord_tones_pc else [root_pc]
        chord = _chord_pool(pool, tones)
        direction = 1 if bar % 2 == 0 else -1  # 2-bar arch: call rises, answer falls

        last_idx = len(cell) - 1
        for idx, (offset, length) in enumerate(cell):
            strong = idx == 0 or float(offset).is_integer()
            phrase_end = bar % 4 == 3 and idx == last_idx
            if phrase_end:
                root_of_bar = int(roots_pc[bar % len(roots_pc)]) % 12 if roots_pc else root_pc
                midi = _nearest([m for m in pool if m % 12 == root_of_bar] or chord, prev)
                length = max(length, 1.0)
            elif strong or rng.random() < 0.6:
                midi = _nearest(chord, prev, bias=direction * 2)
            else:  # scale passing tone, stepwise
                steps = [m for m in pool if 0 < abs(m - prev) <= 2] or pool
                midi = _nearest(steps, prev, bias=direction)
            velocity = rng.randint(96, 108) if strong else rng.randint(84, 96)
            notes.append(
                {
                    "time_step": round(bar * BEATS_PER_BAR + offset, 5),
                    "note": name_for_midi(midi),
                    "length": round(float(length), 5),
                    "velocity": velocity,
                    "symbolic_ml": True,
                    "progression_hook": True,
                    "plg_melody_lane": "main",
                }
            )
            prev = midi

    return notes


def _melody_needs_hook(pattern: dict[str, Any], *, min_notes: int = 4) -> bool:
    """Weak LLM melody: sparse staccato plinks or a single repeated pitch.

    Long sustains are treated as intentional (pad/anthem lines are few notes by
    design), so a 2-note whole-tone melody is NOT replaced."""
    from pattern_utils import parse_note_name

    melody = track_notes(pattern, "melody_lead")
    if not melody:
        return True
    window = [n for n in melody if float(n.get("time_step", 0)) < 8 * BEATS_PER_BAR]
    if not window:
        return True
    sustained = any(float(n.get("length", 0.0)) >= 2.0 for n in window)
    if len(window) < min_notes and not sustained:
        return True
    pcs: set[int] = set()
    for note in window:
        try:
            pcs.add(parse_note_name(str(note.get("note", ""))) % 12)
        except (ValueError, TypeError, KeyError):
            continue
    return len(pcs) < 2


def ensure_progression_melody_if_needed(
    pattern: dict[str, Any],
    *,
    profile: Any = None,
    rng: random.Random | None = None,
) -> bool:
    """Compose a chord-following hook when the melody is weak (non-trap lanes).

    The Opium engine owns the trap family; this runs for every other genre and
    only when ``plg_progression`` is pinned (style corpus on).
    """
    if not hook_composer_enabled():
        return False
    if pattern.get("plg_symbolic_opium_melody") or pattern.get("plg_progression_hook"):
        return False
    if pattern.get("plg_midi_melody"):
        return False  # the user's own (varied) MIDI line owns the lead

    from genre_profiles import profile_for
    from symbolic_composer import TRAP_RAGE_GENRES

    prof = profile or profile_for(
        str(pattern.get("style", "")),
        str(pattern.get("user_prompt", "")),
    )
    if prof.name in TRAP_RAGE_GENRES:
        return False

    prog = pattern.get("plg_progression")
    if not isinstance(prog, dict):
        return False
    roots_pc = prog.get("roots_pc")
    chord_tones = prog.get("chord_tones_pc")
    if not isinstance(roots_pc, list) or not roots_pc or not isinstance(chord_tones, list):
        return False
    if not _melody_needs_hook(pattern):
        return False

    root_pc = NOTE_INDEX.get(str(prog.get("key", "A")), int(roots_pc[0]) % 12)
    scale = str(prog.get("scale") or "natural_minor")
    from drum_defaults import _bar_count

    bars = min(8, max(4, _bar_count(pattern)))
    roll_rng = rng or random.Random(hash(str(pattern.get("plg_beat_id", ""))) & 0xFFFFFFFF)
    hook = compose_progression_hook(
        roll_rng,
        root_pc=int(root_pc) % 12,
        scale=scale,
        roots_pc=[int(pc) % 12 for pc in roots_pc],
        chord_tones_pc=[list(t) for t in chord_tones if isinstance(t, list)] or [[root_pc]],
        genre=prof.name,
        bars=bars,
    )
    if not hook:
        return False

    tracks = pattern.setdefault("tracks", {})
    if not isinstance(tracks, dict):
        return False
    tracks["melody_lead"] = hook
    pattern["plg_progression_hook"] = True
    pattern["plg_offline_melody"] = True
    pattern["plg_melody_repaired"] = True
    pattern["plg_key"] = key_label(int(root_pc) % 12, scale)
    logger.info(
        "Hook composer: %d notes, %s, genre=%s (%s family)",
        len(hook),
        pattern["plg_key"],
        prof.name,
        _FAMILY_OF_GENRE.get(prof.name, "anthem"),
    )
    return True

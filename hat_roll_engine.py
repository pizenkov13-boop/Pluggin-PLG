"""Hat Rolling Engine — inject 1/32–1/64 rolls before snare hits (rage / dark-trap grid)."""

from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from pattern_utils import track_notes

BEATS_PER_BAR = 4.0
ROLL_STEP_32 = 0.125  # 1/32 in PLG beat grid (1.0 = quarter)
ROLL_STEP_64 = 0.0625
DEFAULT_ROLL_DEPTH = 0.375  # deeper pre-snare burst — more panicked rolls
HAT_NOTE = "C5"
DEFAULT_P_64 = 0.7  # share of rolls that run at 1/64 instead of 1/32


def _note_step(entry: dict[str, Any]) -> float:
    return float(entry.get("time_step", 0))


def _note_end(entry: dict[str, Any]) -> float:
    return _note_step(entry) + float(entry.get("length", 0.25))


def snare_hit_steps(
    pattern: dict[str, Any],
    *,
    bars: int | None = None,
    roll_sections: tuple[str, ...] | None = None,
    roll_windows: list[tuple[float, float]] | None = None,
) -> list[float]:
    """Collect snare onsets; fall back to beat 2 & 4 of each bar if empty.

    ``roll_windows`` (real per-section melody density, see
    beat_humanize._roll_eligible_windows) takes priority when given: a snare
    onset qualifies if its time falls inside any (start, end) window,
    regardless of section name — a sparse verse/outro can roll, a busy
    chorus/hook occurrence can't. Same "no beat 2&4 fallback" rule as the
    section-name mode below, since an empty result here is a real answer
    (every section is too busy), not a sign there's nothing to roll on.

    When ``roll_sections`` is given instead (no density info available), only
    snares tagged with one of those section NAMES trigger rolls — so the
    steady verse keeps its hats while the chorus / hook get the machine-gun
    rolls. Untagged snares (standalone / raw humanize calls) always qualify,
    and the empty-snare fallback is skipped in section-aware mode so we never
    roll over a verse.
    """
    snares = track_notes(pattern, "snare")
    if roll_windows is not None:
        steps = sorted({_note_step(n) for n in snares})
        return [t for t in steps if any(s <= t < e for s, e in roll_windows)]
    if roll_sections is not None:
        allowed = set(roll_sections)
        snares = [
            n for n in snares
            if not n.get("section") or str(n.get("section")) in allowed
        ]
        return sorted({_note_step(n) for n in snares})
    steps = sorted({_note_step(n) for n in snares})
    if steps:
        return steps

    max_step = 0.0
    for key in ("kick", "hi_hats", "sub_808", "melody_lead"):
        for entry in track_notes(pattern, key):
            max_step = max(max_step, _note_end(entry))

    bar_count = bars
    if bar_count is None:
        bar_count = max(4, int(max_step // BEATS_PER_BAR) + 1)

    fallback: list[float] = []
    for bar in range(bar_count):
        base = bar * BEATS_PER_BAR
        fallback.extend((base + 1.0, base + 3.0))
    return fallback


def _strip_hats_in_window(
    hats: list[dict[str, Any]],
    start: float,
    end: float,
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for entry in hats:
        step = _note_step(entry)
        note_end = _note_end(entry)
        if note_end <= start or step >= end:
            kept.append(entry)
    return kept


def _build_roll_notes(
    roll_start: float,
    roll_end: float,
    rng: random.Random,
    *,
    use_64: bool,
) -> list[dict[str, Any]]:
    step_size = ROLL_STEP_64 if use_64 else ROLL_STEP_32
    notes: list[dict[str, Any]] = []
    t = roll_start
    index = 0
    span = max(step_size, roll_end - roll_start)
    count = max(2, int(span / step_size))

    while t < roll_end - 1e-6:
        progress = index / max(1, count - 1)
        velocity = int(88 + progress * 39)  # 88 → 127 ramp into snare
        length = min(step_size * 0.92, roll_end - t)
        notes.append({
            "time_step": round(t, 5),
            "note": HAT_NOTE,
            "length": round(max(0.04, length), 5),
            "velocity": min(127, velocity + rng.randint(-4, 6)),
            "hat_roll": True,
        })
        t += step_size
        index += 1
    return notes


def inject_snare_hat_rolls(
    hats: list[dict[str, Any]],
    snare_steps: list[float],
    rng: random.Random,
    *,
    roll_depth: float = DEFAULT_ROLL_DEPTH,
    every_nth: int = 1,
    p_64: float = DEFAULT_P_64,
) -> list[dict[str, Any]]:
    """Replace hats in the pre-snare pocket with machine-gun 1/32 or 1/64 rolls."""
    if not snare_steps:
        return list(hats)

    out = sorted((deepcopy(n) for n in hats), key=_note_step)
    for i, snare_at in enumerate(sorted(snare_steps)):
        if every_nth > 1 and i % every_nth != 0:
            continue
        roll_end = snare_at
        roll_start = max(0.0, roll_end - roll_depth)
        if roll_end - roll_start < ROLL_STEP_64:
            continue

        out = _strip_hats_in_window(out, roll_start, roll_end)
        use_64 = rng.random() < p_64 or roll_depth <= 0.2  # lean into 1/64 vortex
        out.extend(_build_roll_notes(roll_start, roll_end, rng, use_64=use_64))

    return sorted(out, key=_note_step)


# ``apply_hat_rolling_engine`` (a whole-pattern wrapper around the two
# functions above) was removed 2026-08-15: it had zero callers, zero tests,
# and never supported the ``roll_windows`` density gate the live route uses —
# beat_humanize composes ``snare_hit_steps`` + ``inject_snare_hat_rolls``
# directly. Recorded in the project issue log (not included).

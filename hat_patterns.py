"""A library of hi-hat base lines: the hat lane no longer starts from ONE skeleton (straight eighths).

Until now every beat's hats were ``build_steady_eighths`` plus modifiers (gaps, pre-snare rolls, swing, thinning,
``vary_hats``): five beats on one MIDI shared about 70 % of their hat places, with one shape - eighths, a hole under the
backbeat, 1/32 rolls in front of it. This module offers a choice of base lines and picks one per SECTION from the
generation's variety seed and the melody's busyness:

* 42 eight-bar rhythms from the tester's hat packs (``assets/hat_patterns.json``, rhythm only - built by
  ``tools/build_hat_patterns.py``), and
* a few of our own families (straight eighths, a half-time hole, offbeat only, sixteenth pairs with an open hat, a
  skipping triplet).

Calm by construction: the notes OFF the eighth grid (the ornaments: sixteenths, triplets, 32nd rolls) are capped per bar by
the kind of section (none in an intro or an outro, one in a verse, two in a hook), they are quiet and every one of them
carries ``hat_ghost`` so the low end does not read them as a roll (``midi_lowend_director.rhythmic_anchors``); the eighths
carry ``hat_steady`` exactly like ``build_steady_eighths`` does, so every later stage treats them as before.

Everything here draws from its OWN rng seeded from the variety seed (never the shared generation stream), so no other lane's
micro-timing moves. ``PLG_HAT_LIBRARY=off`` and a run without a seed give the old straight eighths.
"""

from __future__ import annotations

import json
import math
import os
import random
from functools import lru_cache
from typing import Any

from plg_paths import bundle_dir

BEATS_PER_BAR = 4.0
HAT_NOTE = "C5"
EIGHTH_LEN = 0.08
GHOST_LEN = 0.04
STEADY_VELOCITY = 78
GHOST_VELOCITY = (28, 48)
OPEN_LEN = 0.30
UNIT = 48  # the library stores positions in 1/48 beat; an eighth is 24 units

#: ornaments (hits off the eighth grid) allowed per bar, by the kind of section
_ORNAMENT_CAP = {"calm": 0, "verse": 1, "hot": 2}
#: eighths per bar a section wants (the cell nearest to it is the likeliest)
_EIGHTHS_TARGET = {"calm": 4.0, "verse": 6.0, "hot": 7.5}
_HOT_SECTIONS = ("hook", "chorus", "drop")
_CALM_SECTIONS = ("intro", "outro")


def library_enabled() -> bool:
    return os.environ.get("PLG_HAT_LIBRARY", "").strip().lower() not in ("0", "off", "false", "no")


def _cell(cell_id: str, bars: int, hits: list[tuple[float, str]], source: str) -> dict[str, Any]:
    """A cell: ``hits`` are (beat in the cell, kind) with kind "eighth" / "orn" / "open"."""
    eighths = sum(1 for _t, kind in hits if kind != "orn")
    ornaments = sum(1 for _t, kind in hits if kind == "orn")
    return {
        "id": cell_id,
        "bars": bars,
        "hits": sorted(hits),
        "source": source,
        "eighths_per_bar": eighths / bars,
        "ornaments_per_bar": ornaments / bars,
    }


def _builtin_cells() -> list[dict[str, Any]]:
    def eighths(bar: int, skip: tuple[float, ...] = ()) -> list[tuple[float, str]]:
        return [(bar * 4 + 0.5 * i, "eighth") for i in range(8) if 0.5 * i not in skip]

    cells: list[dict[str, Any]] = []
    cells.append(_cell("own:straight", 1, eighths(0), "own"))
    cells.append(_cell("own:half_time", 2, eighths(0, (2.0, 2.5)) + eighths(1, (2.0, 2.5, 3.5)), "own"))
    cells.append(_cell("own:offbeat", 1, [(0.0, "eighth")] + [(0.5 + i, "eighth") for i in range(4)] + [(3.0, "eighth")], "own"))
    # sixteenth pairs ("and-a") with an open hat that closes the phrase
    four: list[tuple[float, str]] = []
    for bar in range(4):
        four += eighths(bar, (3.5,) if bar == 3 else ())
        if bar in (1, 3):
            four += [(bar * 4 + 1.75, "orn"), (bar * 4 + 3.75, "orn")]
    four.append((3 * 4 + 3.5, "open"))
    cells.append(_cell("own:sixteenth_open", 4, four, "own"))
    # a skipping triplet: bars 2 and 4 play a triplet of eighths on beat 4
    skip: list[tuple[float, str]] = []
    for bar in range(4):
        if bar % 2 == 1:
            skip += eighths(bar, (3.0, 3.5)) + [(bar * 4 + 3.0 + k / 3, "orn") for k in range(3)]
        else:
            skip += eighths(bar)
    cells.append(_cell("own:skip_triplet", 4, skip, "own"))
    return cells


@lru_cache(maxsize=1)
def library() -> tuple[dict[str, Any], ...]:
    """Every base line: the packs' 8-bar rhythms and our own families. Cached; never raises (an unreadable file = ours only)."""
    cells = _builtin_cells()
    try:
        path = bundle_dir() / "assets" / "hat_patterns.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        unit = int(data.get("unit") or UNIT)
        for raw in data.get("cells", []):
            hits = [
                (int(u) / unit, "eighth" if (int(u) % (unit // 2)) == 0 else "orn")
                for u in raw.get("hits", [])
            ]
            if hits:
                cells.append(_cell(f"pack:{raw.get('id', '?')}", int(raw.get("bars") or 8), hits, "pack"))
    except (OSError, ValueError, TypeError):
        pass
    return tuple(cells)


def _section_kind(name: str, busy: bool) -> str:
    lowered = (name or "").strip().lower()
    if lowered in _CALM_SECTIONS:
        return "calm"
    kind = "hot" if lowered in _HOT_SECTIONS else "verse"
    if busy and kind == "hot":
        return "verse"
    if busy and kind == "verse":
        return "calm"
    return kind


def _seed(pattern: dict[str, Any]) -> int | None:
    from hihat_rolls import _variation_seed

    return _variation_seed(pattern)


def _busy(pattern: dict[str, Any]) -> bool:
    energy = pattern.get("plg_melody_energy") if isinstance(pattern.get("plg_melody_energy"), dict) else {}
    try:
        busyness = float(energy.get("busyness") or 0.0)
    except (TypeError, ValueError):
        busyness = 0.0
    return busyness >= 0.6 or str(energy.get("pace") or "") == "racing"


def _sections(pattern: dict[str, Any], bars: int) -> list[tuple[str, int, int]]:
    arrangement = pattern.get("plg_arrangement") if isinstance(pattern.get("plg_arrangement"), list) else None
    out: list[tuple[str, int, int]] = []
    if arrangement:
        for section in arrangement:
            if not isinstance(section, dict):
                continue
            start = int(section.get("start_bar", 0) or 0)
            count = int(section.get("bars", 0) or 0)
            if count > 0 and start < bars:
                out.append((str(section.get("name") or "verse"), start, min(count, bars - start)))
    if not out:
        out = [("verse", 0, bars)]
    return out


def _pick(kind: str, previous: str | None, rng: random.Random) -> dict[str, Any]:
    target = _EIGHTHS_TARGET[kind]
    cells = library()
    weights = []
    for cell in cells:
        distance = (cell["eighths_per_bar"] - target) / (1.5 if kind == "calm" else 2.0)
        weight = math.exp(-distance * distance)
        if cell["id"] == previous:
            weight *= 0.1  # the same rhythm twice in a row reads as one pattern again
        # a calm section wants cells that are quiet by themselves, not just thinned
        weight *= 1.0 / (1.0 + cell["ornaments_per_bar"] * (2.0 if kind == "calm" else 0.5))
        weights.append(weight)
    return rng.choices(cells, weights=weights)[0]


def build_line(pattern: dict[str, Any], bars: int) -> list[dict[str, Any]] | None:
    """The whole song's base hat line, or None when there is no seed / the library is off (the caller keeps eighths)."""
    if not library_enabled() or bars <= 0:
        return None
    seed = _seed(pattern)
    if seed is None:
        return None
    busy = _busy(pattern)
    notes: list[dict[str, Any]] = []
    previous: str | None = None
    for index, (name, start, count) in enumerate(_sections(pattern, bars)):
        kind = _section_kind(name, busy)
        rng = random.Random(f"hat-cell|{seed}|{index}|{name}")
        cell = _pick(kind, previous, rng)
        previous = cell["id"]
        cap = _ORNAMENT_CAP[kind]
        for offset in range(count):
            bar = start + offset
            in_cell = offset % cell["bars"]
            lo, hi = in_cell * BEATS_PER_BAR, (in_cell + 1) * BEATS_PER_BAR
            hits = [(t - lo, k) for t, k in cell["hits"] if lo <= t < hi]
            ornament_at = [i for i, hit in enumerate(hits) if hit[1] == "orn"]
            keep = set(rng.sample(ornament_at, min(cap, len(ornament_at)))) if cap else set()
            for i, (position, hit_kind) in enumerate(hits):
                if hit_kind == "orn" and i not in keep:
                    continue
                time_step = round(bar * BEATS_PER_BAR + position, 5)
                if hit_kind == "orn":
                    notes.append({
                        "time_step": time_step, "note": HAT_NOTE, "length": GHOST_LEN,
                        "velocity": rng.randint(*GHOST_VELOCITY), "hat_ghost": True, "hat_cell": cell["id"],
                    })
                else:
                    note = {
                        "time_step": time_step, "note": HAT_NOTE, "length": EIGHTH_LEN,
                        "velocity": STEADY_VELOCITY, "hat_steady": True, "hat_cell": cell["id"],
                    }
                    if hit_kind == "open":
                        note.update({"length": OPEN_LEN, "hat_type": "open"})
                    notes.append(note)
    notes.sort(key=lambda n: n["time_step"])
    return notes or None


__all__ = ["build_line", "library", "library_enabled"]

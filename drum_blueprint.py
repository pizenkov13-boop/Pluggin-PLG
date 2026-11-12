"""Drum grid — kick / snare / clap independent of LLM melody, varied by genre+prompt.

Trap-family pockets come from style_corpus.DRUM_CELLS (not a frozen 1+9 grid).
Seed includes a MIDI/melody fingerprint so different MIDIs never share one kick
grid. Melody energy (разъеб) densifies the pocket inside the blueprint itself.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

from drum_defaults import _bar_count

BEATS_PER_BAR = 4.0
STEPS_PER_BAR = 16
STEP_BEATS = BEATS_PER_BAR / STEPS_PER_BAR

# 16-step FL grid (1-indexed): downbeat + half-time backbeat pocket.
KICK_STEPS = (1, 9)
# Snare + clap land on every 3rd grid step (3, 6, 9, 12, 15).
SNARE_CLAP_STEPS = tuple(range(3, STEPS_PER_BAR + 1, 3))

# Genres that ride a steady four-on-floor kick instead of the trap half-time pocket.
_FOUR_ON_FLOOR = ("edm", "disco_pop", "pop", "kpop", "house", "techno", "trance", "hardstyle")
_FOUR_ON_FLOOR_STEPS = (1, 5, 9, 13)
# Weak 16ths the prompt-seed can sprinkle kicks onto (trap family) — never the
# main backbeat (9). Different prompts pick different ones → different groove.
_SYNC_KICK_POOL = (4, 6, 7, 10, 11, 12, 14, 15)
_GHOST_SNARE_POOL = (3, 6, 12, 14)

# Grid archetypes for non-trap genres (16-step, 1-indexed). "halftime" (trap
# pocket, snare step 9) stays the default; these give each family its honest
# skeleton: backbeat 2&4 (steps 5/13), dembow 3+3+2, two-step breaks, etc.
_GRID_ARCHETYPES: dict[str, dict[str, tuple[int, ...]]] = {
    "four_floor": {"kick": _FOUR_ON_FLOOR_STEPS, "kick_sparse": (1, 9), "snare": (5, 13), "sync": ()},
    "backbeat": {"kick": (1, 11), "kick_sparse": (1, 11), "snare": (5, 13), "sync": (4, 7, 8, 12, 15)},
    "dembow": {"kick": _FOUR_ON_FLOOR_STEPS, "kick_sparse": (1, 9), "snare": (4, 7, 12, 15), "sync": ()},
    "twostep": {"kick": (1, 11), "kick_sparse": (1, 11), "snare": (5, 13), "sync": (8, 15)},
    "halftime_heavy": {"kick": (1, 16), "kick_sparse": (1,), "snare": (9,), "sync": ()},
    "afro": {"kick": (1, 7, 11), "kick_sparse": (1, 11), "snare": (5, 13), "sync": (4, 14)},
    "riddim": {"kick": (1, 4, 7), "kick_sparse": (1, 7), "snare": (9,), "sync": ()},
    "epic": {"kick": (1,), "kick_sparse": (1,), "snare": (9,), "sync": ()},
}

_GENRE_ARCHETYPE: dict[str, str] = {
    "house": "four_floor",
    "techno": "four_floor",
    "trance": "four_floor",
    "hardstyle": "four_floor",
    "edm": "four_floor",
    "disco_pop": "four_floor",
    "pop": "four_floor",
    "kpop": "four_floor",
    "reggaeton": "dembow",
    "dancehall": "riddim",
    "garage": "twostep",
    "dnb": "twostep",
    "dubstep": "halftime_heavy",
    "boombap": "backbeat",
    "lofi": "backbeat",
    "funk": "backbeat",
    "soul": "backbeat",
    "rock": "backbeat",
    "afrobeats": "afro",
    "amapiano": "afro",
    "cinematic": "epic",
}

DRUM_NOTE = {
    "kick": "C1",
    "snare": "D1",
    "clap": "E1",
}

# Snare/clap LOUD backbeat accents per section (ghosts are seeded per prompt). The
# verse breathes (one backbeat); the chorus/hook add the pickup on step 15.
_SECTION_SNARE_ACCENT = {
    "verse": (9,),
    "outro": (9,),
    "intro": (9,),
    "hook": (9, 15),
    "chorus": (9, 15),
    "drop": (9, 15),
}


def _step_time(bar: int, step_1idx: int) -> float:
    return round(bar * BEATS_PER_BAR + (step_1idx - 1) * STEP_BEATS, 5)


def _section_for_bar(arrangement: list[dict[str, Any]], bar: int) -> str:
    for section in arrangement:
        start = int(section.get("start_bar", 0))
        count = int(section.get("bars", 0))
        if start <= bar < start + count:
            return str(section.get("name", ""))
    return ""


def _section_start_bar_for_bar(arrangement: list[dict[str, Any]], bar: int) -> int | None:
    """start_bar of the section containing ``bar``, or None if none matches
    — used to make _tile_cell_pattern's sync/ghost/step-15 picks
    section-relative (see _relative_random)."""
    for section in arrangement:
        start = int(section.get("start_bar", 0))
        count = int(section.get("bars", 0))
        if start <= bar < start + count:
            return start
    return None


def _relative_random(base_seed: int, *parts: Any) -> random.Random:
    """Deterministic per-decision RNG, keyed by section-relative position
    instead of the shared sequential stream _tile_cell_pattern otherwise
    draws from.

    _tile_cell_pattern used to drive every sync/ghost/step-15 choice off ONE
    long-lived `random.Random(_seed_for(pattern))` consumed bar-by-bar across
    the whole song — so two occurrences of the same section picked genuinely
    different snare/clap/kick-sync notes, not just different cosmetic
    timing, purely because they sat at different absolute song positions
    (same root cause as bass808_gen._relative_rng_key, but that fix's
    "just relativize the hash key" trick doesn't apply to a stateful stream:
    the picked values there depend on how much of the stream every
    PRECEDING bar in the whole song already consumed, not just on the key).
    This seeds a fresh, independent Random per decision instead, so two
    occurrences of the same section — same relative position, same
    decision tag — draw identically.

    Returns a `random.Random` (not a bare float, unlike bass808_gen._rng)
    so call sites keep using `.choice()`/`.sample()`/`.random()` unchanged,
    and so
    tests/test_drum_blueprint_cells.py::test_clap_does_not_inherit_hot_section_step15_leak's
    `monkeypatch.setattr(drum_blueprint.random.Random, "random", ...)`
    still intercepts these instances (it patches the class, not a specific
    object).
    """
    raw = hashlib.sha256(f"{base_seed}|{'|'.join(map(str, parts))}".encode("utf-8")).hexdigest()
    return random.Random(int(raw[:16], 16))


def _active_tracks_for_bar(
    arrangement: list[dict[str, Any]], bar: int
) -> set[str] | None:
    """Return an arranger section's explicit lane gate, when provided."""
    for section in arrangement:
        start = int(section.get("start_bar", 0))
        count = int(section.get("bars", 0))
        if start <= bar < start + count:
            active = section.get("active_tracks")
            if isinstance(active, list):
                return {str(track) for track in active}
            return None
    return None


def build_kick_pattern(bars: int) -> list[dict[str, Any]]:
    return [
        {
            "time_step": _step_time(bar, step),
            "note": DRUM_NOTE["kick"],
            "length": 0.35,
            "velocity": 118,
            "drum_blueprint": True,
        }
        for bar in range(bars)
        for step in KICK_STEPS
    ]


def build_snare_pattern(bars: int) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for bar in range(bars):
        for step in SNARE_CLAP_STEPS:
            notes.append(
                {
                    "time_step": _step_time(bar, step),
                    "note": DRUM_NOTE["snare"],
                    "length": 0.28,
                    "velocity": 118 if step == 9 else 94,
                    "drum_blueprint": True,
                }
            )
    return notes


def build_clap_pattern(bars: int) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for bar in range(bars):
        for step in SNARE_CLAP_STEPS:
            notes.append(
                {
                    "time_step": _step_time(bar, step),
                    "note": DRUM_NOTE["clap"],
                    "length": 0.22,
                    "velocity": 104 if step == 9 else 82,
                    "drum_blueprint": True,
                }
            )
    return notes


_DRUM_LENGTH = {"kick": 0.35, "snare": 0.28, "clap": 0.22}


def _drum_hit(
    bar: int, step: int, kind: str, section: str, velocity: int, *, ghost: bool = False
) -> dict[str, Any]:
    hit = {
        "time_step": _step_time(bar, step),
        "note": DRUM_NOTE[kind],
        "length": _DRUM_LENGTH[kind],
        "velocity": velocity,
        "drum_blueprint": True,
    }
    if section:
        hit["section"] = section  # keep so the hat-roll gating / dynamics still read it
    if ghost:
        hit["ghost"] = True  # humanize keeps ghosts quiet (no velocity re-randomize)
    return hit


def _melody_fingerprint(pattern: dict[str, Any]) -> str:
    """Stable hash of melody MIDI content — different MIDIs must diverge drums."""
    tracks = pattern.get("tracks") if isinstance(pattern.get("tracks"), dict) else {}
    notes = tracks.get("melody_lead") if isinstance(tracks.get("melody_lead"), list) else []
    parts: list[str] = []
    try:
        from pattern_utils import parse_note_name
    except Exception:  # noqa: BLE001
        parse_note_name = None  # type: ignore[assignment]
    for entry in notes[:512]:
        if not isinstance(entry, dict):
            continue
        try:
            t = round(float(entry.get("time_step", 0)) / 0.25) * 0.25
            note = str(entry.get("note", ""))
            length = round(float(entry.get("length", 0.25)) * 4) / 4.0
            if parse_note_name is not None:
                pc = str(parse_note_name(note) % 12)
            else:
                pc = note
        except (TypeError, ValueError):
            continue
        parts.append(f"{t}:{pc}:{length}")
    meta: list[str] = []
    dna = pattern.get("plg_midi_dna")
    if isinstance(dna, dict):
        meta.append(str(dna.get("family") or dna.get("role") or ""))
    upload = pattern.get("plg_midi_upload_result")
    if isinstance(upload, dict):
        meta.append(str(upload.get("path") or upload.get("file") or ""))
    for key in ("plg_midi_source", "plg_uploaded_midi", "midi_path"):
        if pattern.get(key):
            meta.append(str(pattern.get(key)))
    blob = "|".join(parts) + "#" + "|".join(meta) + f"#n={len(notes)}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _seed_for(pattern: dict[str, Any]) -> int:
    """Pocket seed: variety + MIDI fingerprint so regen and different MIDIs diverge.

    The seed is built only from first-party facts about the beat. A third-party
    synth pack/preset name used to be mixed in here, which made the drum RNG
    depend on which FLEX packs the generating machine had installed and on a
    rotating preset pick. It carried no information the melody fingerprint did
    not already carry more finely, so it is gone (2026-08-28).
    """
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    energy = pattern.get("plg_melody_energy") if isinstance(pattern.get("plg_melody_energy"), dict) else {}
    src = (
        f"{pattern.get('user_prompt', '')}|{pattern.get('bpm', '')}|{pattern.get('style', '')}"
        f"|{variety.get('seed', '')}|{variety.get('drum_variant', '')}"
        f"|{energy.get('pace', '')}|{energy.get('busyness', '')}"
        f"|{_melody_fingerprint(pattern)}"
    )
    return int(hashlib.sha256(src.encode("utf-8")).hexdigest()[:12], 16)


def _melody_pace(pattern: dict[str, Any]) -> str:
    energy = pattern.get("plg_melody_energy")
    if isinstance(energy, dict):
        pace = str(energy.get("pace") or "").strip().lower()
        if pace in ("racing", "driving", "pocket"):
            return pace
    return "pocket"


def _global_busyness(pattern: dict[str, Any]) -> float:
    energy = pattern.get("plg_melody_energy")
    if isinstance(energy, dict):
        try:
            return float(energy.get("busyness") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _bar_energy_hot(pattern: dict[str, Any], bar: int, section: str) -> bool:
    """True when this bar should ride a denser pocket (MIDI разъеб / hot section)."""
    energy = pattern.get("plg_melody_energy") if isinstance(pattern.get("plg_melody_energy"), dict) else {}
    busyness = _global_busyness(pattern)
    pace = str(energy.get("pace") or "")
    attack = str(energy.get("attack") or "")
    if pace == "racing" or (pace == "driving" and busyness >= 0.5):
        return True
    if busyness >= 0.65 and attack == "sharp":
        return True
    if section in ("hook", "chorus", "drop") and busyness >= 0.45:
        return True
    start = bar * BEATS_PER_BAR
    for row in energy.get("section_density") or []:
        if not isinstance(row, dict):
            continue
        try:
            if float(row["start"]) <= start < float(row["end"]) and float(row.get("ratio") or 1.0) >= 1.2:
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def _beat_to_step(beat_in_bar: float) -> int:
    """Map a beat offset inside a bar (0..4) to a 1-indexed 16th-grid step."""
    step = int(round(float(beat_in_bar) / STEP_BEATS)) + 1
    return max(1, min(STEPS_PER_BAR, step))


def _resolve_drum_cell(pattern: dict[str, Any], genre: str, rng: random.Random) -> dict[str, Any]:
    dna = pattern.get("plg_style_dna")
    if isinstance(dna, dict):
        cell = dna.get("drum_cell")
        if isinstance(cell, dict) and cell.get("kick") is not None:
            return {
                "name": str(cell.get("name") or "dna"),
                "kick": [float(x) for x in cell.get("kick") or ()],
                "snare": [float(x) for x in cell.get("snare") or ()],
                "bar_len": int(cell.get("bar_len") or 2),
            }
    from style_corpus import DRUM_CELLS, _DEFAULT_GENRE

    cells = DRUM_CELLS.get(genre) or DRUM_CELLS.get(_DEFAULT_GENRE) or []
    if not cells:
        return {
            "name": "classic half-time",
            "kick": [0.0, 2.75, 4.0, 6.5],
            "snare": [2.0, 6.0],
            "bar_len": 2,
        }
    weights = [max(1, int(c.get("weight", 1))) for c in cells]
    picked = rng.choices(cells, weights=weights, k=1)[0]
    return {
        "name": str(picked.get("name") or "cell"),
        "kick": [float(x) for x in picked.get("kick") or ()],
        "snare": [float(x) for x in picked.get("snare") or ()],
        "bar_len": 2,
    }


def _cell_beats_in_bar(cell_beats: list[float], local_bar: int, *, bar_len: int = 2) -> list[float]:
    """Beats of an N-bar cell that fall inside local_bar, as offsets 0..4 inside that bar."""
    lo = local_bar * BEATS_PER_BAR
    hi = lo + BEATS_PER_BAR
    span = bar_len * BEATS_PER_BAR
    out: list[float] = []
    for beat in cell_beats:
        b = float(beat) % span
        if lo - 1e-9 <= b < hi:
            out.append(round(b - lo, 5))
    return out


def _sparse_kick_beats(local_beats: list[float]) -> list[float]:
    """Verse pocket: downbeat + at most one late knock — not the full cell."""
    if not local_beats:
        return [0.0]
    kept = [b for b in local_beats if b < 0.2]
    late = [b for b in local_beats if b >= 2.5]
    if late:
        kept.append(max(late))
    elif local_beats and not kept:
        kept.append(local_beats[0])
    if not kept:
        kept = [0.0]
    return sorted({round(b, 5) for b in kept})


# Transition fills — the "label glue": an accelerating snare build in the last
# bar before every chorus/hook landing. (step, velocity) pairs, 1-indexed steps.
_FILL_DEFAULT = ((11, 70), (13, 84), (14, 96), (15, 108), (16, 118))
_FILL_FOUR_FLOOR = ((13, 78), (14, 92), (15, 106), (16, 118))
_FILL_TARGETS = ("chorus", "hook", "drop")
# kicks clear the back half of the fill bar so the roll owns the space
_FILL_KICK_CLEAR_FROM = 11


def _fill_bars(pattern: dict[str, Any], arrangement: list[dict[str, Any]]) -> set[int]:
    """Last bar of each section whose NEXT section is a chorus/hook landing."""
    switch = pattern.get("plg_beat_switch")
    cut_bar = switch.get("cut_bar") if isinstance(switch, dict) else None
    bars: set[int] = set()
    ordered = sorted(
        (s for s in arrangement if isinstance(s, dict)),
        key=lambda s: int(s.get("start_bar", 0)),
    )
    for current, nxt in zip(ordered, ordered[1:]):
        if str(nxt.get("name", "")) not in _FILL_TARGETS:
            continue
        boundary = int(current.get("start_bar", 0)) + int(current.get("bars", 0)) - 1
        if cut_bar is not None and boundary == int(cut_bar):
            continue  # the beat-switch silence IS the transition there
        bars.add(boundary)
    return bars


def _tile_cell_pattern(
    pattern: dict[str, Any],
    *,
    bar_count: int,
    genre: str,
    pace: str,
    arrangement: list[dict[str, Any]] | None,
    transition_fill: bool,
) -> str:
    """Trap-family path: materialize DRUM_CELLS with phrase sync + energy densify."""
    tracks = pattern.setdefault("tracks", {})
    base_seed = _seed_for(pattern)
    rng = random.Random(base_seed)
    cell = _resolve_drum_cell(pattern, genre, rng)
    cell_name = str(cell["name"])
    kick_cell = list(cell["kick"])
    snare_cell = list(cell["snare"])
    bar_len = max(1, int(cell.get("bar_len") or 2))
    sync_pool = _SYNC_KICK_POOL
    busyness = _global_busyness(pattern)

    phrase_sync: dict[int, list[int]] = {}
    phrase_ghost: dict[int, list[int]] = {}

    def _sync_for_phrase(phrase: int, *, hot: bool) -> list[int]:
        if phrase not in phrase_sync:
            # Section-relative when arrangement covers this phrase's first
            # bar (see _relative_random) — two occurrences of the same
            # section then draw identically instead of picking up wherever
            # the shared stream happened to be after every preceding bar in
            # the song. Falls back to the original shared `rng` stream
            # (unchanged) when there's no arrangement, matching today's
            # exact behavior for that caller.
            section_start = _section_start_bar_for_bar(arrangement, phrase * 4) if arrangement else None
            if section_start is not None:
                relative_phrase = (phrase * 4 - section_start) // 4
                phrase_rng = _relative_random(
                    base_seed, _section_for_bar(arrangement, phrase * 4), relative_phrase, "phrase"
                )
            else:
                phrase_rng = rng
            if pace == "racing" or hot or busyness >= 0.6:
                k = min(len(sync_pool), phrase_rng.choice((3, 4, 4)))
            else:
                k = min(len(sync_pool), phrase_rng.choice((1, 2, 2, 3)))
            phrase_sync[phrase] = sorted(phrase_rng.sample(sync_pool, k=k)) if k else []
            phrase_ghost[phrase] = sorted(
                phrase_rng.sample(_GHOST_SNARE_POOL, k=min(2, len(_GHOST_SNARE_POOL)))
            )
        return phrase_sync[phrase]

    fill_bars = _fill_bars(pattern, arrangement) if transition_fill and arrangement else set()
    kick: list[dict[str, Any]] = []
    snare: list[dict[str, Any]] = []
    clap: list[dict[str, Any]] = []

    for bar in range(bar_count):
        section = _section_for_bar(arrangement, bar) if arrangement else ""
        active_tracks = _active_tracks_for_bar(arrangement, bar) if arrangement else None
        section_start_bar = _section_start_bar_for_bar(arrangement, bar) if arrangement else None
        relative_bar = bar - section_start_bar if section_start_bar is not None else None
        hot = _bar_energy_hot(pattern, bar, section)
        full = section in ("hook", "chorus", "drop") or pace == "racing" or hot
        dense = full or hot or busyness >= 0.65
        is_fill = bar in fill_bars
        phrase = bar // 4
        sync_steps = _sync_for_phrase(phrase, hot=hot or dense)
        ghost_steps = phrase_ghost.get(phrase, [])

        local = bar % bar_len
        kick_local = _cell_beats_in_bar(kick_cell, local, bar_len=bar_len)
        if not dense:
            kick_local = _sparse_kick_beats(kick_local)
        ksteps = {_beat_to_step(b) for b in kick_local}
        sync_prob = 0.85 if (hot or pace == "racing") else (0.6 if dense else 0.25)
        if dense or hot:
            for step in sync_steps:
                roll = (
                    _relative_random(base_seed, section, relative_bar, "sync_step", step).random()
                    if relative_bar is not None
                    else rng.random()
                )
                if roll < sync_prob:
                    ksteps.add(step)
        if is_fill:
            ksteps = {s for s in ksteps if s < _FILL_KICK_CLEAR_FROM}

        snare_local = _cell_beats_in_bar(snare_cell, local, bar_len=bar_len)
        if not snare_local:
            snare_local = [2.0]
        snare_steps = tuple(_beat_to_step(b) for b in snare_local)
        # Clap locks to the core backbeat BEFORE the hot-section step-15 add
        # below — clap stays on the stack's main hits (no random extra),
        # snare is still free to pick up the syncopated addition on its own.
        clap_steps = snare_steps
        if dense and section in ("hook", "chorus", "drop") and 15 not in snare_steps:
            roll = (
                _relative_random(base_seed, section, relative_bar, "step15").random()
                if relative_bar is not None
                else rng.random()
            )
            if roll < 0.55:
                snare_steps = tuple(sorted(set(snare_steps) | {15}))
        main_steps = set(snare_steps)

        if active_tracks is None or "kick" in active_tracks:
            for step in sorted(ksteps):
                main = step in (1, 5, 9, 13)
                kick.append(_drum_hit(bar, step, "kick", section, 118 if main else 98))
        if active_tracks is None or "snare" in active_tracks:
            for step in snare_steps:
                snare.append(
                    _drum_hit(bar, step, "snare", section, 118 if step in main_steps else 106)
                )
        if active_tracks is None or "clap" in active_tracks:
            for step in clap_steps:
                clap.append(
                    _drum_hit(bar, step, "clap", section, 104 if step in main_steps else 92)
                )
        if dense and (active_tracks is None or "snare" in active_tracks):
            for step in ghost_steps:
                roll = (
                    _relative_random(base_seed, section, relative_bar, "ghost_add", step).random()
                    if relative_bar is not None
                    else rng.random()
                )
                if roll < (0.8 if hot else 0.65):
                    snare.append(_drum_hit(bar, step, "snare", section, 56, ghost=True))

        if is_fill and (active_tracks is None or "snare" in active_tracks):
            existing_fill_steps = {
                int(round((float(h["time_step"]) - bar * BEATS_PER_BAR) / STEP_BEATS)) + 1
                for h in snare
                if float(h["time_step"]) >= bar * BEATS_PER_BAR
            }
            for step, velocity in _FILL_DEFAULT:
                if step in existing_fill_steps:
                    continue
                hit = _drum_hit(bar, step, "snare", section, velocity)
                hit["fill"] = True
                snare.append(hit)

    tracks["kick"] = kick
    tracks["snare"] = snare
    tracks["clap"] = clap
    tracks.pop("snare_layer", None)
    return cell_name


def apply_drum_blueprint(
    pattern: dict[str, Any],
    *,
    bars: int | None = None,
    genre: str = "trap",
    transition_fill: bool = False,
) -> None:
    """Replace kick/snare/clap with a clean drum grid (never copy melody lanes).

    Trap-family (no archetype): tiles ``DRUM_CELLS`` with MIDI-fingerprint seed,
    phrase-level sync mutation, and melody-energy densify. Dance/backbeat genres
    keep their archetypes. ``transition_fill`` gates snare builds into landings.
    """
    tracks = pattern.setdefault("tracks", {})
    if not isinstance(tracks, dict):
        return
    bar_count = bars if bars is not None else _bar_count(pattern)
    arrangement = pattern.get("plg_arrangement")
    pace = _melody_pace(pattern)
    archetype_name = _GENRE_ARCHETYPE.get(genre)
    four_floor = genre in _FOUR_ON_FLOOR
    use_cells = not archetype_name and not four_floor

    if not isinstance(arrangement, list) or not arrangement:
        if pace in ("racing", "driving") and use_cells:
            kick_steps = _FOUR_ON_FLOOR_STEPS if pace == "racing" else (1, 5, 9, 13)
            snare_steps = (5, 13)
            tracks["kick"] = [
                {
                    "time_step": _step_time(bar, step),
                    "note": DRUM_NOTE["kick"],
                    "length": 0.35,
                    "velocity": 118 if step in (1, 9) else 104,
                    "drum_blueprint": True,
                }
                for bar in range(bar_count)
                for step in kick_steps
            ]
            tracks["snare"] = [
                {
                    "time_step": _step_time(bar, step),
                    "note": DRUM_NOTE["snare"],
                    "length": 0.28,
                    "velocity": 118,
                    "drum_blueprint": True,
                }
                for bar in range(bar_count)
                for step in snare_steps
            ]
            tracks["clap"] = [
                {
                    "time_step": _step_time(bar, step),
                    "note": DRUM_NOTE["clap"],
                    "length": 0.28,
                    "velocity": 104,
                    "drum_blueprint": True,
                }
                for bar in range(bar_count)
                for step in snare_steps
            ]
            pattern["plg_drum_grid"] = "four_floor" if pace == "racing" else "backbeat"
        elif use_cells:
            cell_name = _tile_cell_pattern(
                pattern,
                bar_count=bar_count,
                genre=genre,
                pace=pace,
                arrangement=None,
                transition_fill=False,
            )
            pattern["plg_drum_grid"] = cell_name
        else:
            tracks["kick"] = build_kick_pattern(bar_count)
            tracks["snare"] = build_snare_pattern(bar_count)
            tracks["clap"] = build_clap_pattern(bar_count)
            pattern["plg_drum_grid"] = "halftime"
        tracks.pop("snare_layer", None)
        pattern["plg_drum_blueprint"] = True
        pattern["plg_drum_pace"] = pace
        return

    rng = random.Random(_seed_for(pattern))
    archetype = _GRID_ARCHETYPES.get(archetype_name or "")
    if pace in ("racing", "driving") and use_cells:
        archetype_name = "backbeat" if pace == "driving" else "four_floor"
        archetype = _GRID_ARCHETYPES[archetype_name]
        four_floor = archetype_name == "four_floor"
        use_cells = False

    if use_cells:
        cell_name = _tile_cell_pattern(
            pattern,
            bar_count=bar_count,
            genre=genre,
            pace=pace,
            arrangement=arrangement,
            transition_fill=transition_fill,
        )
        pattern["plg_drum_blueprint"] = True
        pattern["plg_drum_pace"] = pace
        pattern["plg_drum_grid"] = cell_name
        return

    if archetype:
        sync_pool = archetype["sync"]
    else:
        sync_pool = () if four_floor else _SYNC_KICK_POOL
    if pace == "racing" and sync_pool:
        sync_k = min(len(sync_pool), rng.choice((3, 4, 4)))
    else:
        sync_k = min(rng.choice((2, 2, 3)), len(sync_pool)) if sync_pool else 0
    phrase_sync: dict[int, list[int]] = {}
    phrase_ghost: dict[int, list[int]] = {}

    def _phrase_picks(phrase: int) -> tuple[list[int], list[int]]:
        if phrase not in phrase_sync:
            k = sync_k
            phrase_sync[phrase] = sorted(rng.sample(sync_pool, k=k)) if sync_pool and k else []
            phrase_ghost[phrase] = sorted(rng.sample(_GHOST_SNARE_POOL, k=2))
        return phrase_sync[phrase], phrase_ghost[phrase]

    fill_bars = _fill_bars(pattern, arrangement) if transition_fill else set()

    kick: list[dict[str, Any]] = []
    snare: list[dict[str, Any]] = []
    clap: list[dict[str, Any]] = []
    for bar in range(bar_count):
        section = _section_for_bar(arrangement, bar)
        active_tracks = _active_tracks_for_bar(arrangement, bar)
        hot = _bar_energy_hot(pattern, bar, section)
        full = section in ("hook", "chorus", "drop") or pace == "racing" or hot
        is_fill = bar in fill_bars
        sync_steps, ghost_steps = _phrase_picks(bar // 4)

        if archetype:
            ksteps = set(archetype["kick"] if full else archetype["kick_sparse"])
            if full or pace == "racing" or hot:
                for step in sync_steps:
                    if pace == "racing" or hot or rng.random() < 0.6:
                        ksteps.add(step)
        elif four_floor:
            ksteps = set(_FOUR_ON_FLOOR_STEPS if full else (1, 9))
        else:
            ksteps = {1, 9}
            if full:
                for step in sync_steps:
                    if rng.random() < 0.6:
                        ksteps.add(step)
        if is_fill and archetype_name != "four_floor":
            ksteps = {s for s in ksteps if s < _FILL_KICK_CLEAR_FROM}
        if active_tracks is None or "kick" in active_tracks:
            for step in sorted(ksteps):
                main = step in (1, 5, 9, 13)
                kick.append(_drum_hit(bar, step, "kick", section, 118 if main else 98))

        if archetype:
            snare_steps = archetype["snare"]
            main_steps = {5, 13} if 5 in snare_steps else set(snare_steps)
            clap_steps: tuple[int, ...] = ()
            if archetype_name in ("four_floor", "backbeat", "twostep", "afro"):
                clap_steps = (5, 13)
            elif archetype_name in ("riddim", "halftime_heavy", "epic"):
                clap_steps = snare_steps
        elif pace in ("racing", "driving"):
            snare_steps = (5, 13) if full or pace == "racing" else (5,)
            main_steps = {5, 13}
            clap_steps = snare_steps
        else:
            snare_steps = _SECTION_SNARE_ACCENT.get(section, (9,))
            main_steps = {9}
            clap_steps = snare_steps
        if active_tracks is None or "snare" in active_tracks:
            for step in snare_steps:
                snare.append(
                    _drum_hit(bar, step, "snare", section, 118 if step in main_steps else 106)
                )
        if active_tracks is None or "clap" in active_tracks:
            for step in clap_steps:
                clap.append(
                    _drum_hit(bar, step, "clap", section, 104 if step in main_steps else 92)
                )
        if full and (active_tracks is None or "snare" in active_tracks):
            for step in ghost_steps:
                if rng.random() < 0.7:
                    snare.append(_drum_hit(bar, step, "snare", section, 56, ghost=True))

        if is_fill and (active_tracks is None or "snare" in active_tracks):
            fill = _FILL_FOUR_FLOOR if archetype_name == "four_floor" else _FILL_DEFAULT
            existing_fill_steps = {
                int(round((float(h["time_step"]) - bar * BEATS_PER_BAR) / STEP_BEATS)) + 1
                for h in snare
                if h.get("section") == section and float(h["time_step"]) >= bar * BEATS_PER_BAR
            }
            for step, velocity in fill:
                if step in existing_fill_steps:
                    continue
                hit = _drum_hit(bar, step, "snare", section, velocity)
                hit["fill"] = True
                snare.append(hit)

    tracks["kick"] = kick
    tracks["snare"] = snare
    tracks["clap"] = clap
    tracks.pop("snare_layer", None)
    pattern["plg_drum_blueprint"] = True
    pattern["plg_drum_pace"] = pace
    pattern["plg_drum_grid"] = archetype_name or ("four_floor" if four_floor else "halftime")

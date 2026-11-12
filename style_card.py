"""The Style Card: one read-only summary of what this generation is.

Phase 1 of the engine-consistency plan.  The card COLLECTS what the pipeline
has already decided and stamps it as ``pattern["plg_style_card"]``.  It is
write-only on purpose: no generator, gate or sample picker reads it yet, so
stamping it cannot change a single note, sample or byte of the FLP.

Rules this module keeps:

* It never recomputes a decision another owner already made.  Tempo comes from
  the pattern / MIDI passport, the key from ``plg_progression`` (the result of
  ``beat_humanize._resolve_style_dna_key``), energy from ``plg_melody_energy``,
  density from ``plg_drum_density``.  The only new numbers are ``mood`` and
  ``kit_target`` — a deliberately coarse first version, to be calibrated on
  real material once Phase 3 starts reading them.
* It reads no genre lane.  ``HIPHOP_LANES`` and ``genre_override`` are not
  touched; the card is built from the MIDI alone.
* No randomness: the same pattern always yields the same card.
* It never raises into the pipeline (``attach_style_card`` swallows failures)
  and never mutates anything but its own key.

It is meant to run AFTER ``humanize_pattern`` — that is where the progression
key and the drum density card come into existence.
"""
from __future__ import annotations

import logging
from statistics import median
from typing import Any

from pattern_utils import NOTE_NAMES, parse_note_name, track_notes

logger = logging.getLogger(__name__)

STYLE_CARD_KEY = "plg_style_card"
STYLE_CARD_VERSION = 1

# --- coarse mood model (first version, NOT calibrated) -----------------------
# How dark each scale reads, 0 (bright) .. 1 (dark).
_SCALE_DARKNESS: dict[str, float] = {
    "natural_minor": 1.0,
    "harmonic_minor": 1.0,
    "phrygian": 1.0,
    "locrian": 1.0,
    "dorian": 0.7,
    "lydian": 0.2,
    "major": 0.1,
}
_NEUTRAL = 0.5
# Register mapping: median pitch at/below LOW is fully "low", at/above HIGH is
# fully "high" (MIDI numbers: 48 = C3, 84 = C6).
_REGISTER_LOW = 48.0
_REGISTER_HIGH = 84.0
_PACE_AGGRESSION = {"racing": 1.0, "driving": 0.6, "pocket": 0.2}

# kit_target thresholds
_DARK_HI = 0.62
_BRIGHT_HI = 0.62
_LOW_CUT = 0.45
_HARD_AT = 0.60
_SOFT_BELOW = 0.35
_LONG_TAIL_AT = 0.55

_TEMPO_CONFLICT_BPM = 0.5

_ENERGY_FIELDS = (
    "busyness",
    "lushness",
    "pace",
    "onset_rate",
    "short_frac",
    "syncopation",
    "poly",
    "coverage",
    "median_len",
    "attack",
)


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _tempo_block(pattern: dict[str, Any]) -> dict[str, Any]:
    passport = _dict(pattern.get("plg_midi_passport"))
    p_tempo = _dict(passport.get("tempo"))
    p_safety = _dict(passport.get("safety"))
    warnings = [str(w) for w in (p_safety.get("warnings") or [])]

    bpm = _num(pattern.get("bpm"))
    passport_bpm = _num(p_tempo.get("bpm"))
    source = str(
        pattern.get("plg_midi_tempo_source")
        or p_tempo.get("source")
        or ("pattern" if bpm is not None else "unknown")
    )
    if bpm is None:
        bpm = passport_bpm

    conflict = "tempo_conflict_embedded_vs_filename" in warnings
    if bpm is not None and passport_bpm is not None and abs(bpm - passport_bpm) > _TEMPO_CONFLICT_BPM:
        conflict = True

    reliable = p_safety.get("tempo_reliable")
    reliable = bool(reliable) if isinstance(reliable, bool) else (bpm is not None)
    if bpm is None:
        reliable = False

    if bpm is None:
        feel = "unknown"
    elif bpm < 100.0:
        feel = "slow"
    elif bpm <= 155.0:
        feel = "mid"
    else:
        feel = "fast"
    return {
        "bpm": round(bpm, 2) if bpm is not None else None,
        "source": source,
        "reliable": reliable and not conflict,
        "conflict": conflict,
        "feel": feel,
    }


def _mode_of(scale: str | None) -> str:
    if not scale:
        return "unknown"
    name = scale.lower()
    if "minor" in name or name in ("phrygian", "locrian", "dorian"):
        return "minor"
    if "major" in name or name in ("lydian", "mixolydian"):
        return "major"
    return "other"


def _root_from_name(name: Any) -> int | None:
    if not isinstance(name, str):
        return None
    try:
        return NOTE_NAMES.index(name.strip().capitalize())
    except ValueError:
        return None


def _key_block(pattern: dict[str, Any]) -> dict[str, Any]:
    """The final key is whatever humanize pinned; the rest is evidence."""
    passport = _dict(pattern.get("plg_midi_passport"))
    p_key = _dict(passport.get("key"))
    p_safety = _dict(passport.get("safety"))
    warnings = [str(w) for w in (p_safety.get("warnings") or [])]

    passport_root = p_key.get("root_pc") if isinstance(p_key.get("root_pc"), int) else None
    passport_scale = p_key.get("scale") if p_key.get("scale") not in (None, "unknown") else None

    root_pc: int | None = None
    scale: str | None = None
    source = "unknown"

    prog = _dict(pattern.get("plg_progression"))
    prog_root = _root_from_name(prog.get("key"))
    if prog_root is not None:
        root_pc = prog_root
        scale = str(prog["scale"]) if prog.get("scale") else None
        source = str(pattern.get("plg_progression_root_source") or "progression")
    elif passport_root is not None:
        root_pc, scale = int(passport_root) % 12, passport_scale
        source = str(p_key.get("source") or "midi_passport")
    else:
        for field in ("plg_midi_key", "plg_key_hint"):
            hint = _dict(pattern.get(field))
            if isinstance(hint.get("root_pc"), int):
                root_pc, scale = int(hint["root_pc"]) % 12, hint.get("scale")
                source = field
                break

    conflict = "key_conflict_embedded_vs_filename" in warnings
    if (
        root_pc is not None
        and passport_root is not None
        and int(passport_root) % 12 != root_pc
    ):
        conflict = True

    known = root_pc is not None
    reliable = known and not conflict and p_safety.get("key_reliable", True) is not False
    return {
        "root_pc": root_pc,
        "name": NOTE_NAMES[root_pc] if root_pc is not None else None,
        "scale": scale,
        "mode": _mode_of(scale),
        "source": source,
        "reliable": bool(reliable),
        "conflict": conflict,
    }


def _energy_block(pattern: dict[str, Any]) -> dict[str, Any]:
    energy = _dict(pattern.get("plg_melody_energy"))
    return {field: energy[field] for field in _ENERGY_FIELDS if field in energy}


def _density_block(pattern: dict[str, Any]) -> dict[str, Any]:
    card = _dict(pattern.get("plg_drum_density"))
    return {
        "drive": card.get("drive"),
        "pairs_per_bar": card.get("pairs_per_bar"),
        "bars": card.get("bars"),
    }


def _role_block(pattern: dict[str, Any]) -> dict[str, Any]:
    energy = _dict(pattern.get("plg_melody_energy"))
    role_info = _dict(_dict(pattern.get("plg_midi_passport")).get("role"))
    return {
        "melody_role": energy.get("role") or pattern.get("plg_melody_role"),
        "melody_vibe": energy.get("vibe") or pattern.get("plg_melody_vibe"),
        "musical_role": role_info.get("musical_role"),
    }


def _sections_block(pattern: dict[str, Any]) -> dict[str, Any]:
    energy = _dict(pattern.get("plg_melody_energy"))
    ratios = energy.get("section_density")
    ratios = [float(r["ratio"]) for r in ratios if isinstance(r, dict) and _num(r.get("ratio")) is not None] if isinstance(ratios, list) else []
    arrangement = pattern.get("plg_arrangement")
    count = len(arrangement) if isinstance(arrangement, list) else len(ratios)
    return {"count": count, "density_ratios": ratios}


def _median_pitch(pattern: dict[str, Any]) -> float | None:
    pitches: list[int] = []
    for note in track_notes(pattern, "melody_lead"):
        try:
            pitches.append(int(parse_note_name(str(note.get("note")))))
        except Exception:  # noqa: BLE001 — an unparsable note is simply not evidence
            continue
    return float(median(pitches)) if pitches else None


def _mood_block(
    pattern: dict[str, Any], tempo: dict[str, Any], key: dict[str, Any], energy: dict[str, Any]
) -> dict[str, Any]:
    pitch = _median_pitch(pattern)
    register_high = _clip(((pitch - _REGISTER_LOW) / (_REGISTER_HIGH - _REGISTER_LOW))) if pitch is not None else _NEUTRAL
    register_dark = 1.0 - register_high

    scale = key.get("scale")
    mode_dark = _SCALE_DARKNESS.get(str(scale), _NEUTRAL) if scale else _NEUTRAL

    bpm = tempo.get("bpm")
    slow = _clip(1.0 - (float(bpm) - 60.0) / 120.0) if bpm is not None else _NEUTRAL

    busyness = _num(energy.get("busyness"))
    lushness = _num(energy.get("lushness"))
    coverage = _num(energy.get("coverage"))
    median_len = _num(energy.get("median_len"))
    busyness = _NEUTRAL if busyness is None else busyness
    lushness = _NEUTRAL if lushness is None else lushness
    coverage = _NEUTRAL if coverage is None else coverage
    sharp = 1.0 if energy.get("attack") == "sharp" else 0.0
    pace_w = _PACE_AGGRESSION.get(str(energy.get("pace")), _NEUTRAL)
    long_notes = 1.0 if (median_len is not None and median_len >= 1.2) else 0.0

    darkness = _clip(0.5 * mode_dark + 0.3 * register_dark + 0.2 * slow)
    brightness = _clip(0.5 * register_high + 0.3 * sharp + 0.2 * (1.0 - lushness))
    aggression = _clip(0.5 * busyness + 0.3 * pace_w + 0.2 * sharp)
    space = _clip(0.5 * lushness + 0.3 * (1.0 - coverage) + 0.2 * long_notes)
    return {
        "darkness": round(darkness, 3),
        "brightness": round(brightness, 3),
        "aggression": round(aggression, 3),
        "space": round(space, 3),
    }


def _kit_target(mood: dict[str, Any], energy: dict[str, Any]) -> dict[str, str]:
    darkness, brightness = mood["darkness"], mood["brightness"]
    if darkness >= _DARK_HI and brightness < _LOW_CUT:
        centroid = "low"
    elif brightness >= _BRIGHT_HI and darkness < _LOW_CUT:
        centroid = "high"
    else:
        centroid = "mid"
    aggression = mood["aggression"]
    attack = "hard" if aggression >= _HARD_AT else ("soft" if aggression < _SOFT_BELOW else "mid")
    tail = "long" if mood["space"] >= _LONG_TAIL_AT else "short"
    body = "full" if darkness >= _DARK_HI else "thin" if brightness >= _BRIGHT_HI else "mid"
    return {"centroid": centroid, "attack": attack, "tail": tail, "body": body}


def _confidence(tempo: dict[str, Any], key: dict[str, Any], energy: dict[str, Any], has_notes: bool) -> dict[str, float]:
    def _score(known: bool, reliable: bool, conflict: bool) -> float:
        if not known:
            return 0.0
        if conflict:
            return 0.3
        return 1.0 if reliable else 0.6

    tempo_c = _score(tempo["bpm"] is not None, tempo["reliable"], tempo["conflict"])
    key_c = _score(key["root_pc"] is not None, key["reliable"], key["conflict"])
    mood_c = 0.0 if not has_notes else round(0.5 * key_c + 0.5 * (1.0 if energy else 0.0), 3)
    return {"tempo": tempo_c, "key": key_c, "mood": mood_c}


def build_style_card(pattern: dict[str, Any]) -> dict[str, Any]:
    """Assemble the card from a pattern without touching it."""
    tempo = _tempo_block(pattern)
    key = _key_block(pattern)
    energy = _energy_block(pattern)
    has_notes = len(track_notes(pattern, "melody_lead")) > 0
    mood = _mood_block(pattern, tempo, key, energy)
    return {
        "version": STYLE_CARD_VERSION,
        "tempo": tempo,
        "key": key,
        "energy": energy,
        "density": _density_block(pattern),
        "mood": mood,
        "role": _role_block(pattern),
        "sections": _sections_block(pattern),
        "kit_target": _kit_target(mood, energy),
        "confidence": _confidence(tempo, key, energy, has_notes),
    }


def attach_style_card(pattern: dict[str, Any]) -> dict[str, Any] | None:
    """Stamp ``plg_style_card``; never raises, never touches another key."""
    try:
        card = build_style_card(pattern)
    except Exception as exc:  # noqa: BLE001 — a write-only card must never block a beat
        logger.info("Style card skipped (%s)", exc)
        return None
    pattern[STYLE_CARD_KEY] = card
    return card


def style_card(pattern: dict[str, Any]) -> dict[str, Any] | None:
    """Read the stamped card, or None when this run never built one."""
    card = pattern.get(STYLE_CARD_KEY)
    return card if isinstance(card, dict) and card.get("version") else None


__all__ = ["STYLE_CARD_KEY", "attach_style_card", "build_style_card", "style_card"]

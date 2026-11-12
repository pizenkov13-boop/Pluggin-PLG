"""Resolve the Architect's fx_intent into a concrete, grid-locked FX plan.

DeepSeek writes high-level intentions ("crash + long reverb at bar 8 before the
chorus", "1-bar stop-time for the punchline"). This module turns those into exact
events on the beat grid and applies the part that is a pure, verifiable pattern
transform right now: STOP-TIME (cut the drums for the build/punchline drop).

The rest of the plan (one-shot FX samples placed on their own channel, volume
ramps, filter sweeps) is stored on the pattern for the builder/build-guide. Those
need an FX channel in flp_writer + native automation — the binary bits that must
be verified in FL — so this module computes them but does not fake them as live.
"""

from __future__ import annotations

import random
from typing import Any

BEATS_PER_BAR = 4.0
_DRUM_LANES = ("kick", "snare", "snare_layer", "clap", "hi_hats")
_FX_TYPES = ("crash", "riser", "downlifter", "impact", "texture", "sweep")
_STOP_TYPES = ("stop_time", "stoptime", "stop", "silence", "cut")
_MAX_STOPS = 3
_MAX_STOP_BARS = 1.0


def _event_beats(entry: dict[str, Any]) -> float | None:
    if "time_step" in entry:
        try:
            return float(entry["time_step"])
        except (TypeError, ValueError):
            return None
    if "bar" in entry:
        try:
            return (float(entry["bar"]) - 1.0) * BEATS_PER_BAR  # bars are 1-indexed
        except (TypeError, ValueError):
            return None
    return None


def _norm_type(entry: dict[str, Any]) -> str:
    return str(entry.get("type") or entry.get("fx") or "").strip().lower().replace("-", "_")


def plan_fx_events(data: dict[str, Any]) -> dict[str, Any]:
    """Resolve fx_intent -> {fx_events, stop_time} (beats), store on the pattern."""
    intent = data.get("fx_intent")
    if isinstance(intent, dict):
        intent = intent.get("events") or list(intent.values())
    if not isinstance(intent, list):
        return {}

    fx_events: list[dict[str, Any]] = []
    stop_time: list[dict[str, float]] = []
    for entry in intent:
        if not isinstance(entry, dict):
            continue
        kind = _norm_type(entry)
        start = _event_beats(entry)
        if start is None or start < 0:
            continue
        if kind in _STOP_TYPES:
            bars = entry.get("length_bars", entry.get("bars", 1.0))
            try:
                length = max(0.25, min(_MAX_STOP_BARS, float(bars))) * BEATS_PER_BAR
            except (TypeError, ValueError):
                length = BEATS_PER_BAR
            stop_time.append({"start": round(start, 4), "length": round(length, 4)})
        else:
            fx_type = kind if kind in _FX_TYPES else "impact"
            try:
                length = float(entry.get("length", 1.0))
            except (TypeError, ValueError):
                length = 1.0
            fx_events.append({
                "type": fx_type,
                "time_step": round(start, 4),
                "length": round(max(0.25, length), 4),
                "note": str(entry.get("note", "C5")),
            })

    plan = {"fx_events": fx_events, "stop_time": stop_time[:_MAX_STOPS]}
    data["fx_plan"] = plan
    return plan


def apply_stop_time(data: dict[str, Any]) -> int:
    """Cut drum hits inside each stop-time range so the drop/punchline explodes.

    Pure pattern transform (audible immediately). 808 + melody are kept so the
    tonal element rings under the silence. Returns the number of notes removed.
    """
    plan = data.get("fx_plan") or {}
    ranges = plan.get("stop_time") or []
    tracks = data.get("tracks")
    if not ranges or not isinstance(tracks, dict):
        return 0

    removed = 0
    for lane in _DRUM_LANES:
        notes = tracks.get(lane)
        if not isinstance(notes, list):
            continue
        kept = []
        for note in notes:
            t = note.get("time_step") if isinstance(note, dict) else None
            cut = False
            if isinstance(t, (int, float)):
                for r in ranges:
                    if r["start"] <= float(t) < r["start"] + r["length"]:
                        cut = True
                        break
            if cut:
                removed += 1
            else:
                kept.append(note)
        tracks[lane] = kept
    if removed:
        data.setdefault("plg_producer_meta", {})
        if isinstance(data["plg_producer_meta"], dict):
            data["plg_producer_meta"]["stop_time_cut"] = removed
    return removed


def apply_fx_intent(data: dict[str, Any]) -> dict[str, Any]:
    """Resolve the plan and apply the audible (stop-time) part. Returns the plan."""
    plan = plan_fx_events(data)
    if plan:
        plan["stop_time_removed"] = apply_stop_time(data)
    return plan


_MAX_FX_NOTES = 10
_TEXTURE_GENRES = frozenset({
    "trap", "dark_trap", "opium", "plugg", "lofi", "drill", "rage", "melodic_trap", "toliver", "kanye",
    "west_coast", "pluggnb", "detroit",
    # Praxi Plays (Drift Phonk): vintage vinyl/room-noise texture — the
    # existing atmosphere mechanism already searches "vinyl"/"tape"/"static"/
    # "noise" keywords (added earlier this session), no new code needed.
    "phonk",
})


def materialize_cowbell_notes(data: dict[str, Any]) -> int:
    """Cowbell (PLG Cowbell lane) — two unrelated signatures, style-gated:

    - Carti (opium/rage/plugg): a few sparse off-beat clanks per bar, never
      dense — an accent, not a pulse.
    - Phonk: a dense, driving 8th-note ostinato — the Memphis cowbell IS the
      rhythmic backbone, not an accent (phonk is its own tradition, not a
      trap-family derivative — see style_family.py's module docstring).

    starter_kit drops the lane when no cowbell is in the library."""
    import os
    import random

    if os.environ.get("PLG_COWBELL_LANE", "auto").strip().lower() in ("0", "off", "false", "no"):
        return 0
    from style_family import genre_of, is_carti, is_phonk

    tracks = data.get("tracks")
    if not isinstance(tracks, dict) or tracks.get("cowbell"):
        return 0
    genre = genre_of(data)
    carti = is_carti(genre)
    phonk = is_phonk(genre)
    if not carti and not phonk:
        return 0

    kick = tracks.get("kick")
    if not isinstance(kick, list) or not kick:
        return 0
    end_beat = max(float(n.get("time_step", 0)) for n in kick) + BEATS_PER_BAR
    bars = max(1, int(end_beat // BEATS_PER_BAR))
    variety = data.get("plg_variety") if isinstance(data.get("plg_variety"), dict) else {}
    seed = int(variety.get("seed") or 0)

    if phonk:
        rng = random.Random(seed ^ 0xF80708)
        notes = []
        for bar in range(bars):
            base = bar * BEATS_PER_BAR
            for step in range(8):  # steady 8th-note ostinato — the driving Memphis pulse
                accent = step % 2 == 0
                notes.append({
                    "time_step": round(base + step * 0.5 + rng.uniform(-0.008, 0.012), 4),
                    "note": "C5",
                    "length": 0.18,
                    "velocity": rng.randint(96, 112) if accent else rng.randint(72, 90),
                })
        tracks["cowbell"] = notes
        return len(notes)

    rng = random.Random(seed ^ 0xCB0011)
    # Off-beat clank slots (the "and" of beats) — Carti places these sparse.
    slots = (0.75, 1.5, 2.75, 3.5)
    notes = []
    for bar in range(bars):
        base = bar * BEATS_PER_BAR
        for slot in slots:
            if rng.random() < 0.28:  # sparse: ~1 clank per bar on average
                notes.append({
                    "time_step": round(base + slot + rng.uniform(-0.01, 0.02), 4),
                    "note": "C5",
                    "length": 0.2,
                    "velocity": rng.randint(78, 104),
                })
    if len(notes) < 2:  # guarantee at least a hint of the signature
        notes.append({"time_step": 0.75, "note": "C5", "length": 0.2, "velocity": 92})

    tracks["cowbell"] = notes
    return len(notes)


def materialize_texture_notes(data: dict[str, Any]) -> int:
    """One atmospheric trigger per section (PLG Texture lane) — the scene layer.

    Sirens/rain/vinyl under the beat is what turns a loop into a place. One
    sample, retriggered at each section start so short atmospheres stay alive;
    starter_kit drops the lane when the library has no texture sample.
    Returns the number of notes written."""
    import os

    if os.environ.get("PLG_TEXTURE_LANE", "auto").strip().lower() in ("0", "off", "false", "no"):
        return 0
    tracks = data.get("tracks")
    arrangement = data.get("plg_arrangement")
    if not isinstance(tracks, dict) or not isinstance(arrangement, list):
        return 0
    if tracks.get("texture"):
        return 0
    variety = data.get("plg_variety") if isinstance(data.get("plg_variety"), dict) else {}
    genre = str(variety.get("genre") or data.get("plg_genre") or "").strip().lower()
    if genre and genre not in _TEXTURE_GENRES:
        return 0

    notes: list[dict[str, Any]] = []
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        try:
            start_bar = int(section.get("start_bar", 0))
            bars = int(section.get("bars", 0))
        except (TypeError, ValueError):
            continue
        if bars <= 0:
            continue
        big = str(section.get("name", "")) in ("chorus", "hook", "drop")
        notes.append({
            "time_step": round(start_bar * BEATS_PER_BAR, 4),
            "note": "C5",
            "length": round(bars * BEATS_PER_BAR, 4),
            "velocity": 58 if big else 48,
        })
        if len(notes) >= 12:
            break

    if notes:
        tracks["texture"] = notes
    return len(notes)


_MIN_OCTAVE_LAYER_BARS = 2  # a 1-bar transition smears too much to read as a background layer


def materialize_melody_octave_down_layer(
    data: dict[str, Any],
    *,
    profile: Any = None,
    semitones: int = -12,
    velocity_drop: int = 22,
    flag_name: str = "melody_octave_down_layer",
    texture_kind: str = "octave_down_layer",
    env_var: str = "PLG_OCTAVE_DOWN_LAYER",
) -> int:
    """Half-speed, octave-down duplicate of the FULL melody line (PLG Texture lane).

    Trap ear-candy move (Gross Beat's "half-speed + octave-down layer" trick,
    used to fatten a hook): a full copy of melody_lead, transposed -12
    semitones (default — see ``semitones``), riding melody_lead's own one-shot
    (see starter_kit.py's texture sample pick) rather than a generic
    atmosphere sample. The texture channel carries no time-stretch params —
    unlike melody_lead/counter_melody, see _MELODY_STRETCH_PARAMS in
    flp_writer.py — so FL's classic Sampler resampling naturally drags a
    lower-pitched copy to half speed under the original (the same physics as
    slowing a record down, no note-timing math needed) — a higher-pitched
    copy (positive ``semitones``, e.g. the minimal-trap "quiet octave-up
    shimmer") plays correspondingly FASTER instead, a quick reflective ghost
    rather than a half-speed drag. Confined to sections >= _MIN_OCTAVE_LAYER_BARS
    bars so a 1-bar transition never gets smeared. Opt-in via
    GenreProfile.<flag_name> — a no-op everywhere else.
    Returns the number of notes written.
    """
    import os

    if os.environ.get(env_var, "auto").strip().lower() in ("0", "off", "false", "no"):
        return 0
    if not bool(getattr(profile, flag_name, False)):
        return 0
    tracks = data.get("tracks")
    arrangement = data.get("plg_arrangement")
    if not isinstance(tracks, dict) or not isinstance(arrangement, list):
        return 0
    if tracks.get("texture"):
        return 0  # already materialized (best-of variants re-run humanize, or atmos claimed it first)
    melody = tracks.get("melody_lead")
    if not isinstance(melody, list) or not melody:
        return 0

    from music_theory import name_for_midi
    from pattern_utils import parse_note_name

    spans: list[tuple[float, float]] = []
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        try:
            start_bar = int(section.get("start_bar", 0))
            bars = int(section.get("bars", 0))
        except (TypeError, ValueError):
            continue
        if bars >= _MIN_OCTAVE_LAYER_BARS:
            spans.append((start_bar * BEATS_PER_BAR, (start_bar + bars) * BEATS_PER_BAR))
    if not spans:
        return 0

    notes: list[dict[str, Any]] = []
    for note in melody:
        if not isinstance(note, dict):
            continue
        t = note.get("time_step")
        if not isinstance(t, (int, float)):
            continue
        t = float(t)
        if not any(start <= t < end for start, end in spans):
            continue
        try:
            midi = parse_note_name(str(note.get("note", "C5"))) + semitones
        except ValueError:
            continue
        copy = dict(note)
        copy["note"] = name_for_midi(max(0, midi))
        copy["velocity"] = max(1, int(note.get("velocity", 100)) - velocity_drop)
        notes.append(copy)

    if notes:
        tracks["texture"] = notes
        data["plg_texture_kind"] = texture_kind
    return len(notes)


def materialize_riser_notes(data: dict[str, Any]) -> int:
    """Mike Dean (Travis Scott): a reversed-chords swell leads INTO each
    chorus/hook/drop instead of a crash landing exactly on it — one note in
    the bar immediately BEFORE the section, ringing up to the boundary.
    Claims the ``fx`` lane before materialize_fx_notes so a genre that wants
    this gets risers instead of crashes (one channel = one sample — they
    can't coexist). starter_kit bakes the actual reversed-chords audio (see
    plg_fx_kind) and drops the lane if there's no chords sample to reverse.
    Returns the number of notes written.
    """
    import os

    if os.environ.get("PLG_FX_LANE", "auto").strip().lower() in ("0", "off", "false", "no"):
        return 0
    tracks = data.get("tracks")
    arrangement = data.get("plg_arrangement")
    if not isinstance(tracks, dict) or not isinstance(arrangement, list):
        return 0
    if tracks.get("fx"):
        return 0  # already materialized (best-of variants re-run humanize)

    notes: list[dict[str, Any]] = []
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        if str(section.get("name", "")) not in ("chorus", "hook", "drop"):
            continue
        try:
            start_bar = int(section.get("start_bar", 0))
        except (TypeError, ValueError):
            continue
        if start_bar <= 0:
            continue  # nothing precedes bar 0 to swell out of
        lead_in = float((start_bar - 1) * BEATS_PER_BAR)
        notes.append({
            "time_step": round(lead_in, 4),
            "note": "C5",
            "length": BEATS_PER_BAR,
            "velocity": 112 if section.get("final_chorus") else 98,
        })
        if len(notes) >= _MAX_FX_NOTES:
            break

    if notes:
        tracks["fx"] = notes
        data["plg_fx_kind"] = "riser_from_chords"
    return len(notes)


DELAY_ACCENT_SECTIONS = ("chorus", "hook", "drop")
DELAY_ACCENT_PROBABILITY = 0.4  # not every landing — an occasional accent, not a pattern


def materialize_delay_accent_notes(data: dict[str, Any], *, rng: random.Random) -> int:
    """Mike Dean: an occasional delay-tail accent leading into SOME
    chorus/hook/drop landings — never all of them, never a constant layer.
    One note, one bar before each selected landing (same lead-in convention
    as materialize_riser_notes). Claims the fx lane before materialize_fx_notes
    if the genre wants it (one channel = one sample). `rng` is the song's own
    seeded RNG so the same generation seed reproduces the same placement.
    starter_kit bakes the actual delay-tail audio (see plg_fx_kind) and
    drops the lane if there's no chords sample to bake from.
    Returns the number of notes written.
    """
    import os

    if os.environ.get("PLG_FX_LANE", "auto").strip().lower() in ("0", "off", "false", "no"):
        return 0
    tracks = data.get("tracks")
    arrangement = data.get("plg_arrangement")
    if not isinstance(tracks, dict) or not isinstance(arrangement, list):
        return 0
    if tracks.get("fx"):
        return 0  # already materialized (riser/crash/alt-bass got there first)

    notes: list[dict[str, Any]] = []
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        if str(section.get("name", "")) not in DELAY_ACCENT_SECTIONS:
            continue
        if rng.random() >= DELAY_ACCENT_PROBABILITY:
            continue  # this landing doesn't get the accent — occasional, not constant
        try:
            start_bar = int(section.get("start_bar", 0))
        except (TypeError, ValueError):
            continue
        if start_bar <= 0:
            continue  # nothing precedes bar 0 to lead into
        lead_in = float((start_bar - 1) * BEATS_PER_BAR)
        notes.append({
            "time_step": round(lead_in, 4),
            "note": "C5",
            "length": BEATS_PER_BAR,
            "velocity": 100,
        })
        if len(notes) >= _MAX_FX_NOTES:
            break

    if notes:
        tracks["fx"] = notes
        data["plg_fx_kind"] = "delay_accent"
    return len(notes)


def materialize_fx_notes(data: dict[str, Any]) -> int:
    """Write crash/impact one-shots onto the ``fx`` lane at arrangement landings.

    Reads ``plg_arrangement`` directly (full-song grid) instead of the LLM's
    fx_intent, whose bars live on the 8-bar core grid. One channel = one sample,
    so only the crash family is placed: a crash on every chorus/hook/drop
    landing, louder at the final chorus. starter_kit attaches a crash sample and
    DROPS the lane when the library has none — no silent channel ships.
    Returns the number of notes written.
    """
    import os

    if os.environ.get("PLG_FX_LANE", "auto").strip().lower() in ("0", "off", "false", "no"):
        return 0
    tracks = data.get("tracks")
    arrangement = data.get("plg_arrangement")
    if not isinstance(tracks, dict) or not isinstance(arrangement, list):
        return 0
    if tracks.get("fx"):
        return 0  # already materialized (best-of variants re-run humanize)

    notes: list[dict[str, Any]] = []
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        if str(section.get("name", "")) not in ("chorus", "hook", "drop"):
            continue
        try:
            start = float(int(section.get("start_bar", 0)) * BEATS_PER_BAR)
        except (TypeError, ValueError):
            continue
        notes.append({
            "time_step": round(start, 4),
            "note": "C5",
            "length": 4.0,
            "velocity": 112 if section.get("final_chorus") else 98,
        })
        if len(notes) >= _MAX_FX_NOTES:
            break

    if notes:
        tracks["fx"] = notes
    return len(notes)

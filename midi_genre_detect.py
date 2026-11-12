"""Genre auto-detection from an uploaded MIDI file's musical content —
density, rhythm/interval shape, scale, tempo — instead of text-prompt
keywords or LLM guessing from mood words.

Deliberately reuses, never duplicates: music_theory.detect_root_pc/
detect_scale (already-working pitch-class-histogram detectors) and
style_corpus.BPM_RANGES / GenreProfile.melody_scale (already-tuned,
real per-genre reference data). This module only adds the NEW signals
those don't cover: note density and interval shape (average jump size,
chromatic-step fraction, wide-jump fraction, net pitch trend), plus
reading the file's own tempo — mido gives this directly, but
midi_ingest.read_midi_notes is deliberately tempo-agnostic (ticks->beats
only) and never surfaces it.

Scope: only classifies into GenreProfile.DETAILED_GENRES — the 10 genres
with real tuning. Detecting into an undetailed genre would be pointless
(see genre_profiles.DETAILED_GENRES / the "honest stub" gate).

Confidence-first, by design: bpm_fit and scale_fit are backed by real
data and dominate the score (weight 3.0 each); density/chromatic/
wide-jump/pitch-trend are first-pass, UNVALIDATED estimates (weight 1.0
or 0.5 each) that only break ties inside an already-narrowed BPM+scale
cluster. detect_genre_from_midi returns None — never a forced guess —
whenever there are too few notes, or the top-two candidates are too
close to call. A wrong call here drives the ENTIRE downstream generation
(BPM pool, chord corpus, drum blueprint, sample matching) and the
pre-generation DETAILED_GENRES gate, so silence is the safe default.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from music_theory import detect_root_pc, detect_scale, key_from_midi_key_name, key_from_sf_mi
from pattern_utils import parse_note_name

logger = logging.getLogger("plg.midi_genre_detect")

# MIDI stores tempo as microseconds-per-quarter-note (MPQN), not BPM.
# BPM = 60_000_000 / MPQN  (e.g. 500_000 → 120.0).
_MPQN_PER_MINUTE = 60_000_000

DETAILED_GENRES: tuple[str, ...] = (
    "trap", "rage", "opium", "drill", "underground",
    "west_coast", "detroit", "phonk", "pluggnb", "hyperpop",
)

MIN_NOTES = 8
_CONFIDENCE_FLOOR = 0.55   # best genre's normalized score must clear this...
_CONFIDENCE_MARGIN = 0.10  # ...AND beat the runner-up by at least this much

# First-pass density/interval-shape targets — NOT measured from a real
# corpus of confirmed-genre MIDI, unlike bpm_range (style_corpus.BPM_RANGES)
# and scale (GenreProfile.melody_scale) below, which are real and already
# tuned. Density is notes-per-beat. These exist only as tiebreakers within
# an already BPM+scale-narrowed cluster (e.g. rage/opium/drill are all
# phrygian and overlap heavily in tempo) — revisit once real uploads have
# been audited.
_GENRE_SHAPE_TARGETS: dict[str, dict[str, tuple[float, float]]] = {
    # trap-family cluster, mostly phrygian/overlapping-tempo — density is
    # the main thing separating "minimal sinister" opium from busier rage.
    "trap":        {"density": (1.0, 2.5), "chromatic_step_frac": (0.0, 0.35), "wide_jump_frac": (0.0, 0.25)},
    "rage":        {"density": (1.5, 3.5), "chromatic_step_frac": (0.0, 0.3), "wide_jump_frac": (0.0, 0.3)},
    "opium":       {"density": (0.4, 1.5), "chromatic_step_frac": (0.0, 0.3), "wide_jump_frac": (0.0, 0.2)},
    "drill":       {"density": (1.0, 2.5), "chromatic_step_frac": (0.0, 0.35), "wide_jump_frac": (0.0, 0.2)},
    "underground": {"density": (1.5, 3.5), "chromatic_step_frac": (0.0, 0.3), "wide_jump_frac": (0.0, 0.3)},
    # slow/melodic cluster (west_coast/detroit share a distinct 90-105 BPM
    # window, well separated from everything else) — detroit is the sparser,
    # "one short repeating phrase" of the two.
    "west_coast":  {"density": (0.4, 1.3), "chromatic_step_frac": (0.0, 0.25), "wide_jump_frac": (0.0, 0.2)},
    "detroit":     {"density": (0.3, 1.0), "chromatic_step_frac": (0.0, 0.2), "wide_jump_frac": (0.0, 0.15)},
    # phonk's melody_chromatic_walk flag is the sharpest existing signal
    # this module has for any genre — lean on it hard.
    "phonk":       {"density": (0.8, 2.5), "chromatic_step_frac": (0.35, 1.0), "wide_jump_frac": (0.0, 0.25)},
    "pluggnb":     {"density": (0.6, 1.8), "chromatic_step_frac": (0.0, 0.25), "wide_jump_frac": (0.0, 0.2)},
    # hyperpop's melody_chaotic_wide_jumps flag is the other sharp signal.
    "hyperpop":    {"density": (1.5, 4.0), "chromatic_step_frac": (0.0, 0.3), "wide_jump_frac": (0.3, 1.0)},
}
# GenreProfile.melody_upward_bias — the one genre with a directional-trend flag.
_UPWARD_BIAS_GENRES = frozenset({"underground"})

_TEMPO_WEIGHT = 3.0
_SCALE_WEIGHT = 3.0
_SHAPE_WEIGHT = 1.0
_TREND_WEIGHT = 0.5


def mpqn_to_bpm(mpqn: int | float) -> float | None:
    """Convert MIDI microseconds-per-quarter-note → BPM: ``60_000_000 / MPQN``."""
    try:
        value = float(mpqn)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    bpm = _MPQN_PER_MINUTE / value
    if 40.0 <= bpm <= 300.0:
        return round(bpm, 1)
    return None


def read_tempo_bpm(path: Path) -> float | None:
    """First ``set_tempo`` meta-message → BPM via MPQN (``60_000_000 / tempo``).

    Returns None if absent — never fabricates a default. Many short melodic
    uploads won't carry a real tempo meta-event at all.
    """
    import mido

    try:
        mid = mido.MidiFile(str(path))
    except Exception as exc:  # noqa: BLE001 — malformed MIDI, never crash
        logger.warning("MIDI tempo read failed %s: %s", path.name, exc)
        return None
    for track in mid.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                # msg.tempo is MPQN; mido.tempo2bpm == 60_000_000 / MPQN.
                bpm = mpqn_to_bpm(msg.tempo)
                if bpm is not None:
                    return bpm
    return None


def read_key_signature(path: Path) -> tuple[int, str] | None:
    """First MIDI ``key_signature`` meta → ``(root_pc, scale)``, or None.

    mido exposes the key as a name (``Cm``, ``F#``, …). The sf/mi table in
    ``music_theory.KEY_SIGNATURE_SF_MI`` is the canonical mapping; when a
    raw ``sf``/``mi`` pair is available it wins, otherwise the key name is
    parsed. Plan B (note histogram) lives in callers via detect_root_pc /
    detect_scale when this returns None.
    """
    import mido

    try:
        mid = mido.MidiFile(str(path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("MIDI key_signature read failed %s: %s", path.name, exc)
        return None
    for track in mid.tracks:
        for msg in track:
            if msg.type != "key_signature":
                continue
            sf = getattr(msg, "sf", None)
            mi = getattr(msg, "mi", None)
            if sf is not None and mi is not None:
                hit = key_from_sf_mi(sf, mi)
                if hit is not None:
                    return hit
            key_name = getattr(msg, "key", None)
            if key_name:
                hit = key_from_midi_key_name(str(key_name))
                if hit is not None:
                    return hit
    return None


def read_midi_control_markers(path: Path) -> dict[str, bool]:
    """Hidden MIDI markers used by the 12-instrument DNA sieve.

    Controllers / aftertouch / pitch bend shape the role gate:
      aftertouch          → Pad / Strings / Synth only
      CC 64 sustain       → Piano / Rhodes
      CC 11 / CC 7 waves  → Strings (bowing)
      CC 1 modulation     → long leads / strings
      CC 2 breath         → winds → Synth family bias
      CC 10 pan ping-pong → Bell / Pluck
      CC 74 filter        → electronic Synth
      pitch_bend_micro    → guitar slides
      pitch_bend_wide     → synth-lead dives
    """
    import mido

    markers = {
        "sustain_cc64": False,
        "expression_cc11": False,
        "volume_cc7": False,
        "mod_cc1": False,
        "breath_cc2": False,
        "pan_cc10_pingpong": False,
        "filter_cc74": False,
        "aftertouch": False,
        "pitch_bend": False,
        "pitch_bend_micro": False,
        "pitch_bend_wide": False,
    }
    try:
        mid = mido.MidiFile(str(path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("MIDI control-marker read failed %s: %s", path.name, exc)
        return markers

    seen_cc7: set[int] = set()
    seen_cc11: set[int] = set()
    seen_cc1: set[int] = set()
    seen_cc2: set[int] = set()
    seen_cc10: list[int] = []
    seen_cc74: set[int] = set()
    pb_abs: list[int] = []

    for track in mid.tracks:
        for msg in track:
            mtype = msg.type
            if mtype == "control_change":
                ctrl = int(msg.control)
                val = int(msg.value)
                if ctrl == 64 and val > 0:
                    markers["sustain_cc64"] = True
                elif ctrl == 11:
                    seen_cc11.add(val)
                elif ctrl == 7:
                    seen_cc7.add(val)
                elif ctrl == 1:
                    seen_cc1.add(val)
                elif ctrl == 2:
                    seen_cc2.add(val)
                elif ctrl == 10:
                    seen_cc10.append(val)
                elif ctrl == 74:
                    seen_cc74.add(val)
            elif mtype in ("aftertouch", "polytouch"):
                # Channel Pressure or Polyphonic Aftertouch — any non-zero press.
                if int(getattr(msg, "value", 0) or 0) > 0:
                    markers["aftertouch"] = True
            elif mtype == "pitchwheel":
                pitch = int(getattr(msg, "pitch", 0) or 0)
                if pitch != 0:
                    markers["pitch_bend"] = True
                    pb_abs.append(abs(pitch))

    # Automation = more than a single static value (bow / volume / filter waves).
    markers["expression_cc11"] = len(seen_cc11) >= 2
    markers["volume_cc7"] = len(seen_cc7) >= 2
    markers["mod_cc1"] = len(seen_cc1) >= 2
    markers["breath_cc2"] = len(seen_cc2) >= 2
    markers["filter_cc74"] = len(seen_cc74) >= 2

    # Ping-pong pan: aggressive L↔R jumps (not a static sit in one speaker).
    if len(seen_cc10) >= 4:
        lo, hi = min(seen_cc10), max(seen_cc10)
        crosses = 0
        for a, b in zip(seen_cc10, seen_cc10[1:]):
            if (a <= 40 and b >= 88) or (a >= 88 and b <= 40):
                crosses += 1
        markers["pan_cc10_pingpong"] = (hi - lo) >= 64 and crosses >= 2

    if pb_abs:
        peak_pb = max(pb_abs)
        # mido pitchwheel: ±8192. Micro slides ≪ semitone; wide dives are lead-like.
        markers["pitch_bend_micro"] = peak_pb <= 900 and peak_pb > 0
        markers["pitch_bend_wide"] = peak_pb >= 2400
    return markers


# General MIDI program (0–127) → one of the 12 FLEX DNA families.
# Families match the GM Level-1 spec the user asked for:
#   0–7 Piano · 8–15 Chromatic Perc (Bell/Marimba) · 24–31 Guitar
#   80–87 Synth Lead · 88–95 Synth Pad
_GM_PROGRAM_DNA: dict[int, str] = {
    0: "Piano", 1: "Piano", 2: "Piano", 3: "Piano",
    4: "Rhodes", 5: "Rhodes",
    6: "Key", 7: "Key",
    8: "Bell", 9: "Bell", 10: "Bell", 11: "Bell",
    12: "Bell", 13: "Bell", 14: "Bell", 15: "Pluck",
    16: "Key", 17: "Key", 18: "Key", 19: "Key", 20: "Key",
    21: "Key", 22: "Key", 23: "Key",
    24: "Guitar", 25: "Guitar",
    26: "Electric Guitar", 27: "Electric Guitar", 28: "Electric Guitar",
    29: "Electric Guitar", 30: "Electric Guitar", 31: "Electric Guitar",
    32: "Bass", 33: "Bass", 34: "Bass", 35: "Bass",
    36: "Bass", 37: "Bass", 38: "Bass", 39: "Bass",
    40: "Strings", 41: "Strings", 42: "Strings", 43: "Strings", 44: "Strings",
    45: "Pizzicato",
    46: "Pluck",
    48: "Strings", 49: "Strings", 50: "Strings", 51: "Pad",
    52: "Pad", 53: "Pad", 54: "Pad",
    56: "Synth", 57: "Synth", 58: "Synth", 59: "Synth",
    60: "Synth", 61: "Synth", 62: "Synth", 63: "Synth",
    64: "Synth", 65: "Synth", 66: "Synth", 67: "Synth",
    68: "Synth", 69: "Synth", 70: "Synth", 71: "Synth",
    72: "Key", 73: "Key", 74: "Key", 75: "Key",
    76: "Synth", 77: "Synth", 78: "Synth", 79: "Synth",
    80: "Synth", 81: "Synth", 82: "Synth", 83: "Synth",
    84: "Synth", 85: "Synth", 86: "Synth", 87: "Synth",
    88: "Pad", 89: "Pad", 90: "Pad", 91: "Pad",
    92: "Pad", 93: "Pad", 94: "Pad", 95: "Pad",
    96: "Pad", 97: "Pad", 98: "Pad", 99: "Pad",
    100: "Synth", 101: "Pad", 102: "Synth", 103: "Pad",
    104: "Pluck", 105: "Pluck", 106: "Pluck", 107: "Pluck", 108: "Pluck",
    110: "Strings",
    112: "Bell", 113: "Bell", 114: "Bell",
}

# Hard map: (CC0 bank MSB, CC32 bank LSB, Program Change) → DNA.
# Non-GM banks from GS/XG / DAW exports that would otherwise misread as GM.
_BANK_PROGRAM_DNA: dict[tuple[int, int, int], str] = {
    # Roland GS / common pad & lead variations (MSB=1 capital tones, etc.)
    (1, 0, 80): "Synth",
    (1, 0, 81): "Synth",
    (1, 0, 88): "Pad",
    (1, 0, 89): "Pad",
    (1, 0, 90): "Pad",
    (8, 0, 80): "Synth",
    (8, 0, 81): "Synth",
    (8, 0, 88): "Pad",
    (8, 0, 89): "Pad",
    # Yamaha XG SFX / bank-select leads often land here in marketplace packs
    (64, 0, 80): "Synth",
    (64, 0, 81): "Synth",
    (64, 0, 88): "Pad",
    (127, 0, 0): "Pad",  # some exporters park “atmosphere” here
}


def dna_from_gm_program(program: int) -> str | None:
    """Map a General MIDI program number (0–127) → one of the 12 DNA tags."""
    try:
        prog = int(program)
    except (TypeError, ValueError):
        return None
    if not 0 <= prog <= 127:
        return None
    return _GM_PROGRAM_DNA.get(prog)


def dna_from_bank_program(
    bank_msb: int | None,
    bank_lsb: int | None,
    program: int,
) -> str | None:
    """CC0 (Bank Select MSB) + CC32 (LSB) + Program Change → DNA.

    Returns a mapped DNA when the triple is in the hard table, otherwise
    falls back to GM program family when a bank select was actually present.
    """
    try:
        prog = int(program)
    except (TypeError, ValueError):
        return None
    if not 0 <= prog <= 127:
        return None
    if bank_msb is None and bank_lsb is None:
        return None
    msb = int(bank_msb or 0)
    lsb = int(bank_lsb or 0)
    hit = _BANK_PROGRAM_DNA.get((msb, lsb, prog))
    if hit:
        return hit
    # Bank byte present but no specialty row → still trust GM program family.
    return dna_from_gm_program(prog)


_JUNK_TRACK_NAME_RE = re.compile(
    r"^(midi\s*out|kontakt.*|microsoft\s*gs|default|untitled|track\s*\d+)\s*$",
    re.IGNORECASE,
)
# a kit pack / pack exporters often stamp the same template track_name
# ("Adieu - Bell - Ding - C 1") onto every MIDI in a pack — not a real instrument.
_PACK_TEMPLATE_TRACK_RE = re.compile(
    r"(bell\s*-\s*ding|ding\s*-\s*c\s*\d|template|placeholder)",
    re.IGNORECASE,
)


def _clean_midi_meta_name(raw: str) -> str:
    return str(raw or "").replace("\x00", "").strip()


def _is_junk_track_name(name: str) -> bool:
    text = _clean_midi_meta_name(name)
    if not text or len(text) < 2:
        return True
    if _JUNK_TRACK_NAME_RE.match(text):
        return True
    if _PACK_TEMPLATE_TRACK_RE.search(text):
        return True
    return False


# System markers that replace piano-roll geometry for FLEX role pick.
_SYSTEM_LOCK_SOURCES = frozenset({
    "text_event",
    "program_change",
    "track_name",
    "instrument_name",
    "bank_select+program",
})

# Bare plugin / host names in Instrument Name (0x04) / Track Name (0x03)
# when no Pluck/Pad/etc. keyword is present. Longer phrases first.
#
# DNA values are the existing 12-family tags (Synth/Pluck/Bell/Key/…).
# "skip" = recognise the brand so we do NOT invent a family from it
# (sampler hosts, junk channel names, bass-only engines on a melody lane).
_PLUGIN_DEFAULT_DNA: tuple[tuple[str, str], ...] = (
    # --- Aggressive / rage / modern wavetable → Synth -------------------
    ("serum 2", "Synth"),
    ("serum", "Synth"),
    ("phase plant", "Synth"),
    ("sylenth1", "Synth"),
    ("sylenth", "Synth"),
    ("massive x", "Synth"),
    ("massive", "Synth"),
    ("synth1", "Synth"),
    ("superstarsaw", "Synth"),
    ("superstar saw", "Synth"),
    ("dune 3", "Synth"),
    ("dune", "Synth"),
    ("pigments", "Synth"),
    ("electra 3", "Synth"),
    ("electra", "Synth"),
    ("tone2", "Synth"),
    ("tone 2", "Synth"),
    ("quanta 2", "Synth"),
    ("quanta", "Synth"),
    ("spline synth", "Synth"),
    ("spire", "Synth"),
    ("vital", "Synth"),
    ("harmor", "Synth"),
    ("sytrus", "Synth"),
    ("omnisphere", "Synth"),
    ("avenger", "Synth"),
    ("zebra", "Synth"),
    ("myth", "Synth"),
    ("bazille", "Synth"),
    ("ace", "Synth"),  # u-he ACE — short token, matched on word boundary
    # --- Dark analog / opium / heated keys → Synth (electronic family) -
    # Brand alone cannot tell Rhodes vs Pad; Synth beats piano/guitar guess.
    ("zenology", "Synth"),
    ("zen core", "Synth"),
    ("jupiter 4", "Synth"),
    ("jupiter", "Synth"),
    ("analog lab", "Synth"),
    ("prophet 5", "Synth"),
    ("prophet", "Synth"),
    ("synplant 2", "Synth"),
    ("synplant", "Synth"),
    ("stacks", "Synth"),
    ("diva", "Synth"),
    ("repro", "Synth"),
    ("hive", "Synth"),
    ("u he", "Synth"),  # "u-he" / "u_he" after normalize
    # --- Plugg / light cartoon → Bell / Pluck --------------------------
    ("meowsynth", "Pluck"),
    ("meow synth", "Pluck"),
    ("purity", "Bell"),
    # --- Romplers → Synth (generic electronic; keyword may refine later)
    ("na nexus", "Synth"),
    ("nexus 4", "Synth"),
    ("nexus2", "Synth"),
    ("nexus", "Synth"),
    ("xpand 2", "Synth"),
    ("xpand", "Synth"),
    # --- Vintage workstations → Key ------------------------------------
    ("korg m1", "Key"),
    ("triton extreme", "Key"),
    ("wavestation", "Key"),
    ("microwave 1", "Key"),
    ("microwave", "Key"),
    ("workstation", "Key"),
    ("trinity", "Key"),
    ("triton", "Key"),
    # --- Acoustic / piano libraries ------------------------------------
    ("keyscape", "Piano"),
    ("pianoteq", "Piano"),
    ("addictive keys", "Piano"),
    ("galaxy vintage", "Piano"),
    ("the giant", "Piano"),
    # --- Guitar --------------------------------------------------------
    ("session guitarist", "Guitar"),
    ("guitar rig", "Electric Guitar"),
    # --- Named orchestral / ensemble packs -----------------------------
    ("bloom ensemble strings", "Strings"),
    # --- Skip: sampler hosts, bass-only engines, junk channel names ----
    ("kontakt", "skip"),
    ("bassmaker", "skip"),
    ("subcraft", "skip"),
    ("variflute", "skip"),
    ("dw soundworks", "skip"),
    ("ad055", "skip"),
    ("dorothy", "skip"),
    ("attack 3", "skip"),
    ("swam", "skip"),
)

# Author text like "Type: Dark Hyperpop Pluck" — prefer the Type: clause.
_TYPE_CLAUSE_RE = re.compile(
    r"(?:type|style|sound|inst(?:rument)?)\s*[:=]\s*([^\n|;]+)",
    re.IGNORECASE,
)


def dna_from_plugin_name(text: str) -> str | None:
    """Map Instrument Name / Track Name plugin brands → DNA when keywords are absent.

    Short tokens (ace, myth, hive) require a word boundary so random English
    does not false-trigger. ``skip`` brands are recognised and deliberately
    return None (no family lock).
    """
    low = re.sub(r"[_\-.!]+", " ", str(text or "")).lower()
    low = re.sub(r"\s+", " ", low).strip()
    if not low:
        return None
    for plugin, dna in _PLUGIN_DEFAULT_DNA:
        needle = plugin.replace("-", " ")
        if " " in needle or len(needle) >= 5:
            hit = needle in low
        else:
            hit = bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", low))
        if not hit:
            continue
        if dna == "skip":
            return None
        return dna
    return None



def dna_from_meta_blob(text: str) -> str | None:
    """Keyword DNA, then plugin-brand fallback (0x01 / 0x04 / 0x03 strings)."""
    from melody_character import dna_from_text

    blob = _clean_midi_meta_name(text)
    if not blob or _is_junk_track_name(blob):
        return None
    # Prefer explicit Type:/Style: clause inside pack comments.
    for match in _TYPE_CLAUSE_RE.finditer(blob):
        clause = match.group(1).strip()
        dna = dna_from_text(clause)
        if dna:
            return dna
    dna = dna_from_text(blob)
    if dna:
        return dna
    return dna_from_plugin_name(blob)


def read_midi_instrument_hints(path: Path) -> dict[str, Any]:
    """Pull hidden MIDI system markers for DNA family locking.

    Real signal (geometry ignored):
      1. Text Event (0x01) — pack author tags (Type: … Pluck)
      2. Instrument Name (0x04) — plugin / instrument string
      3. Program Change (0xC0) + optional CC0 bank map
      4. Track/Sequence Name (0x03)

    Junk pack templates (a kit pack \"Bell - Ding\") are still stripped.
    """
    import mido

    hints: dict[str, Any] = {
        "track_names": [],
        "instrument_names": [],
        "text_events": [],
        "programs": [],
        "bank_msb": None,
        "bank_lsb": None,
        "bank_program_pairs": [],
        "dna_from_program": None,
        "dna_from_name": None,  # instrument_name 0x04
        "dna_from_track": None,  # track/sequence name 0x03
        "dna_from_text": None,  # text event 0x01
        "dna_from_bank_map": None,
        "lock_text": None,
        "lock_source": None,
        "track_lock_text": None,
        "text_lock_text": None,
        "system_lock": False,
    }
    try:
        mid = mido.MidiFile(str(path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("MIDI instrument-hint read failed %s: %s", path.name, exc)
        return hints

    track_names: list[str] = []
    instrument_names: list[str] = []
    text_events: list[str] = []
    programs: list[int] = []
    bank_pairs: list[dict[str, Any]] = []
    # Per-channel running bank select (CC0 MSB / CC32 LSB) before each PC.
    ch_bank_msb: dict[int, int] = {}
    ch_bank_lsb: dict[int, int] = {}
    first_bank_msb: int | None = None
    first_bank_lsb: int | None = None

    for track in mid.tracks:
        for msg in track:
            mtype = msg.type
            if mtype in ("track_name", "sequence_name"):
                name = _clean_midi_meta_name(getattr(msg, "name", "") or "")
                if name:
                    track_names.append(name)
            elif mtype == "instrument_name":
                name = _clean_midi_meta_name(getattr(msg, "name", "") or "")
                if name:
                    instrument_names.append(name)
            elif mtype == "text":
                # Meta Event 0x01 — author comments / pack tags.
                blob = _clean_midi_meta_name(getattr(msg, "text", "") or "")
                if blob:
                    text_events.append(blob)
            elif mtype == "control_change":
                try:
                    ctrl = int(msg.control)
                    val = int(msg.value)
                    ch = int(getattr(msg, "channel", 0) or 0)
                except (TypeError, ValueError, AttributeError):
                    continue
                if ctrl == 0:
                    ch_bank_msb[ch] = val
                    if first_bank_msb is None:
                        first_bank_msb = val
                elif ctrl == 32:
                    ch_bank_lsb[ch] = val
                    if first_bank_lsb is None:
                        first_bank_lsb = val
            elif mtype == "program_change":
                try:
                    prog = int(msg.program)
                    ch = int(getattr(msg, "channel", 0) or 0)
                except (TypeError, ValueError, AttributeError):
                    continue
                programs.append(prog)
                msb = ch_bank_msb.get(ch)
                lsb = ch_bank_lsb.get(ch)
                bank_pairs.append({
                    "channel": ch,
                    "program": prog,
                    "bank_msb": msb,
                    "bank_lsb": lsb,
                })

    hints["track_names"] = track_names
    hints["instrument_names"] = instrument_names
    hints["text_events"] = text_events
    hints["programs"] = programs
    hints["bank_msb"] = first_bank_msb
    hints["bank_lsb"] = first_bank_lsb
    hints["bank_program_pairs"] = bank_pairs

    # --- 0x01 Text Event (pack author tags) — highest trust when present -
    for blob in text_events:
        dna = dna_from_meta_blob(blob)
        if dna:
            hints["dna_from_text"] = dna
            hints["text_lock_text"] = blob
            break

    # --- 0x04 Instrument Name (plugin / instrument string) ---------------
    for blob in instrument_names:
        dna = dna_from_meta_blob(blob)
        if dna:
            hints["dna_from_name"] = dna
            break

    # --- Program Change (GM family) --------------------------------------
    for prog in programs:
        dna = dna_from_gm_program(prog)
        if dna:
            hints["dna_from_program"] = dna
            break

    # --- CC0 Bank Select + Program Change hard map -----------------------
    for pair in bank_pairs:
        if pair.get("bank_msb") is None and pair.get("bank_lsb") is None:
            continue
        dna = dna_from_bank_program(
            pair.get("bank_msb"),
            pair.get("bank_lsb"),
            int(pair["program"]),
        )
        if dna:
            hints["dna_from_bank_map"] = dna
            break

    # --- 0x03 Track/Sequence Name ----------------------------------------
    for blob in track_names:
        dna = dna_from_meta_blob(blob)
        if dna:
            hints["dna_from_track"] = dna
            hints["track_lock_text"] = blob
            break

    # Final hard lock priority (author text beats default GM program):
    #   text_event → instrument_name → bank_select+program → program → track
    if hints["dna_from_text"]:
        hints["lock_source"] = "text_event"
        hints["lock_text"] = hints["text_lock_text"] or str(hints["dna_from_text"])
        hints["system_lock"] = True
    elif hints["dna_from_name"]:
        hints["lock_source"] = "instrument_name"
        hints["lock_text"] = next(
            (b for b in instrument_names if dna_from_meta_blob(b)),
            str(hints["dna_from_name"]),
        )
        hints["system_lock"] = True
    elif hints["dna_from_bank_map"]:
        hints["lock_source"] = "bank_select+program"
        hints["lock_text"] = (
            f"CC0={hints['bank_msb']}/CC32={hints['bank_lsb']} "
            f"PC={programs[0] if programs else '?'} → {hints['dna_from_bank_map']}"
        )
        hints["system_lock"] = True
    elif hints["dna_from_program"]:
        hints["lock_source"] = "program_change"
        hints["lock_text"] = (
            f"PC={programs[0]} → {hints['dna_from_program']}"
            if programs else str(hints["dna_from_program"])
        )
        hints["system_lock"] = True
    elif hints["dna_from_track"]:
        hints["lock_source"] = "track_name"
        hints["lock_text"] = hints["track_lock_text"] or str(hints["dna_from_track"])
        hints["system_lock"] = True

    if hints["system_lock"] and hints["lock_source"] in _SYSTEM_LOCK_SOURCES:
        logger.info(
            "MIDI system lock file=%s source=%s dna=%s pc=%s bank_msb=%s "
            "bank_lsb=%s track=%r text=%r instrument=%r",
            path.name,
            hints["lock_source"],
            (
                hints.get("dna_from_text")
                or hints.get("dna_from_name")
                or hints.get("dna_from_bank_map")
                or hints.get("dna_from_program")
                or hints.get("dna_from_track")
            ),
            programs[0] if programs else None,
            hints.get("bank_msb"),
            hints.get("bank_lsb"),
            hints.get("track_lock_text"),
            hints.get("text_lock_text"),
            next(iter(instrument_names), None),
        )

    return hints


def _note_step(entry: dict[str, Any]) -> float:
    return float(entry.get("time_step", 0))


def extract_midi_features(notes: list[dict[str, Any]], tempo_bpm: float | None = None) -> dict[str, Any]:
    """Density/interval/scale features for genre classification. ``notes``
    is PLG's standard {time_step, note, length, velocity} format (e.g. from
    midi_ingest.read_midi_notes)."""
    ordered = sorted(notes, key=_note_step)
    span = _note_step(ordered[-1]) - _note_step(ordered[0]) if len(ordered) > 1 else 0.0
    density = (len(ordered) / span) if span > 0 else 0.0

    midis: list[int] = []
    for n in ordered:
        try:
            midis.append(parse_note_name(str(n.get("note", ""))))
        except (KeyError, TypeError, ValueError):
            continue

    intervals = [b - a for a, b in zip(midis, midis[1:])]
    abs_intervals = [abs(i) for i in intervals]
    avg_interval = (sum(abs_intervals) / len(abs_intervals)) if abs_intervals else 0.0
    chromatic_step_frac = (sum(1 for i in abs_intervals if i == 1) / len(abs_intervals)) if abs_intervals else 0.0
    wide_jump_frac = (sum(1 for i in abs_intervals if i >= 7) / len(abs_intervals)) if abs_intervals else 0.0
    pitch_trend = (midis[-1] - midis[0]) if len(midis) >= 2 else 0

    root_pc = detect_root_pc(notes) if notes else None
    scale = detect_scale(notes, root_pc) if notes and root_pc is not None else None

    return {
        "note_count": len(ordered),
        "density": density,
        "avg_interval": avg_interval,
        "chromatic_step_frac": chromatic_step_frac,
        "wide_jump_frac": wide_jump_frac,
        "pitch_trend": pitch_trend,
        "root_pc": root_pc,
        "scale": scale,
        "tempo_bpm": tempo_bpm,
    }


def _range_fit(value: float, lo: float, hi: float, *, tolerance: float = 0.25) -> float:
    """1.0 inside [lo, hi], falling off linearly outside over tolerance*span."""
    if lo <= value <= hi:
        return 1.0
    span = max(hi - lo, 1e-6)
    tol = span * tolerance
    if tol <= 0:
        return 0.0
    if value < lo:
        return max(0.0, 1.0 - (lo - value) / tol)
    return max(0.0, 1.0 - (value - hi) / tol)


def score_genres(features: dict[str, Any]) -> dict[str, float]:
    """Per-genre normalized fit score (0..1) for the 10 DETAILED_GENRES."""
    from genre_profiles import PROFILES
    from style_corpus import BPM_RANGES

    scores: dict[str, float] = {}
    for genre in DETAILED_GENRES:
        score = 0.0
        weight = 0.0

        bpm_range = BPM_RANGES.get(genre)
        if bpm_range and features.get("tempo_bpm") is not None:
            score += _TEMPO_WEIGHT * _range_fit(features["tempo_bpm"], *bpm_range)
            weight += _TEMPO_WEIGHT

        genre_scale = PROFILES[genre].melody_scale
        if genre_scale and features.get("scale") is not None:
            score += _SCALE_WEIGHT * (1.0 if features["scale"] == genre_scale else 0.0)
            weight += _SCALE_WEIGHT

        shape = _GENRE_SHAPE_TARGETS.get(genre, {})
        for feature_key in ("density", "chromatic_step_frac", "wide_jump_frac"):
            target = shape.get(feature_key)
            if target is not None:
                score += _SHAPE_WEIGHT * _range_fit(features[feature_key], *target)
                weight += _SHAPE_WEIGHT

        if genre in _UPWARD_BIAS_GENRES:
            score += _TREND_WEIGHT * (1.0 if features.get("pitch_trend", 0) > 0 else 0.0)
            weight += _TREND_WEIGHT

        scores[genre] = (score / weight) if weight > 0 else 0.0
    return scores


def detect_genre_from_midi(
    notes: list[dict[str, Any]],
    tempo_bpm: float | None = None,
    *,
    min_notes: int = MIN_NOTES,
) -> tuple[str, float] | None:
    """Best-fit genre + confidence (0..1), or None when the signal is too
    thin to trust — too few notes, or the top two candidates too close to
    call. Never forces a guess: a wrong call here drives the entire
    downstream generation, so silence (fall back to text-based resolution)
    is the safe default."""
    if len(notes) < min_notes:
        return None

    features = extract_midi_features(notes, tempo_bpm)
    scores = score_genres(features)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_genre, best_score = ranked[0]
    runner_score = ranked[1][1] if len(ranked) > 1 else 0.0

    if best_score < _CONFIDENCE_FLOOR:
        logger.info("MIDI genre detection inconclusive: best=%s score=%.2f (floor %.2f)", best_genre, best_score, _CONFIDENCE_FLOOR)
        return None
    if (best_score - runner_score) < _CONFIDENCE_MARGIN:
        logger.info(
            "MIDI genre detection ambiguous: %s=%.2f vs %s=%.2f (margin %.2f)",
            best_genre, best_score, ranked[1][0], runner_score, _CONFIDENCE_MARGIN,
        )
        return None
    logger.info("MIDI genre detection: %s (score=%.2f) features=%s", best_genre, best_score, features)
    return best_genre, round(best_score, 3)

"""Underground / rage producer brain — deterministic MIDI humanization after the LLM."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import random
import re
from copy import deepcopy
from typing import Any

from genre_profiles import GenreProfile, profile_for
from music_theory import detect_root_pc, diatonic_transpose, key_label, snap_pc_to_scale
from pattern_utils import TRACK_KEYS, count_active_sound_layers, parse_note_name, track_notes

logger = logging.getLogger("plg.humanize")

BEATS_PER_BAR = 4.0
# hats per bar a library hat line (hat_patterns) is refilled up to when a racing melody needs an audible clock
HAT_CELL_BAR_MINIMUM = 4
PPQ_SWING_MIN = 6 / 96.0   # dirtier swing — +6..+9 ticks @ PPQ 96
PPQ_SWING_MAX = 9 / 96.0
#: Hard stop for bass808_llm, the LLM kick/808 placer (see the project issue log (not included) 0au). The code stays, the path is
#: unreachable: no profile enables it AND this must be True, whatever API key is present. The contract
#: (ACTIVE_ROUTE.md) is that an LLM never places notes; tests that exercise the branch flip this constant.
KICK808_LLM_ALLOWED = False

#: Ceiling on GenreProfile.hat_swing as applied. trap and drill ask for 1.28 and
#: are held to this on purpose: drum_density._HAT_SWING_REACH_BEATS (0.11) is
#: derived from PPQ_SWING_MAX * this cap, and tests/test_drum_density.py guards it.
HAT_SWING_CAP = 1.15
#: thin_generated_hats' tolerance when a Param Plan raises the swing cap above
#: HAT_SWING_CAP: the new widest swing plus this margin (default reach is 0.11).
SWING_REACH_MARGIN = 0.002
SNARE_VEL_RANGE = (100, 127)
CLAP_VEL_RANGE = (95, 120)
MELODY_LAG_JITTER_MS = 2.0
# Kick and 808 fire on the SAME instant — the kick is the 808's attack
# transient ("кнопка включения"): kick punches the speaker, the 808's long
# tail carries the energy on from that same moment. Any offset splits one
# hit into two smeared events and (measured on the real BLAKK ROCKSTAR P2
# Kick + DOWN2EARTH 808 pair) costs ~1.9 dB in the kick's own 55-75 Hz
# fundamental band vs firing together.
PHASE_DELAY_BEATS = 0.0
DROP_GAP_START = 3.5
PRE_SNARE_SHIFT_MS = (2.0, 5.0)
ATTACK_FLATTEN_MS = 15.0   # 808 attack pushed so the click-kick punches first
HAT_DB_DOWN = 6.0  # Rule 25 — hats ~6 dB below clap/snare
VELOCITY_DB_FACTOR = 10 ** (-HAT_DB_DOWN / 20.0)
PITCH_BEND_PHRASE_BARS = 8


def _rng_for(pattern: dict[str, Any]) -> random.Random:
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    seed_src = "|".join(
        [
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
    return float(entry.get("time_step", 0))


def _note_end(entry: dict[str, Any]) -> float:
    return _note_step(entry) + float(entry.get("length", 0.25))


def _set_velocity(entry: dict[str, Any], value: int) -> None:
    entry["velocity"] = max(1, min(127, int(value)))


def ms_to_beats(ms: float, bpm: float) -> float:
    return ms / (60_000.0 / max(1.0, bpm))


def apply_pre_snare_shift(
    notes: list[dict[str, Any]],
    bpm: float,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Rule 22 — clap/snare 2–5 ms early for Atlanta push."""
    out: list[dict[str, Any]] = []
    for entry in notes:
        note = deepcopy(entry)
        ms = rng.uniform(*PRE_SNARE_SHIFT_MS)
        note["time_step"] = round(_note_step(note) - ms_to_beats(ms, bpm), 5)
        note["pre_snare_shift_ms"] = round(ms, 2)
        out.append(note)
    return sorted(out, key=_note_step)


def settle_generated_hat_register(
    notes: list[dict[str, Any]],
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Keep generated hi-hats in the native C5 pocket with small nearby color."""
    out: list[dict[str, Any]] = []
    for entry in notes:
        note = deepcopy(entry)
        step = _note_step(note)
        if note.get("hat_roll"):
            slot = int(round(step * 16))
            note["note"] = ("C5", "C#5", "D5")[slot % 3]
        else:
            eighth = int(round(step * 2))
            if eighth % 8 in (3, 7):
                note["note"] = "D5"
            elif rng is not None and rng.random() < 0.08:
                note["note"] = "C#5"
            else:
                note["note"] = "C5"
        out.append(note)
    return sorted(out, key=_note_step)


def move_late_midi_octave_lift_to_middle(data: dict[str, Any]) -> int:
    """Move MIDI octave-lift doubles out of the late drop into the middle hook."""
    if not data.get("plg_midi_melody"):
        return 0
    arrangement = data.get("plg_arrangement")
    tracks = data.get("tracks")
    if not isinstance(arrangement, list) or not isinstance(tracks, dict):
        return 0
    melody = tracks.get("melody_lead")
    if not isinstance(melody, list):
        return 0

    sections = [s for s in arrangement if isinstance(s, dict)]
    target = next(
        (
            s for s in sections
            if str(s.get("name") or "") in ("hook", "chorus")
            and int(s.get("start_bar", 0) or 0) > 0
        ),
        None,
    )
    source = next(
        (
            s for s in reversed(sections)
            if str(s.get("name") or "") == "drop"
            and int(s.get("start_bar", 0) or 0) > 0
        ),
        None,
    )
    if not target or not source:
        return 0

    target_start = float(target.get("start_bar", 0) or 0) * BEATS_PER_BAR
    target_len = max(BEATS_PER_BAR, float(target.get("bars", 0) or 0) * BEATS_PER_BAR)
    target_end = target_start + target_len
    source_start = float(source.get("start_bar", 0) or 0) * BEATS_PER_BAR
    source_end = source_start + max(BEATS_PER_BAR, float(source.get("bars", 0) or 0) * BEATS_PER_BAR)
    if source_start <= target_start:
        return 0

    kept: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    existing = {
        (round(_note_step(note), 3), str(note.get("note") or ""))
        for note in melody
        if not (
            isinstance(note, dict)
            and note.get("switch_octave")
            and source_start <= _note_step(note) < source_end
        )
    }
    for entry in melody:
        if (
            isinstance(entry, dict)
            and entry.get("switch_octave")
            and not entry.get("midi_sustained_octave_layer")
            and source_start <= _note_step(entry) < source_end
        ):
            note = deepcopy(entry)
            offset = (_note_step(note) - source_start) % target_len
            note["time_step"] = round(target_start + offset, 5)
            note["section"] = str(target.get("name") or "hook")
            key = (round(_note_step(note), 3), str(note.get("note") or ""))
            if key not in existing and _note_step(note) < target_end:
                moved.append(note)
                existing.add(key)
            continue
        kept.append(entry)
    if not moved:
        tracks["melody_lead"] = sorted(kept, key=_note_step)
        return 0
    tracks["melody_lead"] = sorted(kept + moved, key=_note_step)
    data["plg_midi_octave_lift_moved_to_middle"] = len(moved)
    return len(moved)


def apply_velocity_sine_curve(
    notes: list[dict[str, Any]],
    *,
    base: int = 100,
    amplitude: int = 22,
) -> list[dict[str, Any]]:
    """Rule 24 — sinusoidal velocity wave for percussion."""
    ordered = sorted(notes, key=_note_step)
    out: list[dict[str, Any]] = []
    total = max(1, len(ordered) - 1)
    for index, entry in enumerate(ordered):
        note = deepcopy(entry)
        phase = (index / total) * 2.0 * math.pi
        wave = math.sin(phase)
        _set_velocity(note, int(base + amplitude * wave))
        out.append(note)
    return out


def apply_six_db_hat_rule(
    hats: list[dict[str, Any]],
    reference_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rule 25 — hats ~6 dB below main clap/snare velocity.

    SCALES the lane under the ceiling instead of clamping: the old ``min(v,
    target)`` flattened every hat to one identical velocity (a robotic 52-wall)
    the moment the Markov dynamics sat above the cap. Now the loudest hat lands
    on the target and the groove's relative accents survive."""
    if not hats or not reference_notes:
        return hats
    ref_vel = max(int(n.get("velocity", 100)) for n in reference_notes)
    target = max(1, min(127, int(ref_vel * VELOCITY_DB_FACTOR)))
    loudest = max(int(n.get("velocity", 100)) for n in hats)
    factor = min(1.0, target / max(1, loudest))
    out: list[dict[str, Any]] = []
    for entry in hats:
        note = deepcopy(entry)
        current = int(note.get("velocity", 100))
        _set_velocity(note, max(24, int(round(current * factor))))
        note["hat_db_rule"] = True
        out.append(note)
    return out


def _is_open_hat(entry: dict[str, Any]) -> bool:
    if entry.get("hat_type") == "open":
        return True
    length = float(entry.get("length", 0.25))
    name = str(entry.get("note", "")).lower()
    return length >= 0.35 or "open" in name


def apply_open_hat_choke(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rule 26 — closed hat cuts open hat tail (choke group)."""
    ordered = sorted((deepcopy(n) for n in notes), key=_note_step)
    closed_hits = [_note_step(n) for n in ordered if not _is_open_hat(n)]

    for entry in ordered:
        entry["choke_group"] = "plg_hats"
        if not _is_open_hat(entry):
            entry["hat_type"] = "closed"
            continue
        entry["hat_type"] = "open"
        start = _note_step(entry)
        end = _note_end(entry)
        for hit in closed_hits:
            if start < hit < end:
                entry["length"] = max(0.05, round(hit - start, 5))
                entry["choked_by"] = hit
                break
    return ordered


_SNARE_HAT_DROP_WINDOW = 0.06  # beats — catches the hat hit sitting on the snare's own onset


def apply_hat_snare_micro_drop(
    hats: list[dict[str, Any]],
    snare_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Cut the hi-hat note landing on a (non-ghost) snare hit — a brief
    "micro-drop" instead of the hat ringing straight through the snare's own
    transient. Distinct from apply_six_db_hat_rule (that's a global velocity
    duck, not a per-hit gap) and from the roll injection (that crescendos
    INTO the snare, it doesn't leave a hole at the hit itself). Ghost snare
    taps don't trigger a drop — only real strikes."""
    if not hats or not snare_notes:
        return hats
    drop_times = {
        round(float(n.get("time_step", 0)), 4)
        for n in snare_notes
        if isinstance(n, dict) and not n.get("ghost")
    }
    if not drop_times:
        return hats
    out: list[dict[str, Any]] = []
    for entry in hats:
        step = round(_note_step(entry), 4)
        if any(abs(step - t) < _SNARE_HAT_DROP_WINDOW for t in drop_times):
            continue
        out.append(deepcopy(entry))
    return out


def apply_808_attack_flatten(
    bass_notes: list[dict[str, Any]],
    bpm: float,
) -> list[dict[str, Any]]:
    """Rule 27 — soften 808 attack so kick wins the transient."""
    delay = ms_to_beats(ATTACK_FLATTEN_MS, bpm)
    out: list[dict[str, Any]] = []
    for entry in bass_notes:
        note = deepcopy(entry)
        note["time_step"] = round(_note_step(note) + delay, 5)
        note["attack_ms"] = ATTACK_FLATTEN_MS
        vel = int(note.get("velocity", 127))
        _set_velocity(note, min(127, vel + 4))
        out.append(note)
    return sorted(out, key=_note_step)


def apply_mono_stereo_drop(
    melody_notes: list[dict[str, Any]],
    *,
    phrase_bars: int = PITCH_BEND_PHRASE_BARS,
) -> list[dict[str, Any]]:
    """Rule 28 — verse melody narrow mono; drop bars wide stereo."""
    out: list[dict[str, Any]] = []
    phrase_len = phrase_bars * BEATS_PER_BAR
    drop_window = 0.5  # beats before phrase boundary

    for entry in melody_notes:
        note = deepcopy(entry)
        step = _note_step(note)
        section = note.get("section")
        if section in ("chorus", "drop", "hook"):
            wide = True
        elif section in ("verse", "outro"):
            wide = False
        elif section == "intro":
            wide = True
        else:
            # No section tag (raw humanize call): fall back to phrase position.
            pos_in_phrase = step % phrase_len
            near_drop = pos_in_phrase >= phrase_len - drop_window
            bar_in_phrase = int(pos_in_phrase // BEATS_PER_BAR)
            wide = near_drop or bar_in_phrase >= phrase_bars - 1

        if wide:
            note["pan"] = 48 if int(step * 2) % 2 == 0 else 80
            note["stereo_width"] = 1.0
        else:
            note["pan"] = 64
            note["stereo_width"] = 0.15
            # Rule 28 — duck the verse melody so the drop hits harder.
            note["velocity"] = max(1, int(int(note.get("velocity", 100)) * 0.8))
        out.append(note)
    return out


def dedupe_heavy_overlaps(
    pattern: dict[str, Any],
    *,
    keys: tuple[str, ...] = ("kick", "sub_808"),
    eps: float = 0.03,
) -> None:
    """Censor: two heavy hits never share a tick — keep the loudest, drop the rest."""
    tracks = pattern.get("tracks")
    if not isinstance(tracks, dict):
        return
    for key in keys:
        notes = tracks.get(key)
        if not isinstance(notes, list) or len(notes) < 2:
            continue
        ordered = sorted(notes, key=lambda n: (float(n.get("time_step", 0)), -int(n.get("velocity", 0))))
        kept: list[dict[str, Any]] = []
        last: float | None = None
        for note in ordered:
            step = float(note.get("time_step", 0))
            if last is not None and abs(step - last) < eps:
                continue  # a louder hit already owns this tick
            kept.append(note)
            last = step
        tracks[key] = kept


_RAMP_IN_BARS = 1
_RAMP_SECTIONS = ("chorus", "drop")
_RAMP_LANES = ("fx", "chords", "texture", "cowbell")


def apply_chorus_density_ramp(pattern: dict[str, Any]) -> int:
    """"Silence as an instrument" (Dirkey): thin the ear-candy lanes for the
    first bar of every chorus/drop section instead of everything slamming in
    at once. Deliberately scoped to chorus/drop only and to the same 4
    optional lanes the sound-count cap gates (fx/chords/texture/cowbell) —
    core drums/melody/808 always hit immediately, and hook/intro are untouched
    on purpose (a prior fix made the hook hit full-band right away because 8
    bars of naked melody read as "nothing is happening"; ramping hook back in
    would undo that). Returns the number of notes removed."""
    arrangement = pattern.get("plg_arrangement")
    tracks = pattern.get("tracks")
    if not isinstance(arrangement, list) or not isinstance(tracks, dict):
        return 0
    ramp_windows: list[tuple[float, float]] = []
    for section in arrangement:
        if not isinstance(section, dict) or section.get("name") not in _RAMP_SECTIONS:
            continue
        start_bar = int(section.get("start_bar", 0))
        lo = start_bar * BEATS_PER_BAR
        ramp_windows.append((lo, lo + _RAMP_IN_BARS * BEATS_PER_BAR))
    if not ramp_windows:
        return 0

    removed = 0
    for lane in _RAMP_LANES:
        notes = tracks.get(lane)
        if not isinstance(notes, list) or not notes:
            continue
        kept = [
            n for n in notes
            if not any(lo <= _note_step(n) < hi for lo, hi in ramp_windows)
        ]
        removed += len(notes) - len(kept)
        tracks[lane] = kept
    return removed


_DENSITY_INVERSE_MIN_SECTIONS = 2   # need >=2 sections with melody notes for a meaningful average
_DENSITY_INVERSE_RATIO = 1.15       # section must be >15% busier than the record's average melody density
_ROLL_GROUP_GAP_BEATS = 0.1         # hat_roll notes closer than this belong to the same roll burst


def _hat_roll_groups(notes: list[dict[str, Any]]) -> list[list[int]]:
    """Group consecutive hat_roll-tagged note indices into roll instances —
    removing a roll must drop the whole burst, never leave a gapped stub."""
    groups: list[list[int]] = []
    current: list[int] = []
    prev_end: float | None = None
    for i, note in enumerate(notes):
        if not (isinstance(note, dict) and note.get("hat_roll")):
            if current:
                groups.append(current)
                current = []
            prev_end = None
            continue
        start = float(note.get("time_step", 0))
        if current and prev_end is not None and start - prev_end > _ROLL_GROUP_GAP_BEATS:
            groups.append(current)
            current = []
        current.append(i)
        prev_end = start + float(note.get("length", 0))
    if current:
        groups.append(current)
    return groups


def _section_windows_with_density(data: dict[str, Any]) -> list[tuple[float, float, float]] | None:
    """(start_beat, end_beat, density_ratio_vs_song_average) per real arranged
    section, keyed off ``plg_arrangement`` + ``melody_lead`` — the same
    per-section melody density apply_melody_drum_density_inverse thins drums
    against, factored out so the hat-roll ELIGIBILITY gate below can use the
    real signal too instead of a fixed section-name list. Returns None when
    there isn't enough information for a meaningful average (no arrangement,
    no melody, too few sections, or a silent melody) so callers fall back to
    their own static default rather than trusting a degenerate ratio."""
    arrangement = data.get("plg_arrangement")
    tracks = data.get("tracks")
    if not isinstance(arrangement, list) or not isinstance(tracks, dict):
        return None
    melody = tracks.get("melody_lead")
    if not isinstance(melody, list) or not melody:
        return None

    sections: list[tuple[float, float, int]] = []
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        try:
            start_bar = int(section.get("start_bar", 0))
            bars = int(section.get("bars", 0))
        except (TypeError, ValueError):
            continue
        if bars > 0:
            sections.append((start_bar * BEATS_PER_BAR, (start_bar + bars) * BEATS_PER_BAR, bars))
    if len(sections) < _DENSITY_INVERSE_MIN_SECTIONS:
        return None

    def _density(start: float, end: float, bars: int) -> float:
        count = sum(
            1 for n in melody
            if isinstance(n, dict) and start <= float(n.get("time_step", 0)) < end
        )
        return count / bars

    densities = [_density(s, e, b) for s, e, b in sections]
    avg = sum(densities) / len(densities)
    if avg <= 0:
        return None
    return [(s, e, d / avg) for (s, e, _b), d in zip(sections, densities)]


def _roll_eligible_windows(data: dict[str, Any]) -> list[tuple[float, float]] | None:
    """Sections whose OWN melody density doesn't run hot (<=
    ``_DENSITY_INVERSE_RATIO`` above the song's average) are allowed to roll —
    replaces the fixed hook/chorus/drop allow-list (arranger.ROLL_SECTIONS)
    with the real density signal, so a sparse verse/outro can roll and a
    busy chorus/hook occurrence can't. Returns None when there's no
    melody/arrangement to judge density from — callers should fall back to
    ROLL_SECTIONS in that case."""
    windows = _section_windows_with_density(data)
    if windows is None:
        return None
    return [(s, e) for s, e, ratio in windows if ratio <= _DENSITY_INVERSE_RATIO]


def apply_melody_drum_density_inverse(data: dict[str, Any]) -> int:
    """"Trust the Process" principle: arrange the melody by section, THEN let
    the drums answer it — where the melody is busier than the record's own
    average, thin the OPTIONAL drum extras (add_kick_syncopation's tagged
    ``kick_sync`` hits, hat_roll bursts) instead of letting both fight for the
    same rhythmic space. Never touches the core kick/snare/hat grid — only
    tagged, already-optional hits, the same "removal only" safety rule as
    apply_chorus_density_ramp. Relative-to-the-record's-own-average threshold
    (not an absolute density number) so it self-calibrates across BPM/genre.
    Returns the number of notes removed."""
    tracks = data.get("tracks")
    windows = _section_windows_with_density(data)
    if windows is None or not isinstance(tracks, dict):
        return 0

    # On a sharp/racing разъеб the kit should get denser with the MIDI — don't
    # strip kick_sync / hat rolls that the blueprint just added for energy.
    energy = data.get("plg_melody_energy") if isinstance(data.get("plg_melody_energy"), dict) else {}
    try:
        busyness = float(energy.get("busyness") or 0.0)
    except (TypeError, ValueError):
        busyness = 0.0
    pace = str(energy.get("pace") or "")
    attack = str(energy.get("attack") or "")
    if pace in ("racing", "driving") and busyness >= 0.55:
        return 0
    if busyness >= 0.65 and attack == "sharp":
        return 0

    dense_windows = [(s, e) for s, e, ratio in windows if ratio > _DENSITY_INVERSE_RATIO]
    if not dense_windows:
        return 0

    removed = 0

    kick = tracks.get("kick")
    if isinstance(kick, list) and kick:
        kept = []
        for note in kick:
            if isinstance(note, dict) and note.get("kick_sync"):
                t = float(note.get("time_step", 0))
                if any(s <= t < e for s, e in dense_windows):
                    removed += 1
                    continue
            kept.append(note)
        tracks["kick"] = kept

    hats = tracks.get("hi_hats")
    if isinstance(hats, list) and hats:
        drop: set[int] = set()
        for group in _hat_roll_groups(hats):
            start = float(hats[group[0]].get("time_step", 0))
            if any(s <= start < e for s, e in dense_windows):
                drop.update(group)
        if drop:
            removed += len(drop)
            tracks["hi_hats"] = [n for i, n in enumerate(hats) if i not in drop]

    return removed


def apply_west_coast_pad_holdback(data: dict[str, Any]) -> int:
    """Jay Cactus: the pad/"roads" sits out the first hook/chorus pass and
    enters on the repeat — remove chords-lane notes from the FIRST chorus/hook
    occurrence only, everywhere else untouched. Runs AFTER compose_chord_bed
    (chords aren't materialized until then) and reads the occurrence field
    arrange_song already records per section. Returns the number removed."""
    arrangement = data.get("plg_arrangement")
    tracks = data.get("tracks")
    if not isinstance(arrangement, list) or not isinstance(tracks, dict):
        return 0
    chords = tracks.get("chords")
    if not isinstance(chords, list) or not chords:
        return 0

    hold_windows: list[tuple[float, float]] = []
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        if section.get("name") not in ("chorus", "hook", "drop"):
            continue
        if section.get("occurrence") != 1:
            continue
        try:
            start_bar = int(section.get("start_bar", 0))
            bars = int(section.get("bars", 0))
        except (TypeError, ValueError):
            continue
        if bars > 0:
            hold_windows.append((start_bar * BEATS_PER_BAR, (start_bar + bars) * BEATS_PER_BAR))
    if not hold_windows:
        return 0

    kept: list[dict[str, Any]] = []
    removed = 0
    for note in chords:
        t = float(note.get("time_step", 0)) if isinstance(note, dict) else 0.0
        if any(s <= t < e for s, e in hold_windows):
            removed += 1
            continue
        kept.append(note)
    tracks["chords"] = kept
    return removed


_MAJOR_DUPLICATE_TRANSPOSE = 3  # relative major: same key signature, root +3 semitones
_MAJOR_DUPLICATE_VELOCITY_DROP = 20


def add_major_duplicate_chords(data: dict[str, Any]) -> int:
    """Sam Tsui (Post Malone-style pop): a quiet second copy of the chord bed,
    transposed +3 semitones to the relative major, riding counter_melody
    (repurposed — pop's usual off-beat answer line steps aside for this
    instead). The classic pop 'bittersweet' color: major harmony floating
    over the minor progression underneath. Runs AFTER compose_chord_bed
    (chords aren't materialized until then). Returns the number written."""
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return 0
    chords = tracks.get("chords")
    if not isinstance(chords, list) or not chords:
        return 0
    dup: list[dict[str, Any]] = []
    for entry in chords:
        if not isinstance(entry, dict):
            continue
        try:
            midi = parse_note_name(str(entry.get("note", "A4"))) + _MAJOR_DUPLICATE_TRANSPOSE
        except ValueError:
            continue
        note = deepcopy(entry)
        note["note"] = _midi_to_name(max(0, min(131, midi)))
        _set_velocity(note, int(entry.get("velocity", 70)) - _MAJOR_DUPLICATE_VELOCITY_DROP)
        dup.append(note)
    if dup:
        tracks["counter_melody"] = dup
    return len(dup)


HARMONIZER_INTERVAL_STEPS = 2   # diatonic third above; a fifth would be steps=4
HARMONIZER_VELOCITY_DROP = 25   # quieter than melody_lead — matches every other duplicate-layer convention


def materialize_melody_harmonizer(data: dict[str, Any], *, profile: GenreProfile) -> int:
    """Mike Dean: scale-aware diatonic harmony (a third above each melody
    note, staying in-scale — not a fixed chromatic interval) on
    counter_melody — repurposed, same established pattern as
    add_major_duplicate_chords for pop ('the usual off-beat answer line
    steps aside'). Root/scale resolved the same way every other scale-aware
    postprocess in this function does: detect_root_pc(melody) +
    profile.melody_scale. Returns the number of harmony notes written."""
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return 0
    melody = tracks.get("melody_lead")
    if not isinstance(melody, list) or not melody:
        return 0
    scale = profile.melody_scale or "natural_minor"
    root_pc = detect_root_pc(melody)
    harmony: list[dict[str, Any]] = []
    for entry in melody:
        if not isinstance(entry, dict):
            continue
        try:
            midi = parse_note_name(str(entry.get("note", "A4")))
        except ValueError:
            continue
        note = deepcopy(entry)
        shifted = diatonic_transpose(midi, root_pc, scale, HARMONIZER_INTERVAL_STEPS)
        note["note"] = _midi_to_name(max(0, min(131, shifted)))
        _set_velocity(note, int(entry.get("velocity", 100)) - HARMONIZER_VELOCITY_DROP)
        harmony.append(note)
    if harmony:
        tracks["counter_melody"] = harmony
    return len(harmony)


_SPARSE_COUNTER_MIN_GAP_BEATS = 2.0    # only answer real rests, not tiny cracks between phrase notes
_SPARSE_COUNTER_FILL_RATE = 0.45       # keep it rare — call-and-response, not a note in every rest
_SPARSE_COUNTER_VELOCITY_DROP = 30     # quieter than melody_lead, sits behind it
_SPARSE_COUNTER_ANSWER_LAG_BEATS = 0.25  # the answer lands just after the rest starts, not on top of it
_SPARSE_COUNTER_MAX_LEN_BEATS = 2.0
_SPARSE_COUNTER_REGISTER_OCTAVES = (12, -12)  # answers an octave above or below the hook


def materialize_sparse_counter_melody_fallback(
    data: dict[str, Any], *, profile: GenreProfile, rng: random.Random
) -> int:
    """Last-resort floor for counter_melody: runs only when NOTHING upstream
    (LLM composer, opium symbolic repair, or any of the genre-specific
    counter_melody generators above — harmonizer/bell-octave/muted-top/major-
    duplicate) put anything in the lane. Unlike materialize_melody_harmonizer
    (a harmony note on EVERY melody note — a parallel-third doubling, right
    for toliver's dedicated "harmony voice"), this answers only in
    melody_lead's own rests, sparsely, in a different register — the
    call-and-response character every composer prompt already asks for
    (COMPOSER_SYSTEM/ARCHITECT_SYSTEM in llm_pipeline.py) but has no hard
    enforcement behind it, so an empty array is a legal LLM response. Only
    ever fills an EMPTY lane — never overwrites existing content, so it can't
    fight anything that already claimed the lane. Returns notes placed."""
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return 0
    if track_notes(data, "counter_melody"):
        return 0
    melody = tracks.get("melody_lead")
    if not isinstance(melody, list) or len(melody) < 2:
        return 0

    ordered = sorted((n for n in melody if isinstance(n, dict)), key=_note_step)
    scale = profile.melody_scale or "natural_minor"
    root_pc = detect_root_pc(ordered)

    counter: list[dict[str, Any]] = []
    for prev, nxt in zip(ordered, ordered[1:]):
        gap_start = _note_end(prev)
        gap_end = _note_step(nxt)
        gap = gap_end - gap_start
        if gap < _SPARSE_COUNTER_MIN_GAP_BEATS:
            continue
        if rng.random() > _SPARSE_COUNTER_FILL_RATE:
            continue
        try:
            base_midi = parse_note_name(str(prev.get("note", "A4")))
        except ValueError:
            continue
        octave = rng.choice(_SPARSE_COUNTER_REGISTER_OCTAVES)
        pitch = snap_pc_to_scale(base_midi + octave, root_pc, scale)
        start = gap_start + min(_SPARSE_COUNTER_ANSWER_LAG_BEATS, gap * 0.3)
        length = max(0.25, min(_SPARSE_COUNTER_MAX_LEN_BEATS, gap_end - start - 0.1))
        counter.append(
            {
                "time_step": round(start, 4),
                "note": _midi_to_name(max(0, min(131, pitch))),
                "length": round(length, 4),
                "velocity": max(1, min(127, int(prev.get("velocity", 100)) - _SPARSE_COUNTER_VELOCITY_DROP)),
            }
        )
    if counter:
        tracks["counter_melody"] = counter
    return len(counter)


def build_pitch_bend_automation(
    pattern: dict[str, Any],
    *,
    phrase_bars: int = PITCH_BEND_PHRASE_BARS,
) -> list[dict[str, Any]]:
    """Rule 23 — pitch bend dip (-2 st) before each 8-bar transition."""
    max_step = 0.0
    for key in ("melody_lead", "counter_melody"):
        for entry in track_notes(pattern, key):
            max_step = max(max_step, _note_end(entry))

    phrase_len = phrase_bars * BEATS_PER_BAR
    phrases = max(1, int(max_step // phrase_len))
    events: list[dict[str, Any]] = []

    for p in range(1, phrases + 1):
        boundary = p * phrase_len
        dip_start = boundary - 0.5
        events.extend([
            {"time_step": round(dip_start, 4), "track": "melody_lead", "value": 8192},
            {"time_step": round(boundary - 0.08, 4), "track": "melody_lead", "value": 0},
            {"time_step": round(boundary, 4), "track": "melody_lead", "value": 8192},
        ])
    return events


def chord_change_glides_enabled() -> bool:
    return os.environ.get("PLG_CHORD_GLIDES", "auto").strip().lower() not in (
        "0", "false", "no", "off",
    )


_GLIDE_MAX_SEMITONES = 2.0  # default MIDI pitch-wheel range (no RPN reset sent)
_GLIDE_MAX_GAP_BEATS = 1.0  # notes farther apart than this don't read as "connected"


def build_chord_change_glides(
    pattern: dict[str, Any],
    *,
    track_key: str = "melody_lead",
) -> list[dict[str, Any]]:
    """Glide into the next chord tone at chord-change boundaries (Dirkey "How To
    Make UNLIMITED Melodies": note A slides into note B instead of retriggering,
    for consecutive notes that land on different chords/bars).

    Approximated as pitch-bend automation — same 3-point shape (center -> bend
    -> snap back to center) as the existing phrase dip above, just retargeted to
    the actual interval at a real chord change instead of a fixed -2 st dip on a
    fixed 8-bar clock. .mid export only: ``midi_export.py`` already turns these
    into real ``pitchwheel`` messages; the native .flp writer does not consume
    ``pitch_bend_automation`` at all (a genuine .flp-native glide would mean new
    binary automation-clip authoring — a bigger, riskier piece of work, not
    attempted here). Leaps bigger than the default +-2 semitone pitch-wheel
    range are skipped rather than mis-bent (no RPN pitch-bend-range message is
    sent, so bending further would just clip at the wrong pitch).
    """
    if not chord_change_glides_enabled():
        return []
    prog = pattern.get("plg_progression")
    degrees = list(prog.get("bar_degrees") or []) if isinstance(prog, dict) else []
    notes = sorted(track_notes(pattern, track_key), key=_note_step)
    if not degrees or len(notes) < 2:
        return []

    events: list[dict[str, Any]] = []
    for prev, nxt in zip(notes, notes[1:]):
        prev_bar = int(_note_step(prev) // BEATS_PER_BAR)
        next_bar = int(_note_step(nxt) // BEATS_PER_BAR)
        if degrees[prev_bar % len(degrees)] == degrees[next_bar % len(degrees)]:
            continue  # same chord — a plain retrigger, no glide
        gap = _note_step(nxt) - _note_end(prev)
        if gap < -0.02 or gap > _GLIDE_MAX_GAP_BEATS:
            continue  # overlapping or too far apart to read as connected
        try:
            interval = parse_note_name(str(nxt.get("note", ""))) - parse_note_name(
                str(prev.get("note", ""))
            )
        except ValueError:
            continue
        if interval == 0 or abs(interval) > _GLIDE_MAX_SEMITONES:
            continue
        target = max(0, min(16383, int(round(8192 + (interval / _GLIDE_MAX_SEMITONES) * 8191))))
        ramp_start = _note_end(prev) - min(0.2, max(0.0, gap) * 0.5 + 0.05)
        events.extend([
            {"time_step": round(max(0.0, ramp_start), 4), "track": track_key, "value": 8192},
            {"time_step": round(_note_step(nxt) - 0.02, 4), "track": track_key, "value": target},
            {"time_step": round(_note_step(nxt) + 0.02, 4), "track": track_key, "value": 8192},
        ])
    return events


def add_counter_melody_offbeat(
    melody_notes: list[dict[str, Any]],
    kick_notes: list[dict[str, Any]],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Rule 29 — counter melody only on off-beats, never with kick."""
    if not melody_notes:
        return []

    kick_steps = {_note_step(k) for k in kick_notes}
    melody_steps = {_note_step(m) for m in melody_notes}
    counter: list[dict[str, Any]] = []

    for entry in melody_notes:
        step = _note_step(entry)
        bar_base = int(step // BEATS_PER_BAR) * BEATS_PER_BAR
        # Off-beats: eighth-note syncopation (0.5, 1.5, 2.5, 3.5 …)
        for offset in (0.5, 1.5, 2.5, 3.5):
            hit = round(bar_base + offset, 4)
            if hit <= step:
                continue
            if any(abs(hit - ks) < 0.06 for ks in kick_steps):
                continue
            if any(abs(hit - ms) < 0.06 for ms in melody_steps):
                continue
            if rng.random() > 0.45:
                continue
            try:
                midi = parse_note_name(str(entry.get("note", "A4")))
            except ValueError:
                midi = 69
            counter.append({
                "time_step": hit,
                "note": _midi_to_name(min(91, midi + rng.choice((7, 12, 19)))),
                "length": rng.choice((0.125, 0.2, 0.25)),
                "velocity": rng.randint(68, 92),
                "pan": rng.choice((44, 84)),
                "offbeat_sync": True,
            })
            break

    return sorted(counter, key=_note_step)[:16]


def humanize_velocities(
    notes: list[dict[str, Any]],
    *,
    lo: int,
    hi: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in notes:
        note = deepcopy(entry)
        if note.get("ghost") or note.get("fill"):
            # Ghost hits keep their quiet velocity; fill hits keep their
            # crescendo — re-randomizing either flattens the groove.
            out.append(note)
            continue
        _set_velocity(note, rng.randint(lo, hi))
        out.append(note)
    return out


def apply_hi_hat_swing(
    notes: list[dict[str, Any]],
    rng: random.Random,
    *,
    intensity: float = 1.0,
    swing_min: float = PPQ_SWING_MIN,
    swing_max: float = PPQ_SWING_MAX,
) -> list[dict[str, Any]]:
    """Micro-timing: the off-beat eighth ("&", x.5) lands late for bounce.

    ``intensity`` scales the swing depth per genre (0 = grid-locked, 1 = trap).

    Which notes swing is read from each note's own position in the beat, never
    from its index in the list: until 2026-10-01 it was ``index % 2 == 1`` with
    the sign flipped on ``index % 4 in (1, 3)``, which is every odd index, so
    every swung hat landed EARLY (-0.07..-0.11 beats on the corpus) and, once
    gaps or rolls broke the alternation, the wrong notes moved — roll notes
    swapped places.

    Roll notes (``hat_roll``) never swing; a roll is a deliberate machine burst.
    A note is also left on the grid when its late position would come within
    ``midi_lowend_director.HAT_ROLL_GAP`` of the next hat: the "&" right before
    a pre-snare roll would otherwise flam into the roll's first note and read
    as part of the roll to ``rhythmic_anchors``, which decides kick deletions.
    """
    from midi_lowend_director import HAT_ROLL_GAP

    out: list[dict[str, Any]] = []
    if intensity <= 0:
        return [deepcopy(n) for n in sorted(notes, key=_note_step)]
    ordered = sorted(notes, key=_note_step)
    for index, entry in enumerate(ordered):
        note = deepcopy(entry)
        step = _note_step(note)
        is_offbeat = abs(step % 1.0 - 0.5) < 0.06 and not note.get("hat_roll")
        if is_offbeat:
            shift = rng.uniform(swing_min, swing_max) * intensity
            following = _note_step(ordered[index + 1]) if index + 1 < len(ordered) else None
            if following is None or following - (step + shift) > HAT_ROLL_GAP:
                note["time_step"] = round(step + shift, 5)
        out.append(note)
    return out


_HAT_FORWARD_LAG_MIN_PCT = 0.05
_HAT_FORWARD_LAG_MAX_PCT = 0.08
_HAT_GRID_UNIT_BEATS = 0.5  # the hi-hat's own 1/8 grid spacing


def apply_hat_forward_lag(
    notes: list[dict[str, Any]],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Shift the WHOLE hi-hat lane forward by one constant 5-8% of its 1/8
    grid unit — a "rushed ahead of the kick" feel. Distinct from
    apply_hi_hat_swing above: that lands each off-beat eighth late
    (groove *within* the hat line); this moves every note the same amount in
    the same direction (the lane sits early *relative to* the kick/other
    lanes, which don't move). One random draw for the whole track, not
    per-note — the ask is a single lane-wide offset, not more jitter."""
    if not notes:
        return notes
    shift = rng.uniform(_HAT_FORWARD_LAG_MIN_PCT, _HAT_FORWARD_LAG_MAX_PCT) * _HAT_GRID_UNIT_BEATS
    out: list[dict[str, Any]] = []
    for entry in notes:
        note = deepcopy(entry)
        note["time_step"] = round(max(0.0, _note_step(note) + shift), 5)
        out.append(note)
    return out


def apply_hat_roll_pitch(
    notes: list[dict[str, Any]],
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Pitch roller: rolls drift +/-2-3 semitones, never further, in ONE
    direction per roll (picked at random) — not the old unbounded always-up
    creep (which on a 5-6 note 1/64 roll drifted +4/+5 semitones). Only notes
    tagged ``hat_roll`` (hat_roll_engine._build_roll_notes) are touched — the
    steady 1/8 line stays strictly C5 (see DRUM_LAYOUT_KEYS comment above:
    hi-hats are deliberately NOT in normalize_registers' native-key snap,
    specifically so this function can shape rolls)."""
    ordered = sorted(notes, key=_note_step)
    out: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []

    def flush(group_notes: list[dict[str, Any]]) -> None:
        if len(group_notes) < 3:
            out.extend(deepcopy(n) for n in group_notes)
            return
        direction = rng.choice((-1, 1)) if rng is not None else 1
        cap = rng.randint(2, 3) if rng is not None else 2
        for i, entry in enumerate(group_notes):
            note = deepcopy(entry)
            try:
                midi = parse_note_name(str(note.get("note", "C5")))
                note["note"] = _midi_to_name(midi + direction * min(cap, i))
            except ValueError:
                pass
            note["pan"] = _roll_pan(i, len(group_notes))
            out.append(note)

    for entry in ordered:
        if entry.get("hat_roll"):
            group.append(entry)
            continue
        if group:
            flush(group)
            group = []
        out.append(deepcopy(entry))
    if group:
        flush(group)
    return sorted(out, key=_note_step)


_ROLL_PAN_SPREAD = 50  # ~40% of the 0-127 width — L-C-R staircase across a roll


def _roll_pan(index: int, total: int) -> int:
    """L-C-R sweep across a roll — |offset| traces 40%/20%/0/20%/40% (the
    staircase) since a linear pass through center IS that magnitude curve
    sampled at even points. Was +/-20 (~16%), widened to +/-50 (~40%)."""
    if total <= 1:
        return 64
    phase = index / max(1, total - 1)
    return int(64 + _ROLL_PAN_SPREAD * (phase * 2 - 1))


def _midi_to_name(midi: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    octave = midi // 12
    return f"{names[midi % 12]}{octave}"


_HAT_PAN_JITTER_SPREAD = 10  # ~8% of the 0-127 width — every steady note, not a 35% dice roll


def apply_hat_panning(notes: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in notes:
        note = deepcopy(entry)
        name = str(note.get("note", "")).lower()
        if "pan" in note:
            out.append(note)
            continue
        if "open" in name:
            note["pan"] = 54
        else:
            # Continuous micro-jitter on every closed/steady hit (was a 35%
            # dice roll onto a fixed +/-14 — most notes sat dead-center and
            # the ones that didn't jumped further than a "micro" spread).
            note["pan"] = max(0, min(127, 64 + rng.randint(-_HAT_PAN_JITTER_SPREAD, _HAT_PAN_JITTER_SPREAD)))
        out.append(note)
    return out


def apply_melody_pan_jitter(
    notes: list[dict[str, Any]], rng: random.Random, *, spread: int = 8
) -> list[dict[str, Any]]:
    """Tiny per-note stereo jitter (+/- ~6% of full width, pan 0..127 centered
    on 64) for melody_lead/counter_melody — without it every lead/pad note
    hits dead center, so a run of overlapping notes (chords, echoes, thickened
    octave doubles) collapses into one mono-sounding pole instead of the mild
    natural width a real multi-voice performance has. Deliberately much
    narrower than apply_hat_panning's spread (roughly +/-14) — this is subtle
    atmosphere, not a stereo-roll effect, and a lead line must still read as
    centered. Same opt-out convention as apply_hat_panning: a note that
    already carries an explicit pan (e.g. from a prior humanize step) is left
    untouched."""
    out: list[dict[str, Any]] = []
    for entry in notes:
        note = deepcopy(entry)
        if "pan" in note:
            out.append(note)
            continue
        note["pan"] = max(0, min(127, 64 + rng.randint(-spread, spread)))
        out.append(note)
    return out


def mono_legato_bass(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cut Itself: 808 notes never overlap (monophonic sub line)."""
    ordered = sorted((deepcopy(n) for n in notes), key=_note_step)
    for i in range(len(ordered) - 1):
        current = ordered[i]
        nxt = ordered[i + 1]
        end = _note_end(current)
        next_start = _note_step(nxt)
        if next_start < end:
            current["length"] = max(0.05, round(next_start - _note_step(current), 5))
    return ordered


def apply_sub_808_glide(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Real FL portamento between every root change on the sub line (2026-07,
    GenreProfile.sub_808_glide, opt-in). generate_sub_808 already merges
    identical-pitch consecutive notes, so every note boundary that survives
    IS a genuine root change — tagging every note but the first with "glide"
    is therefore exactly "connect every pitch change", not a guess about
    which ones matter. See flp_writer._note_struct for the actual FL flags=8
    bit this sets. Must run BEFORE apply_808_breath — that function already
    skips cutting a silence gap into glide/slide-tagged notes (a portamento
    into silence is a broken gesture), so tagging first makes the two
    features compose for free, no separate "no breath for glide" step needed."""
    if not notes:
        return notes
    ordered = sorted((deepcopy(n) for n in notes), key=_note_step)
    for note in ordered[1:]:
        note["glide"] = True
    return ordered


def avoid_melody_root_start(
    notes: list[dict[str, Any]], *, scale: str = "natural_minor"
) -> list[dict[str, Any]]:
    """Deon/Infinite: an opening note on the tonic is predictable — nudge the
    melody's FIRST note only to the scale's 5th (or 4th) degree, same register,
    if it currently lands on the root. No-op if it already doesn't. Every other
    note in the phrase is untouched — this is a single opening-note move, not
    a re-harmonization."""
    if not notes:
        return notes
    from music_theory import detect_root_pc, nearest_off_root_pc

    ordered = sorted((deepcopy(n) for n in notes), key=_note_step)
    try:
        midi = parse_note_name(str(ordered[0].get("note", "C5")))
    except ValueError:
        return ordered
    root_pc = detect_root_pc(ordered)
    if midi % 12 != root_pc % 12:
        return ordered  # already off the tonic

    target_pc = nearest_off_root_pc(root_pc, scale)
    candidates = [target_pc + 12 * octave for octave in range(11) if 0 <= target_pc + 12 * octave <= 131]
    if not candidates:
        return ordered
    new_midi = min(candidates, key=lambda m: abs(m - midi))
    ordered[0] = {**ordered[0], "note": _midi_to_name(new_midi)}
    return ordered


def apply_root_delay_tension(
    notes: list[dict[str, Any]], *, scale: str = "phrygian", delay_beats: float = 0.15
) -> list[dict[str, Any]]:
    """Dirkey Destroy Lonely: the melody's opening root note starts LATE and
    lands off the scale entirely (a genuine chromatic neighbor, not an
    in-scale degree) — tension through both timing and pitch. The opposite
    move from avoid_melody_root_start's smooth in-scale open; the two are not
    meant to run on the same genre. No-op if the first note isn't on the root.
    Only that one note moves — the rest of the phrase is untouched."""
    if not notes:
        return notes
    from music_theory import chromatic_neighbor_pc, detect_root_pc

    ordered = sorted((deepcopy(n) for n in notes), key=_note_step)
    first = ordered[0]
    try:
        midi = parse_note_name(str(first.get("note", "C5")))
    except ValueError:
        return ordered
    root_pc = detect_root_pc(ordered)
    if midi % 12 != root_pc % 12:
        return ordered  # already off the tonic

    tension_pc = chromatic_neighbor_pc(root_pc, scale)
    candidates = [tension_pc + 12 * octave for octave in range(11) if 0 <= tension_pc + 12 * octave <= 131]
    if not candidates:
        return ordered
    new_midi = min(candidates, key=lambda m: abs(m - midi))

    start = float(first.get("time_step", 0))
    length = float(first.get("length", 0.25))
    new_length = max(0.05, length - delay_beats)
    ordered[0] = {
        **first,
        "time_step": round(start + delay_beats, 5),
        "length": round(new_length, 5),
        "note": _midi_to_name(new_midi),
    }
    return ordered


def apply_voice_leading_direction(
    melody_notes: list[dict[str, Any]],
    counter_notes: list[dict[str, Any]],
    *,
    phrase_bars: float = 2.0,
) -> list[dict[str, Any]]:
    """Richie Souf ("New Atlanta Sound"): counter_melody should move the SAME
    direction as melody_lead, not against it — contrary motion between two
    simultaneous lines reads as a clash, not counterpoint.

    Per phrase window (``phrase_bars``), compares each line's net pitch
    direction (last note vs first note within the window). If they conflict,
    counter_melody's notes in that window are mirrored around counter's own
    first note in the window, so its direction matches melody_lead's — a
    contour flip, not a rewrite. melody_lead is never touched; it's the
    anchor the counter line follows. Windows with fewer than 2 notes in
    either line have no defined direction and are left alone."""
    if not melody_notes or not counter_notes:
        return counter_notes
    window_beats = phrase_bars * BEATS_PER_BAR
    mel_sorted = sorted(melody_notes, key=_note_step)
    cnt_sorted = sorted((deepcopy(n) for n in counter_notes), key=_note_step)
    end_beat = max(_note_end(n) for n in mel_sorted + cnt_sorted)
    windows = int(end_beat // window_beats) + 1

    for w in range(windows):
        lo = w * window_beats
        hi = lo + window_beats
        mel_window = [n for n in mel_sorted if lo <= _note_step(n) < hi]
        cnt_window = [n for n in cnt_sorted if lo <= _note_step(n) < hi]
        if len(mel_window) < 2 or len(cnt_window) < 2:
            continue
        try:
            mel_dir = parse_note_name(str(mel_window[-1]["note"])) - parse_note_name(str(mel_window[0]["note"]))
            cnt_first = parse_note_name(str(cnt_window[0]["note"]))
            cnt_dir = parse_note_name(str(cnt_window[-1]["note"])) - cnt_first
        except ValueError:
            continue
        if mel_dir == 0 or cnt_dir == 0:
            continue
        if (mel_dir > 0) == (cnt_dir > 0):
            continue  # already agree

        for note in cnt_window:
            try:
                midi = parse_note_name(str(note["note"]))
            except ValueError:
                continue
            mirrored = max(0, min(131, 2 * cnt_first - midi))
            note["note"] = _midi_to_name(mirrored)

    return cnt_sorted


# Nettspend: melody_lead and counter_melody get independently octave-wrapped
# into the SAME MELODY_REGISTER band by normalize_registers (no cross-lane
# awareness), so two simultaneous notes can land at the same pitch class or a
# semitone apart — mush, not harmony. Detection and resolution are kept as
# two separate functions (not one): detection is pure analysis (no
# mutation), resolution is a policy decision (which note yields, re-verified
# against every note it now overlaps) — conflating them makes both harder to
# test and harder to reason about when a fix doesn't fully resolve.
COLLISION_INTERVAL_SEMITONES = 1  # unison + minor 2nd read as mush; major 2nd+ is acceptable spacing
COUNTER_COLLISION_REGISTER = (48, 95)  # MELODY_REGISTER (60, 83) +/-12 — room to shift without leaving a sane range


def detect_register_collisions(
    melody_notes: list[dict[str, Any]],
    counter_notes: list[dict[str, Any]],
    *,
    max_interval: int = COLLISION_INTERVAL_SEMITONES,
) -> list[tuple[int, int]]:
    """Pure analysis, no mutation. Returns (counter_index, melody_index) pairs
    where the two notes' time windows STRICTLY overlap (touching/adjacent
    boundaries — a clean line hand-off — are NOT a collision) and the pitch
    difference is <= max_interval semitones."""
    collisions: list[tuple[int, int]] = []
    for ci, cnote in enumerate(counter_notes):
        try:
            c_start = _note_step(cnote)
            c_end = _note_end(cnote)
            c_pitch = parse_note_name(str(cnote["note"]))
        except (ValueError, KeyError):
            continue
        for mi, mnote in enumerate(melody_notes):
            try:
                m_start = _note_step(mnote)
                m_end = _note_end(mnote)
                m_pitch = parse_note_name(str(mnote["note"]))
            except (ValueError, KeyError):
                continue
            if not (c_start < m_end and m_start < c_end):
                continue  # no time overlap
            if abs(c_pitch - m_pitch) <= max_interval:
                collisions.append((ci, mi))
    return collisions


def resolve_register_collisions(
    melody_notes: list[dict[str, Any]],
    counter_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """melody_lead is the anchor and is NEVER touched (same priority
    convention as apply_voice_leading_direction). Each colliding counter note
    tries an octave shift — whichever direction keeps it inside
    COUNTER_COLLISION_REGISTER — then is RE-CHECKED against every melody note
    it now overlaps, not just the one that triggered detection; a shift that
    trades one collision for another is rejected in favor of the other
    direction. If neither direction clears every overlap, the note is left
    as-is (v1 has no scale-aware pitch-nudge fallback). Returns a new
    counter_notes list."""
    out = [deepcopy(n) for n in counter_notes]
    collisions = detect_register_collisions(melody_notes, out)
    colliding_indices = sorted({ci for ci, _ in collisions})
    lo, hi = COUNTER_COLLISION_REGISTER

    for ci in colliding_indices:
        note = out[ci]
        try:
            pitch = parse_note_name(str(note["note"]))
        except ValueError:
            continue

        def _overlap_count(candidate_pitch: int) -> int:
            trial = dict(note)
            trial["note"] = _midi_to_name(candidate_pitch)
            return len(detect_register_collisions(melody_notes, [trial]))

        candidates = [p for p in (pitch + 12, pitch - 12) if lo <= p <= hi]
        best_pitch = None
        best_overlaps = _overlap_count(pitch)
        for candidate in candidates:
            overlaps = _overlap_count(candidate)
            if overlaps < best_overlaps:
                best_overlaps = overlaps
                best_pitch = candidate
            if best_overlaps == 0:
                break
        if best_pitch is not None:
            note["note"] = _midi_to_name(best_pitch)
        # else: neither direction helps — leave the note as-is (logged by the caller)

    return out


def apply_register_collision_avoidance(data: dict[str, Any]) -> int:
    """Nettspend: no two melodic lines share the same pitch space at the same
    time. Thin wrapper matching every other opt-in lane's single-call-site
    convention — pulls melody_lead/counter_melody, runs detect -> resolve,
    writes counter_melody back. Returns the number of notes actually shifted."""
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return 0
    melody = tracks.get("melody_lead")
    counter = tracks.get("counter_melody")
    if not isinstance(melody, list) or not melody or not isinstance(counter, list) or not counter:
        return 0

    resolved = resolve_register_collisions(melody, counter)
    shifted = sum(
        1 for before, after in zip(counter, resolved) if before.get("note") != after.get("note")
    )
    if shifted:
        tracks["counter_melody"] = resolved
    return shifted


def apply_melody_upward_bias(
    notes: list[dict[str, Any]], *, phrase_bars: float = 2.0
) -> list[dict[str, Any]]:
    """Nettspend ("Early Life Crisis"): "melodies are playful, fun, but
    they're going up" — a fixed directional preference, not neutral/random.
    Per phrase window, if the melody's net direction (last note vs first) is
    flat or descending, mirror the phrase around its own first note (same
    math as apply_voice_leading_direction, biased toward a fixed direction
    instead of matching another line). Mirroring a genuinely FLAT phrase
    leaves it flat by definition, so that case gets one extra nudge: the
    window's last note moves up an octave to force a real rise."""
    if not notes:
        return notes
    window_beats = phrase_bars * BEATS_PER_BAR
    ordered = sorted((deepcopy(n) for n in notes), key=_note_step)
    end_beat = max(_note_end(n) for n in ordered)
    windows = int(end_beat // window_beats) + 1

    for w in range(windows):
        lo = w * window_beats
        hi = lo + window_beats
        window_notes = [n for n in ordered if lo <= _note_step(n) < hi]
        if len(window_notes) < 2:
            continue
        try:
            first_pc = parse_note_name(str(window_notes[0]["note"]))
            direction = parse_note_name(str(window_notes[-1]["note"])) - first_pc
        except ValueError:
            continue
        if direction > 0:
            continue  # already rises

        for note in window_notes:
            try:
                midi = parse_note_name(str(note["note"]))
            except ValueError:
                continue
            mirrored = max(0, min(131, 2 * first_pc - midi))
            note["note"] = _midi_to_name(mirrored)

        if direction == 0:
            try:
                last_midi = parse_note_name(str(window_notes[-1]["note"]))
                window_notes[-1]["note"] = _midi_to_name(min(131, last_midi + 12))
            except ValueError:
                pass

    return ordered


def _fold_verse_melody(data: dict[str, Any]) -> None:
    """Verse idea: melody notes above the register's midpoint drop an octave
    inside verse bars — the verse talks low, the hook flies high. Skips corpus
    MIDI melodies (already composed as full pieces)."""
    arrangement = data.get("plg_arrangement")
    tracks = data.get("tracks")
    if not isinstance(arrangement, list) or not isinstance(tracks, dict):
        return
    if data.get("plg_midi_melody"):
        return
    verse_ranges = [
        (int(s.get("start_bar", 0)) * BEATS_PER_BAR,
         (int(s.get("start_bar", 0)) + int(s.get("bars", 0))) * BEATS_PER_BAR)
        for s in arrangement
        if isinstance(s, dict) and str(s.get("name", "")) == "verse"
    ]
    if not verse_ranges:
        return
    notes = tracks.get("melody_lead")
    if not isinstance(notes, list):
        return
    folded = 0
    for note in notes:
        try:
            step = float(note.get("time_step", 0))
            midi = parse_note_name(str(note.get("note", "")))
        except (TypeError, ValueError):
            continue
        if midi >= 72 and any(lo <= step < hi for lo, hi in verse_ranges):
            # strictly into the register's LOWER octave (60..71): the later
            # normalize pass clamps the chorus to <=83, so a single -12 could
            # still tie the verse to the ceiling and erase the contrast
            note["note"] = _midi_to_name(60 + ((midi - 60) % 12))
            folded += 1
    if folded:
        logger.info("Verse idea: %d melody notes folded down an octave", folded)


def humanize_melody_dynamics(
    notes: list[dict[str, Any]], rng: random.Random, *, loose: bool = False
) -> list[dict[str, Any]]:
    """Real performance dynamics: wide velocity spread with DELIBERATE quiet
    notes (not just noise), and a micro push off the grid so no note sits at
    machine-perfect time. Corpus MIDI notes keep their human timing.

    ``loose=True`` = the Ken Carson "leave it unquantized" lead: the drift is
    2-3x wider, audibly crooked on purpose."""
    lo, hi = (-0.025, 0.05) if loose else (-0.01, 0.018)
    out: list[dict[str, Any]] = []
    for entry in notes:
        note = dict(entry)
        vel = int(note.get("velocity", 95)) + rng.randint(-12, 10)
        if rng.random() < 0.12:
            vel = int(vel * 0.72)  # the intentionally-soft note that makes it human
        note["velocity"] = max(35, min(127, vel))
        if not note.get("midi"):
            note["time_step"] = round(
                max(0.0, float(note.get("time_step", 0)) + rng.uniform(lo, hi)), 5
            )
        out.append(note)
    return out


def densify_hats_one_step(
    hats: list[dict[str, Any]], data: dict[str, Any], rng: random.Random
) -> list[dict[str, Any]]:
    """Ken/rage hats: "one-step, every single slot filled" in hook/chorus bars.

    Existing accents/rolls stay; every empty 16th slot gets a machine tick in a
    velocity wave. Verses keep the arranger's sparser grid (section contrast)."""
    from style_family import genre_of, is_ken

    if not is_ken(genre_of(data)):
        return hats
    arrangement = data.get("plg_arrangement")
    if not isinstance(arrangement, list) or not hats:
        return hats
    dense_ranges = [
        (int(s.get("start_bar", 0)) * BEATS_PER_BAR,
         (int(s.get("start_bar", 0)) + int(s.get("bars", 0))) * BEATS_PER_BAR)
        for s in arrangement
        if isinstance(s, dict) and str(s.get("name", "")) in ("hook", "chorus", "drop")
    ]
    if not dense_ranges:
        return hats

    taken = {round(_note_step(n) * 4) / 4 for n in hats}
    out = [deepcopy(n) for n in hats]
    added = 0
    for lo, hi in dense_ranges:
        slot = lo
        while slot < hi - 1e-6:
            key = round(slot * 4) / 4
            if key not in taken:
                # velocity wave: crest into the bar end, mechanical but alive
                pos = (slot % BEATS_PER_BAR) / BEATS_PER_BAR
                out.append({
                    "time_step": round(slot, 5),
                    "note": "D5" if added % 3 == 2 else "C5",
                    "length": 0.08,
                    "velocity": int(62 + 22 * pos + rng.randint(-4, 4)),
                })
                taken.add(key)
                added += 1
            slot += 0.25
    if added:
        logger.info("Ken hats: %d one-step slots filled in hooks", added)
    return sorted(out, key=_note_step)


# «ТУК-УУУМ… тишина»: the pocket breathes because the 808 SHUTS UP before the
# next hit. Wall-to-wall sustains (note ends exactly where the next starts)
# read as a train-horn drone; the micro-silence is what makes the beat pump.
_BREATH_MIN = 0.22   # beats — safely clears the 0.20-beat audible-gap floor
_BREATH_MAX = 0.5    # beats — never eat more than this from a sustain
_BREATH_FRAC = 0.12  # of the interval to the next note
_BREATH_MIN_INTERVAL = 1.0  # slides/grace notes closer than this stay legato


def add_808_ascent(notes: list[dict[str, Any]], data: dict[str, Any]) -> list[dict[str, Any]]:
    """Ascending minor-2nd walk-up into every chorus/hook/drop landing.

    The bar before the drop, the 808 climbs chromatically (root-2, root-1)
    and LANDS on the next section's root — the tension move that separates a
    loop from a record. Rides the same pre-landing bar as the snare fill and
    the beat-switch silence (bass climbing through the gap = drama).

    STYLE-GATED: this is a Travis/Don-Toliver move. Carti (opium/plugg) keeps
    its 808 on the root with a slide; Ken/rage builds structure from 808
    switch-ups, not walk-ups — both worlds skip this."""
    from style_family import genre_of, is_carti, is_kanye, is_ken, is_toliver

    arrangement = data.get("plg_arrangement")
    if not isinstance(arrangement, list) or not notes:
        return notes
    genre = genre_of(data)
    if is_carti(genre) or is_ken(genre) or is_toliver(genre) or is_kanye(genre):
        # Toliver floats, Kanye's 808 sits slow & gritty on the root — no climbs
        return notes

    out = sorted((deepcopy(n) for n in notes), key=_note_step)
    added: list[dict[str, Any]] = []
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        if str(section.get("name", "")) not in ("chorus", "hook", "drop"):
            continue
        landing_bar = int(section.get("start_bar", 0))
        if landing_bar <= 0:
            continue
        landing_beat = landing_bar * BEATS_PER_BAR
        target = next((n for n in out if _note_step(n) >= landing_beat - 0.1), None)
        if target is None:
            continue
        try:
            root_midi = parse_note_name(str(target.get("note", "C2")))
        except ValueError:
            continue
        base_vel = int(target.get("velocity", 118))
        for step_back, semitones, length in ((1.0, -2, 0.45), (0.5, -1, 0.4)):
            added.append({
                "time_step": round(landing_beat - step_back, 5),
                "note": _midi_to_name(max(24, root_midi + semitones)),
                "length": length,
                "velocity": max(1, min(127, base_vel - 4)),
                "ascent": True,
            })
    if not added:
        return out
    # replace anything already sitting on the walk-up slots, then re-mono
    slots = {round(n["time_step"], 2) for n in added}
    kept = [n for n in out if round(_note_step(n), 2) not in slots]
    return mono_legato_bass(sorted(kept + added, key=_note_step))


def apply_808_register_lift(notes: list[dict[str, Any]], data: dict[str, Any]) -> list[dict[str, Any]]:
    """One-note +12 semitone accent right at a hook/chorus/drop's landing bar
    — the "screaming" sub-808 rise many producers use as a hook-entry punch
    (2026-07, GenreProfile.sub_808_register_lift, opt-in). Deliberately only
    the SINGLE note landing at the section start moves — the sub's "felt not
    heard" identity has to survive for the rest of the section, this is a
    one-note attention grab, not a register change held through the whole
    hook. A different device from add_808_ascent's walk-up (a stepped
    chromatic climb INTO the landing, played as separate quick stabs) — this
    is the landing note itself jumping register, so the two can coexist."""
    arrangement = data.get("plg_arrangement")
    if not isinstance(arrangement, list) or not notes:
        return notes
    landing_beats = {
        int(section["start_bar"]) * BEATS_PER_BAR
        for section in arrangement
        if isinstance(section, dict) and str(section.get("name", "")) in ("hook", "chorus", "drop")
    }
    if not landing_beats:
        return notes
    out = sorted((deepcopy(n) for n in notes), key=_note_step)
    for note in out:
        step = _note_step(note)
        if any(abs(step - lb) < 0.1 for lb in landing_beats):
            try:
                midi = parse_note_name(str(note.get("note", "C2")))
            except ValueError:
                continue
            note["note"] = _midi_to_name(min(96, midi + 12))
            note["register_lift"] = True
    return out


def apply_808_section_switch(
    notes: list[dict[str, Any]], data: dict[str, Any]
) -> list[dict[str, Any]]:
    """Ken/Carti structure move: TWO 808 characters alternating by section.

    Verse = short "bow" stabs (staccato, punchy); hook/chorus = the long
    distorted sustains. Same channel, two personalities — the switch-up IS the
    arrangement ("you really thought you'd get a video without two 808s?")."""
    from style_family import genre_of, is_carti, is_ken

    genre = genre_of(data)
    if not (is_ken(genre) or is_carti(genre)):
        return notes
    arrangement = data.get("plg_arrangement")
    if not isinstance(arrangement, list) or not notes:
        return notes

    bow_ranges = [
        (int(s.get("start_bar", 0)) * BEATS_PER_BAR,
         (int(s.get("start_bar", 0)) + int(s.get("bars", 0))) * BEATS_PER_BAR)
        for s in arrangement
        if isinstance(s, dict) and str(s.get("name", "")) in ("verse", "intro")
    ]
    if not bow_ranges:
        return notes

    out: list[dict[str, Any]] = []
    switched = 0
    for entry in notes:
        note = deepcopy(entry)
        step = _note_step(note)
        if (
            not note.get("slide") and not note.get("glide") and not note.get("ascent")
            and any(lo <= step < hi for lo, hi in bow_ranges)
            and float(note.get("length", 0)) > 0.6
        ):
            note["length"] = 0.5  # the short "bow" 808
            switched += 1
        out.append(note)
    if switched:
        logger.info("808 switch-up: %d bow stabs in verse/intro", switched)
    return out


def apply_808_breath(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim each 808 sustain to leave a gap of silence before the next hit.

    Runs AFTER mono_legato_bass: legato guarantees no overlap; breath turns the
    butt-joint into a pocket. Notes marked ``glide``/``slide`` keep their tails
    (a glide into silence is a broken gesture), and the LAST note of the line
    rings out untouched.

    Checks BOTH sides of each gap (2026-07): FL's slide flag lives on the
    DESTINATION note (apply_sub_808_glide tags the note being glided INTO,
    matching flp_writer._note_struct's flags=8 — the note that arrives via
    portamento, not the one it came from), so a gap before a glide-tagged
    note must be suppressed too, not just a gap after a slide-tagged note's
    own tail (the pre-existing add_808_bar_slides case this originally
    guarded). Checking only ``current`` left an off-by-one: the silence
    right before the FIRST glide-destination note in a run was still being
    cut, breaking the exact portamento it was supposed to protect.
    """
    ordered = sorted((deepcopy(n) for n in notes), key=_note_step)
    for i in range(len(ordered) - 1):
        current = ordered[i]
        nxt = ordered[i + 1]
        # A glide's destination may still follow an ordinary sustained note.
        # Preserve only tails that themselves glide; otherwise the preceding
        # note must breathe before the new attack.
        if current.get("glide") or current.get("slide"):
            continue
        start = _note_step(current)
        interval = _note_step(nxt) - start
        if interval < _BREATH_MIN_INTERVAL:
            continue
        breath = min(_BREATH_MAX, max(_BREATH_MIN, interval * _BREATH_FRAC))
        max_len = round(interval - breath, 5)
        if float(current.get("length", 0)) > max_len:
            current["length"] = max(0.05, max_len)
    return ordered


def add_808_bar_slides(notes: list[dict[str, Any]], *, bars: int = 8) -> list[dict[str, Any]]:
    """High-register slide stabs at end of each 4-bar phrase.

    2026-07 audit, two compounding bugs found from a real generation's piano
    roll (not just the interval): (1) +24 semitones (two octaves) landed
    these consistently at the 84 (C7) clamp, stranded a full octave+ above
    even the widest walk pool. (2) ``root = out[-1]`` picked the *last list
    element in append order*, not the walk note immediately preceding the
    slide — after phrase 1 that's the slide note itself, so phrase 2's jump
    compounded off phrase 1's slide (+12, then +24, then hit the clamp
    anyway), regardless of the interval fix. Now anchors to the nearest
    actual walk note before each slide point, so every phrase jumps the same
    +12 from ITS OWN local root, not a runaway chain."""
    walk = sorted((deepcopy(n) for n in notes), key=_note_step)
    if not walk:
        return walk
    out = list(walk)

    phrase_count = max(1, int(bars // 4))
    for phrase in range(1, phrase_count + 1):
        slide_at = phrase * 4 * BEATS_PER_BAR - 0.25
        prior = [n for n in walk if _note_step(n) < slide_at]
        root = prior[-1] if prior else walk[-1]
        try:
            root_midi = parse_note_name(str(root.get("note", "C2")))
        except ValueError:
            root_midi = 36
        # The ONE sanctioned exception to the C4-C5 working window: a short
        # stab up to C6/C#6 that immediately drops back down. Anything in
        # between would just be an out-of-window bass note.
        from bass808_gen import SLIDE_HI, SLIDE_LO

        high_midi = max(SLIDE_LO, min(SLIDE_HI, root_midi + 24))
        out.append({
            "time_step": round(slide_at, 5),
            "note": _midi_to_name(high_midi),
            "length": 0.125,
            "velocity": 118,
            "slide": True,
        })
    return sorted(out, key=_note_step)


def phase_align_kick_808(
    kick_notes: list[dict[str, Any]],
    bass_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """808 starts slightly after kick transients.

    Snaps onto the NEAREST matching kick's own time_step (not "this note's
    current position + delay") so the function is safe to re-run after
    something else has moved the kick track — see the second call in
    humanize_pattern, right after apply_melody_beat_symbiosis's
    lock_drums_to_melody_accents nudges kicks onto melody accents. Without
    re-anchoring to the kick's NEW position there, 808_bass stayed glued to
    where the kick USED to be, so the two drifted apart (measured ~9ms, kick
    ahead of 808 instead of the intended 808-after-kick pocket — a real
    phase-cancellation zone for kick/808 pairs sharing the same low band)."""
    kick_steps = sorted({_note_step(n) for n in kick_notes})
    out: list[dict[str, Any]] = []
    for entry in bass_notes:
        note = deepcopy(entry)
        step = _note_step(note)
        nearest = min(kick_steps, key=lambda ks: abs(ks - step)) if kick_steps else None
        if nearest is not None and abs(step - nearest) < 0.05:
            note["time_step"] = round(nearest + PHASE_DELAY_BEATS, 5)
        out.append(note)
    return sorted(out, key=_note_step)


_DUCK_ONSET_SKIP_BEATS = 0.1   # a kick this close to the note's own onset IS the attack, not a mid-sustain hit
_DUCK_GAP_BEATS = 0.05         # true silence right on the kick — a hard notch, not a soft dip
_DUCK_RECOVER_BEATS = 0.6      # matches sidechain_curve's bezier recovery time — same broadband feel, baked
# Rich Amiri sidechain: 808 ducks 3–6 dB under the kick (remaining ~60% ≈ −4.4 dB).
# Shallower 80% remaining left the low end mushy when kick+808 hit together.
_DUCK_REMAINING = 0.60
_DUCK_VELOCITY_DROP = 26       # legacy absolute drop — only used when callers pass velocity_drop=


def duck_808_under_kick(
    kick_notes: list[dict[str, Any]],
    sub_notes: list[dict[str, Any]],
    *,
    gap_beats: float = _DUCK_GAP_BEATS,
    recover_beats: float = _DUCK_RECOVER_BEATS,
    velocity_drop: int | None = None,
    remaining: float = _DUCK_REMAINING,
) -> list[dict[str, Any]]:
    """Broadband kick -> 808 duck, baked as real note splits — no automation clip.

    A sustained 808 note that a kick lands INSIDE (not at its own onset, which
    is the attack transient phase_align_kick_808 already owns) gets cut at the
    kick and re-triggered a beat-fraction later at reduced velocity (~30% duck
    by default). Every sub_808 channel already carries an instant-attack/
    instant-release volume envelope (the note-off gate — see _ENV_GATE_SLOT1/2
    in flp_writer.py), so the gap and re-trigger play as clean silence + a
    fresh hit, not a cross-fade glitch. Notes tagged ``glide``/``slide`` are
    left alone (a duck mid-glide is a broken gesture, same rule as apply_808_breath).
    """
    kicks = sorted({round(float(k.get("time_step", 0)), 4) for k in kick_notes or [] if isinstance(k, dict)})
    if not kicks or not sub_notes:
        return sub_notes

    out: list[dict[str, Any]] = []
    for note in sub_notes:
        if not isinstance(note, dict) or note.get("glide") or note.get("slide"):
            out.append(note)
            continue
        start = float(note.get("time_step", 0))
        length = float(note.get("length", 0))
        end = start + length
        base_velocity = int(note.get("velocity", 100))
        if velocity_drop is not None:
            duck_velocity = max(1, base_velocity - int(velocity_drop))
        else:
            duck_velocity = max(1, int(round(base_velocity * float(remaining))))
        hits = [k for k in kicks if start + _DUCK_ONSET_SKIP_BEATS < k < end]
        if not hits:
            out.append(note)
            continue

        cursor = start
        cursor_velocity = base_velocity
        for k in hits:
            if k - cursor > 1e-4:
                seg = dict(note)
                seg["time_step"] = round(cursor, 4)
                seg["length"] = round(k - cursor, 4)
                seg["velocity"] = cursor_velocity
                out.append(seg)
            duck_start = k + gap_beats
            if duck_start >= end:
                cursor = end
                break
            duck_end = min(duck_start + recover_beats, end)
            seg = dict(note)
            seg["time_step"] = round(duck_start, 4)
            seg["length"] = round(duck_end - duck_start, 4)
            seg["velocity"] = duck_velocity
            out.append(seg)
            cursor = duck_end
            cursor_velocity = base_velocity
        if end - cursor > 1e-4:
            seg = dict(note)
            seg["time_step"] = round(cursor, 4)
            seg["length"] = round(end - cursor, 4)
            seg["velocity"] = cursor_velocity
            out.append(seg)
    return sorted(out, key=_note_step)


def align_808_to_key(
    bass_notes: list[dict[str, Any]],
    melody_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rule 18 — lock the 808 to the melody's key.

    Transposes the whole 808 line by the nearest interval (-6..+5 semitones)
    mapping its detected root onto the melody root, so the sub never clashes
    with the chords. No-op when the keys already agree or melody is empty.
    """
    if not bass_notes or not melody_notes:
        return bass_notes
    mel_root = detect_root_pc(melody_notes)
    bass_root = detect_root_pc(bass_notes)
    delta = (mel_root - bass_root) % 12
    if delta > 6:
        delta -= 12
    if delta == 0:
        return bass_notes
    out: list[dict[str, Any]] = []
    for entry in bass_notes:
        note = deepcopy(entry)
        try:
            midi = parse_note_name(str(note.get("note", "C2")))
            note["note"] = _midi_to_name(midi + delta)
            note["key_matched"] = True
        except ValueError:
            pass
        out.append(note)
    return out


def walk_bass_to_progression(
    bass_notes: list[dict[str, Any]],
    roots_pc: list[int],
) -> list[dict[str, Any]]:
    """Transpose each bar of a (non-808) bassline onto the pinned progression.

    The line's internal rhythm and intervals stay; only the bar's tonal center
    moves, so an LLM bassline gets real harmonic motion instead of one root.
    """
    if not bass_notes or not roots_pc:
        return bass_notes
    by_bar: dict[int, list[dict[str, Any]]] = {}
    for entry in bass_notes:
        by_bar.setdefault(int(_note_step(entry) // BEATS_PER_BAR), []).append(entry)
    out: list[dict[str, Any]] = []
    for bar, notes in sorted(by_bar.items()):
        target = int(roots_pc[bar % len(roots_pc)]) % 12
        floors: list[int] = []
        for entry in notes:
            try:
                floors.append(parse_note_name(str(entry.get("note", "C2"))))
            except ValueError:
                continue
        if not floors:
            out.extend(deepcopy(n) for n in notes)
            continue
        delta = (target - (min(floors) % 12)) % 12
        if delta > 6:
            delta -= 12
        for entry in notes:
            note = deepcopy(entry)
            if delta:
                try:
                    midi = parse_note_name(str(note.get("note", "C2")))
                    note["note"] = _midi_to_name(midi + delta)
                    note["bass_walk"] = True
                except ValueError:
                    pass
            out.append(note)
    return sorted(out, key=_note_step)


def apply_drop_tension(
    pattern: dict[str, Any],
    *,
    phrase_bars: int = 4,
) -> None:
    """Mute kick + 808 for half a beat before each phrase drop."""
    tracks = pattern.get("tracks")
    if not isinstance(tracks, dict):
        return

    phrase_len = phrase_bars * BEATS_PER_BAR
    max_step = 0.0
    for key in TRACK_KEYS:
        for entry in track_notes(pattern, key):
            max_step = max(max_step, _note_end(entry))

    phrases = max(1, int(max_step // phrase_len) + 1)
    gaps: list[tuple[float, float]] = []
    for p in range(1, phrases + 1):
        start = p * phrase_len - (BEATS_PER_BAR - DROP_GAP_START)
        gaps.append((start, start + 0.5))

    for key in ("kick", "sub_808"):
        raw = tracks.get(key)
        if not isinstance(raw, list):
            continue
        kept: list[dict[str, Any]] = []
        for entry in raw:
            step = _note_step(entry)
            if any(start <= step < end for start, end in gaps):
                continue
            kept.append(entry)
        tracks[key] = kept


def enforce_midi_section_density(pattern: dict[str, Any]) -> None:
    """Apply final lane gates without erasing a fast melody's hat pulse."""
    if not pattern.get("plg_midi_melody"):
        return
    tracks = pattern.get("tracks")
    arrangement = pattern.get("plg_arrangement")
    if not isinstance(tracks, dict) or not isinstance(arrangement, list):
        return

    windows: list[tuple[float, float, set[str]]] = []
    for section in arrangement:
        if not isinstance(section, dict) or not isinstance(section.get("active_tracks"), list):
            continue
        start = float(section.get("start_bar", 0)) * BEATS_PER_BAR
        end = start + float(section.get("bars", 0)) * BEATS_PER_BAR
        windows.append((start, end, {str(key) for key in section["active_tracks"]}))
    for lane in ("kick", "sub_808", "snare", "snare_layer", "clap", "hi_hats"):
        notes = track_notes(pattern, lane)
        if not notes:
            continue
        tracks[lane] = [
            note
            for note in notes
            if not any(
                start <= _note_step(note) < end and lane not in active
                for start, end, active in windows
            )
        ]

    if pattern.get("plg_midi_hats"):
        return

    energy = pattern.get("plg_melody_energy")
    energy = energy if isinstance(energy, dict) else {}
    pace = str(energy.get("pace") or "pocket")
    attack = str(energy.get("attack") or "neutral")
    busyness = float(energy.get("busyness", 0.0) or 0.0)
    racing = pace == "racing" or (attack == "sharp" and busyness >= 0.65)

    # A racing/sharp uploaded melody needs a stable audible clock in verses.
    # Rebuild only missing 1/8 skeleton hits; MIDI-owned hat lanes are sacred.
    # Floor AND ceiling below both used to be the literal 8 — the 1/8 grid —
    # gated only on `racing`, which measures true on every corpus file. That
    # is why the final hat lane sat at 8-9 per bar whatever the melody did,
    # and why thinning it earlier in the chain was simply refilled here. Both
    # now read the material's own transient budget.
    from drum_density import density_card, hat_floor_per_bar, late_hat_velocity

    hat_target = hat_floor_per_bar(density_card(pattern))

    hats = track_notes(pattern, "hi_hats")
    if any(note.get("hat_cell") for note in hats):
        # A library line (hat_patterns) has its own air and is calm on purpose: refilling every bar to the full
        # eighth skeleton put the straight line back under it. A bar below the calm minimum still gets its clock.
        hat_target = min(hat_target, HAT_CELL_BAR_MINIMUM)
    if hats and racing and not pattern.get("plg_midi_hats"):
        rebuilt = [deepcopy(note) for note in hats]
        existing = {round(_note_step(note), 3) for note in rebuilt}
        # This rebuild runs at stage 18, EIGHT stages after the lane's only
        # leveller, so a literal here ships un-levelled: measured across the
        # five-file corpus these 33-121 notes per file carried a median of
        # 85-96 against lane medians of 38-45, and none of them had ever seen
        # apply_six_db_hat_rule (the project issue log (not included) 0v.8). The 84/76 alternation is
        # this writer's own and it survives — only the level it is measured
        # against changes, from a constant to the lane.
        skeleton_vel = (
            late_hat_velocity(hats, 84, peak=84),
            late_hat_velocity(hats, 76, peak=84),
        )
        per_bar_now: dict[int, int] = {}
        for note in rebuilt:
            per_bar_now[int(_note_step(note) // BEATS_PER_BAR)] = (
                per_bar_now.get(int(_note_step(note) // BEATS_PER_BAR), 0) + 1
            )
        for section in arrangement:
            if (
                not isinstance(section, dict)
                or section.get("name") not in ("verse", "hook", "chorus")
            ):
                continue
            active = section.get("active_tracks")
            if isinstance(active, list) and "hi_hats" not in active:
                continue
            start = float(section.get("start_bar", 0) or 0) * BEATS_PER_BAR
            end = start + float(section.get("bars", 0) or 0) * BEATS_PER_BAR
            t = start
            index = 0
            while t < end - 1e-6:
                key = round(t, 3)
                bar_here = int(t // BEATS_PER_BAR)
                if per_bar_now.get(bar_here, 0) >= hat_target:
                    # This bar already carries its whole budget — the skeleton
                    # is a floor for material that leaves room, not a grid.
                    t += 0.5
                    index += 1
                    continue
                if not any(abs(key - hit) <= 0.08 for hit in existing):
                    per_bar_now[bar_here] = per_bar_now.get(bar_here, 0) + 1
                    rebuilt.append(
                        {
                            "time_step": round(t, 5),
                            "note": "C5" if index % 4 != 3 else "D5",
                            "length": 0.12,
                            "velocity": skeleton_vel[0] if index % 2 == 0 else skeleton_vel[1],
                            "steady_hat": True,
                            "melody_hat_floor": True,
                            "section": str(section.get("name") or "verse"),
                        }
                    )
                    existing.add(key)
                t += 0.5
                index += 1
        tracks["hi_hats"] = sorted(rebuilt, key=_note_step)

    # Keep contrast, but make the cap context-aware.  Slow/lush verses can
    # breathe at four hits; sharp/racing verses retain the 1/8 skeleton.
    # Outro stays reduced, while hook/drop keep their full roll grid.
    hats = track_notes(pattern, "hi_hats")
    if hats:
        sparse_hats: list[dict[str, Any]] = []
        handled: set[int] = set()
        for section in arrangement:
            if not isinstance(section, dict) or section.get("name") not in ("verse", "outro"):
                continue
            start_bar = int(section.get("start_bar", 0))
            end_bar = start_bar + int(section.get("bars", 0))
            for bar in range(start_bar, end_bar):
                lo = bar * BEATS_PER_BAR
                hi = lo + BEATS_PER_BAR
                indexed = [
                    (index, note)
                    for index, note in enumerate(hats)
                    if lo <= _note_step(note) < hi
                ]
                if not indexed:
                    continue
                section_name = str(section.get("name") or "")
                limit = 8 if racing and section_name == "verse" else 4
                if racing and section_name == "outro":
                    limit = 6
                # The budget may only make this stricter, never looser.
                limit = max(1, min(limit, hat_target))
                if len(indexed) <= limit:
                    selected = indexed
                elif limit == 1:
                    selected = [indexed[0]]
                else:
                    selected_indices = {
                        round(i * (len(indexed) - 1) / (limit - 1))
                        for i in range(limit)
                    }
                    selected = [indexed[i] for i in sorted(selected_indices)]
                sparse_hats.extend(note for _index, note in selected)
                handled.update(index for index, _note in indexed)
        sparse_hats.extend(
            note for index, note in enumerate(hats) if index not in handled
        )
        tracks["hi_hats"] = sorted(sparse_hats, key=_note_step)


def apply_midi_arrangement_transitions(pattern: dict[str, Any]) -> None:
    """Print a small set of musical transitions into MIDI-owned beats.

    No synthesized risers, impacts, reverse chops, or extra FX lanes are used:
    the hook gets one beat of kick breathing plus a short snare/hat pickup, and
    the drop gets a clean half-bar rhythm-section stop before the full grid
    returns on its first beat.
    """
    if not pattern.get("plg_midi_melody"):
        return
    tracks = pattern.get("tracks")
    plans = pattern.get("plg_midi_transitions")
    if not isinstance(tracks, dict) or not isinstance(plans, list):
        return

    enforce_midi_section_density(pattern)

    def _clear(lanes: tuple[str, ...], start: float, end: float) -> int:
        removed = 0
        for lane in lanes:
            notes = track_notes(pattern, lane)
            if not notes:
                continue
            kept = [note for note in notes if not (start <= _note_step(note) < end)]
            removed += len(notes) - len(kept)
            tracks[lane] = kept
        return removed

    def _fill(lane: str, landing: float, offsets: tuple[float, ...], target: str) -> int:
        notes = track_notes(pattern, lane)
        if not notes:
            return 0
        template = next(
            (note for note in reversed(notes) if _note_step(note) < landing),
            notes[0],
        )
        velocities = (72, 92, 112)
        added: list[dict[str, Any]] = []
        occupied = {round(_note_step(note), 4) for note in notes}
        for index, offset in enumerate(offsets):
            step = round(landing + offset, 4)
            if step < 0 or round(step, 4) in occupied:
                continue
            note = deepcopy(template)
            note["time_step"] = step
            note["length"] = 0.12 if lane == "hi_hats" else 0.18
            _set_velocity(note, velocities[min(index, len(velocities) - 1)])
            note["fill"] = True
            note["midi_transition"] = "%s_pickup" % target
            added.append(note)
            occupied.add(round(step, 4))
        if added:
            tracks[lane] = sorted(notes + added, key=_note_step)
        return len(added)

    report: list[dict[str, Any]] = []
    for raw in plans:
        if not isinstance(raw, dict):
            continue
        try:
            landing = float(raw["landing_bar"]) * BEATS_PER_BAR
        except (KeyError, TypeError, ValueError):
            continue
        kind = str(raw.get("kind") or "")
        # The landing's own section name, not a literal: since 2026-08-26 every
        # section landing gets a transition, so a report row hardcoded to
        # "hook"/"drop" would mislabel the verse and outro rows.
        target = str(raw.get("target") or "")
        if kind == "kick_breath_fill":
            removed = _clear(("kick", "sub_808"), landing - 1.0, landing)
            fills = _fill("snare", landing, (-1.0, -0.5, -0.25), target)
            fills += _fill("hi_hats", landing, (-0.75, -0.5, -0.25), target)
            report.append(
                {
                    "target": target or "hook",
                    "landing_bar": int(raw["landing_bar"]),
                    "kind": kind,
                    "removed": removed,
                    "fill_hits": fills,
                    "window": [round(landing - 1.0, 4), round(landing, 4)],
                }
            )
        elif kind == "half_bar_stop":
            removed = _clear(
                ("kick", "sub_808", "snare", "snare_layer", "clap", "hi_hats"),
                landing - 2.0,
                landing,
            )
            report.append(
                {
                    "target": target or "drop",
                    "landing_bar": int(raw["landing_bar"]),
                    "kind": kind,
                    "removed": removed,
                    "fill_hits": 0,
                    "window": [round(landing - 2.0, 4), round(landing, 4)],
                }
            )
    if report:
        pattern["plg_midi_transition_report"] = report


_EVENT_WINDOW_LANES: dict[str, tuple[str, ...]] = {
    "kick_breath_fill": ("kick", "808_bass", "sub_808"),
    "half_bar_stop": (
        "kick", "808_bass", "sub_808", "snare", "snare_layer", "clap", "hi_hats",
    ),
}
# The user's standing requirement: something must change at least this often,
# or the loop reads as dead. Recorded here so the pattern carries the target it
# was measured against, not just the measurement.
SECTION_EVENT_MAX_GAP_BARS = 16


def _measure_section_events(data: dict[str, Any]) -> dict[str, Any] | None:
    """Write ``plg_section_events``: where the finished song actually changes.

    Three kinds of change-point are counted, all of them audible:

    * a transition window (`plg_midi_transition_report`) — the rhythm section
      breathing or stopping before a landing;
    * the beat-switch cut before the final peak (`plg_beat_switch`);
    * the start of the outro fade (`plg_outro_fade`).

    Each transition row is RE-MEASURED against the final note lists rather than
    trusted: `survivors` counts notes still standing inside a window that was
    cleared at stage 18 and re-cleared after the pocket rebuild. A non-zero
    survivor count is a real defect (a later pass refilled a deliberate
    silence), so it belongs in the exported data where it can be grepped.
    """
    total_bars = data.get("plg_total_bars")
    if not isinstance(total_bars, int) or total_bars <= 0:
        return None

    events: list[dict[str, Any]] = []
    for row in data.get("plg_midi_transition_report") or []:
        if not isinstance(row, dict):
            continue
        window = row.get("window")
        survivors = None
        if isinstance(window, (list, tuple)) and len(window) == 2:
            start, end = float(window[0]), float(window[1])
            lanes = _EVENT_WINDOW_LANES.get(str(row.get("kind") or ""), ())
            survivors = sum(
                1
                for lane in lanes
                for note in track_notes(data, lane)
                if start <= _note_step(note) < end
            )
        # `removed_at_plan_time` is what the stage-18 clear took, and it is
        # routinely 0: the kick/808 pocket is REBUILT at stage 20, over the
        # window, and `_reapply_midi_kick_808_clears` empties it again
        # afterwards without reporting. The field that actually proves the
        # silence is `survivors_in_window`, measured here against the final
        # note lists.
        in_window = None
        if isinstance(window, (list, tuple)) and len(window) == 2:
            start, end = float(window[0]), float(window[1])
            in_window = sum(
                1
                for lane in TRACK_KEYS
                for note in track_notes(data, lane)
                if start <= _note_step(note) < end
            )
        events.append({
            "bar": int(row.get("landing_bar", 0)),
            "kind": str(row.get("kind") or ""),
            "target": str(row.get("target") or ""),
            "removed_at_plan_time": int(row.get("removed", 0) or 0),
            "fill_hits": int(row.get("fill_hits", 0) or 0),
            "survivors_in_window": survivors,
            "notes_in_window_all_lanes": in_window,
            "window_beats": [round(float(window[0]), 4), round(float(window[1]), 4)]
            if isinstance(window, (list, tuple)) and len(window) == 2 else None,
        })

    switch = data.get("plg_beat_switch")
    if isinstance(switch, dict) and switch.get("cut_bar") is not None:
        events.append({
            "bar": int(switch["cut_bar"]),
            "kind": "beat_switch_cut",
            "target": "final_peak",
            "removed_at_plan_time": int(switch.get("removed", 0) or 0),
            "fill_hits": 0,
            "survivors_in_window": None,
            "notes_in_window_all_lanes": None,
            "window_beats": None,
        })

    fade = data.get("plg_outro_fade")
    if isinstance(fade, dict) and fade.get("enabled"):
        events.append({
            "bar": int(float(fade.get("start_beat", 0.0)) // BEATS_PER_BAR),
            "kind": "outro_fade",
            "target": "outro",
            "removed_at_plan_time": 0,
            "fill_hits": int(fade.get("applied_notes", 0) or 0),
            "survivors_in_window": None,
            "notes_in_window_all_lanes": None,
            "window_beats": None,
        })

    events.sort(key=lambda e: e["bar"])
    bars = sorted({int(e["bar"]) for e in events})
    gaps = [b - a for a, b in zip([0] + bars, bars + [total_bars])]
    max_gap = max(gaps) if gaps else total_bars

    arrangement = data.get("plg_arrangement")
    sections = [
        "%s#%d@%d+%d" % (s.get("name"), s.get("occurrence", 1),
                         s.get("start_bar", 0), s.get("bars", 0))
        for s in arrangement if isinstance(s, dict)
    ] if isinstance(arrangement, list) else []

    bpm = float(data.get("bpm") or 0) or 0.0
    seconds_per_bar = (BEATS_PER_BAR * 60.0 / bpm) if bpm else 0.0
    summary = {
        "form": sections,
        "total_bars": total_bars,
        "events": events,
        "event_bars": bars,
        "gaps_bars": gaps,
        "max_gap_bars": max_gap,
        "max_gap_seconds": round(max_gap * seconds_per_bar, 2) if seconds_per_bar else None,
        "target_max_gap_bars": SECTION_EVENT_MAX_GAP_BARS,
        "meets_change_requirement": max_gap <= SECTION_EVENT_MAX_GAP_BARS,
        "windows_with_survivors": [
            e["bar"] for e in events if (e["survivors_in_window"] or 0) > 0
        ],
    }
    data["plg_section_events"] = summary
    logger.info(
        "Section events: %d change-point(s), longest gap %d bars (%s)",
        len(events), max_gap,
        "meets the 8-16 bar requirement" if summary["meets_change_requirement"]
        else "TOO LONG",
    )
    return summary


def _reapply_midi_kick_808_clears(pattern: dict[str, Any]) -> None:
    """Breath/stop windows must stay clear after the kick+808 pocket rebuild."""
    if not pattern.get("plg_midi_melody"):
        return
    tracks = pattern.get("tracks")
    plans = pattern.get("plg_midi_transitions")
    if not isinstance(tracks, dict) or not isinstance(plans, list):
        return

    def _clear(lanes: tuple[str, ...], start: float, end: float) -> None:
        for lane in lanes:
            notes = track_notes(pattern, lane)
            if not notes:
                continue
            tracks[lane] = [note for note in notes if not (start <= _note_step(note) < end)]

    for raw in plans:
        if not isinstance(raw, dict):
            continue
        try:
            landing = float(raw["landing_bar"]) * BEATS_PER_BAR
        except (KeyError, TypeError, ValueError):
            continue
        kind = str(raw.get("kind") or "")
        if kind == "kick_breath_fill":
            _clear(("kick", "808_bass", "sub_808"), landing - 1.0, landing)
        elif kind == "half_bar_stop":
            _clear(
                ("kick", "808_bass", "sub_808", "snare", "snare_layer", "clap", "hi_hats"),
                landing - 2.0,
                landing,
            )


def _backbeat_palette_hits(
    pattern: dict[str, Any], notes: list[dict[str, Any]], *, want: str
) -> list[dict[str, Any]]:
    """Keep only the hits whose bar's palette figure asks for ``want``.

    ``want`` is ``"layer"`` (this bar's stack includes the layer) or
    ``"pickup"`` (this bar takes the ghost before the anchor). A pass-through
    when the backbeat palette never ran — the non-MIDI route and standalone
    unit patterns keep the old every-clap layer exactly.
    """
    from midi_phrase_drums import backbeat_figure_for_bar

    if not isinstance(pattern.get("plg_backbeat_figures"), dict):
        return notes
    kept: list[dict[str, Any]] = []
    for note in notes:
        figure = backbeat_figure_for_bar(pattern, int(_note_step(note) // BEATS_PER_BAR))
        if figure is None:
            kept.append(note)
        elif want == "pickup":
            if figure.get("pickup"):
                kept.append(note)
        elif "layer" in (figure.get("stack") or ()):
            kept.append(note)
    return kept


def duplicate_snare_layer(
    snare_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rim layer: same hits, ~40% velocity."""
    layer: list[dict[str, Any]] = []
    for entry in snare_notes:
        hit = deepcopy(entry)
        _set_velocity(hit, int(int(hit.get("velocity", 100)) * 0.4))
        layer.append(hit)
    return layer


_CLAP_LAYER_DELAY_MS_MIN = 5.0
_CLAP_LAYER_DELAY_MS_MAX = 15.0
_CLAP_LAYER_VELOCITY_FACTOR = 0.8
_CLAP_LAYER_PAN_SPREAD = 45  # wide, alternating L/R — the "expensive" doubled-clap width


def duplicate_clap_layer(
    clap_notes: list[dict[str, Any]],
    bpm: float,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Snare Layer as a CLAP double — same hits, delayed 5-15 ms and panned
    wide alternating L/R, for a thicker "expensive" stacked-clap sound.
    Unlike duplicate_snare_layer's quiet ~40% rim texture, this is meant to
    be heard as a real double, so velocity only drops ~20%. The delay is
    drawn ONCE per generation (not per-hit) so the whole layer sits at one
    consistent offset behind the dry clap, not a jittery smear."""
    if not clap_notes:
        return []
    delay_beats = ms_to_beats(rng.uniform(_CLAP_LAYER_DELAY_MS_MIN, _CLAP_LAYER_DELAY_MS_MAX), bpm)
    layer: list[dict[str, Any]] = []
    for i, entry in enumerate(clap_notes):
        hit = deepcopy(entry)
        hit["time_step"] = round(_note_step(hit) + delay_beats, 5)
        _set_velocity(hit, int(int(hit.get("velocity", 100)) * _CLAP_LAYER_VELOCITY_FACTOR))
        hit["pan"] = 64 + (_CLAP_LAYER_PAN_SPREAD if i % 2 == 0 else -_CLAP_LAYER_PAN_SPREAD)
        hit["clap_double"] = True
        layer.append(hit)
    return layer


_PRE_SNARE_PICKUP_LEAD_BEATS = 0.25
_PRE_SNARE_PICKUP_LENGTH = 0.1
_PRE_SNARE_PICKUP_VELOCITY_DROP = 35


def add_pre_snare_pickup(
    snare_notes: list[dict[str, Any]],
    layer_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Minimal dark trap: a quiet 'snap' pickup just before each snare hit,
    riding the existing snare_layer channel — no new lane created. Only
    fires on the honest backbeat (skips fills/ghosts/risers), same filter
    duplicate_snare_layer already applies.
    """
    if not snare_notes or not layer_notes:
        return layer_notes
    existing_steps = {round(_note_step(n), 3) for n in layer_notes}
    pickups: list[dict[str, Any]] = []
    for hit in snare_notes:
        if hit.get("fill") or hit.get("ghost") or hit.get("riser"):
            continue
        pickup_step = round(_note_step(hit) - _PRE_SNARE_PICKUP_LEAD_BEATS, 4)
        if pickup_step < 0 or round(pickup_step, 3) in existing_steps:
            continue
        pickup = deepcopy(hit)
        pickup["time_step"] = pickup_step
        pickup["length"] = _PRE_SNARE_PICKUP_LENGTH
        _set_velocity(pickup, int(hit.get("velocity", 100)) - _PRE_SNARE_PICKUP_VELOCITY_DROP)
        pickups.append(pickup)
        existing_steps.add(round(pickup_step, 3))
    if not pickups:
        return layer_notes
    return sorted(layer_notes + pickups, key=_note_step)


_PRE_CLAP_ROLL_MIN_BARS = 4
_PRE_CLAP_ROLL_MAX_BARS = 8
_PRE_CLAP_ROLL_STEP = 0.25  # 1/16 grid
_PRE_CLAP_ROLL_FACTORS = (0.4, 0.7)  # the main hit itself is the 100% stage


def _pre_clap_roll_micro_notes(
    entry: dict[str, Any], existing_steps: set[float]
) -> list[dict[str, Any]]:
    """The two build-up micro-notes (40% -> 70%) for one clap hit, or []
    if every micro slot is already occupied. Pulled out of add_pre_clap_roll
    so both its exact-old and section-relative paths share one
    implementation."""
    step = _note_step(entry)
    base_vel = int(entry.get("velocity", 100))
    stages = len(_PRE_CLAP_ROLL_FACTORS)
    out: list[dict[str, Any]] = []
    for i, factor in enumerate(_PRE_CLAP_ROLL_FACTORS, start=1):
        lead = _PRE_CLAP_ROLL_STEP * (stages + 1 - i)
        micro_step = round(step - lead, 5)
        if micro_step < 0 or round(micro_step, 4) in existing_steps:
            continue
        micro = deepcopy(entry)
        micro["time_step"] = micro_step
        micro["length"] = min(float(entry.get("length", 0.2)), _PRE_CLAP_ROLL_STEP * 0.9)
        _set_velocity(micro, max(1, int(round(base_vel * factor))))
        micro["pre_clap_roll"] = True
        out.append(micro)
        existing_steps.add(round(micro_step, 4))
    return out


def add_pre_clap_roll(
    clap_notes: list[dict[str, Any]],
    rng: random.Random,
    *,
    arrangement: list[dict[str, Any]] | None = None,
    base_seed: int | None = None,
) -> list[dict[str, Any]]:
    """Every 4-8 bars (random per occurrence, not every clap), two 1/16-grid
    micro-notes build into the clap hit — 40% -> 70% -> 100% (the clap's own
    velocity) — instead of every clap firing cold with no lead-in.

    The "how many bars until the next roll" countdown used to run as ONE
    sequential walk across the whole song, seeded once from the shared
    `rng` stream — so two occurrences of the same section picked rolls at
    different relative positions purely because of how much of the song
    preceded them (same bug class as fix #3,
    drum_blueprint._tile_cell_pattern). This one also reached further than
    its own track: the extra clap onsets it inserts feed
    bass808_gen._pick_onsets' clap-avoidance filter, so it kept perturbing
    kick/808 choices even after the timing/rhythm/sync fixes upstream.

    When `arrangement`/`base_seed` are given, the countdown resets at every
    section-occurrence boundary and each occurrence draws its thresholds
    from drum_blueprint._relative_random(base_seed, section_name,
    attempt_index, ...) instead of the shared stream — reusing fix #3's
    own helpers — so two occurrences of the same section place their rolls
    identically. Falls back to today's exact stateful-`rng` walk, unchanged,
    when arrangement/base_seed aren't supplied.
    """
    if not clap_notes:
        return clap_notes
    ordered = sorted((dict(n) for n in clap_notes), key=_note_step)
    existing_steps = {round(_note_step(n), 4) for n in ordered}
    added: list[dict[str, Any]] = []

    if not arrangement or base_seed is None:
        next_eligible_bar = rng.randint(_PRE_CLAP_ROLL_MIN_BARS, _PRE_CLAP_ROLL_MAX_BARS)
        for entry in ordered:
            if entry.get("fill") or entry.get("ghost") or entry.get("riser"):
                continue
            step = _note_step(entry)
            bar = int(step // BEATS_PER_BAR)
            if bar < next_eligible_bar:
                continue
            micros = _pre_clap_roll_micro_notes(entry, existing_steps)
            added.extend(micros)
            if micros:
                next_eligible_bar = bar + rng.randint(_PRE_CLAP_ROLL_MIN_BARS, _PRE_CLAP_ROLL_MAX_BARS)
        return sorted(ordered + added, key=_note_step)

    from drum_blueprint import _relative_random, _section_for_bar, _section_start_bar_for_bar

    current_occurrence: Any = "unset"  # sentinel — never equals a real (name, start_bar) or None
    occurrence: tuple[str, int] | None = None
    next_eligible_bar = 0.0
    attempt_index = 0
    for entry in ordered:
        if entry.get("fill") or entry.get("ghost") or entry.get("riser"):
            continue
        step = _note_step(entry)
        bar = int(step // BEATS_PER_BAR)
        section_start = _section_start_bar_for_bar(arrangement, bar)
        occurrence = (_section_for_bar(arrangement, bar), section_start) if section_start is not None else None
        if occurrence != current_occurrence:
            current_occurrence = occurrence
            attempt_index = 0
            if occurrence is None:
                # No section covers this note (partial/missing arrangement
                # coverage) — same stateful fallback as the no-arrangement
                # path, scoped to just this stray run of notes.
                next_eligible_bar = bar + rng.randint(_PRE_CLAP_ROLL_MIN_BARS, _PRE_CLAP_ROLL_MAX_BARS)
            else:
                name, start_bar = occurrence
                seed_rng = _relative_random(base_seed, name, attempt_index, "pre_clap_roll")
                next_eligible_bar = start_bar + seed_rng.randint(_PRE_CLAP_ROLL_MIN_BARS, _PRE_CLAP_ROLL_MAX_BARS)
        if bar < next_eligible_bar:
            continue
        micros = _pre_clap_roll_micro_notes(entry, existing_steps)
        added.extend(micros)
        if micros:
            attempt_index += 1
            if occurrence is None:
                next_eligible_bar = bar + rng.randint(_PRE_CLAP_ROLL_MIN_BARS, _PRE_CLAP_ROLL_MAX_BARS)
            else:
                name, start_bar = occurrence
                seed_rng = _relative_random(base_seed, name, attempt_index, "pre_clap_roll")
                # `bar`, not `start_bar` — the countdown runs from the roll that
                # just fired, exactly as the stateful branch above does. It read
                # `start_bar` until 2026-08-26, which meant every threshold after
                # the first was `start_bar + 4..8`: once the section was more
                # than eight bars old, EVERY later threshold was already in the
                # past and the gate stopped blocking, so a roll fired on every
                # remaining clap. Invisible while sections were 4-8 bars long
                # (the section ended before the gate could fail); the five-part
                # form's 12-16 bar sections turned it into three claps a bar for
                # ten bars running — 55 % of the whole lane on BatonRouge
                # against 17 % before. the project issue log (not included) 0ab.7.
                next_eligible_bar = bar + seed_rng.randint(_PRE_CLAP_ROLL_MIN_BARS, _PRE_CLAP_ROLL_MAX_BARS)
    return sorted(ordered + added, key=_note_step)


_DELAYED_CLAP_SHIFT = 0.25
_DELAYED_CLAP_CYCLE_BEATS = 8.0  # 2-bar cell


def add_delayed_clap_hit(data: dict[str, Any]) -> int:
    """prod gas ("How To Make Pluggnb"): one fixed clap hit per 2-bar cell
    lands a quarter-beat late instead of on its usual grid slot — a single
    deliberate off-grid accent for "balance and uniqueness", not a general
    swing. Picks the LATEST clap in each 2-bar cell (that cell's own backbeat
    accent). Returns the number of notes shifted."""
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return 0
    clap = tracks.get("clap")
    if not isinstance(clap, list) or not clap:
        return 0

    by_cycle: dict[int, dict[str, Any]] = {}
    for note in clap:
        if not isinstance(note, dict):
            continue
        step = _note_step(note)
        cycle = int(step // _DELAYED_CLAP_CYCLE_BEATS)
        current = by_cycle.get(cycle)
        if current is None or step > _note_step(current):
            by_cycle[cycle] = note

    existing_steps = {round(_note_step(n), 3) for n in clap if isinstance(n, dict)}
    shifted = 0
    for note in by_cycle.values():
        old_step = round(_note_step(note), 3)
        new_step = round(_note_step(note) + _DELAYED_CLAP_SHIFT, 4)
        if round(new_step, 3) in existing_steps:
            continue
        existing_steps.discard(old_step)
        note["time_step"] = new_step
        existing_steps.add(round(new_step, 3))
        shifted += 1
    return shifted


_EIGHT_BAR_BEATS = 8 * BEATS_PER_BAR


def apply_melody_8bar_variation(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """prod gas: "no two 8-bars are identical" — the melody develops (a note
    added or dropped) at every 8-bar boundary instead of looping unchanged.
    Odd-indexed 8-bar blocks lose their last note (a held silence reads as
    development); blocks too sparse to thin get a passing tone added instead."""
    if not notes:
        return notes
    by_block: dict[int, list[dict[str, Any]]] = {}
    for note in notes:
        if not isinstance(note, dict):
            continue
        block = int(_note_step(note) // _EIGHT_BAR_BEATS)
        by_block.setdefault(block, []).append(note)

    out: list[dict[str, Any]] = []
    for block in sorted(by_block):
        block_notes = sorted(by_block[block], key=_note_step)
        if block % 2 == 1:
            if len(block_notes) >= 3:
                block_notes = block_notes[:-1]
            else:
                last = deepcopy(block_notes[-1])
                try:
                    midi = parse_note_name(str(last.get("note", "A4"))) + 3
                    last["note"] = _midi_to_name(min(131, midi))
                except ValueError:
                    pass
                last["time_step"] = round(_note_step(block_notes[-1]) + 0.5, 4)
                block_notes.append(last)
        out.extend(block_notes)
    return out


_BELL_OCTAVE_TRANSPOSE = 12
_BELL_OCTAVE_VELOCITY_DROP = 15


def materialize_bell_octave_counter_melody(data: dict[str, Any]) -> int:
    """GOYXRD ("Pluggnb" build) — the "bell cheat code": counter_melody
    becomes a full octave-up duplicate of melody_lead's own rhythm/contour,
    riding counter_melody's own independently-picked preset (not
    melody_lead's own sample) instead of the usual off-beat answer line.
    Overwrites whatever add_counter_melody_offbeat already wrote. Returns
    the number of notes written."""
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return 0
    melody = tracks.get("melody_lead")
    if not isinstance(melody, list) or not melody:
        return 0
    dup: list[dict[str, Any]] = []
    for entry in melody:
        if not isinstance(entry, dict):
            continue
        try:
            midi = parse_note_name(str(entry.get("note", "A4"))) + _BELL_OCTAVE_TRANSPOSE
        except ValueError:
            continue
        note = deepcopy(entry)
        note["note"] = _midi_to_name(max(0, min(131, midi)))
        _set_velocity(note, int(entry.get("velocity", 90)) - _BELL_OCTAVE_VELOCITY_DROP)
        dup.append(note)
    if dup:
        tracks["counter_melody"] = dup
    return len(dup)


_TOP_LAYER_TRANSPOSE = 12
_TOP_LAYER_VELOCITY_DROP = 12


def materialize_muted_top_layer(data: dict[str, Any]) -> int:
    """Jay Cactus (Detroit): the piano's own top-line — only the notes in the
    UPPER half of its own register survive (the lower notes are muted before
    render, the classic "render the top, mute the bottom" trick), pitched up
    an octave, riding counter_melody's own independently-picked preset. A
    filtered variation on the octave-up bell/shimmer layers elsewhere: ONLY
    the melody's brighter half gets duplicated, not the whole pattern.
    Overwrites whatever add_counter_melody_offbeat already wrote. Returns
    the number of notes written."""
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return 0
    melody = tracks.get("melody_lead")
    if not isinstance(melody, list) or not melody:
        return 0
    pitches: list[int] = []
    for entry in melody:
        if not isinstance(entry, dict):
            continue
        try:
            pitches.append(parse_note_name(str(entry.get("note", "A4"))))
        except ValueError:
            continue
    if not pitches:
        return 0
    pitches.sort()
    median = pitches[len(pitches) // 2]

    dup: list[dict[str, Any]] = []
    for entry in melody:
        if not isinstance(entry, dict):
            continue
        try:
            midi = parse_note_name(str(entry.get("note", "A4")))
        except ValueError:
            continue
        if midi < median:
            continue  # muted — this is the "bottom" the video renders silent
        note = deepcopy(entry)
        note["note"] = _midi_to_name(max(0, min(131, midi + _TOP_LAYER_TRANSPOSE)))
        _set_velocity(note, int(entry.get("velocity", 90)) - _TOP_LAYER_VELOCITY_DROP)
        dup.append(note)
    if dup:
        tracks["counter_melody"] = dup
    return len(dup)


_ALT_BASS_SECTIONS = ("chorus", "hook", "drop")


def apply_alternating_bass_sections(data: dict[str, Any]) -> int:
    """Jay Cactus (Detroit): the sub bass alternates between the "classic
    Detroit bass" (default sub_808 sample) and a "spins" alternate bass in
    chorus/hook/drop sections — not one bass sample for the whole track.
    sub_808 is a single Sampler channel (one loaded sample), so the alternate
    rides the fx lane instead: sub_808's notes in the big sections move over
    to fx (same pitch/rhythm), and are removed from sub_808 there, so the two
    never sound at once. Returns the number of notes moved."""
    tracks = data.get("tracks")
    arrangement = data.get("plg_arrangement")
    if not isinstance(tracks, dict) or not isinstance(arrangement, list):
        return 0
    sub = tracks.get("sub_808")
    if not isinstance(sub, list) or not sub:
        return 0
    if tracks.get("fx"):
        return 0  # fx lane already claimed (e.g. a riser/crash genre)

    windows: list[tuple[float, float]] = []
    for section in arrangement:
        if not isinstance(section, dict) or section.get("name") not in _ALT_BASS_SECTIONS:
            continue
        try:
            start_bar = int(section.get("start_bar", 0))
            bars = int(section.get("bars", 0))
        except (TypeError, ValueError):
            continue
        if bars > 0:
            windows.append((start_bar * BEATS_PER_BAR, (start_bar + bars) * BEATS_PER_BAR))
    if not windows:
        return 0

    kept: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    for note in sub:
        t = _note_step(note) if isinstance(note, dict) else 0.0
        if any(s <= t < e for s, e in windows):
            moved.append(deepcopy(note))
        else:
            kept.append(note)
    if not moved:
        return 0
    tracks["sub_808"] = kept
    tracks["fx"] = sorted(moved, key=_note_step)
    data["plg_fx_kind"] = "alt_bass_spins"
    return len(moved)


def apply_live_bass_texture_sections(data: dict[str, Any]) -> int:
    """Mike Dean/Travis: chorus/hook/drop sections DOUBLE the synth 808 with a
    genuinely different LIVE-sounding bass (bass guitar/upright/analog
    character) on the PLG Texture lane — additive, not a swap. sub_808 keeps
    its full part everywhere (the 808 identity/listenability floor stays
    intact); the live layer is extra weight under the hook/drop only. Same
    section-window logic as apply_alternating_bass_sections
    (_ALT_BASS_SECTIONS), targeting `texture` instead of `fx` so it never
    fights the trap riser (fx_riser_from_chords) for the fx lane. Runs after
    the octave-down/up melody-shimmer layers so it never preempts their
    (audit-protected) claim on the texture lane. Returns the number of
    live-bass notes placed."""
    tracks = data.get("tracks")
    arrangement = data.get("plg_arrangement")
    if not isinstance(tracks, dict) or not isinstance(arrangement, list):
        return 0
    sub = tracks.get("sub_808")
    if not isinstance(sub, list) or not sub:
        return 0
    if tracks.get("texture"):
        return 0  # texture lane already claimed (e.g. an octave-layer genre)

    windows: list[tuple[float, float]] = []
    for section in arrangement:
        if not isinstance(section, dict) or section.get("name") not in _ALT_BASS_SECTIONS:
            continue
        try:
            start_bar = int(section.get("start_bar", 0))
            bars = int(section.get("bars", 0))
        except (TypeError, ValueError):
            continue
        if bars > 0:
            windows.append((start_bar * BEATS_PER_BAR, (start_bar + bars) * BEATS_PER_BAR))
    if not windows:
        return 0

    doubled: list[dict[str, Any]] = []
    for note in sub:
        t = _note_step(note) if isinstance(note, dict) else 0.0
        if any(s <= t < e for s, e in windows):
            doubled.append(deepcopy(note))
    if not doubled:
        return 0
    tracks["texture"] = sorted(doubled, key=_note_step)
    data["plg_texture_kind"] = "live_bass"
    return len(doubled)


_OPEN_HAT_CUT_LEAD_BEATS = 0.25
_OPEN_HAT_CUT_LENGTH = 0.4  # >=0.35 qualifies as "open" per _is_open_hat
_OPEN_HAT_CUT_VELOCITY_DROP = 25


def add_open_hat_before_snare(
    hats: list[dict[str, Any]],
    snare_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Jay Cactus (Detroit): an open hat lands just before each snare hit,
    velocity pulled back — apply_open_hat_choke (already in the pipeline,
    must run AFTER this) naturally clips its tail against the next closed
    hit, reading as a rhythmic "cut". Reuses a nearby existing hi-hat note
    if one already sits in the window; only inserts a new one when the slot
    is empty."""
    if not snare_notes:
        return hats
    from hihat_rolls import HAT_NOTE

    out = [deepcopy(h) for h in hats] if hats else []
    existing_steps = {round(_note_step(h), 3) for h in out}
    for hit in snare_notes:
        if not isinstance(hit, dict):
            continue
        target = round(_note_step(hit) - _OPEN_HAT_CUT_LEAD_BEATS, 4)
        if target < 0:
            continue
        near = next((h for h in out if abs(_note_step(h) - target) < 0.06), None)
        if near is not None:
            near["hat_type"] = "open"
            near["length"] = max(float(near.get("length", 0.0)), _OPEN_HAT_CUT_LENGTH)
            _set_velocity(near, int(near.get("velocity", 100)) - _OPEN_HAT_CUT_VELOCITY_DROP)
            continue
        if round(target, 3) in existing_steps:
            continue
        base_velocity = int(hit.get("velocity", 100))
        out.append({
            "time_step": target,
            "note": HAT_NOTE,
            "length": _OPEN_HAT_CUT_LENGTH,
            "velocity": max(1, base_velocity - _OPEN_HAT_CUT_VELOCITY_DROP),
            "hat_type": "open",
        })
        existing_steps.add(round(target, 3))
    return sorted(out, key=_note_step)


def apply_intro_octave_up(data: dict[str, Any]) -> int:
    """Jay Cactus (Detroit): the whole piano/lead layer transposes up a full
    octave for just the intro section, for variety before the beat "locks
    in" at full pitch. Returns the number of notes transposed."""
    tracks = data.get("tracks")
    arrangement = data.get("plg_arrangement")
    if not isinstance(tracks, dict) or not isinstance(arrangement, list):
        return 0
    melody = tracks.get("melody_lead")
    if not isinstance(melody, list) or not melody:
        return 0
    intro_window: tuple[float, float] | None = None
    for section in arrangement:
        if isinstance(section, dict) and section.get("name") == "intro":
            try:
                start_bar = int(section.get("start_bar", 0))
                bars = int(section.get("bars", 0))
            except (TypeError, ValueError):
                continue
            if bars > 0:
                intro_window = (start_bar * BEATS_PER_BAR, (start_bar + bars) * BEATS_PER_BAR)
            break
    if intro_window is None:
        return 0
    start, end = intro_window
    transposed = 0
    for note in melody:
        if not isinstance(note, dict):
            continue
        t = _note_step(note)
        if not (start <= t < end):
            continue
        try:
            midi = parse_note_name(str(note.get("note", "C5"))) + 12
        except ValueError:
            continue
        note["note"] = _midi_to_name(min(131, midi))
        transposed += 1
    return transposed


_CHAOTIC_JUMP_INTERVALS = (-19, -12, -7, 7, 12, 19)
_CHAOTIC_JUMP_CHANCE = 0.5


def apply_chaotic_melody_jumps(
    notes: list[dict[str, Any]],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Felix (hyperpop/100 gecs-style): intentionally chaotic, wide-interval
    melody contour — the opposite of smooth voice-leading. Each note (after
    the first) has a coin-flip chance to jump a wide interval (a 5th to an
    octave+5th, either direction) from the PREVIOUS note instead of
    following a smooth line. Returns a new list."""
    if not notes:
        return notes
    ordered = sorted((deepcopy(n) for n in notes), key=_note_step)
    prev_midi: int | None = None
    for note in ordered:
        try:
            midi = parse_note_name(str(note.get("note", "A4")))
        except ValueError:
            continue
        if prev_midi is not None and rng.random() < _CHAOTIC_JUMP_CHANCE:
            jump = rng.choice(_CHAOTIC_JUMP_INTERVALS)
            midi = max(0, min(131, prev_midi + jump))
            note["note"] = _midi_to_name(midi)
        prev_midi = midi
    return ordered


def apply_808_snare_gate_stutter(
    sub_notes: list[dict[str, Any]],
    snare_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Felix (hyperpop): the 808 cuts off exactly on the next snare hit — an
    ADSR envelope gate, not a duck — leaving a hard silence before the next
    808 note picks back up. Shortens any sub_808 note whose sustain runs
    past a snare hit so it ends right there. Returns a new list."""
    if not sub_notes or not snare_notes:
        return sub_notes
    snare_steps = sorted(_note_step(s) for s in snare_notes if isinstance(s, dict))
    out: list[dict[str, Any]] = []
    for note in sub_notes:
        if not isinstance(note, dict):
            out.append(note)
            continue
        entry = deepcopy(note)
        start = _note_step(entry)
        end = _note_end(entry)
        cut_at = next((s for s in snare_steps if start < s < end), None)
        if cut_at is not None:
            entry["length"] = max(0.05, round(cut_at - start, 5))
            entry["gated_stutter"] = True
        out.append(entry)
    return out


def apply_root_fifth_seventh_bias(
    notes: list[dict[str, Any]],
    root_pc: int,
    scale: str,
) -> list[dict[str, Any]]:
    """nightiger (digicore): "the most common progression in digicore" —
    melody drawn predominantly from the root/5th/7th scale degrees, not the
    full 7-tone scale. Snaps each note to the nearest of those three pitch
    classes (ties resolve downward, matching darken_melody_intervals).
    Returns a new list."""
    if not notes:
        return notes
    from music_theory import SCALES

    intervals = SCALES.get(scale, SCALES["natural_minor"])
    degree_pcs = {(root_pc + intervals[d]) % 12 for d in (0, 4, 6) if d < len(intervals)}
    if not degree_pcs:
        return notes
    out: list[dict[str, Any]] = []
    for entry in notes:
        note = deepcopy(entry)
        try:
            midi = parse_note_name(str(note.get("note", "A4")))
        except ValueError:
            out.append(note)
            continue
        pc = midi % 12
        if pc not in degree_pcs:
            for dist in range(1, 7):
                if (pc - dist) % 12 in degree_pcs:
                    midi -= dist
                    break
                if (pc + dist) % 12 in degree_pcs:
                    midi += dist
                    break
            note["note"] = _midi_to_name(max(0, min(131, midi)))
        out.append(note)
    return out


_PLUCK_LENGTH_THRESHOLD = 0.6  # beats — shorter than this reads as a "pluck", not a sustained 808
_PLUCK_SIDECHAIN_FACTOR = 0.35  # plucks get a much shallower pump — mostly "none"


def classify_bass_type(sub_notes: list[dict[str, Any]]) -> str | None:
    """"pluck" | "sustained" from the sub_808 lane's median note length, or
    None when there's nothing to measure."""
    if not sub_notes:
        return None
    lengths = sorted(float(n.get("length", 0.75)) for n in sub_notes if isinstance(n, dict))
    if not lengths:
        return None
    median_len = lengths[len(lengths) // 2]
    return "pluck" if median_len < _PLUCK_LENGTH_THRESHOLD else "sustained"


def bass_type_aware_sidechain_depth(
    sub_notes: list[dict[str, Any]],
    base_depth: float,
    *,
    pre_gate_kind: str | None = None,
) -> float:
    """nightiger (digicore): a long/sustained 808 wants MORE sidechain pump
    from the kick; a short bass-pluck wants less (or none) — not one fixed
    depth for every bass type. Uses the sub_808 lane's own median note
    length as the signal. ``pre_gate_kind`` is the classification taken
    BEFORE apply_808_snare_gate_stutter shortened the notes (2026-07 audit:
    a gate-chopped sustained 808 must not be mistaken for plucks)."""
    kind = pre_gate_kind or classify_bass_type(sub_notes)
    if kind == "pluck":
        return round(base_depth * _PLUCK_SIDECHAIN_FACTOR, 3)
    return base_depth


_CHROMATIC_WALK_STEP_CHANCE = 0.6  # continues the crawl instead of holding/repeating
_CHROMATIC_WALK_JUMP_CHANCE = 0.15  # rare unrelated jump aside
_CHROMATIC_WALK_JUMP_INTERVALS = (-7, -5, 5, 7)
_CHROMATIC_WALK_REVERSE_CHANCE = 0.2


def apply_chromatic_melody_walk(
    notes: list[dict[str, Any]],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Drift Phonk tutorial: the melody moves chromatically note-to-note —
    deliberate passing tones OUTSIDE the scale, not "stay in scale" like
    every other genre in this system. A repeated-note riff that crawls up or
    down by a semitone (direction occasionally reverses), with a rare
    unrelated jump aside. Runs AFTER the genre's normal scale-snap — the
    point is to deliberately pull SOME notes back off it. Returns a new
    list."""
    if not notes:
        return notes
    ordered = sorted((deepcopy(n) for n in notes), key=_note_step)
    direction = rng.choice((-1, 1))
    prev_midi: int | None = None
    for note in ordered:
        try:
            midi = parse_note_name(str(note.get("note", "A4")))
        except ValueError:
            continue
        if prev_midi is not None:
            roll = rng.random()
            if roll < _CHROMATIC_WALK_JUMP_CHANCE:
                jump = rng.choice(_CHROMATIC_WALK_JUMP_INTERVALS)
                midi = max(0, min(131, prev_midi + jump))
                direction = rng.choice((-1, 1))
            elif roll < _CHROMATIC_WALK_JUMP_CHANCE + _CHROMATIC_WALK_STEP_CHANCE:
                if rng.random() < _CHROMATIC_WALK_REVERSE_CHANCE:
                    direction = -direction
                midi = max(0, min(131, prev_midi + direction))
            else:
                midi = prev_midi  # repeated note — the riff's held pulse
            note["note"] = _midi_to_name(midi)
        prev_midi = midi
    return ordered


_KICK_808_SYNC_TOLERANCE = 0.12  # beats — close enough to count as already aligned


def apply_kick_synced_to_808(
    kick_notes: list[dict[str, Any]],
    sub_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drift Phonk tutorial: the kick is placed WHERE the 808 hits
    (reinforcing it), not generated independently of the bass line. Adds a
    kick hit at every 808 note's onset that doesn't already have one nearby.
    Returns a new list."""
    if not sub_notes:
        return kick_notes
    out = [deepcopy(k) for k in kick_notes] if kick_notes else []
    kick_steps = [_note_step(k) for k in out]
    for sub in sub_notes:
        if not isinstance(sub, dict):
            continue
        onset = _note_step(sub)
        if any(abs(onset - ks) < _KICK_808_SYNC_TOLERANCE for ks in kick_steps):
            continue
        out.append({
            "time_step": round(onset, 4),
            "note": "C1",
            "length": 0.3,
            "velocity": 116,
            "kick_808_sync": True,
        })
        kick_steps.append(onset)
    return sorted(out, key=_note_step)


def darken_melody_intervals(
    notes: list[dict[str, Any]],
    *,
    scale: str = "natural_minor",
) -> list[dict[str, Any]]:
    """Rule 11 — snap melody into a dark, key-aware scale (minor / phrygian).

    Detects the melody's own root, then quantizes every note to the nearest
    in-scale tone (ties resolve downward for darkness). Replaces the old blind
    'flatten every E and B' rule, which corrupted any key that wasn't C-ish.
    """
    if not notes:
        return []
    root = detect_root_pc(notes)
    out: list[dict[str, Any]] = []
    for entry in notes:
        note = deepcopy(entry)
        try:
            midi = parse_note_name(str(note.get("note", "A4")))
            snapped = snap_pc_to_scale(midi, root, scale)
            if snapped != midi:
                note["note"] = _midi_to_name(snapped)
        except ValueError:
            pass
        out.append(note)
    return out


def add_counter_melody_hooks(
    melody_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Legacy sparse hooks — prefer add_counter_melody_offbeat in humanize."""
    if not melody_notes:
        return []
    counter: list[dict[str, Any]] = []
    for entry in melody_notes:
        step = _note_step(entry)
        bar = int(step // BEATS_PER_BAR)
        if bar % 2 == 1 and step % BEATS_PER_BAR >= 2.0:
            try:
                midi = parse_note_name(str(entry.get("note", "A4")))
            except ValueError:
                midi = 69
            counter.append({
                "time_step": round(step + 0.5, 5),
                "note": _midi_to_name(min(88, midi + 12)),
                "length": 0.2,
                "velocity": 78,
                "pan": 72,
            })
    return sorted(counter, key=_note_step)


def build_sidechain_hints(
    kick_notes: list[dict[str, Any]], *, target: str = "sub_808"
) -> list[dict[str, Any]]:
    """Automation hints: duck the active 808 lane by ~30% on kick (gain 0.7),
    recover in 2 steps. ``target`` defaults to sub_808 but callers should pass
    whichever 808 lane is actually populated — bass808_enabled profiles carry
    their sub on 808_bass, with sub_808 always empty (see _drop_midi_lanes),
    so a hardcoded "sub_808" target here used to point at nothing for the
    entire trap/rage/pluggnb/... family."""
    hints: list[dict[str, Any]] = []
    for entry in kick_notes:
        step = _note_step(entry)
        hints.append({"time_step": step, "target": target, "gain": 0.7})
        hints.append({"time_step": round(step + 0.125, 5), "target": target, "gain": 0.85})
        hints.append({"time_step": round(step + 0.25, 5), "target": target, "gain": 1.0})
    return hints


_HARD_TRAP_MARKERS = (
    "rage",
    "opium",
    "опium",
    "dark trap",
    "underground",
    "plugg",
    "jerk",
    "phonk",
    "drift",
    "memphis",
    "drill",
    "808",
    "hard-hitting",
    "aggressive",
    "distorted",
)
_AMBIENT_RE = re.compile(r"\b(ambient|chill|sleep|эмбиент)\b", re.IGNORECASE)


def _style_context(pattern: dict[str, Any]) -> str:
    parts = [str(pattern.get("style", "")), str(pattern.get("user_prompt", ""))]
    tags = pattern.get("plg_style_tags")
    if isinstance(tags, list):
        parts.extend(str(tag) for tag in tags)
    return " ".join(parts).lower()


def _style_flags(pattern: dict[str, Any]) -> dict[str, bool]:
    text = _style_context(pattern)
    tags = pattern.get("plg_style_tags")
    tag_blob = " ".join(str(t).upper() for t in tags).upper() if isinstance(tags, list) else ""
    rage = any(k in text for k in ("rage", "dark trap", "underground", "plugg", "jerk", "opium", "опium"))
    phonk = any(k in text for k in ("phonk", "drift", "memphis", "cowbell"))
    hard_trap = rage or phonk or "RAGE" in tag_blob or any(k in text for k in _HARD_TRAP_MARKERS)
    ambientish = bool(_AMBIENT_RE.search(text))
    return {
        "rage": rage,
        "country": any(k in text for k in ("country", "кантри", "banjo", "банджо")),
        "phonk": phonk,
        "hard_trap": hard_trap,
        "ambient": ambientish and not hard_trap,
        "pop_dance": any(k in text for k in ("dance pop", "nu-disco", "disco", "dark pop")),
    }


def reprocess_hi_hats(pattern: dict[str, Any], *, chaos_seed: int | None = None) -> dict[str, Any]:
    """Re-run hat roll + humanize on hi-hats (Chaos Roll button)."""
    data = deepcopy(pattern)
    tracks = data.setdefault("tracks", {})
    if not isinstance(tracks, dict):
        return data

    hats = track_notes(data, "hi_hats")
    if not hats:
        return data

    base = [deepcopy(n) for n in hats if not n.get("hat_roll")]
    if len(base) < 3:
        base = [deepcopy(n) for n in hats]

    tracks["hi_hats"] = base
    rng = random.Random(chaos_seed) if chaos_seed is not None else _rng_for(data)

    from hihat_rolls import apply_trap_hi_hats

    hats = apply_trap_hi_hats(data, rng)
    hats = apply_hat_snare_micro_drop(hats, track_notes(data, "snare"))
    hats = apply_hat_roll_pitch(hats, rng)
    hats = settle_generated_hat_register(hats, rng)
    hats = apply_hi_hat_swing(hats, rng)
    hats = apply_open_hat_choke(hats)
    hats = apply_hat_panning(hats, rng)
    hats = apply_hat_forward_lag(hats, rng)

    snare = track_notes(data, "snare")
    clap = track_notes(data, "clap")
    ref = clap or snare
    if ref:
        hats = apply_six_db_hat_rule(hats, ref)

    tracks["hi_hats"] = hats
    data["plg_chaos_rolls"] = int(data.get("plg_chaos_rolls", 0)) + 1
    data["plg_hat_rolls"] = data.get("plg_hat_rolls", 0)
    return data


# Register layout — stop the "everything mushed in C5-C6" problem.
DRUM_NATIVE_MIDI = 60        # C5 — one-shot drums play at native pitch here
SUB_REGISTER = (48, 60)      # C4..C5 — library 808 one-shots; below C4 pitches to mud
MELODY_REGISTER = (60, 83)   # C5..B6 — keep the lead clear of the bass
# Hi-hats are intentionally left alone: apply_hat_roll_pitch tunes rolls (Rule 3).
DRUM_LAYOUT_KEYS = ("kick", "snare", "snare_layer", "clap")


def _clamp_octave(notes: list[dict[str, Any]], lo: int, hi: int) -> None:
    for entry in notes:
        try:
            midi = parse_note_name(str(entry.get("note", "C5")))
        except ValueError:
            continue
        while midi < lo:
            midi += 12
        while midi > hi:
            midi -= 12
        entry["note"] = _midi_to_name(midi)


def normalize_registers(pattern: dict[str, Any]) -> None:
    """Lay tracks into clean registers so notes don't collide into mush.

    Drums snap to a native key (one-shots play un-pitched); the 808 drops into a
    fat sub octave; melody/counter sit in the mid register — all preserving
    pitch class, so the key the humanizer locked stays intact.
    """
    tracks = pattern.get("tracks")
    if not isinstance(tracks, dict):
        return
    native = _midi_to_name(DRUM_NATIVE_MIDI)
    for key in DRUM_LAYOUT_KEYS:
        for entry in tracks.get(key) or []:
            entry["note"] = native
    _clamp_octave(tracks.get("sub_808") or [], *SUB_REGISTER)
    _clamp_octave(tracks.get("melody_lead") or [], *MELODY_REGISTER)
    _clamp_octave(tracks.get("counter_melody") or [], *MELODY_REGISTER)


def thicken_melody(
    notes: list[dict[str, Any]],
    *,
    register: tuple[int, int] = MELODY_REGISTER,
    max_density: float = 4.0,
) -> list[dict[str, Any]]:
    """Lush layered lead — octave-UP double of each note (reduced velocity) so a
    thin 2-note hook fills into a full 'wall', the way rage/opium leads are
    layered. Octave-UP never lowers the 808 root (it tracks the lowest melody
    note), and stays inside the melody register so normalize_registers won't snap
    it back. Skips already-busy melodies so it never clutters a dense line."""
    if not notes:
        return notes
    lo, hi = register
    end = max((_note_end(n) for n in notes), default=BEATS_PER_BAR)
    bars = max(1, math.ceil(end / BEATS_PER_BAR))
    if len(notes) / bars > max_density:  # already full — don't muddy it
        return [deepcopy(n) for n in notes]

    out = [deepcopy(n) for n in notes]
    for entry in notes:
        try:
            midi = parse_note_name(str(entry.get("note", "C5")))
        except ValueError:
            continue
        up = midi + 12
        if up <= hi:
            layer = deepcopy(entry)
            layer["note"] = _midi_to_name(up)
            layer["velocity"] = max(1, int(int(entry.get("velocity", 90)) * 0.68))
            layer["octave_layer"] = True
            out.append(layer)
    return sorted(out, key=_note_step)


def apply_melody_echo(
    notes: list[dict[str, Any]],
    *,
    max_per_note: int = 2,
    triplet: bool = False,
) -> list[dict[str, Any]]:
    """Printed dotted-eighth delay for sparse hooks (the trap mix staple).

    A 4-note sustained hook through a decaying one-shot reads as "нажали
    пианино раз в секунду". Real mixes print a 3/16 delay on the lead: each
    hit answers itself quieter. Deterministic echoes at +0.75 and +1.5 beats,
    55%/34% velocity, skipped when they'd collide with a real note.
    ``triplet=True`` switches to 1/3-beat taps — the Ken Carson texture."""
    if not notes:
        return notes
    out = sorted((deepcopy(n) for n in notes), key=_note_step)
    tap_grid = ((1 / 3, 0.55), (2 / 3, 0.34)) if triplet else ((0.75, 0.55), (1.5, 0.34))
    taps = tap_grid[:max_per_note]
    echoes: list[dict[str, Any]] = []

    def _collides(t: float) -> bool:
        return any(
            abs(t - _note_step(existing)) < 0.3
            for existing in out + echoes
        )

    for entry in out:
        if entry.get("echo") or int(entry.get("velocity", 0)) < 55:
            continue
        for delay, factor in taps:
            t = round(_note_step(entry) + delay, 5)
            if _collides(t):
                continue
            echo = deepcopy(entry)
            echo["time_step"] = t
            echo["velocity"] = max(26, int(round(int(entry.get("velocity", 96)) * factor)))
            echo["length"] = round(min(float(entry.get("length", 0.5)), max(0.25, delay * 0.8)), 5)
            echo["echo"] = True
            echoes.append(echo)
    return sorted(out + echoes, key=_note_step)


def apply_melody_groove_lag(
    notes: list[dict[str, Any]],
    bpm: float,
    ms: float,
    rng: random.Random,
    *,
    jitter_ms: float = MELODY_LAG_JITTER_MS,
) -> list[dict[str, Any]]:
    """Lazy groove-lag — drag melody notes a few ms late off the grid for a
    behind-the-beat feel (Groove-опоздание). Drums stay locked; only the melody
    leans back, which is what makes the pocket feel human instead of robotic."""
    if ms <= 0:
        return [deepcopy(n) for n in notes]
    out: list[dict[str, Any]] = []
    for entry in notes:
        note = deepcopy(entry)
        lag = max(0.0, ms + rng.uniform(-jitter_ms, jitter_ms))
        note["time_step"] = round(_note_step(note) + ms_to_beats(lag, bpm), 5)
        note["groove_lag_ms"] = round(lag, 2)
        out.append(note)
    return sorted(out, key=_note_step)


def apply_chord_voicing_spread(
    notes: list[dict[str, Any]],
    rng: random.Random,
    *,
    chance: float,
    register: tuple[int, int] = MELODY_REGISTER,
) -> list[dict[str, Any]]:
    """Open clustered chords — with ``chance`` probability per note, invert a tone
    by an octave (kept inside the melody register) so triads breathe and leave
    mid-range space for a vocal, instead of a tight cramped block chord."""
    if chance <= 0:
        return [deepcopy(n) for n in notes]
    lo, hi = register
    out: list[dict[str, Any]] = []
    for entry in notes:
        note = deepcopy(entry)
        if rng.random() < chance:
            try:
                midi = parse_note_name(str(note.get("note", "C5")))
                cand = midi + (12 if rng.random() < 0.5 else -12)
                if lo <= cand <= hi:
                    note["note"] = _midi_to_name(cand)
                    note["voicing_inverted"] = True
            except ValueError:
                pass
        out.append(note)
    return out


_HAT_STATES = ("ghost", "normal", "accent")
_HAT_VEL_FACTOR = {"ghost": 0.52, "normal": 0.80, "accent": 1.0}
# Legacy transitions — tests import apply_markov_hat_dynamics from beat_humanize.
from hihat_rolls import apply_markov_hat_dynamics  # noqa: E402


_KICK_SYNC_OFFSETS = (1.0, 2.5, 2.75, 3.25)  # weak 16ths; beat 2.0 (clap) excluded


def add_kick_syncopation(
    kick_notes: list[dict[str, Any]],
    bars: int,
    rng: random.Random,
    *,
    prob: float,
    clap_offsets: tuple[float, ...] = (2.0,),
) -> list[dict[str, Any]]:
    """Weighted syncopation matrix — sprinkle off-grid kicks on weak steps (never
    on the clap, never doubling an existing kick). The downbeat stays the anchor."""
    if prob <= 0:
        return [deepcopy(n) for n in kick_notes]
    out = [deepcopy(n) for n in kick_notes]
    existing = {round(_note_step(n), 3) for n in kick_notes}
    for bar in range(max(1, bars)):
        base = bar * BEATS_PER_BAR
        for off in _KICK_SYNC_OFFSETS:
            if rng.random() > prob:
                continue
            t = round(base + off, 5)
            if any(abs(t - e) < 0.2 for e in existing):
                continue
            if any(abs((t % BEATS_PER_BAR) - c) < 0.15 for c in clap_offsets):
                continue
            out.append({
                "time_step": t, "note": "C1", "length": 0.4,
                "velocity": rng.randint(86, 104), "kick_sync": True,
            })
            existing.add(t)
    return sorted(out, key=_note_step)


def build_snare_riser(
    end_beats: float,
    *,
    phrase_bars: int = 8,
) -> list[dict[str, Any]]:
    """Festival build — accelerating snare roll (1/4→1/8→1/16→1/32) with rising
    velocity and a pitch-up hint, in the bar before each phrase drop."""
    risers: list[dict[str, Any]] = []
    phrase_len = phrase_bars * BEATS_PER_BAR
    phrases = max(1, int(end_beats // phrase_len))
    # (segment start, end, step) — 1/4 → 1/8 → 1/16 → 1/32 across the last bar.
    segments = ((0.0, 1.0, 1.0), (1.0, 2.0, 0.5), (2.0, 3.0, 0.25), (3.0, 4.0, 0.125))
    for p in range(1, phrases + 1):
        bar_start = p * phrase_len - BEATS_PER_BAR
        for seg_start, seg_end, step in segments:
            t = bar_start + seg_start
            while t < bar_start + seg_end - 1e-6:
                prog = (t - bar_start) / BEATS_PER_BAR
                risers.append({
                    "time_step": round(t, 5), "note": "D1",
                    "length": round(step * 0.9, 5),
                    "velocity": min(127, int(78 + prog * 49)),
                    "riser": True, "pitch_up": int(round(prog * 12)),
                })
                t += step
    return sorted(risers, key=_note_step)


def _apply_sub_808_lane(data: dict[str, Any], profile: GenreProfile, bpm: float) -> None:
    """Rebuild or repair the 808 lane after all kick edits are final.

    LLM often dumps hat-style C5 micro-notes on sub_808; we always replace that
    with generate_sub_808 when kicks exist so FL shows long C1–B2 sustains.
    """
    from drum_defaults import _bar_count
    from sub_808_gen import (
        SUB_HI,
        SUB_HI_HIGH,
        SUB_LO,
        SUB_LO_HIGH,
        clamp_sub_register_notes,
        filter_close_onsets,
        generate_sub_808,
        is_hat_like_sub_808,
        should_replace_sub_808,
    )

    tracks = data.setdefault("tracks", {})
    kick = track_notes(data, "kick")
    melody = track_notes(data, "melody_lead")
    declared_bars = data.get("plg_total_bars")
    bars = int(declared_bars) if isinstance(declared_bars, int) and declared_bars > 0 else _bar_count(data)
    existing = track_notes(data, "sub_808")
    sub_lo, sub_hi = (
        (SUB_LO_HIGH, SUB_HI_HIGH) if getattr(profile, "sub_808_register_high", False) else (SUB_LO, SUB_HI)
    )

    from style_corpus import progression_roots_pc

    roots_pc = progression_roots_pc(data)

    # Where this function's low end is allowed to land.
    #
    # `808_bass` is the only permitted low-end lane; `sub_808` is retired and
    # midi_validate.sanitize_midi_mode deletes it. Only the 10 trap-family
    # profiles (bass808_enabled) have their own writer for `808_bass` —
    # `_apply_808_bass_lane`, which runs later and owns that lane outright. For
    # those, this function's output is a redundant second bass and is dropped.
    #
    # The other 29 profiles have no `808_bass` writer at all. Before
    # 2026-08-11 their bass was written to `sub_808` and then deleted, so they
    # generated with no low end and died in enforce_midi_mode with
    # "required lane is empty: 808_bass". They publish here instead.
    def _publish_low_end(notes: list[dict[str, Any]], *, source: str) -> None:
        """Publish this function's low end to the lane that can actually ship.

        Only for ``profile.eight08 is False`` — the 14 genres with no 808 lane
        of their own (house, techno, trance, hardstyle, edm, garage, dnb,
        dubstep, amapiano, funk, soul, rock, cinematic, grind). Their bassline
        used to be written to ``sub_808``, which
        ``midi_validate.sanitize_midi_mode`` deletes, so they generated with no
        low end and then failed ``enforce_midi_mode`` with "required lane is
        empty: 808_bass".

        Profiles with ``eight08`` keep writing ``sub_808`` exactly as before:
        later passes in humanize_pattern still read that lane for them, and
        ``_apply_808_bass_lane`` owns ``808_bass`` for the trap family.
        """
        tracks["sub_808"] = []
        if not notes:
            return
        tracks["808_bass"] = notes
        data["plg_bass808_lane_active"] = True
        data.setdefault("plg_kick808_source", source)
        logger.info(
            "Low end -> 808_bass: %s notes (%s, %s)", len(notes), profile.name, source
        )

    if profile.eight08 and kick:
        if should_replace_sub_808(existing, kick):
            bass = generate_sub_808(melody, kick, bars=bars, roots_pc=roots_pc)
        else:
            bass = mono_legato_bass(existing)
            if roots_pc and any(n.get("midi") for n in bass):
                # a real 808 line from the MIDI corpus: keep its rhythm/slides,
                # walk its bars onto the pinned progression
                bass = walk_bass_to_progression(bass, roots_pc)
        if profile.eight08_slides:
            bass = add_808_bar_slides(bass, bars=bars)
        bass = phase_align_kick_808(kick, bass)
        bass = apply_808_attack_flatten(bass, bpm)
        # Final Cut Itself AFTER clamp: slide stabs land under long sustains, and
        # clamp_sub_register_notes re-EXTENDS every note to MIN_SUB_LENGTH — a
        # mono pass done any earlier gets undone (25 overlapping 808s per song).
        # Glide (opt-in) tags every real root change BEFORE breath, so breath's
        # existing glide/slide exemption applies for free. Then breath: the 808
        # shuts up before the next hit so the pocket pumps (skipped on
        # glide-tagged boundaries). Then the chromatic walk-up into every drop
        # landing (Travis worlds), the register-lift accent (opt-in), and the
        # verse bow-stab switch-up (Ken/Carti worlds).
        bass = clamp_sub_register_notes(bass, lo=sub_lo, hi=sub_hi)
        # A too-close onset never survives mono/breath at >= MIN_SUB_LENGTH
        # regardless of which branch above produced it (fresh generation,
        # walked-to-progression, or phase-aligned to kick) — drop it once,
        # here, right before the pass that would otherwise clip it short.
        bass = filter_close_onsets(bass)
        bass = mono_legato_bass(bass)
        if getattr(profile, "sub_808_glide", False):
            bass = apply_sub_808_glide(bass)
        bass = apply_808_breath(bass)
        bass = add_808_ascent(bass, data)
        if getattr(profile, "sub_808_register_lift", False):
            bass = apply_808_register_lift(bass, data)
        final = apply_808_section_switch(bass, data)
        if is_hat_like_sub_808(final):
            logger.warning("808 lane still hat-like after regen — forcing sub_808_gen")
            rebuilt = clamp_sub_register_notes(
                generate_sub_808(melody, kick, bars=bars, roots_pc=roots_pc), lo=sub_lo, hi=sub_hi
            )
            rebuilt = filter_close_onsets(rebuilt)
            rebuilt = mono_legato_bass(rebuilt)
            if getattr(profile, "sub_808_glide", False):
                rebuilt = apply_sub_808_glide(rebuilt)
            final = apply_808_breath(rebuilt)
        tracks["sub_808"] = final
        return

    if existing:
        repaired = mono_legato_bass(existing)
        if roots_pc:
            repaired = walk_bass_to_progression(repaired, roots_pc)
        if kick:
            repaired = phase_align_kick_808(kick, repaired)
        repaired = apply_808_attack_flatten(repaired, bpm)
        # mono AFTER clamp — clamp re-extends lengths (see the branch above)
        repaired = filter_close_onsets(clamp_sub_register_notes(repaired, lo=sub_lo, hi=sub_hi))
        repaired = apply_808_breath(mono_legato_bass(repaired))
        if profile.eight08:
            tracks["sub_808"] = repaired
        else:
            _publish_low_end(repaired, source="genre_existing_bass")
    elif roots_pc:
        # Non-808 genre with an empty bass lane: synthesize an idiomatic
        # bassline from the pinned progression (off-beat house 8ths, funk
        # syncopation, reese sustains…) instead of shipping silence.
        #
        # 2026-08-11: this used to write to `sub_808`, which midi_validate
        # deletes, so all 14 non-808 genres (house, techno, trance, hardstyle,
        # edm, garage, dnb, dubstep, amapiano, funk, soul, rock, cinematic,
        # grind) shipped with no low end at all and then failed
        # enforce_midi_mode with "required lane is empty: 808_bass".
        from style_corpus import synthesize_bassline
        from drum_defaults import _bar_count as _bars_of

        synth = synthesize_bassline(profile.name, roots_pc, _bars_of(data))
        if profile.eight08:
            tracks["sub_808"] = synth
        else:
            _publish_low_end(synth, source="genre_bassline_cell")
    else:
        tracks["sub_808"] = []



def _resolve_progression_key(
    data: dict[str, Any], profile: GenreProfile, melody: list[dict[str, Any]]
) -> tuple[int, str]:
    """Root pc + scale for the current generation — same resolution
    _apply_808_bass_lane uses for the 808's own root. Factored out so any
    other lane that wants to land on the identical harmonic root (e.g.
    add_structural_accent_notes) stays in sync with the bass instead of
    re-deriving it and risking drift between the two."""
    from music_theory import detect_root_pc, detect_scale, key_from_midi_key_name
    from style_corpus import progression_roots_pc

    roots = progression_roots_pc(data) or []
    progression = data.get("plg_progression") if isinstance(data.get("plg_progression"), dict) else {}
    variety = data.get("plg_variety") if isinstance(data.get("plg_variety"), dict) else {}
    uploaded_scale_lock = data.get("plg_uploaded_scale_lock")
    uploaded_scale = (
        uploaded_scale_lock.get("scale")
        if isinstance(uploaded_scale_lock, dict)
        else None
    )
    key_hint = str(data.get("key") or variety.get("key") or "").strip()
    # 2026-07 fix: plg_progression["scale"] is pinned once, early, from
    # profile.melody_scale (a per-genre HARDCODED default — see
    # genre_profiles.py) — it used to win here even when the uploaded MIDI's
    # own scale was genuinely detected (midi_ingest.py, respect_uploaded_scale,
    # stamps plg_uploaded_scale_lock). That let 808/kick placement snap into
    # a scale the song isn't actually in (e.g. genre-default major/lydian
    # while the real upload is phrygian), producing pitch classes
    # _audit_kick808_symbiosis correctly flags as off_scale_808 — audit and
    # generation were each internally consistent with a DIFFERENT scale
    # source. The explicit uploaded-scale marker is real ground truth from
    # the user's own material and must win over a genre guess.
    scale_hint = str(
        uploaded_scale
        or progression.get("scale")
        or data.get("scale")
        or variety.get("scale")
        or ""
    ).strip().lower()
    # "A" + scale=minor → treat as Am so the root lands on A/C/E/F…
    key_for_parse = key_hint
    if key_hint and scale_hint in ("minor", "min", "natural_minor", "aeolian") and not key_hint.lower().endswith("m"):
        key_for_parse = f"{key_hint}m"
    elif key_hint and scale_hint in ("major", "maj") and key_hint.lower().endswith("m"):
        key_for_parse = key_hint[:-1]
    parsed_key = key_from_midi_key_name(key_for_parse) if key_for_parse else None
    if roots:
        # The 808 must follow the actual progression roots.  A stale key hint
        # from MIDI detection/variety can be a semitone away from the attached
        # progression; using it here snaps good roots into the wrong scale and
        # makes the low end feel out of tune.
        root_pc = int(roots[0]) % 12
        key_scale = None
    elif parsed_key:
        root_pc, key_scale = parsed_key
    else:
        root_pc = detect_root_pc(melody)
        key_scale = None
    raw_scale = str(
        scale_hint
        or key_scale
        or getattr(profile, "melody_scale", None)
        or detect_scale(melody, root_pc)
        or "natural_minor"
    )
    scale = {
        "minor": "natural_minor",
        "min": "natural_minor",
        "maj": "major",
        "aeolian": "natural_minor",
    }.get(raw_scale.lower(), raw_scale)
    return root_pc, scale


_STRUCTURAL_ACCENT_SECTIONS = ("hook", "drop")
_STRUCTURAL_ACCENT_LENGTH_BEATS = 1.0
_STRUCTURAL_ACCENT_VELOCITY = 118
_STRUCTURAL_ACCENT_COLLISION_WINDOW = 0.1


def add_structural_accent_notes(data: dict[str, Any], profile: GenreProfile) -> int:
    """One accent note at the FIRST ``hook`` landing and the FIRST ``drop``
    landing (plg_arrangement) — reinforces the section change for kick/808
    symbiosis without composing new melodic material.

    This is the only "enrichment" this engine adds beyond humanize: it never
    invents a new idea, never touches an existing note, and the pitch it
    picks is the SAME root _apply_808_bass_lane already plays there (via
    _resolve_progression_key) — an accent on harmony that's already true,
    snapped to the octave nearest wherever the melody itself was sitting
    just before the landing, so it reads as a continuation, not a new voice.
    Skips a landing entirely if melody_lead already has a note within
    ``_STRUCTURAL_ACCENT_COLLISION_WINDOW`` beats of it. Rare by design: at
    most 2 notes added per song, never a repeating pattern."""
    tracks = data.get("tracks")
    arrangement = data.get("plg_arrangement")
    if not isinstance(tracks, dict) or not isinstance(arrangement, list):
        return 0
    melody = tracks.get("melody_lead")
    if not isinstance(melody, list) or not melody:
        return 0

    root_pc, _scale = _resolve_progression_key(data, profile, melody)

    seen_sections: set[str] = set()
    landings: list[float] = []
    for section in sorted(
        (s for s in arrangement if isinstance(s, dict)),
        key=lambda s: int(s.get("start_bar", 0) or 0),
    ):
        name = str(section.get("name", ""))
        if name not in _STRUCTURAL_ACCENT_SECTIONS or name in seen_sections:
            continue
        seen_sections.add(name)
        try:
            start_bar = int(section.get("start_bar", 0))
        except (TypeError, ValueError):
            continue
        if start_bar <= 0:
            continue  # nothing precedes bar 0 to accent into
        landings.append(start_bar * BEATS_PER_BAR)

    if not landings:
        return 0

    ordered_melody = sorted((n for n in melody if isinstance(n, dict)), key=_note_step)
    existing_steps = [round(_note_step(n), 3) for n in ordered_melody]
    added: list[dict[str, Any]] = []
    for landing in sorted(landings):
        if any(abs(landing - t) < _STRUCTURAL_ACCENT_COLLISION_WINDOW for t in existing_steps):
            continue
        prior = [n for n in ordered_melody if _note_step(n) < landing]
        try:
            anchor_midi = parse_note_name(str(prior[-1]["note"])) if prior else 60
        except (KeyError, TypeError, ValueError):
            anchor_midi = 60
        octave = anchor_midi - (anchor_midi % 12)
        candidates = (octave + root_pc, octave + root_pc - 12, octave + root_pc + 12)
        pitch = min(candidates, key=lambda m: abs(m - anchor_midi))
        added.append({
            "time_step": round(landing, 5),
            "note": _midi_to_name(max(0, min(131, pitch))),
            "length": _STRUCTURAL_ACCENT_LENGTH_BEATS,
            "velocity": _STRUCTURAL_ACCENT_VELOCITY,
            "structural_accent": True,
        })
        existing_steps.append(round(landing, 3))

    if not added:
        return 0
    tracks["melody_lead"] = sorted(melody + added, key=_note_step)
    return len(added)


def _apply_808_bass_lane(
    data: dict[str, Any], profile: GenreProfile, bpm: float, *, pocket_kwargs: dict[str, Any] | None = None
) -> None:
    """Hip-hop kick/808 pocket — kick and 808 planned TOGETHER.

    2026-07: an LLM (GPT-4.1-mini, opt-in via GenreProfile.llm_kick808_enabled
    — see bass808_llm.py) gets first refusal at placing kick/808 with real
    musical judgement, after three-plus rounds of purely deterministic rules
    kept passing every invariant test but not landing on real listens. On
    ANY failure (disabled, no API key, timeout, bad JSON, degenerate output)
    it falls back to bass808_gen.build_kick_808_pocket — the deterministic
    engine — silently and instantly. That engine's own law (pitch follows
    the pinned progression, steps every 2 bars; density sparse by
    construction; kick/808 share one call-and-response decision per onset;
    rare point-like variation confined to bars 7-8 of each 8-bar loop) is
    documented on build_kick_808_pocket itself.

    Opt-in via GenreProfile.bass808_enabled. ``plg_bass808_lane_active`` is
    still set unconditionally as a dormant safety net: if a short/edge-case
    span genuinely yields zero notes, the (non-core) 808_bass channel stays
    visible in the export instead of silently vanishing (see
    flp_template.active_export_channels and friends).
    """
    if not getattr(profile, "bass808_enabled", False):
        return
    from bass808_gen import _BASS_VEL, build_kick_808_pocket
    from drum_defaults import _bar_count
    from style_corpus import progression_roots_pc

    tracks = data.setdefault("tracks", {})
    data["plg_bass808_lane_active"] = True
    kick = track_notes(data, "kick")
    bars = _bar_count(data)
    if kick:
        first = min(float(k.get("time_step", 0) or 0) for k in kick)
        start = float(int(round(first, 5) // 4.0) * 4.0)
    else:
        start = 0.0
    # Span from the drum window's own start to arrangement end — whatever
    # drum_blueprint put in "kick" upstream was always meant to be replaced
    # here (this family's kick belongs to this lane, not the generic
    # blueprint), so it's overwritten outright below, never merged.
    usable_bars = max(1, bars - int(start // 4.0))
    end = start + usable_bars * BEATS_PER_BAR

    melody = track_notes(data, "melody_lead")
    from melody_beat_symbiosis import extract_melody_accents

    raw_melody_accents = extract_melody_accents(melody)
    melody_accents = list(raw_melody_accents)
    data["plg_melody_accents"] = raw_melody_accents
    # How firmly the melody itself chose those instants, bar by bar. Built here
    # rather than earlier because it must score the SAME melody the accent map
    # above scored — passes between the ingest and this point still move notes,
    # and a firmness read off the ingested melody agrees with this one on only
    # about three quarters of Tragic's bars.
    from melody_beat_symbiosis import melody_accent_firmness

    accent_firmness = melody_accent_firmness(melody)
    data["plg_melody_accent_firmness"] = accent_firmness
    # Uploaded MIDI gets a phrase-level priority map before the pocket is
    # generated.  It does not replace the dense deterministic melody analysis:
    # it only tells kick/808 which *musical statements* deserve first refusal.
    # A weak/missing map deliberately falls back to the proven raw accents.
    beat_plan = data.get("plg_midi_beat_plan")
    if isinstance(beat_plan, dict) and beat_plan.get("enabled"):
        planned_accents = beat_plan.get("kick808_anchors")
        if isinstance(planned_accents, list) and planned_accents:
            phrase_anchor_accents = [float(time) for time in planned_accents]
            melody_accents = sorted(set(raw_melody_accents + phrase_anchor_accents))
            data["plg_kick808_phrase_anchors"] = phrase_anchor_accents
    phrase_roles = data.get("plg_phrase_rhythm_plan")
    role_span = (data.get("plg_midi_structure") or {}).get("span", {}) if isinstance(data.get("plg_midi_structure"), dict) else {}
    phrase_covers_song = (
        isinstance(phrase_roles, dict)
        and bool(phrase_roles.get("enabled"))
        and int(role_span.get("bars", 0) or 0) == bars
    )
    roots = progression_roots_pc(data) or []
    variety = data.get("plg_variety") if isinstance(data.get("plg_variety"), dict) else {}
    root_pc, scale = _resolve_progression_key(data, profile, melody)
    if roots:
        try:
            from music_theory import key_from_midi_key_name

            key_hint = str(data.get("key") or variety.get("key") or "").strip()
            scale_hint = str(data.get("scale") or variety.get("scale") or "").strip().lower()
            if key_hint and scale_hint in ("minor", "min", "natural_minor", "aeolian") and not key_hint.lower().endswith("m"):
                key_hint = f"{key_hint}m"
            parsed_key = key_from_midi_key_name(key_hint) if key_hint else None
            melody_root = detect_root_pc(melody)
            first_melody_pc = None
            if melody:
                try:
                    first_melody_pc = parse_note_name(str(sorted(melody, key=_note_step)[0].get("note"))) % 12
                except (TypeError, ValueError, KeyError):
                    first_melody_pc = None
            parsed_root = int(parsed_key[0]) % 12 if parsed_key else None
            target_root = None
            if parsed_root is not None and parsed_root in (melody_root, first_melody_pc):
                target_root = parsed_root
            if target_root is not None and int(roots[0]) % 12 != target_root:
                shift = target_root - int(roots[0]) % 12
                roots = [(int(pc) + shift) % 12 for pc in roots]
                root_pc = target_root
                data["plg_progression_transposed_to_midi_root"] = {
                    "from_root_pc": int((roots[0] - shift) % 12),
                    "to_root_pc": target_root,
                }
        except Exception as exc:  # noqa: BLE001
            logger.info("Progression/MIDI root alignment skipped (%s)", exc)
    seed = int(variety.get("seed") or data.get("bpm") or 0)
    clap = track_notes(data, "clap")
    clap_times = [float(n.get("time_step", 0) or 0) for n in clap]
    kick_template = kick[0] if kick else None

    kicks: list[dict[str, Any]] | None = None
    bass: list[dict[str, Any]] | None = None
    source = "deterministic"

    if phrase_covers_song:
        try:
            from midi_phrase_drums import build_phrase_kick_808_pocket

            from drum_density import density_card
            from midi_fullkit_director import bar_brief
            from midi_lowend_director import rhythmic_anchors

            director_meta: dict[str, Any] = {}
            phrase_result = build_phrase_kick_808_pocket(
                anchors=rhythmic_anchors(
                    hats=track_notes(data, "hi_hats"),
                    snare=track_notes(data, "snare"),
                    clap=track_notes(data, "clap"),
                    melody=melody,
                ),
                plan=phrase_roles,
                start=start,
                end=end,
                root_pc=root_pc,
                scale=scale,
                roots_pc=roots or [root_pc],
                kick_template=kick_template,
                melody_accents=melody_accents,
                melody_onsets=sorted(
                    float(n.get("time_step", 0.0) or 0.0)
                    for n in melody if isinstance(n, dict)
                ),
                melody_spans=[
                    (
                        float(n.get("time_step", 0.0) or 0.0),
                        float(n.get("time_step", 0.0) or 0.0) + float(n.get("length", 0.0) or 0.0),
                    )
                    for n in melody if isinstance(n, dict)
                ],
                accent_firmness=accent_firmness,
                director_meta=director_meta,
                pocket_mode=str((data.get("plg_midi_pocket_mode") or {}).get("mode") or "general"),
                harmonic_rhythm=_lowend_harmonic_windows(data),
                bpm=bpm,
                arrangement=(data.get("plg_arrangement") if isinstance(data.get("plg_arrangement"), list) else None),
                density=density_card(data),
                root_map=_lowend_root_map(data),
                # The shared per-bar contract, through its only sanctioned
                # accessor. The low end reads the phrase position from it the
                # same way the backbeat already does (2026-08-20).
                bar_briefs=bar_brief(data),
                **(pocket_kwargs or {}),
            )
            if director_meta:
                data["plg_midi_lowend_director"] = director_meta
        except Exception as exc:  # noqa: BLE001
            logger.info("MIDI phrase kick/808 pocket skipped (%s)", exc)
            phrase_result = None
        if phrase_result is not None:
            kicks, bass = phrase_result
            source = "midi_phrase_composer"

    if kicks is None and bass is None and KICK808_LLM_ALLOWED and getattr(profile, "llm_kick808_enabled", False):
        try:
            import bass808_llm

            arrangement = data.get("plg_arrangement")
            melody_energy = data.get("plg_melody_energy")
            section_density = (
                melody_energy.get("section_density")
                if isinstance(melody_energy, dict)
                else None
            )
            llm_result = bass808_llm.generate(
                start=start,
                end=end,
                bars=usable_bars,
                root_pc=root_pc,
                scale=scale,
                roots_pc=roots or [root_pc],
                genre=profile.name,
                bpm=bpm,
                clap_times=clap_times,
                kick_template=kick_template,
                arrangement=arrangement if isinstance(arrangement, list) else None,
                melody_energy=melody_energy if isinstance(melody_energy, dict) else None,
                section_density=section_density if isinstance(section_density, list) else None,
                melody_accents=melody_accents,
                allow_slides=False,
            )
        except Exception as exc:  # noqa: BLE001 — LLM path must never break generation
            logger.info("kick/808 LLM path skipped (%s)", exc)
            llm_result = None
        if llm_result is not None:
            kicks, bass = llm_result
            source = "llm"

    if kicks is None or bass is None:
        phrase_root_overrides = None
        if data.get("plg_midi_melody"):
            # Rare-by-design harmony follow — only meaningful against the user's
            # own uploaded material (see detect_phrase_root_overrides). A
            # composed melody already comes FROM this same progression, so
            # there's no independent signal to follow there.
            from melody_beat_symbiosis import detect_phrase_root_overrides

            phrase_root_overrides = detect_phrase_root_overrides(
                melody,
                start=start,
                bars=usable_bars,
                global_root_pc=root_pc,
                seed=seed,
            )

        kicks, bass = build_kick_808_pocket(
            start=start,
            bars=usable_bars,
            root_pc=root_pc,
            scale=scale,
            roots_pc=roots or [root_pc],
            clap_times=clap_times,
            kick_template=kick_template,
            seed=seed,
            phrase_root_overrides=phrase_root_overrides,
            melody_accents=melody_accents,
            allow_slides=False,
            arrangement=data.get("plg_arrangement"),
            bpm=bpm,
        )
        source = "deterministic"

    data["plg_kick808_source"] = source
    tracks["kick"] = kicks
    for note in bass:
        note["velocity"] = _BASS_VEL
        note["pan"] = 64
    tracks["808_bass"] = bass


def _outro_fade_window(data: dict[str, Any]) -> tuple[float | None, float | None]:
    """``plg_outro_fade`` as ``(start_beat, end_beat)``, or ``(None, None)``."""
    fade = data.get("plg_outro_fade")
    if not isinstance(fade, dict) or not fade.get("enabled"):
        return None, None
    return (
        float(fade.get("start_beat", -1.0)),
        float(fade.get("end_beat", -1.0)),
    )


def _section_velocity_scales_for(data: dict[str, Any]) -> list[tuple[float, float, float]]:
    """arranger's section-level scale for this pattern (``[]`` when there is none)."""
    from arranger import section_velocity_scales

    return section_velocity_scales(data.get("plg_arrangement"))


def _expected_kick808_velocity(
    note: dict[str, Any],
    lane: str,
    base: int,
    *,
    scales: list[tuple[float, float, float]],
    fade_start: float | None,
    fade_end: float | None,
) -> int:
    """The velocity a kick/808 note is supposed to carry, in one place.

    ONE owner for both sides of the contract, the same reason
    bass808_gen.kick_velocity_for exists: `_normalize_kick808_dynamics`
    writes this and `_audit_kick808_symbiosis` checks against it, and a
    second copy of the arithmetic would fail the whole generation on
    velocity_drift rather than drift quietly.

    Three terms, applied in this order:

    1. the lane's tuned base, with the quieter half of a deliberate double
       taken from `kick_velocity_for` (the one documented exception to "one
       tuned level per lane");
    2. the SECTION's level (arranger.section_velocity_scales) — added
       2026-08-26. Until then the tuned base WAS the final answer, which is
       why kick shipped two velocities and 808_bass exactly one across a
       whole two-minute song. "One tuned level per lane" is now "one tuned
       BASE per lane, one level per section around it", and the
       _KICK808_VELOCITY_JITTER tolerance below is measured against that
       base rather than against the bare constant;
    3. the outro fade, which now tapers from the section-scaled level rather
       than resetting to the bare lane constant. On the MIDI route this is a
       no-op that keeps the arithmetic honest: kick and 808 carry no notes at
       all in the outro (measured on all five corpus files).
    """
    from arranger import section_velocity_scale_at
    from bass808_gen import kick_velocity_for

    level = kick_velocity_for(note, base) if lane == "kick" else base
    step = _note_step(note)
    level = max(1, int(round(level * section_velocity_scale_at(scales, step))))
    if fade_start is not None and fade_end is not None and fade_end > fade_start and step >= fade_start:
        progress = min(1.0, max(0.0, (step - fade_start) / (fade_end - fade_start)))
        level = max(1, int(round(level * (1.0 - 0.82 * progress))))
    return level


def _normalize_kick808_dynamics(data: dict[str, Any], *, micro_humanize: bool = False) -> None:
    """Final invariant after every creative/post-processing pass.

    Placement belongs to the LLM/deterministic pocket engine; dynamics do
    not. Keeping one tuned base per lane prevents melody symbiosis or a
    stale LLM field from silently changing the tuned kick/808 balance.

    Since 2026-08-26 that base is scaled by the SECTION's level — see
    `_expected_kick808_velocity`, which is the one place the arithmetic
    lives and which `_audit_kick808_symbiosis` checks against. Re-run it
    after any late pass that ADDS a kick/808 note (`_vary_repeated_lowend_cycle`
    writes bass808_gen._BASS_VEL straight onto a new note); the function is
    idempotent, since every term is derived from the note's tags and time
    rather than from its current velocity.

    ``micro_humanize`` (GenreProfile.kick808_micro_humanize, 2026-07) adds a
    small deterministic +/- _KICK808_VELOCITY_JITTER dust on top of that
    fixed base — still one tuned average level per lane, just not perfectly
    flat. Keyed by (seed, lane, note time) via bass808_gen._rng, the same
    hash already used for kick/808 role decisions, so it never touches the
    shared stateful ``rng`` other passes draw from (no risk of shifting
    their random sequence) and is fully reproducible for a given seed.
    """
    from bass808_gen import _BASS_VEL, _KICK808_VELOCITY_JITTER, _KICK_VEL, _rng

    tracks = data.setdefault("tracks", {})
    fade_start, fade_end = _outro_fade_window(data)
    scales = _section_velocity_scales_for(data)
    seed = 0
    if micro_humanize:
        variety = data.get("plg_variety") if isinstance(data.get("plg_variety"), dict) else {}
        seed = int(variety.get("seed") or data.get("bpm") or 0)
    for lane, velocity in (("kick", _KICK_VEL), ("808_bass", _BASS_VEL)):
        notes = tracks.get(lane)
        if not isinstance(notes, list):
            continue
        for note in notes:
            if isinstance(note, dict):
                step = _note_step(note)
                base = _expected_kick808_velocity(
                    note, lane, velocity,
                    scales=scales, fade_start=fade_start, fade_end=fade_end,
                )
                if fade_start is not None and fade_end is not None and fade_end > fade_start and step >= fade_start:
                    note["outro_fade"] = True
                if micro_humanize:
                    jitter = round((_rng(seed, "kick808_vel", lane, step) - 0.5) * 2 * _KICK808_VELOCITY_JITTER)
                    base = max(1, min(127, base + jitter))
                note["velocity"] = base


_SECTION_DYNAMIC_LANES = ("clap", "snare", "snare_layer")


# --- Section melody arrangement -------------------------------------------
#
# The applier for `arranger.section_melody_plan`. That map owns HOW MUCH of the
# melody a section plays; this owns WHICH notes go — the same split as
# `section_velocity_scales` (how loud) against `_apply_section_dynamics` (apply
# it), and for the same reason: two copies of one musical decision is
# the project issue log (not included) 0.1.
#
# It removes and only removes. Every surviving note is the SAME dict object it
# received, untouched — no new note, no altered pitch, no moved onset. That is
# not a style preference, it is product decision 2026-07 (an explicit upload is
# finished material), and `tests/test_section_melody_arrangement.py` asserts it
# by object identity rather than by comparing values.
#
# ⚠️ Position is load-bearing. This runs at the very END of `humanize_pattern`,
# after `_seal_midi_kick808_pocket`, both kick/808 audits and
# `_correct_808_vertical_clashes`, so every drum owner has already read the
# FULL melody and committed. Moving this pass earlier moves the low end:
# measured on the same lane in the project issue log (not included) 0ab.8, perturbing `melody_lead`
# before `humanize_pattern` moves kick on three of five corpus files. The
# round's whole safety argument is that the drums cannot see this edit.
_MELODY_ONSET_TOL = 0.06        # half a 64th at 4/4 — clusters a jittered chord
_MELODY_MIN_SECTION_NOTES = 4   # below this there is nothing meaningful to thin


def _melody_is_layer(note: dict[str, Any]) -> bool:
    """Both octave-layer owners, never one of them — see the project issue log (not included) 0ab.8.

    `_develop_section._octave_lift` tags `switch_octave` on hook#1;
    `_extend_midi_octave_layer` tags `midi_sustained_octave_layer` on top.
    """
    return bool(note.get("switch_octave") or note.get("midi_sustained_octave_layer"))


def _melody_on_beat(note: dict[str, Any]) -> bool:
    within = _note_step(note) % 1.0
    return within <= _MELODY_ONSET_TOL or within >= 1.0 - _MELODY_ONSET_TOL


def _melody_bar_of(note: dict[str, Any]) -> int:
    return int(_note_step(note) // BEATS_PER_BAR)


def _melody_cut_tiers(base: list[dict[str, Any]]) -> tuple[list, list]:
    """The two note-level tiers, computed in draw order.

    A single fixed rule degenerates on half the corpus and the tiers exist for
    exactly that (measured per section, PLG_Beats_MelodyArr/probe_tier_budget):
    BatonRouge is monophonic, so "drop the inner voices" removes ZERO notes
    there; Tragic sits entirely on the beat, so "drop the offbeats" removes
    zero notes on THAT one. Neither file has both tiers empty, and the offbeat
    tier is computed on what the inner-voice tier leaves behind, because that
    is the order the draw runs in — reporting them independently would
    overstate the budget.
    """
    inner: list[dict[str, Any]] = []
    groups: list[list[dict[str, Any]]] = []
    for note in sorted(base, key=_note_step):
        if groups and _note_step(note) - _note_step(groups[-1][0]) <= _MELODY_ONSET_TOL:
            groups[-1].append(note)
        else:
            groups.append([note])
    for group in groups:
        if len(group) < 2:
            continue
        ranked = []
        for note in group:
            try:
                ranked.append((parse_note_name(str(note.get("note") or "")), note))
            except (TypeError, ValueError):
                continue
        if len(ranked) < 2:
            continue
        ranked.sort(key=lambda pair: pair[0])
        inner.extend(note for _midi, note in ranked[:-1])   # every voice but the top

    taken = {id(note) for note in inner}
    per_bar: dict[int, list[dict[str, Any]]] = {}
    for note in base:
        if id(note) not in taken:
            per_bar.setdefault(_melody_bar_of(note), []).append(note)
    offbeat: list[dict[str, Any]] = []
    for bar_notes in per_bar.values():
        candidates = [note for note in bar_notes if not _melody_on_beat(note)]
        if len(candidates) >= len(bar_notes):
            # A bar is emptied deliberately by the bar-rest tier or not at all,
            # so this one never takes a bar's last surviving note.
            candidates = candidates[:-1]
        offbeat.extend(candidates)
    return inner, offbeat


def _melody_spread_pick(
    candidates: list[dict[str, Any]], want: int, phase: int
) -> set[int]:
    """``want`` notes out of ``candidates``, spread across bars and rotated.

    Two decisions, both measured rather than assumed:

    * SPREAD, not "the first N". Taking a tier's first N notes puts the whole
      cut in the section's opening bars and leaves the rest playing the hook
      verbatim.
    * ROTATED by bar (and by the section's `phase`). Taking the same-ranked
      note out of every bar reproduces the loop one notch thinner — which on
      this corpus is the whole problem (the project issue log (not included) 0u.2: 1-4 distinct bar
      figures per song). Rotating lifted distinct bar figures on deyflo's
      verse from 10 to 16 of 16 bars, Shuttle's drop from 4 to 13, BatonRouge's
      verse from 6 to 11. Where a budget drains a tier outright (Sahara,
      Detroit) the removed set is determined and rotation cannot help; that is
      expected, not a failure.

    `phase` is what keeps drop#1 and drop#2 from being the same edit at the
    same size.
    """
    if want <= 0 or not candidates:
        return set()
    by_bar: dict[int, list[dict[str, Any]]] = {}
    for note in candidates:
        by_bar.setdefault(_melody_bar_of(note), []).append(note)
    for slot in by_bar.values():
        slot.sort(key=_note_step)
    bars = sorted(by_bar)
    picked: set[int] = set()
    lap = 0
    while len(picked) < want and any(by_bar[bar] for bar in bars):
        for index, bar in enumerate(bars):
            if len(picked) >= want:
                break
            slot = by_bar[bar]
            if slot:
                picked.add(id(slot.pop((index + lap + phase) % len(slot))))
        lap += 1
    return picked


def _melody_bar_rest_pick(
    base: list[dict[str, Any]], already: set[int], want: int
) -> set[int]:
    """The floor tier: whole bars of silence, cheapest first, never the last.

    Always has material, which is why it is last — every other tier can be
    empty on some file, this one cannot. It fires rarely in practice (only on
    the leanest sections of Sahara and Tragic in the stage-1 measurement).
    """
    if want <= 0:
        return set()
    survivors: dict[int, list[dict[str, Any]]] = {}
    for note in base:
        if id(note) not in already:
            survivors.setdefault(_melody_bar_of(note), []).append(note)
    picked: set[int] = set()
    ordered = sorted(survivors.items(), key=lambda item: (len(item[1]), item[0]))
    for _bar, notes in ordered[:-1]:      # a section never goes fully silent
        if len(picked) >= want:
            break
        picked.update(id(note) for note in notes)
    return picked


def _apply_section_melody_arrangement(data: dict[str, Any]) -> int:
    """Arrange the melody across sections by subtraction. Returns notes removed.

    Reads `arranger.section_melody_plan` — the single owner of how much a
    section plays — and spends that budget on two axes: the octave layer
    (register) and the section's own notes (space). No arrangement means no
    plan means no edit, so unit fixtures and the single-pattern route are
    untouched.

    Publishes `plg_melody_arrangement` so the result is checkable in
    pattern.json rather than only by ear, the same way `plg_section_events`
    made the change-point requirement data.
    """
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return 0
    melody = tracks.get("melody_lead")
    if not isinstance(melody, list) or not melody:
        return 0

    from arranger import section_melody_plan

    plans = section_melody_plan(data.get("plg_arrangement"))
    if not plans:
        return 0

    removed: set[int] = set()
    rows: list[dict[str, Any]] = []
    for start, end, plan in plans:
        section_notes = [
            note for note in melody
            if isinstance(note, dict) and start <= _note_step(note) < end
        ]
        if not section_notes:
            continue
        layer = [note for note in section_notes if _melody_is_layer(note)]
        base = [note for note in section_notes if not _melody_is_layer(note)]
        drawn = {"layer": 0, "inner_voice": 0, "offbeat": 0, "bar_rest": 0}

        layer_picked = _melody_spread_pick(
            layer, int(round(len(layer) * (1.0 - plan["layer_keep"]))), plan["phase"]
        )
        drawn["layer"] = len(layer_picked)

        base_picked: set[int] = set()
        if plan["base_cut"] > 0 and len(base) >= _MELODY_MIN_SECTION_NOTES:
            want = int(round(len(base) * plan["base_cut"]))
            inner, offbeat = _melody_cut_tiers(base)
            for key, tier in (("inner_voice", inner), ("offbeat", offbeat)):
                if len(base_picked) >= want:
                    break
                got = _melody_spread_pick(tier, want - len(base_picked), plan["phase"])
                base_picked |= got
                drawn[key] = len(got)
            if len(base_picked) < want:
                got = _melody_bar_rest_pick(base, base_picked, want - len(base_picked))
                base_picked |= got
                drawn["bar_rest"] = len(got)

        picked = layer_picked | base_picked
        # Structurally unreachable (base_cut is capped at 0.5 and the bar-rest
        # tier never takes the last sounding bar), but a silent section would
        # also lose its Playlist clip in flp_template._lane_section_windows and
        # an empty LANE hard-fails midi_validate.enforce_midi_mode — so it is
        # guarded rather than argued.
        if len(picked) >= len(section_notes):
            picked = set()
            drawn = {key: 0 for key in drawn}
        removed |= picked

        bars = max(1.0, (end - start) / BEATS_PER_BAR)
        kept_base = [note for note in base if id(note) not in picked]
        rows.append({
            "section": plan["section"],
            "occurrence": plan["occurrence"],
            "bars": int(bars),
            "layer_keep": plan["layer_keep"],
            "base_cut": plan["base_cut"],
            "notes_before": len(section_notes),
            "notes_after": len(section_notes) - len(picked),
            "layer_before": len(layer),
            "layer_after": len(layer) - drawn["layer"],
            "base_before": len(base),
            "base_after": len(kept_base),
            "drawn": drawn,
            "removed": len(picked),
            "per_bar_after": round((len(section_notes) - len(picked)) / bars, 2),
            "base_per_bar_after": round(len(kept_base) / bars, 2),
            "distinct_bar_figures": len({
                tuple(sorted(
                    (round((_note_step(note) - _melody_bar_of(note) * BEATS_PER_BAR) * 8) / 8.0,
                     str(note.get("note") or ""))
                    for note in group
                ))
                for group in _grouped_by_bar(kept_base).values()
            }),
            "sounding_bars": len(_grouped_by_bar(kept_base)),
        })

    kept = [note for note in melody if id(note) not in removed]
    if not kept:                       # never trade a hard export gate for a colour
        removed, kept = set(), melody
    if removed:
        tracks["melody_lead"] = kept

    hook = next((row for row in rows if row["section"] == "hook"), None)
    same_as_hook = []
    if hook and hook["per_bar_after"]:
        for row in rows:
            if row is hook or row["section"] in ("intro", "outro"):
                continue
            if abs(row["per_bar_after"] - hook["per_bar_after"]) / hook["per_bar_after"] < 0.10:
                same_as_hook.append("%s#%d" % (row["section"], row["occurrence"]))
    # Always the same shape, including the nothing-removed case: readers (and
    # tests/test_midi_enrichment_ungate.py, which uses it to prove no OTHER
    # owner touched the lane) must never have to branch on which keys exist.
    data["plg_melody_arrangement"] = {
        "removed": len(removed),
        "notes_before": len(melody),
        "notes_after": len(kept),
        "sections": rows,
        # A section within 10 % of the hook's density is one the plan failed to
        # differentiate. Recorded, not hidden — the same contract as
        # `plg_section_events`' meets_change_requirement.
        "sections_matching_hook": same_as_hook,
        "every_section_differs_from_the_hook": not same_as_hook,
    }
    return len(removed)


def _grouped_by_bar(notes: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    for note in notes:
        out.setdefault(_melody_bar_of(note), []).append(note)
    return out


def _apply_section_dynamics(data: dict[str, Any]) -> int:
    """Give the transient lanes and the melody's octave layer the section's level.

    The counterpart of `_normalize_kick808_dynamics` for the lanes that have
    no tuned constant of their own, reading the same single owner
    (arranger.section_velocity_scales) so there is one answer to "how loud is
    this section" and not four.

    Why this is subtraction and not a louder hook: measured on the five corpus
    files 2026-08-26, clap and snare sit at exactly 127 from the second verse
    through the second drop on three of them — `melody_beat_symbiosis.
    lock_drums_to_melody_accents` boosts every accent-aligned hit by +10 and
    clamps at 127, so the peak has nothing left to give. the project issue log (not included) 0u
    already recorded that conclusion for the kick. The verse comes down
    instead.

    ``hi_hats`` is deliberately NOT in the list. That lane has its own
    levelling chain — `apply_six_db_hat_rule` against the backbeat plus
    `drum_density.late_hat_velocity` for the four passes that append after it
    (the project issue log (not included) 0v.2/0v.8) — and a fifth multiplier on top is exactly the
    compounding those entries exist to prevent. Hat section behaviour is the
    deferred level-3 work, not this.

    Only the melody's OCTAVE LAYER is touched, never the lead itself: the
    doubling is arrangement thickness and is allowed to breathe with the
    section, while the lead's own dynamics belong to the melody route.

    Runs immediately before `_apply_outro_fade`, i.e. as the last dynamics
    writer before this file's "nothing after this point may alter note
    dynamics" boundary, so nothing refills what it takes away.
    """
    from arranger import section_velocity_scale_at

    scales = _section_velocity_scales_for(data)
    if not scales:
        return 0
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return 0

    def _scale(notes: Any, predicate: Any = None) -> int:
        if not isinstance(notes, list):
            return 0
        touched = 0
        for note in notes:
            if not isinstance(note, dict):
                continue
            if predicate is not None and not predicate(note):
                continue
            scale = section_velocity_scale_at(scales, _note_step(note))
            if scale >= 1.0:
                continue
            current = int(note.get("velocity", 0) or 0)
            if current <= 0:
                continue
            scaled = max(1, int(round(current * scale)))
            if scaled != current:
                note["velocity"] = scaled
                note["section_dynamics"] = True
                touched += 1
        return touched

    changed = 0
    for lane in _SECTION_DYNAMIC_LANES:
        changed += _scale(tracks.get(lane))
    changed += _scale(
        tracks.get("melody_lead"),
        lambda note: bool(note.get("switch_octave") or note.get("midi_sustained_octave_layer")),
    )
    if changed:
        # Its own top-level key, not plg_producer_meta: that dict is rebuilt
        # downstream on this route, so a counter written into it does not reach
        # the exported pattern and would read as "this pass never ran".
        data["plg_section_dynamics"] = {
            "notes": changed,
            "sections": [
                {"start_beat": start, "end_beat": end, "scale": round(scale, 4)}
                for start, end, scale in scales
            ],
        }
    return changed


def _apply_outro_fade(data: dict[str, Any]) -> int:
    """Write an audible four-bar fade into the final arranged notes.

    MIDI has no master-volume curve by itself, so the export needs a real note
    level ramp.  Kick/808 are handled by their final dynamics normalizer; all
    remaining musical/drum lanes taper here from their already-composed level.
    """
    fade = data.get("plg_outro_fade")
    if not isinstance(fade, dict) or not fade.get("enabled"):
        return 0
    try:
        start, end = float(fade["start_beat"]), float(fade["end_beat"])
    except (KeyError, TypeError, ValueError):
        return 0
    if end <= start:
        return 0
    changed = 0
    for lane, notes in (data.get("tracks") or {}).items():
        if lane in ("kick", "808_bass") or not isinstance(notes, list):
            continue
        for note in notes:
            if not isinstance(note, dict) or _note_step(note) < start:
                continue
            progress = min(1.0, max(0.0, (_note_step(note) - start) / (end - start)))
            note["velocity"] = max(1, int(round(int(note.get("velocity", 96) or 96) * (1.0 - 0.82 * progress))))
            note["outro_fade"] = True
            changed += 1
    if changed:
        fade["applied_notes"] = changed
    return changed


def _seal_song_note_bounds(data: dict[str, Any]) -> int:
    """Remove impossible post-end onsets and trim tails to the declared song.

    Late humanization and transition writers can move a final note past the
    arrangement endpoint.  Such a note cannot be represented by an FL
    Playlist clip without recreating the Snare Layer "145 bars" bug.  The
    arrangement length is authoritative, so the final export owns one hard
    half-open interval: ``0 <= onset < total_bars * 4``.
    """
    bars = data.get("plg_total_bars")
    if not isinstance(bars, int) or bars <= 0:
        return 0
    end = float(bars * BEATS_PER_BAR)
    changed = 0
    tracks = data.setdefault("tracks", {})
    for lane, raw_notes in list(tracks.items()):
        if not isinstance(raw_notes, list):
            continue
        sealed: list[Any] = []
        for raw in raw_notes:
            if not isinstance(raw, dict):
                sealed.append(raw)
                continue
            note = dict(raw)
            try:
                onset = float(note.get("time_step", 0.0) or 0.0)
                length = max(0.0, float(note.get("length", 0.0) or 0.0))
            except (TypeError, ValueError):
                changed += 1
                continue
            if onset < 0.0 or onset >= end:
                changed += 1
                continue
            if onset + length > end:
                note["length"] = round(max(0.01, end - onset), 5)
                changed += 1
            sealed.append(note)
        tracks[lane] = sealed
    data["plg_song_bounds_audit"] = {
        "passed": True,
        "end_beat": end,
        "repaired_notes": changed,
    }
    return changed


def _audit_kick808_symbiosis(data: dict[str, Any]) -> dict[str, Any]:
    """Measure the final exported lane after every creative/repair pass.

    This gate never rewrites a healthy groove. The caller runs the existing
    pair/dynamics repair immediately before it, so a failed audit is a real
    export failure rather than a harmless telemetry warning.
    """
    from bass808_gen import MAX_BASS_LENGTH, _BASS_VEL, _KICK808_VELOCITY_JITTER, _KICK_VEL, is_double_main, is_ghost_kick
    from drum_defaults import _bar_count
    from drum_density import density_card

    kicks = sorted(track_notes(data, "kick"), key=_note_step)
    bass = sorted(track_notes(data, "808_bass"), key=_note_step)
    bass_attacks = [note for note in bass if not bool(note.get("slide"))]
    bass_times = {round(_note_step(note), 3) for note in bass_attacks}
    kick_times = {round(_note_step(note), 3) for note in kicks}
    paired = sum(round(_note_step(kick), 3) in bass_times for kick in kicks)
    # A composer-tagged solo hit on a real anchor is call-and-response, not a
    # broken pair — it is excluded from both pairing measures rather than
    # repaired away (see _intentional_solo_lowend).
    anchor_times = _lowend_anchor_times(data)
    solo_kicks = sum(_intentional_solo_lowend(kick, "kick_only", anchor_times) for kick in kicks)
    extra_bass_attacks = sum(
        round(_note_step(note), 3) not in kick_times
        and not _intentional_solo_lowend(note, "bass_only", anchor_times)
        for note in bass_attacks
    )
    # The one deliberate gesture _vary_repeated_lowend_cycle is allowed to add
    # is not the stray slide this audit exists to reject.
    slides = sum(bool(note.get("slide")) and not note.get("variation_slide") for note in bass)
    lengths = [float(note.get("length", 0.0) or 0.0) for note in bass_attacks]
    fade_start, fade_end = _outro_fade_window(data)
    velocity_scales = _section_velocity_scales_for(data)
    # <= tolerance, not ==: GenreProfile.kick808_micro_humanize adds a small
    # +/- _KICK808_VELOCITY_JITTER dust on top of the base (see
    # _normalize_kick808_dynamics). When the flag is off the dust is zero,
    # so this stays exactly as strict as before for every other genre.
    #
    # The base itself is section-dependent since 2026-08-26. That is the
    # whole point of _expected_kick808_velocity being one shared function:
    # widening the tolerance instead would have let a real drift through.
    velocities_ok = all(
        abs(
            int(note.get("velocity", 0) or 0)
            - _expected_kick808_velocity(
                note, lane, velocity,
                scales=velocity_scales, fade_start=fade_start, fade_end=fade_end,
            )
        ) <= _KICK808_VELOCITY_JITTER
        for lane, velocity in (("kick", _KICK_VEL), ("808_bass", _BASS_VEL))
        for note in track_notes(data, lane)
    )
    lengths_ok = all(0.0 < length <= MAX_BASS_LENGTH + 1e-6 for length in lengths)

    # Pitch is not inferred from a screenshot or a fixed reference song: the
    # current pattern's pinned progression/key owns the accepted 808 scale.
    try:
        from music_theory import SCALES, parse_note_name
        from style_corpus import progression_roots_pc

        roots_for_audit = progression_roots_pc(data) or []
        harmonic_windows = _lowend_harmonic_windows(data)
        root_for_audit = int(roots_for_audit[0]) % 12 if roots_for_audit else detect_root_pc(track_notes(data, "melody_lead"))
        melody_for_audit = sorted(track_notes(data, "melody_lead"), key=_note_step)
        scale_name = _progression_scale_name(data)
        if scale_name in ("minor", "min", "aeolian"):
            scale_name = "natural_minor"
        intervals = SCALES.get(scale_name, SCALES["natural_minor"])
        root_map = _lowend_root_map(data)

        def _in_scale_at_note(note: dict[str, Any]) -> bool:
            """Measured against the SAME target generation was given.

            This used to carry its own window -> melody-bar -> progression
            fallback, which disagreed with both the generator's and the
            corrector's. It now reads _lowend_root_map, so a note the engine
            deliberately placed can no longer be rejected for landing on the
            root the engine was told to use. Same reason it reads ``tone_at``
            and not ``root_at`` (2026-08-16): a deliberate third or fifth in a
            colour slot is the engine's own note, not an off-root error.
            """
            step = _note_step(note)
            target, tier, _degree = root_map.tone_at(step)
            try:
                pc = parse_note_name(str(note.get("note") or "C2")) % 12
            except (TypeError, ValueError):
                return False
            if target is None:
                return True
            if tier != "progression":
                return pc == target
            # No material anywhere before this point: stay as lenient as this
            # audit has always been and accept anything in the bar's scale.
            return pc in {(target + interval) % 12 for interval in intervals}

        off_scale_bass = sum(not _in_scale_at_note(note) for note in bass_attacks)
    except Exception:  # defensive: a missing theory hint must not kill export
        off_scale_bass = 0

    accent_sources = []
    for key in ("plg_melody_accents", "plg_kick808_phrase_anchors"):
        values = data.get(key)
        if isinstance(values, list):
            accent_sources.extend(values)
    accents = sorted({
        round(float(t), 5)
        for t in accent_sources
        if isinstance(t, (int, float))
    })
    accent_hits = sum(
        any(abs(_note_step(kick) - accent) <= 1e-3 for accent in accents)
        for kick in kicks
    )
    bars = max(1, _bar_count(data))
    # Outro/intro may intentionally keep a kick transition while muting the
    # 808.  Low-end coverage must follow the shared kick+808 contract, not the
    # presence of a kick-only arrangement lane.
    lowend_end_beat = float(bars * BEATS_PER_BAR)
    arrangement = data.get("plg_arrangement")
    if isinstance(arrangement, list):
        active_lowend_ends = []
        for section in arrangement:
            if not isinstance(section, dict):
                continue
            active = section.get("active_tracks")
            if not isinstance(active, list) or "kick" not in active or not ({"808_bass", "sub_808"} & set(active)):
                continue
            start_bar = int(section.get("start_bar", 0) or 0)
            section_bars = int(section.get("bars", 0) or 0)
            active_lowend_ends.append((start_bar + section_bars) * BEATS_PER_BAR)
        if active_lowend_ends:
            lowend_end_beat = max(active_lowend_ends)
    last_kick = max((_note_step(note) for note in kicks), default=None)
    last_lowend = max((_note_step(note) for note in kicks + bass_attacks), default=None)
    coverage_complete = bool(
        last_lowend is not None and last_lowend >= max(0.0, lowend_end_beat - BEATS_PER_BAR)
    )
    active_window_counts: list[int] = []
    if isinstance(arrangement, list):
        for section in arrangement:
            if not isinstance(section, dict):
                continue
            active_tracks = section.get("active_tracks")
            if (
                not isinstance(active_tracks, list)
                or "kick" not in active_tracks
                or not ({"808_bass", "sub_808"} & set(active_tracks))
            ):
                continue
            sec_start = int(section.get("start_bar", 0) or 0)
            sec_bars = max(0, int(section.get("bars", 0) or 0))
            for window_bar in range(sec_start, sec_start + sec_bars, 4):
                window_end_bar = min(sec_start + sec_bars, window_bar + 4)
                # A deliberate double's ghost is the quieter half of ONE
                # onset — it does not spend this window's budget either.
                # (Note the reason emitted below is named
                # active_section_too_sparse but fires for the CEILING too.)
                active_window_counts.append(
                    sum(
                        window_bar * BEATS_PER_BAR
                        <= _note_step(kick)
                        < window_end_bar * BEATS_PER_BAR
                        for kick in kicks
                        if not is_ghost_kick(kick)
                    )
                )
    # The 3..8 window was a fixed contract that assumed every uploaded MIDI is
    # busy enough to carry it. The ceiling still is one — nothing may exceed
    # it — but the FLOOR now comes from the same density card generation was
    # given, so a deliberately sparse pocket is not rejected for being the
    # thing it was asked to be. Busy material resolves to the original 3.
    _density = density_card(data) or {}
    try:
        _window_floor = int(_density.get("min_pairs_per_4bar") or 3)
        _window_ceiling = int(_density.get("max_pairs_per_4bar") or 8)
    except (TypeError, ValueError):
        _window_floor, _window_ceiling = 3, 8
    active_density_ok = not active_window_counts or all(
        _window_floor <= count <= _window_ceiling for count in active_window_counts
    )
    kick_pairs = list(zip(kicks, kicks[1:]))
    kick_gaps = [_note_step(right) - _note_step(left) for left, right in kick_pairs]
    # A labelled half-beat hook answer is intentional punctuation, not the
    # accidental kick-roll/pulverised low end this audit rejects. The read
    # goes through bass808_gen.is_double_main: the tag alone is not enough —
    # a template-copied kick can leak the key without BEING the pair's main
    # hit, and the role check keeps such a leak inert (see bass808_gen).
    kick_rolls = sum(
        gap < 0.95 and not is_double_main(right)
        for gap, (_left, right) in zip(kick_gaps, kick_pairs)
    )
    # A deliberate double is ONE onset rendered as two hits, so its quieter
    # half does not spend the bar's ceiling — see bass808_gen's tags. The
    # kick_roll test above already exempts the pair; this is the second of
    # the three gates that had to learn the same thing.
    kicks_per_bar: dict[int, int] = {}
    for note in kicks:
        if is_ghost_kick(note):
            continue
        bar = max(0, int(_note_step(note) // BEATS_PER_BAR))
        kicks_per_bar[bar] = kicks_per_bar.get(bar, 0) + 1
    overfull_kick_bars = sum(count > 2 for count in kicks_per_bar.values())
    welded_kicks = max(0, len(kicks) - solo_kicks)
    pairing_ratio = paired / welded_kicks if welded_kicks else (0.0 if not kicks else 1.0)

    reasons: list[str] = []
    if not kicks:
        reasons.append("no_kicks")
    # Same band bass808_llm._call_response_valid already enforces at
    # generation time (0.15-0.85) — this final-export audit used to be
    # stricter (0.80) for no documented reason, which let a short/sparse
    # window (few onsets per bar, so the mandatory paired foundation hit
    # dominates) fail here even though it satisfied the actual generation
    # contract. Kept in sync with that constant rather than re-tuned
    # independently.
    # The kick and 808 are one skeleton: an audible 808 attack may never be
    # emitted alone.  The final pairing seal is responsible for repairing it.
    if kicks and pairing_ratio < 0.85:
        reasons.append("unbalanced_lowend_roles")
    if extra_bass_attacks:
        reasons.append("unpaired_808_onset")
    if kick_rolls:
        reasons.append("kick_roll")
    if overfull_kick_bars:
        reasons.append("too_many_kicks_per_bar")
    if not velocities_ok:
        reasons.append("velocity_drift")
    if not lengths_ok:
        reasons.append("invalid_808_length")
    if slides:
        reasons.append("unexpected_slide")
    if off_scale_bass:
        reasons.append("off_scale_808")
    if not coverage_complete:
        reasons.append("incomplete_kick_coverage")
    if not active_density_ok:
        reasons.append("active_section_too_sparse")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "kick_count": len(kicks),
        "bass_attack_count": len(bass_attacks),
        "paired_kicks": paired,
        "extra_bass_attacks": extra_bass_attacks,
        "solo_kicks": solo_kicks,
        "pairing_ratio": round(pairing_ratio, 4),
        "kick_rolls": kick_rolls,
        "overfull_kick_bars": overfull_kick_bars,
        "kick_velocity": _KICK_VEL,
        "bass_velocity": _BASS_VEL,
        "max_bass_length": round(max(lengths), 5) if lengths else 0.0,
        "slides": slides,
        "off_scale_bass": off_scale_bass,
        "melody_accents": len(accents),
        "kick_on_melody_accent": accent_hits,
        # PURE TELEMETRY — by decision, 2026-08-15. No code consumes this:
        # pulling kicks toward melody accents (even as a soft score) fights
        # the documented ``move_kick_timing=False`` decision for the 808
        # family — on that route kick timing belongs to the phrase pocket and
        # the seal's 1/16 grid, not to accent nudges. Making it a gate reason
        # is forbidden by the §0.1 history (a new hard reason here can reject
        # every generation through beat_quality._symbiosis_failures). If a
        # future change wants it to ACT, that decision must be revisited
        # explicitly and recorded in the owner map (not included).
        "melody_lock_ratio": round(accent_hits / len(kicks), 4) if kicks else 0.0,
        "last_kick_beat": round(last_kick, 5) if last_kick is not None else None,
        "coverage_complete": coverage_complete,
        "active_4bar_counts": active_window_counts,
        "active_density_ok": active_density_ok,
        "bars": bars,
    }


def _audit_midi_kick808_context(data: dict[str, Any]) -> dict[str, Any]:
    """Acceptance gate: the paired low-end must belong to the actual MIDI kit."""
    if not data.get("plg_midi_melody"):
        return {"passed": True, "skipped": True, "windows": []}

    from music_theory import parse_note_name
    from style_corpus import progression_roots_pc

    kicks = sorted(track_notes(data, "kick"), key=_note_step)
    bass = sorted(track_notes(data, "808_bass"), key=_note_step)
    melody = sorted(track_notes(data, "melody_lead"), key=_note_step)
    hats = sorted(track_notes(data, "hi_hats"), key=_note_step)
    phrase_bounds = sorted(track_notes(data, "snare") + track_notes(data, "clap"), key=_note_step)
    bass_by_time = {round(_note_step(note), 3): note for note in bass if not note.get("slide")}
    accents = {round(float(step), 3) for step in (data.get("plg_melody_accents") or []) if isinstance(step, (int, float))}
    from midi_lowend_director import in_hat_roll, rhythmic_anchors

    roll_times = rhythmic_anchors(hats=hats)["roll_times"]
    anchor_times = _lowend_anchor_times(data)

    harmonic = _lowend_harmonic_windows(data)
    roots = progression_roots_pc(data) or [0]
    # Same shared resolver generation and the off-scale corrector use, so a
    # bar with no confident window cannot mean one root here and another one
    # there — the divergence that made every sparse-MIDI generation fail.
    root_map = _lowend_root_map(data)

    def root_at(time: float) -> int:
        # tone_at, not root_at (2026-08-16): the map owns the degree too, and
        # this gate's 808_not_on_midi_root reason is a HARD fail — measuring
        # against a different method than the generator used would reject the
        # engine's own deliberate colour tone on every generation.
        pc, _tier, _degree = root_map.tone_at(time)
        if pc is not None:
            return int(pc) % 12
        bar = int(time // BEATS_PER_BAR)
        return int(roots[bar % len(roots)]) % 12

    windows: list[dict[str, Any]] = []
    signatures: list[tuple[float, ...]] = []
    max_step = max([_note_step(note) for note in kicks] or [0.0])
    for start in range(0, int(max_step // 16.0) * 16 + 1, 16):
        local_kicks = [note for note in kicks if start <= _note_step(note) < start + 16.0]
        if not local_kicks:
            continue
        root_errors = orphan = hat_collisions = support_hits = boundary_hits = 0
        for kick in local_kicks:
            time = _note_step(kick)
            bass_note = bass_by_time.get(round(time, 3))
            if bass_note is None:
                # A kick_only push on a real anchor is the composer letting an
                # already-ringing 808 breathe, not a missing answer.
                if not _intentional_solo_lowend(kick, "kick_only", anchor_times):
                    orphan += 1
                continue
            try:
                if parse_note_name(str(bass_note.get("note") or "C3")) % 12 != root_at(time):
                    root_errors += 1
            except (TypeError, ValueError):
                root_errors += 1
            is_accent = any(abs(time - accent) <= 0.25 for accent in accents)
            is_downbeat = time % BEATS_PER_BAR <= 0.13
            if in_hat_roll(time, roll_times) and not is_accent and not is_downbeat:
                hat_collisions += 1
            nearest_melody = min((abs(time - _note_step(note)) for note in melody), default=99.0)
            if is_accent or nearest_melody >= 0.35:
                support_hits += 1
            if any(abs(time - _note_step(note)) <= 0.25 for note in phrase_bounds):
                boundary_hits += 1
        signature = tuple(round(_note_step(note) - start, 2) for note in local_kicks)
        signatures.append(signature)
        windows.append({"start": start, "root_errors": root_errors, "orphan_attacks": orphan,
                        "hat_roll_collisions": hat_collisions, "support_hits": support_hits,
                        "phrase_boundary_hits": boundary_hits, "signature": signature})
    reasons: list[str] = []
    if any(row["root_errors"] for row in windows): reasons.append("808_not_on_midi_root")
    if any(row["orphan_attacks"] for row in windows): reasons.append("808_without_kick_attack")
    if any(row["hat_roll_collisions"] >= 2 for row in windows): reasons.append("kick_over_hat_roll")
    if len(signatures) >= 3 and len(set(signatures)) == 1: reasons.append("identical_4bar_cycle")
    # Which windows restate a 4-bar figure this song has already played.
    #
    # Deliberately NOT a reason (2026-08-20). ``identical_4bar_cycle`` is a
    # hard fail consumed by beat_quality._symbiosis_failures, so widening it
    # would turn a check that fires on nothing into one that fires constantly,
    # and any generation its single allowed repair did not fully clear would
    # be rejected outright — the the project issue log (not included) 0.1 failure class. This is a
    # meter that _vary_repeated_lowend_cycle acts on, on the same footing as
    # _correct_808_vertical_clashes: a repair and a measurement, never a gate.
    #
    # Measured on the listening corpus with the reason alone: 9-16 distinct
    # signatures per song, so it never fired once, while BatonRouge repeated
    # its bar figure every second bar 71 % of the time.
    seen: set[tuple[float, ...]] = set()
    repeated: list[int] = []
    for index, signature in enumerate(signatures):
        if signature in seen:
            repeated.append(index)
        else:
            seen.add(signature)
    return {"passed": not reasons, "reasons": reasons, "windows": windows,
            "window_count": len(windows), "distinct_4bar_signatures": len(set(signatures)),
            "repeated_blocks": repeated}


def _correct_off_scale_808_notes(data: dict[str, Any]) -> int:
    """Enforcement companion to _audit_kick808_symbiosis's off_scale_808
    check (2026-07): a flagged note used to just log a warning and ship to
    export unchanged. Re-snaps every flagged note onto the EXACT SAME target
    the audit itself checks against — a confident harmonic-rhythm window's
    own root when one covers the note, else the pinned progression's
    in-scale pitch classes for that bar (music_theory.snap_pc_to_scale,
    small +/- semitone nudge, never an octave jump) — so a note that was
    "correct" under generation's own (now-fixed, see
    _resolve_progression_key) scale source can no longer silently diverge
    from what the audit actually enforces. Called only when the audit still
    reports off_scale_808 after generation — expected to rarely fire once
    the root-cause scale fix is in place; this is the safety net, not the
    primary mechanism. Returns the number of notes corrected."""
    from music_theory import SCALES, snap_pc_to_scale
    from style_corpus import progression_roots_pc

    tracks = data.setdefault("tracks", {})
    bass = tracks.get("808_bass")
    if not isinstance(bass, list) or not bass:
        return 0

    # The window -> melody-bar -> carry-forward chain this used to implement
    # privately now lives in _lowend_root_map, which the generator and both
    # export audits read as well. That is the point: the correction target and
    # the audited target are the same value by construction, not by two
    # implementations happening to agree. Since 2026-08-16 that shared value is
    # tone_at (root, or the window's one planned chord tone) — reading root_at
    # here would make this "repair" strip every colour the generator placed.
    root_map = _lowend_root_map(data)

    corrected = 0
    for note in bass:
        if not isinstance(note, dict) or note.get("slide"):
            continue
        try:
            midi = parse_note_name(str(note.get("note") or "C2"))
        except (KeyError, TypeError, ValueError):
            continue
        step = _note_step(note)
        target_pc, _tier, _degree = root_map.tone_at(step)
        if target_pc is not None:
            if midi % 12 == target_pc:
                continue
            base = midi - (midi % 12)
            options = [base - 12 + target_pc, base + target_pc, base + 12 + target_pc]
            new_midi = min(options, key=lambda m: abs(m - midi))
        else:
            # No verified current/previous root: retain the old note instead
            # of inventing a scale pitch. The coverage gate may reject it,
            # but export must not silently manufacture harmony.
            continue
        if new_midi != midi:
            note["note"] = _midi_to_name(new_midi)
            corrected += 1
    return corrected


def _measure_808_voicing(data: dict[str, Any]) -> dict[str, Any]:
    """Telemetry for the 808's chord voicing (2026-08-16) — a METER, not a gate.

    Measures the notes as actually shipped, i.e. after the vertical consonance
    repair has had the last word, so ``attacks_colored`` is what the listener
    hears and not what the plan intended. Deliberately adds no reason to any
    audit: a new hard reason in the kick/808 gate risks rejecting every
    generation (the owner map (not included), "808 vertical consonance").
    """
    stats: dict[str, Any] = {
        "windows_total": 0, "colored_windows": 0, "third": 0, "fifth": 0,
        "attacks_total": 0, "attacks_planned_colored": 0, "attacks_colored": 0,
        "attacks_off_plan": 0,
    }
    try:
        root_map = _lowend_root_map(data)
    except Exception:  # noqa: BLE001 — telemetry must never break export
        data["plg_808_voicing"] = stats
        return stats
    slots = root_map.color_slots
    stats["windows_total"] = len(_lowend_harmonic_windows(data))
    stats["colored_windows"] = len(slots)
    for _start, _end, _pc, degree in slots:
        if degree in stats:
            stats[degree] += 1
    for note in track_notes(data, "808_bass"):
        if not isinstance(note, dict) or note.get("slide"):
            continue
        step = _note_step(note)
        planned, _tier, degree = root_map.tone_at(step)
        try:
            pc = parse_note_name(str(note.get("note") or "C3")) % 12
        except (KeyError, TypeError, ValueError):
            continue
        stats["attacks_total"] += 1
        if degree != "root":
            # What the plan asked for, whether or not it survived — the
            # difference between this and attacks_colored is exactly how much
            # the vertical repair overruled, which is the number worth
            # watching if colours ever stop being audible.
            stats["attacks_planned_colored"] += 1
        if planned is not None and pc != int(planned) % 12:
            # Almost always the vertical repair overriding the plan, which is
            # the documented precedence — worth counting, never worth failing.
            stats["attacks_off_plan"] += 1
        elif degree != "root":
            stats["attacks_colored"] += 1
    data["plg_808_voicing"] = stats
    return stats


def _correct_808_vertical_clashes(data: dict[str, Any]) -> dict[str, int]:
    """Vertical consonance repair for the 808 (2026-08-15) — the LAST owner
    of 808 pitch in MIDI mode.

    Every check above compares the 808 against the internal root map — "does
    the 808 match OUR model?". Before 2026-08-15 the model's root could be
    wrong (the filename key won over the actual notes), so both audits passed
    while the 808 sat a semitone off the sounding melody. This owner instead
    compares the 808 directly with the melody SOUNDING AT THE SAME TIME: for
    each 808 attack, the melody notes whose windows overlap the 808's own
    sounding window (an 808 rings for up to ~2.75 beats, and the clash is
    heard across the whole sustain, not only at the attack instant).

    A pitch-class interval of m2 / tritone / M7 ({1, 6, 11}) against any
    sounding melody note is a clash. A clashing note is rewritten to the
    nearest octave of a pitch class that is consonant with every sounding
    melody pc, preferring the current tone_at target, then that window's own
    root, then — when both are vetoed — the nearest pitch class the harmony
    ITSELF uses (``LowendRootMap.harmony_pcs``), and only if that set is empty
    the nearest consonant scale pc (deterministic tie-break: lower pc). Only
    the ``note`` field changes; time_step / velocity / length / slide are
    never touched.

    The harmony-first last resort landed 2026-08-20 (`the project issue log (not included)` §0t).
    Scale membership alone has no harmonic term, so a vetoed root fell through
    to "nearest legal note", which is how Dragon Orchestral's F# window shipped
    a G# bass 20 times. WHEN the repair fires is unchanged — same clash rule,
    same counts — only WHAT it chooses afterwards.

    Runs AFTER the kick/808 audits are computed (deliberate audit staleness,
    same precedent as _apply_kick808_micro_timing): the gates validate
    model-consistency, this owner validates the ear. Telemetry lands in
    ``plg_808_vertical``; this is a repair + meter, not a new hard gate.
    """
    stats = {
        "checked": 0,
        "clashes_found": 0,
        "repaired": 0,
        "unrepairable": 0,
        "skipped_slides": 0,
        # How often BOTH the planned tone and the window's root were vetoed and
        # the last resort had to choose (2026-08-20), and how often that choice
        # still landed on a pitch class the harmony never used. The second
        # counter is the one that was invisible: every gate passed green while
        # 39% of Dragon Orchestral's 808 sat off the harmonic map.
        "root_overruled": 0,
        "off_map_substitutions": 0,
    }
    tracks = data.get("tracks") if isinstance(data.get("tracks"), dict) else {}
    bass = tracks.get("808_bass")
    melody = track_notes(data, "melody_lead")
    if not isinstance(bass, list) or not bass or not melody:
        data["plg_808_vertical"] = stats
        return stats

    clash_intervals = (1, 6, 11)

    melody_windows = []
    for entry in melody:
        if not isinstance(entry, dict):
            continue
        try:
            pitch = parse_note_name(str(entry.get("note") or ""))
        except (KeyError, TypeError, ValueError):
            continue
        start = _note_step(entry)
        length = max(float(entry.get("length", 0.0) or 0.0), 0.05)
        melody_windows.append((start, start + length, pitch % 12))

    root_map = _lowend_root_map(data)
    scale_pcs = root_map.scale_pcs
    harmony_pcs = root_map.harmony_pcs

    def _min_interval(pc_a: int, pc_b: int) -> int:
        diff = abs(pc_a - pc_b) % 12
        return min(diff, 12 - diff)

    def _clashes_with(pc: int, sounding: list[int]) -> bool:
        return any(_min_interval(pc, mpc) in clash_intervals for mpc in sounding)

    for note in bass:
        if not isinstance(note, dict):
            continue
        if note.get("slide"):
            stats["skipped_slides"] += 1
            continue
        try:
            midi = parse_note_name(str(note.get("note") or "C2"))
        except (KeyError, TypeError, ValueError):
            continue
        start = _note_step(note)
        end = start + max(float(note.get("length", 0.0) or 0.0), 0.05)
        sounding = sorted({pc for m_start, m_end, pc in melody_windows if m_start < end and m_end > start})
        if not sounding:
            continue
        stats["checked"] += 1
        pc = midi % 12
        if not _clashes_with(pc, sounding):
            continue
        stats["clashes_found"] += 1
        target, _tier, degree = root_map.tone_at(start)
        # Preference order: the planned tone, then — when that plan was a
        # colour — the window's own ROOT, and only then the nearest consonant
        # scale pc (2026-08-16). Without the middle step a clashing third fell
        # straight through to "nearest to the note we are replacing", which can
        # be further from the harmony than the root this attack would have
        # taken anyway. The plan may be overruled; the harmony should not be.
        fallbacks: list[int] = []
        if target is not None:
            fallbacks.append(int(target) % 12)
        if degree != "root":
            root_pc, _root_tier = root_map.root_at(start)
            if root_pc is not None:
                fallbacks.append(int(root_pc) % 12)
        chosen = None
        for candidate in fallbacks:
            if not _clashes_with(candidate, sounding):
                chosen = candidate
                break
        if chosen is None:
            consonant = [
                candidate
                for candidate in sorted(scale_pcs)
                if not _clashes_with(candidate, sounding)
            ]
            # The last resort keeps its selector — nearest to the note being
            # replaced, tie-break on the lower pc — but no longer draws from
            # the whole key (2026-08-20). Scale membership says "this note is
            # legal here"; it does not say the harmony ever chose it, and the
            # difference is audible: on Dragon Orchestral the window's own F#
            # root was vetoed by one passing F and shipped as G#, a whole tone
            # off the chord, 20 times. Narrowing the pool to harmony_pcs can
            # only move attacks that currently land off the map — the pool is
            # a subset, so anything already on-map stays where it is
            # (measured: deyflo / Baton Rouge / Graveyard byte-identical).
            # An empty pool means no confident window, and the old full-scale
            # behaviour stands rather than refusing to repair.
            on_map = [
                candidate for candidate in consonant if candidate in harmony_pcs
            ]
            pool = on_map or consonant
            if pool:
                chosen = min(pool, key=lambda candidate: (_min_interval(candidate, pc), candidate))
                stats["root_overruled"] += 1
                if chosen not in harmony_pcs and harmony_pcs:
                    stats["off_map_substitutions"] += 1
        if chosen is None:
            stats["unrepairable"] += 1
            continue
        if chosen == pc:
            continue
        base = midi - pc
        options = [base - 12 + chosen, base + chosen, base + 12 + chosen]
        new_midi = min(options, key=lambda m: abs(m - midi))
        if new_midi != midi:
            note["note"] = _midi_to_name(new_midi)
            stats["repaired"] += 1
    data["plg_808_vertical"] = stats
    return stats


def _vary_repeated_lowend_cycle(data: dict[str, Any], context_audit: dict[str, Any]) -> int:
    """Enforcement companion to _audit_midi_kick808_context's
    identical_4bar_cycle check.

    A low end that tiles one verbatim 4-bar block across the whole song reads
    as a loop, not a performance — the gate caught it but nothing acted on it,
    so it shipped. This varies every SECOND block only (A A' A A'): the
    statement is left exactly as composed and only its answer is altered, so
    the pocket keeps its identity instead of turning into constant churn.

    The variation is deliberately the smallest one a producer would make by
    hand: drop one non-essential kick (never a downbeat, never a kick sitting
    on a melody accent, never the block's own first hit, and never from an
    already-sparse block), and let the 808 slide up into the next block at the
    very end of the bar. The dropped kick's paired 808 goes with it so the
    pairing contract survives untouched.

    This is the ONLY path that may emit a slide — bass808_gen's variation
    toolbox stays disabled, and both pairing repairs still strip every other
    slide. Tagged ``variation_slide`` so _audit_kick808_symbiosis can tell this
    one deliberate gesture from the stray slides it still rejects. Returns the
    number of blocks varied.
    """
    windows = [row for row in (context_audit.get("windows") or []) if isinstance(row, dict)]
    if len(windows) < 2:
        return 0
    # Which blocks to vary. The audit's own measurement of what actually
    # repeats (2026-08-20), falling back to the old blind every-second-window
    # parity only when a caller hands us an audit without it. Parity was the
    # wrong question: A A' A A' is a gesture about a figure being restated, and
    # window 3 being odd says nothing about whether window 3 repeats anything.
    raw = context_audit.get("repeated_blocks")
    if isinstance(raw, list):
        targets = {int(index) for index in raw if isinstance(index, int)}
    else:
        targets = {index for index in range(len(windows)) if index % 2}
    if not targets:
        return 0

    from bass808_gen import BASS_SUSTAIN_LENGTH, LOWEND_808_LO, MIN_BASS_LENGTH, _BASS_VEL
    from music_theory import name_for_midi, parse_note_name

    tracks = data.setdefault("tracks", {})
    kicks = sorted(track_notes(data, "kick"), key=_note_step)
    bass = sorted(track_notes(data, "808_bass"), key=_note_step)
    if not kicks or not bass:
        return 0
    accents = {
        round(float(step), 5)
        for key in ("plg_melody_accents", "plg_kick808_phrase_anchors")
        for step in (data.get(key) or [])
        if isinstance(step, (int, float))
    }
    harmonic = _lowend_harmonic_windows(data)

    def _root_pc_at(time: float) -> int | None:
        for window in harmonic:
            try:
                if (
                    float(window["start"]) <= time < float(window["end"])
                    and float(window.get("confidence", 0.0) or 0.0) >= 0.48
                ):
                    return int(window["root_pc"]) % 12
            except (KeyError, TypeError, ValueError):
                continue
        return None

    # The fourth owner that had to learn the double-hit tag. Until 2026-08-20
    # this pass could not run at all (its reason never fired), so it never met
    # a ghost; taking one half of a deliberate double would leave the other
    # half sounding like a stray. Read through bass808_gen, never by testing
    # the raw key — see PROJECT_MAP 4.1.
    from bass808_gen import is_double_member

    varied = 0
    for index, window in enumerate(windows):
        if index not in targets:
            continue
        try:
            start = float(window["start"])
        except (KeyError, TypeError, ValueError):
            continue
        end = start + 16.0
        local = [note for note in kicks if start <= _note_step(note) < end]
        # Thinning an already-sparse block would trip the audit's own
        # active_section_too_sparse floor (3 kicks per active 4 bars).
        if len(local) < 4:
            continue
        candidates = [
            note for note in local[1:]
            if abs(_note_step(note) % BEATS_PER_BAR) > 1e-5
            and not is_double_member(note)
            and not any(abs(_note_step(note) - accent) <= 0.13 for accent in accents)
        ]
        if not candidates:
            continue
        victim = candidates[-1]
        victim_key = round(_note_step(victim), 3)
        kicks = [note for note in kicks if note is not victim]
        bass = [note for note in bass if round(_note_step(note), 3) != victim_key]
        varied += 1

        # The slide leans into the next block's root, in the last half-beat,
        # and never on top of a kick attack (it is a bend, not a new hit).
        slide_time = round(end - 0.5, 5)
        target_pc = _root_pc_at(end) or _root_pc_at(slide_time)
        sounding = [note for note in bass if _note_step(note) < slide_time and not note.get("slide")]
        if target_pc is None or not sounding:
            continue
        if any(abs(_note_step(note) - slide_time) < 0.25 for note in kicks):
            continue
        if any(abs(_note_step(note) - slide_time) < 0.25 for note in bass):
            continue
        try:
            if parse_note_name(str(sounding[-1].get("note") or "C3")) % 12 == target_pc:
                continue  # nothing to slide to
        except (TypeError, ValueError):
            continue
        previous = sounding[-1]
        # A portamento bends a tail that is STILL SOUNDING. `sounding` above
        # only proves the note started before the slide, and the length line
        # here used to be a `min`, so it could only ever shorten the previous
        # note — never carry it up to the slide. When the tail had already
        # died (the ordinary case: 2.45 beats of note against a 3.5-beat
        # reach) FL had nothing to glide from, retriggered the sample, and
        # cut it mid-body 0.5 beats later. Measured on Sahara, 2026-08-25:
        # four such stubs, each an 808 attack with no kick under it, heard as
        # a noise burst. Carry the tail to the slide, bounded by the lane's
        # own sustain ceiling, and emit nothing at all when it cannot reach —
        # a bend with no tail behind it is a click, not a gesture.
        reach = round(slide_time - _note_step(previous), 5)
        if not MIN_BASS_LENGTH <= reach <= BASS_SUSTAIN_LENGTH:
            continue
        previous["length"] = reach
        bass.append({
            "time_step": slide_time,
            "note": name_for_midi(LOWEND_808_LO + target_pc),
            "length": round(min(BASS_SUSTAIN_LENGTH, 0.5), 5),
            "velocity": _BASS_VEL,
            "pan": 64,
            "slide": True,
            "variation_slide": True,
        })
        bass.sort(key=_note_step)

    if varied:
        tracks["kick"] = sorted(kicks, key=_note_step)
        tracks["808_bass"] = sorted(bass, key=_note_step)
    return varied


def _clear_kicks_from_hat_rolls(data: dict[str, Any]) -> int:
    """Drop a kick buried inside a hi-hat roll, taking its 808 answer with it.

    Hats own the pulse: a kick landing mid-roll is masked by it and reads as a
    stumble. _seal_midi_kick808_pocket already applies this rule, so anything
    still colliding here was placed by a later pass. A downbeat or a kick
    sitting on a melody accent is kept — those carry their own meaning — and a
    block already at the density floor (3 kicks per active 4 bars) is left
    alone rather than thinned into a hole. Returns the number removed.
    """
    from bass808_gen import is_double_member
    from midi_lowend_director import rhythmic_anchors, roll_take_verdict

    tracks = data.setdefault("tracks", {})
    kicks = sorted(track_notes(data, "kick"), key=_note_step)
    bass = sorted(track_notes(data, "808_bass"), key=_note_step)
    if not kicks:
        return 0
    roll_times = rhythmic_anchors(hats=track_notes(data, "hi_hats"))["roll_times"]
    if not roll_times:
        return 0
    accents = {
        round(float(step), 5)
        for key in ("plg_melody_accents", "plg_kick808_phrase_anchors")
        for step in (data.get(key) or [])
        if isinstance(step, (int, float))
    }
    # The "3 kicks per 4-bar window" floor is a recorded law with its own test
    # and it stands unchanged — see drum_density.ROLL_THINNING_FLOOR_PER_4BAR.
    # What changed is that busy material may now RAISE it: measured on
    # Graveyard the law's 3 never binds (real windows carry 5-8) while the
    # card asks for 6.7, so the roll could thin the low end well below what
    # the material implies without ever touching the law. Same floor is now
    # applied in _seal_midi_kick808_pocket, which had none at all.
    from drum_density import density_card, min_kicks_per_4bar

    window_floor = min_kicks_per_4bar(density_card(data))
    per_window: dict[int, int] = {}
    for note in kicks:
        per_window[int(_note_step(note) // 16.0)] = per_window.get(int(_note_step(note) // 16.0), 0) + 1

    # Same owner as the seal — two writers of one rule is exactly §0.1.
    kicks_left_in_bar: dict[int, int] = {}
    for note in kicks:
        bar = int(_note_step(note) // BEATS_PER_BAR)
        kicks_left_in_bar[bar] = kicks_left_in_bar.get(bar, 0) + 1

    removed: list[float] = []
    kept: list[dict[str, Any]] = []
    for note in kicks:
        step = _note_step(note)
        window = int(step // 16.0)
        bar = int(step // BEATS_PER_BAR)
        if is_double_member(note):
            # Deleting one half of a deliberate double leaves a stray hit,
            # which is worse than a kick a roll happens to cover.
            kept.append(note)
            continue
        verdict = roll_take_verdict(
            step,
            roll_times=roll_times,
            accents=accents,
            kicks_in_window=per_window.get(window, 0),
            min_kicks_in_window=window_floor,
            beats_per_bar=BEATS_PER_BAR,
        )
        collides = verdict == "take"
        if verdict == "density_floor":
            note["lowend_roll_priority"] = True
        if collides:
            kicks_left_in_bar[bar] = kicks_left_in_bar.get(bar, 0) - 1
            per_window[window] -= 1
            removed.append(round(step, 3))
            continue
        kept.append(note)
    if not removed:
        return 0
    tracks["kick"] = kept
    tracks["808_bass"] = [note for note in bass if round(_note_step(note), 3) not in removed]
    return len(removed)


def _cap_midi_kicks_per_bar(data: dict[str, Any], *, maximum: int = 2) -> int:
    """Keep the MIDI kick/808 skeleton inside its stated density contract.

    Late phrase helpers can add a legitimate paired attack after the low-end
    seal.  Rather than letting the final audit reject the whole 72-bar song,
    retain the two strongest attacks in an overfull bar and remove only their
    paired 808 notes.  This is a repair, never a second kick generator.
    """
    tracks = data.setdefault("tracks", {})
    kicks = sorted(track_notes(data, "kick"), key=_note_step)
    bass = sorted(track_notes(data, "808_bass"), key=_note_step)
    if not kicks:
        return 0

    accents = {
        round(float(step), 3)
        for key in ("plg_melody_accents", "plg_kick808_phrase_anchors")
        for step in (data.get(key) or [])
        if isinstance(step, (int, float))
    }
    # The third gate that had to learn the double-hit tag: a ghost is the
    # quieter half of ONE onset and must not push its bar over the ceiling,
    # or this repair would delete the very hit the generator meant to add.
    from bass808_gen import is_ghost_kick

    by_bar: dict[int, list[dict[str, Any]]] = {}
    for note in kicks:
        by_bar.setdefault(max(0, int(_note_step(note) // BEATS_PER_BAR)), []).append(note)

    removed: set[float] = set()
    for notes in by_bar.values():
        if sum(1 for note in notes if not is_ghost_kick(note)) <= maximum:
            continue
        ranked = sorted(
            notes,
            key=lambda note: (
                0 if abs(_note_step(note) % BEATS_PER_BAR) <= 1e-5 else 1,
                0 if round(_note_step(note), 3) in accents else 1,
                _note_step(note),
            ),
        )
        removed.update(round(_note_step(note), 3) for note in ranked[maximum:])

    if not removed:
        return 0
    tracks["kick"] = [note for note in kicks if round(_note_step(note), 3) not in removed]
    tracks["808_bass"] = [note for note in bass if round(_note_step(note), 3) not in removed]
    return len(removed)


def _repair_midi_kick808_context(data: dict[str, Any], context_audit: dict[str, Any]) -> int:
    """One bounded repair attempt per failing _audit_midi_kick808_context reason.

    These checks used to be measured, logged and shipped anyway. Each reason
    now has an enforcement companion, mirroring how off_scale_808 has always
    been paired with _correct_off_scale_808_notes.

    Order matters: pairing is restored first, then masked/off-root notes are
    fixed against the repaired skeleton, and the cycle variation runs LAST
    because it is the only pass allowed to leave a slide behind — the pairing
    repair strips every slide it sees, so running it afterwards would undo it.
    """
    reasons = set(context_audit.get("reasons") or [])
    repaired = 0
    if "808_without_kick_attack" in reasons and _ensure_808_on_every_kick(data):
        repaired += 1
    if "kick_over_hat_roll" in reasons:
        repaired += _clear_kicks_from_hat_rolls(data)
    if "808_not_on_midi_root" in reasons:
        repaired += _correct_off_scale_808_notes(data)
    if "identical_4bar_cycle" in reasons:
        repaired += _vary_repeated_lowend_cycle(data, context_audit)
    return repaired


def _section_beat_ranges(arrangement: Any) -> list[tuple[float, float, str]]:
    """``plg_arrangement`` (arranger.arrange_song's per-occurrence section
    list) as sorted ``(start_beat, end_beat, name)`` ranges. Used to key the
    kick/808 micro-timing dust by position-within-section instead of
    position-in-song — see _kick808_timing_key for why."""
    ranges: list[tuple[float, float, str]] = []
    if not isinstance(arrangement, list):
        return ranges
    for section in arrangement:
        if not isinstance(section, dict):
            continue
        try:
            start_bar = float(section.get("start_bar", 0) or 0)
            bars = float(section.get("bars", 0) or 0)
        except (TypeError, ValueError):
            continue
        start_beat = start_bar * BEATS_PER_BAR
        ranges.append((start_beat, start_beat + bars * BEATS_PER_BAR, str(section.get("name") or "")))
    ranges.sort(key=lambda r: r[0])
    return ranges


def _kick808_timing_key(step: float, ranges: list[tuple[float, float, str]]) -> tuple[Any, ...]:
    """RNG key for one onset's micro-timing dust.

    Two occurrences of the same section (e.g. hook #1 and hook #2) tile the
    identical relative kick/808 pattern from arrange_song — but keying dust
    by absolute song position gave each occurrence different jitter, which
    made them byte-different and defeated flp_template._apply_pattern_bank's
    exact-content pattern reuse (every repeat minted a new "808 Bass Main N"
    pattern even though the loop never actually changed). Keying by
    (section name, position relative to that section's start) instead means
    true repeats get identical dust and dedupe naturally, while any section
    whose content genuinely differs (final chorus, evolved verse) already
    has different relative content and keeps independent dust.

    Falls back to the bare absolute step (today's exact key) when no
    ``plg_arrangement`` range contains the note — e.g. direct-call test
    fixtures that build a track without running arrange_song — so those
    callers see unchanged jitter values.
    """
    for start_beat, end_beat, name in ranges:
        # Half-open [start, end): tolerate float noise at the START of a
        # range only. Sections are contiguous (one's end == the next one's
        # start), so tolerating +1e-6 at BOTH ends made a note sitting
        # exactly on a boundary match whichever range came first in the
        # list — silently keying the first note of "verse" off the tail end
        # of "hook" instead of its own section's start.
        if start_beat - 1e-6 <= step < end_beat - 1e-6:
            return (name, round(step - start_beat, 5))
    return (step,)


def _apply_kick808_micro_timing(data: dict[str, Any], bpm: float) -> None:
    """GenreProfile.kick808_micro_humanize opt-in: a few ms of deterministic
    timing dust on kick/808_bass onsets, matching the CLASS of micro-timing
    hi_hats/snare/clap already get (apply_hi_hat_swing, apply_pre_snare_shift)
    without touching where those onsets land structurally.

    Must run strictly AFTER _audit_kick808_symbiosis (see the call site in
    humanize_pattern) — that audit's pairing_ratio/kick_rolls/coverage/
    density checks all read exact onset times, and this function is the
    only place in the whole kick/808 pipeline that perturbs them. Running
    after the audit means it validates the clean, un-jittered grid exactly
    as it always has; this is a purely cosmetic layer on top that no gate
    ever sees.

    Keyed by (seed, section-relative onset key) via bass808_gen._rng — see
    _kick808_timing_key. Deliberately NOT by lane: a paired kick+808 hit
    shares one exact time_step by construction (see build_kick_808_pocket /
    bass808_llm.generate / build_phrase_kick_808_pocket), and since both
    notes of a pair sit in the same section at the same relative offset,
    keying on that alone gives both notes of a pair the identical shift for
    free — they stay exactly simultaneous, no separate pairing lookup
    needed. A kick-only or bass-only onset's key is unique to that note (or
    shared with its own repeats in other occurrences of the same section),
    so it gets its own independent shift.

    Clamped to never cross into a neighboring bar (2026-07 fix: a kick
    landing exactly on a bar line — e.g. the first hit after a transition
    fill — carries structural meaning several other passes read via plain
    bar-bucketing, such as test_transition_fills' "kick clears the fill bar"
    contract or _audit_kick808_symbiosis's own kicks_per_bar. A few ms of
    backward jitter could otherwise walk that note across the line into the
    previous bar and silently break whatever depended on it staying there.
    Musically inaudible restriction — the jitter magnitude is a handful of
    ms, so this only ever actually engages for a note already within a few
    ms of its bar's edge.
    """
    from bass808_gen import _KICK808_TIMING_JITTER_MS, _rng
    from drum_defaults import _bar_count

    tracks = data.setdefault("tracks", {})
    variety = data.get("plg_variety") if isinstance(data.get("plg_variety"), dict) else {}
    seed = int(variety.get("seed") or data.get("bpm") or 0)
    declared_bars = data.get("plg_total_bars")
    bars = int(declared_bars) if isinstance(declared_bars, int) and declared_bars > 0 else _bar_count(data)
    song_end = max(0.0, bars * BEATS_PER_BAR - 0.01)
    lo, hi = _KICK808_TIMING_JITTER_MS
    span = hi - lo
    section_ranges = _section_beat_ranges(data.get("plg_arrangement"))
    for lane in ("kick", "808_bass"):
        notes = tracks.get(lane)
        if not isinstance(notes, list):
            continue
        for note in notes:
            # Slide destinations encode a deliberate phrase-boundary timing
            # (see bass808_gen._apply_slide) — left untouched, same exemption
            # apply_808_breath already gives glide/slide notes elsewhere.
            if not isinstance(note, dict) or note.get("slide"):
                continue
            step = _note_step(note)
            key = _kick808_timing_key(step, section_ranges)
            ms = lo + _rng(seed, "kick808_timing_mag", *key) * span
            sign = 1.0 if _rng(seed, "kick808_timing_sign", *key) >= 0.5 else -1.0
            shift = ms_to_beats(ms, bpm) * sign
            bar_start = math.floor(step / BEATS_PER_BAR) * BEATS_PER_BAR
            bar_end = bar_start + BEATS_PER_BAR - 1e-4
            new_step = max(bar_start, min(bar_end, step + shift))
            note["time_step"] = round(max(0.0, min(song_end, new_step)), 5)


def _audit_hat_symbiosis(data: dict[str, Any]) -> dict[str, Any]:
    """Final section-aware hat density report for uploaded-MIDI beats."""
    hats = sorted(track_notes(data, "hi_hats"), key=_note_step)
    energy = data.get("plg_melody_energy")
    energy = energy if isinstance(energy, dict) else {}
    pace = str(energy.get("pace") or "pocket")
    attack = str(energy.get("attack") or "neutral")
    busyness = float(energy.get("busyness", 0.0) or 0.0)
    racing = pace == "racing" or (attack == "sharp" and busyness >= 0.65)
    midi_owned = bool(data.get("plg_midi_hats"))
    from drum_density import density_card

    try:
        hat_scale = float((density_card(data) or {}).get("hat_multiplier") or 1.0)
    except (TypeError, ValueError):
        hat_scale = 1.0
    hat_scale = max(0.5, min(1.0, hat_scale))

    rows: list[dict[str, Any]] = []
    reasons: list[str] = []
    arrangement = data.get("plg_arrangement")
    if isinstance(arrangement, list):
        for section in arrangement:
            if not isinstance(section, dict):
                continue
            name = str(section.get("name") or "")
            start_bar = int(section.get("start_bar", 0) or 0)
            section_bars = max(0, int(section.get("bars", 0) or 0))
            active = section.get("active_tracks")
            active_hat_section = not isinstance(active, list) or "hi_hats" in active
            per_bar = [
                sum(
                    bar * BEATS_PER_BAR
                    <= _note_step(note)
                    < (bar + 1) * BEATS_PER_BAR
                    for note in hats
                )
                for bar in range(start_bar, start_bar + section_bars)
            ]
            minimum = min(per_bar) if per_bar else 0
            # The final bar may contain an intentional pre-hook/drop clear.
            # Judge the section's body separately from that transition bar.
            body_per_bar = (
                per_bar[:-1]
                if len(per_bar) > 1 and name in ("verse", "hook", "chorus")
                else per_bar
            )
            body_minimum = min(body_per_bar) if body_per_bar else minimum
            required = 0
            if active_hat_section and not midi_owned:
                if racing and name == "verse":
                    required = 7  # one transition omission is musical
                elif name == "drop":
                    required = 6
                elif name in ("hook", "chorus"):
                    required = 7
                # Outro is the intentional release/fade. Requiring racing
                # verse density there contradicts the arrangement contract
                # and produced a false failure on otherwise valid 120s MIDI.
                #
                # These counts assume a busy host melody. Measured on a calm
                # 68 BPM ballad the drop demanded 6 hats a bar from material
                # averaging under one melodic onset a bar — a requirement the
                # arrangement should never have been asked to meet. Scale it
                # by the same density card the hats themselves answer; busy
                # material has a multiplier of 1.0 and keeps 6/7 exactly.
                required = int(round(required * hat_scale))
                if any(note.get("hat_cell") for note in hats):
                    # A library hat line (hat_patterns) is calm by decision: it keeps an audible clock
                    # (HAT_CELL_BAR_MINIMUM a bar), not the busy-melody count above.
                    required = min(required, HAT_CELL_BAR_MINIMUM)
            if required and body_minimum < required:
                reasons.append(f"{name}_hats_too_sparse")
            rows.append(
                {
                    "name": name,
                    "start_bar": start_bar,
                    "per_bar": per_bar,
                    "minimum": minimum,
                    "body_minimum": body_minimum,
                    "required": required,
                }
            )

    return {
        "passed": not reasons,
        "reasons": reasons,
        "midi_owned": midi_owned,
        "pace": pace,
        "attack": attack,
        "hat_count": len(hats),
        "sections": rows,
    }


def _ensure_808_on_every_kick(data: dict[str, Any]) -> bool:
    """Make kick and audible 808 attacks an exact two-way pair.

    A new 808 attack without kick is not a "bass answer" in the exported
    skeleton: it reads as a detached low-end line and was the direct cause of
    the bass-only bars visible in the Playlist.  Slides are stripped here as
    well, so every surviving 808 onset has an identical-grid kick. Its written
    sustain is preserved: the sampler's mono/Cut-Itself behaviour handles a
    later re-attack without turning every 808 into a short "tu-tu" click.
    """
    from bass808_gen import BASS_SUSTAIN_LENGTH, MAX_BASS_LENGTH, MIN_BASS_LENGTH, LOWEND_808_LO, _BASS_VEL, _KICK_VEL
    from music_theory import name_for_midi, parse_note_name
    from style_corpus import progression_roots_pc

    tracks = data.setdefault("tracks", {})
    kicks = sorted(
        (deepcopy(n) for n in track_notes(data, "kick")),
        key=_note_step,
    )
    bass = sorted(
        (deepcopy(n) for n in track_notes(data, "808_bass")),
        key=_note_step,
    )
    if not kicks and not bass:
        return False

    roots = progression_roots_pc(data) or [0]
    # This pass MINTS 808 notes, so it is an 808 pitch consumer like any other
    # and reads the shared map (2026-08-16). It used to reach straight for
    # roots[bar % len] — a fourth private fallback of exactly the kind
    # the project issue log (not included) §0.1 is about. It never showed while every consumer agreed
    # that the answer is always the root; with the map owning the degree it
    # would have minted a root inside a colour slot the audits then reject.
    voicing_map = _lowend_root_map(data)

    def _mapped_pc(step: float) -> int:
        pc, _tier, _degree = voicing_map.tone_at(step)
        if pc is not None:
            return int(pc) % 12
        bar = max(0, int(step // BEATS_PER_BAR))
        return int(roots[bar % len(roots)]) % 12

    changed = False
    paired_by_time: dict[float, dict[str, Any]] = {}
    kick_times = {round(_note_step(k), 3) for k in kicks}
    # The phrase composer's anchored solo hits are the one exception to the
    # weld — see _intentional_solo_lowend.
    anchor_times = _lowend_anchor_times(data)

    for kick in kicks:
        step = _note_step(kick)
        key = round(step, 3)
        if _intentional_solo_lowend(kick, "kick_only", anchor_times) and not any(
            abs(_note_step(note) - step) <= 1e-3 and not note.get("slide") for note in bass
        ):
            continue
        exact = [
            note for note in bass
            if abs(_note_step(note) - step) <= 1e-3 and not note.get("slide")
        ]
        if exact:
            paired = deepcopy(exact[0])
        else:
            # Both the "copy whatever is still ringing" and the
            # roots[bar % len] readings this replaces answered the harmony at
            # some OTHER instant than the one being minted. The map answers it
            # at this instant, which is also what the audits measure here.
            pitch = name_for_midi(LOWEND_808_LO + _mapped_pc(step))
            paired = {
                "time_step": round(step, 5),
                "note": pitch,
                "length": 0.75,
                "velocity": _BASS_VEL,
                "pan": 64,
                "bass808_gen": True,
            }
            changed = True
        paired["time_step"] = round(step, 5)
        # Every physical 808 note lives in the sub register even when an
        # upstream fallback supplied an old C4/C5 pitch. Preserve its
        # harmonic pitch class, discard only the wrong octave.
        try:
            paired["note"] = name_for_midi(LOWEND_808_LO + (parse_note_name(str(paired.get("note") or "C3")) % 12))
        except (TypeError, ValueError):
            bar = max(0, int(step // BEATS_PER_BAR))
            paired["note"] = name_for_midi(LOWEND_808_LO + int(roots[bar % len(roots)]) % 12)
        paired["velocity"] = _BASS_VEL
        paired["pan"] = 64
        paired["kick_lock"] = True
        paired.pop("slide", None)
        paired_by_time[key] = paired

    # At a kick onset the mandatory attack wins over a slide/duplicate.
    merged_by_time: dict[float, dict[str, Any]] = {}
    for note in bass:
        key = round(_note_step(note), 3)
        if key in kick_times:
            continue
        if note.get("slide"):
            bar = max(0, int(_note_step(note) // BEATS_PER_BAR))
            note["note"] = name_for_midi(LOWEND_808_LO + int(roots[bar % len(roots)]) % 12)
        else:
            try:
                note["note"] = name_for_midi(LOWEND_808_LO + (parse_note_name(str(note.get("note") or "C3")) % 12))
            except (TypeError, ValueError):
                bar = max(0, int(_note_step(note) // BEATS_PER_BAR))
                note["note"] = name_for_midi(LOWEND_808_LO + int(roots[bar % len(roots)]) % 12)
        note.pop("slide", None)
        merged_by_time.setdefault(key, deepcopy(note))
    merged_by_time.update(paired_by_time)
    merged = sorted(merged_by_time.values(), key=_note_step)

    # The converse is just as important as kick -> 808.  A bare 808 onset
    # makes the low end feel imported from another beat.  Add a short,
    # constant-pitch kick exactly at every remaining 808 re-attack.  This is
    # deliberately after normalising slides: a slide is never allowed to
    # create a hidden bass-only attack in the final export.  The role composer
    # already caps the pocket at two kick attacks per bar; enforcing a pair
    # must not silently create a third kick and make the final audit reject its
    # own result.  When that cap is full, discard the extra unpaired 808 rather
    # than changing the drum density after it was composed.
    kick_by_time = {round(_note_step(note), 3) for note in kicks}
    kicks_per_bar: dict[int, int] = {}
    for kick in kicks:
        bar = max(0, int(_note_step(kick) // BEATS_PER_BAR))
        kicks_per_bar[bar] = kicks_per_bar.get(bar, 0) + 1
    kick_template = deepcopy(kicks[0]) if kicks else {
        "note": "C5", "length": 0.28, "velocity": _KICK_VEL, "pan": 64,
    }
    supported_bass: list[dict[str, Any]] = []
    for bass_note in merged:
        step = _note_step(bass_note)
        key = round(step, 3)
        if key in kick_by_time:
            supported_bass.append(bass_note)
            continue
        if _intentional_solo_lowend(bass_note, "bass_only", anchor_times):
            supported_bass.append(bass_note)
            continue
        bar = max(0, int(step // BEATS_PER_BAR))
        if kicks_per_bar.get(bar, 0) >= 2:
            changed = True
            continue
        support = deepcopy(kick_template)
        # A support kick is a plain paired hit: strip any double tags the
        # template carried (kicks[0] may be a double's half) and mark it
        # paired, so the solo machinery never claims a note that HAS its 808.
        support.pop("intentional_double", None)
        support.pop("intentional_double_ghost", None)
        support["lowend_role"] = "paired"
        support["time_step"] = round(step, 5)
        support["note"] = "C5"
        support["length"] = min(0.28, float(support.get("length", 0.28) or 0.28))
        support["velocity"] = _KICK_VEL
        support["pan"] = 64
        support["kick_808_lock"] = True
        support["bass_support_kick"] = True
        fade = data.get("plg_outro_fade")
        if isinstance(fade, dict) and fade.get("enabled") and step >= float(fade.get("start_beat", float("inf"))):
            support["outro_fade"] = True
        kicks.append(support)
        kick_by_time.add(key)
        kicks_per_bar[bar] = kicks_per_bar.get(bar, 0) + 1
        supported_bass.append(bass_note)
        changed = True
    merged = supported_bass

    # An 808 is the tail after the kick, never a second short click: hold
    # each note toward the next attack (with a tiny breath) instead of the
    # old 0.25/0.5-beat pluck. 2026-08 regression (fixed): a prior version of
    # this pass dropped a bass note outright whenever it landed less than a
    # fixed 1.25-beat floor after the previous one, without removing that
    # note's paired kick above -- silently orphaning every kick whose 808
    # answer got dropped this way. Every kick already got a guaranteed
    # paired 808 note above; that pairing must survive here unconditionally,
    # so a tight gap shortens the note (down to MIN_BASS_LENGTH) instead of
    # deleting it. Overlap past the next onset is fine and expected -- the
    # sampler's mono/Cut-Itself behaviour handles the re-attack.
    for index, note in enumerate(merged):
        step = _note_step(note)
        next_step = _note_step(merged[index + 1]) if index + 1 < len(merged) else step + BASS_SUSTAIN_LENGTH
        length = min(MAX_BASS_LENGTH, BASS_SUSTAIN_LENGTH, max(MIN_BASS_LENGTH, next_step - step - 0.05))
        note["length"] = round(length, 5)
        note["velocity"] = _BASS_VEL
        note["pan"] = 64

    tracks["kick"] = sorted(kicks, key=_note_step)
    tracks["808_bass"] = merged
    return changed


_SOLO_ANCHOR_TOLERANCE = 0.15


def _lowend_harmonic_windows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The 808's harmonic map: analysed chord windows with each change pulled
    onto the snare/clap hit that masks it. Shared by generation, correction and
    every audit so all four agree on where a root change actually happens."""
    from midi_lowend_director import rhythmic_anchors, snap_harmonic_windows_to_boundaries

    structure = data.get("plg_midi_structure")
    raw = structure.get("harmonic_rhythm") if isinstance(structure, dict) else None
    return snap_harmonic_windows_to_boundaries(
        raw if isinstance(raw, list) else [],
        rhythmic_anchors(
            snare=track_notes(data, "snare"), clap=track_notes(data, "clap")
        )["boundary_times"],
    )


def _progression_scale_name(data: dict[str, Any]) -> str:
    """The harmony chain's scale name (2026-08-15).

    The pinned progression carries the note-detected scale (see
    _resolve_style_dna_key) and wins over ``data["scale"]``; the latter is
    generation_variety's per-run anti-clone roll, and reading it here used to
    make the 808's allowed scale set — correction targets, audit tolerance —
    drift run-to-run on identical MIDI. Mirrors the scale_hint chain in
    _resolve_progression_key. An explicit uploaded scale lock still wins.
    """
    uploaded = data.get("plg_uploaded_scale_lock")
    if isinstance(uploaded, dict) and uploaded.get("scale"):
        return str(uploaded["scale"]).lower()
    progression = data.get("plg_progression")
    if isinstance(progression, dict) and progression.get("scale"):
        return str(progression["scale"]).lower()
    return str(data.get("scale") or "natural_minor").lower()


def _lowend_root_map(data: dict[str, Any]):
    """The shared harmonic resolver for this pattern's 808.

    Companion to _lowend_harmonic_windows: that function makes everyone agree
    on WHERE a chord change happens, this one on WHICH root applies between
    the changes. Generation, the off-scale corrector and both export audits
    must build it from here rather than re-deriving a fallback of their own —
    the three private fallbacks this replaces disagreed in exactly the bars a
    sparse MIDI leaves empty (see midi_lowend_director.LowendRootMap).
    """
    from midi_lowend_director import LowendRootMap
    from music_theory import SCALES
    from style_corpus import progression_roots_pc

    scale_name = _progression_scale_name(data)
    if scale_name in ("minor", "min", "aeolian"):
        scale_name = "natural_minor"
    roots = progression_roots_pc(data) or []
    root_pc = int(roots[0]) % 12 if roots else detect_root_pc(track_notes(data, "melody_lead"))
    intervals = SCALES.get(scale_name, SCALES["natural_minor"])
    return LowendRootMap(
        harmonic_windows=_lowend_harmonic_windows(data),
        melody=track_notes(data, "melody_lead"),
        roots_pc=roots,
        scale_pcs={(root_pc + interval) % 12 for interval in intervals},
    )


def _lowend_anchor_times(data: dict[str, Any]) -> list[float]:
    """Beats where the already-final kit justifies a low-end hit standing alone.

    A snare/clap edge or a melody accent is an audible event the listener
    already hears at that instant, so a kick without its 808 answer (or an 808
    re-attack without a kick) reads as a deliberate call-and-response rather
    than a hole. Anywhere else, the pair stays welded — see
    _seal_midi_kick808_pocket / _ensure_808_on_every_kick.
    """
    from midi_lowend_director import rhythmic_anchors

    points = list(
        rhythmic_anchors(
            snare=track_notes(data, "snare"), clap=track_notes(data, "clap")
        )["boundary_times"]
    )
    for key in ("plg_melody_accents", "plg_kick808_phrase_anchors"):
        values = data.get(key)
        if isinstance(values, list):
            points.extend(float(t) for t in values if isinstance(t, (int, float)))
    return sorted(set(points))


def _intentional_solo_lowend(
    note: dict[str, Any], want_role: str, anchor_times: list[float]
) -> bool:
    """Is this the phrase composer's own deliberate unpaired hit?

    Only the MIDI phrase composer (midi_phrase_drums) reasons about phrase
    roles, so only its tagged notes may skip the pairing repair — output from
    the deterministic/LLM engines, and the repair passes' own safety-net hits,
    stay unconditionally paired exactly as before.
    """
    if not note.get("midi_phrase_composer"):
        return False
    if str(note.get("lowend_role") or "") != want_role:
        return False
    if note.get("intentional_double_ghost"):
        # The quieter half of a deliberate double (2026-08-20, variant B) is
        # a KICK-ONLY onset: the 808 stays one ringing tail under both taps.
        # Its justification is the pair's MAIN hit sitting on a melody accent,
        # not its own position — the pickup stands 0.5 beats before the
        # accent, further than _SOLO_ANCHOR_TOLERANCE (0.15) can ever reach,
        # so the anchor test below is mathematically unreachable for it.
        # Every consumer of this owner (the pairing seal's 808 rebuild,
        # _ensure_808_on_every_kick, the pairing_ratio audit and the context
        # audit's orphan count) reads the same answer.
        return True
    step = _note_step(note)
    return any(abs(step - point) <= _SOLO_ANCHOR_TOLERANCE for point in anchor_times)


def _seal_midi_kick808_pocket(data: dict[str, Any]) -> int:
    """Last-write ownership for an uploaded-MIDI kick/808 lane.

    Earlier passes are allowed to *suggest* melody-aware accents.  They are
    not allowed to leave arbitrary decimal kick positions or a separate 808
    rhythm behind.  This runs after every such pass, preserves the strongest
    two legal attacks per bar, and recreates one low, harmonic 808 attack for
    each surviving kick.  It deliberately does not alter hats/snare/clap.
    """
    if not data.get("plg_midi_melody"):
        return 0
    from bass808_gen import BASS_SUSTAIN_LENGTH, LOWEND_808_LO, MIN_BASS_LENGTH, _BASS_VEL, _KICK_VEL
    from music_theory import name_for_midi, parse_note_name
    # the project issue log (not included) 0.2a: this seal used to hard-code the literal 1.0 twice
    # instead of reading the module that OWNS minimum kick spacing, so
    # changing the owner's value would have left the seal silently keeping
    # its own answer — the duplicated-constant shape 0.1 is about.
    from bass808_gen import is_double_member, is_ghost_kick, kick_velocity_for
    from midi_phrase_drums import MIN_LOWEND_GAP_BEATS
    from style_corpus import progression_roots_pc

    tracks = data.setdefault("tracks", {})
    raw_kicks = sorted(track_notes(data, "kick"), key=_note_step)
    raw_bass = sorted(track_notes(data, "808_bass"), key=_note_step)
    if not raw_kicks:
        return 0

    arrangement = data.get("plg_arrangement")
    allowed_bars: set[int] | None = None
    if isinstance(arrangement, list):
        allowed_bars = set()
        for section in arrangement:
            if not isinstance(section, dict):
                continue
            active = section.get("active_tracks")
            if not isinstance(active, list) or "kick" not in active or not ({"808_bass", "sub_808"} & set(active)):
                continue
            start = int(section.get("start_bar", 0) or 0)
            allowed_bars.update(range(start, start + max(0, int(section.get("bars", 0) or 0))))

    kept: list[dict[str, Any]] = []
    per_bar: dict[int, int] = {}
    last_time = -999.0
    # A deliberate double is one onset rendered as two hits. This seal must
    # not re-litigate it: its ghost does not spend the bar's ceiling, and
    # the internal spacing of the pair is the generator's decision, not a
    # violation to repair. Without this the ghost is dropped here and the
    # double never reaches the export.
    prev_was_ghost = False
    changed = 0
    for original in raw_kicks:
        time = round(round(_note_step(original) * 4.0) / 4.0, 5)
        bar = max(0, int(time // BEATS_PER_BAR))
        if allowed_bars is not None and bar not in allowed_bars:
            changed += 1
            continue
        ghost = is_ghost_kick(original)
        crowded = (time - last_time) < MIN_LOWEND_GAP_BEATS
        if ghost or prev_was_ghost:
            crowded = False
        # The ghost is exempt from the ceiling, not merely uncounted by it:
        # a bar that already holds its two hits is exactly where a double
        # belongs, and testing the ceiling before the exemption dropped 34
        # of 38 planned doubles on Sahara before this line was fixed.
        if (not ghost and per_bar.get(bar, 0) >= 2) or crowded:
            changed += 1
            continue
        prev_was_ghost = ghost
        note = deepcopy(original)
        if time != _note_step(original):
            changed += 1
        note["time_step"] = time
        # kick_velocity_for, not the bare constant: the seal normalises
        # every kick to one tuned level, and the quieter half of a double
        # is the one exception. _normalize_kick808_dynamics would fix it
        # afterwards anyway; reading the same owner here keeps the two
        # from ever disagreeing about it.
        note["velocity"] = kick_velocity_for(original, _KICK_VEL)
        note["kick_808_lock"] = True
        note["midi_kick808_sealed"] = True
        kept.append(note)
        if not ghost:
            per_bar[bar] = per_bar.get(bar, 0) + 1
        last_time = time

    # A fast active section cannot silently lose two whole bars merely because
    # a late melody-symbiosis pass removed its accents. Restore one downbeat
    # foundation per permitted bar; hooks may still retain their second hit.
    if allowed_bars:
        template = deepcopy(raw_kicks[0])
        for bar in sorted(allowed_bars):
            if per_bar.get(bar, 0):
                continue
            point = float(bar * BEATS_PER_BAR)
            # A bar with no kick of its own is not necessarily an empty one:
            # a neighbouring attack just before the barline already covers the
            # downbeat, and restoring a foundation a fraction of a beat after
            # it is a kick roll, which the export audit rejects outright. The
            # main loop above already refuses to keep attacks closer than
            # MIN_LOWEND_GAP_BEATS; this restoration skipped that rule and
            # could therefore undo it.
            if any(abs(point - _note_step(other)) < MIN_LOWEND_GAP_BEATS for other in kept):
                continue
            note = deepcopy(template)
            # A restored foundation is a plain paired hit: strip any double
            # tags the template carried (raw_kicks[0] may be a double's half —
            # the tag must not leak into a note that is not a pickup/main).
            note.pop("intentional_double", None)
            note.pop("intentional_double_ghost", None)
            note["lowend_role"] = "paired"
            note["time_step"] = point
            note["velocity"] = _KICK_VEL
            note["kick_808_lock"] = True
            note["midi_kick808_sealed"] = True
            note["lowend_role"] = "foundation"
            kept.append(note)
            per_bar[bar] = 1
            changed += 1
        kept.sort(key=_note_step)

    # The phrase composer owns the rhythm. This final seal only removes
    # unmotivated attacks buried inside a dense hat roll; it never replaces
    # the MIDI-aware Main/Alt/Hook conversation with a fixed grid.
    from midi_lowend_director import rhythmic_anchors, roll_take_verdict

    anchors = rhythmic_anchors(
        hats=track_notes(data, "hi_hats"),
        snare=track_notes(data, "snare"),
        clap=track_notes(data, "clap"),
    )
    roll_times = anchors["roll_times"]
    boundary_times = anchors["boundary_times"]
    melody_accents = {
        round(float(step), 5)
        for step in (data.get("plg_melody_accents") or [])
        if isinstance(step, (int, float))
    }
    # midi_lowend_director.roll_may_take_kick owns this decision — including
    # the term this loop never had: a roll may mask a kick, it may not empty
    # the bar. A kick it spares is stamped so the ROLL can yield instead
    # (midi_fullkit_director.apply_lowend_roll_yield), keeping the audibility
    # promise "no kick buried inside a roll" true either way.
    from drum_density import density_card, min_kicks_per_4bar

    window_floor = min_kicks_per_4bar(density_card(data))
    kicks_left_in_bar: dict[int, int] = {}
    kicks_left_in_window: dict[int, int] = {}
    for note in kept:
        step = _note_step(note)
        bar = int(step // BEATS_PER_BAR)
        kicks_left_in_bar[bar] = kicks_left_in_bar.get(bar, 0) + 1
        window = int(step // (4.0 * BEATS_PER_BAR))
        kicks_left_in_window[window] = kicks_left_in_window.get(window, 0) + 1
    filtered: list[dict[str, Any]] = []
    for note in kept:
        time = _note_step(note)
        bar = int(time // BEATS_PER_BAR)
        window = int(time // (4.0 * BEATS_PER_BAR))
        verdict = roll_take_verdict(
            time,
            roll_times=roll_times,
            accents=melody_accents,
            kicks_in_window=kicks_left_in_window.get(window, 0),
            min_kicks_in_window=window_floor,
            beats_per_bar=BEATS_PER_BAR,
        )
        if verdict == "take":
            kicks_left_in_bar[bar] = kicks_left_in_bar.get(bar, 0) - 1
            kicks_left_in_window[window] = kicks_left_in_window.get(window, 0) - 1
            changed += 1
            continue
        # Only the NEW term asks the roll for anything. A downbeat/accent kick
        # has coexisted with rolls since this rule was written.
        if verdict == "density_floor":
            note["lowend_roll_priority"] = True
        filtered.append(note)
    kept = filtered

    roots = progression_roots_pc(data) or [0]
    melody = sorted(track_notes(data, "melody_lead"), key=_note_step)
    harmonic_windows = _lowend_harmonic_windows(data)

    def _melody_root_pc_at(time: float) -> int | None:
        for window in harmonic_windows:
            if not isinstance(window, dict):
                continue
            try:
                if float(window["start"]) <= time < float(window["end"]) and float(window.get("confidence", 0.0) or 0.0) >= 0.48:
                    return int(window["root_pc"]) % 12
            except (KeyError, TypeError, ValueError):
                continue
        bar_start = float(int(time // BEATS_PER_BAR) * BEATS_PER_BAR)
        candidates = [note for note in melody if bar_start <= _note_step(note) < bar_start + BEATS_PER_BAR]
        if candidates:
            try:
                return min(parse_note_name(str(note.get("note") or "C3")) for note in candidates) % 12
            except (TypeError, ValueError):
                pass
        # No confident harmonic evidence and no sounding lower MIDI note:
        # never invent a new 808 pitch from a global progression guess.
        # The caller keeps the last confirmed root or leaves a deliberate gap.
        return None

    bass: list[dict[str, Any]] = []
    paired_kicks: list[tuple[dict[str, Any], int]] = []
    last_confirmed_root: int | None = None
    for kick in kept:
        time = _note_step(kick)
        confirmed_root = _melody_root_pc_at(time)
        if confirmed_root is not None:
            last_confirmed_root = confirmed_root
        if last_confirmed_root is None:
            # At the very beginning with no verified MIDI root, silence is
            # safer than a random low note. Keep kick/808 paired by removing
            # this attack from the accepted low-end skeleton.
            changed += 1
            continue
        paired_kicks.append((kick, last_confirmed_root))
    kept = [kick for kick, _pc in paired_kicks]

    # The phrase composer's own call-and-response survives the seal: a
    # kick_only push keeps the low end from re-triggering under an already
    # ringing 808, and a bass_only answer lets the 808 move on a snare/clap
    # edge. Both must sit on a real anchor (see _lowend_anchor_times) —
    # anything untagged or unanchored is still welded into a pair below.
    anchor_times = _lowend_anchor_times(data)
    attacks: list[tuple[float, bool]] = [
        (_note_step(kick), False)
        for kick, _pc in paired_kicks
        if not _intentional_solo_lowend(kick, "kick_only", anchor_times)
    ]
    for note in raw_bass:
        if not _intentional_solo_lowend(note, "bass_only", anchor_times):
            continue
        time = round(round(_note_step(note) * 4.0) / 4.0, 5)
        # MIN_BASS_LENGTH away from every existing attack, not just an exact
        # duplicate: the later monophony seal (_ensure_808_on_every_kick)
        # floors every note's length at MIN_BASS_LENGTH even when the next
        # onset is closer, so a tighter gap here would silently create an
        # overlapping pair downstream instead of the clean call-and-response
        # this anchor is meant to add.
        if not any(abs(time - point) < MIN_BASS_LENGTH for point, _solo in attacks):
            attacks.append((time, True))
    attacks.sort(key=lambda row: row[0])

    running_root: int | None = None
    for index, (time, is_solo) in enumerate(attacks):
        confirmed_root = _melody_root_pc_at(time)
        if confirmed_root is not None:
            running_root = confirmed_root
        if running_root is None:
            continue
        next_time = attacks[index + 1][0] if index + 1 < len(attacks) else time + BASS_SUSTAIN_LENGTH
        bass.append({
            "time_step": time,
            "note": name_for_midi(LOWEND_808_LO + running_root),
            "length": round(min(BASS_SUSTAIN_LENGTH, max(0.05, next_time - time)), 5),
            "velocity": _BASS_VEL,
            "pan": 64,
            "kick_lock": True,
            "lowend_role": "paired",
            "midi_kick808_sealed": True,
        })
        if is_solo:
            template = min(raw_kicks, key=lambda note: abs(_note_step(note) - time))
            companion = deepcopy(template)
            # A companion is a plain paired hit for the solo 808: strip any
            # double tags the nearest kick carried (the template may be a
            # double's half — its tag must not leak into an unrelated hit,
            # where it would fake a solo exemption or a velocity contract).
            companion.pop("intentional_double", None)
            companion.pop("intentional_double_ghost", None)
            companion["time_step"] = time
            companion["velocity"] = _KICK_VEL
            companion["kick_808_lock"] = True
            companion["midi_kick808_sealed"] = True
            companion["lowend_role"] = "paired"
            kept.append(companion)
            changed += 1
    for note in bass:
        if note.get("midi_phrase_composer") is None:
            note.pop("midi_phrase_composer", None)

    solo_kicks = sum(
        _intentional_solo_lowend(kick, "kick_only", anchor_times) for kick, _pc in paired_kicks
    )
    solo_bass = 0
    kept.sort(key=_note_step)
    tracks["kick"] = kept
    tracks["808_bass"] = bass
    data["plg_midi_kick808_seal"] = {
        "passed": bool(kept) and len(kept) - solo_kicks == len(bass) - solo_bass,
        "kick_count": len(kept),
        "removed_or_quantized": changed,
        "solo_kicks": solo_kicks,
        "solo_bass": solo_bass,
        "grid": "1/16",
        "register": "C3-B3",
    }
    return changed


def _duck_808_bass_lane(data: dict[str, Any]) -> bool:
    """808_bass keeps composer-owned call/response; mixer sidechain owns ducking."""
    return False


def _shape_sub_808_velocity(data: dict[str, Any]) -> None:
    """Kill the flat-127 sub wall: each 808 hit follows its kick's velocity
    (couple of ticks above for weight) and the section's energy, so verses
    breathe and the chorus knocks instead of one fatiguing drone."""
    subs = track_notes(data, "sub_808")
    if not subs:
        return
    kicks = track_notes(data, "kick")
    kick_vel_at: dict[float, int] = {
        round(float(k.get("time_step", 0)), 3): int(k.get("velocity", 112)) for k in kicks
    }
    energies: list[tuple[float, float, float]] = []
    arrangement = data.get("plg_arrangement")
    if isinstance(arrangement, list):
        for section in arrangement:
            if not isinstance(section, dict):
                continue
            start = float(section.get("start_bar", 0)) * BEATS_PER_BAR
            end = start + float(section.get("bars", 0)) * BEATS_PER_BAR
            energies.append((start, end, float(section.get("energy", 1.0))))
    for note in subs:
        step = round(float(note.get("time_step", 0)), 3)
        base = kick_vel_at.get(step, 116) + 6  # a touch over the kick for sub weight
        energy = next((e for s, e_end, e in energies if s <= step < e_end), 1.0)
        _set_velocity(note, int(round(base * max(0.6, min(1.05, energy)))))


def _drop_midi_lanes(data: dict[str, Any], lanes: tuple[str, ...]) -> None:
    """Remove lanes a MIDI-owned beat does not ship, plus their sample picks."""
    tracks = data.setdefault("tracks", {})
    for lane in lanes:
        tracks.pop(lane, None)
    for field in ("plg_sample_picks", "plg_sound_paths"):
        mapped = data.get(field)
        if isinstance(mapped, dict):
            for lane in lanes:
                mapped.pop(lane, None)
    if isinstance(data.get("samples"), list):
        data["samples"] = [
            item
            for item in data["samples"]
            if not isinstance(item, dict) or item.get("track") not in lanes
        ]


def _resolve_style_dna_key(
    data: dict[str, Any], melody_now: list[dict[str, Any]]
) -> tuple[int | None, str | None, str]:
    """Source of truth for the Style-DNA progression key (2026-08-15).

    Priority, highest first:

    1. the real sounding notes — ``detect_root_pc`` / ``detect_scale`` over
       the melody lane, when there are enough notes to analyse;
    2. the embedded MIDI key_signature meta (``plg_midi_key``);
    3. the filename hint in the passport — a last resort for material too
       thin to analyse and lacking a key_signature.

    The filename hint used to win unconditionally here; the 2026-08-14 audit
    of ten real marketplace files showed it wrong in four of ten (the
    producer-declared name lagged the actual loop by a semitone), anchoring
    the progression and every 808 a semitone off the sounding melody.  The
    sounding notes are the primary source of truth; the hint is a fallback.

    Returns ``(root_pc, scale, source)``; ``source`` is one of
    ``"note_analysis"``, ``"midi_key_signature"``, ``"filename_hint"``,
    ``"none"``.
    """
    from music_theory import detect_scale

    if len(melody_now) >= 4:
        root = int(detect_root_pc(melody_now)) % 12
        return root, detect_scale(melody_now, root), "note_analysis"
    key_meta = data.get("plg_midi_key") if isinstance(data.get("plg_midi_key"), dict) else {}
    if key_meta.get("root_pc") is not None:
        scale = key_meta.get("scale")
        return int(key_meta["root_pc"]) % 12, (str(scale) if scale else None), "midi_key_signature"
    passport = data.get("plg_midi_passport") if isinstance(data.get("plg_midi_passport"), dict) else {}
    passport_key = passport.get("key") if isinstance(passport.get("key"), dict) else {}
    filename_key = (
        passport_key.get("filename_hint")
        if isinstance(passport_key.get("filename_hint"), dict)
        else {}
    )
    if isinstance(filename_key.get("root_pc"), int):
        scale = filename_key.get("scale")
        return int(filename_key["root_pc"]) % 12, (str(scale) if scale else None), "filename_hint"
    return None, None, "none"


def _applied_param_plan(data: dict[str, Any], profile: GenreProfile) -> dict[str, Any] | None:
    """The validated Param Plan values to apply, or None.

    Only an ``on``-mode plan that came from the LLM (or its cache) and passed
    the validator is applied. Shadow, off, fallback and anything malformed give
    None, i.e. the genre profile exactly as before. The values are validated
    again here so a hand-edited pattern cannot smuggle an out-of-range number in.
    """
    record = data.get("plg_param_plan")
    if not isinstance(record, dict):
        return None
    if record.get("mode") != "on" or record.get("ok") is not True or record.get("source") not in ("llm", "cache"):
        return None
    try:
        import param_plan

        values = record.get("values")
        if not isinstance(values, dict):
            return None
        checked = param_plan.validate_plan({"schema": param_plan.SCHEMA_VERSION, **values}, param_plan.baseline_plan(profile))
        return checked.values if checked.ok else None
    except Exception as exc:  # noqa: BLE001 - a bad plan must never cost a beat
        logger.info("Param plan not applied (%s)", exc)
        return None


def humanize_pattern(pattern: dict[str, Any], *, profile: GenreProfile | None = None) -> dict[str, Any]:
    """Apply producer-brain post-processing to LLM output.

    Pass ``profile`` to override genre detection (used by ``plg_tune`` offline).
    """
    data = deepcopy(pattern)
    try:
        from generation_variety import enforce_midi_bpm

        enforce_midi_bpm(data)
    except Exception:
        pass
    tracks = data.setdefault("tracks", {})
    if not isinstance(tracks, dict):
        return data

    # MIDI owns all melodic content.  The active product has a closed lane
    # contract (seven lanes + the two midi_roles lanes), so stale generator
    # output cannot survive even if an archived helper was accidentally reached
    # while processing this pattern.
    midi_melody = bool(data.get("plg_midi_melody"))
    disabled_midi_lanes: tuple[str, ...] = ()
    if midi_melody:
        from midi_validate import MIDI_DISABLED_LANES, sanitize_midi_mode

        disabled_midi_lanes = MIDI_DISABLED_LANES
        sanitize_midi_mode(data)

    flags = _style_flags(data)
    rng = _rng_for(data)
    bpm = float(data.get("bpm", 140))

    if flags["country"]:
        logger.info("Producer brain: country shuffle mode")
        if isinstance(tracks.get("hi_hats"), list):
            tracks["hi_hats"] = apply_hi_hat_swing(tracks["hi_hats"], rng)
        return data

    if flags["ambient"]:
        logger.info("Producer brain: ambient — light humanize only")
        return data

    style = str(data.get("style", ""))
    prompt = str(data.get("user_prompt", ""))
    max_drive = bool(data.get("plg_clipper_mode") or data.get("plg_filth_mode")) or any(
        k in f"{style} {prompt}".lower()
        for k in ("мясо", "брутал", "max drive", "soft clip", "clipper")
    )
    variety = data.get("plg_variety") if isinstance(data.get("plg_variety"), dict) else {}
    genre_hint = variety.get("genre") if isinstance(variety.get("genre"), str) else None
    profile = (
        profile
        if profile is not None
        else profile_for(style, prompt, max_drive=max_drive, genre_hint=genre_hint)
    )
    logger.info("Producer brain: genre=%s max_drive=%s", profile.name, max_drive)

    # --- Style DNA first: pin the chord progression so every 808 rebuild below
    # walks real changes (i–VI–III–VII…) instead of droning one pitch. The
    # anchor key comes from the real sounding notes (_resolve_style_dna_key:
    # notes > embedded key_signature > filename hint); the filename hint is a
    # last resort only, never the primary source. ---
    try:
        from style_corpus import attach_progression

        melody_now = track_notes(data, "melody_lead")
        progression_root, progression_scale, key_source = _resolve_style_dna_key(data, melody_now)
        if progression_scale is None:
            progression_scale = profile.melody_scale or None
        if key_source != "none":
            data["plg_progression_root_source"] = key_source
        attach_progression(
            data,
            root_pc=progression_root,
            scale=progression_scale,
        )
    except Exception as exc:  # noqa: BLE001 — DNA must never break generation
        logger.info("Style DNA skipped (%s)", exc)

    # Finalize melody BEFORE the drum grid so pace/energy can force double-time
    # under a racing hook (half-time trap under a fast melody is the classic miss).
    from symbolic_composer import ensure_opium_melody_if_needed

    ensure_opium_melody_if_needed(data, profile=profile, rng=rng)
    try:
        from hook_composer import ensure_progression_melody_if_needed

        ensure_progression_melody_if_needed(data, profile=profile, rng=rng)
    except Exception as exc:  # noqa: BLE001 — hook repair must never break generation
        logger.info("Hook composer skipped (%s)", exc)

    try:
        from melody_beat_symbiosis import build_melody_energy_card

        build_melody_energy_card(data)
    except Exception as exc:  # noqa: BLE001
        logger.info("Melody energy card (pre-drums) skipped (%s)", exc)

    # How busy the drums may get, read from the material rather than from the
    # genre label. Built here because the melody is final and in its final
    # arrangement, but no drum lane exists yet — every density consumer
    # downstream (pocket shapes, section floor, gap filler, hats, export
    # audit) reads this one card instead of re-deriving its own answer.
    # Param Plan (PLG_PARAM_PLAN=on) is read here, before the density card, because two of its knobs
    # (kick density multiplier, double-kick share) belong to the kick/808 side. None unless an on-mode,
    # ok, llm/cache plan validated against this profile; every value below then stays at today's default.
    plan_values = _applied_param_plan(data, profile)
    plan_kwargs: dict[str, Any] | None = None
    if plan_values is not None:
        import param_plan

        plan_kwargs = param_plan.humanizer_kwargs(param_plan.ValidatedPlan(values=plan_values, ok=True))
    density_kwargs: dict[str, Any] = dict(plan_kwargs["build_drum_density_card"]) if plan_kwargs else {}
    pocket_kwargs: dict[str, Any] = dict(plan_kwargs["build_phrase_kick_808_pocket"]) if plan_kwargs else {}

    try:
        from drum_density import build_drum_density_card

        density = build_drum_density_card(data, profile=profile, **density_kwargs)
        logger.info(
            "Drum density from MIDI: %.2f pairs/bar (onset_rate=%.2f busyness=%.2f "
            "drive=%.2f, genre %s x%.2f)",
            density["pairs_per_bar"],
            density["onset_rate"],
            density["busyness"],
            density["drive"],
            density.get("genre"),
            density["genre_multiplier"],
        )
    except Exception as exc:  # noqa: BLE001 — every consumer degrades to its old fixed default
        logger.info("Drum density card skipped (%s)", exc)

    # The source MIDI is often a short 2-4 bar idea that arrangement has just
    # tiled into the full song.  Re-analyse unconditionally *after* melody
    # finalization: retaining the old source-only map made a 40-bar song look
    # like two phrases, so every later kick/808 cell fell back to a default
    # rhythm and the move director selected nothing.
    if midi_melody:
        try:
            from midi_structure_analysis import attach_midi_structure

            attach_midi_structure(data)
        except Exception as exc:  # noqa: BLE001
            logger.info("MIDI structure analysis (pre-drums) skipped: %s", exc)

    if midi_melody:
        try:
            from midi_beat_planner import attach_midi_beat_plan

            attach_midi_beat_plan(data)
        except Exception as exc:  # noqa: BLE001
            logger.info("MIDI beat plan (pre-drums) skipped: %s", exc)

        try:
            from midi_phrase_rhythm_plan import attach_phrase_rhythm_plan

            attach_phrase_rhythm_plan(data)
        except Exception as exc:  # noqa: BLE001
            logger.info("MIDI phrase rhythm plan skipped: %s", exc)

        try:
            from midi_fullkit_director import attach_fullkit_plan

            attach_fullkit_plan(data)
        except Exception as exc:  # noqa: BLE001
            logger.info("MIDI full-kit plan skipped: %s", exc)

    # A beat gets a few compatible producer moves, never every available
    # technique.  The selector is structural; individual move functions still
    # enforce their own collision and MIDI-ownership safeguards.
    try:
        from dataclasses import replace
        from producer_move_director import choose_moves

        move_plan = choose_moves(data, profile.name)
        data["plg_producer_moves"] = move_plan
        selected = set(move_plan["move_ids"])
        profile = replace(
            profile,
            transition_fill=profile.transition_fill or "transition_fill" in selected,
            melody_drum_density_inverse=(
                profile.melody_drum_density_inverse or "drum_density_inverse" in selected
            ),
            pre_snare_pickup=profile.pre_snare_pickup or "pre_snare_pickup" in selected,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Producer move director skipped: %s", exc)

    # Param Plan (PLG_PARAM_PLAN=on): the plan overrides four knobs of the
    # profile; every function below stays the executor. Without an applicable
    # plan all of these stay at today's values.
    swing_cap = HAT_SWING_CAP
    trap_hat_kwargs: dict[str, Any] = {}
    snare_vel_range, clap_vel_range = SNARE_VEL_RANGE, CLAP_VEL_RANGE
    markov_hat_kwargs: dict[str, Any] = {}
    thin_kwargs: dict[str, Any] = {}
    if plan_values is not None:
        from dataclasses import replace as _replace

        kwargs = plan_kwargs
        profile = _replace(profile, hat_swing=kwargs["apply_hi_hat_swing"]["intensity"], melody_lag_ms=kwargs["melody_lag_ms"])
        trap_hat_kwargs = kwargs["apply_trap_hi_hats"]
        markov_hat_kwargs = kwargs["apply_markov_hat_dynamics"]
        snare_vel_range, clap_vel_range = kwargs["snare_velocity"], kwargs["clap_velocity"]
        swing_cap = max(HAT_SWING_CAP, profile.hat_swing)
        if swing_cap > HAT_SWING_CAP:
            # thin_generated_hats reads a hat's grid cell within the swing's own reach
            thin_kwargs["swing_reach"] = round(PPQ_SWING_MAX * swing_cap + SWING_REACH_MARGIN, 5)
        data["plg_param_plan_applied"] = {
            "hat_swing": profile.hat_swing, "swing_cap": swing_cap, "melody_lag_ms": profile.melody_lag_ms,
            "trap_hats": dict(trap_hat_kwargs), "markov_hats": dict(markov_hat_kwargs),
            "snare_velocity": list(snare_vel_range), "clap_velocity": list(clap_vel_range), **thin_kwargs,
            "density": dict(density_kwargs), "pocket": dict(pocket_kwargs),
        }
        logger.info("Param plan applied: %s", data["plg_param_plan_applied"])

    from drum_defaults import _bar_count
    phrase_drums_applied = False
    phrase_span = (data.get("plg_midi_structure") or {}).get("span", {}) if isinstance(data.get("plg_midi_structure"), dict) else {}
    phrase_covers_song = int(phrase_span.get("bars", 0) or 0) == _bar_count(data)
    # MIDI-derived phrase drums: kick/snare/clap come from the melody's own
    # phrase structure instead of a genre-keyed DRUM_CELLS table.
    #
    # 2026-08-11: this used to also require
    # `profile.name in ("trap", "rage", "opium", "underground", "pluggnb")`.
    # That genre-name whitelist covered only 5 of the 10 active hip-hop lanes,
    # so drill / phonk / west_coast / detroit / hyperpop silently took the
    # table path instead — even though `midi_phrase_drums` has no genre
    # coupling at all (zero GenreProfile references in the module) and all ten
    # lanes have bass808_enabled=True, meaning `_apply_808_bass_lane` was
    # overwriting the table's kick a few passes later anyway.
    #
    # The remaining conditions are the real, material-based gate:
    #   midi_melody        — there is an ingested MIDI to derive phrases from
    #   phrase_covers_song — the phrase plan spans the whole arrangement
    # and `apply_phrase_drums` independently returns False when
    # `plg_phrase_rhythm_plan` is absent/disabled/empty, so a thin or unusable
    # plan still falls through to `apply_drum_blueprint` below.
    if midi_melody and phrase_covers_song:
        try:
            from midi_phrase_drums import apply_phrase_drums
            phrase_drums_applied = apply_phrase_drums(data)
        except Exception as exc:  # noqa: BLE001
            logger.info("MIDI phrase drums skipped: %s", exc)

    from drum_blueprint import apply_drum_blueprint

    # Clean drum grid — never let LLM melody leak into drum lanes; varied by genre+prompt.
    if not phrase_drums_applied and profile.name not in ("country", "ambient", "rnb", "pop", "kpop", "dark_pop"):
        apply_drum_blueprint(
            data, genre=profile.name,
            transition_fill=getattr(profile, "transition_fill", False),
        )
        kick = track_notes(data, "kick")
        snare = track_notes(data, "snare")
        clap = track_notes(data, "clap")
    else:
        kick = track_notes(data, "kick")
        snare = track_notes(data, "snare")
        clap = track_notes(data, "clap")

    # --- Hi-hats: Symbolic ML (Markov + drop) or legacy trap rolls ---
    if data.get("plg_midi_hats") and track_notes(data, "hi_hats"):
        # Real groove from the user's MIDI collection — keep its grid and
        # velocities; only choke, pan and duck it under the backbeat.
        # apply_hat_forward_lag is deliberately NOT applied here — it would
        # move the user's own MIDI timing, which this lane guarantees not to
        # touch (see test_humanize_keeps_midi_hats_and_ships_one_808_lane).
        hats = track_notes(data, "hi_hats")
        if getattr(profile, "open_hat_before_snare_cut", False):
            hats = add_open_hat_before_snare(hats, track_notes(data, "snare"))
        hats = apply_open_hat_choke(hats)
        hats = apply_hat_panning(hats, rng)
        ref = track_notes(data, "clap") or track_notes(data, "snare")
        if ref:
            hats = apply_six_db_hat_rule(hats, ref)
        tracks["hi_hats"] = hats
    elif data.get("plg_drum_blueprint") or track_notes(data, "hi_hats"):
        from symbolic_composer import apply_drum_symbolic_pipeline, should_apply_symbolic

        if should_apply_symbolic(data, profile):
            data = apply_drum_symbolic_pipeline(data, profile=profile)
            tracks = data.setdefault("tracks", {})
            hats = track_notes(data, "hi_hats")
        elif profile.hat_rolls:
            from arranger import ROLL_SECTIONS
            from hihat_rolls import apply_trap_hi_hats

            tagged = any(n.get("section") for n in track_notes(data, "snare"))
            # Prefer the real per-section melody density over the fixed
            # hook/chorus/drop name list — a sparse verse/outro can roll now,
            # and a hot chorus/hook occurrence won't. Falls back to
            # ROLL_SECTIONS when there's no melody/arrangement to judge
            # density from (_roll_eligible_windows returns None).
            hats = apply_trap_hi_hats(
                data,
                rng,
                roll_sections=ROLL_SECTIONS if tagged else None,
                roll_windows=_roll_eligible_windows(data) if tagged else None,
                **trap_hat_kwargs,
            )
        else:
            # Non-trap genres: steady 1/8 grid with living dynamics, no rolls.
            from drum_defaults import _bar_count as _bars_of
            from hihat_rolls import base_hat_line

            hats = apply_markov_hat_dynamics(base_hat_line(data, _bars_of(data)), rng, **markov_hat_kwargs)
        hats = apply_hat_snare_micro_drop(hats, track_notes(data, "snare"))
        hats = apply_hat_roll_pitch(hats, rng)
        hats = apply_hi_hat_swing(hats, rng, intensity=min(profile.hat_swing, swing_cap))
        hats = densify_hats_one_step(hats, data, rng)  # Ken: every 16th slot in hooks
        hats = settle_generated_hat_register(hats, rng)
        if getattr(profile, "open_hat_before_snare_cut", False):
            hats = add_open_hat_before_snare(hats, track_notes(data, "snare"))
        hats = apply_open_hat_choke(hats)
        hats = apply_hat_panning(hats, rng)
        ref = clap or snare
        if ref:
            hats = apply_six_db_hat_rule(hats, ref)
        hats = apply_hat_forward_lag(hats, rng)
        tracks["hi_hats"] = hats

    # Roll generation and the sixteenth densifier answer the SECTION, not the
    # material, so a calm host melody still received hook-grade hat runs. Bring
    # the generated lane down to the density the melody implies before the
    # phrase contract below reads it. Never touches the user's own MIDI hats,
    # and a busy card makes this an explicit no-op.
    if not data.get("plg_midi_hats") and track_notes(data, "hi_hats"):
        try:
            from drum_density import density_card, thin_generated_hats

            thinned, removed = thin_generated_hats(track_notes(data, "hi_hats"), density_card(data), **thin_kwargs)
            if removed:
                tracks["hi_hats"] = thinned
                logger.info(
                    "Hat density from MIDI: %d of %d generated hats thinned",
                    removed,
                    removed + len(thinned),
                )
        except Exception as exc:  # noqa: BLE001 — hats already exist; this is a trim
            logger.info("Hat density thinning skipped (%s)", exc)

    if midi_melody:
        try:
            from midi_fullkit_director import apply_generated_hat_contract

            removed = apply_generated_hat_contract(data)
            if removed:
                logger.info("MIDI full-kit hat contract: %d support hats removed", removed)
        except Exception as exc:  # noqa: BLE001
            logger.info("MIDI full-kit hat contract skipped: %s", exc)

    # --- Snare / clap: pre-snare shift + velocity ---
    for key, (lo, hi) in (("snare", snare_vel_range), ("clap", clap_vel_range)):
        raw = track_notes(data, key)
        if raw:
            shifted = apply_pre_snare_shift(raw, bpm, rng)
            if profile.humanize_drum_velocity:
                tracks[key] = humanize_velocities(shifted, lo=lo, hi=hi, rng=rng)
            else:
                tracks[key] = shifted

    if getattr(profile, "delayed_clap_hit", False):
        shifted_claps = add_delayed_clap_hit(data)
        if shifted_claps:
            logger.info("Pluggnb pocket: %d clap hit(s) delayed a quarter-beat", shifted_claps)

    if getattr(profile, "bass808_enabled", False):
        clap_pre_roll = track_notes(data, "clap")
        if clap_pre_roll:
            from drum_blueprint import _seed_for as _drum_seed_for

            tracks["clap"] = add_pre_clap_roll(
                clap_pre_roll, rng,
                arrangement=data.get("plg_arrangement"),
                base_seed=_drum_seed_for(data),
            )

    snare = track_notes(data, "snare")
    clap = track_notes(data, "clap")
    if clap:
        # Snare Layer as a CLAP double (not a snare rim) — the "expensive"
        # layered-clap symbiosis. Falls back to the old snare-rim behavior
        # below only when there's no clap at all.
        #
        # `pre_clap_roll` joins the fill/ghost/riser filter here (2026-08-21).
        # add_pre_clap_roll runs immediately above and prints two 1/16 build-up
        # micro-claps at 40 %/70 % of the main hit's velocity; without this
        # term the layer doubled every one of them, so a lead-in that exists to
        # be quiet was played twice. Measured on the five listening files:
        # 22/36/18/20/26 doubled decorations per song. Same reasoning as the
        # elif branch's own comment — doubling a decoration smears it.
        clap_core_hits = [
            n for n in clap
            if not (n.get("fill") or n.get("ghost") or n.get("riser") or n.get("pre_clap_roll"))
        ]
        # Which bars the layer sounds in is the backbeat palette's decision,
        # read here, never re-derived (midi_phrase_drums.backbeat_figure_for_bar).
        # duplicate_clap_layer itself is untouched: it still builds the layer
        # from the clap with its own delay/pan/velocity, it is simply handed
        # the hits the palette said carry a layer. Where the palette never ran
        # (non-MIDI route, unit patterns) the filter is a no-op and this is
        # the old every-clap behaviour.
        clap_core_hits = _backbeat_palette_hits(data, clap_core_hits, want="layer")
        layer = duplicate_clap_layer(clap_core_hits, bpm, rng)
        layer = apply_velocity_sine_curve(layer, base=55, amplitude=15)
        if getattr(profile, "pre_snare_pickup", False) and snare:
            snare_core_hits = [n for n in snare if not (n.get("fill") or n.get("ghost") or n.get("riser"))]
            # WHERE the pickup fires is the palette's decision, not this
            # flag's. The flag (and producer_move_director, which is the thing
            # that actually turns it on for every trap generation) still answer
            # "is this gesture available at all"; the palette answers "here".
            snare_core_hits = _backbeat_palette_hits(data, snare_core_hits, want="pickup")
            layer = add_pre_snare_pickup(snare_core_hits, layer)
        tracks["snare_layer"] = layer
    elif snare:
        # Layer only the honest backbeat — doubling fills/ghosts/risers smears
        # the crescendo and turns transitions into mush.
        core_hits = [n for n in snare if not (n.get("fill") or n.get("ghost") or n.get("riser"))]
        layer = duplicate_snare_layer(core_hits)
        layer = apply_velocity_sine_curve(layer, base=55, amplitude=15)
        if getattr(profile, "pre_snare_pickup", False):
            layer = add_pre_snare_pickup(core_hits, layer)
        tracks["snare_layer"] = layer

    if midi_melody:
        try:
            from midi_fullkit_director import apply_section_pulse_switch, apply_strong_phrase_answers

            phrase_answers = apply_strong_phrase_answers(data)
            pulse_changed = apply_section_pulse_switch(data)
            if pulse_changed:
                phrase_answers["section_pulse_hats"] = pulse_changed
            if any(phrase_answers.values()):
                data["plg_midi_phrase_answers"] = phrase_answers
                logger.info("MIDI strong-phrase answers: %s", phrase_answers)
        except Exception as exc:  # noqa: BLE001
            logger.info("MIDI strong-phrase answers skipped: %s", exc)

    # --- Melody polish (hook already composed above, before drum blueprint) ---
    melody = track_notes(data, "melody_lead")
    symbolic_opium = bool(data.get("plg_symbolic_opium_melody"))
    progression_hook = bool(data.get("plg_progression_hook"))
    if melody:
        if profile.melody_scale and not symbolic_opium and not progression_hook:
            # (a composed hook already follows the progression; re-snapping to a
            # detected root would bend GOOD chord tones out of the changes)
            melody = darken_melody_intervals(melody, scale=profile.melody_scale)
            root_pc_now = detect_root_pc(melody)
            data["plg_key"] = key_label(root_pc_now, profile.melody_scale)
            if getattr(profile, "melody_root_fifth_seventh_bias", False):
                melody = apply_root_fifth_seventh_bias(melody, root_pc_now, profile.melody_scale)
            if getattr(profile, "melody_chromatic_walk", False):
                melody = apply_chromatic_melody_walk(melody, rng)
        if profile.voicing_spread > 0:
            melody = apply_chord_voicing_spread(melody, rng, chance=profile.voicing_spread)
        # Symbolic Opium hook already has octave jumps — skip generic thicken.
        if not symbolic_opium:
            melody = thicken_melody(melody)
        if profile.melody_lag_ms > 0:
            melody = apply_melody_groove_lag(melody, bpm, profile.melody_lag_ms, rng)
        if profile.stereo_drop:
            melody = apply_mono_stereo_drop(melody)
        if not midi_melody and profile.counter_melody and not symbolic_opium:
            counter = add_counter_melody_offbeat(melody, kick, rng)
            if counter:
                tracks["counter_melody"] = counter
        # Sparse trap-family hooks get a printed dotted-eighth delay so the
        # lead answers itself instead of plinking once a second.
        if profile.name in ("trap", "rage", "opium", "phonk", "drill") and melody:
            from drum_defaults import _bar_count
            from style_family import genre_of, is_ken

            per_bar = len(melody) / max(1, _bar_count(data))
            if per_bar <= 3.2:
                # Ken/rage prints TRIPLET taps (the Ellis Lost texture move)
                melody = apply_melody_echo(melody, triplet=is_ken(genre_of(data)))
        if getattr(profile, "melody_8bar_variation", False):
            melody = apply_melody_8bar_variation(melody)
        if getattr(profile, "melody_chaotic_wide_jumps", False):
            melody = apply_chaotic_melody_jumps(melody, rng)
            # Chaos supplies the CONTOUR, the digicore root/5th/7th rule owns
            # the PITCH CLASSES (2026-07 audit: the jumps used to run last and
            # undo ~half the bias) — re-snap after the jumps when both are on.
            if getattr(profile, "melody_root_fifth_seventh_bias", False):
                melody = apply_root_fifth_seventh_bias(
                    melody, detect_root_pc(melody), profile.melody_scale or "natural_minor"
                )
        tracks["melody_lead"] = melody
        # (intro_melody_octave_up moved below materialize_muted_top_layer —
        # 2026-07 audit: raising the intro first made the top-layer counter
        # copy stack to +24 there and preferentially survive its median cut)

    # --- Structural accent: the ONLY melodic enrichment on top of a user's
    # MIDI (besides humanize timing/velocity above) — see
    # add_structural_accent_notes' own docstring. Universal (no per-genre
    # opt-in flag), scoped to MIDI mode since a composed/legacy melody
    # already gets its own structural shape from the hook composer.
    if midi_melody:
        added = add_structural_accent_notes(data, profile)
        if added:
            logger.info("Structural accent: %d note(s) placed at hook/drop landings", added)

    # --- Drop tension ---
    if profile.drop_tension and not midi_melody:
        apply_drop_tension(data)

    # --- Kick syncopation (also under blueprint — extras tagged kick_sync) ---
    # 2026-07 architecture audit: was running unconditionally then getting
    # silently overwritten by _apply_808_bass_lane below for every
    # bass808_enabled profile (all of trap/rage/opium have kick_syncopation
    # > 0) — pure wasted computation and a trap for future readers ("looks
    # active, isn't"). build_trap_kick_times owns density/syncopation for
    # that family now (4-6 hits/4 bars, >=2-step anti-spam law); gated off
    # outright instead of computed-then-discarded. Still applies normally
    # for non-bass808 genres.
    kick = track_notes(data, "kick")
    if profile.kick_syncopation > 0 and kick and not profile.bass808_enabled:
        bars = max(1, int(max(_note_end(n) for n in kick) // BEATS_PER_BAR) + 1)
        kick = add_kick_syncopation(kick, bars, rng, prob=profile.kick_syncopation)
        tracks["kick"] = kick

    # --- Snare riser: accelerating build before each drop (festival/EDM) ---
    if profile.snare_riser:
        end_beats = 0.0
        for key in TRACK_KEYS:
            for entry in track_notes(data, key):
                end_beats = max(end_beats, _note_end(entry))
        risers = build_snare_riser(end_beats)
        if risers:
            tracks["snare"] = sorted(track_notes(data, "snare") + risers, key=_note_step)

    # --- Beat switch: cut the drums for the bar before the final chorus (the
    # arranger's plan). Runs after every drum rebuild so nothing refills the
    # gap, and BEFORE the 808 lane so the sub sustains straight through it. ---
    switch_plan = data.get("plg_beat_switch")
    if (
        not midi_melody
        and isinstance(switch_plan, dict)
        and switch_plan.get("cut_bar") is not None
    ):
        try:
            cut_start = float(switch_plan["cut_bar"]) * BEATS_PER_BAR
            cut_end = cut_start + BEATS_PER_BAR
            removed = 0
            for lane in ("kick", "snare", "snare_layer", "clap", "hi_hats"):
                lane_notes = track_notes(data, lane)
                if not lane_notes:
                    continue
                kept = [n for n in lane_notes if not (cut_start <= _note_step(n) < cut_end)]
                removed += len(lane_notes) - len(kept)
                tracks[lane] = kept
            if removed:
                switch_plan["cut_removed"] = removed
                logger.info(
                    "Beat switch: cut %d drum hits in bar %s before the final chorus",
                    removed,
                    switch_plan["cut_bar"],
                )
        except (TypeError, ValueError) as exc:
            logger.info("Beat switch cut skipped (%s)", exc)

    # --- Kick + 808 Bass pocket LAST (after arrangement gates the drum window) ---
    # MIDI + hip-hop: rare kick + long C5 808 on the same attacks. Sub stays out.
    # Other genres keep the long sub_808 lane.
    if midi_melody:
        apply_midi_arrangement_transitions(data)
        kick = track_notes(data, "kick")
    if not midi_melody or not profile.bass808_enabled:
        _apply_sub_808_lane(data, profile, bpm)
    _apply_808_bass_lane(data, profile, bpm, pocket_kwargs=pocket_kwargs)

    if midi_melody and profile.bass808_enabled:
        disabled_midi_lanes = disabled_midi_lanes + ("sub_808",)
        _drop_midi_lanes(data, ("sub_808",))
        _reapply_midi_kick_808_clears(data)
    if not midi_melody:
        _shape_sub_808_velocity(data)

    # Pin the bass type from PRE-gate lengths so the bass-type-aware sidechain
    # (backend_core) never mistakes a gate-chopped sustained 808 for plucks.
    if getattr(profile, "sidechain_depth_bass_type_aware", False):
        kind = classify_bass_type(track_notes(data, "sub_808"))
        if kind:
            data["plg_bass_type"] = kind

    # Felix (hyperpop): the 808 cuts off exactly on the next snare hit — an
    # ADSR envelope gate, not a duck — leaving a hard silence before it retriggers.
    if getattr(profile, "sub_808_snare_gate_stutter", False):
        try:
            sub_now = track_notes(data, "sub_808")
            snare_now = track_notes(data, "snare")
            if sub_now and snare_now:
                tracks["sub_808"] = apply_808_snare_gate_stutter(sub_now, snare_now)
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("808 snare-gate stutter skipped (%s)", exc)

    # Drift Phonk tutorial: the kick is placed WHERE the 808 hits, reinforcing
    # it, instead of being generated independently of the bass line.
    if getattr(profile, "kick_synced_to_808", False):
        try:
            sub_now = track_notes(data, "sub_808")
            kick_now = track_notes(data, "kick")
            if sub_now:
                synced = apply_kick_synced_to_808(kick_now, sub_now)
                if len(synced) > len(kick_now):
                    tracks["kick"] = synced
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("Kick-synced-to-808 skipped (%s)", exc)

    # Jay Cactus (Detroit): the sub bass alternates — chorus/hook/drop move to
    # a genuinely different "spins" bass on the fx lane instead of sub_808.
    if getattr(profile, "alternating_bass_sections", False):
        try:
            moved = apply_alternating_bass_sections(data)
            if moved:
                logger.info("FX lane: %d sub_808 notes moved to alternate 'spins' bass", moved)
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("Alternating bass sections skipped (%s)", exc)

    # Broadband kick→Sub duck (baked note splits). 808 Bass is ducked after
    # the trap-pocket seal further below so the final grid owns the sidechain.
    kick_808_duck_applied = False
    if getattr(profile, "kick_808_duck", False):
        import os

        if os.environ.get("PLG_KICK_808_DUCK", "auto").strip().lower() not in ("0", "off", "false", "no"):
            kick_final = track_notes(data, "kick")
            sub = track_notes(data, "sub_808")
            if sub and kick_final:
                ducked = duck_808_under_kick(kick_final, sub)
                if len(ducked) > len(sub):
                    kick_808_duck_applied = True
                for note in ducked:
                    if isinstance(note, dict):
                        note["pan"] = 64
                tracks["sub_808"] = ducked

    # Verse = a SECOND musical idea, not the hook again: the verse melody folds
    # into the lower half of the register (the hook owns the top) — the record
    # switches ideas instead of looping one. Chorus/hook bars stay untouched.
    _fold_verse_melody(data)

    # Performance dynamics on the melodic voices — ±20-30 velocity spread with
    # deliberate soft notes, nothing machine-locked to the grid. Ken/rage leads
    # stay deliberately unquantized (2-3x wider drift).
    from style_family import genre_of as _genre_of, is_ken as _is_ken

    loose_lead = _is_ken(_genre_of(data))
    for lane in ("melody_lead", "counter_melody"):
        lane_notes = track_notes(data, lane)
        if lane_notes:
            tracks[lane] = humanize_melody_dynamics(lane_notes, rng, loose=loose_lead)

    # Lay everything into clean registers (808 sub, melody mid, drums native).
    normalize_registers(data)
    # Censor: no two kick or two 808 hits on the same tick (no phase mush).
    dedupe_heavy_overlaps(data)

    # Subtle stereo width on the melodic voices — every lead/pad note otherwise
    # hits dead center regardless of how many notes are ringing together.
    for lane in ("melody_lead", "counter_melody"):
        lane_notes = track_notes(data, lane)
        if lane_notes:
            tracks[lane] = apply_melody_pan_jitter(lane_notes, rng)

    # Commercial-simplicity cap: Billboard/Grammy sessions run on ~9-10 sounds
    # total (chord + counter-melody + a couple synth layers + a handful of drum
    # sounds) — past that, the ear-candy lanes below stop adding NEW sounds and
    # leave room for the artist instead. Core drums/melody/808 above are never
    # gated; only the four purely-optional lanes materialized here are.
    def _under_sound_cap() -> bool:
        room = profile.max_active_sounds - count_active_sound_layers(data)
        if room <= 0:
            logger.info(
                "Sound cap reached (%d/%d active lanes) — skipping remaining ear-candy lanes",
                count_active_sound_layers(data),
                profile.max_active_sounds,
            )
        return room > 0

    # Lane-priority note (2026-07 audit): with all 8 base lanes active the
    # sound cap (max_active_sounds=10) leaves TWO slots for chords/texture/fx.
    # Order = priority: the harmonic bed first (core, never starved), then the
    # melody-derived texture layers (octave-down/up, atmosphere), then the fx
    # crash/riser — previously fx+chords ran first and the texture features
    # were silently dead in every full-lane arrangement.

    # Product decision (2026-07): MIDI is now mandatory for generation (see
    # midi_ingest.has_available_material / plg_api.create_beat's early gate),
    # so every pattern reaching this point has user-brought melodic material
    # on melody_lead (plg_midi_melody set by ingest_uploaded_midi/
    # resolve_midi_folder/reference-audio ingestion). The engine's job is
    # drums + 808 answering that material, not layering its own chords/
    # counter-melody/texture on top of it — so the entire harmonic-bed +
    # counter-melody ear-candy + texture block below is now MIDI-gated off.
    # 2026-07 revert: a brief experiment un-gated melody_harmonizer_layer and
    # the octave-layer copies from MIDI mode (they only transform existing
    # melody_lead notes, unlike chord_bed/counter_melody-from-scratch/etc.).
    # Reverted per the final architecture decision: the ONLY enrichment on
    # top of a user's MIDI is (a) humanize timing/velocity and (b) the
    # narrow, structure-anchored accent (see add_structural_accent_notes) —
    # no separate counter_melody/texture/chords/fx lanes at all, through any
    # mechanism. Every sub-block below stays MIDI-gated.

    # Harmonic bed: the pinned progression VOICED as sustained chords under the
    # hook — the middle of the record stops being empty (PLG Chords lane).
    if not midi_melody and _under_sound_cap():
        try:
            from chord_bed import compose_chord_bed

            compose_chord_bed(data, profile=profile)
            if getattr(profile, "chorus_pad_holdback", False):
                held = apply_west_coast_pad_holdback(data)
                if held:
                    logger.info("Pad holdback: %d chords notes removed from the first chorus/hook pass", held)
        except Exception as exc:  # noqa: BLE001 — the bed must never break a beat
            logger.info("Chord bed skipped (%s)", exc)

    # Sam Tsui (Post Malone-style pop): counter_melody repurposed as a quiet
    # relative-major duplicate of the chord bed — the "bittersweet" pop color.
    if not midi_melody and getattr(profile, "chords_major_duplicate_layer", False):
        try:
            placed = add_major_duplicate_chords(data)
            if placed:
                logger.info(
                    "Counter-melody lane: %d major-duplicate chord notes placed (relative major +3)", placed
                )
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("Major-duplicate chord layer skipped (%s)", exc)

    # Mike Dean: scale-aware diatonic harmony (a third above, in-scale) on
    # counter_melody — repurposed, same pattern as the pop major-duplicate layer.
    if not midi_melody and getattr(profile, "melody_harmonizer_layer", False):
        try:
            placed = materialize_melody_harmonizer(data, profile=profile)
            if placed:
                logger.info("Counter-melody: %d diatonic harmony notes placed (harmonizer)", placed)
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("Melody harmonizer skipped (%s)", exc)

    # GOYXRD ("Pluggnb" build): the "bell cheat code" — counter_melody becomes
    # a full octave-up duplicate of melody_lead instead of the off-beat answer.
    if not midi_melody and getattr(profile, "bell_octave_counter_melody", False):
        try:
            placed = materialize_bell_octave_counter_melody(data)
            if placed:
                logger.info("Counter-melody lane: %d bell octave-up notes placed", placed)
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("Bell octave counter-melody skipped (%s)", exc)

    # Jay Cactus (Detroit): piano top-line — only the melody's upper half
    # survives (lower notes muted before render), pitched up an octave, on
    # counter_melody's own preset.
    if not midi_melody and getattr(profile, "melody_top_layer_muted_lows", False):
        try:
            placed = materialize_muted_top_layer(data)
            if placed:
                logger.info("Counter-melody lane: %d muted-top-layer notes placed", placed)
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("Muted top layer skipped (%s)", exc)

    # Jay Cactus (Detroit): the whole lead pitches up an octave for the intro
    # only. Runs AFTER the muted-top-layer copy above so the counter is taken
    # from the un-raised melody (both lanes then sit exactly +12 in the intro,
    # not +24 stacked).
    if not midi_melody and getattr(profile, "intro_melody_octave_up", False):
        raised = apply_intro_octave_up(data)
        if raised:
            logger.info("Intro: %d melody notes pitched up an octave", raised)

    # Half-speed, octave-down melody duplicate (trap-only, opt-in) — claims the
    # Texture lane before the generic atmosphere layer if the genre wants it.
    if not midi_melody and _under_sound_cap():
        try:
            from fx_layer import materialize_melody_octave_down_layer

            placed = materialize_melody_octave_down_layer(data, profile=profile)
            if placed:
                logger.info("Texture lane: %d octave-down melody notes placed", placed)
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("Octave-down layer skipped (%s)", exc)

    # Minimal dark trap: the OPPOSITE direction — a quiet octave-UP shimmer
    # (claims the Texture lane first if octave-down above didn't already).
    if not midi_melody and _under_sound_cap():
        try:
            from fx_layer import materialize_melody_octave_down_layer as _materialize_octave_layer

            placed = _materialize_octave_layer(
                data, profile=profile, semitones=12, velocity_drop=30,
                flag_name="melody_octave_up_layer_quiet", texture_kind="octave_up_layer_quiet",
                env_var="PLG_OCTAVE_UP_LAYER",
            )
            if placed:
                logger.info("Texture lane: %d octave-up shimmer notes placed", placed)
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("Octave-up layer skipped (%s)", exc)

    # Mike Dean/Travis: live bass doubles the synth 808 on chorus/hook/drop
    # (additive — sub_808 keeps its full part). Runs after the octave-layer
    # shimmers above so it never preempts their claim on the texture lane.
    if not midi_melody and _under_sound_cap() and getattr(profile, "live_bass_texture_sections", False):
        try:
            placed = apply_live_bass_texture_sections(data)
            if placed:
                logger.info("Texture lane: %d live-bass notes doubled under sub_808", placed)
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("Live-bass texture sections skipped (%s)", exc)

    # Scene layer: one atmosphere (rain/vinyl/siren) triggered per section.
    if not midi_melody and _under_sound_cap():
        try:
            from fx_layer import materialize_texture_notes

            materialize_texture_notes(data)
        except Exception as exc:  # noqa: BLE001 — atmosphere must never break a beat
            logger.info("Texture lane skipped (%s)", exc)

    # Mike Dean (Travis Scott): a reversed-chords swell into chorus/hook/drop
    # (PLG FX lane) — claims the lane before the generic crash below if the
    # genre wants it (one channel = one sample, they're mutually exclusive).
    # Stays MIDI-gated: derived from chord_bed's chords, which stay off in MIDI mode.
    if not midi_melody and _under_sound_cap() and getattr(profile, "fx_riser_from_chords", False):
        try:
            from fx_layer import materialize_riser_notes

            placed = materialize_riser_notes(data)
            if placed:
                logger.info("FX lane: %d reversed-chords risers placed", placed)
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("Riser lane skipped (%s)", exc)

    # Mike Dean: an occasional delay-tail accent into a handful of landings —
    # claims the fx lane before the generic crash below if the genre wants it.
    if not midi_melody and _under_sound_cap() and getattr(profile, "delay_accent_lane", False):
        try:
            from fx_layer import materialize_delay_accent_notes

            placed = materialize_delay_accent_notes(data, rng=rng)
            if placed:
                logger.info("FX lane: %d delay-tail accents placed", placed)
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("Delay accent lane skipped (%s)", exc)

    # Audible ear candy: crash one-shots on chorus/hook landings (PLG FX lane).
    # starter_kit drops the lane again if the library has no crash sample.
    if not midi_melody and _under_sound_cap():
        try:
            from fx_layer import materialize_fx_notes

            placed = materialize_fx_notes(data)
            if placed:
                logger.info("FX lane: %d crash/impact landings placed", placed)
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("FX lane skipped (%s)", exc)

    # Cowbell accents — the Carti signature, gated to Carti lanes only.
    if _under_sound_cap():
        try:
            from fx_layer import materialize_cowbell_notes

            materialize_cowbell_notes(data)
        except Exception as exc:  # noqa: BLE001 — cowbell must never break a beat
            logger.info("Cowbell lane skipped (%s)", exc)

    if getattr(profile, "chorus_density_ramp", False):
        removed = apply_chorus_density_ramp(data)
        if removed:
            logger.info("Chorus density ramp: %d ear-candy notes thinned from section openers", removed)

    # "Trust the Process": drums answer the melody's section-by-section density
    # (opt-in, baked as a removal-only postprocess — see apply_melody_drum_density_inverse).
    density_inverse_removed = 0
    if getattr(profile, "melody_drum_density_inverse", False):
        density_inverse_removed = apply_melody_drum_density_inverse(data)
        if density_inverse_removed:
            logger.info(
                "Melody/drum density inverse: %d drum extras thinned under busy melody sections",
                density_inverse_removed,
            )

    # Full-kit symbiosis: kick/snare/clap/hats/808/layers/fx answer melody+FLEX.
    # bass808_enabled: kick is phase-locked to 808_bass at birth
    # (_apply_808_bass_lane) — never let symbiosis move its time_step
    # afterward (only velocity), same treatment snare/clap/hats already get
    # here. That removes the need for a compensating re-align pass below;
    # 808_bass built once against kick's true final position stays valid.
    try:
        from melody_beat_symbiosis import apply_melody_beat_symbiosis, build_melody_energy_card

        if not isinstance(data.get("plg_melody_energy"), dict):
            build_melody_energy_card(data)
        apply_melody_beat_symbiosis(
            data, move_kick_timing=not getattr(profile, "bass808_enabled", False)
        )
    except Exception as exc:  # noqa: BLE001 — drums already built; symbiosis is bonus
        logger.info("Melody–beat symbiosis skipped (%s)", exc)

    # Deon/Infinite: don't open the hook on the tonic (opt-in, single-note move).
    if getattr(profile, "melody_avoid_root_start", False):
        mel = track_notes(data, "melody_lead")
        if mel:
            tracks["melody_lead"] = avoid_melody_root_start(mel, scale=profile.melody_scale or "natural_minor")

    # Dirkey Destroy Lonely: the root opens late AND off the scale (opt-in,
    # single-note move) — the opposite move from melody_avoid_root_start above.
    if getattr(profile, "melody_root_delay_tension", False):
        mel = track_notes(data, "melody_lead")
        if mel:
            tracks["melody_lead"] = apply_root_delay_tension(mel, scale=profile.melody_scale or "phrygian")

    # Dirkey "How To Make Underground Beats": same move, on the 808 this time —
    # its opening note opens late AND off the scale.
    if getattr(profile, "sub_808_root_delay_tension", False):
        sub = track_notes(data, "sub_808")
        if sub:
            tracks["sub_808"] = apply_root_delay_tension(sub, scale=profile.melody_scale or "natural_minor")

    # Nettspend ("Early Life Crisis"): melodies trend up, not neutral/random —
    # runs BEFORE the voice-leading check below so counter_melody follows the
    # melody's true (already-biased) final contour, not its pre-bias shape.
    if getattr(profile, "melody_upward_bias", False):
        mel = track_notes(data, "melody_lead")
        if mel:
            tracks["melody_lead"] = apply_melody_upward_bias(mel)

    # Floor for counter_melody: the genre wants one (profile.counter_melody —
    # true by default, off only for genres that explicitly don't want the
    # lane, e.g. minimal_trap/house/techno/lofi) but nothing above claimed it —
    # the LLM composer's own line is only a soft prompt suggestion, so an
    # empty array is a legal response. Runs LAST among counter_melody
    # generators (after harmonizer/bell-octave/muted-top/major-duplicate/
    # opium-repair above) so it never preempts a genre-specific mechanism, and
    # BEFORE the direction-mirror/register-collision passes below so those can
    # still act on whatever it places.
    if not midi_melody and getattr(profile, "counter_melody", True) and not track_notes(data, "counter_melody"):
        try:
            placed = materialize_sparse_counter_melody_fallback(data, profile=profile, rng=rng)
            if placed:
                logger.info(
                    "Counter-melody lane: %d sparse call-and-response notes placed (fallback)", placed
                )
        except Exception as exc:  # noqa: BLE001 — ear candy must never break a beat
            logger.info("Sparse counter-melody fallback skipped (%s)", exc)

    # Richie Souf ("New Atlanta Sound"): counter_melody follows melody_lead's
    # phrase direction instead of fighting it (opt-in, contour mirror only on
    # conflict — melody_lead is never touched).
    if getattr(profile, "counter_melody_matches_direction", False):
        mel = track_notes(data, "melody_lead")
        cnt = track_notes(data, "counter_melody")
        if mel and cnt:
            tracks["counter_melody"] = apply_voice_leading_direction(mel, cnt)

    # Nettspend: no two melodic lines share the same pitch space at the same
    # time — runs LAST among melody/counter pitch-shaping steps (after upward
    # bias, bell-octave/muted-top-layer counter-melody variants, and the
    # voice-leading contour mirror above) so it resolves the FINAL pitches,
    # not an intermediate state that a later step could re-collide.
    if getattr(profile, "register_collision_avoidance", False):
        mel = track_notes(data, "melody_lead")
        cnt = track_notes(data, "counter_melody")
        if mel and cnt:
            adjusted = apply_register_collision_avoidance(data)
            if adjusted:
                logger.info("Counter-melody: %d notes octave-shifted to clear register collisions", adjusted)

    # Keep a terminal MIDI-gate assertion here as new style passes are commonly
    # appended above this point. It prevents a future fallback from silently
    # resurrecting a user-disabled lane — including a composer elsewhere in
    # the pipeline that forgets the plg_midi_melody check (see
    # symbolic_composer.apply_symbolic_composition's rage/opium melody
    # replacement, which is exactly that bug, fixed at the source but this
    # net stays as defense-in-depth).
    if midi_melody:
        for lane in disabled_midi_lanes:
            tracks.pop(lane, None)
        for field in ("plg_sample_picks", "plg_sound_paths"):
            mapped = data.get(field)
            if isinstance(mapped, dict):
                for lane in disabled_midi_lanes:
                    mapped.pop(lane, None)

    pitch_bends = build_pitch_bend_automation(data)
    # "chords" is not looped over by midi_export.py's TRACK_KEYS-only export, so
    # glides are only built for lanes that actually reach a .mid file.
    for glide_lane in ("melody_lead", "counter_melody"):
        pitch_bends.extend(build_chord_change_glides(data, track_key=glide_lane))
    if pitch_bends:
        data["pitch_bend_automation"] = pitch_bends

    # The bass808 family owns a separate lane, so the earlier sub_808 duck
    # cannot touch it. Bake a real gap into long 808_bass sustains while
    # preserving the fixed 112 velocity: this is timing/gating, not dynamics.
    if getattr(profile, "bass808_enabled", False) and getattr(profile, "kick_808_duck", False):
        import os

        if os.environ.get("PLG_KICK_808_DUCK", "auto").strip().lower() not in (
            "0",
            "off",
            "false",
            "no",
        ):
            if _duck_808_bass_lane(data):
                kick_808_duck_applied = True

    if midi_melody:
        try:
            from midi_placement_symbiosis import apply_midi_placement_symbiosis

            placement = apply_midi_placement_symbiosis(data)
            if any(value for key, value in placement.items() if key != "enabled"):
                logger.info("MIDI placement symbiosis: %s", placement)
        except Exception as exc:  # noqa: BLE001 — final placement must never block export
            logger.info("MIDI placement symbiosis skipped: %s", exc)

    # Per-generation hat variation (hihat_rolls.vary_hats): the one writer of the
    # variety seed's effect on the finished generated hat line. It runs here, after
    # every pass that draws from the shared rng (humanize_velocities and the rest
    # draw once per hat, so a changed hat count anywhere earlier would re-roll the
    # micro-timing of snare, clap and melody), after the phrase contract and the
    # density floor (which refill a plainly dropped eighth), and before the dynamics
    # seal below so the outro fade still reaches the ghosts. Never the user's own hats.
    if not data.get("plg_midi_hats") and track_notes(data, "hi_hats"):
        from hihat_rolls import vary_hats

        tracks["hi_hats"] = vary_hats(track_notes(data, "hi_hats"), data)

    # Nothing after this point may alter note dynamics. Re-assert the tuned
    # kick/808 balance after every creative and collision-avoidance pass.
    shaped = _apply_section_dynamics(data)
    if shaped:
        logger.info("Section dynamics: %d transient/octave-layer note(s) stepped down", shaped)
    _apply_outro_fade(data)
    _seal_song_note_bounds(data)
    # Several genre-specific 808 decorations run late (ascent, section
    # switches, root-delay tension). Seal monophony after *all* of them;
    # otherwise a longer two-minute form lets those decorations overlap.
    sub_final = track_notes(data, "sub_808")
    if sub_final:
        from sub_808_gen import MIN_SUB_LENGTH, filter_close_onsets

        # Late decorations (ascent, section switches, root-delay tension)
        # can remove/shift a neighbor that an EARLIER breath pass (inside
        # _apply_sub_808_lane) already used to justify shrinking a note
        # below MIN_SUB_LENGTH. mono_legato_bass/apply_808_breath only ever
        # shrink a length, never grow it back — so that stale short length
        # would otherwise survive untouched even once its neighbor is gone
        # and the gap has opened back up. Filter close onsets first, then
        # reset every surviving non-glide/slide note back up to the floor
        # so THIS pass's breath recomputes the trim from the real final gap
        # instead of compounding an earlier, now-outdated shrink.
        sub_final = filter_close_onsets(sub_final)
        for note in sub_final:
            if not (note.get("glide") or note.get("slide")):
                note["length"] = max(float(note.get("length", 0) or 0), MIN_SUB_LENGTH)
        # Mono alone only trims at the next onset (legato); restore the
        # deliberate silence before ordinary hits after the late writers.
        tracks["sub_808"] = apply_808_breath(mono_legato_bass(sub_final))
    if getattr(profile, "bass808_enabled", False):
        # Uploaded MIDI may be intentionally loose, but FL's Piano Roll must
        # show the kick/808 skeleton on-grid. Keep micro-dust for generated
        # beats only; it is not allowed to turn a MIDI-owned low end into
        # random-looking decimal positions.
        micro_humanize = getattr(profile, "kick808_micro_humanize", False) and not midi_melody
        _seal_midi_kick808_pocket(data)
        _ensure_808_on_every_kick(data)
        if midi_melody:
            # The roll/kick negotiation, second half: the seal spared kicks a
            # roll would have emptied their bar for, so the roll now gives way
            # over exactly those. Must run BEFORE the context audit, which
            # counts a surviving kick-under-roll as a hard reason.
            try:
                from midi_fullkit_director import apply_lowend_roll_yield

                yielded = apply_lowend_roll_yield(data)
                if yielded.get("hats_removed"):
                    logger.info(
                        "Low-end roll yield: %d roll hat(s) moved aside for %d protected kick(s)",
                        yielded["hats_removed"], yielded["protected_kicks"],
                    )
            except Exception as exc:  # noqa: BLE001 — a negotiation must never break a beat
                logger.info("Low-end roll yield skipped: %s", exc)
            capped = _cap_midi_kicks_per_bar(data)
            if capped:
                logger.info("MIDI kick/808 density repair: removed %d late extra pair(s)", capped)
        _normalize_kick808_dynamics(data, micro_humanize=micro_humanize)
        kick808_audit = _audit_kick808_symbiosis(data)
        if "off_scale_808" in kick808_audit.get("reasons", []):
            corrected = _correct_off_scale_808_notes(data)
            if corrected:
                logger.info("Re-snapped %d off-scale 808_bass note(s) to the pinned scale", corrected)
                kick808_audit = _audit_kick808_symbiosis(data)
        if midi_melody:
            context_audit = _audit_midi_kick808_context(data)
            # A restated 4-bar figure is varied whether or not anything FAILED
            # (2026-08-20). The gate only ever caught a song that was one
            # signature from end to end; this acts on the audit's own
            # repeated_blocks meter, so it reaches the ordinary case the ear
            # objects to. Re-audited afterwards like any other repair, and the
            # kick/808 gate below still has the last word on the result.
            varied = _vary_repeated_lowend_cycle(data, context_audit)
            if varied:
                logger.info("Varied %d repeated 4-bar low-end block(s)", varied)
                # It writes bass808_gen._BASS_VEL straight onto the new note,
                # which is the lane BASE and not the section's level. Re-assert
                # before re-auditing, or the gate rejects our own repair on
                # velocity_drift (the normalizer is idempotent).
                _normalize_kick808_dynamics(data, micro_humanize=micro_humanize)
                context_audit = _audit_midi_kick808_context(data)
                kick808_audit = _audit_kick808_symbiosis(data)
            # A failing window is rebuilt once and re-measured rather than
            # logged and shipped — see _repair_midi_kick808_context.
            if not context_audit["passed"]:
                repaired = _repair_midi_kick808_context(data, context_audit)
                if repaired:
                    logger.info(
                        "Repaired %d MIDI kick/808 context issue(s): %s",
                        repaired,
                        context_audit["reasons"],
                    )
                    # Same reason as the variation pass above: a rebuilt
                    # window comes back at the lane base, not the section's.
                    _normalize_kick808_dynamics(data, micro_humanize=micro_humanize)
                    context_audit = _audit_midi_kick808_context(data)
                    context_audit["repaired"] = repaired
                    kick808_audit = _audit_kick808_symbiosis(data)
            data["plg_midi_kick808_context_audit"] = context_audit
            if not context_audit["passed"]:
                kick808_audit["reasons"] = list(kick808_audit.get("reasons") or []) + [
                    f"context:{reason}" for reason in context_audit["reasons"]
                ]
                kick808_audit["passed"] = False
        if midi_melody:
            # Vertical consonance against the sounding melody — the last
            # owner of 808 pitch, deliberately after the audits above are
            # computed (they validate model-consistency; this validates the
            # ear). Never a gate: telemetry in plg_808_vertical.
            try:
                vertical = _correct_808_vertical_clashes(data)
                if vertical.get("repaired"):
                    logger.info(
                        "Vertical 808 repair: %d of %d clashing attack(s) re-pitched against the sounding melody",
                        vertical["repaired"],
                        vertical["clashes_found"],
                    )
            except Exception as exc:  # noqa: BLE001 — a repair must never block export
                logger.info("Vertical 808 repair skipped (%s)", exc)
            try:
                voicing = _measure_808_voicing(data)
                if voicing.get("attacks_colored"):
                    logger.info(
                        "808 voicing: %d of %d attack(s) on a chord tone (%d third / %d fifth slot(s))",
                        voicing["attacks_colored"], voicing["attacks_total"],
                        voicing["third"], voicing["fifth"],
                    )
            except Exception as exc:  # noqa: BLE001 — telemetry must never block export
                logger.info("808 voicing telemetry skipped (%s)", exc)
        data["plg_kick808_audit"] = kick808_audit
        if not kick808_audit["passed"]:
            logger.warning("Final kick/808 symbiosis audit failed: %s", kick808_audit)
        # Strictly after the audit above — see _apply_kick808_micro_timing's
        # docstring for why the ordering matters (pairing/density/coverage
        # all validate the clean un-jittered grid; this is cosmetic dust on
        # top that no gate needs to see).
        if micro_humanize:
            _apply_kick808_micro_timing(data, bpm)
    if midi_melody:
        move_late_midi_octave_lift_to_middle(data)
        # Strictly after the octave-lift move (the last writer of melody_lead)
        # and after the whole kick/808 chain has sealed and audited: every drum
        # owner read the FULL melody, so this edit provably cannot move them.
        # The audits below then measure the melody that actually ships, not one
        # that no longer exists.
        try:
            thinned = _apply_section_melody_arrangement(data)
            if thinned:
                logger.info(
                    "Section melody arrangement: %d note(s) removed across %d section(s)",
                    thinned, len(data.get("plg_melody_arrangement", {}).get("sections") or []),
                )
        except Exception as exc:  # noqa: BLE001 — a colour must never block export
            logger.info("Section melody arrangement skipped (%s)", exc)
        if track_notes(data, "hi_hats") and not data.get("plg_midi_hats"):
            tracks["hi_hats"] = settle_generated_hat_register(track_notes(data, "hi_hats"), rng)
        hat_audit = _audit_hat_symbiosis(data)
        data["plg_hat_symbiosis_audit"] = hat_audit
        if not hat_audit["passed"]:
            logger.warning("Final melody/hat symbiosis audit failed: %s", hat_audit)
        try:
            from midi_fullkit_director import audit_fullkit_contract, audit_global_midi_symbiosis

            fullkit_audit = audit_fullkit_contract(data)
            data["plg_fullkit_symbiosis_audit"] = fullkit_audit
            if not fullkit_audit["passed"]:
                logger.warning("Final full-kit symbiosis audit failed: %s", fullkit_audit)
            global_audit = audit_global_midi_symbiosis(data)
            data["plg_global_symbiosis_audit"] = global_audit
            if not global_audit["passed"]:
                logger.warning("Final seven-lane symbiosis audit failed: %s", global_audit)
        except Exception as exc:  # noqa: BLE001
            logger.info("Full-kit symbiosis audit skipped: %s", exc)

    # Every change-point in the FINISHED pattern, plus the gap between them.
    # Measured, not declared: written last so it reads the notes that survived
    # every refill pass, and so the "something changes every 8-16 bars"
    # requirement is checkable in pattern.json instead of only by ear.
    try:
        _measure_section_events(data)
    except Exception as exc:  # noqa: BLE001 — telemetry must never block export
        logger.info("Section event map skipped (%s)", exc)

    # One traceable registry for the producer techniques extracted from the
    # video research.  It is audit-only for now: this exposes which rules
    # actually reached the export before a future director chooses moves.
    try:
        from producer_rulebook import build_producer_rule_audit

        data["plg_producer_rule_audit"] = build_producer_rule_audit(data)
    except Exception as exc:  # noqa: BLE001
        logger.info("Producer rule audit skipped (%s)", exc)

    # --- Producer metadata for FL / mixer ---
    melody_vol = int(round(10000 * (10 ** (profile.melody_volume_db / 20.0))))
    mixer_levels = {
        "kick": profile.kick_volume,
        "sub_808": profile.sub_808_volume,
        "melody_lead": melody_vol,
        "counter_melody": melody_vol,
    }
    data["plg_mixer_levels"] = mixer_levels

    # Profile-driven, not note-count-driven: bass808_enabled genres route
    # their 808 duty through 808_bass even when that lane is empty (kick/
    # 808_bass generation was removed — see _apply_808_bass_lane), and
    # track_notes-based selection would silently fall back to sub_808, which
    # is intentionally dropped for that same family.
    active_808_lane = "808_bass" if getattr(profile, "bass808_enabled", False) else "sub_808"
    meta = {
        "sidechain": build_sidechain_hints(kick, target=active_808_lane),
        "kick_808_duck_applied": kick_808_duck_applied,
        "melody_drum_density_inverse_removed": density_inverse_removed,
        "low_cut_hz": profile.melody_low_cut_hz,
        "melody_low_cut_hz": profile.melody_low_cut_hz,
        "master_fader_db": profile.master_fader_db,
        "melody_insert_db": profile.melody_insert_db,
        "master_soft_clip": profile.soft_clip,
        "mixer_levels": mixer_levels,
        "reverb_duck": {"melody_lead": 0.7},
        "bpm_drift": float(data.get("plg_vibe_drift", 0) or 0),
        "hat_choke_group": "plg_hats",
        "808_attack_ms": ATTACK_FLATTEN_MS,
        "mono_stereo_drop_bars": PITCH_BEND_PHRASE_BARS,
        "pre_snare_shift_ms": PRE_SNARE_SHIFT_MS,
        "hat_db_down": HAT_DB_DOWN,
        "key": data.get("plg_key"),
        "key_matched_808": bool(profile.eight08),
        "melody_lag_ms": profile.melody_lag_ms,
        "voicing_spread": profile.voicing_spread,
        "kick_syncopation": profile.kick_syncopation,
        "markov_hats": profile.markov_hats,
        "snare_riser": profile.snare_riser,
        "genre": profile.name,
        "drive": round(profile.drive, 2),
        "max_drive": max_drive,
        "background_bus_eq": bool(profile.background_bus_eq),
    }
    try:
        from melody_beat_symbiosis import merge_flex_producer_meta

        meta = merge_flex_producer_meta(data, meta)
    except Exception:  # noqa: BLE001
        pass
    data["plg_producer_meta"] = meta
    data["plg_genre"] = profile.name

    steps = list(data.get("manual_steps") or [])
    steps.insert(0, (
        "Piano Roll: двойной клик по клипу в плейлисте открывает ноты ИМЕННО этого "
        "инструмента. Серые полосы = призраки соседних дорожек (настройка FL: "
        "Alt+V выключает). / Double-click a playlist clip to edit THAT instrument; "
        "grey bars are FL's ghost channels (Alt+V toggles them)."
    ))
    steps.append("Pre-snare shift: clap/snare 2–5 ms early (Atlanta push).")
    steps.append("6 dB rule: hats ducked under clap/snare velocity.")
    steps.append("Open-hat choke: closed hats cut open tails (choke_group plg_hats).")
    steps.append("808 attack +15 ms soften — click-kick punches first, no phase mush.")
    steps.append("Pitch bend: -2 st dip every 8 bars on melody (see pitch_bend_automation).")
    steps.append("Mono→stereo drop: narrow verse melody, wide at phrase drop.")
    if profile.melody_lag_ms > 0:
        steps.append(f"Groove lag: melody dragged ~{profile.melody_lag_ms:.0f} ms late (lazy pocket).")
    if profile.voicing_spread > 0:
        steps.append("Chord voicing: ~octave inversions open the harmony for vocal space.")
    if profile.markov_hats:
        steps.append("Hi-hats: steady 1/8 grid + sparse 1/32 rolls (hihat_rolls).")
    if data.get("plg_drum_blueprint"):
        grid = data.get("plg_drum_grid") or "cell"
        steps.append(f"Drum blueprint: pocket '{grid}' from DRUM_CELLS / archetype + MIDI fingerprint.")
    if track_notes(data, "808_bass"):
        steps.append(
            "Kick+808 call/response: shared foundations, kick punctuation, "
            "bass-only answers, mono short tails and harmonic-root lock."
        )
        if kick_808_duck_applied:
            steps.append("Kick→808 sidechain: baked ~−4 dB duck under every kick hit.")
        if profile.eight08_slides:
            steps.append("808 slides: octave stabs at end of each 4-bar phrase.")
    if profile.kick_syncopation > 0:
        steps.append("Kick syncopation: weighted off-grid hits on weak steps, clear of the clap.")
    if profile.snare_riser:
        steps.append("Snare riser: 1/4→1/32 accelerating build + pitch-up into each drop.")
    if data.get("plg_key"):
        steps.append(f"Key lock: melody + 808 snapped to {data['plg_key']}.")
    steps.append(f"Genre profile: {profile.name}" + (" · MAX DRIVE" if max_drive else "") + ".")
    data["manual_steps"] = steps[:16]

    # Run the same closed-contract cleanup at the final point of the active
    # route.  This also removes stale mixer/sample references to retired lanes.
    if midi_melody:
        from midi_roles import split_into_lanes
        from midi_validate import sanitize_midi_mode

        # Last, after every audit above read the whole melody as one lane: the
        # tagged chord/bass/counter notes become their own lanes (midi_roles).
        split_into_lanes(data)
        sanitize_midi_mode(data)
    logger.info("Producer brain applied (rage=%s)", flags["rage"])
    return data

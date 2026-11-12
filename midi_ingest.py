"""Ingest user .mid files from the library so their notes actually play.

Moves MIDI from a build-guide reference (Tier 2) to a real, played part
(Tier 1): the best-matching .mid in the library is parsed and merged into the
pattern (high notes → melody, low notes → 808), then humanize_pattern key-locks
it like everything else.

Beat-relative and tempo-agnostic: reads ticks / ticks_per_beat, so the loop
lines up at any BPM. Only fires when a .mid name matches the prompt or the user
explicitly asks for MIDI — otherwise the LLM melody is left untouched.
"""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path
from typing import Any

import mido

import midi_roles
from library_paths import MIDI_FOLDER, distinct_midi_files, is_service_midi
from music_theory import name_for_midi
from pattern_utils import parse_note_name, track_notes

logger = logging.getLogger("plg.midi_ingest")

REGISTER_SPLIT = 48  # MIDI C3 — below = 808/bass, at/above = melody
MAX_BEATS = 64.0
MIDI_EXTS = (".mid", ".midi")
_EXPLICIT = ("midi", "миди", "мелодию из", "из миди", "loop", "луп")

# Explicit user upload (audit 2026-07): a NEW, untrusted-input surface, unlike
# the other two ingest tiers above which only ever read from a curated
# library the app itself scanned. File-size and note-count caps are
# defense-in-depth against a pathological/malicious .mid (read_midi_notes
# already never raises, but a huge file could still be slow to parse).
#
# UPLOAD_MAX_BEATS (2026-07 audit follow-up): raised from 32 (8 bars) past
# reference-reverse's own MAX_BEATS=64 (16 bars) to 128 (32 bars) — arranger.
# _tile's cursor-tiling fix (see _uses_full_core_material) exists specifically
# to make a core LONGER than a single section surface its middle/tail across
# real sections instead of being lost, but that only ever engages for
# genuinely long, real material. An 8-bar cap meant the fix could never
# exercise on the two most direct, highest-trust sources of that material —
# an explicit upload and the curated PLG_MIDI folder (resolve_midi_folder
# below reuses this same function/cap). 32 bars is a deliberate ceiling, not
# an arbitrary one: arrange_song's own song templates top out around 52
# total bars (DEFAULT_SECTIONS/BUILDUP_SECTIONS/WEST_COAST_SECTIONS), so
# anything past that many bars of core material could never be heard by any
# section's cursor anyway — no point accepting beats the arranger can't reach.
UPLOAD_MAX_FILE_BYTES = 2_000_000  # 2 MB — generous for a single melodic line/progression
UPLOAD_MAX_NOTES = 2000
UPLOAD_MAX_BEATS = 128.0
UPLOAD_RIGHTS_WARNING = "Only upload MIDI you own or have the rights to use."

# PLG_MIDI means the synth melody only. A full-arrangement .mid also carries
# drum parts, and read_midi_notes used to merge every track and channel, so a
# hi-hat line (GM note 42 = F#3) landed in melody_lead as "melody".
GM_DRUM_CHANNEL = 9  # mido numbers channels from 0: this is GM channel 10
_DRUM_WORDS = frozenset({
    "drum", "drums", "perc", "percussion", "hat", "hats", "hihat", "hihats",
    "hh", "kick", "kicks", "snare", "snares", "clap", "claps", "shaker",
    "cymbal", "cymbals", "rim", "rimshot",
})


def _is_drum_name(text: str) -> bool:
    """Whole-word match, so "Hats" is a drum part and "Thatcher" is not."""
    return any(word in _DRUM_WORDS for word in re.findall(r"[a-z0-9]+", text.lower()))


def read_midi_notes(path: Path, *, max_beats: float = MAX_BEATS) -> list[dict[str, Any]]:
    """Parse a .mid into PLG notes (time_step + length in beats)."""
    try:
        mid = mido.MidiFile(str(path))
    except Exception as exc:  # malformed MIDI — skip, never crash a generation
        logger.warning("MIDI read failed %s: %s", path.name, exc)
        return []

    tpb = mid.ticks_per_beat or 480
    notes: list[dict[str, Any]] = []
    skipped_drums = 0
    for track in mid.tracks:
        drum_track = _is_drum_name(track.name or "")
        abs_tick = 0
        active: dict[tuple[int, int], tuple[int, int]] = {}
        for msg in track:
            abs_tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                if drum_track or msg.channel == GM_DRUM_CHANNEL:
                    skipped_drums += 1
                    continue
                active[(msg.channel, msg.note)] = (abs_tick, msg.velocity)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                start = active.pop((msg.channel, msg.note), None)
                if start is None:
                    continue
                start_tick, vel = start
                start_beat = start_tick / tpb
                if start_beat >= max_beats:
                    continue
                length_beat = max(0.0625, (abs_tick - start_tick) / tpb)
                notes.append({
                    "time_step": round(start_beat, 5),
                    "note": name_for_midi(msg.note),
                    "length": round(length_beat, 5),
                    "velocity": max(1, min(127, int(vel))),
                    "midi": True,
                })
    if skipped_drums:
        logger.info("MIDI %s: left out %d drum-part notes", path.name, skipped_drums)
    notes.sort(key=lambda n: (n["time_step"], n["note"]))
    return notes


def split_by_register(
    notes: list[dict[str, Any]],
    split: int = REGISTER_SPLIT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split notes into (melody, bass) by pitch."""
    melody: list[dict[str, Any]] = []
    bass: list[dict[str, Any]] = []
    for note in notes:
        try:
            midi = parse_note_name(str(note["note"]))
        except (ValueError, KeyError):
            melody.append(note)
            continue
        (bass if midi < split else melody).append(note)
    return melody, bass


def find_library_midi(library_root: Path, *, extra_root: Path | None = None) -> list[Path]:
    """``extra_root`` — the dedicated PLG_MIDI folder (2026-07: split out as
    its own top-level sibling of the sample library, see
    library_paths.DEFAULT_MIDI_DIR), searched IN ADDITION TO library_root —
    merge, not replace, so a library that hasn't migrated its MIDI out yet
    keeps working exactly as before.

    Excludes drum-lane filenames (2026-07 audit) — same "drums" bucket
    midi_corpus._detect_lane already uses to keep one-shot drum-kit MIDI
    stubs (kick/snare/perc/vox/riser/…) out of its melody/808/hats pools.
    This is the legacy whole-library melody fallback (only used by
    pick_library_midi/ingest_library_midi below, and only reached when
    resolve_midi_folder/reference-reverse/midi_corpus have all missed) — it
    had no such filtering before, so a stray drum stub could in principle
    win the "melody" if it was the only/best-scoring .mid in the library."""
    from midi_corpus import _detect_lane

    found: set[Path] = set()
    roots = [library_root]
    if extra_root is not None and extra_root != library_root:
        roots.append(extra_root)
    for root in roots:
        if not root.is_dir():
            continue
        for ext in MIDI_EXTS:
            found.update(
                p for p in root.rglob(f"*{ext}")
                if p.is_file() and _detect_lane(p.stem) != "drums" and not is_service_midi(p)
            )
    return sorted(found)


def _score_name(path: Path, tokens: set[str]) -> int:
    name = path.stem.lower()
    return sum(1 for token in tokens if token and token in name)


def pick_library_midi(
    library_root: Path, prompt: str = "", style: str = "", *, extra_root: Path | None = None
) -> Path | None:
    """Best .mid for the prompt, or None when nothing matches (LLM melody kept)."""
    candidates = find_library_midi(library_root, extra_root=extra_root)
    if not candidates:
        return None
    from sound_descriptors import descriptor_hints

    text = f"{prompt} {style}".lower()
    tokens = set(re.findall(r"[a-z0-9]{3,}", text))
    tokens |= set(descriptor_hints(prompt, style, track="melody_lead"))
    best = max(candidates, key=lambda p: _score_name(p, tokens))
    if _score_name(best, tokens) > 0 or any(k in text for k in _EXPLICIT):
        return best
    return None


def _estimate_bpm_from_notes(path: Path) -> float | None:
    """Deterministic tempo estimate for note-only MIDI (no set_tempo event).

    A file with neither an embedded tempo nor a BPM in its name used to fall
    through to ``generation_variety``'s time-seeded bpm roll, so the SAME
    file generated at a different tempo every run (measured 2026-08-15:
    Graveyard 144 -> 147 and Shuttle 148 -> 153 between two batches, with the
    arrangement length following). The estimate is a pure function of the
    file's note content:

    * onsets are read in the file's own beat grid;
    * without a tempo event every player assumes the MIDI default 120 BPM
      clock, so that clock converts the median onset interval to seconds;
    * ``120 / median_interval`` is folded into the generation range by
      doubling/halving (octave ambiguity is inherent to beat-only data).

    The result is stable per file, unlike the roll it replaces. Embedded
    tempo and the filename label both still win over it.
    """
    notes = read_midi_notes(path)
    onsets = sorted({round(float(n.get("time_step", 0.0) or 0.0), 4) for n in notes})
    if len(onsets) < 3:
        return None
    gaps = [b - a for a, b in zip(onsets, onsets[1:]) if b - a > 1e-6]
    if not gaps:
        return None
    interval_beats = sorted(gaps)[len(gaps) // 2]
    if interval_beats <= 1e-6:
        return None
    bpm = 120.0 / interval_beats
    while bpm > 200.0:
        bpm /= 2.0
    while bpm < 70.0:
        bpm *= 2.0
    return round(bpm, 1)


def _attach_midi_file_meta(pattern: dict[str, Any], path: Path) -> None:
    """Stamp BPM, key_signature, control markers, and instrument-family hints."""
    from midi_genre_detect import (
        read_key_signature,
        read_midi_control_markers,
        read_midi_instrument_hints,
        read_tempo_bpm,
    )

    tempo = read_tempo_bpm(path)
    tempo_source = "midi_tempo"
    if tempo is None:
        # A lot of marketplace MIDI is note-only: it deliberately contains no
        # set_tempo event but still names the intended pocket ("...150 BPM").
        # Treat that explicit source label as a lock too.  Otherwise a random
        # genre pick can turn a 150 BPM melody into a slow 130 BPM beat.
        from midi_corpus import bpm_from_name

        tempo = bpm_from_name(path.stem)
        tempo_source = "filename_bpm"
    if tempo is None:
        # Third tier (2026-08-15): a deterministic estimate from the note
        # grid itself. Without it this file reached the variety roll and got
        # a NEW tempo on every generation — tempo is the base of every
        # density/arrangement decision and must not be a per-run dice throw.
        tempo = _estimate_bpm_from_notes(path)
        tempo_source = "note_onset_estimate"
    if tempo is not None:
        # Embedded MIDI tempo remains the authority over the filename, then
        # both sources remain authoritative over planner/variety BPM.
        pattern["bpm"] = tempo
        pattern["plg_midi_tempo_bpm"] = tempo
        pattern["plg_bpm_locked_from_midi"] = True
        pattern["plg_midi_tempo_source"] = tempo_source

    key_hit = read_key_signature(path)
    if key_hit is not None:
        root_pc, scale = key_hit
        pattern["plg_midi_key"] = {"root_pc": int(root_pc) % 12, "scale": scale, "source": "key_signature"}
        # Soft key stamp for humanize — do not override an explicit scale
        # lock the caller already set unless absent.
        if not pattern.get("plg_uploaded_scale_lock"):
            pattern.setdefault("plg_key_hint", {"root_pc": int(root_pc) % 12, "scale": scale})

    markers = read_midi_control_markers(path)
    pattern["plg_midi_markers"] = markers

    # System markers: Text 0x01 → Instrument 0x04 → PC/CC0 → Track 0x03.
    # Junk pack templates (a kit pack "Bell - Ding") are stripped inside the reader.
    hints = read_midi_instrument_hints(path)
    pattern["plg_midi_instrument_hints"] = hints
    lock_text = hints.get("lock_text")
    if lock_text and not pattern.get("plg_instrument_tag"):
        pattern["plg_instrument_tag"] = str(lock_text)

    # Build the existing read-only passport.
    # The role brain must be able to see reliable musical-role evidence (for
    # example ``sustained_melody``) before it turns a weak shape guess into a
    # system lock.  Pocket routing is still attached later, after structure.
    try:
        from midi_profiler import profile_midi_file

        pattern["plg_midi_passport"] = profile_midi_file(path)
    except Exception as exc:  # noqa: BLE001
        logger.info("MIDI passport profile skipped: %s", exc)

    if hints.get("system_lock"):
        pattern["plg_midi_system_lock"] = {
            "source": hints.get("lock_source"),
            "text": hints.get("lock_text"),
            "dna": (
                hints.get("dna_from_llm")
                or hints.get("dna_from_text")
                or hints.get("dna_from_name")
                or hints.get("dna_from_bank_map")
                or hints.get("dna_from_program")
                or hints.get("dna_from_track")
            ),
            "program": (hints.get("programs") or [None])[0],
            "bank_msb": hints.get("bank_msb"),
            "bank_lsb": hints.get("bank_lsb"),
            "track": hints.get("track_lock_text"),
            "text_event": hints.get("text_lock_text"),
            "instrument_name": (hints.get("instrument_names") or [None])[0],
        }
    dna_hint = (
        hints.get("dna_from_llm")
        or hints.get("dna_from_text")
        or hints.get("dna_from_name")
        or hints.get("dna_from_bank_map")
        or hints.get("dna_from_program")
        or hints.get("dna_from_track")
    )
    if dna_hint and not pattern.get("melody_dna"):
        pattern.setdefault("melody_dna", str(dna_hint))

    # Structural analysis is metadata-only.  It runs after notes and official
    # tempo/key metadata are present, but before any drum planner can consume
    # the pattern.  Failures never block ingest.
    try:
        from midi_structure_analysis import attach_midi_structure

        attach_midi_structure(pattern)
    except Exception as exc:  # noqa: BLE001
        logger.info("MIDI structure analysis attach skipped: %s", exc)

    # The unified passport is read-only.  Its pocket mode is merely a
    # conservative routing hint for the existing composer; an unsafe/unknown
    # passport explicitly falls back to the current general behaviour.
    try:
        from midi_pocket_mode import attach_midi_pocket_mode

        attach_midi_pocket_mode(pattern)
    except Exception as exc:  # noqa: BLE001
        logger.info("MIDI passport attach skipped: %s", exc)


def merge_notes_into_pattern(
    pattern: dict[str, Any],
    notes: list[dict[str, Any]],
    *,
    source: str,
    name: str,
    meta_key: str = "plg_midi_ingest",
    manual_prefix: str = "MIDI ingest",
) -> bool:
    """Write every parsed MIDI note into the synth melody lane.

    An imported MIDI file is the user's melodic performance, not an 808 stem.
    Low notes may be part of a chord or a left-hand melody and therefore stay
    on ``melody_lead``.  The active low-end generator is the sole owner of
    ``808_bass`` later in the pipeline.  Each note is tagged with its musical
    role (``midi_roles``: bass / chords / lead / counter); the tag is
    information only.
    """
    if not notes:
        return False

    melody, bass = split_by_register(notes)
    tracks = pattern.setdefault("tracks", {})
    if not isinstance(tracks, dict):
        return False

    melody_notes = sorted(melody + bass, key=lambda note: float(note.get("time_step", 0.0)))
    if melody_notes:
        tracks["melody_lead"] = melody_notes
        # Roles are only written on the notes (midi_roles); every note stays in
        # melody_lead, so nothing that reads the melody sees a difference.
        if midi_roles.enabled():
            pattern["plg_midi_roles"] = midi_roles.tag_roles(melody_notes)

    pattern[meta_key] = {
        "source": source,
        "name": name,
        "notes": len(notes),
        "melody_notes": len(melody_notes),
        "bass_notes": len(bass),
    }
    src_path = Path(source)
    if src_path.is_file() and src_path.suffix.lower() in MIDI_EXTS:
        try:
            _attach_midi_file_meta(pattern, src_path)
        except Exception as exc:  # noqa: BLE001 — meta is best-effort
            logger.warning("MIDI file meta attach failed %s: %s", src_path.name, exc)
    steps = list(pattern.get("manual_steps") or [])
    steps.insert(0, f"{manual_prefix}: played {name} ({len(notes)} notes).")
    pattern["manual_steps"] = steps[:16]
    logger.info("%s: %s (%s notes)", manual_prefix, name, len(notes))
    return True


def ingest_library_midi(
    pattern: dict[str, Any],
    *,
    library_root: Path | str,
    prompt: str = "",
    style: str = "",
    midi_root: Path | str | None = None,
) -> bool:
    """Play the best-matching library .mid as the melody (and 808 if low notes).

    ``midi_root`` — see find_library_midi: searched in addition to
    library_root, never instead of it."""
    from symbolic_composer import local_opium_melody_mode

    if local_opium_melody_mode(prompt, style):
        logger.info("MIDI ingest skipped — local Opium melody composer owns the lead")
        return False

    root = Path(library_root)
    if not root.is_dir():
        return False
    extra_root = Path(midi_root) if midi_root else None
    midi_path = pick_library_midi(root, prompt, style, extra_root=extra_root)
    if midi_path is None:
        return False
    stem = midi_path.stem.lower()
    if any(marker in stem for marker in ("808", "bass", "sub", "kick", "drum")):
        logger.info("MIDI ingest skipped — %s is a bass/drum stem, not a hook", midi_path.name)
        return False
    notes = read_midi_notes(midi_path)
    return merge_notes_into_pattern(
        pattern,
        notes,
        source=str(midi_path),
        name=midi_path.name,
    )


def ingest_uploaded_midi(
    pattern: dict[str, Any],
    midi_path: Path | str,
    *,
    respect_uploaded_scale: bool = False,
) -> dict[str, Any]:
    """Explicit user upload — highest priority over the corpus/library
    auto-match tiers (call this first; on success the other ingest tiers
    should be skipped entirely, since the user pointed at one specific file).

    Unlike ingest_library_midi/ingest_from_corpus, this never searches — it
    always uses exactly the file the caller passed. Returns a result dict
    (never raises) so the API layer can surface the rights warning and any
    truncation notice without this module reaching into UI/API code:
        {"ok": bool, "warning": str | None, "notes": int, "truncated": bool}

    respect_uploaded_scale=False (default): unchanged behavior for the
    section-transform scale (genre profile's melody_scale still wins — same
    as the corpus/library tiers always did). =True: pattern["scale"] is set
    to the scale detected from the uploaded melody itself, so
    arrange_song()'s retrograde/sequence/inversion transforms bend it toward
    the user's own material instead of the genre's default. Note this only
    affects the MELODY's section transforms — the 808/chord progression's
    own harmonic engine (style_corpus.attach_progression, pinned later in
    the pipeline) still walks the genre profile's scale either way; the two
    are intentionally decoupled, not a full-song key override."""
    result: dict[str, Any] = {"ok": False, "warning": None, "notes": 0, "truncated": False}
    path = Path(midi_path)

    if path.suffix.lower() not in MIDI_EXTS:
        result["warning"] = "Not a .mid/.midi file."
        return result
    try:
        size = path.stat().st_size
    except OSError:
        result["warning"] = "MIDI file not found or unreadable."
        return result
    if size > UPLOAD_MAX_FILE_BYTES:
        result["warning"] = (
            f"MIDI file too large ({size // 1024} KB, max {UPLOAD_MAX_FILE_BYTES // 1024} KB)."
        )
        return result

    notes = read_midi_notes(path, max_beats=UPLOAD_MAX_BEATS)
    if not notes:
        result["warning"] = "No readable notes found in this MIDI file."
        return result
    truncated = len(notes) > UPLOAD_MAX_NOTES
    if truncated:
        notes = notes[:UPLOAD_MAX_NOTES]

    merged = merge_notes_into_pattern(
        pattern,
        notes,
        source=str(path),
        name=path.name,
        manual_prefix="Uploaded MIDI",
    )
    if not merged:
        result["warning"] = "Uploaded MIDI produced no usable melody/bass notes."
        return result

    pattern["plg_midi_melody"] = True
    pattern["plg_midi_uploaded"] = True
    # Product decision (2026-07): an explicit upload is finished material the
    # beatmaker brought in on purpose — the engine's job is drums answering
    # it (density-driven rolls, kick/808 duck), never rewriting it. Always on,
    # no opt-out: unlike plg_midi_melody (shared with the corpus/library
    # AUTO-match tiers, which keep their own variation), this flag is only
    # ever set here and in reference_reverse.ingest_reference_from_file — the
    # two genuinely explicit, user-pointed-at-one-file paths. See
    # arranger.py's melody_root_pc gate, the only consumer.
    pattern["plg_preserve_melody_sections"] = True

    if respect_uploaded_scale:
        from music_theory import detect_root_pc, detect_scale

        # Prefer official MIDI key_signature meta; fall back to note histogram.
        meta_key = pattern.get("plg_midi_key") if isinstance(pattern.get("plg_midi_key"), dict) else None
        if meta_key and meta_key.get("scale") is not None and meta_key.get("root_pc") is not None:
            root_pc = int(meta_key["root_pc"]) % 12
            scale = str(meta_key["scale"])
        else:
            melody = track_notes(pattern, "melody_lead")
            if len(melody) >= 3:
                root_pc = detect_root_pc(melody)
                scale = detect_scale(melody, root_pc)
            else:
                root_pc, scale = None, None
        if scale is not None and root_pc is not None:
            pattern["scale"] = scale
            pattern["plg_uploaded_scale_lock"] = {"root_pc": root_pc, "scale": scale}

    result.update(ok=True, notes=len(notes), truncated=truncated, warning=UPLOAD_RIGHTS_WARNING)
    logger.info(
        "Uploaded MIDI ingested: %s (%d notes%s, respect_scale=%s)",
        path.name,
        len(notes),
        ", truncated" if truncated else "",
        respect_uploaded_scale,
    )
    return result


def _find_midi_folder_candidates(midi_dir: Path) -> list[Path]:
    if not midi_dir.is_dir():
        return []
    found: set[Path] = set()
    for ext in MIDI_EXTS:
        found.update(p for p in midi_dir.rglob(f"*{ext}") if p.is_file() and not is_service_midi(p))
    # Copies of one file (a folder dropped twice) are one candidate, not several chances in the random pick.
    return distinct_midi_files(found)


def _rng_for_folder_pick(pattern: dict[str, Any]) -> random.Random:
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    seed_src = f"midi-folder|{variety.get('seed', '')}|{pattern.get('user_prompt', '')}"
    return random.Random(seed_src)


def _pick_best_midi_in_folder(
    candidates: list[Path],
    *,
    prompt: str = "",
    style: str = "",
    rng: random.Random | None = None,
) -> Path:
    """Best contextual match among 2+ files the user deliberately kept in the
    curated midi/ folder — audit 2026-07: unlike pick_library_midi's
    whole-library search (which has to reject weak matches or it would drag
    in every incidental MIDI stub from a random drum kit), everything here
    was put in this folder on purpose, so a weak/zero keyword match still
    deserves a real pick, never a silent reject back to the LLM's melody."""
    from sound_descriptors import descriptor_hints

    text = f"{prompt} {style}".lower()
    tokens = set(re.findall(r"[a-z0-9]{3,}", text))
    tokens |= set(descriptor_hints(prompt, style, track="melody_lead"))
    scored = [(_score_name(p, tokens), p) for p in candidates]
    best_score = max(score for score, _ in scored)
    top = sorted(p for score, p in scored if score == best_score)
    if rng is not None and len(top) > 1:
        return rng.choice(top)
    return top[0]


def resolve_midi_folder(
    pattern: dict[str, Any],
    library_root: Path | str,
    *,
    midi_root: Path | str | None = None,
    prompt: str = "",
    style: str = "",
    respect_uploaded_scale: bool = False,
) -> dict[str, Any] | None:
    """The midi/ folder IS the control surface (2026-07 audit/product
    decision) — no separate explicit/auto UI toggle needed:
      - exactly one file  -> used directly, UNCONDITIONALLY. Leaving only one
        file in the folder IS the explicit choice; no keyword-match score,
        no reject gate (this is the fix for midi_corpus.py's 30%-skip-on-
        weak-match gate and pick_library_midi's score>0-or-explicit-trigger
        gate — both used to let even a lone file get silently dropped).
      - two or more files -> best contextual match by genre/prompt
        (_pick_best_midi_in_folder), ALWAYS returns something — never a
        probabilistic or score-threshold reject.
      - folder missing/empty -> None; caller falls through to the existing
        reference-audio / corpus / library-match chain unchanged.

    ``midi_root`` — the dedicated PLG_MIDI folder (2026-07: split out as its
    own top-level sibling of the sample library, see
    library_paths.DEFAULT_MIDI_DIR). Candidates from library_root/midi/ (the
    legacy nested layout) and midi_root are MERGED into one pool — a library
    that hasn't moved anything out yet keeps working exactly as before;
    ``midi_root`` only adds to it, never replaces it. Defaults to
    library_root/midi/ alone when not given (unchanged old behavior).

    Reuses ingest_uploaded_midi for the actual parse/merge — same length/size
    caps, same plg_midi_melody protection, same respect_uploaded_scale
    contract. This function only decides WHICH file, not how it's ingested."""
    legacy_dir = Path(library_root) / MIDI_FOLDER
    dirs = [legacy_dir]
    if midi_root is not None:
        resolved_midi_root = Path(midi_root)
        if resolved_midi_root != legacy_dir:
            dirs.append(resolved_midi_root)

    seen: set[Path] = set()
    candidates: list[Path] = []
    for midi_dir in dirs:
        for path in _find_midi_folder_candidates(midi_dir):
            if path not in seen:
                seen.add(path)
                candidates.append(path)
    candidates.sort()
    if not candidates:
        return None

    if len(candidates) == 1:
        chosen = candidates[0]
    else:
        # A file named like a drum part ("Hats.mid") is not a melody; pick
        # among the rest. If everything is named that way, nothing is excluded.
        pool = [p for p in candidates if not _is_drum_name(p.stem)] or candidates
        rng = _rng_for_folder_pick(pattern)
        chosen = _pick_best_midi_in_folder(pool, prompt=prompt, style=style, rng=rng)

    # A pick that has no melodic notes (drum-only file) must not end the run
    # while other files wait in the folder: try the rest, the pick goes first.
    result: dict[str, Any] | None = None
    for path in [chosen] + [p for p in candidates if p != chosen]:
        result = ingest_uploaded_midi(pattern, path, respect_uploaded_scale=respect_uploaded_scale)
        if result.get("ok"):
            break
    return result


def has_available_material(
    library_root: Path | str,
    *,
    midi_root: Path | str | None = None,
    midi_path: Path | str | None = None,
    reference_audio_path: Path | str | None = None,
) -> bool:
    """Product decision (2026-07): MIDI is now mandatory for generation —
    the LLM no longer writes melody/chords/counter_melody/texture from
    scratch, so a beat needs SOME user-brought melodic material (an explicit
    .mid/audio upload, or a file the user curated into the PLG_MIDI folder)
    before the pipeline is worth running at all. This is a cheap presence
    check ONLY (extension + exists, or folder non-empty) — no parsing, so
    callers can gate BEFORE spending quota or an LLM call; the real
    ingest_uploaded_midi/resolve_midi_folder still does the actual validation
    (size caps, readable notes, etc.) further down the pipeline.

    Explicit reference-audio upload counts too — it's the same "user pointed
    at one specific file" tier as MIDI upload (see _humanize_and_ingest's
    midi_path/reference_audio_path either-or), just an audio source instead
    of a symbolic one."""
    if midi_path:
        path = Path(midi_path)
        if path.suffix.lower() in MIDI_EXTS and path.is_file():
            return True

    if reference_audio_path:
        from library_paths import AUDIO_EXTENSIONS

        path = Path(reference_audio_path)
        if path.suffix.lower() in AUDIO_EXTENSIONS and path.is_file():
            return True

    legacy_dir = Path(library_root) / MIDI_FOLDER
    dirs = [legacy_dir]
    if midi_root is not None:
        resolved_midi_root = Path(midi_root)
        if resolved_midi_root != legacy_dir:
            dirs.append(resolved_midi_root)

    return any(_find_midi_folder_candidates(midi_dir) for midi_dir in dirs)


def has_available_midi_material(
    library_root: Path | str,
    *,
    midi_root: Path | str | None = None,
    midi_path: Path | str | None = None,
) -> bool:
    """Return whether this run has a symbolic MIDI source.

    This deliberately excludes reference audio.  The main pipeline uses it to
    choose the MIDI-first route: MIDI owns the musical lanes, while an LLM may
    still assign their own instrument to the melody lane in FL Studio.
    """
    if midi_path:
        path = Path(midi_path)
        return path.suffix.lower() in MIDI_EXTS and path.is_file()

    legacy_dir = Path(library_root) / MIDI_FOLDER
    dirs = [legacy_dir]
    if midi_root is not None:
        resolved_midi_root = Path(midi_root)
        if resolved_midi_root != legacy_dir:
            dirs.append(resolved_midi_root)
    return any(_find_midi_folder_candidates(midi_dir) for midi_dir in dirs)

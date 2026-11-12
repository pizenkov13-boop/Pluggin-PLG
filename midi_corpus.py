"""MIDI corpus — the user's .mid collection becomes a living style library.

The library often ships REAL musical DNA: melody packs with key/BPM in the
filename and song-deconstruction kits with per-lane MIDIs (``… 808 [MIDI]``,
``… Hat [MIDI]``, ``… Snare [MIDI]``). The old ingest used almost none of it
(name-match only, melody only, copied 1:1). This module makes the collection a
first-class source with mandatory variation:

- **melody**: a seeded pick per generation (different beat → different file →
  «каждый бит не похож»), then *varied*: transposed into the session key,
  scale-snapped, strong beats gently voice-led onto the pinned progression's
  chord of that bar, plus 2 seeded mutations (note thinning / octave pops /
  rhythm nudge / velocity wave). Never a 1:1 copy.
- **808**: rhythm + sustain shapes from real 808 MIDIs, re-rooted so the line
  walks the session progression (the humanizer's walk keeps it in harmony).
- **hi-hats**: real hat grooves (grid + velocities) instead of synthetic grids.

Everything is deterministic per ``plg_variety`` seed and can be killed with
``PLG_MIDI_CORPUS=off``. When nothing matches, the legacy behaviour remains.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import re
from pathlib import Path
from typing import Any

from library_paths import is_service_midi
from music_theory import SCALES, detect_root_pc, name_for_midi, snap_pc_to_scale
from pattern_utils import NOTE_INDEX, parse_note_name, track_notes

logger = logging.getLogger("plg.midi_corpus")

BEATS_PER_BAR = 4.0
MELODY_LO, MELODY_HI = 60, 83  # beat_humanize.MELODY_REGISTER
_MAX_MELODY_BEATS = 32.0
_MAX_LANE_BEATS = 16.0

_LANE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("808", ("808", "sub bass", "subbass", "bassline", " bass", "_bass")),
    ("hats", ("hat", "hi-hat", "hihat", "hh ", "shaker")),
    # One-shot MIDI stubs from drum kits (a single snap/chant/scream note) used
    # to fall through to the "melody" lane and dilute the real melody packs —
    # picks landed on a 1-note "XPOSED Snap [MIDI]" and the melody never fired.
    ("drums", ("snare", "clap", "kick", "perc", "drum", "rim", "crash", "cymbal", "tom",
               "snap", "chant", "scream", "vox", "vocal", "voice", "tag", "fx", "riser",
               "sweep", "impact", "stab", "open", "closed", "oneshot", "one shot")),
)

_KEY_RE = re.compile(
    r"\b([a-g])\s*(#|sharp|b|flat)?\s*(min|minor|maj|major|m\b)", re.IGNORECASE
)
_BPM_RE = re.compile(r"(?<![a-z0-9])(\d{2,3})\s*(?:bpm|b\.?p\.?m\.?)\b", re.IGNORECASE)
# Bare tempo tags like "_140_" / "- 145 -" when "BPM" is omitted (common in packs).
_BPM_BARE_RE = re.compile(r"(?<![a-z0-9])(6\d|7\d|8\d|9\d|1[0-9]\d|200)(?![a-z0-9])")


def _env_flag(name: str, default: str = "auto") -> str:
    return os.environ.get(name, default).strip().lower()


def midi_corpus_enabled() -> bool:
    return _env_flag("PLG_MIDI_CORPUS", "auto") not in ("0", "false", "no", "off")


def _detect_lane(name: str) -> str:
    low = f" {name.lower()} "
    for lane, markers in _LANE_MARKERS:
        if any(marker in low for marker in markers):
            return lane
    return "melody"


def key_from_name(name: str) -> tuple[int, str] | None:
    """Parse 'Cmin' / 'F# minor' / 'Abmaj' from a filename → (root_pc, scale)."""
    # underscores/dashes are word chars — they'd hide the \b before the letter
    match = _KEY_RE.search(re.sub(r"[_\-.]+", " ", name))
    if not match:
        return None
    letter = match.group(1).upper()
    accidental = (match.group(2) or "").lower()
    if accidental in ("#", "sharp"):
        letter += "#"
    elif accidental in ("b", "flat"):
        letter += "b"
    pc = NOTE_INDEX.get(letter)
    if pc is None:
        return None
    quality = match.group(3).lower()
    scale = "major" if quality.startswith("maj") else "natural_minor"
    return int(pc) % 12, scale


def bpm_from_name(name: str) -> float | None:
    """Parse tempo from a MIDI/sample filename ('130 BPM', '140bpm', '_145_').

    Prefers an explicit ``BPM`` token; falls back to a bare 60–200 number only
    when a single producer-tempo candidate is present. Used by the FLEX melody
    mapper so ``ExamplePack_-_Anguish_-_130_BPM_G_Min.mid`` drives attack/release
    scoring from the filename itself.
    """
    cleaned = re.sub(r"[_\-.]+", " ", name)
    match = _BPM_RE.search(cleaned)
    if match:
        bpm = float(match.group(1))
        return bpm if 60.0 <= bpm <= 220.0 else None
    candidates = [float(m.group(1)) for m in _BPM_BARE_RE.finditer(cleaned)]
    band = [b for b in candidates if 90.0 <= b <= 180.0]
    if len(band) == 1:
        return band[0]
    return None


def scan_midi_corpus(
    library_root: Path | str, *, extra_root: Path | str | None = None
) -> list[dict[str, Any]]:
    """Catalog every .mid under the library: lane, key hint, path. Cheap (names
    only) — note parsing happens lazily for the picked files.

    ``extra_root`` — the dedicated PLG_MIDI folder (2026-07: split out as its
    own top-level sibling of the sample library, see
    library_paths.DEFAULT_MIDI_DIR), scanned IN ADDITION TO library_root —
    merge, not replace, so a library that hasn't migrated its MIDI out yet
    keeps working exactly as before."""
    roots = [Path(library_root)]
    if extra_root is not None:
        extra = Path(extra_root)
        if extra not in roots:
            roots.append(extra)

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()  # duplicate kit folders = same .mid twice
    for root in roots:
        if not root.is_dir():
            continue
        for ext in (".mid", ".midi"):
            for path in sorted(root.rglob(f"*{ext}")):
                if not path.is_file() or is_service_midi(path):
                    continue
                try:
                    dedupe_key = (path.name.lower(), path.stat().st_size)
                except OSError:
                    dedupe_key = (path.name.lower(), -1)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                stem = path.stem
                key_hint = key_from_name(stem) or key_from_name(path.parent.name)
                entries.append(
                    {
                        "path": str(path),
                        "name": path.name,
                        "stem": stem.lower(),
                        "lane": _detect_lane(stem),
                        "key_pc": key_hint[0] if key_hint else None,
                        "key_scale": key_hint[1] if key_hint else None,
                    }
                )
    return entries


def _rng_for(pattern: dict[str, Any], salt: str) -> random.Random:
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    mix = f"midi-corpus:{salt}:{variety.get('seed', 0)}:{pattern.get('user_prompt', '')}"
    return random.Random(int(hashlib.sha256(mix.encode("utf-8")).hexdigest()[:12], 16))


def _score_entry(entry: dict[str, Any], tokens: set[str]) -> int:
    stem = entry["stem"]
    return sum(1 for token in tokens if token and token in stem)


def pick_corpus_midi(
    entries: list[dict[str, Any]],
    lane: str,
    *,
    prompt: str = "",
    style: str = "",
    rng: random.Random,
) -> dict[str, Any] | None:
    """Seeded pick from the top-scoring band of a lane — like sample_match: the
    best matches compete, the seed decides, so every beat pulls a different file."""
    pool = [e for e in entries if e["lane"] == lane]
    if not pool:
        return None
    text = f"{prompt} {style}".lower()
    tokens = set(re.findall(r"[a-zа-я0-9]{3,}", text))
    scored = sorted(
        ((e, _score_entry(e, tokens)) for e in pool), key=lambda p: p[1], reverse=True
    )
    top = scored[0][1]
    if top <= 0:
        return rng.choice(pool)
    # Relevance band, widened to ≥6 candidates so a single lucky token match
    # can't pin every generation to the same file (variety > exact match).
    band = [e for e, s in scored if s >= top]
    tier = top - 1
    while len(band) < 6 and tier > 0:
        band = [e for e, s in scored if s >= tier]
        tier -= 1
    if len(band) < 6:
        band = [e for e, _ in scored[: max(6, len(scored) // 4)]]
    return rng.choice(band)


# ---------------------------------------------------------------------------
# Variation engine — «немного менять»: never a 1:1 copy of the user's MIDI.
# ---------------------------------------------------------------------------


def _clamp_register(midi: int, lo: int = MELODY_LO, hi: int = MELODY_HI) -> int:
    while midi < lo:
        midi += 12
    while midi > hi:
        midi -= 12
    return midi


def vary_melody_notes(
    notes: list[dict[str, Any]],
    rng: random.Random,
    *,
    root_pc: int,
    scale: str,
    roots_pc: list[int] | None = None,
    chord_tones_pc: list[list[int]] | None = None,
    source_root_pc: int | None = None,
) -> list[dict[str, Any]]:
    """Transpose to the session key, lead strong beats onto the changes, mutate."""
    if not notes:
        return []
    src_root = source_root_pc if source_root_pc is not None else detect_root_pc(notes)
    delta = (int(root_pc) - int(src_root)) % 12
    if delta > 6:
        delta -= 12
    scale_name = scale if scale in SCALES else "natural_minor"

    out: list[dict[str, Any]] = []
    for entry in notes:
        try:
            midi = parse_note_name(str(entry.get("note", "")))
        except (ValueError, TypeError):
            continue
        midi = _clamp_register(midi + delta)
        midi = snap_pc_to_scale(midi, int(root_pc), scale_name)
        note = dict(entry)
        # gentle voice-leading: strong beats pulled ≤2 semitones onto the bar's chord
        step = float(note.get("time_step", 0.0))
        if chord_tones_pc and step % 1.0 < 1e-6:
            bar = int(step // BEATS_PER_BAR)
            tones = {int(pc) % 12 for pc in chord_tones_pc[bar % len(chord_tones_pc)]}
            if midi % 12 not in tones:
                for shift in (1, -1, 2, -2):
                    if (midi + shift) % 12 in tones:
                        midi = _clamp_register(midi + shift)
                        break
        note["note"] = name_for_midi(midi)
        note["midi"] = True
        note["midi_varied"] = True
        out.append(note)

    if not out:
        return []

    mutations = rng.sample(("thin", "octave_pop", "nudge", "wave"), k=2)
    if "thin" in mutations and len(out) > 8:
        keep = max(int(len(out) * 0.88), 8)
        quietest = sorted(out, key=lambda n: int(n.get("velocity", 100)))[: len(out) - keep]
        drop_ids = {id(n) for n in quietest}
        out = [n for n in out if id(n) not in drop_ids]
    if "octave_pop" in mutations:
        for note in out:
            step = float(note.get("time_step", 0.0))
            if int(step // BEATS_PER_BAR) % 4 == 3 and step % BEATS_PER_BAR >= 3.0:
                midi = parse_note_name(str(note["note"]))
                if midi + 12 <= MELODY_HI:
                    note["note"] = name_for_midi(midi + 12)
    if "nudge" in mutations:
        for note in out:
            step = float(note.get("time_step", 0.0))
            if step % 2.0 == 1.5 and rng.random() < 0.4:
                note["time_step"] = round(step + 0.25, 5)
    if "wave" in mutations:
        for index, note in enumerate(out):
            vel = int(note.get("velocity", 96))
            swing = 10 if index % 4 in (0, 3) else -6
            note["velocity"] = max(40, min(120, vel + swing))

    return sorted(out, key=lambda n: float(n.get("time_step", 0.0)))


def _read_lane_notes(path: str, *, max_beats: float) -> list[dict[str, Any]]:
    from midi_ingest import read_midi_notes

    return read_midi_notes(Path(path), max_beats=max_beats)


# ---------------------------------------------------------------------------
# Ingest — melody + 808 + hats per generation, all seeded, all optional.
# ---------------------------------------------------------------------------

_808_GENRES = frozenset({
    "trap", "rage", "opium", "phonk", "drill", "pop_rap", "boombap", "lofi",
    "reggaeton", "dancehall", "jersey", "dark_pop",
})
_HAT_GENRES = frozenset({"trap", "rage", "opium", "phonk", "drill", "pop_rap", "jersey"})


def _dna_for_ingest(pattern: dict[str, Any]) -> dict[str, Any] | None:
    """The same style DNA the prompt announced (ctx key/scale/seed) — computed
    here because ingest runs before the progression is pinned on the pattern."""
    try:
        from style_corpus import corpus_enabled, style_dna, _genre_for_pattern

        if not corpus_enabled():
            return None
        variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
        return style_dna(
            _genre_for_pattern(pattern),
            key=str(variety.get("key") or pattern.get("key") or "A"),
            scale=str(variety.get("scale") or pattern.get("scale") or "natural_minor"),
            seed=variety.get("seed") if isinstance(variety.get("seed"), int) else None,
        )
    except Exception:  # noqa: BLE001
        return None


def ingest_from_corpus(
    pattern: dict[str, Any],
    *,
    library_root: Path | str,
    prompt: str = "",
    style: str = "",
    midi_root: Path | str | None = None,
) -> bool:
    """Pull melody / 808 / hats from the user's MIDI collection (seeded, varied).

    ``midi_root`` — see scan_midi_corpus: scanned in addition to
    library_root, never instead of it."""
    if not midi_corpus_enabled():
        return False
    entries = scan_midi_corpus(library_root, extra_root=midi_root)
    if not entries:
        return False

    dna = _dna_for_ingest(pattern)
    genre = str((dna or {}).get("genre") or "trap")
    prog = (dna or {}).get("progression") or {}
    root_pc = int((dna or {}).get("root_pc", 9))
    scale = str((dna or {}).get("scale") or "natural_minor")

    tracks = pattern.setdefault("tracks", {})
    if not isinstance(tracks, dict):
        return False

    used: dict[str, str] = {}

    # --- melody: strong name-match → always; otherwise a seeded 70% (the user's
    # melody packs are real composed hooks — prefer them over synthetic ones).
    # A pick that turns out to be a 1-2 note stub does NOT kill the melody: the
    # next band candidate gets a shot.
    rng = _rng_for(pattern, "melody")
    text = f"{prompt} {style}".lower()
    tokens = set(re.findall(r"[a-zа-я0-9]{3,}", text))
    melody_pool = list(entries)
    for _attempt in range(4):
        melody_entry = pick_corpus_midi(melody_pool, "melody", prompt=prompt, style=style, rng=rng)
        if melody_entry is None:
            break
        matched = _score_entry(melody_entry, tokens) > 0
        if not (matched or rng.random() < 0.7):
            break
        raw = _read_lane_notes(melody_entry["path"], max_beats=_MAX_MELODY_BEATS)
        distinct = {str(n.get("note")) for n in raw} if raw else set()
        if len(raw) < 6 or len(distinct) < 4:
            # 1-note kit stub / chirp — drop it from the pool and try the next.
            melody_pool = [e for e in melody_pool if e is not melody_entry]
            continue
        varied = vary_melody_notes(
            raw,
            rng,
            root_pc=root_pc,
            scale=scale,
            roots_pc=list(prog.get("roots_pc") or []) or None,
            chord_tones_pc=list(prog.get("chord_tones_pc") or []) or None,
            source_root_pc=melody_entry.get("key_pc"),
        )
        if len(varied) >= 6:
            tracks["melody_lead"] = varied
            pattern["plg_midi_melody"] = True
            used["melody"] = melody_entry["name"]
        break

    # --- 808: real sustain/glide rhythm from the kits; the humanizer's walk
    # re-roots it onto the progression.
    if genre in _808_GENRES:
        rng = _rng_for(pattern, "808")
        entry = pick_corpus_midi(entries, "808", prompt=prompt, style=style, rng=rng)
        if entry is not None and rng.random() < 0.5:
            raw = _read_lane_notes(entry["path"], max_beats=_MAX_LANE_BEATS)
            if len(raw) >= 3:
                for note in raw:
                    note["sub_808_gen"] = True  # keep: real line, do not regenerate
                    note["midi"] = True
                tracks["sub_808"] = raw
                pattern["plg_midi_808"] = True
                used["sub_808"] = entry["name"]

    # --- hats: real groove grid + velocities instead of a synthetic grid.
    if genre in _HAT_GENRES:
        rng = _rng_for(pattern, "hats")
        entry = pick_corpus_midi(entries, "hats", prompt=prompt, style=style, rng=rng)
        if entry is not None and rng.random() < 0.45:
            raw = _read_lane_notes(entry["path"], max_beats=_MAX_LANE_BEATS)
            if len(raw) >= 8:
                for note in raw:
                    note["note"] = "F#5"  # one-shot lane — grid + velocity carry the groove
                    note["midi"] = True
                tracks["hi_hats"] = raw
                pattern["plg_midi_hats"] = True
                used["hi_hats"] = entry["name"]

    if not used:
        return False

    pattern["plg_midi_corpus"] = used
    steps = list(pattern.get("manual_steps") or [])
    steps.insert(0, "MIDI corpus: " + ", ".join(f"{k}={v}" for k, v in used.items()))
    pattern["manual_steps"] = steps[:16]
    logger.info("MIDI corpus ingest: %s", used)
    return True

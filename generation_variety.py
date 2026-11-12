"""Per-generation variety — no two beats should sound like clones."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any

from plg_paths import app_dir, user_data_dir

logger = logging.getLogger("plg.variety")

_KEYS = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_PATTERN_FILE = app_dir() / "output_pattern.json"
_HISTORY_FILE = user_data_dir() / "beat_history.jsonl"
_MAX_HISTORY = 32
_SIMILARITY_THRESHOLD = 0.48
_active_context: dict[str, Any] | None = None

_GRID_TRACKS = ("kick", "snare", "clap", "hi_hats", "melody_lead", "sub_808")


def set_active_context(ctx: dict[str, Any] | None) -> None:
    global _active_context
    _active_context = ctx


def active_context() -> dict[str, Any] | None:
    return _active_context


def pinned_seed() -> int | None:
    """The explicit reproducibility override, or None for normal operation.

    ``PLG_VARIETY_SEED=<int>`` turns this module's whole anti-clone feature
    OFF and replaces it with one fixed seed, so the same MIDI produces the
    same beat every time. Unset (the default) changes nothing at all.

    It has to switch off all four mechanisms together, because each one alone
    is enough to make a run unrepeatable — measured 2026-08-19 on Sahara:

    1. ``new_variety_seed`` hashes ``time.time_ns()``, so every Generate press
       reseeds every humanize pass. kick/808/hats/snare all move.
    2. (Removed 2026-08-28.) A melody-preset rotation used to penalise the last
       three shipped presets, which also perturbed the drum seed.
    3. ``recent_forbidden`` excludes recently used keys, so ``build_context``
       re-rolls ``key``/``scale`` every run (Sahara drew F#/C/D#/G/E and
       minor/harmonic_minor/phrygian on a file that is in A minor).
    4. ``is_too_similar`` REJECTS a beat that resembles a recent one. With
       only the seed pinned, generation does not repeat — it fails outright
       after every retry ("anti-clone guard, 65% similar").

    Pinning also stops this module WRITING history (``append_beat_history``),
    so an evaluation run leaves no trace that would shift the next real one.

    This is an evaluation/debugging control, not a product mode: without a
    stable result there is no way to tell a real improvement from the seed
    noise, which is what made a listening comparison impossible.

    ⚠ Set it in ``.env.local`` and the TEST SUITE inherits it too — the
    anti-clone tests then fail by design (measured: 8 of them, across
    test_anti_clone / test_generation_variety /
    test_offline_cloud). Clear the variable before running the suite.
    Note also that ``backend_core.load_environment`` reads ``.env`` ONLY,
    unlike ``app_config``'s, so a seed placed in ``.env.local`` reaches
    ``plg_api.create_beat`` but NOT a direct ``run_pipeline`` call.
    """
    raw = os.environ.get("PLG_VARIETY_SEED", "").strip()
    if not raw:
        return None
    try:
        return int(raw, 0) & 0xFFFFFFFF
    except (TypeError, ValueError):
        logger.warning("PLG_VARIETY_SEED=%r is not an integer; ignoring", raw)
        return None


def active_seed() -> int | None:
    ctx = _active_context
    if not ctx:
        return None
    seed = ctx.get("seed")
    return int(seed) if isinstance(seed, int) else None


def variety_rng(seed: int | None, prompt: str) -> random.Random:
    if seed is not None:
        return random.Random(seed)
    mix = f"{time.time_ns()}:{prompt}".encode("utf-8", errors="ignore")
    return random.Random(int.from_bytes(hashlib.sha256(mix).digest()[:4], "big"))


def new_variety_seed(prompt: str, *, regenerate: bool = False, bump: int = 0) -> int:
    pinned = pinned_seed()
    if pinned is not None:
        return pinned
    mix = f"{time.time_ns()}:{prompt}:{'regen' if regenerate else 'new'}:{bump}".encode(
        "utf-8", errors="ignore"
    )
    return int.from_bytes(hashlib.sha256(mix).digest()[:4], "big")


def load_last_pattern() -> dict[str, Any] | None:
    if not _PATTERN_FILE.is_file():
        return None
    try:
        data = json.loads(_PATTERN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def load_beat_history(limit: int = _MAX_HISTORY) -> list[dict[str, Any]]:
    if not _HISTORY_FILE.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in _HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows[-limit:]


def melody_pick_record(pattern: dict[str, Any]) -> dict[str, Any] | None:
    """No melody instrument is journalled any more.

    PLG used to pick a FLEX preset and record it here so the next beat could
    avoid repeating it. The preset selection was retired 2026-08-28 -- PLG ships
    the melody MIDI and a basic preview, and the producer chooses the synth --
    so there is nothing instrument-shaped left to remember.
    """
    del pattern
    return None


def append_beat_history(pattern: dict[str, Any]) -> None:
    """Record variety history when the user-data folder is writable.

    This is only an anti-clone memory. A locked Windows profile must never
    prevent a fully valid MIDI/FLP session from being exported.
    """
    if pinned_seed() is not None:
        return         # see pinned_seed: an evaluation run leaves no trace
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        row = rich_fingerprint(pattern)
        row["ts"] = time.time()
        row["beat_id"] = str(pattern.get("plg_beat_id") or "")
        # Variant 5 (2026-08-14): journal the shipped melody preset so the
        # next generation's FLEX ranking can soften repeats. Additive field —
        # every existing reader uses .get() and ignores it.
        melody = melody_pick_record(pattern)
        if melody:
            row["melody"] = melody
        with _HISTORY_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        lines = _HISTORY_FILE.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_HISTORY:
            _HISTORY_FILE.write_text("\n".join(lines[-_MAX_HISTORY:]) + "\n", encoding="utf-8")
    except OSError as exc:
        logging.getLogger("plg.generation_variety").warning(
            "Beat history unavailable; continuing without anti-clone memory (%s)", exc
        )


def pattern_fingerprint(pattern: dict[str, Any]) -> dict[str, Any]:
    picks = pattern.get("plg_sample_picks") if isinstance(pattern.get("plg_sample_picks"), dict) else {}
    tracks = pattern.get("tracks") if isinstance(pattern.get("tracks"), dict) else {}
    note_counts = {k: len(v) for k, v in tracks.items() if isinstance(v, list)}
    return {
        "bpm": pattern.get("bpm"),
        "key": pattern.get("key"),
        "style": pattern.get("style"),
        "samples": sorted(Path(str(v)).name for v in picks.values() if v),
    }


def grid_signature(pattern: dict[str, Any]) -> str:
    tracks = pattern.get("tracks") if isinstance(pattern.get("tracks"), dict) else {}
    parts: list[str] = []
    for track in _GRID_TRACKS:
        notes = tracks.get(track) or []
        if not isinstance(notes, list):
            continue
        for entry in sorted(notes, key=lambda n: (float(n.get("time_step", 0)), str(n.get("note", ""))))[:96]:
            step = round(float(entry.get("time_step", 0)) * 4) / 4
            parts.append(f"{track}:{step}:{entry.get('note')}")
    digest = hashlib.sha256("|".join(parts).encode("utf-8", errors="ignore")).hexdigest()
    return digest[:20]


def rich_fingerprint(pattern: dict[str, Any]) -> dict[str, Any]:
    base = pattern_fingerprint(pattern)
    base["grid"] = grid_signature(pattern)
    return base


def _similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    score = 0.0
    if a.get("grid") and a.get("grid") == b.get("grid"):
        score += 0.5
    samples_a = set(a.get("samples") or [])
    samples_b = set(b.get("samples") or [])
    if samples_a and samples_b:
        union = samples_a | samples_b
        overlap = len(samples_a & samples_b) / max(1, len(union))
        score += overlap * 0.35
    if a.get("bpm") is not None and a.get("bpm") == b.get("bpm"):
        score += 0.08
    if a.get("key") and a.get("key") == b.get("key"):
        score += 0.07
    return min(1.0, score)


def max_similarity(pattern: dict[str, Any]) -> float:
    fp = rich_fingerprint(pattern)
    best = 0.0
    for hist in load_beat_history():
        best = max(best, _similarity(fp, hist))
    return best


def is_too_similar(pattern: dict[str, Any], *, threshold: float | None = None) -> tuple[bool, float]:
    if pinned_seed() is not None:
        # see pinned_seed: with a fixed seed the beat SHOULD repeat, and
        # this guard would reject every retry and fail the generation.
        return False, 0.0
    limit = _SIMILARITY_THRESHOLD if threshold is None else threshold
    sim = max_similarity(pattern)
    return sim >= limit, sim


def recent_forbidden() -> dict[str, Any]:
    if pinned_seed() is not None:
        return {}     # see pinned_seed: key/bpm must not re-roll
    history = load_beat_history(12)
    keys: list[str] = []
    bpms: list[int] = []
    samples: set[str] = set()
    grids: set[str] = set()
    for row in history:
        key = row.get("key")
        if isinstance(key, str) and key:
            keys.append(key)
        try:
            if row.get("bpm") is not None:
                bpms.append(int(row["bpm"]))
        except (TypeError, ValueError):
            pass
        for name in row.get("samples") or []:
            if name:
                samples.add(str(name))
        grid = row.get("grid")
        if isinstance(grid, str) and grid:
            grids.add(grid)
    return {
        "keys": keys,
        "bpms": bpms,
        "samples": sorted(samples),
        "grids": grids,
    }


def disliked_sample_names() -> set[str]:
    try:
        from beat_reward import load_ratings
    except ImportError:
        return set()
    names: set[str] = set()
    for row in load_ratings():
        if int(row.get("rating") or 0) >= 0:
            continue
        picks = row.get("sample_picks")
        if isinstance(picks, dict):
            names.update(Path(str(v)).name for v in picks.values() if v)
    return names


_BPM_RE = re.compile(r"(?:\bbpm\s*[:=]?\s*(\d{2,3})\b)|(?:\b(\d{2,3})\s*(?:bpm|бпм)\b)", re.IGNORECASE)
_KEY_RE = re.compile(
    r"\b([a-g][#b♯♭]?)\s*[- ]?\s*(minor|min|major|maj|минор|мажор)\b", re.IGNORECASE
)


def explicit_bpm(prompt: str) -> int | None:
    """The user's own tempo ('150 bpm', 'bpm: 92') — sacred, never overridden."""
    match = _BPM_RE.search(prompt or "")
    if not match:
        return None
    try:
        value = int(match.group(1) or match.group(2))
    except (TypeError, ValueError):
        return None
    return value if 60 <= value <= 220 else None


def explicit_key(prompt: str) -> tuple[str, str] | None:
    """The user's own key ('F# minor', 'c мажор') → (root, scale) or None."""
    match = _KEY_RE.search(prompt or "")
    if not match:
        return None
    root = match.group(1).upper().replace("♯", "#").replace("♭", "b")
    if root.endswith("b"):
        flats = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
        root = flats.get(root, root[0])
    if root not in _KEYS:
        return None
    quality = match.group(2).lower()
    scale = "major" if quality.startswith(("maj", "мажор")) else "minor"
    return root, scale


def build_context(
    prompt: str,
    *,
    regenerate: bool = False,
    last_pattern: dict[str, Any] | None = None,
    bump: int = 0,
    genre_hint: str | None = None,
    mood: list[str] | None = None,
) -> dict[str, Any]:
    last = last_pattern if last_pattern is not None else load_last_pattern()
    forbidden = recent_forbidden()
    seed = new_variety_seed(prompt, regenerate=regenerate, bump=bump)
    rng = variety_rng(seed, prompt)

    # Genre lane: the brain's decision (LLM/keyword/artist) beats raw keyword
    # detection; everything below (BPM pool, scale, corpus DNA) follows it.
    genre = None
    try:
        from genre_profiles import PROFILES, detect_genre

        if genre_hint and genre_hint in PROFILES:
            genre = genre_hint
        else:
            genre = detect_genre("", prompt)
    except Exception:  # noqa: BLE001 — variety must never break generation
        genre = genre_hint or None

    recent_keys = set(forbidden.get("keys") or [])
    key_choices = [k for k in _KEYS if k not in recent_keys] or [k for k in _KEYS if k != str((last or {}).get("key") or "")]
    if not key_choices:
        key_choices = list(_KEYS)
    key = rng.choice(key_choices)

    recent_bpms = {int(b) for b in (forbidden.get("bpms") or []) if b is not None}
    base_bpm = 140
    if isinstance(last, dict) and last.get("bpm"):
        try:
            base_bpm = int(last["bpm"])
        except (TypeError, ValueError):
            pass
    # Genre-aware tempo: a 128 BPM "drill" or 150 BPM R&B is wrong before the
    # first note. Corpus ranges keep the pocket authentic per style.
    lo, hi = 128, 152
    try:
        from style_corpus import bpm_range, corpus_enabled

        if corpus_enabled() and genre:
            lo, hi = bpm_range(genre)
    except Exception:  # noqa: BLE001 — variety must never break generation
        pass
    bpm_pool = [b for b in range(lo, hi + 1) if b not in recent_bpms]
    if not bpm_pool:
        bpm_pool = [b for b in range(lo, hi + 1) if b != base_bpm] or list(range(lo, hi + 1))
    bpm = rng.choice(bpm_pool)

    # Genre-aware scale: dark trio for the trap family (default profile has no
    # snap target), the profile's own scale elsewhere (house≠phrygian).
    scale = rng.choice(("minor", "phrygian", "harmonic_minor"))
    try:
        from genre_profiles import PROFILES

        profile = PROFILES.get(genre or "")
        if profile is not None and profile.melody_scale:
            scale = profile.melody_scale
    except Exception:  # noqa: BLE001 — variety must never break generation
        pass

    # The user's explicit ask is SACRED: "150 bpm" means 150, «фа минор» means
    # F minor — anti-clone variety yields to it (it rotates everything else).
    user_bpm = explicit_bpm(prompt)
    if user_bpm is not None:
        bpm = user_bpm
    user_key = explicit_key(prompt)
    if user_key is not None:
        key = user_key[0]
        if user_key[1] == "major":
            scale = "major"
        elif scale == "major":
            scale = "minor"

    # Cold-open vs buildup hook (audit follow-up, 2026-07): rolled once per
    # generation off the same seeded rng as every other variety pick above,
    # weighted by the genre's own hook_buildup_chance (rage/opium/phonk/
    # underground/hyperpop lean cold-open; toliver/kanye/minimal_trap lean
    # buildup; everything else is a real coin-flip-ish 0.4). arrange_song()
    # only reads this — it never rolls its own dice, so it stays reproducible
    # across regenerate with the same seed.
    hook_buildup_chance = 0.4
    try:
        from genre_profiles import PROFILES as _hb_profiles

        hb_profile = _hb_profiles.get(genre or "")
        if hb_profile is not None:
            hook_buildup_chance = float(hb_profile.hook_buildup_chance)
    except Exception:  # noqa: BLE001 — variety must never break generation
        pass
    hook_entry = "buildup" if rng.random() < hook_buildup_chance else "cold_open"

    ctx = {
        "seed": seed,
        "regenerate": regenerate,
        "bump": bump,
        "key": key,
        "scale": scale,
        "genre": genre,
        "mood": list(mood or []),
        "bpm": int(max(60, min(220, bpm))) if user_bpm is not None else int(max(70, min(200, bpm))),
        "bpm_explicit": user_bpm is not None,
        "key_explicit": user_key is not None,
        "drum_variant": rng.randint(0, 7),
        "melody_variant": rng.randint(0, 11),
        "hat_density": rng.choice(("sparse", "pocket", "busy", "triplet")),
        "swing_profile": rng.choice(("light", "medium", "heavy")),
        "hook_entry": hook_entry,
        "last_fingerprint": pattern_fingerprint(last) if isinstance(last, dict) else None,
        "forbidden_samples": sorted(set(forbidden.get("samples") or []) | disliked_sample_names()),
        "forbidden_keys": sorted(recent_keys),
        "forbidden_bpms": sorted(recent_bpms),
    }
    return ctx


def architect_skeleton_defaults(ctx: dict[str, Any], prompt: str) -> dict[str, Any]:
    return {
        "bpm": ctx.get("bpm", 140),
        "key": ctx.get("key", "F"),
        "scale": ctx.get("scale", "minor"),
        "style": "dark trap",
        "sections": [{"name": "core_loop", "bars": 8, "focus": "hook"}],
        "track_plan": {
            "sub_808": "long distorted roots with glides",
            "kick": "tight click kick around 808 gaps",
            "hi_hats": f"{ctx.get('hat_density', 'pocket')} 1/8 hats with short rolls",
            "melody_lead": f"dark sparse {ctx.get('scale', 'minor')} hook in {ctx.get('key', 'F')}",
        },
        "fx_intent": [{"bar": 8, "type": "stop_time", "length_bars": 0.5}],
        "sidechain": 0.55,
        "note": f"variety seed {ctx.get('seed')}",
        "user_prompt": prompt,
    }


def llm_addendum(ctx: dict[str, Any]) -> str:
    try:
        from beat_reward import taste_profile

        taste = taste_profile()
    except ImportError:
        taste = {}

    lines = [
        "[PLG ANTI-CLONE — this beat must be UNIQUE vs every recent generation:]",
    ]
    if ctx.get("genre"):
        mood = ", ".join(str(m) for m in (ctx.get("mood") or [])[:4])
        lines.append(
            f"- Genre lane: {str(ctx['genre']).replace('_', ' ')}"
            + (f" (mood: {mood})" if mood else "")
            + " — commit fully to this lane's pocket, palette and arrangement."
        )
    lines += [
        f"- Key: {ctx.get('key')} {ctx.get('scale', 'minor')} — new harmonic center.",
        f"- BPM: {ctx.get('bpm')} — different pocket and energy.",
        f"- Drum variant #{ctx.get('drum_variant')}: new kick grid, hat density, snare layers.",
        f"- Melody variant #{ctx.get('melody_variant')}: fresh contour, 8+ distinct notes, new rhythm.",
        f"- Swing profile: {ctx.get('swing_profile')} — do not reuse the previous groove.",
        "- Forbidden: copying the same bar grid, melody shape, or drum pattern as the last beats.",
    ]

    fav = taste.get("favorite_genre")
    if fav and int(taste.get("thumbs_up") or 0) >= 2:
        lines.append(f"- User taste leans toward {fav} — keep the vibe but change the music.")

    forbidden_samples = ctx.get("forbidden_samples") or []
    if forbidden_samples:
        lines.append(
            "- Do NOT reuse these recent samples: " + ", ".join(forbidden_samples[:10]) + "."
        )

    forbidden_keys = ctx.get("forbidden_keys") or []
    if forbidden_keys:
        lines.append("- Recent keys to avoid: " + ", ".join(forbidden_keys[:6]) + ".")

    forbidden_bpms = ctx.get("forbidden_bpms") or []
    if forbidden_bpms:
        lines.append("- Recent BPMs to avoid: " + ", ".join(str(b) for b in forbidden_bpms[:6]) + ".")

    if ctx.get("regenerate") or int(ctx.get("bump") or 0) > 0:
        lines.append("- REGENERATION: must sound like a different producer session, not a remix of the last beat.")

    return "\n".join(lines)


def anti_clone_retry_suffix(similarity: float, attempt: int) -> str:
    return (
        f"\n\n[PLG clone guard {attempt}: last output was {similarity:.0%} similar to a recent beat. "
        "Rewrite drums and melody from scratch — new grid, new motif, new velocities.]"
    )


def prime_pattern_variety(pattern: dict[str, Any], ctx: dict[str, Any]) -> None:
    # Never overwrite a BPM locked from an ingested MIDI set_tempo.
    if pattern.get("plg_bpm_locked_from_midi") and pattern.get("plg_midi_tempo_bpm") is not None:
        pattern["bpm"] = pattern["plg_midi_tempo_bpm"]
    else:
        pattern["bpm"] = ctx.get("bpm", pattern.get("bpm"))
    pattern["key"] = ctx.get("key", pattern.get("key"))
    pattern["scale"] = ctx.get("scale", pattern.get("scale"))
    attach_meta(pattern, ctx)


def enforce_midi_bpm(pattern: dict[str, Any]) -> None:
    """Re-assert MIDI tempo after any planner/variety pass that might drift it."""
    if not pattern.get("plg_bpm_locked_from_midi"):
        return
    tempo = pattern.get("plg_midi_tempo_bpm")
    if tempo is None:
        return
    pattern["bpm"] = tempo


def attach_meta(pattern: dict[str, Any], ctx: dict[str, Any]) -> None:
    pattern["plg_variety"] = {
        "seed": ctx.get("seed"),
        "regenerate": bool(ctx.get("regenerate")),
        "bump": ctx.get("bump"),
        "key": ctx.get("key"),
        "bpm": ctx.get("bpm"),
        "scale": ctx.get("scale"),
        "genre": ctx.get("genre"),
        "mood": list(ctx.get("mood") or []),
        "drum_variant": ctx.get("drum_variant"),
        "melody_variant": ctx.get("melody_variant"),
        "swing_profile": ctx.get("swing_profile"),
        # 2026-07 audit: this allowlist silently dropped hook_entry — the
        # buildup-vs-cold-open pick built_context() rolls — so arranger.py's
        # arrange_song() never saw it and BUILDUP_SECTIONS never fired in a
        # single real generation. Keep this in sync with any new ctx key
        # arrange_song()/humanize_pattern() need downstream.
        "hook_entry": ctx.get("hook_entry"),
    }

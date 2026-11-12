"""Genre brain — the LLM understands ANY prompt and picks the musical lane.

Keyword lists only catch prompts that literally name a genre. Real prompts are
free-form: «грустный бит для ночной поездки», "something for the gym", "summer
pool party vibe". This module lets the gateway model read the prompt like a
producer and route it to one of PLG's genre lanes, which then drives the whole
deterministic machine: BPM pool, chord-progression corpus, drum-pocket
blueprint, groove profile and sample matching.

Flow (cheap → smart):
  1. Explicit genre word in the prompt → keyword router, no LLM call, free.
  2. Artist reference → local artist resolver, no LLM call, free.
  3. Otherwise → one tiny LLM call (temperature 0, ~200 tokens) returns
     {"genre": <lane>, "mood": [english words], "energy": 0..1}.
  4. Any failure / no key → "trap" default, exactly the old behaviour.

Env:
    PLG_GENRE_BRAIN=auto|on|off     (auto = on when OPENROUTER_API_KEY is set)
    PLG_GENRE_BRAIN_MODEL=<slug>    (default: the composer model — small task)
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger("plg.genre_brain")

_MAX_MOOD_WORDS = 4

# Lane menu shown to the model. Keep descriptions terse — this is a routing
# task, not an essay. Names must match genre_profiles.PROFILES keys.
_LANES: tuple[tuple[str, str], ...] = (
    ("trap", "modern dark/melodic trap, street rap, 808-driven hip-hop"),
    ("pop_rap", "chart club rap with pop hooks and chant energy"),
    ("rage", "aggressive distorted underground trap, pluggnb, dark energy"),
    ("opium", "minimal sinister phrygian trap, vast space, bell hooks"),
    ("kanye", "soul/gospel sample-flip hip-hop, chopped vintage vinyl, dusty chord stacks, tape echo, swung drums"),
    ("toliver", "floating psychedelic melodic trap, glide pads, bell-harp hooks, airy reverb"),
    ("drill", "UK/NY drill, sliding 808s, ominous piano/strings"),
    ("phonk", "memphis drift phonk, cowbell leads, dusty aggression"),
    # 2026-08-11: these four are in DETAILED_GENRES but were absent from the
    # menu entirely, so the brain could never route to them however clearly the
    # request pointed there.
    ("underground", "raw underground rap, lo-fi grit, hard minimal 808s"),
    ("west_coast", "west coast / g-funk hip-hop, laid-back swing, whistle and synth leads"),
    ("detroit", "detroit/flint rap, sliding aggressive 808s, off-kilter hats"),
    ("pluggnb", "plugg / pluggnb, bright dreamy synths, soft bouncy drums"),
    ("boombap", "90s hip-hop, dusty swung MPC drums, vinyl chops"),
    ("lofi", "lo-fi chill hip-hop, jazzy warm loops, study/relax mood"),
    ("rnb", "smooth R&B / neo-soul, lazy pocket, lush chords"),
    ("pop", "radio pop, big singable chorus, clean modern production"),
    ("dark_pop", "moody synth-pop, 80s neon pulse, alt-pop melancholy"),
    ("disco_pop", "nu-disco / dance-pop, funky bassline, four-on-floor"),
    ("kpop", "polished idol pop, layered synths, dynamic sections"),
    ("hyperpop", "glitchy maximalist pop, detuned supersaws, chaos"),
    ("edm", "festival big-room / future bounce, euphoric drops"),
    ("house", "house / deep house / tech house, groovy 4x4, warm stabs"),
    ("techno", "driving dark techno, relentless kick, hypnotic"),
    ("trance", "uplifting trance, arpeggiated leads, long builds"),
    ("hardstyle", "hardstyle/gabber, distorted kicks, reverse bass"),
    ("dnb", "drum & bass / jungle, fast breaks, reese bass"),
    ("dubstep", "half-time bass music, wobble/growl drops"),
    ("garage", "UK garage / 2-step, shuffled swing, chopped vocals"),
    ("jersey", "jersey club, 5-kick bounce, bed-squeak energy"),
    ("reggaeton", "reggaeton/dembow, tresillo bounce, latin urban"),
    ("dancehall", "dancehall riddim, 3+3+2 pocket, island energy"),
    ("afrobeats", "afrobeats/afro-fusion, syncopated warmth, shakers"),
    ("amapiano", "amapiano, log-drum bass, soft piano, SA groove"),
    ("funk", "funk / g-funk / retro funk-pop, slap bass, swagger"),
    ("soul", "soul / motown / gospel warmth, live backbeat"),
    ("rock", "rock/metal/punk energy, riffs, live drums"),
    ("cinematic", "epic orchestral / score, braams, huge hits"),
)

def _active_lanes() -> tuple[tuple[str, str], ...]:
    """Lane menu under the current focus (MVP ships hip-hop only)."""
    from genre_profiles import allowed_lanes

    allowed = allowed_lanes()
    return tuple((name, desc) for name, desc in _LANES if name in allowed)


def _allowed_names() -> set[str]:
    return {name for name, _ in _active_lanes()}


def _routing_hints() -> str:
    if len(_active_lanes()) < len(_LANES):  # hip-hop focus
        # Every lane named here must also be on the menu above. This used to
        # point at lofi / jersey / boombap, which the 2026-08-11 narrowing
        # removed — the prompt was telling the model to answer off-menu.
        return (
            "- Sad night drive / heartbreak → dark melodic trap or opium; "
            "gym/workout → rage or trap; chill/laid-back/warm → west_coast; "
            "party/club → drill or trap; raw/lo-fi/gritty → underground; "
            "dreamy/bright → pluggnb. When truly ambiguous default to trap.\n"
        )
    return (
        "- Sad night drive / heartbreak → dark_pop or rnb; gym/workout → rage, "
        "hardstyle or trap; beach/summer party → afrobeats, reggaeton or house; "
        "study/rain → lofi. When truly ambiguous default to trap.\n"
    )


def genre_brain_system() -> str:
    return (
        "You are a music A&R brain. Read the user's beat request (any language) and "
        "route it to exactly ONE production lane from the menu. Think like a "
        "producer: mood, tempo words, activity, imagery all hint at the lane. "
        "Never ask questions. Answer with pure JSON only:\n"
        '{"genre": "<lane>", "mood": ["2-4 lowercase english words"], "energy": 0.0-1.0}\n\n'
        "Lane menu:\n"
        + "\n".join(f"- {name}: {desc}" for name, desc in _active_lanes())
        + "\n\nRules:\n"
        "- genre MUST be one lane name from the menu, verbatim.\n"
        "- mood: 2-4 short lowercase english adjectives (e.g. dark, dreamy, "
        "aggressive, nostalgic, bouncy) usable for sample search.\n"
        "- energy: 0.0 (ambient calm) to 1.0 (peak aggression).\n"
        + _routing_hints()
        + "- No prose, no markdown, JSON object only."
    )


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def brain_enabled() -> bool:
    flag = _env("PLG_GENRE_BRAIN", "auto").lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    return bool(_env("OPENROUTER_API_KEY"))


def midi_genre_detect_enabled() -> bool:
    """PLG_MIDI_GENRE_DETECT=auto|on|off — auto/on both enabled (2026-07
    rollout choice: ships on by default, this is just an escape hatch,
    matching every other feature flag in this codebase)."""
    flag = _env("PLG_MIDI_GENRE_DETECT", "auto").lower()
    return flag not in ("0", "false", "no", "off")


def _midi_genre(midi_path: str | Path) -> dict[str, Any] | None:
    """Genre inferred from the uploaded MIDI's own musical content (density,
    interval shape, scale, tempo) — see midi_genre_detect.py. None when the
    file can't be read, is too short, or the signal is too ambiguous to
    trust (never a forced guess — a wrong call here drives the whole
    downstream generation)."""
    if not midi_genre_detect_enabled():
        return None
    try:
        from midi_genre_detect import detect_genre_from_midi, read_tempo_bpm
        from midi_ingest import read_midi_notes

        path = Path(midi_path)
        notes = read_midi_notes(path)
        tempo_bpm = read_tempo_bpm(path)
        hit = detect_genre_from_midi(notes, tempo_bpm)
    except Exception as exc:  # noqa: BLE001 — detection must never block a beat
        logger.info("MIDI genre detection skipped (%s)", exc)
        return None
    if hit is None:
        return None
    genre, confidence = hit
    return {"genre": genre, "confidence": confidence}


def _brain_model() -> str:
    explicit = _env("PLG_GENRE_BRAIN_MODEL")
    if explicit:
        return explicit
    from llm_pipeline import _composer_model

    return _composer_model()


def _keyword_genre(text: str) -> str | None:
    """Explicit genre word in the prompt (keyword router, no default)."""
    from genre_profiles import GENRE_KEYWORDS, PRIORITY

    low = text.lower()
    for genre in PRIORITY:
        if any(keyword in low for keyword in GENRE_KEYWORDS[genre]):
            return genre
    if any(k in low for k in ("trap", "трэп", "треп", "hip hop", "hip-hop", "хип-хоп", "рэп", "rap")):
        return "trap"
    return None


def _artist_genre(text: str) -> str | None:
    from artist_styles import resolve_artist_style

    hit = resolve_artist_style(text)
    return str(hit["genre"]) if hit else None


def _sanitize_moods(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:_MAX_MOOD_WORDS]:
        word = str(item).strip().lower()
        if word and len(word) <= 24 and word.replace("-", "").replace(" ", "").isalpha():
            out.append(word)
    return out


@lru_cache(maxsize=64)
def _classify_llm(prompt: str, model: str, focus: str) -> str:
    """Raw LLM classification, cached per (prompt, model, focus). Returns JSON text."""
    from llm_client import openrouter_json_object

    result = openrouter_json_object(
        f"Beat request:\n{prompt}\n\nRoute it. JSON only.",
        genre_brain_system(),
        model=model,
        temperature=0.0,
        max_tokens=220,
    )
    return json.dumps(result, ensure_ascii=False)


def classify_prompt(text: str) -> dict[str, Any] | None:
    """LLM lane classification (raw, unclamped) — needs a gateway key.

    Used locally when the key is present, and by the CLOUD endpoint
    ``/v1/genre/resolve`` so paying subscribers get the full brain even though
    their key lives server-side. Returns {genre, mood, energy} or None."""
    from genre_profiles import lane_focus

    raw = json.loads(_classify_llm(text, _brain_model(), lane_focus()))
    genre = str(raw.get("genre") or "").strip().lower()
    if genre not in {name for name, _ in _LANES}:
        logger.warning("Genre brain returned unknown lane %r — ignored.", raw.get("genre"))
        return None
    try:
        energy: float | None = max(0.0, min(1.0, float(raw.get("energy"))))
    except (TypeError, ValueError):
        energy = None
    return {"genre": genre, "mood": _sanitize_moods(raw.get("mood")), "energy": energy}


@lru_cache(maxsize=64)
def _cloud_classify_cached(text: str) -> str:
    """Ask the PLG cloud to classify (subscriber path — key is server-side)."""
    import plg_cloud

    resp = plg_cloud.resolve_genre(text)
    if not isinstance(resp, dict) or not resp.get("ok"):
        return "{}"
    return json.dumps(
        {"genre": resp.get("genre"), "mood": resp.get("mood"), "energy": resp.get("energy")},
        ensure_ascii=False,
    )


def _cloud_classify(text: str) -> dict[str, Any] | None:
    try:
        import plg_cloud

        if not plg_cloud.is_cloud_mode():
            return None
        raw = json.loads(_cloud_classify_cached(text))
    except Exception as exc:  # noqa: BLE001 — cloud brain must never block generation
        logger.info("Cloud genre brain unavailable (%s)", exc)
        return None
    genre = str(raw.get("genre") or "").strip().lower()
    if genre not in {name for name, _ in _LANES}:
        return None
    try:
        energy: float | None = max(0.0, min(1.0, float(raw.get("energy"))))
    except (TypeError, ValueError):
        energy = None
    return {"genre": genre, "mood": _sanitize_moods(raw.get("mood")), "energy": energy}


def resolve_generation_genre(prompt: str, *, midi_path: str | Path | None = None) -> dict[str, Any]:
    """Genre lane + mood for this generation.

    Returns {"genre": str | None, "mood": list[str], "energy": float | None,
    "source": "keyword" | "artist" | "midi" | "llm" | None}. ``genre`` is
    None only when nothing (including the LLM) produced a confident lane —
    callers then keep the legacy trap default.

    ``midi_path`` — when a MIDI file is uploaded as the core motif, its own
    musical content (density, interval shape, scale, tempo — see
    midi_genre_detect.py) is checked FIRST, ahead of keyword/artist/LLM text
    matching: once real material is on the table, text-prompt genre words
    are no longer authoritative (2026-07 decision — the whole beat should be
    built around what was actually uploaded). Falls through to the existing
    text-based chain when detection is inconclusive.
    """
    from genre_profiles import clamp_to_focus

    if midi_path:
        midi_hit = _midi_genre(midi_path)
        if midi_hit:
            logger.info(
                "Genre brain: lane=%s source=midi confidence=%.2f",
                midi_hit["genre"], midi_hit["confidence"],
            )
            return {
                "genre": clamp_to_focus(midi_hit["genre"]),
                "mood": [],
                "energy": None,
                "source": "midi",
                "confidence": midi_hit["confidence"],
            }

    text = (prompt or "").strip()
    if not text:
        return {"genre": None, "mood": [], "energy": None, "source": None}

    keyword = _keyword_genre(text)
    if keyword:
        return {"genre": clamp_to_focus(keyword), "mood": [], "energy": None, "source": "keyword"}

    artist = _artist_genre(text)
    if artist:
        return {"genre": clamp_to_focus(artist), "mood": [], "energy": None, "source": "artist"}

    raw: dict[str, Any] | None = None
    if brain_enabled():
        try:
            raw = classify_prompt(text)
        except Exception as exc:  # noqa: BLE001 — brain must never block generation
            logger.warning("Genre brain call failed (%s) — keyword fallback.", exc)
    else:
        # No local gateway key (cloud subscription): the server classifies
        # with ITS key — paying users keep the flagship any-prompt brain.
        raw = _cloud_classify(text)

    if raw is None:
        return {"genre": None, "mood": [], "energy": None, "source": None}

    genre = str(raw["genre"])
    if genre not in _allowed_names():
        genre = clamp_to_focus(genre) if genre in {n for n, _ in _LANES} else ""
    if not genre:
        return {"genre": None, "mood": list(raw.get("mood") or []), "energy": None, "source": None}

    moods = list(raw.get("mood") or [])
    energy = raw.get("energy")
    logger.info("Genre brain: %s (mood=%s energy=%s)", genre, ",".join(moods) or "-", energy)
    return {"genre": genre, "mood": moods, "energy": energy, "source": "llm"}

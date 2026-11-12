"""PLG data-generation pipeline (draft -> refine -> deterministic build).

Instead of one generic model call, a beat is designed by specialised models routed
through OpenRouter (one OPENROUTER_API_KEY unlocks all), then built by code.

Current defaults (AITUNNEL GPT-5.6 luna line — kopeck-tier):

  Stage 1 — Architect (gpt-5.6-luna-pro, structural)
      Reads the prompt + sample catalog + producer context, reasons about
      structure, and returns a compact rich JSON *skeleton*: bpm, key/scale, sections,
      melodic motifs, 808 glide plan, hat-roll map, fx intent, build order,
      saturation/width intent. Fast and cheap.

  Stage 2 — Composer (gpt-5.6-luna, musical engineer)
      Takes the skeleton and writes the full output_pattern.json — the actual
      note arrays with humanised timing/velocity, swing, fx_automation — matching
      the existing strict schema so nothing downstream changes.

  Stage 3 — Finisher (gpt-5.6-luna, QA)
      Repairs only broken JSON/schema/range issues without changing the music.

  Stage 4 — Builder (deterministic, NOT an LLM)
      flp_writer.py turns that JSON into the .flp. Deterministic on purpose: a
      tested byte-writer is why the project opens first-click with no plugin
      conflicts. Letting an LLM emit the build code would re-introduce exactly the
      bugs we removed, so the "builder titan" is code, not a model.

Models are env-configurable (slugs drift on AITUNNEL/OpenRouter). Any stage failure
raises and llm_client.generate_pattern transparently falls back to the single-stage
path. Budget defaults to <= 0.50 RUB / beat. QA is skipped when it would push the
estimated stage cost over budget.
"""

from __future__ import annotations

import json
import logging
import os
import random
from typing import Any

# GPT-5.6 luna line on AITUNNEL — kopeck-tier, non-reasoning JSON writers.
# Keep COMPOSER_FALLBACK off in .env unless a second provider is needed.
ARCHITECT_MODEL_DEFAULT = "gpt-5.6-luna-pro"
COMPOSER_MODEL_DEFAULT = "gpt-5.6-luna"
COMPOSER_FALLBACK_MODEL_DEFAULT = "claude-haiku-4.5"
QA_MODEL_DEFAULT = "gpt-5.6-luna"
# Hard cap per beat (anomaly insurance). Stage estimates below must sum <= this
# or the pipeline refuses to start / skips QA (see _qa_allowed).
MAX_BEAT_COST_RUB_DEFAULT = 0.50
# Budget bookkeeping for the luna line — headroom under the 0.50 RUB cap so
# architect + composer + QA all fit (0.18+0.16+0.12 = 0.46).
ARCHITECT_COST_RUB_DEFAULT = 0.18
COMPOSER_COST_RUB_DEFAULT = 0.16
QA_COST_RUB_DEFAULT = 0.12


def _architect_model() -> str:
    return (os.environ.get("PLG_TITAN_ARCHITECT_MODEL") or ARCHITECT_MODEL_DEFAULT).strip()


def _composer_model() -> str:
    return (os.environ.get("PLG_TITAN_COMPOSER_MODEL") or COMPOSER_MODEL_DEFAULT).strip()


def _qa_model() -> str:
    """3rd finisher pass. Set PLG_TITAN_QA_MODEL=off to disable."""
    raw = (os.environ.get("PLG_TITAN_QA_MODEL") or QA_MODEL_DEFAULT).strip()
    return "" if raw.lower() in ("0", "false", "no", "off", "none") else raw


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _max_beat_cost_rub() -> float:
    return max(0.0, _env_float("PLG_MAX_BEAT_COST_RUB", MAX_BEAT_COST_RUB_DEFAULT))


def _stage_costs_rub() -> dict[str, float]:
    return {
        "architect": max(0.0, _env_float("PLG_TITAN_ARCHITECT_COST_RUB", ARCHITECT_COST_RUB_DEFAULT)),
        "composer": max(0.0, _env_float("PLG_TITAN_COMPOSER_COST_RUB", COMPOSER_COST_RUB_DEFAULT)),
        "qa": max(0.0, _env_float("PLG_TITAN_QA_COST_RUB", QA_COST_RUB_DEFAULT)),
    }


def _qa_allowed(costs: dict[str, float]) -> bool:
    return bool(_qa_model()) and costs["architect"] + costs["composer"] + costs["qa"] <= _max_beat_cost_rub()


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _short_context(system_instruction: str, max_chars: int = 1800) -> str:
    """Avoid sending the architect/composer the whole sample catalog twice."""
    text = (system_instruction or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[producer context truncated; sample picking is deterministic]..."


def _root_note(skeleton: dict[str, Any]) -> str:
    key = str(skeleton.get("key") or skeleton.get("root") or "F").strip().upper()
    if not key:
        key = "F"
    root = key[0]
    accidental = "#" if "#" in key[:2] else ""
    return root + accidental


def _minor_notes(root: str, octave: int) -> list[str]:
    chroma = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    idx = chroma.index(root) if root in chroma else 5
    intervals = (0, 2, 3, 5, 7, 8, 10)
    return [f"{chroma[(idx + step) % 12]}{octave + ((idx + step) // 12)}" for step in intervals]


def _note(t: float, name: str, length: float, velocity: int, **extra: Any) -> dict[str, Any]:
    out = {
        "time_step": round(float(t), 4),
        "note": name,
        "length": round(float(length), 4),
        "velocity": max(1, min(127, int(velocity))),
    }
    out.update(extra)
    return out


def deterministic_pattern_from_skeleton(
    prompt: str,
    skeleton: dict[str, Any],
    *,
    reason: str,
    variety_seed: int | None = None,
) -> dict[str, Any]:
    """Valid sparse pattern when the composer returns broken/truncated JSON."""
    rng = random.Random(variety_seed if variety_seed is not None else hash(prompt) % (2**32))
    root = str(skeleton.get("key") or _root_note(skeleton))
    if root not in ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"):
        root = _root_note(skeleton)
    scale = _minor_notes(root, 3)
    bpm = int(max(70, min(200, _num(skeleton.get("bpm"), 140))))

    total_bars = 8
    total_beats = total_bars * 4

    # Style-corpus foundation: authentic genre pocket + a real chord progression
    # instead of random degrees. Corpus off / import error → legacy variants.
    dna: dict[str, Any] | None = None
    try:
        from genre_profiles import detect_genre
        from style_corpus import corpus_enabled, style_dna

        if corpus_enabled():
            dna = style_dna(
                detect_genre(str(skeleton.get("style") or ""), prompt),
                key=root,
                scale=str(skeleton.get("scale") or "minor"),
                seed=variety_seed,
                bars=total_bars,
            )
    except Exception:  # noqa: BLE001 — fallback must never fail
        dna = None

    bass_names: list[str] | None = None
    if dna:
        from music_theory import name_for_midi

        cell = dna["drum_cell"]
        cell_beats = cell["bar_len"] * 4
        reps = max(1, total_beats // cell_beats)
        kick_steps = [round(t + r * cell_beats, 4) for r in range(reps) for t in cell["kick"]]
        snare_steps = [round(t + r * cell_beats, 4) for r in range(reps) for t in cell["snare"]]
        bass_roots = list(dna["progression"]["bar_degrees"])
        bass_names = [
            name_for_midi(24 + ((int(pc) - 24) % 12))
            for pc in dna["progression"]["roots_pc"]
        ]
    else:
        kick_variants = [
            [0, 2, 2.75, 4, 5.5, 6, 8, 10, 10.75, 12, 13.5, 14],
            [0, 1.5, 2, 4, 5, 6, 8, 9.5, 10, 12, 14, 15],
            [0, 2, 3.5, 4, 6, 7, 8, 10, 11.5, 12, 14, 15.5],
            [0, 0.5, 2, 4, 4.75, 6, 8, 10.5, 12, 13, 14, 15],
        ]
        snare_variants = [
            [4 * bar + 2 for bar in range(8)],
            [4 * bar + 1.5 for bar in range(8)],
            [4 * bar + 2.5 for bar in range(8)],
        ]
        kick_steps = kick_variants[rng.randint(0, len(kick_variants) - 1)]
        snare_steps = snare_variants[rng.randint(0, len(snare_variants) - 1)]
        bass_roots = [rng.randint(0, 6) for _ in range(8)]

    clap_steps = [step + 0.01 for step in snare_steps]
    hat_stride = 0.5 if rng.random() > 0.35 else 0.25
    hat_steps = [round(i * hat_stride, 4) for i in range(int(total_beats / hat_stride))]
    roll_steps = [7.5, 15.5, 23.5, 31.0, 31.25, 31.5, 31.75]

    from symbolic_composer import (
        apply_opium_octave_jumps,
        compose_opium_counter_melody,
        compose_opium_melody_hook,
        tile_melody_loop,
        _key_cfg_from_pattern,
    )

    fallback_key_ctx = {
        "key": root,
        "scale": str(skeleton.get("scale") or "minor"),
        "plg_variety": {"melody_variant": rng.randint(0, 11)},
    }
    key_cfg = _key_cfg_from_pattern(fallback_key_ctx, rng)
    hook, key_cfg = compose_opium_melody_hook(
        rng,
        key_cfg=key_cfg,
        melody_variant=int(fallback_key_ctx["plg_variety"]["melody_variant"]),
    )
    melody_lead = tile_melody_loop(hook, total_bars)
    melody_lead = apply_opium_octave_jumps(melody_lead, rng)
    counter_melody = compose_opium_counter_melody(rng, key_cfg, total_bars=total_bars)

    tracks = {
        "kick": [_note(t, "C5", 0.22, 116) for t in kick_steps if t < total_beats],
        "snare": [_note(t, "D5", 0.18, 106) for t in snare_steps],
        "clap": [_note(t, "E5", 0.14, 92) for t in clap_steps],
        "hi_hats": [
            _note(t, "F#5", 0.08, 70 + (18 if int(t * 2) % 4 == 0 else 0))
            for t in hat_steps
        ] + [_note(t, "A#5", 0.045, 86, roll=True) for t in roll_steps],
        "sub_808": [
            _note(
                bar * 4,
                bass_names[bar % len(bass_names)]
                if bass_names
                else scale[degree % len(scale)].replace("3", "1"),
                1.65,
                124,
                glide=bar in (2, 5, 7),
            )
            for bar, degree in enumerate(bass_roots)
        ],
        "melody_lead": melody_lead,
        "counter_melody": counter_melody,
    }

    result_extra: dict[str, Any] = {}
    if dna:
        prog = dict(dna["progression"])
        prog["genre"] = dna["genre"]
        prog["key"] = dna["key"]
        prog["scale"] = dna["scale"]
        prog["label"] = dna["label"]
        prog["seed"] = variety_seed
        result_extra["plg_progression"] = prog

    return {
        "bpm": bpm,
        "style": str(skeleton.get("style") or "dark trap"),
        "key": key_cfg.get("label", root).split()[0] if isinstance(key_cfg.get("label"), str) else root,
        "scale": str(skeleton.get("scale") or "minor"),
        "tracks": tracks,
        **result_extra,
        "build_order": ["kick", "sub_808", "snare", "clap", "hi_hats", "melody_lead", "counter_melody"],
        "fx_automation": {"Channel_Precomputed": {"boost": 0.12}},
        "fx_intent": skeleton.get("fx_intent") if isinstance(skeleton.get("fx_intent"), list) else [],
        "mix_db": skeleton.get("mix_db") if isinstance(skeleton.get("mix_db"), dict) else {},
        "user_prompt": prompt,
        "plg_pipeline_fallback": {
            "mode": "deterministic_skeleton_to_pattern",
            "reason": reason,
        },
        "plg_symbolic_opium_melody": True,
        "plg_offline_melody": True,
        "plg_key": str(key_cfg.get("label", root)),
    }


ARCHITECT_SYSTEM = """\
You are THE ARCHITECT — a Grammy-winning executive producer (stadium trap, dark
psychedelic rap, soul-chop hip-hop, arena pop). You design a RECORD a top vocalist
steps into — never a static loop. Decide fast, first-take confidence; output ONE
compact JSON object, nothing else.

THE STANDARD (a placement, not a demo):
- One IDEA rules the beat: one memorable motif, developed (variation / register
  lift / answer phrase). Hum test: singable after one listen, or redesign.
- Tension -> release: verses strip and breathe, the hook lands with weight. Place
  ONE "oh sh*t" moment on the bar grid (drum cut, octave lift, stop_time).
- The 808 is a lead instrument: walks the changes, converses with the kick,
  glides at phrase ends — never a one-pitch drone.
- Space sounds expensive: each element has ONE job, midrange open for the vocal.
- Groove is micro: velocity waves, ghost notes, hats push/pull — no machine-gun.
- Harmony has color: real tension (phrygian b2, 9ths, leading tones), voice-led
  bar to bar. Commit fully to the requested genre's pocket — never a re-skinned
  generic trap loop.
- Sections contrast in density/register/energy — verse ≠ chorus, always.

Output a compact JSON SKELETON (no full note arrays):
- bpm (int), key (e.g. "F"), scale ("minor"/"phrygian"/...), style, energy
- sections: [{name, bars, focus}] with an energy arc, not 4 copies of one loop
- motif_plan: melody + counter_melody as scale degrees / contour / rhythm cells.
  Hook arc start->peak->resolve, rests for vocals, ONE variation per 4 bars;
  counter answers in the hook's rests, different register.
- bass_808_plan: root motion on the changes, glide moments (phrase ends), sustain
  lengths, octave jumps, where the 808 leaves space for the kick.
- drum_pocket: kick/snare/clap placement, swing, hat-roll map, velocity waves,
  ghosts — the kick is a signature bounce locked to the 808, not 4 random hits.
- track_plan: per track (kick, snare, clap, hi_hats, sub_808, melody_lead,
  counter_melody) one short role + density note.
- fx_intent: arrangement MOMENTS on the bar grid (1-indexed), e.g.
  [{"bar":8,"type":"crash","length":4},{"bar":8,"type":"riser"},
  {"bar":9,"type":"stop_time","length_bars":1}]. Types: crash, riser, downlifter,
  impact, texture, sweep, stop_time. Riser into the hook, impact on the drop,
  stop_time sets up the punchline — every event earns its place.
- build_order: array of track keys
- mix_db: per-track dB. Clean commercial physics: melody DOWN (~ -8..-12) for
  808/sub air, kick punchy (~ -1..-3), hats/clap under the kick. Drums in front,
  music behind, low end king. Code clamps and protects the master.
- sidechain: pump depth 0..1 (pop/house ~0.8, trap/opium ~0.4-0.6, off = 0).
- processing_intent: tanh saturation drive/mix per lane, Haas width on melodic
  lanes, mono low-end (plugin-free; deterministic builder applies it).
- notes_for_composer: 1-3 sentences — the record's feel and where its moment is.

A [PLG STYLE DNA] block in the prompt is the PROVEN foundation: keep its key,
progression and bar-by-bar 808 root walk exactly; spend creativity on motifs,
pocket, fx moments and arrangement on top. Never replace the progression.

Hard limits: max 4 sections; 2 motif cells per melodic lane; 8 glide moments;
8 hat-roll moments; 6 fx_intent events. Keep every text field short. Answer as
minified JSON (no spaces/newlines), no prose, no markdown, no hidden reasoning.\
"""

COMPOSER_SYSTEM = """\
You are THE COMPOSER — first-call session musician on a Grammy-winning team. Turn
the architect's skeleton into finished, knocking music as ONE strict JSON object.
Honor the skeleton's bpm / key / scale / genre / sections / build_order exactly.

MUSICIANSHIP (non-negotiable):
- Melody = phrases, not note salad: a 1-2 bar motif with an arc (start -> peak ->
  resolve), repeated with ONE variation (new ending / up a step / displaced).
  6-12 distinct pitches; chord tones on strong beats, passing tones between;
  phrase ends resolve. Leave AIR between phrases (>= 1 beat each 2 bars) — a
  vocalist lives there; never punch rests into the middle of the motif.
- counter_melody answers in the melody's rests (call-and-response), a register
  above or below the hook — it converses, never fights.
- 808 follows the skeleton's root walk bar by bar, syncopated against the kick
  (some hits locked, some answering off-beat), long sustains, glides only where
  marked, NEVER two 808 notes overlapping. Kick = a signature bounce cluster
  locked to the 808, not evenly-spaced hits.
- Velocity is a performance: downbeat accents 110-127, ghosts 55-80, hat waves
  cresting into bar ends. No runs of identical velocity.
- fx_automation: distortion / waveshaping / sidechain where it serves the beat;
  keep the architect's saturation/width intent as metadata — deterministic
  Python applies the audio math.

OUTPUT CONTRACT (hard):
- ONLY the beat_pattern JSON per the schema. No prose, markdown, Python, FLP
  bytes, or plugin instructions — flp_writer.py builds the .flp.
- SPARSE CORE: 8 bars (12 max) and <= 140 notes TOTAL across all tracks. PLG's
  deterministic engine expands rolls, swing, hat density and the full arrangement
  after your JSON — do not emit hundreds of hat notes.
- Minified JSON (no spaces/newlines). No artist or label names anywhere.\
"""

COMPOSER_SCHEMA_HINT = """\
Required JSON shape:
{
  "bpm": 140,
  "style": "dark trap",
  "tracks": {
    "kick": [{"time_step": 0, "note": "C5", "length": 0.25, "velocity": 112}],
    "snare": [],
    "clap": [],
    "hi_hats": [],
    "sub_808": [],
    "melody_lead": [],
    "counter_melody": []
  },
  "build_order": ["kick", "sub_808", "snare", "clap", "hi_hats", "melody_lead"],
  "fx_automation": {},
  "mix_db": {}
}

Rules:
- time_step is in beats, 0 <= time_step < total bars * 4.
- Sparse core: 8 bars (12 max), <= 140 notes total; deterministic PLG expands
  arrangement/hats later.
- Density target — write ALL 8 bars, not 2: 70-130 notes total. melody_lead
  16-28 notes (6-12 distinct pitches), sub_808 8-14, kick 12-24, snare/clap
  8-16 each, hi_hats 24-48 sparse pocket (rolls added later).
- counter_melody is REQUIRED, not decoration: 4-10 notes answering in
  melody_lead's own rests, a different register. An empty counter_melody
  array is treated as an incomplete beat_pattern.
- Every note has time_step, note, length, velocity.
- Valid track keys only: kick, snare, snare_layer, clap, hi_hats, sub_808,
  melody_lead, counter_melody.
- Output minified JSON — no spaces, no newlines.
"""

ARCHITECT_USER_TEMPLATE = (
    "User prompt:\n{prompt}\n\n"
    "Design the structural skeleton for this beat. Return ONLY a JSON object."
)

COMPOSER_USER_TEMPLATE = (
    "User prompt:\n{prompt}\n\n"
    "ARCHITECT SKELETON (follow bpm/key/sections/build_order, then make it knock):\n"
    "{skeleton}\n\n"
    "Now write the full beat_pattern JSON."
)

QA_SYSTEM = """\
You are the FINISHER — the mastering-room engineer protecting a finished record.
The composer already played the take; you do NOT rewrite a note of it. You only
repair what is technically broken:
- fix schema/type problems and missing required fields
- clamp velocities into range, drop notes with length <= 0 or bad note names
- remove exact duplicate notes
Keep bpm, style, the groove/swing/feel and every musical choice intact. If nothing
is broken, return the beat unchanged. Return the full corrected beat_pattern JSON.\
"""

QA_USER_TEMPLATE = (
    "Finished beat JSON — QA it, fix ONLY what is broken, keep the music:\n{pattern}"
)


def run_qa(
    prompt: str,
    system_instruction: str,
    json_schema: dict[str, Any],
    pattern: dict[str, Any],
    *,
    model: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Best-effort finisher pass. Never degrades: returns the input on any issue."""
    from llm_client import openrouter_json_object, safe_error_summary

    model = model or _qa_model()
    if not model or not enabled:
        return pattern
    system = QA_SYSTEM + "\n\n--- PRODUCER CONTEXT ---\n" + system_instruction
    user = QA_USER_TEMPLATE.format(pattern=json.dumps(pattern, ensure_ascii=False))
    user += (
        "\n\nReturn ONLY the corrected JSON object conforming to this schema "
        "(no prose, no markdown fences):\n" + json.dumps(json_schema)
    )
    try:
        fixed = openrouter_json_object(user, system, model=model, temperature=0.2, max_tokens=16384)
    except Exception as exc:  # noqa: BLE001
        logging.warning(
            "QA stage (%s) failed (%s) — keeping composer output.",
            model,
            safe_error_summary(exc),
        )
        return pattern
    if isinstance(fixed, dict) and fixed.get("tracks"):
        logging.info("QA pass applied (%s).", model)
        return fixed
    logging.warning("QA stage (%s) returned no tracks — keeping composer output.", model)
    return pattern


def _architect_timeout_ms() -> int:
    """Architect gets a short leash: a 3-minute hang before the fallback IS the
    bad UX. Default 60s, then the fast fallback model gets one shot."""
    return int(_env_float("PLG_TITAN_ARCHITECT_TIMEOUT_MS", 60_000))


def _architect_fallback_model() -> str:
    """When the reasoning architect times out / returns empty (burned its budget
    thinking), a fast non-reasoning model writes the skeleton instead. Same
    prompt, cheaper, near-instant — the composer still gets a real plan."""
    raw = (os.environ.get("PLG_TITAN_ARCHITECT_FALLBACK_MODEL") or _composer_model()).strip()
    return "" if raw.lower() in ("0", "false", "no", "off", "none") else raw


def run_architect(prompt: str, system_instruction: str, *, model: str | None = None) -> dict[str, Any]:
    from llm_client import openrouter_json_object, safe_error_summary

    model = model or _architect_model()
    system = ARCHITECT_SYSTEM + "\n\n--- PRODUCER CONTEXT ---\n" + _short_context(system_instruction)
    user = ARCHITECT_USER_TEMPLATE.format(prompt=prompt)
    # Reasoning models spend hidden thinking tokens INSIDE max_tokens; at 3072
    # deepseek-v4-pro truncated mid-skeleton (empty/broken JSON) on real prompts.
    max_tokens = int(_env_float("PLG_TITAN_ARCHITECT_MAX_TOKENS", 5000))

    try:
        skeleton = openrouter_json_object(
            user,
            system,
            model=model,
            temperature=0.4,
            max_tokens=max_tokens,
            timeout_ms=_architect_timeout_ms(),
        )
    except Exception as exc:  # noqa: BLE001 — one retry on the fast model
        fallback = _architect_fallback_model()
        if not fallback or fallback == model:
            raise
        logging.warning(
            "Architect (%s) failed (%s) — retrying skeleton on fast model %s.",
            model,
            safe_error_summary(exc),
            fallback,
        )
        skeleton = openrouter_json_object(
            user,
            system,
            model=fallback,
            temperature=0.4,
            max_tokens=max_tokens,
            timeout_ms=_architect_timeout_ms(),
        )
    logging.info(
        "Architect skeleton: bpm=%s style=%s sections=%s",
        skeleton.get("bpm"),
        skeleton.get("style"),
        len(skeleton.get("sections") or []),
    )
    return skeleton


def _composer_timeout_ms() -> int:
    """Composer leash independent of the global gateway timeout: a short global
    (e.g. 60s in .env) strangles a 4k-token JSON write on slow gateways, and the
    emergency pattern builder that runs after IS the school-tier beat."""
    return int(_env_float("PLG_TITAN_COMPOSER_TIMEOUT_MS", 90_000))


def _composer_fallback_model() -> str:
    """One retry when the composer times out / errors. Default: a different
    provider's fast model, so one provider's bad day doesn't kill the beat."""
    raw = (
        os.environ.get("PLG_TITAN_COMPOSER_FALLBACK_MODEL")
        or COMPOSER_FALLBACK_MODEL_DEFAULT
    ).strip()
    return "" if raw.lower() in ("0", "false", "no", "off", "none") else raw


def run_composer(
    prompt: str,
    system_instruction: str,
    json_schema: dict[str, Any],
    skeleton: dict[str, Any],
    *,
    model: str | None = None,
) -> dict[str, Any]:
    from llm_client import openrouter_json_object, safe_error_summary

    model = model or _composer_model()
    # Keep the composer fast. The architect has already absorbed the heavy
    # producer context and sample catalog; feeding it all again caused
    # 3-minute gateway timeouts.
    _ = system_instruction
    system = COMPOSER_SYSTEM
    user = COMPOSER_USER_TEMPLATE.format(
        prompt=prompt,
        skeleton=json.dumps(skeleton, ensure_ascii=False, separators=(",", ":")),
    )
    user += (
        "\n\nReturn ONLY a JSON object. No prose, no markdown fences.\n\n"
        + COMPOSER_SCHEMA_HINT
    )
    from generation_variety import active_context

    ctx = active_context() or {}
    temperature = 0.82 if ctx.get("regenerate") else 0.65
    max_tokens = int(_env_float("PLG_TITAN_COMPOSER_MAX_TOKENS", 4096))
    try:
        return openrouter_json_object(
            user,
            system,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_ms=_composer_timeout_ms(),
        )
    except Exception as exc:  # noqa: BLE001 — one retry before the emergency builder
        fallback = _composer_fallback_model()
        if not fallback:
            raise
        logging.warning(
            "Composer (%s) failed (%s) — one retry on %s with a tighter budget.",
            model,
            safe_error_summary(exc),
            fallback,
        )
        return openrouter_json_object(
            user,
            system,
            model=fallback,
            temperature=temperature,
            max_tokens=min(max_tokens, 3072),
            timeout_ms=_composer_timeout_ms(),
        )


def generate_pattern_titans(
    prompt: str,
    system_instruction: str,
    json_schema: dict[str, Any],
    *,
    variety_seed: int | None = None,
) -> dict[str, Any]:
    """Run architect -> composer. Raises on any failure (caller falls back)."""
    logging.info(
        "Titans pipeline: architect=%s composer=%s budget<=%.2f RUB",
        _architect_model(),
        _composer_model(),
        _max_beat_cost_rub(),
    )
    costs = _stage_costs_rub()
    required_cost = costs["architect"] + costs["composer"]
    if required_cost > _max_beat_cost_rub():
        raise RuntimeError(
            "Titan budget exceeded before generation: "
            f"architect+composer={required_cost:.2f} RUB > max={_max_beat_cost_rub():.2f} RUB. "
            "Lower PLG_TITAN_*_COST_RUB estimates or use cheaper models."
        )
    from generation_variety import active_context, architect_skeleton_defaults
    from llm_client import safe_error_summary

    variety_ctx = active_context() or {}
    try:
        skeleton = run_architect(prompt, system_instruction)
    except Exception as exc:  # noqa: BLE001
        # Composer still runs SOLO on the deterministic skeleton defaults —
        # the composer writing real music over a stock plan beats the
        # emergency pattern builder every time. Only a composer failure
        # drops to code.
        logging.warning(
            "Architect stage failed (%s) — composer runs solo.",
            safe_error_summary(exc),
        )
        skeleton = architect_skeleton_defaults(variety_ctx, prompt)

    try:
        pattern = run_composer(prompt, system_instruction, json_schema, skeleton)
    except Exception as exc:  # noqa: BLE001
        reason = safe_error_summary(exc)
        logging.warning("Composer stage failed (%s) — deterministic skeleton builder runs.", reason)
        pattern = deterministic_pattern_from_skeleton(
            prompt, skeleton, reason=reason, variety_seed=variety_seed
        )
    if not isinstance(pattern, dict) or "tracks" not in pattern:
        pattern = deterministic_pattern_from_skeleton(
            prompt,
            skeleton,
            reason="composer returned no tracks",
            variety_seed=variety_seed,
        )

    # Carry the architect's loudness matrix into the deterministic gain staging.
    if isinstance(skeleton.get("mix_db"), dict):
        pattern.setdefault("mix_db", skeleton["mix_db"])
    if isinstance(pattern.get("mix_db"), dict):
        from mix_gain_staging import apply_mix_db

        applied = apply_mix_db(pattern)
        if applied:
            logging.info("Applied architect mix_db -> gain staging: %s", applied)

    # Sidechain pump curve (architect's geometric grid). Math only — see module.
    try:
        from sidechain_curve import attach_sidechain_curve

        depth = None
        sc_hint = skeleton.get("sidechain") if isinstance(skeleton, dict) else None
        if isinstance(sc_hint, (int, float)):
            depth = float(sc_hint)
        elif isinstance(sc_hint, dict) and sc_hint.get("depth") is not None:
            depth = float(sc_hint["depth"])
        if depth is None:
            # Architect silent → the genre's own pump (house breathes ~0.9,
            # boombap barely ducks ~0.25) instead of one 0.82 for everything.
            from generation_variety import active_context
            from style_corpus import mix_recipe

            recipe = mix_recipe((active_context() or {}).get("genre")) or {}
            if recipe.get("sidechain") is not None:
                depth = float(recipe["sidechain"])
        curve = attach_sidechain_curve(pattern, depth=depth)
        if curve:
            logging.info("Sidechain pump curve: depth=%s over %s kicks", curve["depth"], curve["kicks"])
    except Exception as exc:  # noqa: BLE001
        logging.info("Sidechain curve skipped (%s)", exc)

    # FX intent (architect) -> grid-locked plan + audible stop-time drum cuts.
    if isinstance(skeleton.get("fx_intent"), list) and not pattern.get("fx_intent"):
        pattern["fx_intent"] = skeleton["fx_intent"]
    if pattern.get("fx_intent"):
        try:
            from fx_layer import apply_fx_intent

            plan = apply_fx_intent(pattern)
            if plan:
                logging.info(
                    "FX plan: %s events, stop-time cut %s drum hits",
                    len(plan.get("fx_events", [])),
                    plan.get("stop_time_removed", 0),
                )
        except Exception as exc:  # noqa: BLE001
            logging.info("FX intent skipped (%s)", exc)

    qa_enabled = _qa_allowed(costs)
    if _qa_model() and not qa_enabled:
        logging.info(
            "QA skipped to keep beat under budget: %.2f + QA %.2f > %.2f RUB",
            required_cost,
            costs["qa"],
            _max_beat_cost_rub(),
        )
    pattern = run_qa(prompt, system_instruction, json_schema, pattern, enabled=qa_enabled)

    pattern["plg_pipeline"] = {
        "mode": "titans",
        "architect": _architect_model(),
        "composer": _composer_model(),
        "qa": _qa_model() if qa_enabled else None,
        "qa_skipped_for_budget": bool(_qa_model() and not qa_enabled),
        "estimated_cost_rub": round(required_cost + (costs["qa"] if qa_enabled else 0.0), 2),
        "max_cost_rub": _max_beat_cost_rub(),
    }
    return pattern

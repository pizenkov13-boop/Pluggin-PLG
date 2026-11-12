"""LLM-assisted kick/808_bass note placement.

2026-07: three-plus rounds of purely deterministic kick/808_bass rules
(density laws, call-and-response role weighting, 8-bar structural repeat —
see bass808_gen.py) kept passing every formal invariant test but still
didn't land right on real listens. This module gives an LLM real musical
judgement over WHERE to place kick and 808 hits, instead of hardcoded
probabilities.

2026-08: three-stage scheme, mirroring llm_pipeline.py's titans naming —
Architect (COMPOSER_MODEL_DEFAULT's bigger sibling, ARCHITECT_MODEL_DEFAULT,
one song-level concept call, never per window) -> Composer
(COMPOSER_MODEL_DEFAULT, the note-placement call below, per window,
unchanged in behavior from the original single-stage design) -> QA
(QA_MODEL_DEFAULT, fallback-only JSON repair — never an always-on third
call, see _qa_repair). See run_architect/generate() for exactly where each
stage sits.

Design:
  - Strict JSON-schema mode (response_format: json_schema, strict=True) —
    the payload is a simple, flat, uniform note-list schema (unlike the
    composer's complex nested musical structure), so strict mode is
    expected to be reliable here where it wasn't tried for the composer.
  - The LLM PROPOSES; bass808_gen's own deterministic helpers still DISPOSE
    (in-key snap, register clamp, strict mono, bounds clamp) — same
    philosophy as EQ/envelope/phase-align already being deterministic
    post-processing, extended to pitch/timing sanity on the note-placement
    layer itself. The LLM's output is never trusted at face value.
  - A same-process cache keyed on the RESOLVED generation context (genre,
    bpm, bars, progression, arrangement, melody density/energy — not on
    anything LLM-specific) — backend_core.py's outer retry loop can call
    humanize_pattern up to 10x for one beat (5 clone rounds x up to 2
    quality retries each), and within one clone round the context is
    provably identical across retries (same uploaded MIDI, same pinned
    progression) — so the second+ call is a free in-memory hit, not a
    second paid+timed request.
  - ANY failure (missing key, HTTP error, timeout, malformed JSON, schema
    mismatch, degenerate/empty output after sanitization) falls back to
    bass808_gen.build_kick_808_pocket silently — this module must never be
    able to break generation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from typing import Any

logger = logging.getLogger("plg.bass808_llm")

# 2026-08: three-stage scheme, mirroring llm_pipeline.py's titans naming —
# Architect (song-level concept, once per generate() call, never per window)
# -> Composer (the existing per-window strict-schema note placement) -> QA
# (fallback-only JSON repair on a truncated/incomplete response, never an
# always-on third call). See run_architect/_qa_repair below.
ARCHITECT_MODEL_DEFAULT = "gpt-5.6-luna"
COMPOSER_MODEL_DEFAULT = "gpt-5.6-luna"
# DeepSeek does not support response_format=json_schema/strict (only
# json_object — see openrouter_json_object) — confirmed against DeepSeek's
# own API docs. Fine here: QA only repairs JSON structure, it never needs
# schema-level enforcement the way the strict-mode Composer call does.
QA_MODEL_DEFAULT = "deepseek-v4-flash-0731"
TIMEOUT_MS_DEFAULT = 30_000
MAX_TOKENS_DEFAULT = 1400
TEMPERATURE_DEFAULT = 0.7
# 2026-08 fix: the luna line turned out to be reasoning models, not the
# "kopeck-tier non-reasoning JSON writers" this module was designed around —
# real captured calls showed reasoning_tokens regularly eating 500-1500+ of
# the 1400-token MAX_TOKENS_DEFAULT budget, truncating the visible JSON
# mid-array (parse failures, "violates call/response roles" after a partial
# array survives sanitization, or outright timeouts when reasoning is slow).
# AITUNNEL is OpenRouter-compatible and documents a `reasoning: {effort}`
# request field (https://docs.aitunnel.ru/api/reasoning) that caps
# reasoning separately from the visible-output budget; confirmed live
# against gpt-5.6-luna — effort="minimal" dropped reasoning_tokens from
# 900-1500+ to ~250 with finish_reason="stop" (complete, untruncated JSON)
# on the exact composer schema/prompt shape. Applied to the Architect and
# Composer calls (both luna-family); NOT applied to QA repair, which uses a
# different model (deepseek) with unconfirmed support for this field.
REASONING_EFFORT_DEFAULT = "minimal"

# Generous sanity ceiling — NOT the "2-3 per 4 bars" deterministic-era law
# (the whole point is letting the model use real musical judgement instead
# of a hardcoded density target). Only guards against a truly pathological
# response (e.g. a note on every 16th), which mono-abut would survive
# mechanically but which is never a legitimate placement.
_MAX_ONSETS_PER_BAR = 3.0
_MIN_ONSETS_PER_4_BARS = 4
_MAX_ONSETS_PER_4_BARS = 8
_WINDOW_BARS = 8
_WINDOW_BEATS = _WINDOW_BARS * 4.0


def _composer_model() -> str:
    return (os.environ.get("PLG_KICK808_LLM_MODEL") or COMPOSER_MODEL_DEFAULT).strip()


def _architect_model() -> str:
    """PLG_KICK808_ARCHITECT_MODEL=off disables the architect stage — the
    per-window composer still runs solo, same as if the architect call
    simply failed (see run_architect)."""
    raw = (os.environ.get("PLG_KICK808_ARCHITECT_MODEL") or ARCHITECT_MODEL_DEFAULT).strip()
    return "" if raw.lower() in ("0", "false", "no", "off", "none") else raw


def _qa_model() -> str:
    """PLG_KICK808_QA_MODEL=off disables the QA repair fallback — a window
    that fails to parse or fails coverage is rejected immediately, same as
    before this feature existed."""
    raw = (os.environ.get("PLG_KICK808_QA_MODEL") or QA_MODEL_DEFAULT).strip()
    return "" if raw.lower() in ("0", "false", "no", "off", "none") else raw


def _timeout_ms() -> int:
    try:
        return int(os.environ.get("PLG_KICK808_LLM_TIMEOUT_MS", str(TIMEOUT_MS_DEFAULT)))
    except (TypeError, ValueError):
        return TIMEOUT_MS_DEFAULT


def _max_tokens() -> int:
    try:
        return int(os.environ.get("PLG_KICK808_LLM_MAX_TOKENS", str(MAX_TOKENS_DEFAULT)))
    except (TypeError, ValueError):
        return MAX_TOKENS_DEFAULT


def _reasoning_effort() -> str:
    """PLG_KICK808_REASONING_EFFORT=off disables the cap (back to whatever
    the model does by default) — see REASONING_EFFORT_DEFAULT's docstring
    for why this exists."""
    raw = (os.environ.get("PLG_KICK808_REASONING_EFFORT") or REASONING_EFFORT_DEFAULT).strip()
    return "" if raw.lower() in ("0", "false", "no", "off", "none") else raw


def enabled() -> bool:
    """Env-level kill switch (PLG_LLM_KICK808=0/off disables globally,
    independent of any genre's llm_kick808_enabled flag) + API key present."""
    flag = os.environ.get("PLG_LLM_KICK808", "auto").strip().lower()
    if flag in ("0", "off", "false", "no"):
        return False
    return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())


_SYSTEM = """\
You are an expert hip-hop/trap producer placing ONLY kick and 808 bass \
notes for one song — nothing else. A separate deterministic engine already \
placed hi-hats/snare/clap and the melody is the user's own uploaded MIDI; \
you never see or touch those, just place kick+808 to answer them well.

MUSICIANSHIP:
- 808 pitch is assigned deterministically after your response from the exact \
root of the current bass/chord bar. You do not choose note names. C is used \
only when the real harmonic root is C (or the supplied progression itself \
falls back to C).
- Use a sparse, heavy kick-led pocket: normally 1-2 kick hits per bar. Never \
write kick rolls or two kick hits less than one beat apart.
- Kick and 808 share the strongest foundation hits, especially beat one at a \
section opening, but they are NOT cloned streams. After the foundation, use \
kick-only punctuation and bass-only answers. Fast double notes and late \
fourth-bar fills belong to the 808 alone while kick stays silent.
- Across a window, paired, kick-only and bass-only roles must all be present \
when there are enough notes. Usually only 35-75% of kick hits are paired.
- Rare, deliberate accents only (an octave-up stab, a single slide into a \
high register, a one-off extra or missing hit at a phrase boundary) — never \
on every phrase, never stacked.
- Vary the pattern across different song sections (verse vs hook vs drop) \
to match the given per-section melody energy/density — busier sections can \
carry a little more, sparse verses should stay sparse.

OUTPUT CONTRACT (hard):
- ONLY the JSON object per the schema. No prose, no markdown, no code fences.
- time_step is in beats from song start (0 = bar 1 beat 1), 4 beats per bar.
- You are assigned ONE exact window. Fill that whole window, not a shorter \
loop or example. Copy its exact start/end into coverage and set complete=true \
only after you have deliberately considered every bar in the window.
- Every active window needs kick and 808 coverage, including a paired \
foundation near the start. Do not force an 808 onto every kick and do not \
force a kick onto bass runs. A deliberate rest may occupy individual bars.
- Do not choose dynamics: velocity is deliberately absent from the schema. \
The playback engine owns fixed kick/808 dynamics; you cannot alter them.
- Every bass note must use slide=false and articulation="short". The engine \
sets every 808 to a short 0.75-beat hit.
- Kick has no pitch (it's a one-shot sample) — only time_step and velocity.
- Every bass note needs its own length; don't rely on the next note to end it.
"""

_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["coverage", "kick", "bass"],
    "properties": {
        "coverage": {
            "type": "object",
            "additionalProperties": False,
            "required": ["start", "end", "complete"],
            "properties": {
                "start": {"type": "number"},
                "end": {"type": "number"},
                "complete": {"type": "boolean"},
            },
        },
        "kick": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["time_step"],
                "properties": {
                    "time_step": {"type": "number"},
                },
            },
        },
        "bass": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["time_step", "articulation", "slide"],
                "properties": {
                    "time_step": {"type": "number"},
                    "articulation": {
                        "type": "string",
                        "enum": ["short", "legato"],
                    },
                    "slide": {"type": "boolean"},
                },
            },
        },
    },
}


def _progression_text(roots_pc: list[int], root_pc: int) -> str:
    from music_theory import name_for_midi

    names = [name_for_midi(60 + (int(pc) % 12))[:-1] for pc in roots_pc]
    return ", ".join(names) if names else name_for_midi(60 + int(root_pc) % 12)[:-1]


def _build_prompt(context: dict[str, Any]) -> str:
    sections = context.get("sections") or []
    section_lines = []
    for s in sections:
        section_lines.append(
            f"  - {s.get('name', '?')}: bars {s.get('start_bar', 0)}-"
            f"{s.get('start_bar', 0) + s.get('bars', 0)}, energy={s.get('energy', 1.0):.2f}, "
            f"melody_density={s.get('density_ratio', 1.0):.2f}"
        )
    sections_text = "\n".join(section_lines) if section_lines else "  (single section, no arrangement breakdown)"
    clap_times = context.get("clap_times") or []
    clap_text = (
        ", ".join(f"{t:.2f}" for t in clap_times[:24])
        if clap_times
        else "none given — don't worry about clashing"
    )
    melody_accents = context.get("melody_accents") or []
    accent_text = (
        ", ".join(f"{t:.2f}" for t in melody_accents[:24])
        if melody_accents
        else "none"
    )
    architect_notes = str(context.get("architect_notes") or "").strip()
    architect_block = (
        f"\nArchitect's plan for this window's sections (advisory — the "
        f"sparse/kick-led pocket contract from the system instructions "
        f"always applies regardless):\n"
        f"{architect_notes}\n"
        if architect_notes
        else ""
    )

    return f"""\
Genre: {context.get('genre')}
BPM: {context.get('bpm')}
Song span for kick/808: beats {context.get('start')} to {context.get('end')} \
({context.get('bars')} bars)
This is window {context.get('window_index')} of {context.get('window_count')}. \
Return coverage.start={context.get('start')}, coverage.end={context.get('end')}, \
coverage.complete=true exactly.
Key: root pitch class {context.get('root_pc')}, scale={context.get('scale')}
Chord progression (one chord root per bar, repeating): {context.get('progression_text')}
Comfortable 808 register: {context.get('register_lo')} to {context.get('register_hi')} \
(e.g. C4 to C5); a rare accent may reach up to {context.get('accent_hi')} (e.g. B6)
slides_allowed={str(bool(context.get('allow_slides'))).lower()}; enforce the \
slide contract from the system instructions exactly.
Melody character: onset_rate={context.get('onset_rate', 0):.2f}, \
busyness={context.get('busyness', 0):.2f}, pace={context.get('pace', 'pocket')}
Song sections (name, bar range, energy, relative melody density):
{sections_text}
Existing clap hit times (beats) — avoid landing a syncopated kick exactly on \
these: {clap_text}
Selected strong melody anchors (beats): {accent_text}
Use some of these anchors when they strengthen the pocket; do not chase every \
melody note and never exceed the kick-density contract.
{architect_block}
Place kick and 808 notes for this entire span now, per the system \
instructions."""


def _cache_key(context: dict[str, Any]) -> str:
    canon = json.dumps(context, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _window_ranges(start: float, end: float) -> list[tuple[float, float]]:
    windows: list[tuple[float, float]] = []
    cursor = float(start)
    while cursor < end - 1e-6:
        window_end = min(float(end), cursor + _WINDOW_BEATS)
        windows.append((round(cursor, 5), round(window_end, 5)))
        cursor = window_end
    return windows


def _coverage_matches(raw: dict[str, Any], *, start: float, end: float) -> bool:
    coverage = raw.get("coverage")
    if not isinstance(coverage, dict) or coverage.get("complete") is not True:
        return False
    try:
        declared_start = float(coverage["start"])
        declared_end = float(coverage["end"])
    except (KeyError, TypeError, ValueError):
        return False
    return abs(declared_start - start) <= 1e-6 and abs(declared_end - end) <= 1e-6


def _call_response_valid(
    kicks: list[dict[str, Any]],
    bass: list[dict[str, Any]],
) -> bool:
    if not kicks or not bass:
        return False
    kick_times = {round(float(n["time_step"]), 3) for n in kicks}
    bass_attack_times = {
        round(float(n["time_step"]), 3)
        for n in bass
        if not bool(n.get("slide"))
    }
    paired = len(kick_times & bass_attack_times)
    ratio = paired / len(kick_times)
    ordered_kicks = sorted(kick_times)
    no_rolls = all(right - left >= 0.95 for left, right in zip(ordered_kicks, ordered_kicks[1:]))
    per_bar: dict[int, int] = {}
    for time in kick_times:
        bar = int(time // 4.0)
        per_bar[bar] = per_bar.get(bar, 0) + 1
    # A short edge window may not have room for all three roles, hence the
    # ratio gate rather than an unconditional set-difference requirement.
    roles_split = len(kick_times | bass_attack_times) < 4 or (
        bool(kick_times - bass_attack_times) and bool(bass_attack_times - kick_times)
    )
    return 0.15 <= ratio <= 0.85 and no_rolls and all(count <= 2 for count in per_bar.values()) and roles_split


def _window_sections(
    sections: list[dict[str, Any]],
    *,
    start: float,
    end: float,
) -> list[dict[str, Any]]:
    start_bar = start / 4.0
    end_bar = end / 4.0
    return [
        dict(section)
        for section in sections
        if float(section.get("start_bar", 0)) < end_bar
        and float(section.get("start_bar", 0)) + float(section.get("bars", 0)) > start_bar
    ]


_ARCHITECT_SYSTEM = """\
You are the kick/808 ARCHITECT — you set the song-level concept for where \
the low end breathes and where it should hit hardest, before a second \
model places the actual notes window by window. You do NOT place any \
notes yourself and never see individual beats.

The note-placement model works a sparse, kick-led pocket: normally 1-2 \
kick hits per bar, roughly 35-75% of kicks paired with a simultaneous 808 \
(the rest is kick-only punctuation or 808-only answers), with rare \
deliberate accents. You are not changing that contract — only leaning it \
lighter or heavier per section, within its existing range.

For each song section given to you, decide:
- density: "sparse" | "normal" | "dense" — within the pocket's own 1-2 \
kick/bar and 35-75%-paired range, whether this section should sit toward \
the sparse end (more silence, fewer kick-only/808-only extras) or the \
dense end (busier within that same range). Never asks for anything outside \
the note-placement model's own contract.
- accent: "none" | "slide" | "octave_stab" — the one deliberate rare \
accent type this section may use, matching the "rare, deliberate accents" \
rule the note-placement model already follows. Most sections should be \
"none" — accents are rare by design, never a default.

Return ONLY a JSON object:
{"section_plan": [{"name": <section name>, "density": ..., "accent": ...}], \
"notes": <one or two sentences of overall guidance, or empty string>}
No prose outside the JSON, no markdown fences.\
"""


def _architect_user_prompt(context: dict[str, Any]) -> str:
    sections = context.get("sections") or []
    section_lines = [
        f"  - {s.get('name', '?')}: bars {s.get('start_bar', 0)}-"
        f"{s.get('start_bar', 0) + s.get('bars', 0)}, energy={s.get('energy', 1.0):.2f}, "
        f"melody_density={s.get('density_ratio', 1.0):.2f}"
        for s in sections
    ]
    sections_text = "\n".join(section_lines) if section_lines else "  (single section, no arrangement breakdown)"
    return (
        f"Genre: {context.get('genre')}\n"
        f"BPM: {context.get('bpm')}\n"
        f"Song span for kick/808: beats {context.get('start')} to {context.get('end')}\n"
        f"Sections:\n{sections_text}\n\n"
        "Decide the density/accent plan for these sections now."
    )


def run_architect(context: dict[str, Any]) -> dict[str, Any] | None:
    """Song-level concept plan — called ONCE per generate() call, never per
    window (see the call site). Best-effort: any failure (disabled, no key,
    timeout, malformed JSON, missing section_plan) returns None, and every
    window's prompt simply omits the architect section — generation
    proceeds exactly as if this stage didn't exist. The architect never
    blocks or degrades the composer/QA stages."""
    model = _architect_model()
    if not model:
        return None
    from llm_client import openrouter_json_object

    effort = _reasoning_effort()
    try:
        plan = openrouter_json_object(
            _architect_user_prompt(context),
            _ARCHITECT_SYSTEM,
            model=model,
            temperature=0.5,
            max_tokens=600,
            timeout_ms=_timeout_ms(),
            reasoning={"effort": effort} if effort else None,
        )
    except Exception as exc:  # noqa: BLE001 — enrichment only, never required
        logger.info("kick/808 architect (%s) skipped (%s)", model, exc)
        return None
    if not isinstance(plan, dict) or not isinstance(plan.get("section_plan"), list):
        logger.info("kick/808 architect (%s) returned no section_plan — skipped", model)
        return None
    return plan


def _architect_notes_for_window(plan: dict[str, Any] | None, section_names: list[str]) -> str:
    """Render only the guidance relevant to THIS window's own overlapping
    sections (section_names comes from _window_sections), plus the
    song-level free-text note (same for every window)."""
    if not plan:
        return ""
    by_name = {
        str(item.get("name")): item
        for item in (plan.get("section_plan") or [])
        if isinstance(item, dict)
    }
    lines = [
        f"  - {name}: density={by_name[name].get('density', 'normal')}, "
        f"accent={by_name[name].get('accent', 'none')}"
        for name in section_names
        if name in by_name
    ]
    notes = str(plan.get("notes") or "").strip()
    if not lines and not notes:
        return ""
    text = "\n".join(lines)
    if notes:
        text = (text + "\n" if text else "") + f"  overall: {notes}"
    return text


_CACHE: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]] | None] = {}
_CACHE_ORDER: list[str] = []
_CACHE_MAX = 64


def _cache_get(key: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    if key not in _CACHE:
        return None
    logger.info("kick/808 LLM cache hit — skipping request+cost entirely")
    return _CACHE[key]


def _cache_put(key: str, value: tuple[list[dict[str, Any]], list[dict[str, Any]]] | None) -> None:
    if key in _CACHE:
        return
    _CACHE[key] = value
    _CACHE_ORDER.append(key)
    while len(_CACHE_ORDER) > _CACHE_MAX:
        stale = _CACHE_ORDER.pop(0)
        _CACHE.pop(stale, None)


def clear_cache() -> None:
    """Test hook — the module-level cache otherwise lives for the process."""
    _CACHE.clear()
    _CACHE_ORDER.clear()


class _RawContentError(RuntimeError):
    """The composer's strict-schema response failed json.loads — almost
    always a max_tokens truncation (strict mode only guarantees that
    COMPLETED tokens conform to the schema; a response cut off mid-write is
    still invalid JSON regardless of strict=True). Distinct from every other
    _call_llm failure (network/HTTP/empty/wrong-type) so generate()'s window
    loop can route ONLY this case through the QA repair fallback — see
    _qa_repair. Carries the raw text and usage so nothing from the request
    is lost if repair succeeds."""

    def __init__(self, content: str, usage: dict[str, Any]):
        super().__init__("kick/808 LLM response failed to parse as JSON")
        self.content = content
        self.usage = usage


def _call_llm(prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (parsed_json, usage). Raises on any failure — caller decides
    the fallback, this function never swallows an error itself."""
    import httpx

    from llm_client import _openrouter_base, _openrouter_headers, _strip_schema_constraints
    from local_only import require_network

    require_network("an AI request")
    model = _composer_model()
    schema = _strip_schema_constraints(_JSON_SCHEMA)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "kick_808_pocket", "strict": True, "schema": schema},
        },
        "temperature": TEMPERATURE_DEFAULT,
        "max_tokens": _max_tokens(),
    }
    effort = _reasoning_effort()
    if effort:
        payload["reasoning"] = {"effort": effort}
    timeout_s = _timeout_ms() / 1000.0
    logger.info("kick/808 LLM request (%s)...", model)
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(
            f"{_openrouter_base()}/chat/completions",
            headers=_openrouter_headers(),
            json=payload,
        )
    if resp.status_code >= 400:
        detail = resp.text[:400]
        try:
            detail = resp.json().get("error", {}).get("message") or detail
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"OpenRouter {resp.status_code}: {detail}")
    body = resp.json()
    choice = body["choices"][0]
    content = (choice["message"]["content"] or "").strip()
    if not content:
        raise RuntimeError("kick/808 LLM returned empty content.")
    usage = dict(body.get("usage") or {})
    usage["finish_reason"] = choice.get("finish_reason")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise _RawContentError(content, usage) from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("kick/808 LLM did not return a JSON object.")
    return parsed, usage


_QA_SYSTEM = """\
You are a QA engineer. Your ONLY job is to fix JSON SYNTAX problems in the \
kick/808 placement response below — unbalanced brackets, a missing or \
trailing comma, an array cut off mid-write. If the "kick" or "bass" array \
was truncated before reaching the window's end, extend it by repeating the \
SAME rhythmic spacing already established earlier in that array — never \
invent a new pattern, never add notes beyond what completes the coverage.

Do not change any time_step, articulation, or slide value that is already \
present and syntactically valid — you are repairing structure, not \
composing. If the JSON is already valid and fully covers the window, \
return it completely unchanged.

Return ONLY the corrected JSON object. No prose, no explanation, no \
markdown fences (no ```json).\
"""

_QA_USER_TEMPLATE = (
    "Window: beats {start} to {end} ({bars} bars). Required shape:\n"
    '{{"coverage":{{"start":{start},"end":{end},"complete":true}},'
    '"kick":[{{"time_step":<number>}}],'
    '"bass":[{{"time_step":<number>,"articulation":"short"|"legato","slide":<bool>}}]}}\n\n'
    "Broken/incomplete response to repair:\n{raw}"
)


def _qa_repair(raw_text: str, *, start: float, end: float) -> dict[str, Any] | None:
    """Fallback-only JSON repair — see generate()'s two call sites (a parse
    failure via _RawContentError, or a successfully-parsed response whose
    coverage doesn't match the window). Never runs for a window that
    already succeeded, and is not an always-on third call.

    DeepSeek does not support response_format=json_schema/strict (only
    json_object — confirmed against DeepSeek's own API docs, 2026-08),
    unlike the Composer's _call_llm above. openrouter_json_object already
    uses json_object, so this is deliberately built on top of it rather
    than reusing _call_llm's strict-schema request shape.

    Returns a parsed dict on success, None on any failure — the caller
    falls through to the pre-existing reject-and-fallback path either way,
    so a QA failure is never worse than not having this feature."""
    model = _qa_model()
    if not model:
        return None
    from llm_client import openrouter_json_object

    bars = int(round((end - start) / 4.0))
    user = _QA_USER_TEMPLATE.format(start=start, end=end, bars=bars, raw=raw_text[:8000])
    try:
        fixed = openrouter_json_object(
            user,
            _QA_SYSTEM,
            model=model,
            temperature=0.0,
            max_tokens=_max_tokens(),
            timeout_ms=_timeout_ms(),
        )
    except Exception as exc:  # noqa: BLE001 — repair is best-effort
        logger.warning("kick/808 QA repair (%s) failed (%s)", model, exc)
        return None
    if not isinstance(fixed, dict) or "kick" not in fixed or "bass" not in fixed:
        logger.warning("kick/808 QA repair (%s) returned no kick/bass arrays", model)
        return None
    return fixed


def _lane_stats(items: Any, *, start: float, end: float) -> dict[str, Any]:
    """Compact, privacy-safe diagnostics for one raw or sanitized note lane."""
    notes = [n for n in (items or []) if isinstance(n, dict)]
    times: list[float] = []
    velocities: list[int] = []
    for note in notes:
        try:
            times.append(float(note["time_step"]))
        except (KeyError, TypeError, ValueError):
            pass
        try:
            velocities.append(int(note["velocity"]))
        except (KeyError, TypeError, ValueError):
            pass
    window_beats = 8 * 4.0
    windows: list[dict[str, Any]] = []
    cursor = float(start)
    while cursor < end - 1e-6:
        window_end = min(float(end), cursor + window_beats)
        windows.append(
            {
                "start": round(cursor, 3),
                "end": round(window_end, 3),
                "notes": sum(1 for t in times if cursor - 1e-6 <= t < window_end - 1e-6),
            }
        )
        cursor = window_end
    return {
        "count": len(notes),
        "valid_times": len(times),
        "min_time": round(min(times), 3) if times else None,
        "max_time": round(max(times), 3) if times else None,
        "velocity_min": min(velocities) if velocities else None,
        "velocity_max": max(velocities) if velocities else None,
        "velocity_unique": sorted(set(velocities)),
        "slide_count": sum(1 for n in notes if n.get("slide") or n.get("glide")),
        "windows": windows,
    }


def _response_stats(
    kick: Any,
    bass: Any,
    *,
    start: float,
    end: float,
) -> dict[str, Any]:
    kick_stats = _lane_stats(kick, start=start, end=end)
    bass_stats = _lane_stats(bass, start=start, end=end)
    kick_times = {
        round(float(n["time_step"]), 3)
        for n in (kick or [])
        if isinstance(n, dict) and isinstance(n.get("time_step"), (int, float))
    }
    bass_notes = [n for n in (bass or []) if isinstance(n, dict)]
    bass_stats["paired_count"] = sum(
        1
        for n in bass_notes
        if isinstance(n.get("time_step"), (int, float))
        and round(float(n["time_step"]), 3) in kick_times
    )
    return {"kick": kick_stats, "bass": bass_stats}


def _sanitize(
    raw: dict[str, Any],
    *,
    start: float,
    end: float,
    root_pc: int,
    scale: str,
    roots_pc: list[int],
    kick_template: dict[str, Any] | None,
    allow_slides: bool = False,
    bpm: float = 140.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The LLM proposes; this disposes — every note re-validated through the
    SAME deterministic helpers bass808_gen.py's own generator uses (in-key
    snap, register clamp, strict mono, bounds clamp). Never trusts the LLM's
    JSON at face value, exactly like phrase_root_overrides' own in-key guard."""
    from bass808_gen import (
        MAX_BASS_LENGTH,
        MIN_BASS_LENGTH,
        SLIDE_HI,
        SLIDE_LO,
        SUB_HI,
        SUB_LO,
        _BASS_VEL,
        _KICK_VEL,
        BASS_NOTE_LENGTH,
        _abut_mono,
        _kick_note,
        _snap_pc_to_scale,
    )
    from music_theory import SCALES, name_for_midi

    kick_note_name = str((kick_template or {}).get("note") or "C5")
    # 2026-07 fix: this function's own docstring already promised an
    # "in-key snap" — it never actually happened, so an off-scale roots_pc
    # bar root (chromatic/borrowed degree, or a stale scale upstream) sailed
    # straight through unguarded. Same scale_pcs shape bass808_gen.py's own
    # _materialize_cell snaps into, so the LLM path can never place a pitch
    # class the deterministic path wouldn't have allowed.
    scale_pcs = {(int(root_pc) + interval) % 12 for interval in SCALES.get(scale, SCALES["natural_minor"])}

    # At 150+ BPM a 4-bar phrase crosses the listener much faster.  Permit
    # one extra legal onset per cell; all spacing/role/pitch guards below
    # still apply, so this cannot create kick rolls.
    max_onsets_per_cell = _MAX_ONSETS_PER_4_BARS + (1 if bpm >= 150.0 else 0) + (1 if bpm >= 170.0 else 0)
    kicks: list[dict[str, Any]] = []
    for item in (raw.get("kick") or [])[: int((end - start) * _MAX_ONSETS_PER_BAR / 4.0) + 8]:
        if not isinstance(item, dict):
            continue
        try:
            t = float(item["time_step"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (start - 1e-6 <= t < end):
            continue
        kicks.append(_kick_note(t, {"note": kick_note_name, "velocity": _KICK_VEL}))

    # The prompt asks for a dense pocket, not a machine-gun wall. Keep one
    # anchor in every bar, then at most one additional syncopation per bar and
    # one extra per four-bar cell: 4-5 paired attacks per four bars.
    all_kicks = sorted(kicks, key=lambda n: float(n["time_step"]))
    selected_kick_times: set[float] = set()
    for bar_start in range(int(start // 4.0) * 4, int(math.ceil(end / 4.0)) * 4, 4):
        candidates = [
            n for n in all_kicks
            if float(bar_start) <= float(n["time_step"]) < min(float(bar_start + 4), end)
        ]
        if candidates:
            selected_kick_times.add(round(float(candidates[0]["time_step"]), 3))
    for cell_start in range(int(start // 16.0) * 16, int(math.ceil(end / 16.0)) * 16, 16):
        cell_end = min(float(cell_start + 16), end)
        chosen_in_cell = sum(
            1 for t in selected_kick_times if float(cell_start) <= t < cell_end
        )
        for note in all_kicks:
            t = round(float(note["time_step"]), 3)
            if not (float(cell_start) <= t < cell_end) or t in selected_kick_times:
                continue
            bar_key = int((t - start) // 4.0)
            in_bar = sum(1 for kept in selected_kick_times if int((kept - start) // 4.0) == bar_key)
            if in_bar >= 3 or chosen_in_cell >= max_onsets_per_cell:
                continue
            selected_kick_times.add(t)
            chosen_in_cell += 1
    kicks = [
        note for note in all_kicks
        if round(float(note["time_step"]), 3) in selected_kick_times
    ]

    kick_times = {round(float(n["time_step"]), 3) for n in kicks}
    bass_items: list[tuple[float, dict[str, Any]]] = []
    for item in (raw.get("bass") or [])[: int((end - start) * _MAX_ONSETS_PER_BAR / 4.0) + 8]:
        if not isinstance(item, dict):
            continue
        try:
            t = float(item["time_step"])
        except (KeyError, TypeError, ValueError):
            continue
        if start - 1e-6 <= t < end:
            bass_items.append((t, item))
    bass_items.sort(key=lambda pair: pair[0])

    bass_raw: list[dict[str, Any]] = []
    for index, (t, item) in enumerate(bass_items):
        bar = int(t // 4.0)
        harmonic_pc = (
            int(roots_pc[bar % len(roots_pc)]) % 12
            if roots_pc
            else int(root_pc) % 12
        )
        harmonic_pc = _snap_pc_to_scale(harmonic_pc, scale_pcs)
        final_midi = SUB_LO + harmonic_pc
        final_midi = max(SUB_LO, min(SUB_HI, final_midi))

        next_t = bass_items[index + 1][0] if index + 1 < len(bass_items) else end
        pre_paired_air = 0.125 if round(next_t, 3) in kick_times else 0.0
        available = max(0.0, next_t - t - pre_paired_air)
        articulation = "short"
        target_length = BASS_NOTE_LENGTH
        length = min(MAX_BASS_LENGTH, target_length, available)
        if 0.0 < length < MIN_BASS_LENGTH:
            length = min(MIN_BASS_LENGTH, available)
        bass_raw.append(
            {
                "time_step": round(t, 5),
                "note": name_for_midi(final_midi),
                "length": round(length, 5),
                "velocity": _BASS_VEL,
                "pan": 64,
                "bass808_gen": True,
                "articulation": articulation,
            }
        )

    # Strict mono is non-negotiable regardless of what the LLM asked for.
    bass = _abut_mono(bass_raw)

    for n in bass:
        n["kick_lock"] = round(float(n["time_step"]), 3) in kick_times

    return kicks, bass


def generate(
    *,
    start: float,
    end: float,
    bars: int,
    root_pc: int,
    scale: str,
    roots_pc: list[int],
    genre: str,
    bpm: float,
    clap_times: list[float],
    kick_template: dict[str, Any] | None,
    arrangement: list[dict[str, Any]] | None,
    melody_energy: dict[str, Any] | None,
    section_density: list[dict[str, Any]] | None,
    melody_accents: list[float] | None = None,
    allow_slides: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Returns (kicks, bass) from the LLM, or None if unavailable/disabled —
    caller (beat_humanize._apply_808_bass_lane) falls back to
    bass808_gen.build_kick_808_pocket on None OR on any exception raised
    here (this function does NOT catch its own errors — the caller decides
    the fallback, so tests can tell "LLM failed" apart from "LLM disabled")."""
    if not enabled():
        return None

    sections_ctx = []
    if arrangement:
        density_by_start = {
            round(float(d.get("start", 0)), 2): float(d.get("ratio", 1.0))
            for d in (section_density or [])
            if isinstance(d, dict)
        }
        for s in arrangement:
            if not isinstance(s, dict):
                continue
            start_bar = int(s.get("start_bar", 0) or 0)
            sec_start_beats = round(start_bar * 4.0, 2)
            sections_ctx.append(
                {
                    "name": str(s.get("name", "")),
                    "start_bar": start_bar,
                    "bars": int(s.get("bars", 0) or 0),
                    "energy": float(s.get("energy", 1.0) or 1.0),
                    "density_ratio": density_by_start.get(sec_start_beats, 1.0),
                }
            )

    energy = melody_energy or {}
    context = {
        "genre": genre,
        "bpm": round(float(bpm), 1),
        "start": round(start, 3),
        "end": round(end, 3),
        "bars": bars,
        "root_pc": int(root_pc) % 12,
        "scale": scale,
        "roots_pc": [int(pc) % 12 for pc in roots_pc],
        "clap_times": [round(float(t), 3) for t in sorted(clap_times)],
        "melody_accents": [
            round(float(t), 3)
            for t in sorted(melody_accents or [])
            if start <= float(t) < end
        ],
        "sections": sections_ctx,
        "onset_rate": round(float(energy.get("onset_rate", 0.0) or 0.0), 3),
        "busyness": round(float(energy.get("busyness", 0.0) or 0.0), 3),
        "pace": str(energy.get("pace") or "pocket"),
        "allow_slides": bool(allow_slides),
    }
    key = _cache_key(context)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    if key in _CACHE:
        # A prior call for this exact context already failed/degenerated —
        # don't pay for a second network round-trip inside the same retry
        # loop just to fail the same way again.
        return None

    # Architect: ONCE per generate() call, never per window — see
    # run_architect's own docstring for why (cost/latency: a per-window call
    # would multiply architect cost by len(windows) for no coherence gain,
    # since the concept is song-level by nature). run_architect already
    # catches its own openrouter_json_object failures, but this call site is
    # wrapped too, defensively — "architect never blocks generation" must
    # hold even if a caller-injected run_architect (e.g. a test double, or a
    # future refactor) doesn't honor that contract itself.
    try:
        architect_plan = run_architect(context)
    except Exception as exc:  # noqa: BLE001 — enrichment only, never required
        logger.info("kick/808 architect failed (%s) — composer runs without it", exc)
        architect_plan = None

    windows = _window_ranges(start, end)
    all_kicks: list[dict[str, Any]] = []
    all_bass: list[dict[str, Any]] = []
    usage_by_window: list[dict[str, Any]] = []
    for window_index, (window_start, window_end) in enumerate(windows, start=1):
        window_sections = _window_sections(
            context["sections"],
            start=window_start,
            end=window_end,
        )
        window_context = {
            **context,
            "start": window_start,
            "end": window_end,
            "bars": int(round((window_end - window_start) / 4.0)),
            "window_index": window_index,
            "window_count": len(windows),
            "sections": window_sections,
            "architect_notes": _architect_notes_for_window(
                architect_plan, [str(s.get("name")) for s in window_sections]
            ),
            "clap_times": [
                t for t in context["clap_times"] if window_start <= t < window_end
            ],
            "melody_accents": [
                t
                for t in context["melody_accents"]
                if window_start <= t < window_end
            ],
            "progression_text": _progression_text(context["roots_pc"], context["root_pc"]),
            "register_lo": "C4",
            "register_hi": "C5",
            "accent_hi": "B6",
            "allow_slides": bool(allow_slides),
        }
        prompt = _build_prompt(window_context)
        try:
            raw, usage = _call_llm(prompt)
        except _RawContentError as exc:
            # QA fallback #1: the strict-schema response didn't parse — try
            # a cheap repair pass on the raw text before giving up on the
            # whole song (see _qa_repair's own docstring).
            repaired = _qa_repair(exc.content, start=window_start, end=window_end)
            if repaired is None:
                logger.warning(
                    "kick/808 LLM window %d/%d unparsable and QA repair unavailable/failed, "
                    "rejecting entire LLM result",
                    window_index,
                    len(windows),
                )
                _cache_put(key, None)
                return None
            logger.info(
                "kick/808 LLM window %d/%d recovered via QA repair (parse failure)",
                window_index,
                len(windows),
            )
            raw, usage = repaired, exc.usage
        except Exception as exc:  # noqa: BLE001 — network/LLM must never break generation
            logger.warning(
                "kick/808 LLM window %d/%d failed, rejecting entire LLM result (%s)",
                window_index,
                len(windows),
                exc,
            )
            _cache_put(key, None)
            return None

        raw_stats = _response_stats(
            raw.get("kick"),
            raw.get("bass"),
            start=window_start,
            end=window_end,
        )
        if not _coverage_matches(raw, start=window_start, end=window_end):
            # QA fallback #2: valid JSON, but truncated short of the window
            # (e.g. finish_reason="length") — same repair pass, this time on
            # the already-parsed dict rather than raw text.
            repaired = _qa_repair(json.dumps(raw), start=window_start, end=window_end)
            if repaired is not None and _coverage_matches(repaired, start=window_start, end=window_end):
                logger.info(
                    "kick/808 LLM window %d/%d recovered via QA repair (coverage mismatch)",
                    window_index,
                    len(windows),
                )
                raw = repaired
            else:
                logger.warning(
                    "kick/808 LLM window %d/%d coverage mismatch: expected=%s..%s declared=%s "
                    "stats=%s (QA repair unavailable/failed)",
                    window_index,
                    len(windows),
                    window_start,
                    window_end,
                    raw.get("coverage"),
                    raw_stats,
                )
                _cache_put(key, None)
                return None

        kicks, bass = _sanitize(
            raw,
            start=window_start,
            end=window_end,
            root_pc=root_pc,
            scale=scale,
            roots_pc=roots_pc,
            kick_template=kick_template,
            allow_slides=allow_slides,
            bpm=bpm,
        )
        sanitized_stats = _response_stats(
            kicks,
            bass,
            start=window_start,
            end=window_end,
        )
        logger.info(
            "kick/808 LLM diagnostics window=%d/%d finish=%s raw=%s sanitized=%s usage=%s",
            window_index,
            len(windows),
            usage.get("finish_reason"),
            raw_stats,
            sanitized_stats,
            usage,
        )
        if not _call_response_valid(kicks, bass):
            logger.warning(
                "kick/808 LLM window %d/%d violates call/response roles after sanitization; rejecting entire LLM result",
                window_index,
                len(windows),
            )
            _cache_put(key, None)
            return None
        minimum_window_kicks = max(
            1,
            int(
                math.ceil(
                    (window_end - window_start)
                    * _MIN_ONSETS_PER_4_BARS
                    / 16.0
                )
            ),
        )
        if len(kicks) < minimum_window_kicks:
            logger.warning(
                "kick/808 LLM window %d/%d too sparse after sanitization: "
                "got=%d required>=%d; rejecting for dense deterministic fallback",
                window_index,
                len(windows),
                len(kicks),
                minimum_window_kicks,
            )
            _cache_put(key, None)
            return None
        all_kicks.extend(kicks)
        all_bass.extend(bass)
        usage_by_window.append(usage)

    # Final coverage gate before success/cache: every generated window must
    # remain populated after merge, so a future refactor cannot accidentally
    # drop the tail while preserving each per-window response.
    for window_index, (window_start, window_end) in enumerate(windows, start=1):
        window_kicks = [
            n for n in all_kicks if window_start <= float(n["time_step"]) < window_end
        ]
        window_bass = [
            n for n in all_bass if window_start <= float(n["time_step"]) < window_end
        ]
        if not _call_response_valid(window_kicks, window_bass):
            logger.warning(
                "kick/808 LLM merged coverage gate failed at window %d/%d; rejecting result",
                window_index,
                len(windows),
            )
            _cache_put(key, None)
            return None

    all_kicks.sort(key=lambda n: float(n["time_step"]))
    all_bass.sort(key=lambda n: float(n["time_step"]))
    result = (all_kicks, all_bass)
    _cache_put(key, result)
    logger.info(
        "kick/808 LLM complete: %d/%d windows, %d kick + %d bass notes "
        "(model=%s, usage_by_window=%s)",
        len(windows),
        len(windows),
        len(all_kicks),
        len(all_bass),
        _composer_model(),
        usage_by_window,
    )
    return result


__all__ = ["enabled", "generate", "clear_cache"]

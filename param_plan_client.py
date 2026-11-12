"""Param Plan client: asks an LLM for the humanize knobs, one request per unique MIDI.

Mode comes from ``PLG_PARAM_PLAN``:

* ``off`` (default, and anything unrecognised): does nothing - no import of the
  LLM client, no network, no cache read, no key on the pattern.
* ``shadow``: the plan is requested (or read from the cache), validated and
  written to ``pattern["plg_param_plan"]`` for the logs. Nothing reads it:
  ``humanize_pattern`` is untouched, so the sound is identical to ``off``.
* ``on``: reserved for the next step. It currently behaves like ``shadow``.

The LLM is never allowed to break a generation. Every failure (no key, network,
timeout, bad JSON, wrong schema) ends in the genre baseline and is recorded in
the telemetry as the reason.

Cache: key = hash of the ingested melody + genre + schema + prompt version +
model. The validated answer is NOT cached, the raw one is, and it is validated
again on every read, so a change of limits takes effect on cached answers too.
Only real LLM answers are cached; a failure is remembered for the current run
only (so three clone rounds do not wait for three timeouts) and retried on the
next run.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import param_plan

logger = logging.getLogger("plg.param_plan")

PROMPT_VERSION = 5  # 5: kick density + double-kick share offered; 4: snare/clap velocity no longer offered; 3: roll depth; 2: melody_density, swing 1.28
PLAN_KEY = "plg_param_plan"
# AITunnel gateway id (listed by GET /v1/models). Compared with gpt-5.6-luna-pro on three tracks it costs ~1/3
# per call and answers faster, with decisions inside the older model's own run-to-run spread.
MODEL_DEFAULT = "gpt-6-luna-pro"
TIMEOUT_MS_DEFAULT = 15_000
MAX_TOKENS_DEFAULT = 1_500
TEMPERATURE_DEFAULT = 0.2
REASONING_EFFORT_DEFAULT = "minimal"
CACHE_FILE = "param_plans.json"
CACHE_MAX_ENTRIES = 500
RAW_LOG_CHARS = 1_200

_MODES = ("off", "shadow", "on")
_lock = threading.Lock()
_memory: dict[str, dict[str, Any]] = {}  # key -> {"raw": answer, "model": ..., "ts": ...}
_failed: dict[str, str] = {}  # key -> reason, this run only
_disk_loaded = False
_last_usage: dict[str, Any] = {}  # tokens of the most recent real request


_PROMPT_HEAD = (
    "You are the groove decider of a hip-hop beat generator. A deterministic engine has already "
    "built the whole beat from the producer's own MIDI melody and applies a genre profile to the "
    "drums. You do NOT write notes. You only fine-tune {count} humanize knobs for THIS melody.\n\n"
    "The user message is a JSON description of the situation: tempo, key, melody energy (busyness, "
    "pace, onset rate, syncopation), section structure with per-section melody density, the "
    "BASELINE (what the genre profile gives today), and hard LIMITS.\n\n"
    "Think in deltas. The baseline is already good for the genre. Change a knob only when this "
    "melody gives a concrete reason, and keep changes proportionate; leave a knob out entirely "
    "when the baseline is right. Values outside the limits are clamped, so stay inside them.\n\n"
    "Knobs:\n"
)
_KNOBS = (
    "- hat_swing (float): depth of the late off-beat eighth on the hi-hats. 0 = grid-locked. "
    "Fast or very busy melodies usually want less swing, slow relaxed ones more.\n"
    "- hat_velocity {peak, vel_min, vel_max} (ints): the loudness wave of the hats. A wider "
    "vel_min..vel_max range with a moderate peak sounds more human; a busy melody wants quieter, "
    "tighter hats so it keeps space.\n"
    "- roll.prob {hot, default, quiet} (floats 0..1): probability that a backbeat gets a hat roll "
    "in hot sections (hook/chorus/drop), other sections, and quiet sections (verse/intro/outro). "
    "Dense melody sections should roll less; sparse sections can take more.\n"
    "- roll.depth {hot, default} (floats, in beats): how far before the snare a roll starts "
    "(0.25 = one quarter-beat). Longer rolls are more dramatic; keep them short where the melody "
    "is busy. \"default\" applies to non-hot sections.\n"
    "- melody_lag_ms (float): how far the melody drags behind the grid. 0 = tight. Use a few "
    "milliseconds for a lazy pocket; do not use it on a very fast tempo.\n"
    "- kick_density_multiplier (float): scales how many kick+808 pairs per bar the low end may "
    "carry. The number of pairs is already derived from this melody's own busyness; this is only "
    "the genre's seasoning on top (1.0 = neutral). Lower it where the melody is dense and needs "
    "room, raise it where the melody is sparse and the low end should drive.\n"
    "- double_kick_share (float 0..0.3): the share of kick hits that may be a quiet pickup right "
    "before the main hit. 0 = none, 0.1 = an occasional accent, more reads as a constant stutter. "
    "Keep it low on busy or fast material.\n"
)
_DRUM_KNOBS = (
    "- snare_velocity {lo, hi} (ints): the random velocity range of snare hits. A wide range is "
    "looser and more human, a narrow high range is harder and more machine-like.\n"
    "- clap_velocity {lo, hi} (ints): the same for the claps, which sit a little softer than the "
    "snare by default.\n"
)
_TAIL = (
    "\nAnswer with ONE JSON object and nothing else:\n"
    '{{"schema": 1, "hat_swing": ..., "hat_velocity": {{...}}, "roll": {{"prob": {{...}}, "depth": {{...}}}}, '
    '"melody_lag_ms": ..., "kick_density_multiplier": ..., "double_kick_share": ...{drum}, "notes": "<=200 chars: what you changed and why, or \'baseline is right\'"}}\n'
    'Only include the knobs you change. "notes" is for the log, keep it factual.'
)


def system_prompt() -> str:
    """The decider's instructions; snare/clap velocity are described only when they are offered."""
    drums = param_plan.OFFER_DRUM_VELOCITY
    return (
        _PROMPT_HEAD.format(count="nine" if drums else "seven")
        + _KNOBS + (_DRUM_KNOBS if drums else "")
        + _TAIL.format(drum=', "snare_velocity": {...}, "clap_velocity": {...}' if drums else "")
    )


SYSTEM_PROMPT = system_prompt()


# --- configuration ---------------------------------------------------------------------------


def mode() -> str:
    raw = (os.environ.get("PLG_PARAM_PLAN") or "off").strip().lower()
    return raw if raw in _MODES else "off"


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def model_name() -> str:
    return (os.environ.get("PLG_PARAM_PLAN_MODEL") or MODEL_DEFAULT).strip()


def _reasoning() -> dict[str, Any] | None:
    raw = (os.environ.get("PLG_PARAM_PLAN_REASONING") or REASONING_EFFORT_DEFAULT).strip()
    return None if raw.lower() in ("0", "false", "no", "off", "none", "") else {"effort": raw}


def cache_path() -> Path:
    override = os.environ.get("PLG_PARAM_PLAN_CACHE_DIR")
    if override:
        folder = Path(override)
    else:
        from plg_paths import user_data_dir

        folder = user_data_dir() / "param_plan"
    return folder / CACHE_FILE


# --- identity -----------------------------------------------------------------------------------


def melody_hash(pattern: dict[str, Any]) -> str:
    """Hash of the ingested melody (what the MIDI turned into), not of a path."""
    tracks = pattern.get("tracks") if isinstance(pattern.get("tracks"), dict) else {}
    notes = tracks.get("melody_lead") if isinstance(tracks.get("melody_lead"), list) else []
    rows = sorted(
        (round(float(n.get("time_step", 0) or 0), 4), str(n.get("note")), round(float(n.get("length", 0) or 0), 4))
        for n in notes if isinstance(n, dict)
    )
    return hashlib.sha256(json.dumps([round(float(pattern.get("bpm") or 0), 2), rows]).encode()).hexdigest()


def cache_key(pattern: dict[str, Any], genre: str) -> str:
    material = "|".join((
        melody_hash(pattern), genre, f"schema{param_plan.SCHEMA_VERSION}", f"prompt{PROMPT_VERSION}", model_name(),
    ))
    return hashlib.sha256(material.encode()).hexdigest()


def resolve_profile(pattern: dict[str, Any]):
    """The profile humanize_pattern will pick for this pattern (same inputs, same call)."""
    from genre_profiles import profile_for

    style = str(pattern.get("style", ""))
    prompt = str(pattern.get("user_prompt", ""))
    max_drive = bool(pattern.get("plg_clipper_mode") or pattern.get("plg_filth_mode")) or any(
        k in f"{style} {prompt}".lower() for k in ("мясо", "брутал", "max drive", "soft clip", "clipper")
    )
    variety = pattern.get("plg_variety") if isinstance(pattern.get("plg_variety"), dict) else {}
    hint = variety.get("genre") if isinstance(variety.get("genre"), str) else None
    return profile_for(style, prompt, max_drive=max_drive, genre_hint=hint)


# --- cache ------------------------------------------------------------------------------------------


def _load_disk() -> None:
    global _disk_loaded
    if _disk_loaded:
        return
    _disk_loaded = True
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key, entry in data.items():
                if isinstance(entry, dict) and "raw" in entry:
                    _memory.setdefault(key, entry)
    except (OSError, ValueError):
        pass


def _save_disk() -> None:
    try:
        from audio_features import atomic_write_text

        entries = dict(sorted(_memory.items(), key=lambda kv: kv[1].get("ts", 0))[-CACHE_MAX_ENTRIES:])
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(entries, ensure_ascii=False, indent=1))
    except Exception as exc:  # noqa: BLE001 - a cache that cannot be written is not an error
        logger.info("param plan cache not saved (%s)", exc)


def reset_memory_cache() -> None:
    """Forget everything held in memory (tests, and a fresh run)."""
    global _disk_loaded
    with _lock:
        _memory.clear()
        _failed.clear()
        _disk_loaded = False


# --- the LLM call --------------------------------------------------------------------------------------


def _ask(document: dict[str, Any]) -> dict[str, Any]:
    """One request. Separate so tests can replace it. Raises on any failure."""
    from llm_client import openrouter_json_object

    prompt = (
        "Situation (JSON):\n" + json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        + "\n\nAnswer shape:\n" + json.dumps(param_plan.response_template(), ensure_ascii=False)
    )
    _last_usage.clear()
    return openrouter_json_object(
        prompt,
        system_prompt(),
        model=model_name(),
        temperature=TEMPERATURE_DEFAULT,
        max_tokens=_int_env("PLG_PARAM_PLAN_MAX_TOKENS", MAX_TOKENS_DEFAULT),
        timeout_ms=_int_env("PLG_PARAM_PLAN_TIMEOUT_MS", TIMEOUT_MS_DEFAULT),
        reasoning=_reasoning(),
        usage_sink=_last_usage,
    )


def _telemetry(
    plan: param_plan.ValidatedPlan, *, source: str, key: str, document: dict[str, Any] | None,
    raw: Any, latency_ms: int | None, reason: str = "",
) -> dict[str, Any]:
    out = plan.telemetry()
    out.update({
        "mode": mode(), "source": source, "reason": reason, "model": model_name(),
        "prompt_version": PROMPT_VERSION, "cache_key": key[:16], "latency_ms": latency_ms,
        "baseline": (document or {}).get("baseline"),
        "usage": dict(_last_usage) if source == "llm" else None,
        "raw": json.dumps(raw, ensure_ascii=False)[:RAW_LOG_CHARS] if raw is not None else None,
    })
    return out


def plan_for(pattern: dict[str, Any]) -> dict[str, Any] | None:
    """Telemetry dict for this pattern's plan, or None when the mode is off.

    Never raises: any failure is a baseline plan with ``source`` ``fallback``.
    """
    if mode() == "off":
        return None
    profile = None
    key = ""
    document: dict[str, Any] | None = None
    try:
        profile = resolve_profile(pattern)
        baseline = param_plan.baseline_plan(profile)
        key = cache_key(pattern, profile.name)
        with _lock:
            _load_disk()
            cached = _memory.get(key)
            failed = _failed.get(key)
        if cached is not None:
            plan = param_plan.validate_plan(cached["raw"], baseline)
            logger.info("param plan: cache hit %s", key[:12])
            return _telemetry(plan, source="cache", key=key, document=None, raw=cached["raw"], latency_ms=0)
        if failed is not None:
            return _telemetry(
                param_plan.validate_plan(None, baseline), source="fallback", key=key, document=None,
                raw=None, latency_ms=0, reason=f"earlier this run: {failed}",
            )
        document = param_plan.build_plan_input(pattern, profile)
        started = time.monotonic()
        try:
            raw = _ask(document)
        except Exception as exc:  # noqa: BLE001 - network, timeout, no key, bad JSON...
            reason = f"{type(exc).__name__}: {str(exc)[:200]}"
            with _lock:
                _failed[key] = reason
            logger.warning("param plan: LLM unavailable, baseline used (%s)", reason)
            return _telemetry(
                param_plan.validate_plan(None, baseline), source="fallback", key=key, document=document,
                raw=None, latency_ms=int((time.monotonic() - started) * 1000), reason=reason,
            )
        latency = int((time.monotonic() - started) * 1000)
        plan = param_plan.validate_plan(raw, baseline)
        if plan.ok:
            with _lock:
                _memory[key] = {"raw": raw, "model": model_name(), "ts": time.time()}
                _save_disk()
        else:
            with _lock:
                _failed[key] = "answer rejected by validator"
        logger.info(
            "param plan (%s, %d ms): %s | clamped=%s dropped=%s | %s",
            model_name(), latency, plan.values, list(plan.clamped), list(plan.dropped), plan.notes,
        )
        return _telemetry(
            plan, source="llm" if plan.ok else "fallback", key=key, document=document, raw=raw,
            latency_ms=latency, reason="" if plan.ok else "answer rejected by validator",
        )
    except Exception as exc:  # noqa: BLE001 - this must never reach the generation
        logger.warning("param plan skipped (%s)", exc)
        try:
            baseline = param_plan.baseline_plan(profile) if profile is not None else {}
            fallback = param_plan.validate_plan(None, baseline) if baseline else None
        except Exception:  # noqa: BLE001
            fallback = None
        if fallback is None:
            return None
        return _telemetry(fallback, source="fallback", key=key, document=document, raw=None,
                          latency_ms=None, reason=f"{type(exc).__name__}: {str(exc)[:200]}")


def attach(pattern: dict[str, Any]) -> None:
    """Write the plan telemetry onto ``pattern`` (shadow/on). A no-op when off."""
    result = plan_for(pattern)
    if result is not None:
        pattern[PLAN_KEY] = result


__all__ = ["PLAN_KEY", "PROMPT_VERSION", "SYSTEM_PROMPT", "system_prompt", "attach", "cache_key", "mode", "plan_for", "reset_memory_cache"]

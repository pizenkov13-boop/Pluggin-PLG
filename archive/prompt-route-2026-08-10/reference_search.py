"""Real-time reference search → structured production hints.

On EVERY prompt (by default), PLG searches the web for current production guides —
typical BPM / key, drum-layout rules, FX-chain ideas — then an LLM distils that raw
text into a tiny structured hint object that drives the beat. The web informs WHAT
to make; PLG's engine math (sections, humanize, mixing, safety) makes it coherent
and FL-ready. The hints stay advisory where it matters: the engine's safety rules
(e.g. distortion caps / clean .flp in ``flp_writer``) still win, so the web makes
beats smarter but never breaks them. Set ``PLG_SEARCH_MIN_WORDS`` to gate searching
to longer prompts only (to save API quota).

Transparent + optional by design:
  * The UI shows an honest "analysing references" status (see ``create_beat``),
    never a hidden process.
  * With NO search key, offline, a short prompt, a timeout, or ANY error, this
    module returns ``None`` and generation runs exactly as before. It can never
    fail a beat.

Providers (auto-picked): a paid key wins, else **Gemini Google Search grounding**
— the budget-free path that reuses the existing Gemini key, no extra search API.

Env:
    PLG_REFERENCE_SEARCH=auto|on|off    auto = on when a usable provider exists
    PLG_SEARCH_PROVIDER=gemini|tavily|perplexity   (default auto: gemini if no paid key)
    TAVILY_API_KEY=...                  https://tavily.com (optional, paid)
    PERPLEXITY_API_KEY=...              https://perplexity.ai (optional, paid)
    PLG_SEARCH_TIMEOUT_S=8
    PLG_SEARCH_MAX_RESULTS=5
    PLG_SEARCH_MIN_WORDS=7              prompt must be at least this detailed
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.request
from typing import Any

logger = logging.getLogger("plg.reference_search")

_TAVILY_URL = "https://api.tavily.com/search"
_PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"

# Schema the extractor LLM must return — small, technical, engine-ready.
_HINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "bpm": {"type": "integer"},
        "scale": {"type": "string"},
        "drum_layout_rules": {"type": "array", "items": {"type": "string"}},
        "fx_chain": {"type": "array", "items": {"type": "string"}},
        "sound_design": {"type": "array", "items": {"type": "string"}},
    },
}

_EXTRACT_SYSTEM = (
    "You are a music-production analyst. You are given raw web text about how to "
    "produce a specific style of beat. Extract ONLY concrete technical guidance as "
    "JSON: bpm (typical integer), scale (e.g. 'natural minor', 'phrygian'), "
    "drum_layout_rules (short imperative strings), fx_chain (short strings), "
    "sound_design (short strings). Ignore opinions, ads, and filler. If a field is "
    "unknown, omit it. Keep each list to at most 6 short items."
)

_SCALES = {
    "natural minor", "minor", "harmonic minor", "phrygian", "dorian", "locrian",
    "major", "lydian", "mixolydian", "aeolian", "pentatonic minor",
}


def _env_flag(name: str, default: str = "auto") -> str:
    return os.environ.get(name, default).strip().lower()


def provider() -> str:
    raw = _env_flag("PLG_SEARCH_PROVIDER", "")
    if raw in ("tavily", "perplexity", "pplx", "gemini", "google"):
        return {"pplx": "perplexity", "google": "gemini"}.get(raw, raw)
    # Auto-pick: a dedicated paid search key wins; otherwise use Gemini's built-in
    # Google Search grounding — the budget-free path on the existing Gemini key.
    if os.environ.get("TAVILY_API_KEY"):
        return "tavily"
    if os.environ.get("PERPLEXITY_API_KEY"):
        return "perplexity"
    return "gemini"


def _provider_ready(p: str) -> bool:
    if p == "tavily":
        return bool(os.environ.get("TAVILY_API_KEY"))
    if p == "perplexity":
        return bool(os.environ.get("PERPLEXITY_API_KEY"))
    if p == "gemini":
        from llm_client import gemini_search_available

        return gemini_search_available()
    return False


def search_enabled() -> bool:
    flag = _env_flag("PLG_REFERENCE_SEARCH", "auto")
    if flag in ("0", "false", "no", "off"):
        return False
    # on / auto both require a usable provider (paid key, or a Gemini key for
    # free Google Search grounding).
    return _provider_ready(provider())


def _min_words() -> int:
    # Default 1 = search on EVERY prompt (web info always informs the beat). Raise
    # this (e.g. PLG_SEARCH_MIN_WORDS=7) only to save API quota on tag-style prompts.
    try:
        return max(1, int(os.environ.get("PLG_SEARCH_MIN_WORDS", "1")))
    except ValueError:
        return 1


def _timeout_s() -> float:
    try:
        return max(2.0, float(os.environ.get("PLG_SEARCH_TIMEOUT_S", "8")))
    except ValueError:
        return 8.0


def _max_results() -> int:
    try:
        return max(1, min(10, int(os.environ.get("PLG_SEARCH_MAX_RESULTS", "5"))))
    except ValueError:
        return 5


def is_complex_prompt(prompt: str) -> bool:
    """Whether to run the web search for this prompt. Default: any non-empty prompt
    (search ALWAYS informs the beat). Raise ``PLG_SEARCH_MIN_WORDS`` to gate it to
    only detailed prompts and save API quota."""
    words = re.findall(r"[^\s]+", prompt or "")
    return len(words) >= _min_words()


# --- search providers -------------------------------------------------------


def _http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=_timeout_s()) as resp:  # noqa: S310 - fixed API hosts
        return json.loads(resp.read().decode("utf-8"))


def _tavily_search(query: str, key: str) -> str:
    data = _http_post_json(
        _TAVILY_URL,
        {
            "api_key": key,
            "query": query,
            "search_depth": "advanced",
            "include_answer": True,
            "max_results": _max_results(),
        },
        {"Content-Type": "application/json"},
    )
    parts: list[str] = []
    if isinstance(data.get("answer"), str):
        parts.append(data["answer"])
    for item in data.get("results") or []:
        if isinstance(item, dict) and item.get("content"):
            parts.append(str(item["content"]))
    return "\n\n".join(parts)[:8000]


def _perplexity_search(query: str, key: str) -> str:
    data = _http_post_json(
        _PERPLEXITY_URL,
        {
            "model": os.environ.get("PLG_PERPLEXITY_MODEL", "sonar"),
            "messages": [
                {"role": "system", "content": "Be concise and technical."},
                {"role": "user", "content": query},
            ],
        },
        {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    choices = data.get("choices") or []
    if choices and isinstance(choices[0], dict):
        return str(((choices[0].get("message") or {}).get("content") or ""))[:8000]
    return ""


def _gemini_grounded_search(query: str) -> str:
    """Free path: Gemini answers the query grounded in Google Search."""
    from llm_client import grounded_search

    return grounded_search(query)[:8000]


def _run_search(query: str) -> str:
    p = provider()
    if p == "gemini":
        return _gemini_grounded_search(query)
    if p == "perplexity":
        key = os.environ.get("PERPLEXITY_API_KEY")
        return _perplexity_search(query, key) if key else ""
    key = os.environ.get("TAVILY_API_KEY")
    return _tavily_search(query, key) if key else ""


def _build_query(prompt: str, style: str) -> str:
    base = prompt.strip()
    if style and style.lower() not in base.lower():
        base = f"{style} {base}"
    return (
        f"How to produce a {base} beat — typical BPM, key/scale, drum pattern "
        f"rules, and FX chain. Producer guide / sound design."
    )


def search_text(prompt: str, *, style: str = "") -> str:
    """Raw web breakdown text for a prompt (the 'spy' intel the R1 Director reasons
    over). Returns "" when search is disabled / offline / on any error."""
    prompt = (prompt or "").strip()
    if not prompt or not search_enabled():
        return ""
    try:
        return _run_search(_build_query(prompt, style))
    except Exception as exc:  # noqa: BLE001 - intel is best-effort
        logger.warning("Reference intel skipped: %s", exc)
        return ""


# --- hint extraction + validation -------------------------------------------


def _clamp_bpm(value: Any) -> int | None:
    try:
        bpm = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return bpm if 60 <= bpm <= 220 else None


def _clean_list(value: Any, *, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text[:160])
        if len(out) >= limit:
            break
    return out


def _validate_hints(raw: dict[str, Any]) -> dict[str, Any]:
    hints: dict[str, Any] = {}
    bpm = _clamp_bpm(raw.get("bpm"))
    if bpm is not None:
        hints["bpm"] = bpm
    scale = str(raw.get("scale", "")).strip().lower()
    if scale:
        hints["scale"] = scale if scale in _SCALES else scale[:40]
    for key in ("drum_layout_rules", "fx_chain", "sound_design"):
        cleaned = _clean_list(raw.get(key))
        if cleaned:
            hints[key] = cleaned
    return hints


def _extract_hints(prompt: str, raw_text: str) -> dict[str, Any]:
    from llm_client import complete_json

    user_msg = (
        f"PROMPT (what the user wants):\n{prompt}\n\n"
        f"RAW WEB REFERENCE TEXT:\n{raw_text}\n\n"
        "Return the production hints JSON."
    )
    data = complete_json(user_msg, _EXTRACT_SYSTEM, _HINT_SCHEMA, temperature=0.1)
    return _validate_hints(data)


# --- cache ------------------------------------------------------------------


def _cache_dir():
    from plg_paths import user_data_dir

    path = user_data_dir() / "reference_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(prompt: str, style: str) -> str:
    digest = hashlib.sha256(f"{provider()}|{style}|{prompt}".encode("utf-8")).hexdigest()
    return digest[:24]


def _cache_get(key: str) -> dict[str, Any] | None:
    path = _cache_dir() / f"{key}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data else None
    except (OSError, json.JSONDecodeError):
        return None


def _cache_put(key: str, hints: dict[str, Any]) -> None:
    try:
        (_cache_dir() / f"{key}.json").write_text(
            json.dumps(hints, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        logger.debug("reference cache write skipped: %s", exc)


# --- public API -------------------------------------------------------------


def gather_reference_hints(prompt: str, *, style: str = "") -> dict[str, Any] | None:
    """Search the web and distil production hints for ``prompt``.

    Returns a small dict (bpm/scale/drum_layout_rules/fx_chain/sound_design) or
    ``None``. NEVER raises — disabled, offline, short prompt, or any failure all
    fall back to ``None`` so generation continues on PLG's built-in knowledge.
    """
    prompt = (prompt or "").strip()
    try:
        from symbolic_composer import local_opium_melody_mode

        if local_opium_melody_mode(prompt, style):
            logger.info("Reference search skipped — local Opium melody matrix active")
            return None
    except ImportError:
        pass
    if not prompt or not search_enabled() or not is_complex_prompt(prompt):
        return None

    cache_key = _cache_key(prompt, style)
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.info("Reference hints cache hit")
        return cached

    try:
        raw_text = _run_search(_build_query(prompt, style))
        if not raw_text.strip():
            return None
        hints = _extract_hints(prompt, raw_text)
    except Exception as exc:  # noqa: BLE001 - search must never break a beat
        logger.warning("Reference search skipped: %s", exc)
        return None

    if not hints:
        return None
    _cache_put(cache_key, hints)
    logger.info("Reference hints: bpm=%s scale=%s rules=%s",
                hints.get("bpm"), hints.get("scale"), len(hints.get("drum_layout_rules") or []))
    return hints


def reference_addendum(hints: dict[str, Any]) -> str:
    """A system-instruction block injecting the hints as ADVISORY guidance."""
    if not hints:
        return ""
    lines = [
        "REAL-TIME REFERENCE (advisory — current production guidance for this prompt;",
        "use it to inform note/BPM/key choices, but keep PLG's own song structure and",
        "never exceed PLG's mixing safety limits):",
    ]
    if hints.get("bpm"):
        lines.append(f"- Typical BPM ≈ {hints['bpm']}")
    if hints.get("scale"):
        lines.append(f"- Scale / key colour: {hints['scale']}")
    for label, key in (("Drum rules", "drum_layout_rules"),
                       ("FX chain", "fx_chain"),
                       ("Sound design", "sound_design")):
        items = hints.get(key)
        if items:
            lines.append(f"- {label}: " + "; ".join(items))
    return "\n".join(lines)

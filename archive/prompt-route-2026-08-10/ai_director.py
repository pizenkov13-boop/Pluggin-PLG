"""Stage-1 AI Director — DeepSeek-R1 reasons a loose prompt into a tight brief.

The pipeline the founder defined:

    user prompt → [R1 Director: reason + web intel → short brief]
                → [Gemini Flash: brief → notes/params JSON, fast]
                → [symbolic_composer / flp_writer: the 280 math rules]
                → .flp

R1 is the slow "thinking" director: it (optionally) reads web breakdowns and
distils the vibe into a concrete production plan — NOT code, NOT note arrays. The
fast executor (Gemini Flash, the existing llm_client default) then renders it.

Optional + bullet-proof: no ``DEEPSEEK_API_KEY`` / offline / any error → ``make_brief``
returns ``None`` and the pipeline runs exactly as before. It can never break a beat.

Env:
    PLG_USE_DIRECTOR=auto|on|off     auto = on when DEEPSEEK_API_KEY is set
    DEEPSEEK_API_KEY=...             https://platform.deepseek.com
    PLG_DIRECTOR_MODEL=deepseek-reasoner
    PLG_DIRECTOR_TIMEOUT_S=30
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

logger = logging.getLogger("plg.director")

_DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

_DIRECTOR_SYSTEM = (
    "You are a Grammy-winning executive producer — multi-platinum placements in dark trap / "
    "rage / opium / plugg and stadium rap — directing this record from the big chair, plus a "
    "deep FL Studio engineer. Given a loose user prompt (and optional raw web breakdown text), "
    "output a SHORT production BRIEF — the plan only, NEVER code or note arrays. Max ~10 short "
    "lines. Brief it like you'd brief your session team — decisive, concrete, technical, "
    "designed around a vocalist and one peak moment. Cover:\n"
    "- BPM and key/scale (dark = natural minor / phrygian) — commit to one of each.\n"
    "- 808: root register, how it walks the changes, glide/slide spots (e.g. bars 4 & 8), "
    "mono / cut-itself, and keep it ~40% level so it doesn't distort/fart.\n"
    "- Drums: the pocket and its signature bounce (kick locks with the 808), ghost notes, "
    "where hat rolls go (chorus/hook, not the verse).\n"
    "- Melody: a hummable hook with an arc (start → peak → resolve) and real movement, rests "
    "for the vocal, reverb + high-cut EQ for the underwater feel.\n"
    "- The moment: the one arrangement event that makes the beat (drum cut into a bigger drop, "
    "octave lift, stop-time before the last hook).\n"
    "- Overall vibe in one line. Translate any artist reference into these technical choices; "
    "never echo the artist name in the brief."
)


def _env_flag(name: str, default: str = "auto") -> str:
    return os.environ.get(name, default).strip().lower()


def director_enabled() -> bool:
    flag = _env_flag("PLG_USE_DIRECTOR", "auto")
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(os.environ.get("DEEPSEEK_API_KEY"))


def _model() -> str:
    return os.environ.get("PLG_DIRECTOR_MODEL", "deepseek-reasoner")


def _timeout_s() -> float:
    try:
        return max(5.0, float(os.environ.get("PLG_DIRECTOR_TIMEOUT_S", "30")))
    except ValueError:
        return 30.0


def make_brief(prompt: str, *, reference_text: str = "") -> str | None:
    """R1 turns ``prompt`` (+ optional web intel) into a concise production brief.
    Returns the brief text, or ``None`` (disabled / offline / any error)."""
    prompt = (prompt or "").strip()
    if not prompt or not director_enabled():
        return None
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None

    if reference_text.strip():
        user = (
            f"PROMPT:\n{prompt}\n\nWEB BREAKDOWN (intel — distil, don't copy):\n"
            f"{reference_text.strip()[:6000]}\n\nWrite the production brief."
        )
    else:
        user = f"PROMPT:\n{prompt}\n\nWrite the production brief."

    payload = {
        "model": _model(),
        "messages": [
            {"role": "system", "content": _DIRECTOR_SYSTEM},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "max_tokens": 800,
    }
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _DEEPSEEK_URL,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_timeout_s()) as resp:  # noqa: S310 - fixed host
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - director must never break a beat
        logger.warning("Director (R1) skipped: %s", exc)
        return None

    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        return None
    logger.info("Director brief: %s", content[:120].replace("\n", " "))
    return content[:2000]


def brief_addendum(brief: str) -> str:
    """A system-instruction block injecting the director's brief for the executor."""
    if not brief:
        return ""
    return (
        "PRODUCTION BRIEF (a senior producer's plan for THIS beat — follow it exactly "
        "when writing the MIDI/params):\n" + brief
    )

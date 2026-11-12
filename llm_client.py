"""PLG LLM providers — OpenRouter/aitunnel first, legacy providers as opt-in."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

DEFAULT_PROVIDER = "openrouter"
GEMINI_MODEL = "gemini-2.5-flash"
CLAUDE_MODEL = "claude-sonnet-4-6"
OPENROUTER_MODEL = "gpt-5.6-luna"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def _openrouter_base() -> str:
    """Base URL for the OpenAI-compatible gateway. Override to point at another
    aggregator (e.g. aitunnel: https://api.aitunnel.ru/v1)."""
    return (os.environ.get("OPENROUTER_BASE_URL") or OPENROUTER_BASE).rstrip("/")


LLM_TIMEOUT_MS = int(os.environ.get("PLG_LLM_TIMEOUT_MS", "90000"))


def _openrouter_timeout_s() -> float:
    """Gateways routing to reasoning/large-output models are slower than Gemini —
    give them headroom (default 180s, override with PLG_OPENROUTER_TIMEOUT_MS)."""
    return int(os.environ.get("PLG_OPENROUTER_TIMEOUT_MS", "180000")) / 1000.0


LLM_RETRY_ATTEMPTS = int(os.environ.get("PLG_LLM_RETRY_ATTEMPTS", "2"))
GEMINI_BUSY_RETRIES = int(os.environ.get("PLG_GEMINI_BUSY_RETRIES", "4"))
GEMINI_BUSY_WAIT_S = float(os.environ.get("PLG_GEMINI_BUSY_WAIT_S", "8"))


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|pass|token|secret|authorization|api[_-]?key|jwt)\s*[:=]\s*[^\s,;]+"
)
_WINDOWS_USER_PATH_RE = re.compile(r"(?i)\b([A-Z]:\\Users\\)[^\\\s]+")


def redact_external_ai_text(value: str) -> str:
    """Remove credentials and direct identifiers before any external AI call.

    This is applied at the provider boundary, so it covers local and cloud
    generation for every supported provider. MIDI is never sent as a file or
    note payload through this boundary.
    """
    text = str(value or "")
    text = _EMAIL_RE.sub("[redacted-email]", text)
    text = _JWT_RE.sub("[redacted-jwt]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    return _WINDOWS_USER_PATH_RE.sub(r"\1[redacted]", text)


def _external_ai_inputs(prompt: str, system_instruction: str) -> tuple[str, str]:
    """Return the only text that is allowed to leave PLG for an AI provider."""
    return redact_external_ai_text(prompt), redact_external_ai_text(system_instruction)


def get_provider() -> str:
    raw = (os.environ.get("PLG_LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if raw in ("gemini", "google"):
        return "gemini"
    if raw in ("anthropic", "claude"):
        return "anthropic"
    if raw in ("openrouter", "or"):
        return "openrouter"
    raise EnvironmentError(
        f"Unknown PLG_LLM_PROVIDER={raw!r}. Use gemini, openrouter, or anthropic."
    )


def provider_label() -> str:
    mode = (os.environ.get("PLG_LLM_PIPELINE") or "titans").strip().lower()
    if mode not in ("single", "off", "0", "false", "no", "gemini", "solo") and os.environ.get("OPENROUTER_API_KEY", "").strip():
        from llm_pipeline import ARCHITECT_MODEL_DEFAULT, COMPOSER_MODEL_DEFAULT, QA_MODEL_DEFAULT

        architect = (os.environ.get("PLG_TITAN_ARCHITECT_MODEL") or ARCHITECT_MODEL_DEFAULT).strip()
        composer = (os.environ.get("PLG_TITAN_COMPOSER_MODEL") or COMPOSER_MODEL_DEFAULT).strip()
        qa = (os.environ.get("PLG_TITAN_QA_MODEL") or QA_MODEL_DEFAULT).strip()
        if qa.lower() in ("0", "false", "no", "off", "none"):
            return f"OpenAI GPT ({architect} → {composer})"
        return f"OpenAI GPT ({architect} → {composer} → {qa})"
    if get_provider() == "gemini":
        model = _gemini_model()
        return f"Gemini ({model})"
    if get_provider() == "openrouter":
        return f"OpenRouter ({_openrouter_model()})"
    return f"Claude ({_claude_model()})"


def _gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL") or os.environ.get("PLG_GEMINI_MODEL") or GEMINI_MODEL


def _claude_model() -> str:
    return os.environ.get("PLG_CLAUDE_MODEL") or os.environ.get("ANTHROPIC_MODEL") or CLAUDE_MODEL


def _gemini_api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY required when PLG_LLM_PROVIDER=gemini")
    return api_key


def _anthropic_api_key() -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY required when PLG_LLM_PROVIDER=anthropic")
    return api_key


def _openrouter_api_key() -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError("OPENROUTER_API_KEY required when PLG_LLM_PROVIDER=openrouter")
    return api_key


def _openrouter_model() -> str:
    return os.environ.get("OPENROUTER_MODEL") or OPENROUTER_MODEL


def _is_retryable_provider_error(exc: Exception) -> bool:
    text = str(exc).upper()
    markers = (
        "503",
        "UNAVAILABLE",
        "HIGH DEMAND",
        "429",
        "RESOURCE_EXHAUSTED",
        "500",
        "INTERNAL",
        "OVERLOADED",
        "DEADLINE",
    )
    return any(marker in text for marker in markers)


def safe_error_summary(exc: BaseException) -> str:
    """Return a diagnostic category without exposing provider response text.

    Gateway exceptions can contain request fragments, credentials, or user-provided
    prompt text.  Use this in local logs and deterministic fallback metadata; the
    detailed exception must never be persisted verbatim.
    """
    text = str(exc).upper()
    if "429" in text or "RESOURCE_EXHAUSTED" in text or "QUOTA" in text:
        return "rate_limited"
    if "401" in text or "403" in text or "UNAUTHORIZED" in text or "API_KEY" in text:
        return "authentication_failed"
    if "TIMEOUT" in text or "TIMED OUT" in text or "DEADLINE" in text:
        return "timeout"
    if "503" in text or "UNAVAILABLE" in text or "OVERLOADED" in text:
        return "provider_unavailable"
    if "GETADDRINFO" in text or "NAME OR SERVICE NOT KNOWN" in text:
        return "dns_failure"
    return exc.__class__.__name__


def format_llm_error(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    upper = text.upper()

    if "503" in text or "UNAVAILABLE" in upper or "HIGH DEMAND" in upper:
        model = provider_label()
        return (
            "AI gateway is overloaded right now (503).\n\n"
            f"Model: {model}\n\n"
            "The upstream model server is busy — this is usually temporary.\n\n"
            "What to do:\n"
            "1. Wait 30–60 seconds and click CREATE BEAT again\n"
            "2. Try again in a few minutes\n"
            "3. Check OPENROUTER_BASE_URL / OPENROUTER_API_KEY if every request fails"
        )

    if "429" in text or "RESOURCE_EXHAUSTED" in upper or "QUOTA" in upper:
        model = provider_label()
        return (
            "AI gateway quota/rate limit exceeded (429).\n\n"
            f"Model: {model}\n\n"
            "What to do:\n"
            "1. Wait 1–2 minutes and try again\n"
            "2. Check your gateway balance / limits\n"
            "3. Verify OPENROUTER_API_KEY and OPENROUTER_BASE_URL"
        )

    if "getaddrinfo failed" in text.lower() or "name or service not known" in text.lower():
        return (
            "Network/DNS error — cannot reach the server.\n\n"
            "Cloud mode: check PLG_CLOUD_URL in .env and run python cloud\\run.py locally.\n"
            "Local mode: check your internet connection."
        )

    if "timeout" in text.lower() or "timed out" in text.lower():
        model = provider_label()
        return (
            "Request timed out.\n\n"
            f"Model: {model}\n\n"
            "The OpenAI GPT request did not finish before the gateway deadline. "
            "Check the internet connection and retry; the app does not switch "
            "to DeepSeek or Qwen."
        )

    if "10051" in text or "NETWORK IS UNREACHABLE" in upper or "СДЕЛАНА ПОПЫТКА" in upper:
        return (
            "Нет сети или соединение оборвалось.\n\n"
            "Проверь интернет / Wi‑Fi. Если был VPN — включи снова или выключи "
            "полностью и перезапусти PLG.\n\n"
            "Cloud mode: PLG_CLOUD_URL должен открываться в браузере."
        )

    if "LOCATION IS NOT SUPPORTED" in upper or (
        "FAILED_PRECONDITION" in upper and "LOCATION" in upper
    ):
        return (
            "AI provider недоступен в твоём регионе.\n\n"
            "Без VPN:\n"
            "1. Settings → включи Cloud mode (или PLG_CLOUD_MODE=true в .env)\n"
            "2. Войди в аккаунт PLG — AI идёт через сервер в EU\n"
            "3. Перезапусти PLG и снова «Создать бит»\n\n"
            "Со своим ключом: проверь gateway endpoint / VPN / баланс."
        )

    # A bare "INVALID" used to land here, so any engine message containing the
    # word (e.g. midi_validate's "invalid hi_hats sample") was reported as a bad
    # gateway key. Match only phrasings a gateway actually produces.
    if (
        "API_KEY" in upper
        or "401" in text
        or "403" in text
        or "INVALID API KEY" in upper
        or "INVALID_API_KEY" in upper
        or "INVALID AUTHENTICATION" in upper
        or "INCORRECT API KEY" in upper
        or "UNAUTHORIZED" in upper
    ):
        return (
            "Invalid AI gateway API key.\n\n"
            "Check OPENROUTER_API_KEY / aitunnel key and OPENROUTER_BASE_URL."
        )

    return (
        "AI gateway request failed.\n\n"
        f"Diagnostic: {safe_error_summary(exc)}\n\n"
        "Check the connection and gateway settings, then try again."
    )


def titans_enabled() -> bool:
    """3-titan pipeline is the default whenever an OpenRouter key is present.
    Disable with PLG_LLM_PIPELINE=single (or off/0/false)."""
    mode = (os.environ.get("PLG_LLM_PIPELINE") or "titans").strip().lower()
    if mode in ("single", "off", "0", "false", "no", "gemini", "solo"):
        return False
    return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())


def generate_pattern(
    prompt: str,
    system_instruction: str,
    json_schema: dict[str, Any],
) -> dict[str, Any]:
    if titans_enabled():
        try:
            from generation_variety import active_seed
            from llm_pipeline import generate_pattern_titans

            return generate_pattern_titans(
                prompt,
                system_instruction,
                json_schema,
                variety_seed=active_seed(),
            )
        except Exception as exc:
            # Do not silently downgrade to the single-stage path after spending
            # minutes in the titan path. The UI must fail fast/actionably instead
            # of hanging for another full generation.
            logging.warning("Titans pipeline failed: %s", safe_error_summary(exc))
            raise

    if get_provider() == "gemini":
        return _generate_gemini(prompt, system_instruction, json_schema)
    if get_provider() == "openrouter":
        return _generate_openrouter(prompt, system_instruction, json_schema)
    return _generate_anthropic(prompt, system_instruction, json_schema)


def gemini_search_available() -> bool:
    """True when a Gemini key exists, so we can use Google Search grounding for
    the reference booster WITHOUT a paid Tavily/Perplexity key."""
    try:
        _gemini_api_key()
        return True
    except EnvironmentError:
        return False


def grounded_search(query: str, *, temperature: float = 0.2) -> str:
    """Ask Gemini a question with built-in Google Search grounding and return the
    grounded text answer. Uses the existing Gemini key (no extra search budget).
    Raises if the SDK / key is unavailable — callers fall back to no-search.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=_gemini_api_key(),
        http_options=types.HttpOptions(timeout=LLM_TIMEOUT_MS),
    )
    response = client.models.generate_content(
        model=_gemini_model(),
        contents=query,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=temperature,
        ),
    )
    return response.text or ""


def complete_json(
    prompt: str,
    system_instruction: str,
    json_schema: dict[str, Any],
    *,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Generic structured-JSON completion (NOT beat-specific).

    Used to distil raw reference text into a small hint object. Reuses the same
    provider / key plumbing as ``generate_pattern`` but does not require a
    ``tracks`` field and runs cooler (low temperature) for faithful extraction.
    """
    prompt, system_instruction = _external_ai_inputs(prompt, system_instruction)
    if get_provider() == "gemini":
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=_gemini_api_key(),
            http_options=types.HttpOptions(
                timeout=LLM_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=LLM_RETRY_ATTEMPTS),
            ),
        )
        response = client.models.generate_content(
            model=_gemini_model(),
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_json_schema=json_schema,
                temperature=temperature,
            ),
        )
        raw_text = response.text
        if not raw_text or not raw_text.strip():
            raise RuntimeError("Empty extraction response.")
        data = json.loads(raw_text)
        if not isinstance(data, dict):
            raise RuntimeError("Extraction did not return a JSON object.")
        return data

    if get_provider() == "openrouter":
        return _openrouter_json(
            prompt,
            system_instruction,
            json_schema,
            schema_name="extract",
            temperature=temperature,
            max_tokens=max_tokens,
        )

    import anthropic

    client = anthropic.Anthropic(
        api_key=_anthropic_api_key(),
        timeout=LLM_TIMEOUT_MS / 1000.0,
        max_retries=LLM_RETRY_ATTEMPTS,
    )
    tool = {
        "name": "extract",
        "description": "Extract the requested structured fields.",
        "input_schema": json_schema,
    }
    response = client.messages.create(
        model=_claude_model(),
        max_tokens=max_tokens,
        system=system_instruction,
        messages=[{"role": "user", "content": prompt}],
        tools=[tool],
        tool_choice={"type": "tool", "name": "extract"},
        temperature=temperature,
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "extract" and isinstance(block.input, dict):
            return block.input
    raise RuntimeError("Claude did not return extract tool output.")


def _generate_gemini(
    prompt: str,
    system_instruction: str,
    json_schema: dict[str, Any],
) -> dict[str, Any]:
    from google import genai
    from google.genai import types

    prompt, system_instruction = _external_ai_inputs(prompt, system_instruction)
    model = _gemini_model()
    logging.info("Gemini (%s)...", model)

    client = genai.Client(
        api_key=_gemini_api_key(),
        http_options=types.HttpOptions(
            timeout=LLM_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=LLM_RETRY_ATTEMPTS),
        ),
    )

    last_exc: Exception | None = None
    for attempt in range(GEMINI_BUSY_RETRIES):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_json_schema=json_schema,
                    temperature=0.85,
                ),
            )

            raw_text = response.text
            if not raw_text or not raw_text.strip():
                raise RuntimeError("Gemini returned an empty response.")

            data = json.loads(raw_text)
            if not isinstance(data, dict) or "tracks" not in data:
                raise RuntimeError("Expected JSON with bpm, tracks, build_order.")
            return data
        except Exception as exc:
            last_exc = exc
            if attempt + 1 < GEMINI_BUSY_RETRIES and _is_retryable_provider_error(exc):
                wait = GEMINI_BUSY_WAIT_S * (2**attempt)
                logging.warning(
                    "Gemini busy (attempt %s/%s), retry in %.0fs: %s",
                    attempt + 1,
                    GEMINI_BUSY_RETRIES,
                    wait,
                    exc,
                )
                time.sleep(wait)
                continue
            raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Gemini request failed.")


def _openrouter_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_openrouter_api_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://example.com",
        "X-Title": "PLG",
    }


# Keywords that some gateways (e.g. aitunnel -> Anthropic structured output)
# reject in a strict response_format schema. We clamp these ranges downstream
# anyway, so strip them for cross-gateway compatibility.
_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
        "minItems", "maxItems", "uniqueItems", "minLength", "maxLength",
        "pattern", "format", "minProperties", "maxProperties",
    }
)


def _strip_schema_constraints(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _strip_schema_constraints(value)
            for key, value in node.items()
            if key not in _UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(node, list):
        return [_strip_schema_constraints(value) for value in node]
    return node


def _openrouter_json(
    prompt: str,
    system_instruction: str,
    json_schema: dict[str, Any],
    *,
    schema_name: str,
    temperature: float,
    max_tokens: int,
    require_tracks: bool = False,
    model: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    import httpx

    from local_only import require_network

    require_network("an AI request")
    prompt, system_instruction = _external_ai_inputs(prompt, system_instruction)
    model = model or _openrouter_model()
    json_schema = _strip_schema_constraints(json_schema)
    logging.info("OpenRouter (%s)...", model)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
        },
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    timeout_s = (timeout_ms / 1000.0) if timeout_ms else _openrouter_timeout_s()
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

    data = resp.json()
    raw_text = data["choices"][0]["message"]["content"]
    if not raw_text or not str(raw_text).strip():
        raise RuntimeError("OpenRouter returned an empty response.")
    parsed = json.loads(raw_text)
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenRouter did not return a JSON object.")
    if require_tracks and "tracks" not in parsed:
        raise RuntimeError("Expected JSON with bpm, tracks, build_order.")
    return parsed


def openrouter_schema_json(
    prompt: str,
    system_instruction: str,
    json_schema: dict[str, Any],
    *,
    model: str,
    schema_name: str = "beat_pattern",
    temperature: float = 0.85,
    max_tokens: int = 8192,
    require_tracks: bool = True,
) -> dict[str, Any]:
    """Strict-schema OpenRouter completion against an explicit model (titans stage 2)."""
    return _openrouter_json(
        prompt,
        system_instruction,
        json_schema,
        schema_name=schema_name,
        temperature=temperature,
        max_tokens=max_tokens,
        require_tracks=require_tracks,
        model=model,
    )


def openrouter_json_object(
    prompt: str,
    system_instruction: str,
    *,
    model: str,
    temperature: float = 0.4,
    max_tokens: int = 2048,
    timeout_ms: int | None = None,
    reasoning: dict[str, Any] | None = None,
    usage_sink: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Loose JSON-object OpenRouter completion (titans stage 1 skeleton).

    No strict schema — reasoning models brainstorm a structural brief. Tolerates
    code fences / leading think-text by extracting the first JSON object.
    ``timeout_ms`` overrides the global gateway timeout per stage (an architect
    that hangs 3 minutes before the fallback is its own UX bug). ``reasoning``
    (e.g. ``{"effort": "minimal"}``) is AITUNNEL/OpenRouter's field for
    capping a reasoning model's thinking-token budget separately from
    ``max_tokens`` — see bass808_llm.REASONING_EFFORT_DEFAULT's docstring
    for why the kick/808 pipeline passes this. Omitted (None) by default so
    every other caller of this shared function is unaffected.
    """
    import re

    import httpx

    from local_only import require_network

    require_network("an AI request")
    prompt, system_instruction = _external_ai_inputs(prompt, system_instruction)
    logging.info("OpenRouter json (%s)...", model)
    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": prompt},
    ]
    timeout_s = (timeout_ms / 1000.0) if timeout_ms else _openrouter_timeout_s()

    def _post(use_response_format: bool) -> "httpx.Response":
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if reasoning:
            payload["reasoning"] = reasoning
        if use_response_format:
            payload["response_format"] = {"type": "json_object"}
        with httpx.Client(timeout=timeout_s) as client:
            return client.post(
                f"{_openrouter_base()}/chat/completions",
                headers=_openrouter_headers(),
                json=payload,
            )

    resp = _post(True)
    # Some gateways/models reject response_format=json_object — retry plain once.
    if resp.status_code == 400 and "response_format" in resp.text:
        resp = _post(False)
    if resp.status_code >= 400:
        detail = resp.text[:400]
        try:
            detail = resp.json().get("error", {}).get("message") or detail
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"OpenRouter {resp.status_code}: {detail}")

    body = resp.json()
    choice = body["choices"][0]
    if usage_sink is not None:
        # Optional read-out for callers that track cost; None (the default) = unchanged behaviour.
        usage_sink.update(body.get("usage") or {})
        usage_sink["finish_reason"] = choice.get("finish_reason")
    if choice.get("finish_reason") == "length":
        logging.warning(
            "OpenRouter (%s) hit max_tokens=%s — output truncated, JSON may be incomplete.",
            model,
            max_tokens,
        )
    content = choice["message"]["content"]
    text = (content or "").strip()
    if not text:
        raise RuntimeError("OpenRouter architect returned empty content.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise RuntimeError("Architect did not return a JSON object.")
    return data


def _generate_openrouter(
    prompt: str,
    system_instruction: str,
    json_schema: dict[str, Any],
) -> dict[str, Any]:
    return _openrouter_json(
        prompt,
        system_instruction,
        json_schema,
        schema_name="beat_pattern",
        temperature=0.85,
        max_tokens=8192,
        require_tracks=True,
    )


def _generate_anthropic(
    prompt: str,
    system_instruction: str,
    json_schema: dict[str, Any],
) -> dict[str, Any]:
    import anthropic

    prompt, system_instruction = _external_ai_inputs(prompt, system_instruction)
    model = _claude_model()
    logging.info("Claude (%s)...", model)

    client = anthropic.Anthropic(
        api_key=_anthropic_api_key(),
        timeout=LLM_TIMEOUT_MS / 1000.0,
        max_retries=LLM_RETRY_ATTEMPTS,
    )
    tool = {
        "name": "beat_pattern",
        "description": "Beat pattern JSON for FL Studio File Bridge",
        "input_schema": json_schema,
    }

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        system=system_instruction,
        messages=[{"role": "user", "content": prompt}],
        tools=[tool],
        tool_choice={"type": "tool", "name": "beat_pattern"},
        temperature=0.85,
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "beat_pattern":
            data = block.input
            if isinstance(data, dict) and "tracks" in data:
                return data
            raise RuntimeError("Claude tool output missing tracks.")

    raise RuntimeError("Claude did not return beat_pattern tool output.")

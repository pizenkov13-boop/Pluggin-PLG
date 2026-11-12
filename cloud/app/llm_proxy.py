"""Server-side LLM generation — keys never leave the server."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend_core import build_system_instruction, generate_pattern, load_user_profile  # noqa: E402
from cloud.app.config import EST_COST_BASE, EST_COST_PREMIUM  # noqa: E402
from cloud.app.pattern_validate import validate_pattern  # noqa: E402
from cloud.app.privacy import exception_kind  # noqa: E402
from sample_catalog import format_catalog_for_prompt  # noqa: E402

logger = logging.getLogger("plg.cloud.llm")


def _rub_to_usd(value: Any) -> float | None:
    try:
        rub = float(value)
    except (TypeError, ValueError):
        return None
    try:
        rub_per_usd = max(1.0, float(os.environ.get("PLG_RUB_PER_USD", "90")))
    except ValueError:
        rub_per_usd = 90.0
    return round(rub / rub_per_usd, 6)


def _empty_catalog() -> dict[str, Any]:
    return {
        "root": "",
        "total": 0,
        "audio_total": 0,
        "audio": {},
        "midi": [],
        "presets": [],
        "projects": [],
        "banks": [],
        "plugins": [],
    }


def _seed_pattern(prepared: dict[str, Any] | None, beat_brief: Any) -> dict[str, Any]:
    """Minimal deterministic pattern for when the LLM step is unavailable.

    Satisfies ``validate_pattern`` (bpm / tracks / build_order). The client's
    ``run_pipeline_from_pattern`` -> ``_humanize_and_ingest`` is MIDI-first and
    fills kick / 808 / hats / clap / snare / melody from the user's MIDI and
    the genre, identical to a local-mode run — the LLM never composed notes on
    the active route anyway.
    """
    bpm = 140.0
    scale = "C minor"
    style = "trap"
    if isinstance(beat_brief, dict):
        try:
            b = float(beat_brief.get("bpm"))
            if 40.0 <= b <= 220.0:
                bpm = b
        except (TypeError, ValueError):
            pass
        scale = str(beat_brief.get("scale") or scale)
        style = str(beat_brief.get("style") or beat_brief.get("genre") or style)
    if style == "trap" and isinstance(prepared, dict):
        tags = prepared.get("plg_style_tags") or []
        if tags:
            style = str(tags[0]) or style
    return {
        "bpm": bpm,
        "scale": scale,
        "style": style,
        "tracks": {},
        "build_order": [],
        "plg_llm_unavailable": True,
    }


def _resolve_provider(plan: str) -> None:
    # Cloud generation must use the same titans (AITUNNEL) gateway path for
    # every plan — gpt-5.6-luna-pro (architect) / gpt-5.6-luna (composer/QA),
    # see llm_pipeline.py. Do not silently downgrade/fallback to
    # Gemini/Anthropic: that makes quality and analytics impossible to reason
    # about.
    _ = plan
    os.environ["PLG_LLM_PROVIDER"] = os.environ.get("PLG_LLM_PROVIDER") or "openrouter"
    os.environ["PLG_LLM_PIPELINE"] = os.environ.get("PLG_LLM_PIPELINE") or "titans"


def generate_beat_pattern(
    prompt: str,
    *,
    plan: str = "base",
    catalog: dict[str, Any] | None = None,
    user_profile: dict[str, Any] | None = None,
    locale: str | None = None,
    smart_plan: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (pattern, meta) where meta has model + estimated cost."""
    _resolve_provider(plan)

    cat = catalog if isinstance(catalog, dict) and catalog.get("root") is not None else _empty_catalog()
    if cat.get("total", 0) == 0 and not cat.get("audio_total"):
        cat = _empty_catalog()

    profile = user_profile if isinstance(user_profile, dict) else load_user_profile()
    system_instruction = build_system_instruction(profile, cat)

    from prompt_locale import apply_prompt_metadata, prepare_prompt_for_llm

    prepared = prepare_prompt_for_llm(prompt, locale)

    from beat_planner import (
        apply_brief_metadata,
        brief_to_llm_addendum,
        plan_beat_brief,
        planner_enabled,
        should_use_smart_plan,
    )

    llm_prompt = prepared["llm_prompt"]
    beat_brief = None
    if planner_enabled() and should_use_smart_plan(prepared["user_prompt"], smart_plan):
        try:
            beat_brief = plan_beat_brief(prepared["user_prompt"], locale=locale)
            llm_prompt = prepared["llm_prompt"] + "\n\n" + brief_to_llm_addendum(beat_brief)
            logger.info("Smart plan applied for cloud generate")
        except Exception as exc:
            logger.warning("Smart plan failed in cloud type=%s", exception_kind(exc))

    # The LLM pattern step is best-effort. If the provider is down / out of
    # budget / returns junk, generation must still complete: the client's
    # run_pipeline_from_pattern -> _humanize_and_ingest is MIDI-first and
    # deterministic — it fills every lane from the user's MIDI + genre, exactly
    # like a local-mode run — so a minimal seed pattern is enough to hand it.
    llm_ok = True
    try:
        raw = generate_pattern(llm_prompt, system_instruction)
        pattern = validate_pattern(raw)
    except Exception as exc:  # noqa: BLE001 — any LLM/validation failure degrades, never blocks
        llm_ok = False
        logger.warning(
            "LLM pattern step unavailable (%s) — deterministic seed pattern",
            exception_kind(exc),
        )
        pattern = _seed_pattern(prepared, beat_brief)

    pattern = apply_prompt_metadata(pattern, prepared)
    if beat_brief:
        pattern = apply_brief_metadata(pattern, beat_brief)
    from llm_client import provider_label

    pipeline_meta = pattern.get("plg_pipeline") if isinstance(pattern.get("plg_pipeline"), dict) else {}
    estimated_cost_rub = pipeline_meta.get("estimated_cost_rub")
    cost = _rub_to_usd(estimated_cost_rub)
    if cost is None:
        cost = EST_COST_PREMIUM if plan == "premium" else EST_COST_BASE
    meta = {
        "model": provider_label() if llm_ok else "deterministic-fallback",
        "plan": plan,
        "cost_usd": cost if llm_ok else 0.0,
        "cost_rub": estimated_cost_rub,
        "prompt_chars": len(prompt),
        "llm_fallback": not llm_ok,
    }
    return pattern, meta

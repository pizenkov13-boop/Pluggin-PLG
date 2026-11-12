"""Listenability regressions — the exact bugs that made the 2026-07-02 beat unlistenable."""

from __future__ import annotations

import random
from typing import Any

import pytest

from pattern_utils import parse_note_name


# --- 1. Opium hook must not be a two-pitch see-saw ------------------------------


@pytest.mark.parametrize("variant", range(8))
def test_opium_hook_has_pitch_variety(variant: int):
    from symbolic_composer import compose_opium_melody_hook

    key_cfg = {"root_pc": 5, "scale": "phrygian", "label": "F phrygian"}
    notes, _ = compose_opium_melody_hook(
        random.Random(variant * 17 + 1), key_cfg=key_cfg, melody_variant=variant
    )
    pitches = {parse_note_name(str(n["note"])) for n in notes}
    assert len(pitches) >= 3, f"variant {variant}: see-saw hook {sorted(pitches)}"


def test_opium_walk_bans_ping_pong():
    from symbolic_composer import _pick_strict_semitone_step, _scale_midi_pool

    pool = _scale_midi_pool(5, "phrygian", 64, 79)
    rng = random.Random(3)
    walked = [65]
    for _ in range(12):
        walked.append(_pick_strict_semitone_step(walked[-1], pool, rng, history=walked))
    # no A-B-A-B anywhere in the walk
    for i in range(len(walked) - 3):
        a, b, c, d = walked[i : i + 4]
        assert not (a == c and b == d and a != b), f"see-saw at {i}: {walked}"


# --- 2. 6dB hat rule keeps the groove contour -----------------------------------


def test_hat_rule_scales_instead_of_flattening():
    from beat_humanize import apply_six_db_hat_rule

    hats = [
        {"time_step": i * 0.5, "note": "F#5", "length": 0.2, "velocity": v}
        for i, v in enumerate((110, 70, 95, 60, 105, 80))
    ]
    ref = [{"time_step": 2.0, "note": "D1", "length": 0.3, "velocity": 104}]
    out = apply_six_db_hat_rule(hats, ref)
    vels = [n["velocity"] for n in out]
    assert len(set(vels)) >= 4, f"hat dynamics flattened: {vels}"
    assert max(vels) <= int(104 * 0.502) + 1  # still under the -6dB ceiling
    order_in = [sorted(range(len(hats)), key=lambda i: hats[i]["velocity"])]
    order_out = [sorted(range(len(vels)), key=lambda i: vels[i])]
    assert order_in == order_out  # relative accents survive


# --- 3. 808 velocity follows kick + section energy ------------------------------


def test_sub_808_not_a_flat_127_wall():
    from arranger import arrange_song
    from beat_humanize import humanize_pattern

    pattern = {
        "bpm": 144,
        "style": "dark trap",
        "user_prompt": "evil dark trap",
        "plg_variety": {"seed": 9, "genre": "trap", "key": "F", "scale": "minor"},
        "tracks": {
            "kick": [{"time_step": t, "note": "C1", "length": 0.3, "velocity": 112} for t in (0.0, 2.75, 4.0, 6.5)],
            "snare": [{"time_step": t, "note": "D1", "length": 0.3, "velocity": 105} for t in (2.0, 6.0)],
            "hi_hats": [{"time_step": i * 0.5, "note": "F#5", "length": 0.2, "velocity": 90} for i in range(16)],
            "melody_lead": [
                {"time_step": float(i), "note": n, "length": 1.0, "velocity": 96}
                for i, n in enumerate(("F5", "G#5", "C6", "A#5", "F5", "C6", "G#5", "F5"))
            ],
        },
    }
    arrange_song(pattern)
    out = humanize_pattern(pattern)
    subs = out["tracks"]["sub_808"]
    assert subs
    vels = [int(n["velocity"]) for n in subs]
    assert len(set(vels)) >= 3, f"808 is a flat wall: {set(vels)}"
    # verse 808 sits below chorus 808 (section energy applied)
    arrangement = out["plg_arrangement"]
    verse = next(s for s in arrangement if s["name"] == "verse")
    chorus = next(s for s in arrangement if s["name"] == "chorus")

    def _avg(section):
        lo, hi = section["start_bar"] * 4.0, (section["start_bar"] + section["bars"]) * 4.0
        in_sec = [int(n["velocity"]) for n in subs if lo <= float(n["time_step"]) < hi]
        return sum(in_sec) / max(1, len(in_sec))

    assert _avg(verse) < _avg(chorus)


# --- 4. Explicit user BPM / key are sacred ---------------------------------------


def test_explicit_bpm_wins_over_variety():
    from generation_variety import build_context

    ctx = build_context("dark trap 150 bpm evil pluck")
    assert ctx["bpm"] == 150
    assert ctx["bpm_explicit"] is True


@pytest.mark.parametrize(
    ("prompt", "bpm"),
    [("бит 92 бпм", 92), ("bpm: 141 hard", 141), ("slow 68 bpm", 68)],
)
def test_explicit_bpm_formats(prompt: str, bpm: int):
    from generation_variety import explicit_bpm

    assert explicit_bpm(prompt) == bpm


def test_explicit_key_wins_over_variety():
    from generation_variety import build_context

    ctx = build_context("dark beat in F# minor")
    assert ctx["key"] == "F#"
    assert ctx["key_explicit"] is True

    ctx_ru = build_context("бит в c мажор")
    assert ctx_ru["key"] == "C"
    assert ctx_ru["scale"] == "major"


def test_no_false_bpm_from_other_numbers():
    from generation_variety import explicit_bpm

    assert explicit_bpm("трэп с 808 и бэллами") is None
    assert explicit_bpm("beat like 21 savage") is None


# --- 5. Architect failure now runs the composer solo -----------------------------


def test_architect_failure_falls_to_composer_not_code(monkeypatch):
    import llm_pipeline

    def architect_boom(*a, **k):
        raise RuntimeError("read timeout")

    composed = {
        "bpm": 150,
        "style": "dark trap",
        "tracks": {"kick": [{"time_step": 0, "note": "C5", "length": 0.25, "velocity": 110}]},
    }
    calls: list[str] = []

    def fake_composer(prompt, system, schema, skeleton, **k):
        calls.append("composer")
        assert isinstance(skeleton, dict) and skeleton.get("bpm")  # stock plan passed
        return dict(composed)

    monkeypatch.setattr(llm_pipeline, "run_architect", architect_boom)
    monkeypatch.setattr(llm_pipeline, "run_composer", fake_composer)
    monkeypatch.setattr(llm_pipeline, "run_qa", lambda *a, **k: a[3] if len(a) > 3 else k.get("pattern"))

    pattern = llm_pipeline.generate_pattern_titans("dark trap 150 bpm", "ctx", {})
    assert calls == ["composer"]
    assert "plg_pipeline_fallback" not in pattern  # not the emergency skeleton


def test_architect_retries_on_fast_fallback_model(monkeypatch):
    import llm_pipeline

    attempts: list[str] = []

    def fake_json(prompt, system, *, model, **k):
        attempts.append(model)
        if model == "deepseek-v4-pro":
            raise RuntimeError("The read operation timed out")
        return {"bpm": 144, "style": "dark trap", "sections": []}

    import llm_client

    monkeypatch.setattr(llm_client, "openrouter_json_object", fake_json)
    monkeypatch.setenv("PLG_TITAN_ARCHITECT_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("PLG_TITAN_COMPOSER_MODEL", "qwen3.7-plus")

    skeleton = llm_pipeline.run_architect("dark trap", "ctx")
    assert attempts == ["deepseek-v4-pro", "qwen3.7-plus"]
    assert skeleton["bpm"] == 144


# --- 6. snare_layer no longer doubles fills --------------------------------------


def test_snare_layer_skips_fill_crescendo():
    from arranger import arrange_song
    from beat_humanize import humanize_pattern

    pattern = {
        "bpm": 142,
        "style": "dark trap",
        "user_prompt": "dark trap fills",
        "plg_variety": {"seed": 4, "genre": "trap", "key": "F", "scale": "minor"},
        "tracks": {
            "kick": [{"time_step": t, "note": "C1", "length": 0.3, "velocity": 112} for t in (0.0, 4.0)],
            "snare": [{"time_step": t, "note": "D1", "length": 0.3, "velocity": 105} for t in (2.0, 6.0)],
            "hi_hats": [{"time_step": i * 0.5, "note": "F#5", "length": 0.2, "velocity": 90} for i in range(16)],
            "melody_lead": [
                {"time_step": float(i), "note": n, "length": 1.0, "velocity": 96}
                for i, n in enumerate(("F5", "G#5", "C6", "A#5"))
            ],
        },
    }
    arrange_song(pattern)
    out = humanize_pattern(pattern)
    layer = out["tracks"].get("snare_layer") or []
    assert layer
    assert not any(n.get("fill") or n.get("ghost") for n in layer)

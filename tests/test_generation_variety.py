"""Generation variety — beats must not clone on regenerate."""

from __future__ import annotations

import dataclasses

from generation_variety import (
    build_context,
    llm_addendum,
    new_variety_seed,
)
from genre_profiles import PROFILES
from llm_pipeline import deterministic_pattern_from_skeleton


def test_regenerate_seeds_differ():
    a = new_variety_seed("dark trap", regenerate=False)
    b = new_variety_seed("dark trap", regenerate=True)
    assert a != b


def test_llm_addendum_mentions_variety():
    ctx = build_context("dark trap 140", regenerate=True)
    text = llm_addendum(ctx)
    assert "UNIQUE" in text or "ANTI-CLONE" in text
    assert str(ctx["bpm"]) in text


def test_deterministic_patterns_differ_by_seed():
    skeleton = {"bpm": 140, "key": "F", "scale": "minor", "style": "dark trap"}
    a = deterministic_pattern_from_skeleton("dark trap", skeleton, reason="test", variety_seed=11)
    b = deterministic_pattern_from_skeleton("dark trap", skeleton, reason="test", variety_seed=99)
    kicks_a = [n["time_step"] for n in a["tracks"]["kick"]]
    kicks_b = [n["time_step"] for n in b["tracks"]["kick"]]
    assert kicks_a != kicks_b or a["tracks"]["melody_lead"] != b["tracks"]["melody_lead"]


# --- hook_entry (cold-open vs buildup, audit follow-up 2026-07) ---------


def test_ctx_always_carries_a_valid_hook_entry():
    for _ in range(10):
        ctx = build_context("dark trap 140")
        assert ctx["hook_entry"] in ("cold_open", "buildup")


def test_hook_entry_forced_cold_open_when_chance_is_zero(monkeypatch):
    # rng.random() is in [0.0, 1.0) — "< 0.0" can never be true, so chance=0.0
    # guarantees cold_open deterministically, regardless of the (time-seeded)
    # rng draw. No flakiness: this isn't a statistical sample.
    forced = dataclasses.replace(PROFILES["trap"], hook_buildup_chance=0.0)
    monkeypatch.setitem(PROFILES, "trap", forced)
    for _ in range(5):
        ctx = build_context("dark trap 140", genre_hint="trap")
        assert ctx["hook_entry"] == "cold_open"


def test_hook_entry_forced_buildup_when_chance_is_one(monkeypatch):
    # rng.random() < 1.0 is always true (the range is strictly < 1.0) —
    # chance=1.0 guarantees buildup deterministically, same reasoning as above.
    forced = dataclasses.replace(PROFILES["trap"], hook_buildup_chance=1.0)
    monkeypatch.setitem(PROFILES, "trap", forced)
    for _ in range(5):
        ctx = build_context("dark trap 140", genre_hint="trap")
        assert ctx["hook_entry"] == "buildup"


def test_aggressive_instant_hit_genres_lean_cold_open():
    for name in ("rage", "opium", "phonk", "underground", "hyperpop"):
        assert PROFILES[name].hook_buildup_chance < 0.4, name


def test_floating_atmospheric_genres_lean_buildup():
    for name in ("toliver", "kanye", "minimal_trap"):
        assert PROFILES[name].hook_buildup_chance > 0.4, name

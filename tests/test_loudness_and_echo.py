"""Loud master, audible 808, printed melody echo — the 'тихий 808 / пианино' fixes."""

from __future__ import annotations

from typing import Any

import pytest

from beat_humanize import apply_melody_echo
from mix_gain_staging import (
    DEFAULT_MASTER_FADER_DB,
    DEFAULT_SUB_INSERT_DB,
    MASTER_FADER_MAX_DB,
    MASTER_FADER_MIN_DB,
    apply_mix_db,
)


def test_master_is_loud_into_the_clipper():
    # the old -11 dB "safety" made every beat a whisper; the soft clipper on
    # the master catches peaks, so -3 dB is the loud-but-safe default.
    assert DEFAULT_MASTER_FADER_DB == pytest.approx(-3.0)
    assert DEFAULT_SUB_INSERT_DB == pytest.approx(0.0)


def test_architect_master_suggestion_clamped_to_window():
    pattern: dict[str, Any] = {"mix_db": {"master": -14.0}}
    apply_mix_db(pattern)
    assert pattern["plg_producer_meta"]["master_fader_db"] == MASTER_FADER_MIN_DB

    hot: dict[str, Any] = {"mix_db": {"master": 0.0}}
    apply_mix_db(hot)
    assert hot["plg_producer_meta"]["master_fader_db"] == MASTER_FADER_MAX_DB


def test_808_chain_attenuation_is_audible():
    from flp_writer import _DEFAULT_CHANNEL_VOL

    # bus + master path into the Soft Clipper is unchanged.
    total = 0.0 + DEFAULT_SUB_INSERT_DB + DEFAULT_MASTER_FADER_DB
    assert total == pytest.approx(-3.0)
    # 2026-07 producer-law correction: the 808 channel is NOT at unity with
    # the kick. Kick peaks and drives the clipper; the 808 carries mass ~3 dB
    # under it, or its long slabs swallow the punch.
    assert _DEFAULT_CHANNEL_VOL["sub_808"] == pytest.approx(7079, abs=5)
    assert _DEFAULT_CHANNEL_VOL["kick"] > _DEFAULT_CHANNEL_VOL["sub_808"]


# --- printed melody echo ---------------------------------------------------------


def _hook() -> list[dict[str, Any]]:
    return [
        {"time_step": float(bar * 4), "note": n, "length": 2.0, "velocity": 108}
        for bar, n in enumerate(("F5", "G#5", "F5", "A#5"))
    ]


def test_sparse_hook_gets_dotted_eighth_echoes():
    out = apply_melody_echo(_hook())
    echoes = [n for n in out if n.get("echo")]
    assert echoes, "sparse hook must answer itself"
    originals = [n for n in out if not n.get("echo")]
    assert len(originals) == 4
    first = originals[0]
    first_echoes = [e for e in echoes if 0 < float(e["time_step"]) - float(first["time_step"]) <= 1.6]
    assert len(first_echoes) == 2
    assert float(first_echoes[0]["time_step"]) == pytest.approx(0.75)
    assert float(first_echoes[1]["time_step"]) == pytest.approx(1.5)
    # decaying repeats, never louder than the source
    assert first["velocity"] > first_echoes[0]["velocity"] > first_echoes[1]["velocity"]


def test_echoes_never_collide_with_real_notes():
    dense = _hook() + [{"time_step": 0.75, "note": "C6", "length": 0.5, "velocity": 100}]
    out = apply_melody_echo(dense)
    steps = [round(float(n["time_step"]), 3) for n in out]
    assert len(steps) == len(set(steps)), f"collision in {steps}"


def test_quiet_ghost_notes_do_not_echo():
    ghosts = [{"time_step": 0.0, "note": "F5", "length": 0.5, "velocity": 40}]
    out = apply_melody_echo(ghosts)
    assert not any(n.get("echo") for n in out)


def test_humanize_applies_echo_for_sparse_opium_hook():
    from beat_humanize import humanize_pattern

    pattern = {
        "bpm": 150,
        "style": "opium",
        "user_prompt": "opium dark bells",
        "plg_variety": {"seed": 5, "genre": "opium", "key": "F", "scale": "phrygian"},
        "tracks": {
            "kick": [{"time_step": t, "note": "C1", "length": 0.3, "velocity": 112} for t in (0.0, 2.5, 4.0, 6.5)],
            "snare": [{"time_step": t, "note": "D1", "length": 0.3, "velocity": 105} for t in (2.0, 6.0)],
            "hi_hats": [{"time_step": i * 0.5, "note": "F#5", "length": 0.2, "velocity": 82} for i in range(16)],
            "melody_lead": [],
        },
    }
    out = humanize_pattern(pattern)
    melody = out["tracks"]["melody_lead"]
    assert melody
    assert any(n.get("echo") for n in melody), "sparse opium hook should carry printed delay"


def test_dense_melody_left_alone():
    from beat_humanize import humanize_pattern

    dense_notes = [
        {"time_step": i * 0.5, "note": ("F5", "G#5", "C6")[i % 3], "length": 0.4, "velocity": 100}
        for i in range(64)  # 8 notes/bar over 4 bars — busy line
    ]
    pattern = {
        "bpm": 150,
        "style": "dark trap",
        "user_prompt": "busy melodic trap",
        "plg_midi_melody": True,  # intentional user-MIDI line — repair engines keep it
        "plg_variety": {"seed": 6, "genre": "trap", "key": "F", "scale": "minor"},
        "tracks": {
            "kick": [{"time_step": t, "note": "C1", "length": 0.3, "velocity": 112} for t in (0.0, 4.0)],
            "snare": [{"time_step": t, "note": "D1", "length": 0.3, "velocity": 105} for t in (2.0, 6.0)],
            "hi_hats": [{"time_step": i * 0.5, "note": "F#5", "length": 0.2, "velocity": 82} for i in range(16)],
            "melody_lead": dense_notes,
        },
    }
    out = humanize_pattern(pattern)
    assert not any(n.get("echo") for n in out["tracks"]["melody_lead"])

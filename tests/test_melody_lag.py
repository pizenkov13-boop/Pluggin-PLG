"""Melody groove-lag + chord voicing spread (genre-driven humanize additions)."""

from __future__ import annotations

import random

from beat_humanize import (
    apply_chord_voicing_spread,
    apply_melody_groove_lag,
    ms_to_beats,
)
from genre_profiles import PROFILES, profile_for


def test_groove_lag_drags_melody_late():
    rng = random.Random(7)
    notes = [{"time_step": 1.0, "note": "A5", "length": 0.25, "velocity": 100}]
    out = apply_melody_groove_lag(notes, bpm=160, ms=12.0, rng=rng)
    shift = out[0]["time_step"] - 1.0
    # 12 ms ±2 ms jitter at 160 BPM, in beats.
    assert ms_to_beats(10.0, 160) <= shift <= ms_to_beats(14.0, 160)
    assert out[0]["groove_lag_ms"] > 0


def test_groove_lag_zero_is_noop():
    rng = random.Random(1)
    notes = [{"time_step": 2.0, "note": "C5"}]
    out = apply_melody_groove_lag(notes, bpm=140, ms=0.0, rng=rng)
    assert out[0]["time_step"] == 2.0
    assert "groove_lag_ms" not in out[0]


def test_voicing_spread_stays_in_register():
    rng = random.Random(3)
    notes = [{"time_step": float(i), "note": "C5"} for i in range(40)]  # midi 60
    out = apply_chord_voicing_spread(notes, rng, chance=1.0, register=(60, 83))
    for n in out:
        # Only +12 (->C6/72) is in range; -12 (->48) is rejected, stays C5.
        assert n["note"] in ("C5", "C6")
    assert any(n.get("voicing_inverted") for n in out)  # some inversions happened


def test_voicing_spread_zero_is_noop():
    rng = random.Random(9)
    notes = [{"time_step": 0.0, "note": "E5"}]
    out = apply_chord_voicing_spread(notes, rng, chance=0.0)
    assert out[0]["note"] == "E5"
    assert "voicing_inverted" not in out[0]


def test_profiles_lag_wiring():
    # Values may come from genre_profiles.json overlay; assert router wiring.
    trap = PROFILES["trap"]
    rage = PROFILES["rage"]
    routed_trap = profile_for(style="", prompt="just make a beat")
    routed_rage = profile_for(prompt="rage dark trap")
    assert routed_trap.melody_lag_ms == trap.melody_lag_ms
    assert routed_rage.melody_lag_ms == rage.melody_lag_ms
    assert routed_rage.voicing_spread == rage.voicing_spread

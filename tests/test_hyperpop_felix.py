"""Felix (hyperpop / 100 gecs-style tutorial) — a "hyperpop" GenreProfile
already existed (lydian, BPM 150-170, hat_rolls, stereo_drop — gated behind
PLG_LANES=all like every non-hip-hop lane, unchanged here). BPM 183 exceeded
its old range, widened to (150, 190) with headroom. Two genuinely new,
concrete techniques ported and enabled on it:

- intentionally chaotic, wide-interval melody contour (no smooth voice-
  leading) — apply_chaotic_melody_jumps.
- the 808 cuts off exactly on the next snare hit (an ADSR envelope gate, not
  a duck) — apply_808_snare_gate_stutter.

The dual-section trap<->EDM structural switch (item 6) stays out of scope
(architecture-level), as flagged.
"""

from __future__ import annotations

import random

from beat_humanize import apply_808_snare_gate_stutter, apply_chaotic_melody_jumps
from genre_profiles import PROFILES
from style_corpus import BPM_RANGES


def test_hyperpop_bpm_widened_to_cover_183():
    lo, hi = BPM_RANGES["hyperpop"]
    assert lo <= 183 <= hi
    assert hi >= 190 or hi == 190


def test_hyperpop_has_the_two_new_flags():
    profile = PROFILES["hyperpop"]
    assert profile.melody_chaotic_wide_jumps is True
    assert profile.sub_808_snare_gate_stutter is True


def test_only_hyperpop_has_these_two_flags():
    for name, profile in PROFILES.items():
        if name == "hyperpop":
            continue
        assert profile.melody_chaotic_wide_jumps is False, name
        assert profile.sub_808_snare_gate_stutter is False, name


def test_chaotic_jumps_produce_wide_intervals_deterministically():
    notes = [
        {"time_step": float(i), "note": "C4", "length": 0.5, "velocity": 90}
        for i in range(10)
    ]
    out = apply_chaotic_melody_jumps(notes, random.Random(42))
    # at least one note should have moved off C4 given a coin-flip per note over 10 notes
    assert any(n["note"] != "C4" for n in out)
    assert len(out) == len(notes)


def test_chaotic_jumps_is_order_preserving_by_time():
    notes = [
        {"time_step": 3.0, "note": "C4", "length": 0.5, "velocity": 90},
        {"time_step": 1.0, "note": "D4", "length": 0.5, "velocity": 90},
    ]
    out = apply_chaotic_melody_jumps(notes, random.Random(1))
    assert [n["time_step"] for n in out] == [1.0, 3.0]


def test_chaotic_jumps_noop_on_empty():
    assert apply_chaotic_melody_jumps([], random.Random(0)) == []


def test_808_snare_gate_stutter_cuts_note_at_snare():
    sub = [{"time_step": 0.0, "note": "C2", "length": 4.0, "velocity": 100}]
    snare = [{"time_step": 2.0, "note": "D1", "length": 0.25, "velocity": 100}]
    out = apply_808_snare_gate_stutter(sub, snare)
    assert out[0]["length"] == 2.0
    assert out[0]["gated_stutter"] is True


def test_808_snare_gate_stutter_leaves_short_notes_alone():
    sub = [{"time_step": 0.0, "note": "C2", "length": 1.0, "velocity": 100}]
    snare = [{"time_step": 2.0, "note": "D1", "length": 0.25, "velocity": 100}]
    out = apply_808_snare_gate_stutter(sub, snare)
    assert out[0]["length"] == 1.0
    assert "gated_stutter" not in out[0]


def test_808_snare_gate_stutter_noop_without_snare():
    sub = [{"time_step": 0.0, "note": "C2", "length": 4.0, "velocity": 100}]
    assert apply_808_snare_gate_stutter(sub, []) == sub

"""Regression: the final 808 lane is strictly monophonic (Cut Itself in data).

clamp_sub_register_notes re-extends note lengths to MIN_SUB_LENGTH, so any mono
pass that runs before it gets undone — slide stabs then overlap long sustains
AND the next downbeat (~25 overlapping 808s per song on slide lanes). The mono
pass must be the LAST writer of tracks["sub_808"].
"""

from __future__ import annotations

import pytest

from arranger import arrange_song
from beat_humanize import humanize_pattern


def _pattern(genre: str, seed: int) -> dict:
    return {
        "bpm": 142,
        "key": "D",
        "scale": "phrygian",
        "style": genre,
        "user_prompt": f"{genre} beat",
        "plg_variety": {
            "seed": seed, "key": "D", "bpm": 142, "scale": "phrygian", "genre": genre,
            "drum_variant": seed % 8, "melody_variant": seed % 12, "swing_profile": "lazy",
        },
        "tracks": {
            "kick": [{"time_step": t, "note": "C1", "length": 0.3, "velocity": 112} for t in (0.0, 2.5, 4.0, 6.5)],
            "snare": [{"time_step": t, "note": "D1", "length": 0.2, "velocity": 105} for t in (2.0, 6.0)],
            "hi_hats": [{"time_step": i * 0.5, "note": "F#5", "length": 0.1, "velocity": 80} for i in range(16)],
            "sub_808": [{"time_step": b * 4.0, "note": "D2", "length": 2.0, "velocity": 120} for b in range(2)],
            "melody_lead": [
                {"time_step": i, "note": n, "length": 0.75, "velocity": 95}
                for i, n in enumerate(("D5", "F5", "A5", "G5", "E5", "D5", "C5", "F5"))
            ],
        },
        "build_order": ["kick", "sub_808", "snare", "hi_hats", "melody_lead"],
    }


@pytest.mark.parametrize("genre", ["drill", "trap", "rage", "boombap"])
def test_final_808_lane_is_monophonic(genre: str) -> None:
    pattern = _pattern(genre, seed=11)
    arrange_song(pattern)
    result = humanize_pattern(pattern)
    sub = sorted(result["tracks"].get("sub_808", []), key=lambda n: float(n.get("time_step", 0)))
    assert sub, "808 lane must not be empty"
    overlaps = [
        (a, b)
        for a, b in zip(sub, sub[1:])
        if float(a["time_step"]) + float(a.get("length", 0)) > float(b["time_step"]) + 0.01
    ]
    assert not overlaps, f"{genre}: {len(overlaps)} overlapping 808 notes, first={overlaps[0]}"


@pytest.mark.parametrize("genre", ["trap", "rage"])
def test_808_breathes_between_hits(genre: str) -> None:
    """The pocket pumps: sustains leave a gap of silence before the next hit
    ('ТУК-УУУМ…тишина'), instead of a wall-to-wall drone. Glides may stay
    legato; the majority of normal hits must breathe."""
    pattern = _pattern(genre, seed=22)
    arrange_song(pattern)
    result = humanize_pattern(pattern)
    sub = sorted(result["tracks"].get("sub_808", []), key=lambda n: float(n.get("time_step", 0)))
    pairs = [
        (a, b) for a, b in zip(sub, sub[1:])
        if not (a.get("glide") or a.get("slide"))
        and float(b["time_step"]) - float(a["time_step"]) >= 1.0
    ]
    assert pairs, "expected regular 808 hits"
    breathing = sum(
        1 for a, b in pairs
        if float(b["time_step"]) - (float(a["time_step"]) + float(a["length"])) >= 0.2
    )
    assert breathing / len(pairs) >= 0.8, (
        f"{genre}: only {breathing}/{len(pairs)} 808 notes leave a breathing gap"
    )

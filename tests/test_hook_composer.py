"""Hook composer — melodies follow the changes; beat switch — the song evolves."""

from __future__ import annotations

import random
from typing import Any

import pytest

from hook_composer import (
    HOOK_HI,
    HOOK_LO,
    compose_progression_hook,
    ensure_progression_melody_if_needed,
)
from music_theory import SCALES
from pattern_utils import parse_note_name


@pytest.fixture(autouse=True)
def _all_lanes(monkeypatch):
    # These exercise pop/non-trap lanes (full router). The shipping hip-hop
    # focus gate is covered by tests/test_lane_focus.py.
    monkeypatch.setenv("PLG_LANES", "all")

# F natural minor: i–VI–III–VII over 8 bars (F, Db, Ab, Eb)
_ROOT = 5
_SCALE = "natural_minor"
_ROOTS = [5, 5, 1, 1, 8, 8, 3, 3]
_TONES = [
    [5, 8, 0], [5, 8, 0],
    [1, 5, 8], [1, 5, 8],
    [8, 0, 3], [8, 0, 3],
    [3, 7, 10], [3, 7, 10],
]


def _hook(genre: str = "pop", seed: int = 7) -> list[dict[str, Any]]:
    return compose_progression_hook(
        random.Random(seed),
        root_pc=_ROOT,
        scale=_SCALE,
        roots_pc=_ROOTS,
        chord_tones_pc=_TONES,
        genre=genre,
        bars=8,
    )


def test_hook_stays_in_register_and_scale():
    scale_pcs = {(_ROOT + i) % 12 for i in SCALES[_SCALE]}
    notes = _hook()
    assert len(notes) >= 16
    for note in notes:
        midi = parse_note_name(str(note["note"]))
        assert HOOK_LO <= midi <= HOOK_HI
        assert midi % 12 in scale_pcs


def test_hook_follows_the_changes():
    notes = _hook()
    for bar in range(8):
        in_bar = [n for n in notes if bar * 4 <= float(n["time_step"]) < (bar + 1) * 4]
        assert in_bar, f"bar {bar} empty"
        pcs = {parse_note_name(str(n["note"])) % 12 for n in in_bar}
        chord = set(_TONES[bar])
        assert pcs & chord, f"bar {bar}: no chord tone from {chord} in {pcs}"


def test_phrase_end_lands_on_bar_root():
    notes = _hook()
    bar3 = [n for n in notes if 12 <= float(n["time_step"]) < 16]
    last = max(bar3, key=lambda n: float(n["time_step"]))
    assert parse_note_name(str(last["note"])) % 12 == _ROOTS[3]
    assert float(last["length"]) >= 1.0


def test_hook_deterministic_per_seed():
    assert _hook(seed=42) == _hook(seed=42)
    assert _hook(seed=42) != _hook(seed=43)


def _pattern(genre_prompt: str, melody: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "bpm": 120,
        "style": genre_prompt,
        "user_prompt": genre_prompt,
        "plg_progression": {
            "key": "F",
            "scale": _SCALE,
            "roots_pc": _ROOTS,
            "chord_tones_pc": _TONES,
        },
        "tracks": {"melody_lead": melody, "kick": []},
    }


def test_weak_melody_replaced_for_pop():
    pattern = _pattern("pop anthem", [{"time_step": 0, "note": "C5", "length": 0.5, "velocity": 90}])
    assert ensure_progression_melody_if_needed(pattern, rng=random.Random(1))
    assert pattern["plg_progression_hook"] is True
    # short source pattern → at least a 4-bar hook, ~3+ notes per bar
    assert len(pattern["tracks"]["melody_lead"]) >= 10


def test_strong_melody_untouched():
    strong = [
        {"time_step": float(i), "note": name, "length": 0.75, "velocity": 96}
        for i, name in enumerate(("F5", "G#5", "C6", "D#5", "F5", "C6", "G#5", "F5"))
    ]
    pattern = _pattern("pop anthem", list(strong))
    assert not ensure_progression_melody_if_needed(pattern, rng=random.Random(1))
    assert pattern["tracks"]["melody_lead"] == strong


def test_trap_family_left_to_opium_engine():
    pattern = _pattern("dark trap", [])
    assert not ensure_progression_melody_if_needed(pattern, rng=random.Random(1))


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("PLG_HOOK_COMPOSER", "off")
    pattern = _pattern("pop anthem", [])
    assert not ensure_progression_melody_if_needed(pattern, rng=random.Random(1))


# --- Beat switch ---------------------------------------------------------------


def _core_pattern() -> dict[str, Any]:
    return {
        "bpm": 140,
        "style": "pop",
        "user_prompt": "pop anthem vibe",
        "plg_variety": {"seed": 11, "genre": "pop", "key": "F", "scale": "major"},
        "tracks": {
            "kick": [{"time_step": t, "note": "C1", "length": 0.3, "velocity": 110} for t in (0.0, 2.5)],
            "snare": [{"time_step": t, "note": "D1", "length": 0.3, "velocity": 105} for t in (1.0, 3.0)],
            "hi_hats": [{"time_step": i * 0.5, "note": "F#1", "length": 0.2, "velocity": 90} for i in range(8)],
            "melody_lead": [
                {"time_step": float(i), "note": n, "length": 0.75, "velocity": 96}
                for i, n in enumerate(("F5", "A5", "C6", "A5"))
            ],
            "sub_808": [],
        },
    }


def test_arranger_plans_beat_switch():
    from arranger import arrange_song

    pattern = _core_pattern()
    arrange_song(pattern)
    arrangement = pattern["plg_arrangement"]
    finals = [s for s in arrangement if s.get("final_chorus")]
    assert len(finals) == 1
    final = finals[0]
    assert final["name"] == "chorus"
    # cut bar = the bar right before the final chorus
    assert pattern["plg_beat_switch"]["cut_bar"] == final["start_bar"] - 1
    # occurrences tracked
    verse_occ = [s["occurrence"] for s in arrangement if s["name"] == "verse"]
    assert verse_occ == [1, 2]


def test_second_verse_rides_offbeat_hats():
    from arranger import arrange_song

    pattern = _core_pattern()
    arrange_song(pattern)
    arrangement = pattern["plg_arrangement"]
    verse2 = next(s for s in arrangement if s["name"] == "verse" and s["occurrence"] == 2)
    start = verse2["start_bar"] * 4.0
    end = start + verse2["bars"] * 4.0
    hats = [
        n for n in pattern["tracks"]["hi_hats"]
        if start <= float(n["time_step"]) < end and n.get("steady_hat")
    ]
    assert hats
    assert all(abs(float(n["time_step"]) % 1.0 - 0.5) < 1e-6 for n in hats)


def test_final_chorus_gets_octave_lift():
    from arranger import arrange_song

    pattern = _core_pattern()
    arrange_song(pattern)
    lifted = [n for n in pattern["tracks"]["melody_lead"] if n.get("switch_octave")]
    assert lifted
    final = next(s for s in pattern["plg_arrangement"] if s.get("final_chorus"))
    start = final["start_bar"] * 4.0
    assert all(float(n["time_step"]) >= start for n in lifted)


def test_humanize_cuts_drums_before_final_chorus_but_sub_carries():
    from arranger import arrange_song
    from beat_humanize import humanize_pattern

    pattern = _core_pattern()
    arrange_song(pattern)
    out = humanize_pattern(pattern)
    cut_bar = out["plg_beat_switch"]["cut_bar"]
    lo, hi = cut_bar * 4.0, cut_bar * 4.0 + 4.0
    for lane in ("kick", "snare", "clap", "hi_hats", "snare_layer"):
        hits = [n for n in out["tracks"].get(lane, []) if lo <= float(n["time_step"]) < hi]
        assert not hits, f"{lane} not cut in bar {cut_bar}"
    # the 808 sustains straight through the gap
    subs = out["tracks"].get("sub_808", [])
    assert any(
        float(n["time_step"]) < lo and float(n["time_step"]) + float(n["length"]) > lo + 1.0
        for n in subs
    )


def test_beat_switch_kill_switch(monkeypatch):
    from arranger import arrange_song

    monkeypatch.setenv("PLG_BEAT_SWITCH", "off")
    pattern = _core_pattern()
    arrange_song(pattern)
    assert "plg_beat_switch" not in pattern
    assert not any(n.get("switch_octave") for n in pattern["tracks"]["melody_lead"])

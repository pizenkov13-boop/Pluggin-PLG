"""The hat line depends on the generation's variety seed (hihat_rolls.vary_hats).

Five beats on one MIDI shared ~91 % of their hat onsets because the base line is one
hard-wired straight-eighth skeleton. ``vary_hats`` re-voices the finished line per 2-bar
block (ghost sixteenths, a weak eighth played a sixteenth early, accent shifts) without
leaving straight eighths, and without touching the low end.
"""

from __future__ import annotations

import copy
import random

import pytest

import hihat_rolls
from hihat_rolls import GHOST_VEL, build_steady_eighths, vary_hats
from midi_lowend_director import rhythmic_anchors

BARS = 32
ARRANGEMENT = [
    {"name": "intro", "start_bar": 0, "bars": 8},
    {"name": "verse", "start_bar": 8, "bars": 8},
    {"name": "hook", "start_bar": 16, "bars": 8},
    {"name": "outro", "start_bar": 24, "bars": 8},
]


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv("PLG_HAT_VARIATION", raising=False)


def _line():
    return build_steady_eighths(BARS)


def _pattern(seed):
    return {"plg_variety": {"seed": seed}, "plg_arrangement": ARRANGEMENT}


def _times(notes):
    return [round(n["time_step"], 4) for n in notes]


def test_the_same_seed_gives_the_same_line_and_seeds_differ():
    a = vary_hats(_line(), _pattern(11))
    b = vary_hats(_line(), _pattern(11))
    c = vary_hats(_line(), _pattern(12))
    assert _times(a) == _times(b) and [n["velocity"] for n in a] == [n["velocity"] for n in b]
    assert _times(a) != _times(c)


def test_without_a_seed_or_with_the_switch_off_the_line_is_untouched(monkeypatch):
    line = _line()
    assert vary_hats(line, {"plg_arrangement": ARRANGEMENT}) is line  # no seed anywhere

    monkeypatch.setenv("PLG_HAT_VARIATION", "off")
    assert vary_hats(line, _pattern(11)) is line


def test_it_does_not_touch_the_global_random_stream_or_the_input_line():
    line = _line()
    before = copy.deepcopy(line)
    state = random.getstate()
    vary_hats(line, _pattern(5))
    assert random.getstate() == state
    assert line == before


@pytest.mark.parametrize("seed", range(25))
def test_it_stays_straight_eighths(seed):
    out = vary_hats(_line(), _pattern(seed))
    by_bar: dict[int, list[dict]] = {}
    for note in out:
        by_bar.setdefault(int(note["time_step"] // 4.0), []).append(note)

    for bar in range(BARS):
        steady = [n for n in by_bar[bar] if not n.get("hat_ghost")]
        slots = {int(round((n["time_step"] % 4.0) * 2)) % 8 for n in steady}
        assert {0, 4} <= slots, f"bar {bar}: beat one or the backbeat eighth was dropped"
        assert len(steady) >= 5, f"bar {bar}: fewer than five eighths left"
        assert len(by_bar[bar]) >= 8, f"bar {bar}: a bar lost notes (a shift keeps the count)"

    ghosts = [n for n in out if n.get("hat_ghost")]
    for note in ghosts:
        assert abs((note["time_step"] % 0.5) - 0.25) < 1e-3, "a ghost sits on the sixteenth between eighths"
    times = sorted(_times(out))
    assert all(b - a >= 0.2 for a, b in zip(times, times[1:])), "two hats stacked on each other"


def test_ghosts_are_quiet_and_most_of_the_line_is_still_on_the_eighth_grid():
    out = vary_hats(_line(), _pattern(3))
    pure = [n for n in out if n.get("hat_ghost") and n["velocity"] < 50]
    assert pure and all(GHOST_VEL[0] <= n["velocity"] <= GHOST_VEL[1] for n in pure)
    on_grid = sum(1 for n in out if abs((n["time_step"] % 0.5)) < 1e-3)
    assert on_grid / len(out) > 0.75


def test_the_hot_section_changes_more_than_the_straight_line_over_many_seeds():
    changed = 0
    for seed in range(40):
        out = vary_hats(_line(), _pattern(seed))
        changed += sum(1 for n in out if n.get("hat_ghost") and 64.0 <= n["time_step"] < 96.0)
    assert changed > 40  # the hook (bars 16-23) is where ghosts live


def test_only_straight_eighth_notes_are_re_voiced_rolls_and_user_notes_are_left_alone():
    line = _line()
    roll = {"time_step": 33.0, "note": "C5", "length": 0.05, "velocity": 90, "hat_roll": True}
    other = {"time_step": 34.0, "note": "C5", "length": 0.1, "velocity": 70}  # no hat_steady flag
    out = vary_hats(line + [roll, other], _pattern(9))
    assert roll in out and other in out


def test_the_low_end_does_not_read_a_ghost_as_a_roll():
    eighths = [
        {"time_step": 0.0, "note": "C5", "velocity": 70},
        {"time_step": 0.5, "note": "C5", "velocity": 70},
        {"time_step": 1.0, "note": "C5", "velocity": 70},
    ]
    # 0.12 beats after an eighth: closer than the roll gap (0.19), so an unflagged note IS a roll
    ghost = {"time_step": 0.12, "note": "C5", "velocity": 40, "hat_ghost": True}
    assert rhythmic_anchors(hats=eighths)["roll_times"] == []
    assert rhythmic_anchors(hats=eighths + [dict(ghost)])["roll_times"] == [], "a ghost is air, not a roll"
    without_flag = dict(ghost)
    without_flag.pop("hat_ghost")
    assert rhythmic_anchors(hats=eighths + [without_flag])["roll_times"], "an unflagged note that close still is one"


def test_the_trap_hat_builder_itself_is_unchanged_by_the_seed(monkeypatch):
    """With the hat library off the variation is a separate last step: the base line must not move.
    (With the library on the base line is picked per seed on purpose: see tests/test_hat_patterns.py.)"""
    from hihat_rolls import apply_trap_hi_hats

    monkeypatch.setenv("PLG_HAT_LIBRARY", "off")

    def base(seed):
        pattern = {"plg_total_bars": 8, "tracks": {"snare": []}, **_pattern(seed)}
        return apply_trap_hi_hats(pattern, random.Random(1))

    assert [(n["time_step"], n["velocity"]) for n in base(1)] == [(n["time_step"], n["velocity"]) for n in base(2)]

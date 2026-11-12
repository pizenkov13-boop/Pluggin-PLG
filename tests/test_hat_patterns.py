"""The hat lane starts from a library of base lines, and the snare steps out of some bars by section.

Five beats on one MIDI shared ~70 % of their hat places (one skeleton: eighths, a hole under the backbeat, 1/32 rolls before
it) and the snare sat on beat 3 in 52-56 of 72-76 bars. ``hat_patterns`` picks a base line per SECTION from the variety seed
and the melody's busyness; ``midi_phrase_drums._gate_snare`` leaves the snare out of some bars by the kind of section.
"""

from __future__ import annotations

import random

import pytest

import hat_patterns
from hat_patterns import build_line, library
from hihat_rolls import apply_markov_hat_dynamics, base_hat_line, build_steady_eighths, vary_hats
from midi_phrase_drums import _BACKBEAT_STACK_CLAP, _BACKBEAT_STACK_FULL, _BACKBEAT_STACK_PAIR, _gate_snare

ARRANGEMENT = [
    {"name": "intro", "start_bar": 0, "bars": 8},
    {"name": "hook", "start_bar": 8, "bars": 16},
    {"name": "verse", "start_bar": 24, "bars": 16},
    {"name": "drop", "start_bar": 40, "bars": 16},
    {"name": "outro", "start_bar": 56, "bars": 8},
]
BARS = 64


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv("PLG_HAT_LIBRARY", raising=False)
    monkeypatch.delenv("PLG_SNARE_SECTIONS", raising=False)


def _pattern(seed=7, **extra):
    return {"plg_variety": {"seed": seed}, "plg_arrangement": ARRANGEMENT, **extra}


def _per_bar(notes):
    counts = [0] * BARS
    for note in notes:
        counts[int(note["time_step"] // 4)] += 1
    return counts


# --- the library ------------------------------------------------------------------------------------------------


@pytest.mark.skip(reason="requires third-party assets not included")
def test_the_library_holds_the_packs_and_our_own_families():
    cells = library()
    assert len([c for c in cells if c["source"] == "pack"]) >= 40
    assert {c["id"] for c in cells if c["source"] == "own"} >= {"own:straight", "own:half_time", "own:offbeat", "own:sixteenth_open", "own:skip_triplet"}
    for cell in cells:
        assert cell["bars"] in (1, 2, 4, 8) and cell["hits"]
        assert all(0.0 <= t < cell["bars"] * 4 for t, _kind in cell["hits"]), cell["id"]
    kinds = {kind for c in cells for _t, kind in c["hits"]}
    assert kinds == {"eighth", "orn", "open"}


@pytest.mark.skip(reason="requires third-party assets not included")
def test_the_packs_rhythm_is_stored_without_pitch_or_velocity():
    import json

    from plg_paths import bundle_dir

    data = json.loads((bundle_dir() / "assets" / "hat_patterns.json").read_text(encoding="utf-8"))
    assert data["unit"] == 48 and all(set(cell) == {"id", "bars", "hits"} for cell in data["cells"])
    assert all(isinstance(u, int) for cell in data["cells"] for u in cell["hits"])


# --- the line ---------------------------------------------------------------------------------------------------


def test_the_same_seed_gives_the_same_line_and_seeds_differ():
    a, b, c = build_line(_pattern(11), BARS), build_line(_pattern(11), BARS), build_line(_pattern(12), BARS)
    key = lambda notes: [(n["time_step"], n["velocity"], n.get("hat_cell")) for n in notes]  # noqa: E731
    assert key(a) == key(b) and key(a) != key(c)


@pytest.mark.skip(reason="requires third-party assets not included")
def test_ten_seeds_give_many_different_base_lines():
    firsts = {tuple(n["time_step"] for n in build_line(_pattern(seed), BARS)[:60]) for seed in range(10)}
    cells = {n["hat_cell"] for seed in range(10) for n in build_line(_pattern(seed), BARS)}
    assert len(firsts) >= 8 and len(cells) >= 12


def test_no_seed_or_the_switch_off_keeps_straight_eighths(monkeypatch):
    assert build_line({"plg_arrangement": ARRANGEMENT}, BARS) is None
    assert [n["time_step"] for n in base_hat_line({"plg_arrangement": ARRANGEMENT}, 8)] == [n["time_step"] for n in build_steady_eighths(8)]
    monkeypatch.setenv("PLG_HAT_LIBRARY", "off")
    assert build_line(_pattern(), BARS) is None
    assert not any(n.get("hat_cell") for n in base_hat_line(_pattern(), 8))


def test_it_does_not_touch_the_global_random_stream():
    state = random.getstate()
    build_line(_pattern(5), BARS)
    assert random.getstate() == state


@pytest.mark.skip(reason="requires third-party assets not included")
def test_eighths_are_steady_and_ornaments_are_quiet_ghosts():
    notes = build_line(_pattern(3), BARS)
    for note in notes:
        on_eighth = abs(note["time_step"] * 2 - round(note["time_step"] * 2)) < 1e-6
        if on_eighth:
            assert note.get("hat_steady") and not note.get("hat_ghost")
        else:
            assert note.get("hat_ghost") and not note.get("hat_steady") and note["velocity"] <= 48 and note["length"] <= 0.05
        assert note["hat_cell"] in {c["id"] for c in library()}


@pytest.mark.skip(reason="requires third-party assets not included")
def test_it_is_calm_by_section_ornaments_are_capped_and_an_intro_is_sparse():
    totals, intros = [], []
    for seed in range(8):
        notes = build_line(_pattern(seed), BARS)
        orn = [0] * BARS
        for note in notes:
            if note.get("hat_ghost"):
                orn[int(note["time_step"] // 4)] += 1
        assert max(orn[0:8]) == 0 and max(orn[56:64]) == 0  # intro and outro: none
        assert max(orn[24:40]) <= 1  # verse
        assert max(orn[8:24] + orn[40:56]) <= 2  # hook and drop
        per_bar = _per_bar(notes)
        intros.append(sum(per_bar[0:8]) / 8)
        totals.append(sum(per_bar) / BARS)
    # straight eighths are 8 a bar (a straight cell may come up for one seed); the later thinning and density stages go lower still
    assert sum(totals) / len(totals) < 7.6
    assert sum(intros) / len(intros) <= 4.5  # an intro is quieter than the straight line by itself


def test_a_busy_melody_gets_a_calmer_line():
    calm = sum(len(build_line(_pattern(s), BARS)) for s in range(6))
    busy = sum(len(build_line(_pattern(s, plg_melody_energy={"busyness": 0.8, "pace": "racing"}), BARS)) for s in range(6))
    assert busy < calm


def test_no_arrangement_still_gives_a_line():
    notes = build_line({"plg_variety": {"seed": 1}}, 16)
    assert notes and max(n["time_step"] for n in notes) < 64


# --- how the rest of the hat chain treats it -----------------------------------------------------------------------


def test_vary_hats_leaves_a_library_line_alone():
    line = build_line(_pattern(9), BARS)
    assert vary_hats(line, _pattern(9)) is line


def test_the_dynamics_pass_keeps_a_ghost_quiet():
    line = build_line(_pattern(4), BARS)
    ghosts = {n["time_step"]: n["velocity"] for n in line if n.get("hat_ghost")}
    assert ghosts
    out = apply_markov_hat_dynamics(line, random.Random(1))
    assert {n["time_step"]: n["velocity"] for n in out if n.get("hat_ghost")} == ghosts


def test_the_low_end_does_not_read_a_ghost_as_a_roll():
    from midi_lowend_director import rhythmic_anchors

    line = build_line(_pattern(6), BARS)
    assert not rhythmic_anchors(hats=[n for n in line if not n.get("hat_ghost")])["roll_times"]


def test_the_final_hat_audit_asks_a_library_line_for_a_clock_not_for_busy_melody_density():
    from beat_humanize import HAT_CELL_BAR_MINIMUM, _audit_hat_symbiosis

    def data(flag):
        hats = [
            {"time_step": bar * 4 + 0.5 * i, "note": "C5", "length": 0.08, "velocity": 70, **({"hat_cell": "x"} if flag else {})}
            for bar in range(0, 40)
            for i in range(HAT_CELL_BAR_MINIMUM)
        ]
        return {
            "plg_midi_melody": True,
            "plg_melody_energy": {"pace": "racing", "busyness": 0.9, "attack": "sharp"},
            "plg_arrangement": [{"name": "hook", "start_bar": 0, "bars": 16}, {"name": "verse", "start_bar": 16, "bars": 16}],
            "tracks": {"hi_hats": hats},
        }

    assert _audit_hat_symbiosis(data(True))["passed"] is True
    assert _audit_hat_symbiosis(data(False))["passed"] is False  # the old busy-melody rule is unchanged for other lines


# --- the snare ---------------------------------------------------------------------------------------------------


def _figure(stack=_BACKBEAT_STACK_FULL, tail=None):
    return {"bar": 0, "stack": stack, "pickup": False, "tail": tail, "skip": False}


def _snare_bars(section, size, seed, stack=_BACKBEAT_STACK_PAIR):
    row = {"section": section}
    return [i for i in range(size) if "snare" in _gate_snare(_figure(stack), row, i, size, seed)["stack"]]


def test_the_snare_leaves_an_intro_and_most_of_an_outro():
    assert _snare_bars("intro", 8, 5) == [7]
    assert _snare_bars("outro", 8, 5) == [0, 1]


def test_a_verse_plays_the_snare_every_second_bar_and_the_seed_moves_which():
    a, b = _snare_bars("verse", 16, 4), _snare_bars("verse", 16, 5)
    assert len(a) == len(b) == 8 and not set(a) & set(b)
    assert all(y - x == 2 for x, y in zip(a, a[1:]))


def test_a_hook_or_drop_keeps_three_bars_of_four_and_never_drops_the_fourth():
    for seed in range(12):
        for section in ("hook", "drop", "chorus"):
            bars = _snare_bars(section, 16, seed)
            assert len(bars) == 12 and all(i % 4 != 3 or i in bars for i in range(16))
            assert {3, 7, 11, 15} <= set(bars)
    assert {tuple(_snare_bars("hook", 16, s)) for s in range(12)} != {tuple(_snare_bars("hook", 16, 0))}


def test_a_bar_without_the_snare_keeps_its_clap_and_loses_the_layer_and_the_snare_tail():
    gated = _gate_snare(_figure(_BACKBEAT_STACK_FULL, tail="snare"), {"section": "verse"}, 1, 16, 0)
    assert gated["stack"] == _BACKBEAT_STACK_CLAP and gated["tail"] is None
    kept = _gate_snare(_figure(_BACKBEAT_STACK_FULL, tail="snare"), {"section": "verse"}, 0, 16, 0)
    assert kept["stack"] == _BACKBEAT_STACK_FULL and kept["tail"] == "snare"


def test_no_seed_or_the_switch_off_changes_nothing(monkeypatch):
    figure = _figure(_BACKBEAT_STACK_PAIR)
    assert _gate_snare(figure, {"section": "intro"}, 0, 8, None) is figure
    monkeypatch.setenv("PLG_SNARE_SECTIONS", "off")
    assert _gate_snare(figure, {"section": "intro"}, 0, 8, 3) is figure


def test_a_clap_only_bar_is_left_as_it_is():
    figure = _figure(_BACKBEAT_STACK_CLAP)
    assert _gate_snare(figure, {"section": "intro"}, 0, 8, 3) is figure


def test_the_build_ships_the_pack_rhythms():
    """The exe built without the file silently had only our five base lines (found on the first tester build with the library)."""
    from pathlib import Path

    spec = (Path(__file__).resolve().parent.parent / "PLG.spec").read_text(encoding="utf-8")
    assert '"hat_patterns.json"' in spec

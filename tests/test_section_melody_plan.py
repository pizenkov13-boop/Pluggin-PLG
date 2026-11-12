"""The section melody plan: one owner for "how much of the melody plays here".

The defect these cover, measured on the shipped batch 2026-08-26: the base
melody of hook, verse and drop is the same block note for note (Graveyard's
hook and verse#1 byte-identical, Shuttle's hook and drop, and verse#1 ==
verse#2 on Sahara / Tragic / deyflo), and BatonRouge carries 6.5 melody notes
per bar in every section of the song including its intro and outro.

The plan is subtraction only — product decision 2026-07 — so every assertion
here is about how much is TAKEN AWAY, never about anything added or moved.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _arrangement(rows):
    out, cursor = [], 0
    occurrences: dict[str, int] = {}
    for name, bars in rows:
        occurrences[name] = occurrences.get(name, 0) + 1
        out.append({"name": name, "start_bar": cursor, "bars": bars,
                    "occurrence": occurrences[name]})
        cursor += bars
    return out


def _five_part():
    return _arrangement([("intro", 8), ("hook", 16), ("verse", 16),
                         ("drop", 16), ("verse", 16), ("outro", 8)])


def test_the_hook_is_never_trimmed():
    """It is the song stating its identity — the same reason
    `_shrink_sections_to_duration` protects the first peak and
    `melody_variant_for_section` returns the hook untouched."""
    from arranger import section_melody_plan, section_melody_plan_at

    plans = section_melody_plan(_five_part())
    hook = section_melody_plan_at(plans, 9 * 4.0)

    assert hook["section"] == "hook"
    assert hook["base_cut"] == 0.0
    assert hook["layer_keep"] == 1.0


def test_the_verse_gives_up_more_than_the_peak_and_the_peak_keeps_its_doubling():
    from arranger import section_melody_plan, section_melody_plan_at

    plans = section_melody_plan(_five_part())
    verse = section_melody_plan_at(plans, 25 * 4.0)
    drop = section_melody_plan_at(plans, 41 * 4.0)

    assert verse["base_cut"] > drop["base_cut"]
    assert verse["layer_keep"] == 0.0      # the register drops away
    assert drop["layer_keep"] == 1.0       # "the hook again"


def test_a_repeated_section_leans_further_back_than_its_first_pass():
    """Without this the extend cycle's repeats are copies of each other — the
    defect the whole round exists to remove, one level down."""
    from arranger import section_melody_plan, section_melody_plan_at

    plans = section_melody_plan(_five_part())
    first = section_melody_plan_at(plans, 25 * 4.0)
    second = section_melody_plan_at(plans, 57 * 4.0)

    assert first["occurrence"] == 1 and second["occurrence"] == 2
    assert second["base_cut"] > first["base_cut"]


def test_two_same_sized_sections_still_remove_a_DIFFERENT_subset():
    """`phase` is the whole reason drop#1 and drop#2 are not one clip pasted
    twice: same budget, different notes."""
    from arranger import section_melody_plan, section_melody_plan_at

    plans = section_melody_plan(_arrangement([
        ("intro", 8), ("hook", 16), ("verse", 16), ("drop", 16),
        ("verse", 16), ("drop", 16), ("outro", 8),
    ]))
    first = section_melody_plan_at(plans, 41 * 4.0)
    second = section_melody_plan_at(plans, 73 * 4.0)

    assert first["section"] == second["section"] == "drop"
    assert first["phase"] != second["phase"]


def test_the_final_chorus_is_never_thinned_for_being_a_repeat():
    """Thinning the song's peak because it happens to be the second drop is
    backwards — `arrange_song` marks it `final_chorus` and gives it the beat
    switch and the energy bump for the opposite reason."""
    from arranger import section_melody_plan, section_melody_plan_at

    arrangement = _arrangement([("intro", 8), ("hook", 16), ("verse", 16),
                                ("drop", 16), ("verse", 16), ("drop", 16),
                                ("outro", 8)])
    arrangement[-2]["final_chorus"] = True
    plans = section_melody_plan(arrangement)

    first = section_melody_plan_at(plans, 41 * 4.0)
    final = section_melody_plan_at(plans, 73 * 4.0)
    assert final["base_cut"] == first["base_cut"]


def test_the_cut_is_capped_however_deep_the_form_runs():
    """Real forms reach verse#3 at 165-220 BPM. An uncapped escalation would
    empty the last verse before the outro."""
    from arranger import _MELODY_MAX_CUT, section_melody_plan

    plans = section_melody_plan(_arrangement([
        ("intro", 8), ("hook", 16), ("verse", 16), ("drop", 16),
        ("verse", 16), ("drop", 16), ("verse", 16), ("outro", 8),
    ]))
    assert all(plan["base_cut"] <= _MELODY_MAX_CUT for _s, _e, plan in plans)
    assert max(plan["base_cut"] for _s, _e, plan in plans) > 0.0


def test_a_pattern_without_an_arrangement_keeps_every_note():
    """Unit fixtures and the single-pattern route must not change behaviour —
    same contract as `section_velocity_scale_at` answering 1.0."""
    from arranger import section_melody_plan, section_melody_plan_at

    assert section_melody_plan(None) == []
    assert section_melody_plan([]) == []
    neutral = section_melody_plan_at([], 123.0)
    assert neutral["base_cut"] == 0.0
    assert neutral["layer_keep"] == 1.0


def test_a_step_outside_every_section_keeps_every_note():
    from arranger import section_melody_plan, section_melody_plan_at

    plans = section_melody_plan(_five_part())
    past_the_end = section_melody_plan_at(plans, 999 * 4.0)
    assert past_the_end["base_cut"] == 0.0
    assert past_the_end["layer_keep"] == 1.0


def test_every_real_tempo_produces_a_plan_for_every_section():
    """Through the live sizing helpers, not a hand-written form: an unnamed
    section falling through to the default would be a silent behaviour change
    on some tempo nobody tested."""
    from arranger import (
        _MELODY_SECTION_PLAN,
        _target_song_bars,
        _extend_sections_to_duration,
        _shrink_sections_to_duration,
        midi_sections_for_target,
        section_melody_plan,
    )

    for bpm in range(60, 221, 5):
        target = _target_song_bars({"bpm": bpm})
        form = _shrink_sections_to_duration(
            _extend_sections_to_duration(midi_sections_for_target(target), target), target
        )
        names = [name for name, _bars, _tracks in form]
        assert all(name in _MELODY_SECTION_PLAN for name in names), (bpm, names)

        arrangement = _arrangement([(name, bars) for name, bars, _t in form])
        plans = section_melody_plan(arrangement)
        assert len(plans) == len(form), bpm
        # The hook is the one section that must survive whole at every tempo.
        hooks = [plan for _s, _e, plan in plans if plan["section"] == "hook"]
        assert hooks and all(plan["base_cut"] == 0.0 for plan in hooks), bpm

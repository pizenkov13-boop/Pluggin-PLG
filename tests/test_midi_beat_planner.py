from copy import deepcopy

from midi_beat_planner import attach_midi_beat_plan, build_midi_beat_plan
from midi_structure_analysis import attach_midi_structure


def _pattern(with_pause=False):
    notes=[]
    for bar in range(8):
        if with_pause and bar in (4, 5):
            continue
        base=bar*4.0
        notes.extend([
            {"note":"C5", "time_step":base, "length":0.5, "velocity":100},
            {"note":"E5", "time_step":base+1.0, "length":0.25, "velocity":88},
            {"note":"G5", "time_step":base+2.5, "length":0.5, "velocity":96},
        ])
    return {"bpm":140, "tracks":{"melody_lead":notes}}


def test_missing_structure_is_safe_fallback():
    plan=build_midi_beat_plan(_pattern())
    assert plan["enabled"] is False
    assert plan["reason"] == "missing_structure"


def test_phrase_plan_limits_kick808_priorities_to_one_per_bar():
    pattern=_pattern()
    attach_midi_structure(pattern)
    plan=attach_midi_beat_plan(pattern)
    assert plan["enabled"] is True
    anchors=plan["kick808_anchors"]
    assert anchors
    assert len({int(time // 4.0) for time in anchors}) == len(anchors)
    assert all(any(p["kick808_anchor"] == time for p in plan["phrases"]) for time in anchors)


def test_real_pause_becomes_space_window_without_note_mutation():
    pattern=_pattern(with_pause=True)
    before=deepcopy(pattern["tracks"]["melody_lead"])
    attach_midi_structure(pattern)
    plan=attach_midi_beat_plan(pattern)
    assert pattern["tracks"]["melody_lead"] == before
    assert plan["space_windows"]
    assert plan["invariants"]["edits_notes"] is False
    assert plan["invariants"]["allows_slides"] is False


def test_plan_is_deterministic():
    pattern=_pattern()
    attach_midi_structure(pattern)
    assert build_midi_beat_plan(pattern) == build_midi_beat_plan(pattern)

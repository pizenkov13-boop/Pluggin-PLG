"""Section-level dynamics: one owner for "how loud is this section".

The defect these cover, measured on the five corpus files 2026-08-26: kick
carried exactly two velocities across a whole two-minute song (its tuned base
and the quieter half of a deliberate double) and 808_bass exactly one, in
every section from the first verse to the last. `arranger._develop_section`
did scale by `style.energy`, but on the MIDI route every one of those lanes is
rebuilt afterwards, so the scaling never reached the file.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _arrangement():
    return [
        {"name": "intro", "start_bar": 0, "bars": 4, "energy": 0.94},
        {"name": "verse", "start_bar": 4, "bars": 8, "energy": 0.94},
        {"name": "hook", "start_bar": 12, "bars": 8, "energy": 0.98},
        {"name": "drop", "start_bar": 20, "bars": 8, "energy": 1.06},
    ]


def test_the_peak_section_keeps_its_tuned_level_and_the_rest_step_down():
    """Contrast by subtraction, never by raising the peak — the project issue log (not included) 0u."""
    from arranger import section_velocity_scale_at, section_velocity_scales

    scales = section_velocity_scales(_arrangement())
    drop = section_velocity_scale_at(scales, 21 * 4.0)
    hook = section_velocity_scale_at(scales, 13 * 4.0)
    verse = section_velocity_scale_at(scales, 5 * 4.0)

    assert drop == 1.0
    assert verse < hook < drop


def test_a_pattern_without_an_arrangement_is_left_exactly_as_it_was():
    """Unit fixtures and the single-pattern route must not change behaviour."""
    from arranger import section_velocity_scale_at, section_velocity_scales

    assert section_velocity_scales(None) == []
    assert section_velocity_scales([]) == []
    assert section_velocity_scale_at([], 123.0) == 1.0


def test_the_floor_stops_a_quiet_section_from_disappearing():
    from arranger import _SECTION_VELOCITY_FLOOR, section_velocity_scale_at, section_velocity_scales

    scales = section_velocity_scales([
        {"name": "outro", "start_bar": 0, "bars": 4, "energy": 0.05},
        {"name": "drop", "start_bar": 4, "bars": 4, "energy": 1.0},
    ])
    assert section_velocity_scale_at(scales, 0.0) == _SECTION_VELOCITY_FLOOR


def test_writer_and_export_gate_answer_from_the_same_function():
    """Two copies of this arithmetic is the project issue log (not included) 0.1, and here it is fatal:
    _audit_kick808_symbiosis rejects the whole generation on velocity_drift."""
    import beat_humanize as bh
    from bass808_gen import _BASS_VEL, _KICK_VEL

    data = {
        "plg_arrangement": _arrangement(),
        "tracks": {
            "kick": [
                {"time_step": 4.0, "note": "C5", "length": 0.25, "velocity": 1},
                {"time_step": 84.0, "note": "C5", "length": 0.25, "velocity": 1},
            ],
            "808_bass": [
                {"time_step": 4.0, "note": "C2", "length": 0.5, "velocity": 1},
                {"time_step": 84.0, "note": "C2", "length": 0.5, "velocity": 1},
            ],
        },
    }
    bh._normalize_kick808_dynamics(data)

    scales = bh._section_velocity_scales_for(data)
    for lane, base in (("kick", _KICK_VEL), ("808_bass", _BASS_VEL)):
        for note in data["tracks"][lane]:
            assert note["velocity"] == bh._expected_kick808_velocity(
                note, lane, base, scales=scales, fade_start=None, fade_end=None
            )


def test_the_low_end_now_carries_a_section_arc_instead_of_one_flat_level():
    import beat_humanize as bh

    data = {
        "plg_arrangement": _arrangement(),
        "tracks": {
            "kick": [
                {"time_step": 20.0, "note": "C5", "length": 0.25, "velocity": 1},   # verse
                {"time_step": 84.0, "note": "C5", "length": 0.25, "velocity": 1},   # drop
            ],
            "808_bass": [
                {"time_step": 20.0, "note": "C2", "length": 0.5, "velocity": 1},
                {"time_step": 84.0, "note": "C2", "length": 0.5, "velocity": 1},
            ],
        },
    }
    bh._normalize_kick808_dynamics(data)
    for lane in ("kick", "808_bass"):
        verse, drop = (int(n["velocity"]) for n in data["tracks"][lane])
        assert verse < drop


def test_re_running_the_normalizer_changes_nothing():
    """Late passes that ADD a kick/808 note write the bare lane base, so the
    normalizer is re-run before the gate re-measures. That is only safe
    because every term comes from the note's tags and time, not its level."""
    import beat_humanize as bh

    data = {
        "plg_arrangement": _arrangement(),
        "tracks": {
            "kick": [{"time_step": 20.0, "note": "C5", "length": 0.25, "velocity": 1}],
            "808_bass": [{"time_step": 20.0, "note": "C2", "length": 0.5, "velocity": 1}],
        },
    }
    bh._normalize_kick808_dynamics(data)
    once = {lane: [n["velocity"] for n in notes] for lane, notes in data["tracks"].items()}
    bh._normalize_kick808_dynamics(data)
    assert {lane: [n["velocity"] for n in notes] for lane, notes in data["tracks"].items()} == once


def test_transient_lanes_step_down_but_the_hats_and_the_lead_are_left_alone():
    """hi_hats has its own levelling chain (0v.2/0v.8) and a fifth multiplier
    on top is exactly the compounding those entries exist to prevent. The
    melody's octave layer breathes with the section; the lead does not."""
    import beat_humanize as bh

    verse_beat, drop_beat = 20.0, 84.0
    data = {
        "plg_arrangement": _arrangement(),
        "tracks": {
            "clap": [{"time_step": verse_beat, "note": "C5", "length": 0.2, "velocity": 127}],
            "snare": [{"time_step": verse_beat, "note": "C5", "length": 0.2, "velocity": 127}],
            "snare_layer": [{"time_step": verse_beat, "note": "C5", "length": 0.2, "velocity": 100}],
            "hi_hats": [{"time_step": verse_beat, "note": "F#3", "length": 0.1, "velocity": 80}],
            "melody_lead": [
                {"time_step": verse_beat, "note": "C5", "length": 0.5, "velocity": 90},
                {"time_step": verse_beat, "note": "C6", "length": 0.5, "velocity": 70,
                 "switch_octave": True},
                {"time_step": drop_beat, "note": "C6", "length": 0.5, "velocity": 70,
                 "switch_octave": True},
            ],
        },
    }
    changed = bh._apply_section_dynamics(data)

    assert changed == 4
    assert data["tracks"]["clap"][0]["velocity"] < 127
    assert data["tracks"]["snare"][0]["velocity"] < 127
    assert data["tracks"]["snare_layer"][0]["velocity"] < 100
    assert data["tracks"]["hi_hats"][0]["velocity"] == 80
    lead, verse_layer, drop_layer = data["tracks"]["melody_lead"]
    assert lead["velocity"] == 90          # the lead keeps its own dynamics
    assert verse_layer["velocity"] < 70    # the doubling breathes with the section
    assert drop_layer["velocity"] == 70    # the peak keeps its tuned level


def test_section_dynamics_is_a_no_op_without_an_arrangement():
    import beat_humanize as bh

    data = {"tracks": {"clap": [{"time_step": 0.0, "note": "C5", "length": 0.2, "velocity": 127}]}}
    assert bh._apply_section_dynamics(data) == 0
    assert data["tracks"]["clap"][0]["velocity"] == 127

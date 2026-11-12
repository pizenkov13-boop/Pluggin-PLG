"""Placement-only ensemble pass (midi_placement_symbiosis).

The 2026-08-15 density work thinned pocket shapes, which exposed a
kick-spacing violation in the 808-pairing branch: a cloned kick could land
inside the engine's minimum spacing of an existing paired kick and fail the
kick_roll gate (Sahara corpus case).
"""
from __future__ import annotations

from midi_phrase_drums import MIN_LOWEND_GAP_BEATS
from midi_placement_symbiosis import apply_midi_placement_symbiosis


def _pattern(kick_steps, bass_steps):
    return {
        "plg_midi_melody": True,
        "tracks": {
            "melody_lead": [
                {"time_step": 0.0, "note": "C4", "length": 16.0, "velocity": 90},
                {"time_step": 64.0, "note": "C4", "length": 16.0, "velocity": 90},
            ],
            "kick": [
                {"time_step": t, "note": "C5", "length": 0.28, "velocity": 124,
                 "kick_808_lock": True}
                for t in kick_steps
            ],
            "808_bass": [
                {"time_step": t, "note": "C2", "length": 1.0, "velocity": 116}
                for t in bass_steps
            ],
            "hi_hats": [],
            "snare": [],
            "clap": [],
        },
    }


def _kick_steps(pattern):
    return sorted(round(float(n["time_step"]), 4) for n in pattern["tracks"]["kick"])


def test_pairing_moves_the_single_neighbour_instead_of_cloning():
    # Kick at 69.5 is already paired with the 69.5 bass attack; the 70.0
    # attack must not receive a second kick 0.5 beats away.
    pattern = _pattern([69.5], [69.5, 70.0])
    apply_midi_placement_symbiosis(pattern)
    steps = _kick_steps(pattern)
    assert 69.5 not in steps and 70.0 in steps
    assert len(steps) == 1  # moved, not cloned
    assert all(b - a >= MIN_LOWEND_GAP_BEATS for a, b in zip(steps, steps[1:]))


def test_pairing_still_clones_when_no_kick_is_nearby():
    pattern = _pattern([8.0], [8.0, 20.0])
    apply_midi_placement_symbiosis(pattern)
    steps = _kick_steps(pattern)
    assert 8.0 in steps and 20.0 in steps and len(steps) == 2


def test_pairing_leaves_the_attack_solo_between_two_close_kicks():
    # Two kicks already inside the minimum spacing of the attack: any move
    # or clone would create a violation, so the 808 stays solo.
    pattern = _pattern([69.5, 70.2], [69.5, 70.2, 70.0])
    apply_midi_placement_symbiosis(pattern)
    steps = _kick_steps(pattern)
    assert steps == [69.5, 70.2]


# --- kick/backbeat flam and layer duplicates (2026-08-19) --------------------


def _flam_pattern():
    """A kick on the grid and a backbeat Rule 22 pushed 4 ms early onto it."""
    return {
        "plg_midi_melody": True,
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100}],
            "kick": [{"time_step": 1.75, "note": "C5", "length": 0.25, "velocity": 124}],
            "808_bass": [{"time_step": 1.75, "note": "C2", "length": 1.0, "velocity": 116}],
            "snare": [{"time_step": 1.7404, "note": "D1", "length": 0.28, "velocity": 112,
                       "pre_snare_shift_ms": 3.97}],
            "clap": [{"time_step": 1.7404, "note": "E1", "length": 0.22, "velocity": 104,
                      "pre_snare_shift_ms": 3.97}],
            "snare_layer": [{"time_step": 1.7404, "note": "D1", "length": 0.2, "velocity": 60,
                             "pre_snare_shift_ms": 3.97}],
            "hi_hats": [],
        },
    }


def test_a_backbeat_sharing_a_sixteenth_with_a_kick_gives_up_its_rule22_push():
    """Rule 22's 2-5 ms push reads as a flam, not a push, when a kick is there.

    Measured 2026-08-19 on Graveyard: its kick sits on 1.75 in 33 bars and the
    snare_layer's own main position is that same 1.75 — every one of the 35
    resulting pairs landed 1-40 ms apart, median 3.8 ms, none exact.
    """
    pattern = _flam_pattern()
    apply_midi_placement_symbiosis(pattern)
    kick = float(pattern["tracks"]["kick"][0]["time_step"])
    assert kick == 1.75, "the kick is phase-locked to 808_bass and must not move"
    for lane in ("snare", "clap", "snare_layer"):
        note = pattern["tracks"][lane][0]
        assert abs(float(note["time_step"]) - kick) < 1e-9, lane
        assert note["placement_symbiosis"] == "kick_flam_resolved"
    assert pattern["plg_placement_symbiosis"]["backbeat_flams_resolved"] == 3


def test_the_push_survives_where_there_is_no_kick_to_collide_with():
    pattern = _flam_pattern()
    pattern["tracks"]["kick"] = [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 124}]
    pattern["tracks"]["808_bass"] = [{"time_step": 0.0, "note": "C2", "length": 1.0, "velocity": 116}]
    apply_midi_placement_symbiosis(pattern)
    assert float(pattern["tracks"]["snare"][0]["time_step"]) == 1.7404
    assert pattern["plg_placement_symbiosis"]["backbeat_flams_resolved"] == 0


def test_resolving_a_flam_never_stacks_two_notes_of_one_lane():
    """A lane that already speaks on the kick's instant keeps its second hit
    where it is — a duplicate transient is worse than a late one."""
    pattern = _flam_pattern()
    pattern["tracks"]["snare_layer"].append(
        {"time_step": 1.75, "note": "D1", "length": 0.2, "velocity": 55}
    )
    apply_midi_placement_symbiosis(pattern)
    steps = [round(float(n["time_step"]), 5) for n in pattern["tracks"]["snare_layer"]]
    assert len(steps) == len(set(steps)), steps


def test_pulling_the_layer_onto_the_backbeat_never_duplicates_it_either():
    """The pre-existing layer snap had no such guard: measured, Shuttle shipped
    5 duplicated layer positions and Detroit 11, each a plain layer hit on top
    of a clap_double one."""
    pattern = _flam_pattern()
    pattern["tracks"]["kick"] = [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 124}]
    pattern["tracks"]["808_bass"] = [{"time_step": 0.0, "note": "C2", "length": 1.0, "velocity": 116}]
    pattern["tracks"]["snare_layer"] = [
        {"time_step": 1.7404, "note": "D1", "length": 0.2, "velocity": 60},
        {"time_step": 1.68, "note": "D1", "length": 0.2, "velocity": 55, "clap_double": True},
    ]
    apply_midi_placement_symbiosis(pattern)
    steps = [round(float(n["time_step"]), 5) for n in pattern["tracks"]["snare_layer"]]
    assert len(steps) == len(set(steps)), steps


# --- layer yields the impact instant to the kick (2026-08-20) --------------
#
# Graveyard's Alt pattern carried 36 kick+snare_layer stacks (the layer's
# pickup under a kick anticipation or a double's ghost) against 0-2 on the
# approved files — heard as mush. The layer gives the instant up; a FULL
# unison (kick+clap+snare+layer) is the approved backbeat punch and stays.


def _layer_collision_pattern(kicks, bass_at, layer_at, trio_at=None):
    """The 808 always sits under a real kick here: an orphan 808 would make
    the pairing branch clone a kick onto it and muddy what each case tests."""
    trio = trio_at if trio_at is not None else 8.0
    return {
        "plg_midi_melody": True,
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100}],
            "kick": [dict(k) for k in kicks],
            "808_bass": [{"time_step": bass_at, "note": "C2", "length": 1.0, "velocity": 116}],
            "snare": [{"time_step": trio, "note": "D1", "length": 0.28, "velocity": 112}],
            "clap": [{"time_step": trio, "note": "E1", "length": 0.22, "velocity": 104}],
            # a survivor on the backbeat: the thinning guard never empties
            # the lane, so every fixture carries one note that must stay
            "snare_layer": [
                {"time_step": layer_at, "note": "D1", "length": 0.2, "velocity": 60},
                {"time_step": 8.0, "note": "D1", "length": 0.2, "velocity": 60},
            ],
            "hi_hats": [],
        },
    }


def test_a_layer_stacked_on_a_kick_without_the_trio_is_removed():
    pattern = _layer_collision_pattern(
        [{"time_step": 2.0, "note": "C5", "length": 0.25, "velocity": 124}], 2.0, 2.0)
    apply_midi_placement_symbiosis(pattern)
    steps = [round(float(n["time_step"]), 5) for n in pattern["tracks"]["snare_layer"]]
    assert steps == [8.0]
    assert pattern["plg_placement_symbiosis"]["layer_thinned_for_kick"] == 1


def test_a_layer_on_a_full_unison_stays():
    pattern = _layer_collision_pattern(
        [{"time_step": 2.0, "note": "C5", "length": 0.25, "velocity": 124}], 2.0, 2.0,
        trio_at=2.0)
    apply_midi_placement_symbiosis(pattern)
    assert len(pattern["tracks"]["snare_layer"]) == 2


def test_a_layer_on_the_backbeat_without_a_kick_stays():
    pattern = _layer_collision_pattern(
        [{"time_step": 6.0, "note": "C5", "length": 0.25, "velocity": 124}], 6.0, 2.0,
        trio_at=2.0)
    apply_midi_placement_symbiosis(pattern)
    assert len(pattern["tracks"]["snare_layer"]) == 2


def test_a_layer_under_a_double_ghost_is_removed():
    pattern = _layer_collision_pattern(
        [
            {"time_step": 2.0, "note": "C5", "length": 0.25, "velocity": 87,
             "intentional_double_ghost": True, "lowend_role": "kick_only",
             "midi_phrase_composer": True},
            {"time_step": 2.5, "note": "C5", "length": 0.25, "velocity": 124,
             "intentional_double": True, "lowend_role": "paired",
             "midi_phrase_composer": True},
        ],
        2.5, 2.0)
    apply_midi_placement_symbiosis(pattern)
    steps = [round(float(n["time_step"]), 5) for n in pattern["tracks"]["snare_layer"]]
    assert steps == [8.0]

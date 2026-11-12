from midi_fullkit_director import (
    apply_strong_phrase_answers,
    bar_brief,
    apply_section_pulse_switch,
    apply_generated_hat_contract,
    attach_fullkit_plan,
    audit_fullkit_contract,
    audit_global_midi_symbiosis,
)


def _pattern():
    return {
        "plg_phrase_rhythm_plan": {
            "phrases": [
                {"id": "P1", "start": 0.0, "end": 8.0, "role": "statement", "section": "verse"},
                {"id": "P2", "start": 8.0, "end": 16.0, "role": "space", "section": "verse"},
                {"id": "P3", "start": 16.0, "end": 24.0, "role": "lift", "section": "drop"},
            ]
        },
        "plg_midi_beat_plan": {"phrases": [{"phrase_id": "P3", "response_anchor": 22.0}]},
        "tracks": {
            "hi_hats": [{"time_step": step, "note": "C5", "length": .1, "velocity": 90} for step in [x * .5 for x in range(48)]],
            "snare": [{"time_step": step, "note": "D1", "length": .2, "velocity": 110} for step in (2, 6, 10, 14, 18, 22)],
            "clap": [],
        },
    }


def test_fullkit_plan_coordinates_hat_modes_and_backbeat_requirements():
    pattern = _pattern()
    plan = attach_fullkit_plan(pattern)
    assert plan["enabled"] is True
    assert [row["hat_mode"] for row in plan["phrases"]] == ["support", "breathe", "drive"]
    assert plan["phrases"][-1]["response_anchor"] == 22.0
    assert audit_fullkit_contract(pattern)["passed"] is True


def test_hat_contract_makes_space_phrase_breathe_without_silencing_it():
    pattern = _pattern()
    attach_fullkit_plan(pattern)
    before = len(pattern["tracks"]["hi_hats"])
    removed = apply_generated_hat_contract(pattern)
    after = pattern["tracks"]["hi_hats"]
    assert removed > 0
    assert len(after) < before
    assert any(8.0 <= note["time_step"] < 16.0 for note in after)


def test_strong_phrase_answer_creates_air_then_repeating_motif_response():
    pattern = _pattern()
    # Make P3 a strong section entry and give it a MIDI-derived response.
    pattern["plg_phrase_rhythm_plan"]["phrases"][2]["section"] = "drop"
    attach_fullkit_plan(pattern)
    result = apply_strong_phrase_answers(pattern)
    assert result["hat_breaths"] > 0
    assert result["hat_answers"] == 1
    assert result["snare_pickups"] == 1
    assert result["signature_perc"] == 1
    assert result["boundary_fills"] == 1
    assert any(note.get("midi_motif_answer") for note in pattern["tracks"]["hi_hats"])
    assert any(note.get("midi_motif_pickup") for note in pattern["tracks"]["snare"])
    assert any(note.get("midi_signature_perc") for note in pattern["tracks"]["snare_layer"])


def test_later_verse_uses_pulse_switch_not_extra_hat_density():
    pattern = _pattern()
    pattern["plg_arrangement"] = [
        {"name": "verse", "start_bar": 0, "bars": 2, "occurrence": 1},
        {"name": "verse", "start_bar": 2, "bars": 2, "occurrence": 2},
        {"name": "drop", "start_bar": 4, "bars": 2, "occurrence": 1},
    ]
    attach_fullkit_plan(pattern)
    before = len(pattern["tracks"]["hi_hats"])
    changed = apply_section_pulse_switch(pattern)
    switched = [note for note in pattern["tracks"]["hi_hats"] if note.get("midi_section_pulse_switch")]
    assert changed == len(switched) > 0
    assert len(pattern["tracks"]["hi_hats"]) == before


def test_global_midi_symbiosis_audit_requires_one_coherent_seven_lane_ensemble():
    pattern = _pattern()
    pattern["plg_midi_melody"] = True
    pattern["tracks"].update(
        {
            "melody_lead": [{"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100}],
            "kick": [{"time_step": 0.0, "note": "C3", "length": 0.2, "velocity": 110}],
            "808_bass": [{"time_step": 0.0, "note": "C3", "length": 1.0, "velocity": 112}],
            "clap": [{"time_step": 2.0, "note": "D1", "length": 0.2, "velocity": 110}],
            "snare_layer": [{"time_step": 2.0, "note": "D1", "length": 0.1, "velocity": 70}],
        }
    )

    audit = audit_global_midi_symbiosis(pattern)

    assert audit["passed"] is True
    assert audit["kick_808_pairs"] == 1
    assert audit["snare_clap_pairs"] > 0
    assert audit["snare_layer_pairs"] > 0


def test_global_midi_symbiosis_audit_flags_an_unpaired_low_end():
    pattern = _pattern()
    pattern["plg_midi_melody"] = True
    pattern["tracks"].update(
        {
            "melody_lead": [{"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100}],
            "kick": [{"time_step": 0.0, "note": "C3", "length": 0.2, "velocity": 110}],
            "808_bass": [{"time_step": 1.0, "note": "C3", "length": 1.0, "velocity": 112}],
            "clap": [{"time_step": 2.0, "note": "D1", "length": 0.2, "velocity": 110}],
            "snare_layer": [{"time_step": 2.0, "note": "D1", "length": 0.1, "velocity": 70}],
        }
    )

    audit = audit_global_midi_symbiosis(pattern)

    assert audit["passed"] is False
    assert "unpaired_kick:1" in audit["reasons"]


# --- the per-bar brief (2026-08-19) -----------------------------------------


def _long_phrase_pattern():
    """A song whose phrases are 8 bars long — the shape that broke the backbeat."""
    active = ["melody_lead", "kick", "808_bass", "hi_hats", "snare", "clap", "snare_layer"]
    return {
        "plg_phrase_rhythm_plan": {
            "enabled": True,
            "phrases": [
                {"id": "P1", "start": 0.0, "end": 32.0, "role": "statement", "section": "verse"},
                {"id": "P2", "start": 32.0, "end": 64.0, "role": "landing", "section": "hook"},
            ],
        },
        "plg_midi_beat_plan": {"phrases": [
            {"phrase_id": "P1", "response_anchor": 23.5},
            {"phrase_id": "P2", "response_anchor": 55.0},
        ]},
        "plg_arrangement": [
            {"name": "intro", "start_bar": 0, "bars": 2, "occurrence": 1, "active_tracks": ["melody_lead"]},
            {"name": "verse", "start_bar": 2, "bars": 6, "occurrence": 1, "active_tracks": active},
            {"name": "hook", "start_bar": 8, "bars": 8, "occurrence": 1, "active_tracks": active},
        ],
        "plg_midi_structure": {"tension_curve": [{"bar": i, "value": i / 16} for i in range(16)]},
        "plg_drum_density": {"version": 1, "melody_onsets_per_bar": [i % 5 for i in range(16)]},
        "tracks": {},
    }


def test_bar_brief_covers_every_bar_and_locates_it_inside_its_phrase():
    """The brief is the shared per-bar answer the three voices lacked.

    A phrase can be eight bars long; before this existed the only per-bar
    authority was the low end's, so the backbeat had no way to ask what bar 5
    of a phrase is (its shape was written once per phrase and never tiled).
    """
    pattern = _long_phrase_pattern()
    attach_fullkit_plan(pattern)
    rows = bar_brief(pattern)

    assert [row["bar"] for row in rows] == list(range(16))
    # Every bar knows which phrase owns it and where inside that phrase it is.
    assert [row["phrase_position"] for row in rows[:8]] == (
        ["entry"] + ["body"] * 6 + ["exit"]
    )
    assert rows[8]["phrase_position"] == "entry" and rows[15]["phrase_position"] == "exit"
    assert all(row["phrase_bars"] == 8 for row in rows)


def test_bar_brief_reads_lane_permission_from_the_arrangement_not_its_own_rule():
    pattern = _long_phrase_pattern()
    attach_fullkit_plan(pattern)
    rows = bar_brief(pattern)
    # Intro declares melody only: no backbeat, no low end. Verse/hook declare
    # both. This is the arrangement's decision, read — never re-derived.
    assert [row["backbeat_allowed"] for row in rows[:4]] == [False, False, True, True]
    assert [row["lowend_allowed"] for row in rows[:4]] == [False, False, True, True]
    assert all(row["backbeat_allowed"] for row in rows[2:])


def test_bar_brief_carries_the_material_readings_already_measured_elsewhere():
    pattern = _long_phrase_pattern()
    attach_fullkit_plan(pattern)
    rows = bar_brief(pattern)
    # Sewn from plg_midi_structure and plg_drum_density, not recomputed.
    assert rows[8]["tension"] == 0.5
    assert [row["melody_onsets"] for row in rows[:5]] == [0, 1, 2, 3, 4]
    # The response anchor each phrase already carries lands in exactly one bar.
    assert [row["bar"] for row in rows if row["accent_beat"] is not None] == [5, 13]
    assert rows[5]["accent_beat"] == 23.5


def test_bar_brief_is_empty_without_a_phrase_plan():
    pattern = {"tracks": {}}
    plan = attach_fullkit_plan(pattern)
    assert plan["enabled"] is False
    assert plan["bars"] == []
    assert bar_brief(pattern) == []

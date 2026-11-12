import random

from hat_roll_engine import inject_snare_hat_rolls, snare_hit_steps


def test_snare_fallback_beats_two_and_four():
    pattern = {"tracks": {"hi_hats": [], "snare": []}}
    steps = snare_hit_steps(pattern, bars=2)
    assert 1.0 in steps
    assert 3.0 in steps
    assert 5.0 in steps
    assert 7.0 in steps


def test_hat_roll_injects_before_snare():
    hats = [
        {"time_step": 0.5, "note": "C5", "length": 0.25, "velocity": 100},
        {"time_step": 0.75, "note": "C5", "length": 0.25, "velocity": 100},
    ]
    snare_steps = [1.0]
    rng = random.Random(7)
    out = inject_snare_hat_rolls(hats, snare_steps, rng, roll_depth=0.25)
    assert len(out) > len(hats)
    assert any(n.get("hat_roll") for n in out)
    assert max(n["time_step"] for n in out) < 1.0


def test_hat_roll_keeps_notes_outside_window():
    hats = [
        {"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 100},
        {"time_step": 2.0, "note": "C5", "length": 0.25, "velocity": 100},
    ]
    rng = random.Random(1)
    out = inject_snare_hat_rolls(hats, [1.0], rng)
    assert any(abs(n["time_step"] - 0.0) < 1e-4 for n in out)
    assert any(abs(n["time_step"] - 2.0) < 1e-4 for n in out)


def test_snare_hit_steps_roll_windows_filters_by_time_not_section_name():
    """roll_windows (real melody density, see
    beat_humanize._roll_eligible_windows) must gate by absolute time window,
    ignoring the note's section tag entirely — unlike roll_sections, which
    only ever matches a fixed set of section NAMES."""
    pattern = {
        "tracks": {
            "snare": [
                {"time_step": 1.0, "section": "verse"},   # inside the eligible window
                {"time_step": 5.0, "section": "chorus"},  # outside the eligible window
            ],
        },
    }
    steps = snare_hit_steps(pattern, roll_windows=[(0.0, 4.0)])
    assert steps == [1.0]


def test_snare_hit_steps_roll_windows_empty_means_no_rolls_no_fallback():
    """An empty (but non-None) window list is a real answer — every section
    was too busy — not a signal to fall back to the beat-2-and-4 default."""
    pattern = {"tracks": {"snare": [{"time_step": 1.0}]}}
    assert snare_hit_steps(pattern, roll_windows=[]) == []


def test_snare_hit_steps_roll_windows_takes_priority_over_roll_sections():
    pattern = {
        "tracks": {
            "snare": [{"time_step": 1.0, "section": "verse"}],
        },
    }
    # roll_sections alone would reject "verse"; roll_windows should still win.
    steps = snare_hit_steps(pattern, roll_sections=("hook", "chorus"), roll_windows=[(0.0, 4.0)])
    assert steps == [1.0]


def test_tagged_backbeat_activates_the_inverse_density_roll_gate():
    """End-to-end regression for the 2026-08-15 fix: with the backbeat now
    section-tagged by midi_phrase_drums, beat_humanize._roll_eligible_windows
    must actually reach snare_hit_steps — snares in a section denser than the
    song average stop qualifying, sparse-section snares keep rolling."""
    from beat_humanize import _roll_eligible_windows
    from midi_phrase_drums import apply_phrase_drums

    active = ["melody_lead", "kick", "808_bass", "hi_hats", "snare", "clap"]
    arrangement = [
        {"name": "verse", "start_bar": 0, "bars": 5, "active_tracks": active},
        {"name": "hook", "start_bar": 5, "bars": 5, "active_tracks": active},
    ]
    # Sparse melody in the verse (2 notes), dense in the hook (20 notes):
    # the hook's own density runs above the song average and must be barred.
    melody = (
        [{"time_step": t, "note": "C4", "length": 0.5, "velocity": 90} for t in (0.0, 8.0)]
        + [{"time_step": 20.0 + i * 1.0, "note": "C4", "length": 0.4, "velocity": 90}
           for i in range(20)]
    )
    roles = ["statement"] * 5
    plan = {"enabled": True,
            "phrases": [{"start": i * 8.0, "end": i * 8.0 + 8.0, "role": role}
                        for i, role in enumerate(roles)]}
    pattern = {
        "plg_phrase_rhythm_plan": plan,
        "plg_arrangement": arrangement,
        "tracks": {"melody_lead": melody},
    }
    assert apply_phrase_drums(pattern)

    windows = _roll_eligible_windows(pattern)
    assert windows  # the gate now sees tagged snares and real density
    steps = snare_hit_steps(pattern, roll_windows=windows)
    assert steps and all(t < 20.0 for t in steps)  # verse snares only

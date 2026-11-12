"""PLG Chords: extended voice-led chords with breathing and human dynamics."""

from __future__ import annotations

import statistics
from dataclasses import replace

from chord_bed import _LO, _HI, _compact_wide_voicing, compose_chord_bed
from genre_profiles import DEFAULT as DEFAULT_PROFILE
from pattern_utils import parse_note_name


def _pattern() -> dict:
    return {
        "bpm": 150,
        "plg_variety": {"seed": 424242},
        "tracks": {"melody_lead": [{"time_step": 0.0, "note": "E5", "length": 1.0, "velocity": 95}]},
        "plg_progression": {
            # i — VI — III — VII in A natural minor (triad pcs per bar)
            "chord_tones_pc": [[9, 0, 4], [5, 9, 0], [0, 4, 7], [7, 11, 2]],
            "roots_pc": [9, 5, 0, 7],
            "bar_degrees": [0, 5, 2, 6],
            "key": "A",
            "scale": "natural_minor",
        },
        "plg_arrangement": [
            {"name": "intro", "start_bar": 0, "bars": 4},
            {"name": "chorus", "start_bar": 4, "bars": 4, "final_chorus": True},
        ],
    }


def _by_bar(chords: list[dict]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for note in chords:
        bar = int((float(note["time_step"]) + 0.1) // 4)  # jitter-tolerant bucket
        out.setdefault(bar, []).append(parse_note_name(note["note"]))
    return out


def test_extended_chords_follow_progression():
    pattern = _pattern()
    written = compose_chord_bed(pattern)
    chords = pattern["tracks"]["chords"]
    assert written == len(chords) > 0

    for note in chords:
        midi = parse_note_name(note["note"])
        assert _LO <= midi <= _HI, f"voice {note['note']} out of bed register"

    by_bar = _by_bar(chords)
    assert len(by_bar) == 8
    prog = pattern["plg_progression"]["chord_tones_pc"]
    extended_bars = 0
    for bar, voices in by_bar.items():
        triad = {pc % 12 for pc in prog[bar % 4]}
        pcs = {m % 12 for m in voices}
        assert triad <= pcs, f"bar {bar}: chord tones missing"
        if len(pcs) > len(triad):
            extended_bars += 1
    # Cm -> Cm7/Cm9: most bars must carry color tones, not bare triads
    assert extended_bars >= 6


def test_breathing_restrikes_on_odd_bars():
    pattern = _pattern()
    compose_chord_bed(pattern)
    by_bar_counts: dict[int, int] = {}
    for note in pattern["tracks"]["chords"]:
        bar = int((float(note["time_step"]) + 0.1) // 4)
        by_bar_counts[bar] = by_bar_counts.get(bar, 0) + 1
    # odd bars re-strike -> roughly double the notes of even bars
    assert by_bar_counts[1] > by_bar_counts[0]
    assert by_bar_counts[3] > by_bar_counts[2]


def test_human_dynamics_no_flat_velocity_and_off_grid():
    pattern = _pattern()
    compose_chord_bed(pattern)
    chords = pattern["tracks"]["chords"]
    velocities = [n["velocity"] for n in chords]
    assert len(set(velocities)) >= 8, "velocity must be a performance, not a constant"
    off_grid = sum(1 for n in chords if abs(float(n["time_step"]) % 0.25) > 1e-6)
    assert off_grid / len(chords) > 0.9, "notes must not sit machine-perfect on the grid"


def test_chorus_hits_harder_than_intro_on_average():
    pattern = _pattern()
    compose_chord_bed(pattern)
    chords = pattern["tracks"]["chords"]
    intro = [n["velocity"] for n in chords if float(n["time_step"]) < 16]
    chorus = [n["velocity"] for n in chords if float(n["time_step"]) >= 16]
    assert statistics.mean(chorus) > statistics.mean(intro) + 6


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("PLG_CHORD_BED", "off")
    pattern = _pattern()
    assert compose_chord_bed(pattern) == 0
    assert "chords" not in pattern["tracks"]


def test_no_profile_reproduces_default_behavior():
    # Passing no profile at all must behave exactly like the pre-existing
    # signature — no sus/power/octave-down/spread side effects sneak in.
    baseline = _pattern()
    compose_chord_bed(baseline)
    with_default_profile = _pattern()
    compose_chord_bed(with_default_profile, profile=DEFAULT_PROFILE)
    assert baseline["tracks"]["chords"] == with_default_profile["tracks"]["chords"]


def test_power_chord_drops_the_third():
    pattern = _pattern()
    profile = replace(DEFAULT_PROFILE, chord_power_chord=True)
    compose_chord_bed(pattern, profile=profile)
    chords = pattern["tracks"]["chords"]
    prog = pattern["plg_progression"]["chord_tones_pc"]
    by_bar = _by_bar(chords)
    for bar, voices in by_bar.items():
        root_pc, third_pc, fifth_pc = (pc % 12 for pc in prog[bar % 4])
        pcs = {m % 12 for m in voices}
        assert root_pc in pcs and fifth_pc in pcs
        assert third_pc not in pcs, f"bar {bar}: power chord must drop the 3rd"


def test_sus4_replaces_third_with_fourth_degree():
    pattern = _pattern()
    profile = replace(DEFAULT_PROFILE, chord_sus_mode="sus4")
    compose_chord_bed(pattern, profile=profile)
    chords = pattern["tracks"]["chords"]
    prog = pattern["plg_progression"]["chord_tones_pc"]
    by_bar = _by_bar(chords)
    for bar, voices in by_bar.items():
        root_pc, third_pc, fifth_pc = (pc % 12 for pc in prog[bar % 4])
        pcs = {m % 12 for m in voices}
        assert root_pc in pcs and fifth_pc in pcs
        assert third_pc not in pcs, f"bar {bar}: sus4 must replace the 3rd"


def test_root_octave_down_adds_a_bass_note_for_non_kanye_genre():
    pattern = _pattern()
    without = compose_chord_bed(_pattern())
    profile = replace(DEFAULT_PROFILE, chord_root_octave_down=True)
    with_flag = compose_chord_bed(pattern, profile=profile)
    assert with_flag > without
    lowest = min(parse_note_name(n["note"]) for n in pattern["tracks"]["chords"])
    assert lowest < _LO, "octave-down root must sit below the normal bed register"


def test_kanye_root_octave_down_unaffected_by_flag_removal():
    # Kanye's bass anchor is style_family-gated (is_kanye), independent of the
    # new profile flag — a profile without the flag must not silence it.
    pattern = _pattern()
    pattern["plg_variety"] = {"seed": 424242, "genre": "kanye"}
    profile = replace(DEFAULT_PROFILE, chord_root_octave_down=False)
    compose_chord_bed(pattern, profile=profile)
    lowest = min(parse_note_name(n["note"]) for n in pattern["tracks"]["chords"])
    assert lowest < _LO


def test_voicing_spread_inverts_a_tone_when_chance_is_one():
    pattern = _pattern()
    profile = replace(DEFAULT_PROFILE, chord_voicing_spread=1.0)
    compose_chord_bed(pattern, profile=profile)
    chords = pattern["tracks"]["chords"]
    assert any(n.get("voicing_inverted") for n in chords)


def test_voicing_spread_off_by_default_leaves_no_inverted_flags():
    pattern = _pattern()
    compose_chord_bed(pattern, profile=DEFAULT_PROFILE)
    chords = pattern["tracks"]["chords"]
    assert not any(n.get("voicing_inverted") for n in chords)


def test_compact_wide_voicing_pulls_the_outlier_closer():
    wide = [48, 55, 65]  # span 17 semitones
    compacted = _compact_wide_voicing(wide)
    assert max(compacted) - min(compacted) <= 12


def test_compact_wide_voicing_leaves_narrow_voicing_untouched():
    narrow = [55, 59, 62]  # span 7 semitones, already compact
    assert _compact_wide_voicing(narrow) == narrow


def test_compact_wide_voicings_keeps_register_intact_end_to_end():
    pattern = _pattern()
    profile = replace(DEFAULT_PROFILE, chord_compact_wide_voicings=True)
    compose_chord_bed(pattern, profile=profile)
    for note in pattern["tracks"]["chords"]:
        midi = parse_note_name(note["note"])
        assert _LO <= midi <= _HI


def _repeat_section_pattern() -> dict:
    pattern = _pattern()
    pattern["plg_arrangement"] = [
        {"name": "verse", "start_bar": 0, "bars": 4, "occurrence": 1},
        {"name": "verse", "start_bar": 4, "bars": 8, "occurrence": 2},
    ]
    return pattern


def test_section_variation_thins_color_tones_on_repeat_occurrence():
    baseline = _repeat_section_pattern()
    compose_chord_bed(baseline, profile=DEFAULT_PROFILE)
    varied = _repeat_section_pattern()
    profile = replace(DEFAULT_PROFILE, chord_section_variation=True)
    compose_chord_bed(varied, profile=profile)
    assert len(varied["tracks"]["chords"]) < len(baseline["tracks"]["chords"])


def test_section_variation_off_by_default():
    without_profile = _repeat_section_pattern()
    compose_chord_bed(without_profile)
    with_default_profile = _repeat_section_pattern()
    compose_chord_bed(with_default_profile, profile=DEFAULT_PROFILE)
    assert without_profile["tracks"]["chords"] == with_default_profile["tracks"]["chords"]

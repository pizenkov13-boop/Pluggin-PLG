"""The shared harmonic resolver for the 808 (midi_lowend_director.LowendRootMap).

Regression cover for a reproduced export failure: generation, the off-scale
corrector and the two export audits each carried their own fallback for what
root applies when no confident chord window covers an attack. They agreed
whenever the melody was busy and disagreed exactly in the bars a sparse MIDI
leaves silent, so a calm piano ballad could not generate a beat on any genre.
"""
from __future__ import annotations

from midi_lowend_director import LowendRootMap

C_MAJOR_PCS = {0, 2, 4, 5, 7, 9, 11}


def _window(start, end, root_pc, confidence=0.8):
    return {"start": start, "end": end, "root_pc": root_pc, "confidence": confidence}


def _note(step, name):
    return {"time_step": step, "note": name, "length": 1.0, "velocity": 90}


def test_confident_window_wins():
    root_map = LowendRootMap(
        harmonic_windows=[_window(0.0, 4.0, 5)],
        melody=[_note(0.0, "C4")],
        roots_pc=[9],
    )
    assert root_map.root_at(0.0) == (5, "window")
    assert root_map.root_at(3.99) == (5, "window")


def test_low_confidence_window_is_ignored():
    root_map = LowendRootMap(
        harmonic_windows=[_window(0.0, 4.0, 5, confidence=0.2)],
        melody=[_note(0.0, "E4")],
        roots_pc=[9],
    )
    assert root_map.root_at(0.0) == (4, "melody_bar")


def test_melody_bar_uses_the_lowest_sounding_note():
    root_map = LowendRootMap(
        harmonic_windows=[],
        melody=[_note(0.0, "G4"), _note(1.0, "A3"), _note(2.0, "E4")],
        roots_pc=[11],
    )
    assert root_map.root_at(1.5) == (9, "melody_bar")


def test_silent_bar_carries_the_last_confirmed_root_forward():
    """The bug in one assertion. Bar 1 has no window and no melody: the
    generator used to write roots_pc[1], the corrector carried bar 0's root
    forward and the audit demanded roots_pc[1] again. All three now agree,
    and they agree on the harmony that is actually still ringing."""
    root_map = LowendRootMap(
        harmonic_windows=[_window(0.0, 4.0, 5)],
        melody=[_note(0.0, "F4")],
        roots_pc=[0, 7, 9, 2],
    )
    pc, tier = root_map.root_at(6.0)
    assert tier == "carry"
    assert pc == 5


def test_carry_forward_prefers_the_most_recent_source():
    root_map = LowendRootMap(
        harmonic_windows=[_window(0.0, 4.0, 5)],
        melody=[_note(0.0, "F4"), _note(4.0, "A3")],
        roots_pc=[0],
    )
    # Bar 1 has its own melody note, so that is the reading, not a carry.
    assert root_map.root_at(5.0) == (9, "melody_bar")
    # Bar 2 is silent: the most recent source is bar 1's melody, not the
    # older chord window.
    assert root_map.root_at(9.0) == (9, "carry")


def test_progression_only_applies_before_any_material():
    root_map = LowendRootMap(
        harmonic_windows=[_window(8.0, 12.0, 5)],
        melody=[_note(8.0, "F4")],
        roots_pc=[3, 7],
    )
    pc, tier = root_map.root_at(0.0)
    assert tier == "progression"
    assert pc == 3


def test_target_is_snapped_into_the_scale_once_for_everyone():
    """The generator snapped window roots into the scale and the audits did
    not — a latent second divergence. The snap now happens in one place, so
    whatever the map returns is simultaneously what gets written and what
    gets measured."""
    root_map = LowendRootMap(
        harmonic_windows=[_window(0.0, 4.0, 1)],  # C# against C major
        melody=[],
        roots_pc=[0],
        scale_pcs=C_MAJOR_PCS,
    )
    pc, _tier = root_map.root_at(1.0)
    assert pc in C_MAJOR_PCS


def test_in_scale_target_is_left_alone():
    root_map = LowendRootMap(
        harmonic_windows=[_window(0.0, 4.0, 5)],
        melody=[],
        roots_pc=[0],
        scale_pcs=C_MAJOR_PCS,
    )
    assert root_map.root_at(1.0) == (5, "window")


def test_empty_analysis_reports_no_target_instead_of_inventing_one():
    root_map = LowendRootMap(harmonic_windows=[], melody=[], roots_pc=[])
    assert root_map.root_at(0.0) == (None, "unknown")


def test_malformed_windows_and_notes_are_skipped_not_fatal():
    root_map = LowendRootMap(
        harmonic_windows=[
            {"start": "x", "end": 4.0, "root_pc": 5, "confidence": 0.9},
            {"root_pc": 5},
            _window(0.0, 4.0, 7),
        ],
        melody=[{"time_step": 0.0}, {"note": "??"}, _note(4.0, "A3")],
        roots_pc=[0],
    )
    assert root_map.root_at(1.0) == (7, "window")
    assert root_map.root_at(5.0) == (9, "melody_bar")


def test_every_attack_in_a_sparse_song_resolves_to_something():
    """No attack anywhere in a 40-bar song built from a 12-note ballad may
    come back without a target — an unresolved attack is precisely what the
    export gate rejects."""
    root_map = LowendRootMap(
        harmonic_windows=[_window(0.0, 4.0, 0), _window(16.0, 20.0, 5)],
        melody=[_note(0.0, "C4"), _note(16.0, "F4"), _note(96.0, "E4")],
        roots_pc=[0, 8, 3, 10],
        scale_pcs={0, 2, 3, 5, 7, 8, 10},
    )
    for eighth in range(40 * 8):
        pc, tier = root_map.root_at(eighth * 0.5)
        assert pc is not None, (eighth, tier)
        assert tier in ("window", "melody_bar", "carry", "progression")

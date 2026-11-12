"""Chord reading per window: symbols, sus, the role reading, the bass, and chord
changes inside a bar. All of it is measured beside the existing fields; none of
it may move a window, a root or a confidence."""

import copy
import json

import pytest

import midi_roles
from midi_structure_analysis import analyze_midi_structure, chord_symbol

OLD_FIELDS = (
    "start", "end", "root_pc", "confidence", "change_strength", "quality",
    "chord_pcs", "tone_support", "root_candidates",
)

# Measured with the code as it was BEFORE this reading existed (untagged notes, _loop below).
GOLDEN_WINDOWS = json.loads(
    '[{"start": 0.0, "end": 4.0, "root_pc": 10, "confidence": 0.8693, "change_strength": 0.0, "quality": "minor", "chord_pcs": {"root": 10, "third": 1, "fifth": 5, "seventh": null}, "tone_support": {"third": 0.25, "fifth": 0.75, "seventh": 0.0}, "root_candidates": [{"pitch_class": 10, "score": 0.5387}, {"pitch_class": 5, "score": 0.2502}, {"pitch_class": 1, "score": 0.2111}]}, '
    '{"start": 4.0, "end": 8.0, "root_pc": 1, "confidence": 0.494, "change_strength": 0.5979, "quality": "major", "chord_pcs": {"root": 1, "third": 5, "fifth": null, "seventh": 0}, "tone_support": {"third": 0.3333, "fifth": 0.0, "seventh": 0.3333}, "root_candidates": [{"pitch_class": 1, "score": 0.4153}, {"pitch_class": 6, "score": 0.3353}, {"pitch_class": 10, "score": 0.2494}]}, '
    '{"start": 8.0, "end": 12.0, "root_pc": 10, "confidence": 0.8232, "change_strength": 0.7131, "quality": "minor", "chord_pcs": {"root": 10, "third": 1, "fifth": 5, "seventh": null}, "tone_support": {"third": 0.25, "fifth": 0.75, "seventh": 0.0}, "root_candidates": [{"pitch_class": 10, "score": 0.5284}, {"pitch_class": 5, "score": 0.2655}, {"pitch_class": 1, "score": 0.2061}]}, '
    '{"start": 12.0, "end": 16.0, "root_pc": 1, "confidence": 0.494, "change_strength": 0.5979, "quality": "major", "chord_pcs": {"root": 1, "third": 5, "fifth": null, "seventh": 0}, "tone_support": {"third": 0.3333, "fifth": 0.0, "seventh": 0.3333}, "root_candidates": [{"pitch_class": 1, "score": 0.4153}, {"pitch_class": 6, "score": 0.3353}, {"pitch_class": 10, "score": 0.2494}]}, '
    '{"start": 16.0, "end": 20.0, "root_pc": 0, "confidence": 0.8785, "change_strength": 0.6825, "quality": "open", "chord_pcs": {"root": 0, "third": null, "fifth": null, "seventh": null}, "tone_support": {"third": 0.0, "fifth": 0.0, "seventh": 0.0}, "root_candidates": [{"pitch_class": 0, "score": 0.539}, {"pitch_class": 5, "score": 0.2454}, {"pitch_class": 8, "score": 0.2156}]}]'
)


@pytest.fixture(autouse=True)
def _roles_on(monkeypatch):
    monkeypatch.delenv("PLG_MIDI_ROLES", raising=False)


def _n(beat, name, length):
    return {"time_step": float(beat), "note": name, "length": float(length), "velocity": 90}


def _loop():
    notes = []
    for bar in range(4):
        base = bar * 4.0
        if bar % 2 == 0:
            notes += [_n(base, "A#3", 4.0), _n(base, "A#4", 2.0), _n(base, "F5", 4.0)]
        else:
            notes += [_n(base, "C#4", 4.0), _n(base, "F#4", 2.0)]
        notes += [_n(base + 1, "F4", 2.0), _n(base + 2, "C#5", 2.0), _n(base + 3, "C5", 2.0)]
    return notes


def _window(notes, root_pc, scale="natural_minor"):
    return analyze_midi_structure(notes, root_pc=root_pc, scale=scale)["harmonic_rhythm"][0]


def _bar(pitches, length=3.8, beat=0.0):
    return [_n(beat + t, p, length) for t in (0.0,) for p in pitches]


# --- the symbol -------------------------------------------------------------


@pytest.mark.parametrize(
    "pitches, root, symbol",
    [
        (("E3", "G3", "B3"), 4, "Em"),
        (("F3", "A3", "C4"), 5, "F"),
        (("C3", "E3", "G3", "B3"), 0, "Cmaj7"),
        (("G3", "B3", "D4", "F4"), 7, "G7"),
        (("E3", "G3", "B3", "D4"), 4, "Em7"),
        (("B3", "D4", "F4", "A4"), 11, "Bm7b5"),
        (("B3", "D4", "F4"), 11, "Bdim"),
        (("A3", "E4"), 9, "A5"),
        (("A3", "D4", "E4"), 9, "Asus4"),
        (("A3", "B3", "E4"), 9, "Asus2"),
    ],
)
def test_the_window_gets_a_readable_symbol(pitches, root, symbol):
    window = _window(_bar(pitches), root)

    assert window["symbol"] == symbol


def test_sus_is_an_extra_label_the_quality_stays_open_and_a_third_cancels_it():
    suspended = _window(_bar(("A3", "D4", "E4")), 9)
    triad = _window(_bar(("A3", "C4", "D4", "E4")), 9)

    assert suspended["quality"] == "open" and suspended["sus"] == "sus4"
    assert suspended["chord_pcs"] == {"root": 9, "third": None, "fifth": 4, "seventh": None}
    assert triad["sus"] is None and triad["quality"] == "minor"


def test_chord_symbol_is_a_pure_naming_function():
    assert chord_symbol(0, "major", {"seventh": 11}) == "Cmaj7"
    assert chord_symbol(2, "minor", {"seventh": 1}) == "DmMaj7"  # seventh is a pitch class
    assert chord_symbol(7, "open", {"fifth": 2, "seventh": 5}, "sus4") == "G7sus4"
    assert chord_symbol(9, "open", {"fifth": 4, "seventh": None}) == "A5"


# --- nothing that was there moves ---------------------------------------------


def test_window_geometry_roots_and_confidences_are_what_they_were_before():
    windows = analyze_midi_structure(_loop(), bpm=150, root_pc=10, scale="natural_minor")["harmonic_rhythm"]

    assert [{k: w[k] for k in OLD_FIELDS} for w in windows] == GOLDEN_WINDOWS


def test_tags_change_none_of_the_old_fields():
    plain = analyze_midi_structure(_loop(), bpm=150, root_pc=10, scale="natural_minor")
    tagged_notes = _loop()
    midi_roles.tag_roles(tagged_notes)
    assert {n["plg_role"] for n in tagged_notes} >= {"bass", "chords", "lead"}

    tagged = analyze_midi_structure(tagged_notes, bpm=150, root_pc=10, scale="natural_minor")

    assert [{k: w[k] for k in OLD_FIELDS} for w in tagged["harmonic_rhythm"]] == GOLDEN_WINDOWS
    for key in ("phrases", "motifs", "pauses", "tension_curve", "cadences", "phrase_grid", "span"):
        assert tagged[key] == plain[key]


# --- the bass and the role reading --------------------------------------------


def test_without_tags_there_is_no_role_reading_and_no_bass():
    for window in analyze_midi_structure(_loop(), root_pc=10, scale="natural_minor")["harmonic_rhythm"]:
        assert window["bass_pc"] is None and window["harmony"] is None


def test_tagged_notes_give_the_bass_pitch_class_and_a_chord_part_reading():
    notes = _loop()
    midi_roles.tag_roles(notes)

    windows = analyze_midi_structure(notes, root_pc=10, scale="natural_minor")["harmonic_rhythm"]

    assert [w["bass_pc"] for w in windows[:3]] == [10, 1, 10]
    first = windows[0]["harmony"]
    assert first["root_pc"] == 10 and first["same_root_as_window"] is True
    assert first["symbol"].startswith("A#")
    assert set(first) >= {"root_pc", "quality", "symbol", "sus", "chord_pcs", "tone_support", "same_root_as_window"}


# --- a change inside a bar ------------------------------------------------------


def test_a_second_half_on_another_chord_is_reported_and_the_window_is_not_cut():
    notes = _bar(("C3", "E3", "G3"), length=1.9) + _bar(("G2", "B2", "D3"), length=1.9, beat=2.0)

    result = analyze_midi_structure(notes, root_pc=0, scale="major")

    assert [(w["start"], w["end"]) for w in result["harmonic_rhythm"]] == [(0.0, 4.0)]
    change = result["harmonic_rhythm"][0]["mid_bar_changes"]
    assert [(c["at"], c["from_root"], c["to_root"]) for c in change] == [(2.0, 0, 7)]
    assert change[0]["from_symbol"] == "C" and change[0]["to_symbol"] == "G"
    assert result["summary"]["mid_bar_changes"] == 1


def test_changes_on_the_bar_line_and_steady_bars_report_nothing_inside_a_bar():
    notes = []
    for beat in (0.0, 4.0, 8.0, 12.0):
        pitches = ("C3", "E3", "G3") if beat % 8 == 0 else ("A2", "C3", "E3")
        notes += [_n(beat, p, 3.8) for p in pitches]

    windows = analyze_midi_structure(notes, root_pc=9, scale="natural_minor")["harmonic_rhythm"]

    assert len(windows) >= 2
    assert all(w["mid_bar_changes"] == [] for w in windows)


def test_an_empty_or_one_note_file_still_returns_every_field():
    result = analyze_midi_structure([_n(0.0, "C4", 1.0)], root_pc=0, scale="major")

    window = result["harmonic_rhythm"][0]
    assert window["symbol"] and window["mid_bar_changes"] == [] and window["harmony"] is None
    assert analyze_midi_structure([])["status"] == "empty"

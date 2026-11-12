"""midi_roles: which role each note of an imported MIDI plays."""

import copy

import pytest

import midi_roles
from midi_ingest import merge_notes_into_pattern
from midi_roles import BASS, CHORDS, COUNTER, LEAD


def _n(beat, name, length, velocity=90):
    return {"time_step": float(beat), "note": name, "length": float(length), "velocity": velocity, "midi": True}


@pytest.fixture(autouse=True)
def _roles_on(monkeypatch):
    monkeypatch.delenv("PLG_MIDI_ROLES", raising=False)


def _loop_with_chords_on_the_downbeat(bars=4):
    """Sahara's shape: a chord stab on each bar's downbeat, a line on the other beats."""
    notes = []
    for bar in range(bars):
        base = bar * 4.0
        notes += [_n(base, "A#3", 4.0), _n(base, "A#4", 2.0), _n(base, "F5", 4.0)]
        notes += [_n(base + 1, "F4", 2.0), _n(base + 2, "C#5", 2.0), _n(base + 3, "C5", 2.0)]
    return notes


def _roles_by(notes):
    return {(n["time_step"], n["note"]): role for n, role in zip(notes, midi_roles.classify(notes))}


def test_a_chord_stab_and_a_line_are_told_apart():
    notes = _loop_with_chords_on_the_downbeat()

    roles = _roles_by(notes)

    assert roles[(0.0, "A#3")] == BASS
    assert roles[(0.0, "A#4")] == CHORDS and roles[(0.0, "F5")] == CHORDS
    assert [roles[(1.0, "F4")], roles[(2.0, "C#5")], roles[(3.0, "C5")]] == [LEAD, LEAD, LEAD]


def test_the_lowest_held_note_is_bass_only_in_the_lowest_octave_of_the_file():
    # B5 held under a higher short note is a chord tone, not a bass.
    notes = _loop_with_chords_on_the_downbeat(bars=2) + [_n(1.5, "B5", 2.0), _n(1.5, "G6", 0.5)]

    assert _roles_by(notes)[(1.5, "B5")] == CHORDS


def test_a_plain_single_line_stays_one_line():
    notes = [_n(i * 0.5, name, 0.5) for i, name in enumerate(["E4", "G4", "B4", "A4", "G4", "E4", "D4", "E4"])]

    assert set(midi_roles.classify(notes)) == {LEAD}


def test_a_stray_second_voice_or_chord_hit_is_folded_back_into_the_lead():
    one_chord = [_n(0, "C3", 4.0), _n(0, "G3", 4.0), _n(0, "E4", 4.0)] + [_n(i, "C5", 0.5) for i in range(1, 6)]
    assert set(midi_roles.classify(one_chord)) == {LEAD}

    two_dyads = [_n(i, "C5", 0.5) for i in range(8)] + [_n(2, "A4", 0.5), _n(5, "A4", 0.5)]
    assert COUNTER not in midi_roles.classify(two_dyads)


def test_a_second_short_voice_under_the_line_is_the_counter_melody():
    notes = []
    for bar in range(4):
        base = bar * 2.0
        notes += [_n(base, "E5", 0.5), _n(base, "A4", 0.5), _n(base + 1, "D5", 0.5), _n(base + 1, "G4", 0.5)]

    roles = _roles_by(notes)

    assert roles[(0.0, "E5")] == LEAD and roles[(0.0, "A4")] == COUNTER


def test_an_octave_double_is_the_same_line_not_a_counter():
    notes = [_n(i, "C5", 0.5) for i in range(8)] + [_n(i, "C4", 0.5) for i in range(8)]

    assert COUNTER not in midi_roles.classify(notes)


def test_classify_does_not_touch_the_notes_and_is_repeatable():
    notes = _loop_with_chords_on_the_downbeat()
    before = copy.deepcopy(notes)

    first = midi_roles.classify(notes)

    assert notes == before and first == midi_roles.classify(notes)


def test_an_unreadable_pitch_or_an_empty_file_does_not_raise():
    assert midi_roles.classify([]) == []
    assert midi_roles.classify([{"time_step": 0.0, "note": "??", "length": 1.0}]) == [LEAD]


def test_tag_roles_writes_one_tag_per_note_and_counts_them():
    notes = _loop_with_chords_on_the_downbeat()

    summary = midi_roles.tag_roles(notes)

    assert all(note[midi_roles.ROLE_KEY] in midi_roles.ROLES for note in notes)
    assert sum(summary["counts"].values()) == summary["notes"] == len(notes)
    assert summary["counts"][BASS] == 4


@pytest.mark.parametrize("value, expected", [("", True), ("on", True), ("off", False), ("0", False), ("FALSE", False)])
def test_the_switch(monkeypatch, value, expected):
    monkeypatch.setenv("PLG_MIDI_ROLES", value)

    assert midi_roles.enabled() is expected


def test_ingest_tags_the_notes_and_every_note_stays_in_the_melody_lane():
    notes = _loop_with_chords_on_the_downbeat()
    pattern: dict = {}

    assert merge_notes_into_pattern(pattern, copy.deepcopy(notes), source="loop.mid", name="loop.mid")

    lane = pattern["tracks"]["melody_lead"]
    assert len(lane) == len(notes)
    assert {n[midi_roles.ROLE_KEY] for n in lane} >= {BASS, CHORDS, LEAD}
    assert pattern["plg_midi_roles"]["counts"][BASS] == 4


def test_with_the_switch_off_ingest_is_exactly_what_it_was(monkeypatch):
    notes = _loop_with_chords_on_the_downbeat()
    on, off = {}, {}
    merge_notes_into_pattern(on, copy.deepcopy(notes), source="loop.mid", name="loop.mid")
    monkeypatch.setenv("PLG_MIDI_ROLES", "off")
    merge_notes_into_pattern(off, copy.deepcopy(notes), source="loop.mid", name="loop.mid")

    assert "plg_midi_roles" not in off
    assert all(midi_roles.ROLE_KEY not in n for n in off["tracks"]["melody_lead"])
    stripped = [{k: v for k, v in n.items() if k != midi_roles.ROLE_KEY} for n in on["tracks"]["melody_lead"]]
    assert stripped == off["tracks"]["melody_lead"]
    assert {k: v for k, v in on.items() if k not in ("plg_midi_roles", "tracks")} == {
        k: v for k, v in off.items() if k != "tracks"
    }

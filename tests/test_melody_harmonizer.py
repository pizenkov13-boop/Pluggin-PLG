"""Mike Dean: scale-aware diatonic harmony (a third above each melody note,
staying in-scale — not a fixed chromatic interval) on counter_melody.
Targets "toliver" (Don Toliver world), not "trap" where the rest of Mike
Dean's shipped techniques (P.1/P.2/P.4, live-bass texture) landed — verified
trap already has THREE mechanisms touching counter_melody
(counter_melody_mirrors_chords, counter_melody_matches_direction, and an
independently LLM/symbolic-composer-generated line), while toliver had zero
existing counter_melody opt-ins and its own genre-profile comment already
describes the world as "call-response" — a natural home for a harmony voice."""

from __future__ import annotations

from beat_humanize import materialize_melody_harmonizer
from genre_profiles import PROFILES
from music_theory import diatonic_transpose, name_for_midi
from pattern_utils import parse_note_name


def _note(t: float, note: str, length: float = 1.0, velocity: int = 100) -> dict:
    return {"time_step": t, "note": note, "length": length, "velocity": velocity}


def test_diatonic_third_varies_by_scale_degree():
    # A natural minor: A->C and D->F are both diatonic thirds, but NOT the
    # same fixed semitone shift is the point being proven here indirectly —
    # both happen to be minor 3rds (3 semitones) in this scale, so instead
    # verify against a scale where the degrees genuinely differ: major.
    root = parse_note_name("C4") % 12
    c4 = parse_note_name("C4")
    d4 = parse_note_name("D4")
    third_from_c = diatonic_transpose(c4, root, "major", 2) - c4  # C->E = 4 semitones (major 3rd)
    third_from_d = diatonic_transpose(d4, root, "major", 2) - d4  # D->F = 3 semitones (minor 3rd)
    assert third_from_c == 4
    assert third_from_d == 3
    assert third_from_c != third_from_d  # proves it's diatonic, not a fixed chromatic shift


def test_diatonic_fifth_above():
    root = parse_note_name("A4") % 12
    a4 = parse_note_name("A4")
    assert name_for_midi(diatonic_transpose(a4, root, "natural_minor", 4)) == "E5"


def test_off_scale_input_snaps_before_transposing():
    root = parse_note_name("A4") % 12
    off_scale = parse_note_name("A4") + 1  # A# — not in A natural minor
    # Should not raise, and should resolve to a real scale degree.
    result = diatonic_transpose(off_scale, root, "natural_minor", 2)
    assert isinstance(result, int)


def test_diatonic_transpose_below_wraps_octave_correctly():
    root = parse_note_name("A4") % 12
    a4 = parse_note_name("A4")
    below = diatonic_transpose(a4, root, "natural_minor", -2)
    assert below < a4


def test_materialize_melody_harmonizer_noop_without_melody():
    profile = PROFILES["toliver"]
    assert materialize_melody_harmonizer({"tracks": {}}, profile=profile) == 0


def test_materialize_melody_harmonizer_overwrites_counter_melody():
    profile = PROFILES["toliver"]
    data = {
        "tracks": {
            "melody_lead": [_note(0.0, "A4", velocity=100), _note(4.0, "D5", velocity=100)],
            "counter_melody": [_note(0.0, "G3")],  # pre-existing content, must be replaced
        }
    }
    placed = materialize_melody_harmonizer(data, profile=profile)
    assert placed == 2
    counter = data["tracks"]["counter_melody"]
    assert len(counter) == 2
    assert counter[0]["note"] != "G3"


def test_harmony_notes_are_quieter_than_source():
    profile = PROFILES["toliver"]
    data = {"tracks": {"melody_lead": [_note(0.0, "A4", velocity=100)]}}
    materialize_melody_harmonizer(data, profile=profile)
    assert data["tracks"]["counter_melody"][0]["velocity"] == 75


def test_harmony_note_is_a_diatonic_third_above_source():
    profile = PROFILES["toliver"]  # natural_minor
    data = {"tracks": {"melody_lead": [_note(0.0, "A4", velocity=100)]}}
    materialize_melody_harmonizer(data, profile=profile)
    harmony_note = data["tracks"]["counter_melody"][0]["note"]
    root = parse_note_name("A4") % 12
    expected = name_for_midi(diatonic_transpose(parse_note_name("A4"), root, "natural_minor", 2))
    assert harmony_note == expected == "C5"


def test_toliver_profile_has_melody_harmonizer_layer_enabled():
    assert PROFILES["toliver"].melody_harmonizer_layer is True
    for name, other in PROFILES.items():
        if name == "toliver":
            continue
        assert other.melody_harmonizer_layer is False, name

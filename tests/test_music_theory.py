from music_theory import (
    KEY_SIGNATURE_SF_MI,
    chromatic_neighbor_pc,
    detect_root_pc,
    detect_scale,
    key_from_midi_key_name,
    key_from_sf_mi,
    key_label,
    key_label_from_sf_mi,
    name_for_midi,
    nearest_off_root_pc,
    snap_pc_to_scale,
)


def test_detect_root_prefers_weighted_pitch():
    notes = [
        {"note": "A3", "length": 4.0, "velocity": 120},
        {"note": "C4", "length": 0.25, "velocity": 80},
    ]
    assert detect_root_pc(notes) == 9  # A


def test_detect_root_default_when_empty():
    assert detect_root_pc([]) == 9


def test_detect_scale_c_major_run():
    notes = [
        {"note": name, "length": 0.9, "velocity": 100}
        for name in ("C4", "D4", "E4", "F4", "G4", "A4", "B4")
    ]
    assert detect_scale(notes, root_pc=0) == "major"


def test_detect_scale_a_natural_minor():
    notes = [
        {"note": name, "length": 0.9, "velocity": 100}
        for name in ("A3", "B3", "C4", "D4", "E4", "F4", "G4")
    ]
    assert detect_scale(notes, root_pc=9) == "natural_minor"


def test_detect_scale_default_when_empty():
    assert detect_scale([], root_pc=0) == "natural_minor"
    assert detect_scale([], root_pc=0, default="phrygian") == "phrygian"


def test_snap_major_third_to_minor_in_a():
    # A natural minor has C natural, so C#5 (major 3rd) collapses down to C.
    cs5 = 5 * 12 + 1
    snapped = snap_pc_to_scale(cs5, 9, "natural_minor")
    assert snapped % 12 == 0  # C


def test_snap_keeps_in_scale_note():
    a5 = 5 * 12 + 9
    assert snap_pc_to_scale(a5, 9, "natural_minor") == a5


def test_phrygian_allows_flat_second():
    # Phrygian on A includes the flat-2 (A#); it must survive untouched.
    as5 = 5 * 12 + 10
    assert snap_pc_to_scale(as5, 9, "phrygian") == as5


def test_name_for_midi_roundtrip():
    assert name_for_midi(57) == "A4"


def test_key_label():
    assert key_label(9, "natural_minor") == "A minor"
    assert key_label(9, "phrygian") == "A phrygian"


def test_nearest_off_root_prefers_the_fifth():
    # A dorian (root pc 9): the 5th is 7 semitones up -> E (pc 4).
    assert nearest_off_root_pc(9, "dorian") == 4


def test_nearest_off_root_falls_back_to_fourth_without_a_fifth():
    # Locrian has a diminished 5th (6 semitones), not a true 5th (7) -> falls
    # back to the 4th (5 semitones up from root).
    root_pc = 9  # A
    result = nearest_off_root_pc(root_pc, "locrian")
    assert result == (root_pc + 5) % 12


def test_nearest_off_root_never_returns_the_root_itself():
    for scale in ("natural_minor", "harmonic_minor", "phrygian", "major", "dorian", "lydian", "locrian"):
        for root_pc in range(12):
            assert nearest_off_root_pc(root_pc, scale) != root_pc


def test_chromatic_neighbor_prefers_minor_second_below():
    # Phrygian on A (root pc 9): pc 8 (-1 semitone) is NOT in phrygian, so it wins.
    assert chromatic_neighbor_pc(9, "phrygian") == 8


def test_chromatic_neighbor_falls_back_to_plus_one_when_minus_one_is_in_scale():
    # Harmonic minor on A (root pc 9) HAS the raised 7th (pc 8) in-scale, so
    # -1 semitone isn't chromatic there — falls back to +1 (pc 10).
    assert chromatic_neighbor_pc(9, "harmonic_minor") == 10


def test_chromatic_neighbor_is_never_a_scale_tone():
    from music_theory import SCALES

    for scale in SCALES:
        for root_pc in range(12):
            intervals = SCALES[scale]
            scale_pcs = {(root_pc + i) % 12 for i in intervals}
            assert chromatic_neighbor_pc(root_pc, scale) not in scale_pcs


def test_key_from_sf_mi_table_covers_all_signatures():
    assert key_from_sf_mi(0, 0) == (0, "major")  # C
    assert key_from_sf_mi(0, 1) == (9, "natural_minor")  # Am
    assert key_from_sf_mi(-3, 1) == (0, "natural_minor")  # Cm
    assert key_from_sf_mi(3, 1) == (6, "natural_minor")  # F#m
    assert key_from_sf_mi(2, 0) == (2, "major")  # D
    assert key_label_from_sf_mi(-3, 1) == "Cm"
    assert len(KEY_SIGNATURE_SF_MI) == 30  # 15 maj + 15 min
    # Negative sf = flats (−1..−7), exactly as in the MIDI cheat sheet.
    flat_maj = {
        -1: "F", -2: "Bb", -3: "Eb", -4: "Ab", -5: "Db", -6: "Gb", -7: "Cb",
    }
    flat_min = {
        -1: "Dm", -2: "Gm", -3: "Cm", -4: "Fm", -5: "Bbm", -6: "Ebm", -7: "Abm",
    }
    for sf, label in flat_maj.items():
        assert (sf, 0) in KEY_SIGNATURE_SF_MI
        assert key_label_from_sf_mi(sf, 0) == label
        assert key_from_sf_mi(sf, 0)[1] == "major"
    for sf, label in flat_min.items():
        assert (sf, 1) in KEY_SIGNATURE_SF_MI
        assert key_label_from_sf_mi(sf, 1) == label
        assert key_from_sf_mi(sf, 1)[1] == "natural_minor"
    for sf in range(1, 8):
        assert (sf, 0) in KEY_SIGNATURE_SF_MI and (sf, 1) in KEY_SIGNATURE_SF_MI


def test_key_from_midi_key_name_mido_style():
    assert key_from_midi_key_name("Cm") == (0, "natural_minor")
    assert key_from_midi_key_name("C") == (0, "major")
    assert key_from_midi_key_name("Am") == (9, "natural_minor")
    assert key_from_midi_key_name("F#m") == (6, "natural_minor")
    assert key_from_midi_key_name("Bb") == (10, "major")
    assert key_from_midi_key_name("Bbm") == (10, "natural_minor")
    assert key_from_midi_key_name("") is None

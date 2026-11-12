from copy import deepcopy

from midi_structure_analysis import analyze_midi_structure, attach_midi_structure


def _n(t: float, note: str, length: float = 0.25, velocity: int = 100) -> dict:
    return {
        "time_step": t,
        "note": note,
        "length": length,
        "velocity": velocity,
    }


def test_phrase_boundaries_follow_real_breaths():
    notes = [
        _n(0.0, "C5", 0.5),
        _n(0.5, "E5", 0.5),
        _n(1.0, "G5", 0.5),
        _n(1.5, "E5", 0.5),
        # More than two beats of real silence.
        _n(4.5, "D5", 0.5),
        _n(5.0, "F5", 0.5),
        _n(5.5, "A5", 0.5),
        _n(6.0, "F5", 0.5),
    ]
    result = analyze_midi_structure(notes, root_pc=0, scale="major")
    assert result["status"] == "ok"
    assert result["pauses"]
    assert any(abs(phrase["start"] - 4.5) < 0.01 for phrase in result["phrases"])
    assert any(
        phrase["end_boundary_reason"] == "pause" for phrase in result["phrases"]
    )


def test_motif_signature_detects_transposed_repeat():
    motif = [(0.0, "C5"), (0.5, "D5"), (1.0, "E5"), (1.5, "G5"), (2.0, "E5")]
    notes = [_n(t, pitch) for t, pitch in motif]
    notes += [
        _n(8.0 + t, pitch)
        for t, pitch in [(0.0, "D5"), (0.5, "E5"), (1.0, "F#5"), (1.5, "A5"), (2.0, "F#5")]
    ]
    result = analyze_midi_structure(notes, root_pc=0, scale="major")
    assert result["motifs"]
    assert result["motifs"][0]["repeat_count"] >= 2
    assert result["motifs"][0]["interval_shape"] == [0, 2, 4, 7, 4]


def test_harmonic_rhythm_tracks_c_to_g_change():
    notes = []
    for t in (0.0, 1.0, 2.0, 3.0):
        notes += [_n(t, "C4", 0.9), _n(t, "E4", 0.9), _n(t, "G4", 0.9)]
    for t in (4.0, 5.0, 6.0, 7.0):
        notes += [_n(t, "G3", 0.9), _n(t, "B3", 0.9), _n(t, "D4", 0.9)]
    result = analyze_midi_structure(notes, root_pc=0, scale="major")
    roots = [item["root_pc"] for item in result["harmonic_rhythm"]]
    assert roots[:2] == [0, 7]
    assert all(item["root_candidates"] for item in result["harmonic_rhythm"])


def test_chromatic_dense_bar_has_more_tension():
    notes = [_n(i * 0.5, "C5", 0.3) for i in range(8)]
    chromatic = ["C#5", "F#5", "G5", "C#6", "F#5", "C#5", "G5", "F#5"]
    notes += [_n(4.0 + i * 0.5, pitch, 0.3) for i, pitch in enumerate(chromatic)]
    result = analyze_midi_structure(notes, root_pc=0, scale="major")
    assert result["tension_curve"][1]["value"] > result["tension_curve"][0]["value"]
    assert result["tension_curve"][1]["components"]["chromaticity"] > 0


def test_likely_dominant_to_tonic_cadence_is_reported():
    notes = []
    for t in (0.0, 1.0, 2.0, 3.0):
        notes += [_n(t, "G4", 0.8), _n(t, "B4", 0.8), _n(t, "D5", 0.8)]
    for t in (4.0, 5.0, 6.0, 7.0):
        notes += [_n(t, "C4", 0.8), _n(t, "E4", 0.8), _n(t, "G4", 0.8)]
    result = analyze_midi_structure(notes, root_pc=0, scale="major")
    assert any(
        cadence["type"] in ("authentic_resolution", "tonic_resolution")
        for cadence in result["cadences"]
    )


def test_attach_is_metadata_only_and_json_shaped():
    notes = [_n(i * 0.5, pitch) for i, pitch in enumerate(["C5", "D5", "E5", "G5"] * 4)]
    pattern = {
        "bpm": 140,
        "scale": "major",
        "plg_midi_key": {"root_pc": 0, "scale": "major"},
        "tracks": {"melody_lead": deepcopy(notes)},
    }
    before = deepcopy(pattern["tracks"])
    result = attach_midi_structure(pattern)
    assert pattern["tracks"] == before
    assert pattern["plg_midi_structure"] is result
    assert result["summary"]["phrase_count"] >= 1
    assert isinstance(result["confidence"], float)


def test_variety_scale_roll_does_not_leak_into_structure_scale():
    """pattern["scale"] is generation_variety's per-run anti-clone roll.
    plg_midi_structure must detect the scale from the notes themselves so the
    same MIDI yields the same analysis every run (2026-08-15: the leak made
    the same melody flip C# minor <-> C# phrygian between runs)."""
    notes = [
        _n(i * 0.5, pitch, 0.5)
        for i, pitch in enumerate(["C4", "D4", "Eb4", "F4", "G4", "Ab4", "Bb4", "C5"] * 2)
    ]
    results = []
    for variety_scale in ("phrygian", "harmonic_minor", "minor"):
        pattern = {
            "bpm": 140,
            "scale": variety_scale,  # the per-run roll — must be ignored
            "tracks": {"melody_lead": deepcopy(notes)},
        }
        results.append(attach_midi_structure(pattern))
    scales = {r["scale"] for r in results}
    roots = {r["root_pc"] for r in results}
    assert len(scales) == 1, f"structure scale follows the variety roll: {scales}"
    assert len(roots) == 1
    assert results[0]["scale"] == "natural_minor"  # detected from the notes
    assert results[0]["root_pc"] == 0


def test_attach_is_deterministic_across_repeated_calls():
    notes = [_n(i * 0.5, pitch, 0.5) for i, pitch in enumerate(["A4", "C5", "E5", "A5"] * 3)]
    pattern = {"bpm": 140, "tracks": {"melody_lead": deepcopy(notes)}}
    first = attach_midi_structure(pattern)
    second = attach_midi_structure(pattern)
    assert first["root_pc"] == second["root_pc"]
    assert first["scale"] == second["scale"]

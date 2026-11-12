"""Genre auto-detection from uploaded MIDI content (2026-07, Feature 1):
density/interval-shape/scale/tempo instead of text-prompt keywords.

Confidence-first by design — synthetic note lists here are deliberately
clean/unambiguous per genre-cluster so the classifier's real behaviour
(confident hit vs. honest None) is exercised without needing a real
labeled corpus (see midi_genre_detect.py's own docstring for why the
density/interval thresholds are a first-pass estimate, not measured)."""

from __future__ import annotations

from pathlib import Path

import mido
import pytest

from midi_genre_detect import (
    DETAILED_GENRES,
    detect_genre_from_midi,
    extract_midi_features,
    read_tempo_bpm,
)
from pattern_utils import parse_note_name


def _notes(names: list[str], *, step: float = 0.5, length: float = 0.4, velocity: int = 100) -> list[dict]:
    return [
        {"time_step": i * step, "note": n, "length": length, "velocity": velocity}
        for i, n in enumerate(names)
    ]


# --- extract_midi_features ---------------------------------------------------


def test_density_is_notes_per_beat_span():
    notes = _notes(["C3", "D3", "E3", "F3"], step=1.0)  # 4 notes over 3 beats
    features = extract_midi_features(notes)
    assert features["density"] == pytest.approx(4 / 3, rel=1e-6)


def test_empty_notes_returns_zeroed_features_not_a_crash():
    features = extract_midi_features([])
    assert features["note_count"] == 0
    assert features["density"] == 0.0
    assert features["scale"] is None


def test_chromatic_step_frac_detects_all_semitone_motion():
    notes = _notes(["C3", "C#3", "D3", "D#3", "E3"])
    features = extract_midi_features(notes)
    assert features["chromatic_step_frac"] == 1.0
    assert features["wide_jump_frac"] == 0.0


def test_wide_jump_frac_detects_large_leaps():
    notes = _notes(["C3", "C5", "C3", "C5"])  # 24-semitone leaps
    features = extract_midi_features(notes)
    assert features["wide_jump_frac"] == 1.0


def test_pitch_trend_sign_matches_direction():
    up = extract_midi_features(_notes(["C3", "D3", "E3", "G3"]))
    down = extract_midi_features(_notes(["G3", "E3", "D3", "C3"]))
    assert up["pitch_trend"] > 0
    assert down["pitch_trend"] < 0


# --- detect_genre_from_midi: per-cluster confident hits -----------------------


def test_slow_sparse_dorian_stepwise_detects_west_coast_or_detroit_cluster():
    """west_coast/detroit share a distinct 90-105 BPM window, well separated
    from every other DETAILED_GENRES BPM range — this alone should be enough
    to land in that cluster even with generic stepwise motion."""
    notes = _notes(["D3", "F3", "G3", "A3", "C4", "A3", "G3", "F3"], step=2.0, length=1.8)
    hit = detect_genre_from_midi(notes, tempo_bpm=96)
    assert hit is not None
    genre, confidence = hit
    assert genre in ("west_coast", "detroit")
    assert 0.0 < confidence <= 1.0


def test_fast_lydian_wide_jumps_detects_hyperpop():
    """Lydian (raised 4th) + wide jumps + fast tempo is hyperpop's own
    melody_chaotic_wide_jumps/scale signature — the sharpest signal this
    module has for any genre."""
    notes = _notes(
        ["C4", "C5", "D4", "C#5", "E4", "A5", "F#4", "C6", "G4", "D6", "C4", "B5", "E4", "A5", "F#4", "C6"],
        step=0.5,
        length=0.4,
        velocity=110,
    )
    hit = detect_genre_from_midi(notes, tempo_bpm=170)
    assert hit == ("hyperpop", 1.0)


def test_natural_minor_chromatic_walk_detects_phonk():
    """phonk's melody_chromatic_walk flag is the other sharp per-genre signal.
    Notes stay strictly within A natural minor (using its own two diatonic
    half-steps, B-C and E-F, for the "chromatic walk" motion rather than an
    out-of-scale passing tone) with A weighted heavily so root detection
    doesn't ambiguously land on the relative major (C) instead — natural
    minor and its relative major share the exact same pitch classes, so
    detect_root_pc's weighting has to clearly favor one tonic."""
    seq = [
        ("A3", 1.5, 127), ("B3", 0.3, 90), ("C4", 0.3, 90), ("B3", 0.3, 90),
        ("A3", 1.5, 127), ("E3", 0.3, 80), ("F3", 0.3, 80), ("E3", 0.3, 80),
        ("A3", 1.5, 127),
    ]
    notes = []
    t = 0.0
    for note, length, velocity in seq:
        notes.append({"time_step": t, "note": note, "length": length, "velocity": velocity})
        t += length
    hit = detect_genre_from_midi(notes, tempo_bpm=140)
    assert hit == ("phonk", 1.0)


# --- confidence gating: never a forced guess -----------------------------------


def test_too_few_notes_returns_none():
    notes = _notes(["C3", "D3", "E3"])
    assert detect_genre_from_midi(notes, tempo_bpm=140) is None


def test_no_notes_returns_none():
    assert detect_genre_from_midi([], tempo_bpm=140) is None


def test_ambiguous_generic_material_can_return_none():
    """Plain, unremarkable stepwise motion at a heavily-overlapping trap-
    cluster tempo (rage/opium/drill/pluggnb all plausible) with no scale
    signal (tempo_bpm/scale both weak) should not be forced into one lane."""
    notes = _notes(["C4", "D4", "E4", "D4", "C4", "D4", "E4", "D4"], step=1.0, length=0.9)
    hit = detect_genre_from_midi(notes, tempo_bpm=None)
    # Either a genuinely confident cluster hit or None — both are valid
    # outcomes; the real assertion is that it never raises and, if it DOES
    # return a hit, the margin/floor gates were actually satisfied.
    if hit is not None:
        genre, confidence = hit
        assert genre in DETAILED_GENRES
        assert confidence >= 0.55


def test_only_classifies_into_detailed_genres():
    notes = _notes(["D3", "F3", "G3", "A3", "C4", "A3", "G3", "F3"], step=2.0, length=1.8)
    hit = detect_genre_from_midi(notes, tempo_bpm=96)
    if hit is not None:
        assert hit[0] in DETAILED_GENRES


# --- read_tempo_bpm -------------------------------------------------------------


def _write_midi(path: Path, *, tempo_bpm: float | None) -> None:
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    if tempo_bpm is not None:
        track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo_bpm), time=0))
    note = parse_note_name("C4")
    track.append(mido.Message("note_on", note=note, velocity=90, time=0))
    track.append(mido.Message("note_off", note=note, velocity=0, time=480))
    mid.save(str(path))


def test_read_tempo_bpm_reads_a_real_tempo_event(tmp_path: Path):
    path = tmp_path / "with_tempo.mid"
    _write_midi(path, tempo_bpm=128.0)
    assert read_tempo_bpm(path) == pytest.approx(128.0, abs=0.5)


def test_read_tempo_bpm_returns_none_without_a_tempo_event(tmp_path: Path):
    path = tmp_path / "no_tempo.mid"
    _write_midi(path, tempo_bpm=None)
    assert read_tempo_bpm(path) is None


def test_read_tempo_bpm_returns_none_for_a_malformed_file(tmp_path: Path):
    path = tmp_path / "garbage.mid"
    path.write_bytes(b"not a real midi file")
    assert read_tempo_bpm(path) is None


def test_mpqn_to_bpm_formula():
    from midi_genre_detect import mpqn_to_bpm

    # Spec: BPM = 60_000_000 / MPQN; 500_000 → 120.
    assert mpqn_to_bpm(500_000) == pytest.approx(120.0)
    assert mpqn_to_bpm(468_750) == pytest.approx(128.0)
    assert mpqn_to_bpm(0) is None


def test_read_key_signature_from_meta(tmp_path: Path):
    from midi_genre_detect import read_key_signature

    path = tmp_path / "cm.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("key_signature", key="Cm", time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    mid.save(str(path))
    assert read_key_signature(path) == (0, "natural_minor")


def test_read_midi_control_markers_detects_sustain_and_pitch_bend(tmp_path: Path):
    from midi_genre_detect import read_midi_control_markers

    path = tmp_path / "marked.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("control_change", control=64, value=127, time=0))
    track.append(mido.Message("control_change", control=11, value=40, time=0))
    track.append(mido.Message("control_change", control=11, value=90, time=120))
    track.append(mido.Message("pitchwheel", pitch=1200, time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    mid.save(str(path))
    markers = read_midi_control_markers(path)
    assert markers["sustain_cc64"] is True
    assert markers["expression_cc11"] is True
    assert markers["pitch_bend"] is True


def test_read_midi_control_markers_aftertouch_pan_filter(tmp_path: Path):
    from midi_genre_detect import read_midi_control_markers

    path = tmp_path / "at_pan.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("aftertouch", value=80, time=0))
    track.append(mido.Message("control_change", control=10, value=0, time=0))
    track.append(mido.Message("control_change", control=10, value=127, time=60))
    track.append(mido.Message("control_change", control=10, value=0, time=60))
    track.append(mido.Message("control_change", control=10, value=127, time=60))
    track.append(mido.Message("control_change", control=74, value=20, time=0))
    track.append(mido.Message("control_change", control=74, value=100, time=60))
    track.append(mido.Message("control_change", control=1, value=10, time=0))
    track.append(mido.Message("control_change", control=1, value=90, time=60))
    track.append(mido.Message("pitchwheel", pitch=4000, time=0))
    track.append(mido.Message("note_on", note=72, velocity=90, time=0))
    track.append(mido.Message("note_off", note=72, velocity=0, time=240))
    mid.save(str(path))
    markers = read_midi_control_markers(path)
    assert markers["aftertouch"] is True
    assert markers["pan_cc10_pingpong"] is True
    assert markers["filter_cc74"] is True
    assert markers["mod_cc1"] is True
    assert markers["pitch_bend_wide"] is True
    assert markers["pitch_bend_micro"] is False


def test_dna_from_gm_program_covers_all_families():
    from midi_genre_detect import dna_from_gm_program

    assert dna_from_gm_program(0) == "Piano"
    assert dna_from_gm_program(4) == "Rhodes"
    assert dna_from_gm_program(6) == "Key"
    assert dna_from_gm_program(9) == "Bell"
    assert dna_from_gm_program(24) == "Guitar"
    assert dna_from_gm_program(30) == "Electric Guitar"
    assert dna_from_gm_program(33) == "Bass"
    assert dna_from_gm_program(40) == "Strings"
    assert dna_from_gm_program(45) == "Pizzicato"
    assert dna_from_gm_program(46) == "Pluck"
    assert dna_from_gm_program(81) == "Synth"
    assert dna_from_gm_program(88) == "Pad"
    assert dna_from_gm_program(200) is None


def test_read_midi_instrument_hints_ignores_kit_pack_bell_template(tmp_path: Path):
    """a kit pack stamps the same 'Bell - Ding' track_name on every pack MIDI."""
    from midi_genre_detect import read_midi_instrument_hints

    path = tmp_path / "adieu.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Adieu - Bell - Ding - C 1", time=0))
    track.append(mido.Message("note_on", note=84, velocity=90, time=0))
    track.append(mido.Message("note_off", note=84, velocity=0, time=240))
    mid.save(str(path))
    hints = read_midi_instrument_hints(path)
    assert hints["dna_from_name"] is None
    assert hints["lock_text"] is None
    assert hints["dna_from_track"] is None  # pack template junk


def test_read_midi_instrument_hints_real_track_name_is_hard_lock(tmp_path: Path, caplog):
    from midi_genre_detect import read_midi_instrument_hints

    path = tmp_path / "pad_loop.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Omnisphere_Pad", time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    mid.save(str(path))
    with caplog.at_level("INFO", logger="plg.midi_genre_detect"):
        hints = read_midi_instrument_hints(path)
    assert hints["dna_from_track"] == "Pad"
    assert hints["lock_source"] == "track_name"
    assert hints["system_lock"] is True
    assert hints["lock_text"] == "Omnisphere_Pad"
    assert any("MIDI system lock" in r.message for r in caplog.records)


def test_read_midi_instrument_hints_serum_pluck_track_name(tmp_path: Path):
    from midi_genre_detect import read_midi_instrument_hints

    path = tmp_path / "pluck.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Serum_Pluck_Dark", time=0))
    track.append(mido.Message("note_on", note=72, velocity=90, time=0))
    track.append(mido.Message("note_off", note=72, velocity=0, time=240))
    mid.save(str(path))
    hints = read_midi_instrument_hints(path)
    assert hints["dna_from_track"] == "Pluck"
    assert hints["lock_source"] == "track_name"
    assert hints["system_lock"] is True


def test_read_midi_instrument_hints_gm_program_piano(tmp_path: Path, caplog):
    from midi_genre_detect import read_midi_instrument_hints

    path = tmp_path / "gm_piano.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("program_change", program=0, time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    mid.save(str(path))
    with caplog.at_level("INFO", logger="plg.midi_genre_detect"):
        hints = read_midi_instrument_hints(path)
    assert hints["dna_from_program"] == "Piano"
    assert hints["lock_source"] == "program_change"
    assert hints["system_lock"] is True
    assert "PC=0" in str(hints["lock_text"])
    assert any("MIDI system lock" in r.message for r in caplog.records)


def test_read_midi_instrument_hints_gm_synth_lead_and_pad_families(tmp_path: Path):
    from midi_genre_detect import read_midi_instrument_hints

    for program, dna in ((81, "Synth"), (88, "Pad"), (9, "Bell"), (24, "Guitar")):
        path = tmp_path / f"gm_{program}.mid"
        mid = mido.MidiFile(ticks_per_beat=480)
        track = mido.MidiTrack()
        mid.tracks.append(track)
        track.append(mido.Message("program_change", program=program, time=0))
        track.append(mido.Message("note_on", note=60, velocity=90, time=0))
        track.append(mido.Message("note_off", note=60, velocity=0, time=480))
        mid.save(str(path))
        hints = read_midi_instrument_hints(path)
        assert hints["dna_from_program"] == dna
        assert hints["system_lock"] is True


def test_read_midi_instrument_hints_cc0_bank_select_hard_map(tmp_path: Path, caplog):
    from midi_genre_detect import read_midi_instrument_hints

    path = tmp_path / "bank_pad.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message("control_change", control=0, value=1, time=0))  # CC0 bank MSB
    track.append(mido.Message("program_change", program=88, time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    mid.save(str(path))
    with caplog.at_level("INFO", logger="plg.midi_genre_detect"):
        hints = read_midi_instrument_hints(path)
    assert hints["bank_msb"] == 1
    assert hints["dna_from_bank_map"] == "Pad"
    assert hints["lock_source"] == "bank_select+program"
    assert hints["system_lock"] is True
    assert any("MIDI system lock" in r.message for r in caplog.records)


def test_read_midi_text_event_type_pluck_hard_lock(tmp_path: Path, caplog):
    from midi_genre_detect import read_midi_instrument_hints

    path = tmp_path / "pack_comment.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(
        mido.MetaMessage(
            "text",
            text="BPM: 140 | Key: C# Min | Type: Dark Hyperpop Pluck",
            time=0,
        )
    )
    # Misleading default GM piano must lose to author text.
    track.append(mido.Message("program_change", program=0, time=0))
    track.append(mido.Message("note_on", note=72, velocity=90, time=0))
    track.append(mido.Message("note_off", note=72, velocity=0, time=240))
    mid.save(str(path))
    with caplog.at_level("INFO", logger="plg.midi_genre_detect"):
        hints = read_midi_instrument_hints(path)
    assert hints["dna_from_text"] == "Pluck"
    assert hints["lock_source"] == "text_event"
    assert hints["system_lock"] is True
    assert hints["dna_from_program"] == "Piano"  # present but not winning lock
    assert any("source=text_event" in r.message for r in caplog.records)


def test_read_midi_instrument_name_serum_maps_to_synth(tmp_path: Path):
    from midi_genre_detect import read_midi_instrument_hints

    path = tmp_path / "serum.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("instrument_name", name="Serum", time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    mid.save(str(path))
    hints = read_midi_instrument_hints(path)
    assert hints["dna_from_name"] == "Synth"
    assert hints["lock_source"] == "instrument_name"
    assert hints["system_lock"] is True


def test_read_midi_instrument_name_kontakt_alone_does_not_lock(tmp_path: Path):
    from midi_genre_detect import read_midi_instrument_hints

    path = tmp_path / "kontakt.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("instrument_name", name="Kontakt 7", time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    mid.save(str(path))
    hints = read_midi_instrument_hints(path)
    assert hints["dna_from_name"] is None
    assert hints["system_lock"] is False


def test_synth_channel_name_locks_electronic_family(tmp_path: Path):
    """Marketplace MIDI often carries only the authoring synth's channel name.

    "ZENOLOGY 1" (Roland ZEN-Core) has no instrument keyword in it, but it does
    say the sound was electronic — enough to stop the picker from guessing.
    """
    from midi_genre_detect import read_midi_instrument_hints

    path = tmp_path / "zenology.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="ZENOLOGY 1", time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    mid.save(str(path))
    hints = read_midi_instrument_hints(path)
    assert hints["dna_from_track"] == "Synth"
    assert hints["system_lock"] is True


def test_dna_from_plugin_name_covers_marketplace_brands():
    from midi_genre_detect import dna_from_plugin_name

    assert dna_from_plugin_name("Serum 2") == "Synth"
    assert dna_from_plugin_name("Sylenth1") == "Synth"
    assert dna_from_plugin_name("Meowsynth") == "Pluck"
    assert dna_from_plugin_name("Purity") == "Bell"
    assert dna_from_plugin_name("Korg M1") == "Key"
    assert dna_from_plugin_name("Xpand!2") == "Synth"
    assert dna_from_plugin_name("u-he Diva") == "Synth"
    assert dna_from_plugin_name("ACE") == "Synth"
    # Short-token false positives must not fire.
    assert dna_from_plugin_name("palace") is None
    # Sampler hosts / junk / bass-only engines never invent a melody family.
    assert dna_from_plugin_name("Kontakt 7") is None
    assert dna_from_plugin_name("Dorothy") is None
    assert dna_from_plugin_name("Bassmaker") is None
    assert dna_from_plugin_name("Attack 3") is None


def test_dna_from_bank_program_falls_back_to_gm():
    from midi_genre_detect import dna_from_bank_program

    assert dna_from_bank_program(0, 0, 0) == "Piano"
    assert dna_from_bank_program(1, 0, 88) == "Pad"
    assert dna_from_bank_program(None, None, 81) is None

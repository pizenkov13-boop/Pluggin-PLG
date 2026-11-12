"""Bundled melody-sample fallback (2026-07): audio_features.analyze_sustain,
melody_sample_bank's MIDI-profile/scoring/picker (graduated sustain_affinity),
and the starter_kit trigger that consults the bank on weak library matches or
competes when MIDI lean's sustain against a keyword library pick."""

from __future__ import annotations

import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from audio_features import analyze_sustain
from melody_sample_bank import (
    MELODY_SHAPE_CHORDS,
    MELODY_SHAPE_ELECTRONIC_LEAD,
    MELODY_SHAPE_UNKNOWN,
    MELODY_SHAPE_VOCAL_CHOP,
    MUTATION_CHAOS,
    MUTATION_CONTRAST,
    MUTATION_SAFE,
    SAMPLE_SHAPE_SHORT_TRANSIENT,
    SAMPLE_SHAPE_SUSTAINED_DENSE,
    SUSTAIN_AFFINITY_SOFT_FLOOR,
    _midi_melody_profile,
    _root_correction_semitones,
    _sample_key_pc,
    _sample_shape_category,
    classify_melody_shape,
    midi_melody_shape_for_pattern,
    midi_sustain_affinity,
    pick_melody_sample,
    resolve_playback_path,
    sustain_affinity,
)


def _tone_wav(
    path: Path,
    *,
    freq: float = 440.0,
    seconds: float = 1.0,
    sr: int = 44100,
    amp: float = 0.5,
    decay: bool = False,
) -> None:
    """decay=True: fades to near-silence by 1s in (a pluck/bell). decay=False:
    constant amplitude for the whole duration (a sustained pad)."""
    t = np.arange(int(sr * seconds)) / sr
    envelope = np.exp(-3.0 * t) if decay else np.ones_like(t)
    x = (amp * envelope * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


def _note(time_step: float, note: str, length: float, velocity: int = 100) -> dict:
    return {"time_step": time_step, "note": note, "length": length, "velocity": velocity}


# --- audio_features.analyze_sustain -----------------------------------------


def test_analyze_sustain_flags_a_constant_tone_as_still_loud(tmp_path: Path):
    path = tmp_path / "pad.wav"
    _tone_wav(path, seconds=6.0, decay=False)
    feats = analyze_sustain(path)
    assert feats is not None
    assert feats["duration_s"] == pytest.approx(6.0, rel=0.05)
    assert feats["tail_rms_ratio"] > 0.9


def test_analyze_sustain_flags_a_decaying_tone_as_faded_out(tmp_path: Path):
    path = tmp_path / "pluck.wav"
    _tone_wav(path, seconds=6.0, decay=True)
    feats = analyze_sustain(path)
    assert feats is not None
    assert feats["tail_rms_ratio"] < 0.2


def test_analyze_sustain_unreadable_file_returns_none(tmp_path: Path):
    path = tmp_path / "garbage.wav"
    path.write_bytes(b"not a real wav")
    assert analyze_sustain(path) is None


# --- melody_sample_bank._midi_melody_profile / sustain_affinity -------------


def test_midi_melody_profile_empty_notes_is_none():
    assert _midi_melody_profile([], 140) is None


def test_midi_melody_profile_computes_length_and_pitch_stats():
    notes = [
        _note(0.0, "C4", 4.0),   # 4 beats @ 60bpm = 4.0s
        _note(4.0, "C4", 4.0),
        _note(8.0, "E4", 0.25),  # a short one thrown in
    ]
    profile = _midi_melody_profile(notes, bpm=60)
    assert profile is not None
    assert profile["max_length_s"] == pytest.approx(4.0, rel=0.05)
    assert profile["long_fraction"] == pytest.approx(2 / 3, rel=0.05)
    assert profile["median_pitch"] > 0


def test_midi_melody_profile_all_short_notes_has_zero_long_fraction():
    notes = [_note(i * 0.25, "A4", 0.2) for i in range(8)]
    profile = _midi_melody_profile(notes, bpm=140)
    assert profile is not None
    assert profile["long_fraction"] == 0.0
    assert profile["max_length_s"] < 1.5


def test_sustain_affinity_borderline_real_profile_clears_soft_floor():
    """Regression: 0.159 / 2.03s used to fail the old binary gates (0.3 / 3.0s)."""
    profile = {
        "long_fraction": 0.159,
        "max_length_s": 2.03,
        "median_length_s": 0.9,
        "median_pitch": 60.0,
    }
    affinity = sustain_affinity(profile)
    assert affinity >= SUSTAIN_AFFINITY_SOFT_FLOOR
    assert 0.20 <= affinity <= 0.45


def test_sustain_affinity_plucky_profile_near_zero():
    profile = {
        "long_fraction": 0.0,
        "max_length_s": 0.4,
        "median_length_s": 0.25,
        "median_pitch": 69.0,
    }
    assert sustain_affinity(profile) < SUSTAIN_AFFINITY_SOFT_FLOOR
    assert sustain_affinity(profile) == pytest.approx(0.0, abs=0.01)


def test_midi_sustain_affinity_from_pattern():
    # ~2.03s notes @ 140bpm ≈ length 4.737 beats; mix with shorts → borderline.
    pattern = {
        "bpm": 140,
        "tracks": {
            "melody_lead": [
                *[_note(i * 0.5, "C4", 0.4) for i in range(15)],
                _note(8.0, "E4", 4.74),
                _note(13.0, "G4", 4.74),
                _note(18.0, "A4", 4.74),
            ]
        },
    }
    # 3 long / 18 total ≈ 0.167 long_fraction, max ≈ 2.03s
    assert midi_sustain_affinity(pattern) >= SUSTAIN_AFFINITY_SOFT_FLOOR


# --- melody_sample_bank.classify_melody_shape (2026-07) ---------------------
# Pure note-grid geometry (time_step/length/pitch in beats, no audio involved)
# split into the 3 archetypes the product spec calls out, so a fast electronic
# arpeggio can never be mistaken for a vocal-chop line (the reported bug: fast
# leads kept landing on vocal samples -> "пищащий бурундук").


def _chord_notes(bars: int = 8, beats_per_chord: float = 4.0) -> list[dict]:
    """3-4 notes stacked vertically, each held ~1 bar — a chord bed."""
    notes = []
    for i in range(bars):
        t = i * beats_per_chord
        for pitch in ("C4", "E4", "G4", "B4"):
            notes.append(_note(t, pitch, beats_per_chord))
    return notes


def _electronic_lead_notes(count: int = 64) -> list[dict]:
    """Strictly monophonic, 1/16-grid, rapid octave-jumping ("comb") notes."""
    cycle = ["C4", "C5", "D4", "D5", "E4", "E5", "F4", "F5"]
    return [_note(i * 0.25, cycle[i % len(cycle)], 0.25) for i in range(count)]


def _vocal_chop_notes(phrases: int = 12) -> list[dict]:
    """Monophonic, medium-length notes with regular breathing gaps and only
    small pitch moves between phrases (no wild octave leaping)."""
    notes = []
    t = 0.0
    pitches = ["E4", "D4", "E4", "F4", "E4", "D4", "C4", "D4", "E4", "F4", "G4", "F4"]
    for i in range(phrases):
        length = 1.5
        notes.append(_note(t, pitches[i % len(pitches)], length))
        t += length + 1.5  # regular "breathing" pause between phrases
    return notes


def test_classify_melody_shape_detects_chords():
    assert classify_melody_shape(_chord_notes()) == MELODY_SHAPE_CHORDS


def test_classify_melody_shape_detects_electronic_lead():
    assert classify_melody_shape(_electronic_lead_notes()) == MELODY_SHAPE_ELECTRONIC_LEAD


def test_classify_melody_shape_detects_vocal_chop():
    assert classify_melody_shape(_vocal_chop_notes()) == MELODY_SHAPE_VOCAL_CHOP


def test_classify_melody_shape_empty_or_single_note_is_unknown():
    assert classify_melody_shape([]) == MELODY_SHAPE_UNKNOWN
    assert classify_melody_shape([_note(0.0, "C4", 1.0)]) == MELODY_SHAPE_UNKNOWN


def test_classify_melody_shape_dense_same_pitch_run_is_not_electronic_lead():
    """Dense/short but never jumping octaves (no "comb" pattern) — must NOT be
    misread as an electronic lead just because the notes are short and tight."""
    notes = [_note(i * 0.25, "A4", 0.2) for i in range(24)]
    assert classify_melody_shape(notes) == MELODY_SHAPE_UNKNOWN


def test_classify_melody_shape_legato_mono_long_notes_is_unknown_not_chords():
    """Long notes with no vertical stacking at all: sustained, but not a
    chord bed (chords require BOTH the overlap and the length)."""
    notes = [_note(i * 4.0, "C4", 4.0) for i in range(6)]
    assert classify_melody_shape(notes) == MELODY_SHAPE_UNKNOWN


def test_midi_melody_shape_for_pattern_reads_melody_lead_track():
    pattern = {"bpm": 140, "tracks": {"melody_lead": _electronic_lead_notes()}}
    assert midi_melody_shape_for_pattern(pattern) == MELODY_SHAPE_ELECTRONIC_LEAD


# --- melody_sample_bank._sample_shape_category (2026-07, pure waveform) -----
# No filenames anywhere here — a file literally named "...vocal_pad..." must
# classify purely off its measured duration/tail, same as any other file.


def test_sample_shape_category_ignores_vocal_filename_when_waveform_is_dense(tmp_path: Path):
    """Regression: a file named like the real bank's vocal pad, but measuring
    long+loud like Flutter_Piano, must NOT be banned from the dense bucket
    just because "vocal" appears in its name."""
    path = tmp_path / "MO_TC_vocal_pad_angelic_vocals_Fmaj.wav"
    _tone_wav(path, seconds=8.0, decay=False)
    assert _sample_shape_category(path) == SAMPLE_SHAPE_SUSTAINED_DENSE


def test_sample_shape_category_short_vocal_named_file_is_short_transient(tmp_path: Path):
    """Same filename keyword, but the waveform itself is short/decaying —
    classification follows the waveform, not the word "vocal"."""
    path = tmp_path / "vocal_shout_one_shot.wav"
    _tone_wav(path, seconds=1.0, decay=True)
    assert _sample_shape_category(path) == SAMPLE_SHAPE_SHORT_TRANSIENT


def test_sample_shape_category_short_punchy_file_is_short_transient(tmp_path: Path):
    path = tmp_path / "short_pluck.wav"
    _tone_wav(path, seconds=1.0, decay=True)
    assert _sample_shape_category(path) == SAMPLE_SHAPE_SHORT_TRANSIENT


def test_sample_shape_category_long_sustained_file_is_sustained_dense(tmp_path: Path):
    path = tmp_path / "long_pad.wav"
    _tone_wav(path, seconds=8.0, decay=False)
    assert _sample_shape_category(path) == SAMPLE_SHAPE_SUSTAINED_DENSE


def test_sample_shape_category_borderline_duration_needs_loud_tail(tmp_path: Path):
    """~3s and still loud throughout (soft-duration path) -> dense; ~3s but
    decayed away by the tail window -> transient. Same duration, different
    waveform shape, different bucket — the tail_rms_ratio half of the gate."""
    still_loud = tmp_path / "borderline_loud.wav"
    _tone_wav(still_loud, seconds=3.0, decay=False)
    assert _sample_shape_category(still_loud) == SAMPLE_SHAPE_SUSTAINED_DENSE

    decayed = tmp_path / "borderline_decayed.wav"
    _tone_wav(decayed, seconds=3.0, decay=True)
    assert _sample_shape_category(decayed) == SAMPLE_SHAPE_SHORT_TRANSIENT


def test_sample_shape_category_unreadable_file_defaults_to_short_transient(tmp_path: Path):
    path = tmp_path / "garbage.wav"
    path.write_bytes(b"not a real wav")
    assert _sample_shape_category(path) == SAMPLE_SHAPE_SHORT_TRANSIENT


# --- melody_sample_bank.pick_melody_sample ----------------------------------


def _build_bank(tmp_path: Path) -> Path:
    bank = tmp_path / "bank"
    bank.mkdir()
    _tone_wav(bank / "pad_sustained.wav", freq=440.0, seconds=8.0, decay=False)
    _tone_wav(bank / "pluck_short.wav", freq=880.0, seconds=1.0, decay=True)
    return bank


def test_pick_melody_sample_prefers_sustained_file_for_long_notes(tmp_path: Path, monkeypatch):
    bank = _build_bank(tmp_path)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    pattern = {
        "bpm": 90,
        "tracks": {"melody_lead": [_note(i * 4.0, "C4", 4.0) for i in range(6)]},
    }
    path, score = pick_melody_sample(pattern)
    assert path is not None
    assert path.name == "pad_sustained.wav"


def test_pick_melody_sample_prefers_short_file_for_plucky_notes(tmp_path: Path, monkeypatch):
    bank = _build_bank(tmp_path)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    pattern = {
        "bpm": 140,
        "tracks": {"melody_lead": [_note(i * 0.25, "A4", 0.2) for i in range(24)]},
    }
    path, score = pick_melody_sample(pattern)
    assert path is not None
    assert path.name == "pluck_short.wav"


def test_pick_melody_sample_borderline_sustain_leans_pad(tmp_path: Path, monkeypatch):
    """Borderline affinity (~0.27) still prefers a sustained pad over a short pluck."""
    bank = _build_bank(tmp_path)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    # 3 long (~2.03s) + 16 short → long_fraction ≈ 0.158, max ≈ 2.03s
    pattern = {
        "bpm": 140,
        "tracks": {
            "melody_lead": [
                *[_note(i * 0.5, "C4", 0.4) for i in range(16)],
                _note(10.0, "E4", 4.74),
                _note(15.0, "G4", 4.74),
                _note(20.0, "A4", 4.74),
            ]
        },
    }
    assert midi_sustain_affinity(pattern) >= SUSTAIN_AFFINITY_SOFT_FLOOR
    path, _score = pick_melody_sample(pattern)
    assert path is not None
    assert path.name == "pad_sustained.wav"


def test_pick_melody_sample_compares_all_wavs_not_hardcoded_list(tmp_path: Path, monkeypatch):
    bank = tmp_path / "bank"
    bank.mkdir()
    _tone_wav(bank / "a_short_bell.wav", freq=900.0, seconds=0.8, decay=True)
    _tone_wav(bank / "b_mid_keys.wav", freq=500.0, seconds=2.0, decay=True)
    _tone_wav(bank / "c_best_pad.wav", freq=300.0, seconds=9.0, decay=False)
    _tone_wav(bank / "d_another_pluck.wav", freq=700.0, seconds=1.2, decay=True)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    pattern = {
        "bpm": 90,
        "tracks": {"melody_lead": [_note(i * 4.0, "C3", 4.0) for i in range(4)]},
    }
    path, _score = pick_melody_sample(pattern)
    assert path is not None
    assert path.name == "c_best_pad.wav"


def test_pick_melody_sample_missing_bank_dir_returns_none(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: tmp_path / "nope")
    pattern = {"bpm": 140, "tracks": {"melody_lead": [_note(0.0, "C4", 1.0)]}}
    assert pick_melody_sample(pattern) == (None, 0.0)


def test_pick_melody_sample_no_melody_notes_returns_none(tmp_path: Path, monkeypatch):
    bank = _build_bank(tmp_path)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)
    pattern = {"bpm": 140, "tracks": {}}
    assert pick_melody_sample(pattern) == (None, 0.0)


# --- melody_sample_bank.pick_melody_sample: note-shape hard gate (2026-07) --
# The reported bug: a fast electronic lead kept landing on a vocal-*sounding*
# sample regardless of vibe/coverage math ("пищащий бурундук"), AND a
# perfectly good dense/atmospheric file (measured, not by name) was banned
# from the electronic-lead bucket purely because its filename said "vocal".
# These prove both directions of the fix using bare filenames with no vocal
# wording at all — the gate must depend only on the measured waveform.


def _build_dense_vs_transient_bank(tmp_path: Path) -> tuple[Path, Path, Path]:
    bank = tmp_path / "bank"
    bank.mkdir()
    dense = bank / "texture_one.wav"  # long + loud throughout -> sustained_dense
    transient = bank / "texture_two.wav"  # short + decaying -> short_transient
    _tone_wav(dense, freq=440.0, seconds=10.0, decay=False)
    _tone_wav(transient, freq=880.0, seconds=1.0, decay=True)
    return bank, dense, transient


def test_filter_candidates_by_shape_electronic_lead_allows_both_categories(tmp_path: Path):
    """The actual bug fix, tested directly on the gate: electronic_lead must
    NOT drop a dense/sustained file from the pool — it's free to compete
    against (and, on the right MIDI/vibe, beat) a short one, unlike before
    when a "vocal"-named dense file was excluded outright regardless of fit."""
    from melody_sample_bank import MELODY_SHAPE_ELECTRONIC_LEAD, _filter_candidates_by_shape

    _bank, dense, transient = _build_dense_vs_transient_bank(tmp_path)
    filtered = _filter_candidates_by_shape([dense, transient], MELODY_SHAPE_ELECTRONIC_LEAD)
    assert set(filtered) == {dense, transient}


def test_filter_candidates_by_shape_chords_excludes_transient(tmp_path: Path):
    from melody_sample_bank import MELODY_SHAPE_CHORDS, _filter_candidates_by_shape

    _bank, dense, transient = _build_dense_vs_transient_bank(tmp_path)
    filtered = _filter_candidates_by_shape([dense, transient], MELODY_SHAPE_CHORDS)
    assert filtered == [dense]


def test_filter_candidates_by_shape_vocal_chop_excludes_dense(tmp_path: Path):
    from melody_sample_bank import MELODY_SHAPE_VOCAL_CHOP, _filter_candidates_by_shape

    _bank, dense, transient = _build_dense_vs_transient_bank(tmp_path)
    filtered = _filter_candidates_by_shape([dense, transient], MELODY_SHAPE_VOCAL_CHOP)
    assert filtered == [transient]


def test_pick_melody_sample_electronic_lead_reaches_sole_dense_candidate(tmp_path: Path, monkeypatch):
    """End-to-end: when the only bank file is dense/sustained (e.g. the real
    bank's "vocal_pad_angelic_voice" — long and loud, whatever its name), an
    electronic-lead-shaped MIDI still reaches it instead of being starved."""
    bank = tmp_path / "bank"
    bank.mkdir()
    only_dense = bank / "atmospheric_texture.wav"
    _tone_wav(only_dense, freq=440.0, seconds=10.0, decay=False)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    cycle = ["C4", "C5", "D4", "D5", "E4", "E5", "F4", "F5"]
    pattern = {
        "bpm": 150,
        "tracks": {
            "melody_lead": [_note(i * 0.25, cycle[i % len(cycle)], 0.25) for i in range(64)]
        },
    }
    path, _score = pick_melody_sample(pattern)
    assert path is not None
    assert path.name == only_dense.name


def test_pick_melody_sample_vocal_chop_shape_excludes_dense_texture(tmp_path: Path, monkeypatch):
    """vocal_chop is the one shape that must exclude the dense/sustained
    bucket even though it would outscore the transient file on sustain math —
    a chop needs a hit that actually stops between phrases."""
    bank, _dense, transient = _build_dense_vs_transient_bank(tmp_path)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    notes = []
    t = 0.0
    pitches = ["E4", "D4", "E4", "F4", "E4", "D4", "C4", "D4", "E4", "F4", "G4", "F4"]
    for i in range(12):
        notes.append(_note(t, pitches[i % len(pitches)], 1.5))
        t += 1.5 + 1.5
    pattern = {"bpm": 120, "tracks": {"melody_lead": notes}}

    path, _score = pick_melody_sample(pattern)
    assert path is not None
    assert path.name == transient.name


def test_pick_melody_sample_chords_shape_excludes_transient_pluck(tmp_path: Path, monkeypatch):
    bank, dense, _transient = _build_dense_vs_transient_bank(tmp_path)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    notes = []
    for i in range(8):
        t = i * 4.0
        for pitch in ("C4", "E4", "G4", "B4"):
            notes.append(_note(t, pitch, 4.0))
    pattern = {"bpm": 100, "tracks": {"melody_lead": notes}}

    path, _score = pick_melody_sample(pattern)
    assert path is not None
    assert path.name == dense.name


def test_pick_melody_sample_empty_shape_pool_falls_back_to_full_bank(tmp_path: Path, monkeypatch):
    """Chords shape but the ENTIRE bank is short/transient: the gate must not
    return no-sample-at-all — it widens back to the full pool."""
    bank = tmp_path / "bank"
    bank.mkdir()
    only_transient = bank / "short_only.wav"
    _tone_wav(only_transient, freq=440.0, seconds=1.0, decay=True)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    notes = []
    for i in range(8):
        t = i * 4.0
        for pitch in ("C4", "E4", "G4", "B4"):
            notes.append(_note(t, pitch, 4.0))
    pattern = {"bpm": 100, "tracks": {"melody_lead": notes}}

    path, _score = pick_melody_sample(pattern)
    assert path is not None
    assert path.name == only_transient.name


# --- root-pitch correction (2026-07) -----------------------------------------
# flp_writer always assumes a Sampler channel's loaded sample is rooted at C5;
# a bank file whose real tonal center is a different key (declared in its own
# filename, e.g. "..._Dmin.wav") plays back systematically off-key unless
# corrected before it reaches the Sampler.


def test_sample_key_pc_parses_filename_key_tag(tmp_path: Path):
    d_min = tmp_path / "MO_TC_vocal_pad_seagulls_Dmin.wav"
    d_min.touch()
    assert _sample_key_pc(d_min) == 2  # D

    f_maj = tmp_path / "MO_TC_vocal_pad_angelic_vocals_Fmaj.wav"
    f_maj.touch()
    assert _sample_key_pc(f_maj) == 5  # F

    untagged = tmp_path / "Cxdy_v_2_-_Kame.wav"
    untagged.touch()
    assert _sample_key_pc(untagged) is None


def test_root_correction_semitones_picks_minimal_shift():
    assert _root_correction_semitones(0) == 0  # C already rooted
    assert _root_correction_semitones(2) == -2  # D -> down 2, not up 10
    assert _root_correction_semitones(5) == -5  # F -> down 5, not up 7
    assert _root_correction_semitones(7) == 5  # G -> up 5, not down 7
    assert _root_correction_semitones(11) == 1  # B -> up 1, not down 11


def test_resolve_playback_path_leaves_untagged_files_alone(tmp_path: Path):
    path = tmp_path / "keys.wav"
    _tone_wav(path, freq=440.0, seconds=1.0)
    assert resolve_playback_path(path) == path


def test_resolve_playback_path_corrects_non_c_key_and_caches(tmp_path: Path, monkeypatch):
    cache_dir = tmp_path / "pitch_cache"
    monkeypatch.setattr("melody_sample_bank.PITCH_CACHE_DIR", cache_dir)

    path = tmp_path / "vocal_pad_seagulls_Dmin.wav"
    _tone_wav(path, freq=440.0, seconds=2.0, decay=False)

    corrected = resolve_playback_path(path)
    assert corrected != path
    assert corrected.is_file()
    assert corrected.parent == cache_dir

    # Second call reuses the cached file instead of reprocessing.
    corrected_again = resolve_playback_path(path)
    assert corrected_again == corrected


def test_pick_melody_sample_returns_root_corrected_path(tmp_path: Path, monkeypatch):
    cache_dir = tmp_path / "pitch_cache"
    monkeypatch.setattr("melody_sample_bank.PITCH_CACHE_DIR", cache_dir)

    bank = tmp_path / "bank"
    bank.mkdir()
    tagged = bank / "vocal_pad_seagulls_Dmin.wav"
    _tone_wav(tagged, freq=440.0, seconds=8.0, decay=False)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    pattern = {
        "bpm": 90,
        "tracks": {"melody_lead": [_note(i * 4.0, "C4", 4.0) for i in range(6)]},
    }
    path, _score = pick_melody_sample(pattern)
    assert path is not None
    assert path != tagged
    assert path.parent == cache_dir


# --- vibe/genre-fit scoring (2026-07) ----------------------------------------
# Sustain alone used to route every sustain-lean MIDI of every genre onto the
# same "most sustained" file regardless of vibe (reported live: a dark
# aggressive trap beat got an angelic/seagull vocal pad). A dark-toned
# candidate must now beat a bright one for a dark prompt even when both are
# equally sustained, and vice versa for a bright prompt.


def _build_dark_vs_bright_bank(tmp_path: Path) -> Path:
    bank = tmp_path / "bank"
    bank.mkdir()
    # Low frequency -> low spectral centroid ("dark"); both equally sustained
    # (long, non-decaying) so only the vibe term can break the tie.
    _tone_wav(bank / "dark_pad.wav", freq=300.0, seconds=8.0, decay=False)
    _tone_wav(bank / "bright_pad.wav", freq=5000.0, seconds=8.0, decay=False)
    return bank


def test_pick_melody_sample_prefers_dark_toned_file_for_dark_prompt(tmp_path: Path, monkeypatch):
    bank = _build_dark_vs_bright_bank(tmp_path)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    pattern = {
        "bpm": 90,
        "tracks": {"melody_lead": [_note(i * 4.0, "C4", 4.0) for i in range(6)]},
    }
    path, _score = pick_melody_sample(pattern, prompt="dark aggressive trap beat")
    assert path is not None
    assert path.name == "dark_pad.wav"


def test_pick_melody_sample_prefers_bright_toned_file_for_bright_prompt(tmp_path: Path, monkeypatch):
    bank = _build_dark_vs_bright_bank(tmp_path)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    pattern = {
        "bpm": 90,
        "tracks": {"melody_lead": [_note(i * 4.0, "C4", 4.0) for i in range(6)]},
    }
    path, _score = pick_melody_sample(pattern, prompt="bright glassy crystal melody")
    assert path is not None
    assert path.name == "bright_pad.wav"


def test_pick_melody_sample_no_prompt_ignores_vibe_scoring(tmp_path: Path, monkeypatch):
    """No prompt/style text: falls back to sustain/coverage terms only —
    both files tie on those, so the pick just needs to be deterministic."""
    bank = _build_dark_vs_bright_bank(tmp_path)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    pattern = {
        "bpm": 90,
        "tracks": {"melody_lead": [_note(i * 4.0, "C4", 4.0) for i in range(6)]},
    }
    path, _score = pick_melody_sample(pattern)
    assert path is not None
    assert path.name in ("dark_pad.wav", "bright_pad.wav")


# --- starter_kit.attach_sounds_to_pattern wiring ----------------------------


def _midi_pattern(**overrides) -> dict:
    """Default: long sustained notes (high affinity) — used for no-library cases."""
    pattern = {
        "bpm": 90,
        "style": "trap",
        "plg_midi_melody": True,
        "tracks": {
            "kick": [_note(0.0, "C1", 0.3)],
            "melody_lead": [_note(i * 4.0, "C4", 4.0) for i in range(4)],
        },
    }
    pattern.update(overrides)
    return pattern


def _plucky_midi_pattern(**overrides) -> dict:
    """Short notes → affinity below soft floor; library match should keep bank out."""
    pattern = {
        "bpm": 140,
        "style": "trap",
        "plg_midi_melody": True,
        "tracks": {
            "kick": [_note(0.0, "C1", 0.3)],
            "melody_lead": [_note(i * 0.25, "A4", 0.2) for i in range(24)],
        },
    }
    pattern.update(overrides)
    return pattern


def test_bank_not_consulted_without_midi_melody_flag():
    from starter_kit import attach_sounds_to_pattern, ensure_starter_kit

    ensure_starter_kit()
    pattern = _midi_pattern()
    del pattern["plg_midi_melody"]

    def _boom(*_a, **_kw):
        raise AssertionError("melody sample bank must not run without plg_midi_melody")

    with patch("melody_sample_bank.pick_melody_sample", side_effect=_boom):
        attach_sounds_to_pattern(pattern, None, prompt="dark trap beat")


def test_bank_consulted_when_midi_melody_and_no_user_audio_at_all():
    from starter_kit import attach_sounds_to_pattern, ensure_starter_kit

    ensure_starter_kit()
    calls = []

    def _spy(pattern, **_kwargs):
        calls.append(pattern)
        return None, 0.0

    with patch("melody_sample_bank.pick_melody_sample", side_effect=_spy):
        attach_sounds_to_pattern(_midi_pattern(), None, prompt="dark trap beat")

    assert len(calls) == 1


def test_bank_skipped_when_library_match_and_plucky_midi():
    """Low affinity + keyword library match: bank must not run (old behaviour)."""
    from starter_kit import attach_sounds_to_pattern, ensure_starter_kit

    ensure_starter_kit()
    catalog = {"root": "unused", "total": 1, "audio_total": 1, "audio": {}}

    def _decent_match(_catalog, track, _root, **_kwargs):
        if track == "melody_lead":
            return Path("some/library/lead.wav"), 40
        return None, -10_000

    def _boom(*_a, **_kw):
        raise AssertionError("melody sample bank must be skipped for plucky MIDI + library match")

    with patch("starter_kit.pick_best_for_track", side_effect=_decent_match), patch(
        "melody_sample_bank.pick_melody_sample", side_effect=_boom
    ):
        attach_sounds_to_pattern(_plucky_midi_pattern(), catalog, prompt="dark trap beat")


def test_bank_competes_and_wins_when_library_match_is_poor_sustain_fit(tmp_path: Path):
    """Sustain-lean MIDI + keyword library score=40 but short decay sample → bank wins."""
    from starter_kit import attach_sounds_to_pattern, ensure_starter_kit

    ensure_starter_kit()
    catalog = {"root": "unused", "total": 1, "audio_total": 1, "audio": {}}

    library_lead = tmp_path / "library_short_bell.wav"
    _tone_wav(library_lead, freq=880.0, seconds=1.0, decay=True)
    bank_pad = tmp_path / "bank_pad.wav"
    _tone_wav(bank_pad, freq=440.0, seconds=8.0, decay=False)

    def _decent_match(_catalog, track, _root, **_kwargs):
        if track == "melody_lead":
            return library_lead, 40
        return None, -10_000

    with patch("starter_kit.pick_best_for_track", side_effect=_decent_match), patch(
        "melody_sample_bank.pick_melody_sample", return_value=(bank_pad, 25.0)
    ), patch(
        "melody_sample_bank.score_melody_candidate",
        side_effect=lambda path, _profile, **_kwargs: 5.0 if path == library_lead else 25.0,
    ):
        sound_map = attach_sounds_to_pattern(_midi_pattern(), catalog, prompt="dark trap beat")

    assert bank_pad.stem in sound_map["melody_lead"].stem


def test_bank_consulted_when_library_melody_match_is_weak():
    from starter_kit import attach_sounds_to_pattern, ensure_starter_kit

    ensure_starter_kit()
    catalog = {"root": "unused", "total": 1, "audio_total": 1, "audio": {}}
    calls = []

    def _weak_match(_catalog, track, _root, **_kwargs):
        return None, -10_000

    def _spy(pattern, **_kwargs):
        calls.append(pattern)
        return None, 0.0

    with patch("starter_kit.pick_best_for_track", side_effect=_weak_match), patch(
        "melody_sample_bank.pick_melody_sample", side_effect=_spy
    ):
        attach_sounds_to_pattern(_midi_pattern(), catalog, prompt="dark trap beat")

    assert len(calls) == 1


def test_bank_result_lands_in_sound_map(tmp_path: Path):
    from starter_kit import attach_sounds_to_pattern, ensure_starter_kit

    ensure_starter_kit()
    # Synthetic winner — the test must not depend on the real bundled pool
    # (assets/starter/PLG_Samples no longer ships, the project issue log (not included) §0af).
    winner = tmp_path / "bank_keys.wav"
    _tone_wav(winner, freq=500.0, seconds=2.0, decay=True)

    with patch("melody_sample_bank.pick_melody_sample", return_value=(winner, 12.3)):
        sound_map = attach_sounds_to_pattern(_midi_pattern(), None, prompt="dark trap beat")

    # attach_sounds_to_pattern applies width/warmth/fx-character processing to
    # every picked sample AFTER selection (same for every track, not specific
    # to this fallback) — the final path is a processed variant that embeds
    # the original filename, not the original path itself.
    assert winner.stem in sound_map["melody_lead"].stem


def test_missing_bank_keeps_starter_melody_not_a_bank_file(tmp_path: Path, monkeypatch):
    """Shipping-build state: no bundled PLG_Samples bank (the project issue log (not included)
    §0af) → nothing from the bank is substituted. melody_lead keeps the
    starter one-shot seeded by resolve_track_samples (starter_kit.py:476),
    never an error and never a third-party file."""
    from starter_kit import attach_sounds_to_pattern

    monkeypatch.setattr(
        "melody_sample_bank.melody_sample_bank_dir", lambda: tmp_path / "no_bank"
    )
    catalog = {"root": "unused", "total": 1, "audio_total": 1, "audio": {}}

    def _weak_match(_catalog, track, _root, **_kwargs):
        return None, -10_000

    with patch("starter_kit.pick_best_for_track", side_effect=_weak_match):
        sound_map = attach_sounds_to_pattern(_midi_pattern(), catalog, prompt="dark trap beat")

    melody = sound_map.get("melody_lead")
    assert melody is not None
    assert "PLG_starter_melody" in Path(melody).stem


def test_stale_rejected_808_is_replaced_before_sound_processing(tmp_path: Path):
    """An LLM/stale map entry cannot bypass the same audio passport as search."""
    from starter_kit import attach_sounds_to_pattern

    bad = tmp_path / "slow_sub_only_808.wav"
    good = tmp_path / "healthy_808.wav"
    bad.write_bytes(b"bad")
    good.write_bytes(b"good")
    catalog = {"root": str(tmp_path), "audio_total": 2, "audio": {"808": []}}

    def _reason(path, *_args, **_kwargs):
        return "sub_only_slow_808_bass" if Path(path) == bad else None

    def _pick(_catalog, track, *_args, **_kwargs):
        return (good, 99) if track == "808_bass" else (None, -10_000)

    pattern = _midi_pattern(style="trap")
    with patch("starter_kit.resolve_track_samples", return_value={}), patch(
        "starter_kit.merge_llm_sample_picks", return_value={"808_bass": bad}
    ), patch("starter_kit.track_candidate_rejection_reason", side_effect=_reason), patch(
        "starter_kit.pick_best_for_track", side_effect=_pick
    ), patch(
        "kit_variety.save_kit_pick"
    ):
        sound_map = attach_sounds_to_pattern(pattern, catalog, library_root=tmp_path, prompt="trap")

    assert good.stem in sound_map["808_bass"].stem
    assert pattern["plg_808_rejections"][-1]["reason"] == "sub_only_slow_808_bass"


# --- Musical mutation modes (2026-07 multi-export) --------------------------


def _build_dense_and_transient_bank(tmp_path: Path) -> Path:
    bank = tmp_path / "bank"
    bank.mkdir()
    _tone_wav(bank / "dense_pad.wav", freq=300.0, seconds=8.0, decay=False)
    _tone_wav(bank / "short_pluck.wav", freq=900.0, seconds=1.0, decay=True)
    return bank


def test_pick_melody_sample_safe_mode_matches_legacy_default(tmp_path: Path, monkeypatch):
    bank = _build_dense_and_transient_bank(tmp_path)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)
    # Long mono notes → unknown shape, but sustain affinity still leans dense.
    pattern = {
        "bpm": 90,
        "tracks": {"melody_lead": [_note(i * 4.0, "C4", 4.0) for i in range(4)]},
    }
    safe_path, _ = pick_melody_sample(pattern, mode=MUTATION_SAFE)
    default_path, _ = pick_melody_sample(pattern)  # default == safe
    assert safe_path is not None and default_path is not None
    assert safe_path.name == default_path.name


def test_pick_melody_sample_contrast_picks_opposite_texture_for_fast_lead(
    tmp_path: Path, monkeypatch
):
    """Fast electronic lead → safe wants dense-or-short; contrast MUST prefer
    the short/sharp bucket (artificial tail comes from V2 mix macros)."""
    bank = _build_dense_and_transient_bank(tmp_path)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    cycle = ["C4", "C5", "D4", "D5", "E4", "E5", "F4", "F5"]
    pattern = {
        "bpm": 150,
        "tracks": {
            "melody_lead": [_note(i * 0.25, cycle[i % len(cycle)], 0.25) for i in range(64)]
        },
    }
    assert classify_melody_shape(pattern["tracks"]["melody_lead"]) == MELODY_SHAPE_ELECTRONIC_LEAD
    path, _ = pick_melody_sample(pattern, mode=MUTATION_CONTRAST)
    assert path is not None
    assert path.name == "short_pluck.wav"


def test_pick_melody_sample_chaos_is_seeded_and_root_locked(tmp_path: Path, monkeypatch):
    cache_dir = tmp_path / "pitch_cache"
    monkeypatch.setattr("melody_sample_bank.PITCH_CACHE_DIR", cache_dir)
    bank = tmp_path / "bank"
    bank.mkdir()
    # Only one file, keyed off C — chaos must still return a root-corrected path.
    tagged = bank / "chaos_pad_Dmin.wav"
    _tone_wav(tagged, freq=440.0, seconds=6.0, decay=False)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)

    pattern = {
        "bpm": 140,
        "tracks": {"melody_lead": [_note(i * 0.5, "A4", 0.4) for i in range(8)]},
    }
    a, _ = pick_melody_sample(pattern, mode=MUTATION_CHAOS, seed=7)
    b, _ = pick_melody_sample(pattern, mode=MUTATION_CHAOS, seed=7)
    assert a is not None and b is not None
    assert a == b  # same seed → same pick
    assert a.parent == cache_dir  # root-corrected (Dmin → C)


def test_pick_melody_sample_exclude_avoids_sibling_variant(tmp_path: Path, monkeypatch):
    bank = _build_dense_and_transient_bank(tmp_path)
    monkeypatch.setattr("melody_sample_bank.melody_sample_bank_dir", lambda: bank)
    pattern = {
        "bpm": 90,
        "tracks": {"melody_lead": [_note(i * 4.0, "C4", 4.0) for i in range(4)]},
    }
    first, _ = pick_melody_sample(pattern, mode=MUTATION_SAFE)
    assert first is not None
    second, _ = pick_melody_sample(
        pattern, mode=MUTATION_SAFE, exclude={str(first.resolve())}
    )
    assert second is not None
    assert second.resolve() != first.resolve()


@pytest.fixture(autouse=True)
def _melody_sample_on(monkeypatch):
    """This file tests the melody sample machinery: PLG_MELODY_SAMPLE is off by default (sound_policy), and
    the starter melody it falls back to is a substitute sound (PLG_SUBSTITUTE_SOUNDS, also off by default)."""
    monkeypatch.setenv("PLG_MELODY_SAMPLE", "on")
    monkeypatch.setenv("PLG_SUBSTITUTE_SOUNDS", "on")

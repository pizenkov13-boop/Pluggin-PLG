from pathlib import Path

import library_quality as quality


def test_tail_gate_accounts_for_pitch_playback_speed(tmp_path, monkeypatch):
    wav = tmp_path / "short_808.wav"
    wav.write_bytes(b"stub")
    monkeypatch.setattr(quality, "audio_duration_seconds", lambda path: 1.75)
    passport = {"fl_root_note": 24, "pitch_class_map": {"0": 36}}
    notes = [{"note": "C2", "length": 2.0, "velocity": 110}]

    # At 120 BPM this is one second of MIDI. C3 relative to C2 consumes two
    # seconds of source audio, so a 1.75-second WAV must be rejected.
    assert quality.bass_808_tail_rejection_reason(wav, notes, 120.0, passport) == "808_tail_too_short_for_midi"


def test_tail_gate_accepts_a_wav_that_covers_transposed_note(tmp_path, monkeypatch):
    wav = tmp_path / "long_808.wav"
    wav.write_bytes(b"stub")
    monkeypatch.setattr(quality, "audio_duration_seconds", lambda path: 2.1)
    passport = {"fl_root_note": 24, "pitch_class_map": {"0": 36}}
    notes = [{"note": "C2", "length": 2.0, "velocity": 110}]

    assert quality.bass_808_tail_rejection_reason(wav, notes, 120.0, passport) is None

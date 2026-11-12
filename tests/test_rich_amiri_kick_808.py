"""Rich Amiri kick+808 algorithm: pairing, phase invert, Soft Clipper glue."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from flp_writer import _CLIPPER_GLUE_DB, _DEFAULT_CHANNEL_VOL, _FL_VOL_MAX, _channel_level
from sample_match import partner_kick_keywords
from sample_saturate import _first_peak_sign, _read_wav, align_kick_808_phase, invert_wav_polarity


def _sine_wav(path: Path, *, sign: int, hz: float = 60.0, sr: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(sr * 0.2)) / sr
    # Force first peak polarity via a DC-offset transient click.
    x = sign * 0.9 * np.sin(2 * np.pi * hz * t)
    x[0] = sign * 0.95
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def test_partner_kick_keywords_long_808_wants_click():
    assert "click" in partner_kick_keywords("LONG_DISTORTED_808_Sub_deep.wav")
    assert "punch" in partner_kick_keywords("TRAP_JUMP_808_Distorted.wav")


def test_partner_kick_keywords_short_808_wants_fat():
    assert "fat" in partner_kick_keywords("SHORT_punch_808_tight.wav")
    assert "boom" in partner_kick_keywords("808_click_clean_stab.wav")


def test_phase_invert_flips_first_peak(tmp_path: Path):
    src = tmp_path / "kick.wav"
    dst = tmp_path / "kick_inv.wav"
    _sine_wav(src, sign=1)
    assert invert_wav_polarity(src, dst)
    audio, _, _ = _read_wav(dst)
    assert _first_peak_sign(audio) == -1


def test_align_kick_808_inverts_opposing_polarity(tmp_path: Path):
    kick = tmp_path / "kick.wav"
    bass = tmp_path / "808.wav"
    _sine_wav(kick, sign=1)
    _sine_wav(bass, sign=-1)
    out = align_kick_808_phase(
        {"kick": kick, "808_bass": bass},
        tmp_path / "_phase",
    )
    assert out["kick"] != kick
    audio, _, _ = _read_wav(out["kick"])
    assert _first_peak_sign(audio) == -1  # now matches bass


def test_align_kick_808_noop_when_same_polarity(tmp_path: Path):
    kick = tmp_path / "kick.wav"
    bass = tmp_path / "808.wav"
    _sine_wav(kick, sign=-1)
    _sine_wav(bass, sign=-1)
    out = align_kick_808_phase(
        {"kick": kick, "808_bass": bass},
        tmp_path / "_phase",
    )
    assert out["kick"] == kick


def test_clipper_glue_pushes_kick_and_808():
    kick = _channel_level("kick", {}, clipper_glue=True)
    bass = _channel_level("808_bass", {}, clipper_glue=True)
    assert kick > _DEFAULT_CHANNEL_VOL["kick"]
    assert bass > _DEFAULT_CHANNEL_VOL["808_bass"]
    assert kick <= _FL_VOL_MAX and bass <= _FL_VOL_MAX
    glue = _CLIPPER_GLUE_DB["kick"]
    assert kick == min(
        _FL_VOL_MAX,
        int(round(_DEFAULT_CHANNEL_VOL["kick"] * 10 ** (glue / 20))),
    )


def test_max_drive_overrides_clipper_glue():
    hot = _channel_level("kick", {}, max_drive=True, clipper_glue=True)
    glue_only = _channel_level("kick", {}, clipper_glue=True)
    assert hot > glue_only

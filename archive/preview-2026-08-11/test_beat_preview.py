import wave
from pathlib import Path

import numpy as np

import beat_preview
from beat_preview import render_pattern, render_preview


def _wav(path: Path, freq: float, ms: int = 200, sr: int = 32000) -> None:
    t = np.arange(int(sr * ms / 1000)) / sr
    x = (0.5 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


def _pattern(kick_path: Path, bass_path: Path) -> dict:
    return {
        "bpm": 140,
        "plg_sound_paths": {"kick": str(kick_path), "sub_808": str(bass_path)},
        "tracks": {
            "kick": [{"time_step": t, "note": "C5", "length": 0.25, "velocity": 120} for t in (0.0, 1.0, 2.0, 3.0)],
            "sub_808": [{"time_step": 0.0, "note": "C2", "length": 2.0, "velocity": 127}],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 1.0, "velocity": 100}],
        },
    }


def test_render_pattern_is_audible(tmp_path: Path):
    beat_preview._sample_cache.clear()
    kick = tmp_path / "kick.wav"
    bass = tmp_path / "bass.wav"
    _wav(kick, 80)
    _wav(bass, 55)
    stereo = render_pattern(_pattern(kick, bass))
    assert stereo.ndim == 2 and stereo.shape[1] == 2
    assert float(np.max(np.abs(stereo))) > 0.01  # not silent
    assert float(np.max(np.abs(stereo))) <= 1.0  # master clipped/normalized


def test_render_preview_returns_data_uri(tmp_path: Path):
    beat_preview._sample_cache.clear()
    kick = tmp_path / "kick.wav"
    bass = tmp_path / "bass.wav"
    _wav(kick, 80)
    _wav(bass, 55)
    out = tmp_path / "preview.wav"
    res = render_preview(_pattern(kick, bass), out)
    assert res["ok"] is True
    assert res["audio"].startswith("data:audio/wav;base64,")
    assert res["seconds"] > 0
    assert out.is_file() and out.stat().st_size > 44  # real wav, past the header


def test_render_empty_pattern_still_ok():
    res = render_preview({"bpm": 140, "tracks": {}})
    assert res["ok"] is True
    assert res["audio"].startswith("data:audio/wav;base64,")


def test_melody_without_sample_synthesizes(tmp_path: Path):
    beat_preview._sample_cache.clear()
    # no plg_sound_paths at all → melody falls back to the synth tone
    pattern = {
        "bpm": 120,
        "tracks": {"melody_lead": [{"time_step": 0.0, "note": "A4", "length": 1.0, "velocity": 110}]},
    }
    stereo = render_pattern(pattern)
    assert float(np.max(np.abs(stereo))) > 0.01


def test_final_808_bass_lane_is_audible_in_preview():
    """The generator's final lane is 808_bass, not only legacy sub_808."""
    stereo = render_pattern({
        "bpm": 140,
        "tracks": {"808_bass": [{"time_step": 0.0, "note": "C2", "length": 1.0, "velocity": 116}]},
    })
    assert float(np.max(np.abs(stereo))) > 0.01

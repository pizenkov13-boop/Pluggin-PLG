"""Mike Dean: random delay accents at select points. Not a channel-wide
effect — a decaying echo tail baked ONCE into a one-shot (sample_fx.py's
delay_tail), triggered at a random SUBSET of chorus/hook/drop landings
(fx_layer.py's materialize_delay_accent_notes) via a fourth plg_fx_kind
value riding the existing PLG FX lane (starter_kit.py) — no new channel,
same lane already multiplexed between riser_from_chords (trap) and
alt_bass_spins (Detroit). Scoped to "toliver", not "trap": verified trap's
fx lane is already claimed by the shipped P.1 riser."""

from __future__ import annotations

import random

import numpy as np

from fx_layer import DELAY_ACCENT_PROBABILITY, materialize_delay_accent_notes
from genre_profiles import PROFILES
from sample_fx import delay_tail, process_file_delay_accent
from sample_saturate import _read_wav, _write_wav16

SR = 44100


def _tone(freq: float = 440.0, seconds: float = 0.3, amp: float = 0.6) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


class _FixedRNG:
    """Deterministic stand-in for random.Random — avoids statistical/flaky
    probability tests: a fixed .random() return always accepts or rejects."""

    def __init__(self, value: float) -> None:
        self._value = value

    def random(self) -> float:
        return self._value


# --- delay_tail (DSP) --------------------------------------------------


def test_delay_tail_extends_the_buffer():
    x = _tone(seconds=0.2)
    out = delay_tail(x, SR, delay_ms=100.0, repeats=3)
    delay_samples = int(SR * 100.0 / 1000.0)
    assert out.shape[0] == x.shape[0] + delay_samples * 3


def test_delay_tail_echoes_decay():
    x = _tone(seconds=0.05)
    delay_ms = 50.0
    out = delay_tail(x, SR, delay_ms=delay_ms, feedback=0.5, repeats=3, wet_mix=0.6)
    delay_samples = int(SR * delay_ms / 1000.0)
    n = x.shape[0]
    peaks = [
        float(np.max(np.abs(out[i * delay_samples : i * delay_samples + n])))
        for i in range(1, 4)
    ]
    assert peaks[0] > peaks[1] > peaks[2]  # each successive echo is quieter


def test_delay_tail_empty_input_is_a_noop():
    x = np.array([], dtype=np.float64)
    out = delay_tail(x, SR)
    assert out.shape[0] == 0


def test_delay_tail_does_not_clip():
    x = _tone(seconds=0.1, amp=0.95)
    out = delay_tail(x, SR, feedback=0.8, repeats=6, wet_mix=0.9)
    assert np.max(np.abs(out)) <= 1.0 + 1e-6


# --- process_file_delay_accent (bake) -----------------------------------


def test_process_file_delay_accent_end_to_end(tmp_path):
    src = tmp_path / "chords.wav"
    _write_wav16(src, _tone(seconds=0.3), SR)
    dst = tmp_path / "chords_delay.wav"

    ok = process_file_delay_accent(src, dst)
    assert ok
    assert dst.is_file()
    audio, sr, _ch = _read_wav(dst)
    assert sr == SR
    assert audio.shape[0] > 0
    assert np.max(np.abs(audio)) <= 1.0


def test_process_file_delay_accent_missing_source_returns_false(tmp_path):
    src = tmp_path / "missing.wav"
    dst = tmp_path / "out.wav"
    assert process_file_delay_accent(src, dst) is False
    assert not dst.is_file()


# --- materialize_delay_accent_notes (placement) --------------------------


def _arrangement():
    return [
        {"name": "verse", "start_bar": 0, "bars": 8},
        {"name": "chorus", "start_bar": 8, "bars": 8},
        {"name": "verse", "start_bar": 16, "bars": 8},
        {"name": "hook", "start_bar": 24, "bars": 8},
    ]


def test_delay_accent_only_lands_on_chorus_hook_drop_when_rng_always_accepts():
    data = {"plg_arrangement": _arrangement(), "tracks": {}}
    placed = materialize_delay_accent_notes(data, rng=_FixedRNG(0.0))
    assert placed == 2  # one chorus, one hook — verse sections never qualify
    assert data["plg_fx_kind"] == "delay_accent"


def test_delay_accent_places_nothing_when_rng_always_rejects():
    data = {"plg_arrangement": _arrangement(), "tracks": {}}
    placed = materialize_delay_accent_notes(data, rng=_FixedRNG(0.999))
    assert placed == 0
    assert "fx" not in data["tracks"]


def test_delay_accent_skips_when_fx_already_claimed():
    data = {
        "plg_arrangement": _arrangement(),
        "tracks": {"fx": [{"time_step": 0.0, "note": "C5", "length": 1.0, "velocity": 80}]},
    }
    assert materialize_delay_accent_notes(data, rng=_FixedRNG(0.0)) == 0


def test_delay_accent_noop_without_chorus_hook_drop_sections():
    data = {
        "plg_arrangement": [{"name": "verse", "start_bar": 0, "bars": 8}],
        "tracks": {},
    }
    assert materialize_delay_accent_notes(data, rng=_FixedRNG(0.0)) == 0


def test_delay_accent_note_lands_one_bar_before_the_landing():
    data = {
        "plg_arrangement": [{"name": "chorus", "start_bar": 8, "bars": 8}],
        "tracks": {},
    }
    materialize_delay_accent_notes(data, rng=_FixedRNG(0.0))
    assert data["tracks"]["fx"][0]["time_step"] == 7 * 4.0


def test_delay_accent_same_seed_reproduces_same_placement():
    data_a = {"plg_arrangement": _arrangement(), "tracks": {}}
    data_b = {"plg_arrangement": _arrangement(), "tracks": {}}
    placed_a = materialize_delay_accent_notes(data_a, rng=random.Random(42))
    placed_b = materialize_delay_accent_notes(data_b, rng=random.Random(42))
    assert placed_a == placed_b
    assert data_a["tracks"].get("fx") == data_b["tracks"].get("fx")


def test_delay_accent_probability_constant_is_reasonable():
    assert 0.0 < DELAY_ACCENT_PROBABILITY < 1.0


# --- genre gating ---------------------------------------------------------


def test_toliver_profile_has_delay_accent_lane_enabled():
    assert PROFILES["toliver"].delay_accent_lane is True
    for name, other in PROFILES.items():
        if name == "toliver":
            continue
        assert other.delay_accent_lane is False, name

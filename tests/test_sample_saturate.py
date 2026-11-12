"""Outlet's "selective distortion bus" trick, baked into tanh_warm's pre_hp_hz.

No new mixer-bus topology: the WET (saturated) component is high-passed before
the tanh curve while the DRY component keeps its full low end, so a bass-heavy
layer (808) can be parallel-blended with a "dirty" copy without the copy's sub
content phase-smearing against the clean original.
"""

from __future__ import annotations

import numpy as np

from sample_saturate import SATURATION, _unpack_recipe, saturate_file, tanh_warm

SR = 44100


def _tone(freq: float = 440.0, seconds: float = 0.3, amp: float = 0.6) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


def test_pre_hp_zero_matches_legacy_full_band_behaviour():
    x = _tone(220.0)
    legacy = np.tanh(2.0 * x) / np.tanh(2.0)
    legacy_blend = 0.4 * x + 0.6 * legacy
    out = tanh_warm(x, 2.0, 0.6, pre_hp_hz=0.0, sr=SR)
    assert np.allclose(out, legacy_blend, atol=1e-9)


def test_pre_hp_keeps_deep_sub_close_to_dry():
    # 20 Hz — deep 808 territory, well below the 200 Hz cutoff. The wet
    # component's sub content collapses toward ~0, so output should sit much
    # closer to a dry blend than a full-band saturated blend would.
    x = _tone(20.0, seconds=0.5)
    mix = 0.6
    with_hp = tanh_warm(x, 2.2, mix, pre_hp_hz=200.0, sr=SR)
    full_band = tanh_warm(x, 2.2, mix, pre_hp_hz=0.0, sr=SR)
    dry_only = (1.0 - mix) * x

    dist_hp = float(np.sqrt(np.mean((with_hp - dry_only) ** 2)))
    dist_full = float(np.sqrt(np.mean((full_band - dry_only) ** 2)))
    assert dist_hp < dist_full * 0.35  # much closer to dry than the full-band wet blend
    assert dist_full > 0  # sanity: full-band saturation actually did distort the sub


def test_pre_hp_barely_changes_high_frequency_saturation():
    # 4 kHz — well above the 200 Hz cutoff. RMS comparison (not per-sample —
    # the one-pole filter has a startup transient) matches the low_cut test
    # convention in tests/test_sample_fx.py.
    x = _tone(4000.0, seconds=0.2)
    with_hp = tanh_warm(x, 2.2, 0.6, pre_hp_hz=200.0, sr=SR)
    full_band = tanh_warm(x, 2.2, 0.6, pre_hp_hz=0.0, sr=SR)
    rms_diff = float(np.sqrt(np.mean((with_hp - full_band) ** 2)))
    rms_full = float(np.sqrt(np.mean(full_band**2)))
    assert rms_diff < rms_full * 0.15


def test_sub_808_is_not_warm_baked_by_default():
    """808 must load the library wav — no tanh/warm copy in the FLP path."""
    assert "sub_808" not in SATURATION
    assert "808_bass" not in SATURATION
    for _track, recipe in SATURATION.items():
        assert recipe[2] == 0.0


def test_unpack_recipe_accepts_legacy_two_tuples():
    assert _unpack_recipe((1.5, 0.4)) == (1.5, 0.4, 0.0)
    assert _unpack_recipe((1.5, 0.4, 150.0)) == (1.5, 0.4, 150.0)
    assert _unpack_recipe(()) == (0.0, 0.0, 0.0)


def test_saturate_file_end_to_end_with_pre_hp(tmp_path):
    from sample_saturate import _read_wav, _write_wav16

    src = tmp_path / "808.wav"
    _write_wav16(src, _tone(45.0, seconds=0.3), SR)
    dst = tmp_path / "808_warm.wav"

    ok = saturate_file(src, dst, drive=2.2, mix=0.6, pre_hp_hz=200.0)
    assert ok
    assert dst.is_file()
    warmed, sr, _ch = _read_wav(dst)
    assert sr == SR
    assert warmed.shape[0] > 0
    assert np.max(np.abs(warmed)) <= 1.0

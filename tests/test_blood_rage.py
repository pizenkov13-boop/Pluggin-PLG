"""Blood-style rage video: two confirmed gaps closed on the existing rage
profile — a phaser-like flanger in the FX chain (P.4) and light, controlled
saturation on hi_hats (P.5). Phrygian scale itself was already in place."""

from __future__ import annotations

import numpy as np

from genre_profiles import PROFILES
from sample_fx import MELODY_FX_RECIPES
from sample_saturate import SATURATION, _read_wav, _write_wav16, saturate_sound_map

SR = 44100


def _tone(freq: float = 440.0, seconds: float = 0.3, amp: float = 0.6) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


def test_rage_scale_is_already_phrygian():
    assert PROFILES["rage"].melody_scale == "phrygian"


def test_rage_fx_chain_now_has_a_slow_flanger_after_the_distortion():
    chain = MELODY_FX_RECIPES["rage"]
    names = [name for name, _params in chain]
    assert names[-1] == "flanger"  # applied on top of the distortion, not before it
    assert names[:3] == ["hard_clip", "bitcrush", "gate_tail"]  # existing chain untouched
    flanger_params = dict(chain[-1][1])
    assert flanger_params["rate_hz"] < 0.5  # slow, phaser-like sweep, not psychedelic-fast


def test_rage_opts_into_hi_hats_light_saturation_flag():
    assert PROFILES["rage"].hi_hats_light_saturation is True
    for name, other in PROFILES.items():
        if name == "rage":
            continue
        assert other.hi_hats_light_saturation is False, name


def test_global_hi_hats_saturation_default_is_still_zero():
    # The blanket SATURATION table stays untouched — rage gets its warmth via
    # a per-call override, not a change to what every other genre inherits.
    assert SATURATION["hi_hats"] == (0.0, 0.0, 0.0)


def test_hi_hats_override_actually_warms_the_sample(tmp_path):
    src = tmp_path / "hats.wav"
    _write_wav16(src, _tone(seconds=0.2), SR)
    sm = {"hi_hats": src}

    untouched = saturate_sound_map(sm, tmp_path / "c0")
    assert untouched["hi_hats"] == src  # (0,0,0) -> pass-through, same path

    warmed = saturate_sound_map(sm, tmp_path / "c1", overrides={"hi_hats": (0.8, 0.15, 0.0)})
    assert warmed["hi_hats"] != src
    audio, sr, _ch = _read_wav(warmed["hi_hats"])
    assert sr == SR
    assert audio.shape[0] > 0

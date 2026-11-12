"""Melody character FX baked into samples: DSP sanity + recipe routing."""

from __future__ import annotations

import numpy as np
import pytest

from sample_fx import (
    MELODY_FX_RECIPES,
    _match_length,
    _scale_chain,
    _stable_seed,
    apply_chain,
    apply_stereo_split,
    bitcrush,
    flanger,
    gate_tail,
    hard_clip,
    interval_stack,
    low_cut,
    lpf_sweep,
    melody_fx_sound_map,
    mono_below,
    pitch_punch,
    process_file_reverse_chop,
    process_file_stereo,
    reverse_chop,
    reverse_tail,
    ring_mod,
    sub_808_character_sound_map,
    unison_octave_stack,
)
from sample_saturate import _read_wav, _write_wav16

SR = 44100


def _tone(freq: float = 440.0, seconds: float = 0.5, amp: float = 0.6) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


def _crest(x: np.ndarray) -> float:
    return float(np.max(np.abs(x)) / (np.sqrt(np.mean(x**2)) + 1e-12))


def test_hard_clip_squares_the_wave():
    x = _tone()
    y = hard_clip(x, drive=3.0, mix=1.0)
    assert y.shape == x.shape
    assert np.max(np.abs(y)) <= 0.951
    assert _crest(y) < _crest(x)  # square-ish = lower crest factor
    assert not np.any(np.isnan(y))


def test_bitcrush_adds_quantization_dirt():
    x = _tone()
    y = bitcrush(x, SR, factor=6, bits=8, mix=1.0)
    assert y.shape == x.shape
    assert not np.allclose(y, x)
    assert np.max(np.abs(y)) <= 0.951


def test_ring_mod_creates_sidebands():
    x = _tone(440.0)
    y = ring_mod(x, SR, freq=40.0, mix=1.0)
    spec_x = np.abs(np.fft.rfft(x))
    spec_y = np.abs(np.fft.rfft(y))
    freqs = np.fft.rfftfreq(x.shape[0], 1 / SR)
    band = (freqs > 460) & (freqs < 500)  # 440+40 sideband
    assert spec_y[band].max() > spec_x[band].max() * 5


def test_flanger_and_sweep_and_reverse_keep_shape():
    x = _tone(seconds=0.4)
    for y in (
        flanger(x, SR, mix=0.3),
        lpf_sweep(x, SR, mix=0.6),
        reverse_tail(x, mix=0.25),
    ):
        assert y.shape == x.shape
        assert np.max(np.abs(y)) <= 0.951
        assert not np.any(np.isnan(y))
        assert not np.allclose(y, x)


def test_gate_kills_quiet_tail():
    loud = _tone(seconds=0.2)
    quiet = _tone(seconds=0.2, amp=0.001)
    x = np.concatenate([loud, quiet])
    y = gate_tail(x, SR, thresh_db=-40.0)
    tail = y[-int(0.1 * SR):]
    assert float(np.max(np.abs(tail))) < 0.0005


def test_lane_scaling_drops_weak_effects():
    chain = MELODY_FX_RECIPES["rage"]
    chords_chain = _scale_chain(chain, 0.3)
    assert len(chords_chain) < len(chain) or all(
        p.get("mix", 1) <= c[1].get("mix", 1) for (_, p), c in zip(chords_chain, chain)
    )


@pytest.mark.parametrize("genre", ["rage", "opium", "trap", "phonk"])
def test_recipe_chains_run_end_to_end(genre: str):
    x = _tone(seconds=0.3)
    y = apply_chain(x, SR, MELODY_FX_RECIPES[genre])
    assert y.shape == x.shape
    assert 0.05 < float(np.max(np.abs(y))) <= 0.951
    assert not np.any(np.isnan(y))


def test_sound_map_bakes_and_caches(tmp_path):
    src = tmp_path / "lead_bell.wav"
    _write_wav16(src, _tone(seconds=0.3), SR)
    cache = tmp_path / "cache"
    sm = {"melody_lead": src, "kick": src}

    out = melody_fx_sound_map(sm, cache, genre="rage")
    assert out["kick"] == src  # drums untouched
    baked = out["melody_lead"]
    assert baked != src and baked.is_file()
    audio, sr, _ = _read_wav(baked)
    assert sr == SR and audio.shape[0] > 0

    mtime = baked.stat().st_mtime_ns
    again = melody_fx_sound_map(sm, cache, genre="rage")
    assert again["melody_lead"] == baked
    assert baked.stat().st_mtime_ns == mtime  # cache hit, no rewrite


def test_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PLG_MELODY_FX", "off")
    src = tmp_path / "lead.wav"
    _write_wav16(src, _tone(seconds=0.2), SR)
    out = melody_fx_sound_map({"melody_lead": src}, tmp_path / "c", genre="rage")
    assert out["melody_lead"] == src


def test_interval_stack_layers_pitch_shifted_copy():
    x = _tone(440.0, seconds=0.4)
    y = interval_stack(x, SR, semitones=-5.0, mix=0.3)
    assert y.shape == x.shape
    assert np.max(np.abs(y)) <= 0.951
    assert not np.any(np.isnan(y))
    spec_x = np.abs(np.fft.rfft(x))
    spec_y = np.abs(np.fft.rfft(y))
    freqs = np.fft.rfftfreq(x.shape[0], 1 / SR)
    # -5 semitones from 440 Hz ~= 329.6 Hz — that band should gain energy.
    band = (freqs > 315) & (freqs < 345)
    assert spec_y[band].max() > spec_x[band].max() * 3


def test_unison_octave_stack_adds_octave_up_and_down_energy():
    # Jay Cactus "West Coast piano" — copy the melody an octave up AND down,
    # both from the SAME source (not cascaded through interval_stack twice).
    x = _tone(440.0, seconds=0.4)
    y = unison_octave_stack(x, SR, mix_up=0.35, mix_down=0.35)
    assert y.shape == x.shape
    assert np.max(np.abs(y)) <= 0.951
    assert not np.any(np.isnan(y))
    spec_x = np.abs(np.fft.rfft(x))
    spec_y = np.abs(np.fft.rfft(y))
    freqs = np.fft.rfftfreq(x.shape[0], 1 / SR)
    up_band = (freqs > 850) & (freqs < 910)     # ~880 Hz, +12 semitones
    down_band = (freqs > 205) & (freqs < 235)   # ~220 Hz, -12 semitones
    assert spec_y[up_band].max() > spec_x[up_band].max() * 3
    assert spec_y[down_band].max() > spec_x[down_band].max() * 3


def test_unison_octave_stack_skips_too_short_input():
    x = np.zeros(4)
    y = unison_octave_stack(x, SR)
    assert np.array_equal(y, x)


def test_west_coast_fx_recipe_uses_unison_stack_then_chorus_flanger():
    chain = MELODY_FX_RECIPES["west_coast"]
    names = [name for name, _params in chain]
    assert names == ["unison_octave_stack", "flanger"]
    flanger_params = dict(chain[1][1])
    # chorus territory (15-35 ms), not the psychedelic-flanger territory
    # (~3-6 ms) used by the other genre chains in this file.
    assert 15.0 <= flanger_params["depth_ms"] <= 35.0


def test_low_cut_removes_low_frequency_energy():
    x = _tone(60.0, seconds=0.3)  # deep sub tone, well below the cutoff
    y = low_cut(x, SR, hz=300.0, mix=1.0)
    assert y.shape == x.shape
    assert np.max(np.abs(y)) <= 1.0
    rms_x = float(np.sqrt(np.mean(x**2)))
    rms_y = float(np.sqrt(np.mean(y**2)))
    assert rms_y < rms_x * 0.5


def test_low_cut_leaves_high_frequency_mostly_untouched():
    x = _tone(4000.0, seconds=0.3)  # well above the cutoff
    y = low_cut(x, SR, hz=300.0, mix=1.0)
    rms_x = float(np.sqrt(np.mean(x**2)))
    rms_y = float(np.sqrt(np.mean(y**2)))
    assert rms_y > rms_x * 0.85


def test_sample_realism_and_chord_layer_are_additive_bonuses(tmp_path, monkeypatch):
    src = tmp_path / "chords_pad.wav"
    _write_wav16(src, _tone(seconds=0.3), SR)
    sm = {"chords": src, "melody_lead": src}

    monkeypatch.setenv("PLG_SAMPLE_REALISM", "off")
    monkeypatch.setenv("PLG_CHORD_INTERVAL_LAYER", "off")
    baseline = melody_fx_sound_map(sm, tmp_path / "c0", genre="trap")

    monkeypatch.setenv("PLG_SAMPLE_REALISM", "on")
    monkeypatch.setenv("PLG_CHORD_INTERVAL_LAYER", "on")
    bonus = melody_fx_sound_map(sm, tmp_path / "c1", genre="trap")

    audio_chords, sr, _ = _read_wav(bonus["chords"])
    audio_baseline_chords, _, _ = _read_wav(baseline["chords"])
    assert audio_chords.shape == audio_baseline_chords.shape
    assert not np.allclose(audio_chords, audio_baseline_chords)

    audio_lead, _, _ = _read_wav(bonus["melody_lead"])
    audio_baseline_lead, _, _ = _read_wav(baseline["melody_lead"])
    assert not np.allclose(audio_lead, audio_baseline_lead)


def test_melody_delay_layer_applies_only_to_melody_lead(tmp_path, monkeypatch):
    # Dirkey "We Don't Trust You" double piano: upper/lead notes get a tail
    # delay, root/bed notes (chords) don't.
    src = tmp_path / "pad.wav"
    _write_wav16(src, _tone(seconds=0.3), SR)
    sm = {"chords": src, "melody_lead": src}

    monkeypatch.setenv("PLG_MELODY_DELAY_LAYER", "off")
    monkeypatch.setenv("PLG_SAMPLE_REALISM", "off")
    monkeypatch.setenv("PLG_CHORD_INTERVAL_LAYER", "off")
    baseline = melody_fx_sound_map(sm, tmp_path / "c0", genre="trap")

    monkeypatch.setenv("PLG_MELODY_DELAY_LAYER", "on")
    bonus = melody_fx_sound_map(sm, tmp_path / "c1", genre="trap")

    lead_bonus, _sr, _ch = _read_wav(bonus["melody_lead"])
    lead_baseline, _, _ = _read_wav(baseline["melody_lead"])
    assert not np.allclose(lead_bonus, lead_baseline)  # melody_lead got the delay bonus

    chords_bonus, _, _ = _read_wav(bonus["chords"])
    chords_baseline, _, _ = _read_wav(baseline["chords"])
    assert np.allclose(chords_bonus, chords_baseline)  # chords (root/bed) untouched


def test_reverse_chop_with_one_chop_is_a_plain_reverse():
    x = np.linspace(-0.5, 0.5, 200)
    y = reverse_chop(x, SR, chop_count=1, seed=0)
    assert y.shape == x.shape
    assert np.allclose(y, x[::-1], atol=1e-6)


def test_reverse_chop_reorders_segments_when_chopped():
    x = np.linspace(-0.5, 0.5, 200)
    y = reverse_chop(x, SR, chop_count=4, seed=1)
    assert y.shape == x.shape
    assert not np.allclose(y, x[::-1])  # segments got reshuffled, not a plain reverse


def test_reverse_chop_deterministic_same_seed_differs_other_seed():
    x = np.linspace(-0.5, 0.5, 200)
    a = reverse_chop(x, SR, chop_count=4, seed=7)
    b = reverse_chop(x, SR, chop_count=4, seed=7)
    c = reverse_chop(x, SR, chop_count=4, seed=8)
    assert np.array_equal(a, b)
    assert not np.allclose(a, c)


def test_reverse_chop_mixes_in_a_second_layer():
    x = np.linspace(-0.5, 0.5, 200)
    layer = np.sin(np.linspace(0, 20, 200))
    without = reverse_chop(x, SR, chop_count=2, seed=0)
    with_layer = reverse_chop(x, SR, layer=layer, layer_mix=0.5, chop_count=2, seed=0)
    assert with_layer.shape == x.shape
    assert not np.allclose(without, with_layer)


def test_reverse_chop_skips_too_short_input():
    x = np.zeros(4)
    assert np.array_equal(reverse_chop(x, SR), x)


def test_match_length_truncates_and_zero_pads():
    a = np.arange(5)
    assert np.array_equal(_match_length(a, 3), np.array([0, 1, 2]))
    assert np.array_equal(_match_length(a, 8), np.array([0, 1, 2, 3, 4, 0, 0, 0]))


def test_stable_seed_is_deterministic_and_path_specific():
    from pathlib import Path

    assert _stable_seed(Path("a.wav")) == _stable_seed(Path("a.wav"))
    assert _stable_seed(Path("a.wav")) != _stable_seed(Path("b.wav"))


def test_process_file_reverse_chop_end_to_end(tmp_path):
    src = tmp_path / "chords.wav"
    _write_wav16(src, _tone(seconds=0.3), SR)
    dst = tmp_path / "chords_rc.wav"

    ok = process_file_reverse_chop(src, dst, chop_count=6, seed=3)
    assert ok
    assert dst.is_file()
    warmed, sr, _ch = _read_wav(dst)
    assert sr == SR
    assert warmed.shape[0] > 0
    assert np.max(np.abs(warmed)) <= 1.0


def test_melody_fx_sound_map_reverse_chop_requires_both_flag_and_env(tmp_path, monkeypatch):
    # Dirkey Destroy Lonely: mixes melody_lead into chords, reverses, re-chops.
    # Gated by BOTH the caller's chords_reverse_chop=True (GenreProfile-driven)
    # AND the PLG_REVERSE_CHOP env kill switch — neither alone is enough.
    src = tmp_path / "pad.wav"
    _write_wav16(src, _tone(220.0, seconds=0.3), SR)
    lead_src = tmp_path / "lead.wav"
    _write_wav16(lead_src, _tone(880.0, seconds=0.3), SR)
    sm = {"chords": src, "melody_lead": lead_src}

    monkeypatch.setenv("PLG_REVERSE_CHOP", "off")
    off_by_env = melody_fx_sound_map(sm, tmp_path / "c0", genre="opium", chords_reverse_chop=True)
    assert "reversechop" not in off_by_env["chords"].name

    monkeypatch.setenv("PLG_REVERSE_CHOP", "on")
    off_by_flag = melody_fx_sound_map(sm, tmp_path / "c1", genre="opium", chords_reverse_chop=False)
    assert "reversechop" not in off_by_flag["chords"].name

    both_on = melody_fx_sound_map(sm, tmp_path / "c2", genre="opium", chords_reverse_chop=True)
    assert "reversechop" in both_on["chords"].name

    # melody_lead itself is untouched by the chords-only reverse-chop bonus
    assert "reversechop" not in both_on["melody_lead"].name


def test_apply_stereo_split_diverges_left_and_right():
    x = _tone(seconds=0.3)
    left_chain = []  # dry
    right_chain = [("hard_clip", {"drive": 3.0, "mix": 1.0})]
    y = apply_stereo_split(x, SR, left_chain, right_chain)
    assert y.ndim == 2 and y.shape[1] == 2
    assert np.allclose(y[:, 0], x)  # left untouched (empty chain)
    assert not np.allclose(y[:, 1], x)  # right got the extra distortion
    assert not np.allclose(y[:, 0], y[:, 1])


def test_process_file_stereo_writes_stereo_wav(tmp_path):
    src = tmp_path / "chords.wav"
    _write_wav16(src, _tone(seconds=0.2), SR)
    dst = tmp_path / "chords_stereo.wav"
    ok = process_file_stereo(src, dst, [], [("hard_clip", {"drive": 3.0, "mix": 1.0})])
    assert ok and dst.is_file()
    audio, sr, channels = _read_wav(dst)
    assert channels == 2
    assert audio.shape[1] == 2
    assert not np.allclose(audio[:, 0], audio[:, 1])


def test_chords_lane_bakes_stereo_asymmetry_by_default(tmp_path):
    src = tmp_path / "chords_pad.wav"
    _write_wav16(src, _tone(seconds=0.3), SR)
    out = melody_fx_sound_map({"chords": src, "melody_lead": src}, tmp_path / "c", genre="trap")
    audio_chords, sr, channels_chords = _read_wav(out["chords"])
    assert channels_chords == 2  # chords lane got the stereo asymmetry bake
    audio_lead, _, channels_lead = _read_wav(out["melody_lead"])
    assert channels_lead == 1  # melody_lead untouched by the stereo bonus


def test_stereo_asymmetry_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PLG_STEREO_ASYMMETRY", "off")
    src = tmp_path / "chords_pad.wav"
    _write_wav16(src, _tone(seconds=0.2), SR)
    out = melody_fx_sound_map({"chords": src}, tmp_path / "c", genre="trap")
    _, _, channels = _read_wav(out["chords"])
    assert channels == 1  # stayed mono — no stereo bake without the toggle


def test_sample_realism_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PLG_SAMPLE_REALISM", "off")
    monkeypatch.setenv("PLG_CHORD_INTERVAL_LAYER", "off")
    src = tmp_path / "lead.wav"
    _write_wav16(src, _tone(seconds=0.2), SR)
    cache = tmp_path / "c"
    out = melody_fx_sound_map({"melody_lead": src}, cache, genre="rage")
    baked = out["melody_lead"]
    audio_off, sr, _ = _read_wav(baked)

    monkeypatch.setenv("PLG_SAMPLE_REALISM", "on")
    monkeypatch.setenv("PLG_CHORD_INTERVAL_LAYER", "on")
    out2 = melody_fx_sound_map({"melody_lead": src}, cache, genre="rage")
    assert out2["melody_lead"] != baked  # different cache tag, not stale reuse
    audio_on, _, _ = _read_wav(out2["melody_lead"])
    assert not np.allclose(audio_off, audio_on[: audio_off.shape[0]])


def _zero_crossings(segment: np.ndarray) -> int:
    signs = np.sign(segment)
    signs[signs == 0] = 1
    return int(np.sum(np.abs(np.diff(signs)) > 0))


def test_pitch_punch_onset_reads_higher_pitch_than_original():
    # cumulative resampling means the tail ends up phase-shifted rather than
    # amplitude-close to the dry signal, so compare instantaneous frequency
    # (zero-crossing rate) at the onset instead of a pointwise diff.
    x = _tone(220.0, seconds=0.3)
    y = pitch_punch(x, SR, semitones=8.0, duration_ms=25.0)
    assert y.shape == x.shape
    assert np.max(np.abs(y)) <= 0.951
    assert not np.any(np.isnan(y))
    window = int(SR * 0.008)  # 8ms — well inside the 25ms punch window
    assert _zero_crossings(y[:window]) > _zero_crossings(x[:window])


def test_pitch_punch_short_sample_passthrough():
    x = np.zeros(4)
    assert np.array_equal(pitch_punch(x, SR), x)


def _stereo_tone(freq: float, seconds: float, *, out_of_phase: bool) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    left = 0.4 * np.sin(2 * np.pi * freq * t)
    right = -left if out_of_phase else left
    return np.stack([left, right], axis=1)


def test_mono_below_removes_low_side_keeps_high_side():
    low = _stereo_tone(60.0, 0.3, out_of_phase=True)  # pure "side" energy, low freq
    high = _stereo_tone(4000.0, 0.3, out_of_phase=True)  # pure "side" energy, high freq
    low_out = mono_below(low, SR, crossover_hz=300.0)
    high_out = mono_below(high, SR, crossover_hz=300.0)

    low_side_before = float(np.sqrt(np.mean(((low[:, 0] - low[:, 1]) / 2) ** 2)))
    low_side_after = float(np.sqrt(np.mean(((low_out[:, 0] - low_out[:, 1]) / 2) ** 2)))
    high_side_before = float(np.sqrt(np.mean(((high[:, 0] - high[:, 1]) / 2) ** 2)))
    high_side_after = float(np.sqrt(np.mean(((high_out[:, 0] - high_out[:, 1]) / 2) ** 2)))

    assert low_side_after < low_side_before * 0.3  # low-freq width collapsed toward mono
    assert high_side_after > high_side_before * 0.85  # high-freq width survives


def test_mono_below_passes_mono_input_through_unchanged():
    x = _tone(seconds=0.2)
    assert np.array_equal(mono_below(x, SR), x)


def test_sub_808_character_bakes_only_sub_808(tmp_path):
    src = tmp_path / "eight_oh_eight.wav"
    _write_wav16(src, _tone(55.0, seconds=0.3), SR)
    sm = {"sub_808": src, "kick": src}
    out = sub_808_character_sound_map(sm, tmp_path / "c")
    assert out["kick"] == src  # untouched — this only ever bakes sub_808
    baked = out["sub_808"]
    assert baked != src and baked.is_file()

    mtime = baked.stat().st_mtime_ns
    again = sub_808_character_sound_map(sm, tmp_path / "c")
    assert again["sub_808"] == baked
    assert baked.stat().st_mtime_ns == mtime  # cache hit, no rewrite


def test_sub_808_character_kill_switches(tmp_path, monkeypatch):
    monkeypatch.setenv("PLG_808_PUNCH", "off")
    monkeypatch.setenv("PLG_808_MONO_BELOW", "off")
    src = tmp_path / "eight_oh_eight.wav"
    _write_wav16(src, _tone(55.0, seconds=0.2), SR)
    out = sub_808_character_sound_map({"sub_808": src}, tmp_path / "c")
    assert out["sub_808"] == src

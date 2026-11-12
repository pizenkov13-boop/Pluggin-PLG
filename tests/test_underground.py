"""Dirkey "How To Make Underground Beats": new genre profile (BPM ~162, natural
minor), ring modulation as the signature lead FX, 808 root-delay-tension, and
reversed open-hat baking (reuses process_file_reverse_chop, chop_count=1)."""

from __future__ import annotations

from beat_humanize import apply_root_delay_tension
from genre_profiles import HIPHOP_LANES, PROFILES, detect_genre
from sample_fx import MELODY_FX_RECIPES
from style_corpus import BPM_RANGES, bpm_range


def test_genre_detection_routes_underground_keywords():
    assert detect_genre(prompt="underground beats") == "underground"
    assert detect_genre(prompt="underground trap type beat") == "underground"
    assert detect_genre(prompt="андеграунд бит") == "underground"


def test_underground_trap_no_longer_falls_back_to_rage():
    # "underground trap" used to be a rage keyword — moved to its own genre.
    assert detect_genre(prompt="underground trap") == "underground"


def test_underground_is_in_the_mvp_hiphop_lane():
    assert "underground" in HIPHOP_LANES


def test_underground_bpm_covers_162():
    lo, hi = bpm_range("underground")
    assert lo <= 162 <= hi
    assert BPM_RANGES["underground"][0] >= 140  # fast end of the trap family


def test_underground_bpm_widened_to_cover_nettspend_150():
    lo, hi = bpm_range("underground")
    assert lo <= 150 <= hi
    assert BPM_RANGES["underground"][0] == 148  # explicit floor, not just incidentally in range


def test_underground_profile_flags_and_scale():
    profile = PROFILES["underground"]
    assert profile.melody_scale == "natural_minor"  # not phrygian, unlike rage/opium
    assert profile.sub_808_root_delay_tension is True
    assert profile.hi_hats_reverse_open is True
    assert profile.kick_808_duck is True


def test_only_underground_has_its_two_signature_flags():
    for name, other in PROFILES.items():
        if name == "underground":
            continue
        assert other.sub_808_root_delay_tension is False, name
        assert other.hi_hats_reverse_open is False, name


def test_underground_fx_chain_uses_ring_mod_prominently():
    chain = MELODY_FX_RECIPES["underground"]
    names = [name for name, _params in chain]
    assert "ring_mod" in names
    ring_params = dict(next(p for n, p in chain if n == "ring_mod"))
    # louder/more prominent than opium's subtle background buzz (mix 0.22)
    assert ring_params["mix"] > 0.3


def test_sub_808_root_delay_tension_reuses_the_same_move_as_melody():
    # Same mechanism as Destroy Lonely's melody version, applied to the bass.
    notes = [
        {"time_step": 0.0, "note": "A2", "length": 2.0, "velocity": 120},
        {"time_step": 2.0, "note": "C3", "length": 2.0, "velocity": 100},
    ]
    out = apply_root_delay_tension(notes, scale="natural_minor", delay_beats=0.15)
    assert out[0]["time_step"] == 0.15
    assert out[1]["note"] == "C3"  # rest of the bassline untouched

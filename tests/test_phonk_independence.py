"""Phonk is NOT a trap-family derivative — a separate electronic tradition
(Memphis rap roots, now its own scene: cowbell, distorted 808, chopped/
screwed vocal samples, often a different drum structure). The "phonk"
GenreProfile used to be a near-copy of rage's numbers (same hat_swing=1.35,
melody_lag_ms=5.47, kick_syncopation=0.33, markov_hats, etc. — both the
Python source AND the assets/genre_profiles.json overlay that overrides it
in production). Rebuilt independently here, plus a real Memphis cowbell
ostinato (materialize_cowbell_notes used to be hard-gated to Carti's sparse
off-beat clank only — phonk's cowbell is a dense, driving pulse instead).

Vocal chops (chopped/screwed samples) stay in the existing vocal-chop
backlog item — not built here.
"""

from __future__ import annotations

from fx_layer import _TEXTURE_GENRES, materialize_cowbell_notes
from genre_profiles import PROFILES
from style_corpus import BPM_RANGES, _FX_FLAVOR
from style_family import is_phonk


def _pattern(genre: str) -> dict:
    return {
        "plg_variety": {"seed": 9, "genre": genre},
        "tracks": {
            "kick": [{"time_step": b * 4.0, "note": "C1", "length": 0.3, "velocity": 110} for b in range(4)],
        },
    }


def test_is_phonk_helper():
    assert is_phonk("phonk") is True
    assert is_phonk("rage") is False
    assert is_phonk("") is False


def test_phonk_cowbell_is_a_dense_ostinato_not_sparse_clank():
    pattern = _pattern("phonk")
    placed = materialize_cowbell_notes(pattern)
    assert placed > 0
    cowbell = pattern["tracks"]["cowbell"]
    bars = 4
    # dense driving pulse: 8 hits/bar (steady 8ths), unlike Carti's <3/bar sparse clank
    assert len(cowbell) == bars * 8


def test_carti_cowbell_still_sparse_after_the_phonk_branch():
    pattern = _pattern("opium")
    materialize_cowbell_notes(pattern)
    cowbell = pattern["tracks"]["cowbell"]
    bars = 4
    assert len(cowbell) < bars * 3


def test_cowbell_still_zero_for_unrelated_genres():
    for genre in ("trap", "pop_rap", "rage"):
        pattern = _pattern(genre)
        assert materialize_cowbell_notes(pattern) == 0
        assert "cowbell" not in pattern["tracks"]


def test_phonk_profile_diverges_from_rage():
    phonk = PROFILES["phonk"]
    rage = PROFILES["rage"]
    assert phonk.hat_swing != rage.hat_swing
    assert phonk.melody_lag_ms != rage.melody_lag_ms
    assert phonk.kick_syncopation != rage.kick_syncopation
    assert phonk.markov_hats != rage.markov_hats
    assert phonk.melody_octave_down_layer is False
    assert phonk.counter_melody_matches_direction is False


def test_phonk_profile_values_match_the_json_overlay():
    # Guards against the JSON overlay silently drifting back toward rage's
    # numbers (assets/genre_profiles.json overrides the source constructor).
    phonk = PROFILES["phonk"]
    assert phonk.hat_swing == 0.65
    assert phonk.melody_lag_ms == 0.0
    assert phonk.kick_syncopation == 0.15
    assert phonk.markov_hats is False
    assert phonk.drive == 0.95


def test_phonk_bpm_covers_the_confirmed_drift_phonk_range():
    # Praxi Plays (3rd source): confirmed real tempo range 140-160.
    lo, hi = BPM_RANGES["phonk"]
    assert lo <= 140 and hi >= 160


def test_phonk_can_get_the_vinyl_room_noise_texture_layer():
    # Praxi Plays: vintage vinyl/room-noise texture layer — reuses the
    # existing generic atmosphere mechanism (TRACK_PROFILES["texture"]
    # already searches vinyl/tape/static/noise keywords).
    assert "phonk" in _TEXTURE_GENRES
    assert "texture" in _FX_FLAVOR["phonk"]

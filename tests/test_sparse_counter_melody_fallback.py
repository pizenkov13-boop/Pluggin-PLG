"""Counter Melody audit fix: for most genres the ONLY source of counter_melody
content was the LLM composer's own (soft, unenforced) JSON output — none of
trap's existing "three mechanisms" (counter_melody_mirrors_chords,
counter_melody_matches_direction, opium symbolic repair) can create a note
from nothing, they all require the lane to already be non-empty. This left
counter_melody silently empty whenever the composer skipped it and melody_lead
was already fine (so the opium repair never triggered either).

materialize_sparse_counter_melody_fallback is the floor: it only fires when
the lane is STILL empty after every real generator has had a chance, and it
answers in melody_lead's own rests (call-and-response), sparsely, in a
different register — not materialize_melody_harmonizer's dense parallel-third
doubling (that one stays toliver-only, see test_melody_harmonizer.py)."""

from __future__ import annotations

import random

from beat_humanize import materialize_sparse_counter_melody_fallback
from genre_profiles import PROFILES


def _note(t: float, note: str, length: float = 1.0, velocity: int = 100) -> dict:
    return {"time_step": t, "note": note, "length": length, "velocity": velocity}


def _rng(seed: int = 1) -> random.Random:
    return random.Random(seed)


def test_noop_without_melody():
    profile = PROFILES["trap"]
    assert materialize_sparse_counter_melody_fallback({"tracks": {}}, profile=profile, rng=_rng()) == 0


def test_noop_with_single_melody_note():
    profile = PROFILES["trap"]
    data = {"tracks": {"melody_lead": [_note(0.0, "A4")]}}
    assert materialize_sparse_counter_melody_fallback(data, profile=profile, rng=_rng()) == 0


def test_never_overwrites_existing_counter_melody():
    profile = PROFILES["trap"]
    data = {
        "tracks": {
            "melody_lead": [_note(0.0, "A4", length=1.0), _note(4.0, "D5", length=1.0)],
            "counter_melody": [_note(0.5, "G3")],  # already claimed by something upstream
        }
    }
    placed = materialize_sparse_counter_melody_fallback(data, profile=profile, rng=_rng())
    assert placed == 0
    assert data["tracks"]["counter_melody"] == [_note(0.5, "G3")]


def test_fills_real_rests_with_a_deterministic_seed():
    profile = PROFILES["trap"]
    # A wide-open two-bar rest (beats 1-8) between two short melody hits —
    # a real "rest", not a crack between phrase notes.
    data = {
        "tracks": {
            "melody_lead": [
                _note(0.0, "A4", length=1.0, velocity=110),
                _note(8.0, "D5", length=1.0, velocity=110),
            ],
        }
    }
    placed = materialize_sparse_counter_melody_fallback(data, profile=profile, rng=_rng(7))
    assert placed >= 1
    counter = data["tracks"]["counter_melody"]
    assert len(counter) == placed
    for note in counter:
        # answers strictly inside the rest window (beat 1 .. beat 8), never on
        # top of either melody note
        assert 1.0 <= note["time_step"] < 8.0


def test_answer_notes_are_quieter_than_the_source_melody():
    profile = PROFILES["trap"]
    data = {
        "tracks": {
            "melody_lead": [
                _note(0.0, "A4", length=1.0, velocity=110),
                _note(8.0, "D5", length=1.0, velocity=110),
            ],
        }
    }
    materialize_sparse_counter_melody_fallback(data, profile=profile, rng=_rng(7))
    for note in data["tracks"]["counter_melody"]:
        assert note["velocity"] < 110


def test_ignores_gaps_below_the_min_rest_threshold():
    profile = PROFILES["trap"]
    # Notes packed tight — no gap ever reaches the 2-beat minimum rest.
    data = {
        "tracks": {
            "melody_lead": [_note(i * 1.0, "A4", length=0.9, velocity=100) for i in range(8)],
        }
    }
    placed = materialize_sparse_counter_melody_fallback(data, profile=profile, rng=_rng(3))
    assert placed == 0
    assert "counter_melody" not in data["tracks"]


def test_sparse_not_dense_only_a_fraction_of_qualifying_rests_get_filled():
    profile = PROFILES["trap"]
    # Eight identically-shaped 3-beat rests — with the fill rate < 1.0, a
    # deterministic seed should not fill every single one.
    notes = []
    for i in range(9):
        notes.append(_note(i * 4.0, "A4", length=1.0, velocity=100))
    data = {"tracks": {"melody_lead": notes}}
    placed = materialize_sparse_counter_melody_fallback(data, profile=profile, rng=_rng(11))
    assert 0 < placed < 8


def test_counter_melody_defaults_true_for_the_main_trap_family_genres():
    for name in ("trap", "rage", "opium", "drill", "phonk", "west_coast"):
        assert PROFILES[name].counter_melody is True, name


def test_counter_melody_stays_off_for_genres_that_opted_out():
    for name in ("minimal_trap", "house", "techno", "lofi"):
        assert PROFILES[name].counter_melody is False, name

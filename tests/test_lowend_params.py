"""Kick/808 knobs lifted out of constants (Param Plan step 1 for the low end): no behaviour change.

``genre_multiplier`` (drum_density) and ``double_max_onset_share`` (midi_phrase_drums) are now
arguments. Their defaults are today's values, so passing them explicitly changes nothing, and
changing them changes the output (they are not dead).
"""
from __future__ import annotations

import inspect

import pytest

import drum_density
import midi_phrase_drums
from drum_density import GENRE_MULTIPLIER_MAX, GENRE_MULTIPLIER_MIN, _profile_multiplier, build_drum_density_card
from genre_profiles import DETAILED_GENRES, PROFILES
from midi_phrase_drums import _DOUBLE_MAX_ONSET_SHARE, _plan_intentional_doubles


def _pattern(per_bar_onsets=4, bars=8, energy=None):
    melody = [
        {"time_step": bar * 4.0 + i * (4.0 / per_bar_onsets), "note": "C4", "length": 0.5, "velocity": 90}
        for bar in range(bars) for i in range(per_bar_onsets)
    ]
    return {
        "bpm": 140,
        "tracks": {"melody_lead": melody},
        "plg_melody_energy": energy or {"onset_rate": 4.0, "busyness": 0.5, "pace": "driving"},
        "plg_arrangement": [{"name": "verse", "start_bar": 0, "bars": bars,
                             "active_tracks": ["melody_lead", "kick", "808_bass", "hi_hats"]}],
    }


# --- genre_multiplier -------------------------------------------------------------------


@pytest.mark.parametrize("genre", sorted(DETAILED_GENRES))
def test_explicit_profile_multiplier_is_the_same_card_for_every_lane(genre):
    profile = PROFILES[genre]
    default = build_drum_density_card(_pattern(), profile=profile)
    explicit = build_drum_density_card(_pattern(), profile=profile, genre_multiplier=_profile_multiplier(profile))
    assert explicit == default


def test_default_argument_is_none_so_the_profile_still_decides():
    assert inspect.signature(build_drum_density_card).parameters["genre_multiplier"].default is None


def test_genre_multiplier_is_live():
    profile = PROFILES["trap"]
    low = build_drum_density_card(_pattern(per_bar_onsets=8), profile=profile, genre_multiplier=0.80)
    high = build_drum_density_card(_pattern(per_bar_onsets=8), profile=profile, genre_multiplier=1.20)
    assert low["genre_multiplier"] == 0.8 and high["genre_multiplier"] == 1.2
    assert low["pairs_per_bar"] < high["pairs_per_bar"]


def test_genre_multiplier_overrides_the_profile_and_is_clamped_to_the_same_bounds():
    profile = PROFILES["rage"]
    assert build_drum_density_card(_pattern(), profile=profile, genre_multiplier=9.0)["genre_multiplier"] == GENRE_MULTIPLIER_MAX
    assert build_drum_density_card(_pattern(), profile=profile, genre_multiplier=0.0)["genre_multiplier"] == GENRE_MULTIPLIER_MIN
    assert _profile_multiplier(profile) != GENRE_MULTIPLIER_MAX  # the profile's own value is not the clamp


def test_the_clamp_bounds_are_the_ones_the_profile_reading_used_before():
    assert (GENRE_MULTIPLIER_MIN, GENRE_MULTIPLIER_MAX) == (0.75, 1.25)
    assert _profile_multiplier(None) == 1.0
    assert _profile_multiplier(PROFILES["trap"]) == pytest.approx(0.85 + 0.30 * PROFILES["trap"].drive)


# --- double_max_onset_share ----------------------------------------------------------------


def _doubles_input(onsets=40):
    # three alternating phases: a repeating identical figure is itself capped ("never the same figure twice running")
    times = [(2.0, 3.0, 2.5)[i % 3] + 8.0 * i for i in range(onsets)]
    return dict(
        roles=[(t, "statement", "paired") for t in times],
        melody_accents=list(times),
        backbeat_times=[t + 4.0 for t in times],
        density=None, start=0.0, end=max(times) + 8.0,
    )


def test_double_share_default_is_the_old_constant():
    assert _DOUBLE_MAX_ONSET_SHARE == 0.10
    assert inspect.signature(_plan_intentional_doubles).parameters["max_onset_share"].default == 0.10
    assert inspect.signature(midi_phrase_drums.build_phrase_kick_808_pocket).parameters["double_max_onset_share"].default == 0.10


def test_explicit_default_share_gives_the_same_plan():
    args = _doubles_input()
    assert _plan_intentional_doubles(**args) == _plan_intentional_doubles(**args, max_onset_share=0.10)


def test_double_share_is_live():
    args = _doubles_input()
    _r, none_g, none_m = _plan_intentional_doubles(**args, max_onset_share=0.0)
    _r, base_g, base_m = _plan_intentional_doubles(**args)
    _r, more_g, more_m = _plan_intentional_doubles(**args, max_onset_share=0.30)
    assert not none_m and not none_g
    assert len(base_m) == 4  # int(40 * 0.10)
    assert len(more_m) > len(base_m)
    assert (len(none_m), len(base_m), len(more_m)) == (0, 4, 12)


def test_the_bar_share_bound_still_wins_when_the_onset_share_is_raised():
    args = _doubles_input(onsets=40)
    _r, _g, mains = _plan_intentional_doubles(**args, max_onset_share=1.0)
    bars = len({int(t // 4.0) for t, _a, _b in args["roles"]})
    assert len(mains) <= int(bars * midi_phrase_drums._DOUBLE_MAX_BAR_SHARE)


def test_the_pocket_builder_passes_the_share_down():
    source = inspect.getsource(midi_phrase_drums.build_phrase_kick_808_pocket)
    assert "max_onset_share=double_max_onset_share" in source


def test_without_a_plan_the_route_passes_no_extra_arguments(monkeypatch):
    """Defaults decide when there is no applicable plan: the new keywords are never passed."""
    import beat_humanize
    from arranger import arrange_song
    import dataclasses

    seen = {}
    real_card, real_pocket = drum_density.build_drum_density_card, midi_phrase_drums.build_phrase_kick_808_pocket

    def card(pattern, **kw):
        seen["card"] = kw
        return real_card(pattern, **kw)

    def pocket(**kw):
        seen["pocket"] = kw
        return real_pocket(**kw)

    monkeypatch.setattr(drum_density, "build_drum_density_card", card)
    monkeypatch.setattr(midi_phrase_drums, "build_phrase_kick_808_pocket", pocket)
    from test_bass808_llm import _midi_trap_pattern

    pattern = _midi_trap_pattern()
    arrange_song(pattern)
    beat_humanize.humanize_pattern(pattern, profile=dataclasses.replace(PROFILES["trap"]))
    assert "genre_multiplier" not in seen["card"]
    assert "double_max_onset_share" not in seen["pocket"]

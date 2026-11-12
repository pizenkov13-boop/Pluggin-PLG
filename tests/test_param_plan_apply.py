"""Applying a Param Plan in humanize_pattern (PLG_PARAM_PLAN=on) and nothing else.

Without an applicable plan the output must be byte-for-byte what it was before
the plan existed; with one, exactly the four knobs move.
"""
from __future__ import annotations

import random

import pytest

import beat_humanize
import drum_density
import hihat_rolls
import param_plan
from genre_profiles import PROFILES

PROFILE = PROFILES["trap"]
BASE = param_plan.baseline_plan(PROFILE)


@pytest.fixture
def drums_offered(monkeypatch):
    monkeypatch.setattr(param_plan, "OFFER_DRUM_VELOCITY", True)


def _source():
    snares = [{"time_step": b * 4 + 2.0, "note": "D1", "length": 0.25, "velocity": 110} for b in range(8)]
    hats = [
        {"time_step": b * 4 + s * 0.5, "note": "C5", "length": 0.1, "velocity": 80}
        for b in range(8) for s in range(8)
    ]
    melody = [{"time_step": float(i), "note": "A3", "length": 0.5, "velocity": 90} for i in range(32)]
    return {"bpm": 140.0, "style": "trap", "tracks": {"snare": snares, "hi_hats": hats, "melody_lead": melody}}


def _record(*, mode="on", ok=True, source="llm", **changes):
    values = param_plan.validate_plan({"schema": 1, **changes}, BASE).values
    return {"mode": mode, "ok": ok, "source": source, "values": values}


def _humanize(record=None):
    pattern = _source()
    if record is not None:
        pattern["plg_param_plan"] = record
    random.seed(11)
    out = beat_humanize.humanize_pattern(pattern)
    out.pop("plg_param_plan", None)
    return out


# --- when the plan is NOT applied the beat is untouched ----------------------------------------


@pytest.mark.parametrize("record", [
    None,
    _record(mode="shadow", hat_swing=0.3),
    _record(mode="off", hat_swing=0.3),
    _record(ok=False, hat_swing=0.3),
    _record(source="fallback", hat_swing=0.3),
    {"mode": "on", "ok": True, "source": "llm", "values": "garbage"},
    {"mode": "on", "ok": True, "source": "llm"},
    "not a dict",
])
def test_a_plan_that_is_not_applicable_changes_nothing(record):
    assert _humanize(record) == _humanize(None)


def test_a_baseline_plan_in_on_mode_changes_nothing():
    plain = _humanize(None)
    planned = _humanize(_record())
    applied = planned.pop("plg_param_plan_applied")
    assert applied["hat_swing"] == BASE["hat_swing"] and "swing_reach" not in applied
    assert planned["tracks"] == plain["tracks"]


def test_applied_plan_is_validated_again_so_a_forged_value_is_clamped():
    forged = _record()
    forged["values"]["hat_swing"] = 99.0
    applied = _humanize(forged)["plg_param_plan_applied"]
    assert applied["hat_swing"] == param_plan.LIMITS["hat_swing"][1]


# --- when it is applied, the four knobs arrive at the executors ------------------------------------


def test_each_knob_reaches_its_function(monkeypatch):
    seen = {}
    real_swing, real_rolls, real_lag = beat_humanize.apply_hi_hat_swing, hihat_rolls.apply_trap_hi_hats, beat_humanize.apply_melody_groove_lag

    def swing(notes, rng, **kw):
        seen["swing"] = kw
        return real_swing(notes, rng, **kw)

    def rolls(*a, **kw):
        seen["rolls"] = kw
        return real_rolls(*a, **kw)

    def lag(notes, bpm, ms, rng, **kw):
        seen["lag"] = ms
        return real_lag(notes, bpm, ms, rng, **kw)

    monkeypatch.setattr(beat_humanize, "apply_hi_hat_swing", swing)
    monkeypatch.setattr(hihat_rolls, "apply_trap_hi_hats", rolls)
    monkeypatch.setattr(beat_humanize, "apply_melody_groove_lag", lag)
    _humanize(_record(
        hat_swing=0.6, melody_lag_ms=5.0,
        hat_velocity={"peak": 80, "vel_min": 50, "vel_max": 90},
        roll={"prob": {"hot": 0.11, "default": 0.12, "quiet": 0.13}},
    ))
    assert seen["swing"]["intensity"] == 0.6
    assert seen["lag"] == 5.0
    assert seen["rolls"]["roll_prob_hot"] == 0.11 and seen["rolls"]["roll_prob"] == 0.12
    assert seen["rolls"]["roll_prob_quiet"] == 0.13
    assert (seen["rolls"]["peak"], seen["rolls"]["vel_min"], seen["rolls"]["vel_max"]) == (80, 50, 90)


def test_applied_plan_changes_the_hats_and_the_melody_and_the_velocity_wave():
    plain, planned = _humanize(None), _humanize(_record(hat_swing=0.2, melody_lag_ms=0.0, hat_velocity={"peak": 70}))
    assert planned["tracks"]["hi_hats"] != plain["tracks"]["hi_hats"]
    assert planned["tracks"]["melody_lead"] != plain["tracks"]["melody_lead"]
    assert [n["velocity"] for n in planned["tracks"]["hi_hats"]] != [n["velocity"] for n in plain["tracks"]["hi_hats"]]
    # Not asserted on purpose: the other lanes. The generation shares one RNG, so a different
    # hat/roll/lag draw count re-draws every later random pass (kick/snare micro-timing and
    # velocities too). Listening therefore compares two whole beats, not four isolated knobs.


def test_melody_lag_zero_turns_the_lag_off_and_a_value_sets_it():
    lags = lambda out: [n.get("groove_lag_ms") for n in out["tracks"]["melody_lead"]]
    assert all(v is None for v in lags(_humanize(_record(melody_lag_ms=0.0))))
    mean = lambda xs: sum(xs) / len(xs)
    assert mean(lags(_humanize(_record(melody_lag_ms=20.0)))) == pytest.approx(20.0, abs=2.5)


# --- the swing ceiling follows the plan, and drum_density follows the ceiling ---------------------------


def test_swing_above_the_old_cap_is_applied_and_widens_the_thinning_reach(monkeypatch):
    seen = {}
    real_swing, real_thin = beat_humanize.apply_hi_hat_swing, drum_density.thin_generated_hats

    def swing(notes, rng, **kw):
        seen["intensity"] = kw["intensity"]
        return real_swing(notes, rng, **kw)

    def thin(hats, card, **kw):
        seen["thin"] = kw
        return real_thin(hats, card, **kw)

    monkeypatch.setattr(beat_humanize, "apply_hi_hat_swing", swing)
    monkeypatch.setattr(drum_density, "thin_generated_hats", thin)
    applied = _humanize(_record(hat_swing=1.28))["plg_param_plan_applied"]
    assert seen["intensity"] == 1.28
    assert applied["swing_cap"] == 1.28
    reach = seen["thin"]["swing_reach"]
    assert beat_humanize.PPQ_SWING_MAX * 1.28 < reach < 0.125


def test_at_or_below_the_old_cap_the_thinning_reach_is_the_default(monkeypatch):
    seen = {}
    real_thin = drum_density.thin_generated_hats

    def thin(hats, card, **kw):
        seen["thin"] = kw
        return real_thin(hats, card, **kw)

    monkeypatch.setattr(drum_density, "thin_generated_hats", thin)
    _humanize(_record(hat_swing=1.0))
    assert seen["thin"] == {}
    seen.clear()
    _humanize(None)
    assert seen["thin"] == {}


def test_widest_possible_swing_still_fits_inside_half_a_sixteenth():
    widest = beat_humanize.PPQ_SWING_MAX * param_plan.LIMITS["hat_swing"][1]
    assert widest + beat_humanize.SWING_REACH_MARGIN < 0.125


def test_thin_generated_hats_default_reach_is_unchanged():
    import inspect

    assert inspect.signature(drum_density.thin_generated_hats).parameters["swing_reach"].default == 0.11


# --- snare / clap velocity ranges and roll depth reach the executors ------------------------------


def test_snare_and_clap_ranges_reach_the_velocity_draw(monkeypatch, drums_offered):
    seen = []
    real = beat_humanize.humanize_velocities

    def spy(notes, *, lo, hi, rng):
        seen.append((lo, hi))
        return real(notes, lo=lo, hi=hi, rng=rng)

    monkeypatch.setattr(beat_humanize, "humanize_velocities", spy)
    pattern = _source()
    pattern["tracks"]["clap"] = [dict(n) for n in pattern["tracks"]["snare"]]
    pattern["plg_param_plan"] = _record(snare_velocity={"lo": 90, "hi": 110}, clap_velocity={"lo": 85, "hi": 100})
    beat_humanize.humanize_pattern(pattern)
    assert (90, 110) in seen and (85, 100) in seen
    seen.clear()
    plain = _source()
    plain["tracks"]["clap"] = [dict(n) for n in plain["tracks"]["snare"]]
    beat_humanize.humanize_pattern(plain)
    assert (100, 127) in seen and (95, 120) in seen


def test_the_planned_snare_range_moves_the_final_snare_velocities(drums_offered):
    """Later passes (fills, pickups) still shape single hits, so assert the level, not every note."""
    mean = lambda out: sum(n["velocity"] for n in out["tracks"]["snare"]) / len(out["tracks"]["snare"])
    low = _humanize(_record(snare_velocity={"lo": 90, "hi": 100}))
    assert mean(_humanize(None)) - mean(low) > 8


def test_roll_depth_reaches_the_trap_hats(monkeypatch):
    seen = {}
    real = hihat_rolls.apply_trap_hi_hats

    def rolls(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(hihat_rolls, "apply_trap_hi_hats", rolls)
    _humanize(_record(roll={"depth": {"hot": 0.45, "default": 0.2}}))
    assert seen["roll_depth_hot"] == 0.45 and seen["roll_depth"] == 0.2


# --- kick density multiplier and double-kick share reach the low-end builders -------------------------------


def _midi_pattern():
    from test_bass808_llm import _midi_trap_pattern

    return _midi_trap_pattern()


def _lowend_calls(monkeypatch, record):
    import drum_density
    import midi_phrase_drums
    from arranger import arrange_song

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
    pattern = _midi_pattern()
    if record is not None:
        pattern["plg_param_plan"] = record
    arrange_song(pattern)
    random.seed(11)
    out = beat_humanize.humanize_pattern(pattern)
    return seen, out


def test_kick_knobs_are_passed_to_the_density_card_and_the_pocket(monkeypatch):
    seen, out = _lowend_calls(monkeypatch, _record(kick_density_multiplier=0.8, double_kick_share=0.2))
    assert seen["card"]["genre_multiplier"] == 0.8
    assert seen["pocket"]["double_max_onset_share"] == 0.2
    assert out["plg_param_plan_applied"]["density"] == {"genre_multiplier": 0.8}
    assert out["plg_param_plan_applied"]["pocket"] == {"double_max_onset_share": 0.2}
    assert out["plg_drum_density"]["genre_multiplier"] == 0.8


def test_a_plan_that_is_not_applicable_leaves_the_low_end_calls_untouched(monkeypatch):
    for record in (None, _record(mode="shadow", kick_density_multiplier=0.8), _record(ok=False, double_kick_share=0.3)):
        seen, _out = _lowend_calls(monkeypatch, record)
        assert "genre_multiplier" not in seen["card"]
        assert "double_max_onset_share" not in seen["pocket"]


def test_a_baseline_kick_plan_gives_the_same_notes_as_no_plan(monkeypatch):
    plain = _lowend_calls(monkeypatch, None)[1]
    planned = _lowend_calls(monkeypatch, _record())[1]
    assert planned["tracks"]["kick"] == plain["tracks"]["kick"]
    assert planned["tracks"]["808_bass"] == plain["tracks"]["808_bass"]


def test_the_density_multiplier_changes_the_pairs_per_bar_the_low_end_may_carry(monkeypatch):
    low = _lowend_calls(monkeypatch, _record(kick_density_multiplier=0.75))[1]["plg_drum_density"]["pairs_per_bar"]
    high = _lowend_calls(monkeypatch, _record(kick_density_multiplier=1.25))[1]["plg_drum_density"]["pairs_per_bar"]
    assert low < high

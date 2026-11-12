"""Param Plan contract (param_plan.py): schema, validator, and that the knobs are live.

Structure and protection only. Nothing calls an LLM and the route does not read
a plan, so these tests also pin that the plan is not wired anywhere.
"""
from __future__ import annotations

import copy
import inspect
import json
import random
from pathlib import Path

import pytest

import beat_humanize
import hihat_rolls
import param_plan
from genre_profiles import PROFILES
from param_plan import (
    LIMITS, MIN_VELOCITY_SPAN, SCHEMA_VERSION, baseline_plan, build_plan_input,
    humanizer_kwargs, validate_plan,
)

PROFILE = PROFILES["trap"]
BASE = baseline_plan(PROFILE)


def _plan(**fields):
    return {"schema": SCHEMA_VERSION, **fields}


def _offered(values):
    """The part of a baseline the model is shown (snare/clap velocity are switched off)."""
    return {k: v for k, v in values.items() if k not in param_plan.DRUM_VELOCITY_KNOBS}


@pytest.fixture
def drums_offered(monkeypatch):
    """Switch snare/clap velocity back on: they are implemented, just not offered by default."""
    monkeypatch.setattr(param_plan, "OFFER_DRUM_VELOCITY", True)


# --- baseline ----------------------------------------------------------------------


def test_baseline_is_complete_in_range_and_json_safe():
    assert set(BASE) == {
        "hat_swing", "hat_velocity", "roll", "melody_lag_ms", "snare_velocity", "clap_velocity",
        "kick_density_multiplier", "double_kick_share",
    }
    assert json.loads(json.dumps(BASE)) == BASE
    for genre, profile in PROFILES.items():
        values = baseline_plan(profile)
        assert values["hat_swing"] <= param_plan.APPLIED_SWING_CAP, genre
        assert 0.0 <= values["melody_lag_ms"] <= 25.0, genre


def test_baseline_swing_equals_what_the_route_applies_today():
    for profile in PROFILES.values():
        assert baseline_plan(profile)["hat_swing"] == pytest.approx(min(profile.hat_swing, beat_humanize.HAT_SWING_CAP), abs=1e-4)


def test_baseline_plan_round_trips_through_the_validator_unchanged():
    result = validate_plan(_plan(**copy.deepcopy(_offered(BASE))), BASE)
    assert result.ok and result.values == BASE
    assert not (result.clamped or result.fell_back or result.dropped or result.reserved)


def test_full_baseline_round_trips_when_the_drum_knobs_are_offered(drums_offered):
    result = validate_plan(_plan(**copy.deepcopy(BASE)), BASE)
    assert result.ok and result.values == BASE
    assert not (result.clamped or result.fell_back or result.dropped or result.reserved)


# --- validator ------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "text", 7, [], {}, {"schema": 99}, {"schema": "1"}])
def test_garbage_or_wrong_schema_returns_the_baseline(raw):
    result = validate_plan(raw, BASE)
    assert result.ok is False and result.values == BASE


def test_missing_fields_fall_back_silently():
    result = validate_plan(_plan(hat_swing=0.5), BASE)
    assert result.values["hat_swing"] == 0.5
    assert result.values["melody_lag_ms"] == BASE["melody_lag_ms"]
    assert result.values["roll"] == BASE["roll"]
    assert result.fell_back == ()


def test_out_of_range_values_are_clamped_and_reported():
    result = validate_plan(_plan(
        hat_swing=9.0, melody_lag_ms=-5,
        hat_velocity={"peak": 500, "vel_min": 0, "vel_max": 200},
        roll={"prob": {"hot": 3.0, "quiet": -1.0}},
    ), BASE)
    v = result.values
    assert v["hat_swing"] == LIMITS["hat_swing"][1]
    assert v["melody_lag_ms"] == 0.0
    assert v["hat_velocity"] == {"peak": 110, "vel_min": 30, "vel_max": 127}
    assert v["roll"]["prob"]["hot"] == LIMITS["roll.prob.hot"][1]
    assert v["roll"]["prob"]["quiet"] == 0.0
    assert {"hat_swing", "melody_lag_ms", "hat_velocity.peak", "roll.prob.hot", "roll.prob.quiet"} <= set(result.clamped)


@pytest.mark.parametrize("bad", ["fast", True, False, float("nan"), float("inf"), [1], {"a": 1}])
def test_non_numeric_values_fall_back_to_the_baseline(bad):
    result = validate_plan(_plan(hat_swing=bad, melody_lag_ms=bad, hat_velocity={"peak": bad}), BASE)
    assert result.values["hat_swing"] == BASE["hat_swing"]
    assert result.values["melody_lag_ms"] == BASE["melody_lag_ms"]
    assert result.values["hat_velocity"]["peak"] == BASE["hat_velocity"]["peak"]
    assert {"hat_swing", "melody_lag_ms", "hat_velocity.peak"} <= set(result.fell_back)


def test_wrong_container_types_fall_back_not_crash():
    result = validate_plan(_plan(hat_velocity="loud", roll=[1, 2]), BASE)
    assert result.values["hat_velocity"] == BASE["hat_velocity"]
    assert result.values["roll"] == BASE["roll"]
    assert {"hat_velocity", "roll"} <= set(result.fell_back)


def test_unknown_keys_are_dropped_and_never_reach_the_values():
    result = validate_plan(_plan(
        hat_swing=0.6, flavour="spicy", hat_velocity={"peak": 90, "loudness": 5},
        roll={"prob": {"hot": 0.5, "mystery": 0.9}, "burst": 2},
    ), BASE)
    assert {"flavour", "hat_velocity.loudness", "roll.prob.mystery", "roll.burst"} <= set(result.dropped)
    assert "flavour" not in result.values and "loudness" not in result.values["hat_velocity"]
    assert set(result.values["roll"]["prob"]) == {"hot", "default", "quiet"}


def test_reserved_knobs_are_reported_but_inert():
    result = validate_plan(_plan(swing_range_ticks=[1, 2], kick_syncopation=0.9), BASE)
    assert result.ok
    assert set(result.reserved) == {"swing_range_ticks", "kick_syncopation"}
    assert result.values == BASE
    assert set(humanizer_kwargs(result)) == {
        "apply_hi_hat_swing", "apply_trap_hi_hats", "apply_markov_hat_dynamics", "melody_lag_ms",
        "snare_velocity", "clap_velocity", "build_drum_density_card", "build_phrase_kick_808_pocket",
    }


def test_kick_syncopation_stays_reserved_because_nothing_reads_it_on_the_live_route():
    """bass808_enabled gates the only reader of GenreProfile.kick_syncopation off; every live lane has it."""
    from genre_profiles import DETAILED_GENRES

    assert "kick_syncopation" in param_plan.RESERVED
    assert all(PROFILES[g].bass808_enabled for g in DETAILED_GENRES)


def test_section_names_map_to_the_three_buckets():
    result = validate_plan(_plan(roll={"prob": {"Hook": 0.5, "verse": 0.05, "bridge": 0.2}}), BASE)
    assert result.values["roll"]["prob"] == {"hot": 0.5, "default": 0.2, "quiet": 0.05}


def test_velocity_limits_alone_keep_the_wave_at_least_the_minimum_span():
    low = validate_plan(_plan(hat_velocity={"vel_min": 9999, "vel_max": -9999}), BASE).values["hat_velocity"]
    assert low["vel_max"] - low["vel_min"] >= MIN_VELOCITY_SPAN
    assert LIMITS["hat_velocity.vel_max"][0] - LIMITS["hat_velocity.vel_min"][1] >= MIN_VELOCITY_SPAN


def test_span_guard_restores_the_baseline_pair_if_limits_ever_allow_a_collapse(monkeypatch):
    # unreachable with today's LIMITS (see the test above); pinned for the day they change
    monkeypatch.setitem(LIMITS, "hat_velocity.vel_max", (60.0, 127.0))
    result = validate_plan(_plan(hat_velocity={"vel_min": 70, "vel_max": 75}), BASE)
    assert result.values["hat_velocity"]["vel_min"] == BASE["hat_velocity"]["vel_min"]
    assert result.values["hat_velocity"]["vel_max"] == BASE["hat_velocity"]["vel_max"]
    assert "hat_velocity.span" in result.fell_back


def test_notes_are_trimmed_and_text_only():
    assert validate_plan(_plan(notes="x" * 999), BASE).notes == "x" * param_plan.NOTES_MAX_CHARS
    assert validate_plan(_plan(notes={"a": 1}), BASE).notes == ""


def test_validator_does_not_mutate_its_inputs_or_the_baseline():
    raw = _plan(hat_swing=0.7, roll={"prob": {"hot": 0.9}})
    snapshot, base_snapshot = copy.deepcopy(raw), copy.deepcopy(BASE)
    result = validate_plan(raw, BASE)
    assert raw == snapshot and BASE == base_snapshot
    result.values["roll"]["prob"]["hot"] = 0.0
    assert BASE == base_snapshot


# --- knobs are wired to real parameters: defaults are no-ops, changes are not -----------


def _hats(bars=16):
    return hihat_rolls.build_steady_eighths(bars)


def _roll_pattern(section="hook", bars=16):
    snares = [{"time_step": b * 4 + 2.0, "note": "D1", "length": 0.25, "velocity": 110} for b in range(bars)]
    kicks = [{"time_step": b * 4.0, "note": "C1", "length": 0.25, "velocity": 110} for b in range(bars)]
    return {
        "tracks": {"snare": snares, "kick": kicks},
        "plg_arrangement": [{"name": section, "start_bar": 0, "bars": bars}],
    }


def test_swing_default_kwargs_are_a_no_op():
    kwargs = humanizer_kwargs(validate_plan(_plan(), BASE))["apply_hi_hat_swing"]
    plain = beat_humanize.apply_hi_hat_swing(_hats(), random.Random(3), intensity=min(PROFILE.hat_swing, beat_humanize.HAT_SWING_CAP))
    planned = beat_humanize.apply_hi_hat_swing(_hats(), random.Random(3), **kwargs)
    explicit = beat_humanize.apply_hi_hat_swing(
        _hats(), random.Random(3), intensity=kwargs["intensity"],
        swing_min=beat_humanize.PPQ_SWING_MIN, swing_max=beat_humanize.PPQ_SWING_MAX,
    )
    assert plain == planned == explicit


def test_swing_is_live():
    def swung(value):
        kwargs = humanizer_kwargs(validate_plan(_plan(hat_swing=value), BASE))["apply_hi_hat_swing"]
        return beat_humanize.apply_hi_hat_swing(_hats(), random.Random(3), **kwargs)

    assert swung(0.2) != swung(1.1)
    grid = [n["time_step"] for n in _hats()]
    assert [n["time_step"] for n in swung(0.0)] == grid


@pytest.mark.parametrize("section", ["hook", "verse", "bridge"])
def test_trap_hats_default_kwargs_are_a_no_op(section):
    kwargs = humanizer_kwargs(validate_plan(_plan(), BASE))["apply_trap_hi_hats"]
    plain = hihat_rolls.apply_trap_hi_hats(_roll_pattern(section), random.Random(5))
    planned = hihat_rolls.apply_trap_hi_hats(_roll_pattern(section), random.Random(5), **kwargs)
    assert plain == planned


@pytest.mark.parametrize("section,bucket", [("hook", "hot"), ("verse", "quiet"), ("bridge", "default")])
def test_roll_probability_is_live_per_bucket(section, bucket):
    def rolls(p):
        plan = validate_plan(_plan(roll={"prob": {bucket: p}}), BASE)
        kwargs = humanizer_kwargs(plan)["apply_trap_hi_hats"]
        hats = hihat_rolls.apply_trap_hi_hats(_roll_pattern(section), random.Random(5), **kwargs)
        return sum(bool(n.get("hat_roll")) for n in hats)

    assert rolls(LIMITS[f"roll.prob.{bucket}"][1]) > rolls(0.0) == 0


def test_roll_probability_of_one_bucket_does_not_leak_into_another():
    plan = validate_plan(_plan(roll={"prob": {"hot": 0.0}}), BASE)
    kwargs = humanizer_kwargs(plan)["apply_trap_hi_hats"]
    plain = hihat_rolls.apply_trap_hi_hats(_roll_pattern("verse"), random.Random(5))
    planned = hihat_rolls.apply_trap_hi_hats(_roll_pattern("verse"), random.Random(5), **kwargs)
    assert plain == planned


def test_hat_velocity_default_is_a_no_op_and_each_field_is_live():
    kwargs = humanizer_kwargs(validate_plan(_plan(), BASE))["apply_markov_hat_dynamics"]
    plain = hihat_rolls.apply_markov_hat_dynamics(_hats(), random.Random(9))
    assert plain == hihat_rolls.apply_markov_hat_dynamics(_hats(), random.Random(9), **kwargs)

    def vel(**hat_velocity):
        plan = validate_plan(_plan(hat_velocity=hat_velocity), BASE)
        out = hihat_rolls.apply_markov_hat_dynamics(
            _hats(), random.Random(9), **humanizer_kwargs(plan)["apply_markov_hat_dynamics"]
        )
        return [n["velocity"] for n in out]

    assert vel(peak=62) != vel(peak=108)
    assert max(vel(vel_max=95)) <= 95
    assert min(vel(vel_min=60)) >= 60


def test_melody_lag_default_is_a_no_op_and_value_is_live():
    notes = [{"time_step": float(i), "note": "A3", "length": 0.5, "velocity": 90} for i in range(32)]
    base_ms = humanizer_kwargs(validate_plan(_plan(), BASE))["melody_lag_ms"]
    plain = beat_humanize.apply_melody_groove_lag(notes, 140.0, PROFILE.melody_lag_ms, random.Random(2))
    assert plain == beat_humanize.apply_melody_groove_lag(notes, 140.0, base_ms, random.Random(2))
    assert beat_humanize.apply_melody_groove_lag(notes, 140.0, 22.0, random.Random(2)) != plain
    off = beat_humanize.apply_melody_groove_lag(notes, 140.0, 0.0, random.Random(2))
    assert [n["time_step"] for n in off] == [n["time_step"] for n in notes]


# --- decider input ---------------------------------------------------------------------------


def _full_pattern():
    return {
        "bpm": 140.0,
        "plg_variety": {"genre": "trap"},
        "plg_midi_passport": {
            "tempo": {"bpm": 140.0, "source": "midi_tempo"},
            "key": {"root_pc": 9, "scale": "natural_minor"},
        },
        "plg_progression": {"key": "A", "scale": "natural_minor"},
        "plg_melody_energy": {
            "busyness": 0.4123, "pace": "driving", "onset_rate": 2.0, "attack": "sharp",
            "role": "pluck", "vibe": "dark",
            "section_density": [
                {"start": 0.0, "end": 32.0, "ratio": 0.7},
                {"start": 32.0, "end": 64.0, "ratio": 1.4},
            ],
        },
        "plg_midi_structure": {"span": {"bars": 16}, "phrases": [1, 2, 3], "pauses": []},
        "plg_arrangement": [
            {"name": "verse", "start_bar": 0, "bars": 8, "energy": 0.5, "rolls": False, "hat_feel": "straight"},
            {"name": "hook", "start_bar": 8, "bars": 8, "energy": 0.9, "rolls": True, "hat_feel": "rolls"},
        ],
        "tracks": {"melody_lead": [{"time_step": 0.0, "note": "A3", "length": 1.0}]},
        "sample_library": r"C:\secret\library",
    }


def test_plan_input_is_compact_json_without_notes_or_paths():
    document = build_plan_input(_full_pattern(), PROFILE)
    text = json.dumps(document)
    assert json.loads(text) == document
    assert "secret" not in text and "melody_lead" not in text and "A3" not in text
    assert document["schema"] == SCHEMA_VERSION and document["genre"] == "trap"
    assert document["tempo"]["bpm"] == 140.0
    assert document["structure"]["phrases"] == 3 and document["structure"]["bars"] == 16
    verse, hook = document["structure"]["sections"]
    assert verse["melody_density"] == 0.7 and hook["melody_density"] == 1.4 and hook["rolls_allowed"] is True
    assert document["baseline"] == _offered(BASE)
    assert set(document["active_knobs"]) == set(param_plan.ACTIVE_KNOBS)
    assert len(text) < 3000


def test_plan_input_survives_odd_patterns_and_is_deterministic():
    for pattern in ({}, {"plg_melody_energy": "x", "plg_arrangement": ["x", None], "plg_midi_structure": []}):
        document = build_plan_input(pattern, PROFILE)
        assert json.loads(json.dumps(document)) == document
    assert build_plan_input(_full_pattern(), PROFILE) == build_plan_input(_full_pattern(), PROFILE)


def test_plan_input_does_not_mutate_the_pattern():
    pattern = _full_pattern()
    snapshot = copy.deepcopy(pattern)
    build_plan_input(pattern, PROFILE)
    assert pattern == snapshot


def test_kit_summary_passes_only_whitelisted_fields():
    kit = {"hat_tail": "short", "path": "C:/x.wav", "hat_brightness": 0.7}
    assert build_plan_input(_full_pattern(), PROFILE, kit=kit)["kit"] == {"hat_tail": "short", "hat_brightness": 0.7}
    assert "kit" not in build_plan_input(_full_pattern(), PROFILE)


# --- the plan is not wired anywhere ---------------------------------------------------------------


def test_module_has_no_llm_rng_env_or_io():
    source = inspect.getsource(param_plan)
    for needle in ("import random", "os.environ", "llm_client", "openrouter", "open(", "requests", "urllib"):
        assert needle not in source


def test_only_the_client_its_hook_and_the_humanize_executor_know_the_plan():
    """Recorded by param_plan_client (via backend_core), applied only by beat_humanize."""
    root = Path(__file__).resolve().parent.parent
    readers = []
    for path in root.glob("*.py"):
        if path.name == "param_plan.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "param_plan" in text or "plg_param_plan" in text:
            readers.append(path.name)
    assert sorted(readers) == ["backend_core.py", "beat_humanize.py", "param_plan_client.py"]
    for name in ("hihat_rolls.py", "hat_roll_engine.py", "flp_writer.py", "drum_density.py"):
        assert "param_plan" not in (root / name).read_text(encoding="utf-8", errors="ignore")


# --- swing ceiling and per-section density -----------------------------------------------------


def test_applied_swing_cap_mirrors_beat_humanize_and_the_plan_may_ask_for_more():
    assert param_plan.APPLIED_SWING_CAP == beat_humanize.HAT_SWING_CAP == 1.15
    assert LIMITS["hat_swing"][1] == 1.28
    assert baseline_plan(PROFILES["trap"])["hat_swing"] == 1.15  # trap asks for 1.28, 1.15 is what plays
    assert validate_plan(_plan(hat_swing=1.25), BASE).values["hat_swing"] == 1.25
    assert validate_plan(_plan(hat_swing=1.9), BASE).values["hat_swing"] == 1.28


def test_swing_reach_assumption_in_drum_density_still_holds_at_the_applied_cap():
    import drum_density

    reach = beat_humanize.PPQ_SWING_MAX * beat_humanize.HAT_SWING_CAP
    assert reach <= drum_density._HAT_SWING_REACH_BEATS < 0.125


def _arranged(density_in_card=None):
    notes = [{"time_step": float(t), "note": "A3", "length": 0.5, "velocity": 90}
             for t in range(0, 32) for _ in range(1 if t < 16 else 3)]
    pattern = {
        "tracks": {"melody_lead": notes},
        "plg_arrangement": [
            {"name": "verse", "start_bar": 0, "bars": 4, "energy": 0.5, "rolls": False},
            {"name": "hook", "start_bar": 4, "bars": 4, "energy": 0.9, "rolls": True},
        ],
        "plg_melody_energy": {},
    }
    if density_in_card is not None:
        pattern["plg_melody_energy"]["section_density"] = density_in_card
    return pattern


def test_section_density_is_computed_when_the_card_has_none():
    verse, hook = build_plan_input(_arranged(), PROFILE)["structure"]["sections"]
    assert verse["melody_density"] is not None and hook["melody_density"] is not None
    assert hook["melody_density"] > verse["melody_density"]


def test_section_density_from_the_card_wins_over_recomputing():
    card = [{"start": 0.0, "end": 16.0, "ratio": 0.1}, {"start": 16.0, "end": 32.0, "ratio": 9.0}]
    verse, hook = build_plan_input(_arranged(card), PROFILE)["structure"]["sections"]
    assert (verse["melody_density"], hook["melody_density"]) == (0.1, 9.0)


@pytest.mark.parametrize("mutate", [
    lambda p: p["tracks"].update(melody_lead=[]),                       # no melody
    lambda p: p.update(plg_arrangement=p["plg_arrangement"][:1]),       # one section: no contrast to measure
    lambda p: p.pop("tracks"),                                          # no tracks at all
    lambda p: p.update(plg_arrangement=[{"name": "x", "start_bar": "?", "bars": None}]),
])
def test_section_density_degrades_to_none_on_an_incomplete_pattern(mutate):
    pattern = _arranged()
    mutate(pattern)
    document = build_plan_input(pattern, PROFILE)
    assert json.loads(json.dumps(document)) == document
    assert all(s["melody_density"] is None for s in document["structure"]["sections"])


# --- snare / clap velocity and roll depth -------------------------------------------------------


def test_new_knobs_have_the_todays_values_as_baseline():
    assert BASE["snare_velocity"] == {"lo": 100, "hi": 127} and BASE["clap_velocity"] == {"lo": 95, "hi": 120}
    assert BASE["roll"]["depth"] == {"hot": hihat_rolls.ROLL_DEPTH_HOT, "default": hihat_rolls.ROLL_DEPTH}


def test_snare_clap_and_depth_are_validated_clamped_and_reported(drums_offered):
    result = validate_plan(_plan(
        snare_velocity={"lo": 1, "hi": 500, "x": 1}, clap_velocity={"lo": "loud"},
        roll={"depth": {"hot": 9, "default": -1, "bonus": 1}},
    ), BASE)
    v = result.values
    assert v["snare_velocity"] == {"lo": 80, "hi": 127}
    assert v["clap_velocity"] == {"lo": BASE["clap_velocity"]["lo"], "hi": BASE["clap_velocity"]["hi"]}
    assert v["roll"]["depth"] == {"hot": 0.5, "default": 0.15}
    assert {"snare_velocity.lo", "snare_velocity.hi", "roll.depth.hot", "roll.depth.default"} <= set(result.clamped)
    assert "clap_velocity.lo" in result.fell_back
    assert {"snare_velocity.x", "roll.depth.bonus"} <= set(result.dropped)


def test_a_flat_snare_range_restores_the_baseline_pair(drums_offered):
    result = validate_plan(_plan(snare_velocity={"lo": 115, "hi": 105}), BASE)  # both are legal alone
    assert result.values["snare_velocity"] == BASE["snare_velocity"]
    assert "snare_velocity.span" in result.fell_back


def test_kwargs_carry_the_new_knobs(drums_offered):
    plan = validate_plan(_plan(
        snare_velocity={"lo": 90, "hi": 120}, clap_velocity={"lo": 85, "hi": 110},
        roll={"depth": {"hot": 0.45, "default": 0.2}},
    ), BASE)
    kwargs = humanizer_kwargs(plan)
    assert kwargs["snare_velocity"] == (90, 120) and kwargs["clap_velocity"] == (85, 110)
    assert kwargs["apply_trap_hi_hats"]["roll_depth_hot"] == 0.45 and kwargs["apply_trap_hi_hats"]["roll_depth"] == 0.2
    default = humanizer_kwargs(validate_plan(_plan(), BASE))
    assert default["snare_velocity"] == beat_humanize.SNARE_VEL_RANGE
    assert default["clap_velocity"] == beat_humanize.CLAP_VEL_RANGE


def test_roll_depth_is_live():
    def lengths(depth):
        plan = validate_plan(_plan(roll={"prob": {"hot": 0.6}, "depth": {"hot": depth}}), BASE)
        kwargs = humanizer_kwargs(plan)["apply_trap_hi_hats"]
        hats = hihat_rolls.apply_trap_hi_hats(_roll_pattern("hook"), random.Random(5), **kwargs)
        return sum(1 for n in hats if n.get("hat_roll"))

    assert lengths(0.5) > lengths(0.2) > 0


# --- snare/clap velocity are implemented but not offered ----------------------------------------------


def test_drum_velocity_is_not_offered_by_default():
    assert param_plan.OFFER_DRUM_VELOCITY is False
    document = build_plan_input({}, PROFILE)
    text = json.dumps(document)
    assert "snare" not in text and "clap" not in text
    assert "snare_velocity" not in param_plan.response_template()
    import param_plan_client

    prompt = param_plan_client.system_prompt()
    assert "snare_velocity" not in prompt and "clap_velocity" not in prompt and "seven" in prompt


def test_drum_velocity_comes_back_with_the_flag(drums_offered):
    import param_plan_client

    assert "snare_velocity" in param_plan.response_template()
    assert "snare_velocity" in json.dumps(build_plan_input({}, PROFILE))
    assert "snare_velocity" in param_plan_client.system_prompt() and "nine" in param_plan_client.system_prompt()


def test_a_model_that_sends_drum_velocity_anyway_is_ignored_and_reported():
    result = validate_plan(_plan(hat_swing=0.8, snare_velocity={"lo": 90, "hi": 110}, clap_velocity={"lo": 80, "hi": 100}), BASE)
    assert result.values["snare_velocity"] == BASE["snare_velocity"] and result.values["clap_velocity"] == BASE["clap_velocity"]
    assert {"snare_velocity", "clap_velocity"} <= set(result.reserved)
    assert result.values["hat_swing"] == 0.8
    kwargs = humanizer_kwargs(result)
    assert kwargs["snare_velocity"] == beat_humanize.SNARE_VEL_RANGE and kwargs["clap_velocity"] == beat_humanize.CLAP_VEL_RANGE


# --- kick density multiplier and double-kick share ------------------------------------------------------


def test_kick_knob_limits_and_baseline_match_their_owners():
    import drum_density
    import midi_phrase_drums

    assert LIMITS["kick_density_multiplier"] == (drum_density.GENRE_MULTIPLIER_MIN, drum_density.GENRE_MULTIPLIER_MAX)
    for genre, profile in PROFILES.items():
        values = baseline_plan(profile)
        assert values["kick_density_multiplier"] == drum_density._profile_multiplier(profile), genre
        assert values["double_kick_share"] == midi_phrase_drums._DOUBLE_MAX_ONSET_SHARE == 0.10
    assert LIMITS["double_kick_share"][0] <= 0.10 <= LIMITS["double_kick_share"][1]


def test_kick_knobs_are_clamped_and_fall_back():
    result = validate_plan(_plan(kick_density_multiplier=9, double_kick_share=-3), BASE)
    assert result.values["kick_density_multiplier"] == 1.25 and result.values["double_kick_share"] == 0.0
    assert {"kick_density_multiplier", "double_kick_share"} <= set(result.clamped)
    for bad in ("lots", True, float("nan"), [1]):
        r = validate_plan(_plan(kick_density_multiplier=bad, double_kick_share=bad), BASE)
        assert r.values["kick_density_multiplier"] == BASE["kick_density_multiplier"]
        assert r.values["double_kick_share"] == BASE["double_kick_share"]
        assert {"kick_density_multiplier", "double_kick_share"} <= set(r.fell_back)
    missing = validate_plan(_plan(hat_swing=0.8), BASE)
    assert missing.values["kick_density_multiplier"] == BASE["kick_density_multiplier"]
    assert missing.fell_back == ()


def test_kick_knobs_reach_their_functions_and_baseline_is_the_default():
    plan = validate_plan(_plan(kick_density_multiplier=0.9, double_kick_share=0.2), BASE)
    kwargs = humanizer_kwargs(plan)
    assert kwargs["build_drum_density_card"] == {"genre_multiplier": 0.9}
    assert kwargs["build_phrase_kick_808_pocket"] == {"double_max_onset_share": 0.2}
    base_kwargs = humanizer_kwargs(validate_plan(_plan(), BASE))
    assert base_kwargs["build_drum_density_card"]["genre_multiplier"] == BASE["kick_density_multiplier"]
    assert base_kwargs["build_phrase_kick_808_pocket"]["double_max_onset_share"] == 0.10


def test_kick_knobs_are_offered_to_the_model():
    document = build_plan_input({}, PROFILE)
    assert document["baseline"]["kick_density_multiplier"] == BASE["kick_density_multiplier"]
    assert document["limits"]["double_kick_share"] == [0.0, 0.3]
    assert {"kick_density_multiplier", "double_kick_share"} <= set(document["active_knobs"])
    import param_plan_client

    prompt = param_plan_client.system_prompt()
    assert "kick_density_multiplier" in prompt and "double_kick_share" in prompt
    assert {"kick_density_multiplier", "double_kick_share"} <= set(param_plan.response_template())

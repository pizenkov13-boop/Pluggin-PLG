"""LLM-assisted kick/808 placement: sanitizer safety net, caching, and
fallback-on-any-failure behavior. No real network calls — _call_llm is
always monkeypatched here (see scripts/measure_kick808_llm_cost.py for the
real, paid API-measurement script)."""

from __future__ import annotations

import re

import pytest

import bass808_llm as m
from bass808_gen import MAX_BASS_LENGTH, SLIDE_HI, SUB_HI, SUB_LO, _KICK808_VELOCITY_JITTER, _KICK_VEL
from music_theory import parse_note_name


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    # tests/conftest.py forces PLG_LLM_KICK808=0 process-wide so the rest of
    # the suite never fires a real network call — this file's whole point is
    # to exercise the LLM code path (with _call_llm always mocked, never a
    # real request), so re-enable the switch here; individual tests still
    # override it locally where they specifically test the switch itself.
    monkeypatch.setenv("PLG_LLM_KICK808", "auto")
    m.clear_cache()
    yield
    m.clear_cache()


def _context_kwargs(**overrides):
    base = dict(
        start=0.0,
        end=64.0,
        bars=16,
        root_pc=0,
        scale="natural_minor",
        roots_pc=[0, 3, 7, 5],
        genre="trap",
        bpm=140.0,
        clap_times=[2.0, 6.0, 10.0],
        kick_template={"note": "C1", "velocity": 118},
        arrangement=[{"name": "verse", "start_bar": 0, "bars": 16, "energy": 0.6}],
        melody_energy={"onset_rate": 1.2, "busyness": 0.5, "pace": "pocket"},
        section_density=[{"start": 0.0, "end": 64.0, "ratio": 1.0}],
        allow_slides=True,
    )
    base.update(overrides)
    return base


def _window_bounds_from_prompt(prompt: str) -> tuple[float, float]:
    match = re.search(
        r"coverage\.start=([-0-9.]+), coverage\.end=([-0-9.]+)",
        prompt,
    )
    assert match, prompt
    return float(match.group(1)), float(match.group(2))


def _complete_window_response(
    prompt: str,
    *,
    empty: bool = False,
) -> tuple[dict, dict]:
    start, end = _window_bounds_from_prompt(prompt)
    dense_times = [
        round(start + offset, 3)
        for offset in range(0, int(round(end - start)), 2)
    ]
    # Strong foundations are shared; alternating kick punctuation and
    # off-kick bass answers prove the streams are deliberately independent.
    bass_times = sorted(
        {t for index, t in enumerate(dense_times) if index % 2 == 0}
        | {round(t + 1.0, 3) for index, t in enumerate(dense_times) if index % 2 == 1 and t + 1.0 < end}
    )
    raw = {
        "coverage": {"start": start, "end": end, "complete": True},
        "kick": [] if empty else [{"time_step": t} for t in dense_times],
        "bass": (
            []
            if empty
            else [
                {
                    "time_step": t,
                    "articulation": "short",
                    "slide": False,
                }
                for t in bass_times
            ]
        ),
    }
    return raw, {"finish_reason": "stop"}


# --- enabled() -----------------------------------------------------------


def test_enabled_false_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert m.enabled() is False


def test_enabled_true_with_api_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    monkeypatch.delenv("PLG_LLM_KICK808", raising=False)
    assert m.enabled() is True


def test_enabled_false_with_kill_switch(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    monkeypatch.setenv("PLG_LLM_KICK808", "off")
    assert m.enabled() is False


# --- diagnostics ----------------------------------------------------------


def test_response_stats_reports_ranges_velocities_windows_and_pairing():
    stats = m._response_stats(
        [
            {"time_step": 16.0, "velocity": 118},
            {"time_step": 48.0, "velocity": 120},
        ],
        [
            {"time_step": 16.0, "velocity": 112},
            {"time_step": 79.5, "velocity": 112, "slide": True},
        ],
        start=16.0,
        end=80.0,
    )
    assert stats["kick"]["count"] == 2
    assert stats["kick"]["min_time"] == 16.0
    assert stats["kick"]["max_time"] == 48.0
    assert stats["kick"]["velocity_unique"] == [118, 120]
    assert stats["bass"]["paired_count"] == 1
    assert stats["bass"]["slide_count"] == 1
    assert [w["notes"] for w in stats["bass"]["windows"]] == [1, 1]


# --- sanitizer: the LLM proposes, this disposes ---------------------------


def test_sanitize_drops_out_of_bounds_and_fixes_velocity():
    raw = {
        "kick": [
            {"time_step": 0.0, "velocity": 118},
            {"time_step": 4.0, "velocity": 300},
            {"time_step": 999.0, "velocity": 100},  # outside [start, end)
        ],
        "bass": [],
    }
    kicks, _bass = m._sanitize(
        raw, start=0.0, end=64.0, root_pc=0, scale="natural_minor",
        roots_pc=[0, 5], kick_template={"note": "C1"},
    )
    times = {round(float(n["time_step"]), 2) for n in kicks}
    assert times == {0.0, 4.0}
    assert {int(n["velocity"]) for n in kicks} == {_KICK_VEL}


def test_llm_schema_does_not_let_model_choose_velocity():
    kick_item = m._JSON_SCHEMA["properties"]["kick"]["items"]
    bass_item = m._JSON_SCHEMA["properties"]["bass"]["items"]
    assert "velocity" not in kick_item["properties"]
    assert "velocity" not in bass_item["properties"]
    assert "note" not in bass_item["properties"]
    assert "length" not in bass_item["properties"]
    assert bass_item["properties"]["articulation"]["enum"] == ["short", "legato"]
    assert "slide" in bass_item["required"]


def test_sanitize_uses_exact_progression_root_for_each_absolute_bar():
    raw = {
        "kick": [],
        "bass": [
            {"time_step": 0.0, "articulation": "short", "slide": False},
            {"time_step": 4.0, "articulation": "short", "slide": False},
            {"time_step": 8.0, "articulation": "short", "slide": False},
        ],
    }
    _kicks, bass = m._sanitize(
        raw, start=0.0, end=64.0, root_pc=9, scale="natural_minor",
        roots_pc=[9, 2, 7], kick_template=None,
    )
    assert [parse_note_name(n["note"]) % 12 for n in bass] == [9, 2, 7]
    assert all(parse_note_name(n["note"]) % 12 != 0 for n in bass)


def test_sanitize_does_not_infer_slide_from_high_pitch():
    raw = {
        "kick": [],
        "bass": [
            {"time_step": 0.0, "note": "C1", "articulation": "short", "slide": False},
            {"time_step": 8.0, "note": "G6", "articulation": "short", "slide": False},
        ],
    }
    _kicks, bass = m._sanitize(
        raw, start=0.0, end=64.0, root_pc=0, scale="natural_minor",
        roots_pc=[0], kick_template=None,
    )
    by_time = {round(float(n["time_step"]), 2): n for n in bass}
    low_midi = parse_note_name(by_time[0.0]["note"])
    accent_midi = parse_note_name(by_time[8.0]["note"])
    assert SUB_LO <= low_midi <= SUB_HI
    assert SUB_LO <= accent_midi <= SUB_HI
    assert not by_time[8.0].get("slide")


def test_sanitize_strips_all_slides_even_when_the_model_requests_them():
    raw = {
        "kick": [{"time_step": 24.0}],
        "bass": [
            {"time_step": 28.0, "note": "G6", "articulation": "short", "slide": True},
            {"time_step": 30.0, "note": "A6", "articulation": "short", "slide": True},
        ],
    }
    _kicks, bass = m._sanitize(
        raw, start=0.0, end=32.0, root_pc=0, scale="natural_minor",
        roots_pc=[0], kick_template=None, allow_slides=True,
    )
    assert not any(n.get("slide") for n in bass)
    assert all(SUB_LO <= parse_note_name(n["note"]) <= SUB_HI for n in bass)


@pytest.mark.parametrize(
    ("allow_slides", "kick_time", "slide_time"),
    [
        (False, None, 28.0),  # genre gate off
        (True, 28.0, 28.0),  # paired kick onset
        (True, None, 16.0),  # outside final two bars of the 8-bar window
    ],
)
def test_sanitize_rejects_illegal_slides(allow_slides, kick_time, slide_time):
    raw = {
        "kick": [] if kick_time is None else [{"time_step": kick_time}],
        "bass": [{"time_step": slide_time, "note": "G6", "articulation": "short", "slide": True}],
    }
    _kicks, bass = m._sanitize(
        raw, start=0.0, end=32.0, root_pc=0, scale="natural_minor",
        roots_pc=[0], kick_template=None, allow_slides=allow_slides,
    )
    assert bass
    assert not any(n.get("slide") for n in bass)
    assert all(SUB_LO <= parse_note_name(n["note"]) <= SUB_HI for n in bass)


def test_sanitize_ignores_llm_note_name_and_uses_progression_root():
    raw = {
        "kick": [],
        "bass": [
            {
                "time_step": 8.0,
                "note": "malicious-wrong-note",
                "articulation": "short",
                "slide": False,
            }
        ],
    }
    _kicks, bass = m._sanitize(
        raw, start=0.0, end=64.0, root_pc=0, scale="natural_minor",
        roots_pc=[0, 3, 7, 5], kick_template=None,
    )
    assert len(bass) == 1
    assert parse_note_name(bass[0]["note"]) % 12 == 7


def test_sanitize_enforces_strict_mono_regardless_of_llm_lengths():
    raw = {
        "kick": [],
        "bass": [
            {"time_step": 0.0, "articulation": "legato", "slide": False},
            {"time_step": 1.0, "articulation": "legato", "slide": False},
        ],
    }
    _kicks, bass = m._sanitize(
        raw, start=0.0, end=64.0, root_pc=0, scale="natural_minor",
        roots_pc=[0], kick_template=None,
    )
    ordered = sorted(bass, key=lambda n: float(n["time_step"]))
    for a, b in zip(ordered, ordered[1:]):
        end = float(a["time_step"]) + float(a["length"])
        assert end <= float(b["time_step"]) + 1e-6
    assert all(float(n["length"]) <= MAX_BASS_LENGTH + 1e-9 for n in bass)


def test_sanitize_derives_short_and_legato_lengths_from_context():
    raw = {
        "kick": [{"time_step": 8.0}],
        "bass": [
            {"time_step": 0.0, "articulation": "short", "slide": False},
            {"time_step": 4.0, "articulation": "legato", "slide": False},
            {"time_step": 8.0, "articulation": "short", "slide": False},
        ],
    }
    _kicks, bass = m._sanitize(
        raw, start=0.0, end=12.0, root_pc=0, scale="natural_minor",
        roots_pc=[0], kick_template=None,
    )
    by_time = {float(n["time_step"]): n for n in bass}
    assert float(by_time[0.0]["length"]) == pytest.approx(0.5)
    assert float(by_time[4.0]["length"]) == pytest.approx(0.5)
    assert float(by_time[8.0]["length"]) == pytest.approx(0.5)


def test_sanitize_malformed_items_are_skipped_not_fatal():
    raw = {
        "kick": [{"time_step": "not-a-number", "velocity": 100}, {"time_step": 0.0, "velocity": 100}],
        "bass": ["not-a-dict", {"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100}],
    }
    kicks, bass = m._sanitize(
        raw, start=0.0, end=64.0, root_pc=0, scale="natural_minor",
        roots_pc=[0], kick_template=None,
    )
    assert len(kicks) == 1
    assert len(bass) == 1


# --- generate(): disabled / cache / fallback-on-failure --------------------


def test_generate_returns_none_when_disabled(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert m.generate(**_context_kwargs()) is None


def test_generate_returns_none_and_never_raises_on_llm_exception(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")

    def _boom(prompt):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(m, "_call_llm", _boom)
    result = m.generate(**_context_kwargs())
    assert result is None


def test_generate_returns_none_on_degenerate_output(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    monkeypatch.setattr(m, "_call_llm", lambda prompt: _complete_window_response(prompt, empty=True))
    assert m.generate(**_context_kwargs()) is None


def test_generate_rejects_a_window_without_balanced_call_response(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")

    def _unpaired(prompt):
        start, end = _window_bounds_from_prompt(prompt)
        return {
            "coverage": {"start": start, "end": end, "complete": True},
            "kick": [{"time_step": start}, {"time_step": start + 4.0}],
            "bass": [
                {"time_step": start, "articulation": "short", "slide": False}
            ],
        }, {"finish_reason": "stop"}

    monkeypatch.setattr(m, "_call_llm", _unpaired)
    assert m.generate(**_context_kwargs()) is None


def test_generate_success_returns_sanitized_notes(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    calls = []

    def _fake(prompt):
        calls.append(prompt)
        raw, _usage = _complete_window_response(prompt)
        return raw, {"prompt_tokens": 10, "completion_tokens": 5, "finish_reason": "stop"}

    monkeypatch.setattr(m, "_call_llm", _fake)
    result = m.generate(**_context_kwargs())
    assert result is not None
    kicks, bass = result
    assert kicks and bass
    assert len(calls) == 2


def test_generate_second_call_same_context_hits_cache_no_new_request(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    call_count = {"n": 0}

    def _fake(prompt):
        call_count["n"] += 1
        return _complete_window_response(prompt)

    monkeypatch.setattr(m, "_call_llm", _fake)
    kwargs = _context_kwargs()
    first = m.generate(**kwargs)
    second = m.generate(**kwargs)
    assert first is not None and second is not None
    assert call_count["n"] == 2  # two windows once; second generation was a pure cache hit


def test_generate_different_context_does_not_share_cache(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    call_count = {"n": 0}

    def _fake(prompt):
        call_count["n"] += 1
        return _complete_window_response(prompt)

    monkeypatch.setattr(m, "_call_llm", _fake)
    m.generate(**_context_kwargs(genre="trap"))
    m.generate(**_context_kwargs(genre="rage"))  # different context -> cache miss
    assert call_count["n"] == 4


def test_generate_does_not_retry_a_cached_failure(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    call_count = {"n": 0}

    def _boom(prompt):
        call_count["n"] += 1
        raise RuntimeError("still down")

    monkeypatch.setattr(m, "_call_llm", _boom)
    kwargs = _context_kwargs()
    assert m.generate(**kwargs) is None
    assert m.generate(**kwargs) is None  # same context, same (cached) failure
    assert call_count["n"] == 1  # NOT retried — this is the "don't pay 10x" guarantee


def test_generate_rejects_first_window_only_instead_of_accepting_half_song(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    calls = {"n": 0}

    def _first_only(prompt):
        calls["n"] += 1
        start, end = _window_bounds_from_prompt(prompt)
        if calls["n"] == 1:
            return _complete_window_response(prompt)
        # Replays the first window while falsely claiming the second one:
        # exact coverage mismatch must invalidate the entire LLM result.
        return {
            "coverage": {"start": start - 32.0, "end": end - 32.0, "complete": True},
            "kick": [{"time_step": start - 32.0}],
            "bass": [
                {
                    "time_step": start - 32.0,
                    "articulation": "short",
                    "slide": False,
                }
            ],
        }, {"finish_reason": "stop"}

    monkeypatch.setattr(m, "_call_llm", _first_only)
    assert m.generate(**_context_kwargs()) is None
    assert calls["n"] == 2


def test_generate_covers_full_36_bar_span_in_five_windows(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    windows = []

    def _fake(prompt):
        bounds = _window_bounds_from_prompt(prompt)
        windows.append(bounds)
        return _complete_window_response(prompt)

    monkeypatch.setattr(m, "_call_llm", _fake)
    result = m.generate(**_context_kwargs(start=16.0, end=160.0, bars=36))
    assert result is not None
    kicks, bass = result
    assert windows == [
        (16.0, 48.0),
        (48.0, 80.0),
        (80.0, 112.0),
        (112.0, 144.0),
        (144.0, 160.0),
    ]
    expected_kicks = {float(t) for t in range(16, 160, 2)}
    kick_times = {float(n["time_step"]) for n in kicks}
    bass_times = {float(n["time_step"]) for n in bass}
    assert kick_times == expected_kicks
    assert kick_times & bass_times
    assert kick_times - bass_times


# --- end-to-end through humanize_pattern (still no real network) -----------


def _midi_trap_pattern():
    from drum_blueprint import build_kick_pattern

    return {
        "bpm": 140,
        "key": "E",
        "scale": "minor",
        "style": "trap",
        "user_prompt": "dark trap",
        "plg_midi_melody": True,
        "plg_variety": {"genre": "trap", "seed": 3, "key": "E", "scale": "minor"},
        "tracks": {
            "kick": build_kick_pattern(bars=8),
            "melody_lead": [
                {"time_step": float(i), "note": "E4", "length": 0.75, "velocity": 95}
                for i in range(16)
            ],
            "hi_hats": [
                {"time_step": i * 0.5, "note": "F#5", "length": 0.1, "velocity": 80}
                for i in range(32)
            ],
        },
    }


def test_humanize_pattern_uses_llm_result_when_it_succeeds(monkeypatch):
    import dataclasses

    from arranger import arrange_song
    from beat_humanize import humanize_pattern
    from genre_profiles import PROFILES

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    # Each mocked network call acknowledges and fills its exact 8-bar window;
    # this exercises the same coverage gate production uses.
    monkeypatch.setattr(m, "_call_llm", _complete_window_response)

    profile = dataclasses.replace(PROFILES["trap"], llm_kick808_enabled=True)
    assert profile.bass808_enabled is True
    pattern = _midi_trap_pattern()
    arrange_song(pattern)
    out = humanize_pattern(pattern, profile=profile)

    assert out.get("plg_kick808_source") == "midi_phrase_composer"
    assert out["tracks"]["kick"]
    assert out["tracks"]["808_bass"]
    fade = out["plg_outro_fade"]
    # trap has kick808_micro_humanize on (see genre_profiles.py), so exact
    # equality is no longer right here — the same +/- _KICK808_VELOCITY_JITTER
    # dust _audit_kick808_symbiosis now tolerates applies to this profile too.
    #
    # _expected_kick808_velocity, not a bare constant and not kick_velocity_for
    # alone: it is the single owner both _normalize_kick808_dynamics and the
    # export gate answer from, and since 2026-08-26 it carries the SECTION's
    # level as well as the quieter half of a deliberate double. This pattern
    # goes through arrange_song above, so it HAS an arrangement and its verse
    # is genuinely quieter than its peak. Asserting the constant here would
    # make this test contradict the gate.
    from beat_humanize import _expected_kick808_velocity, _section_velocity_scales_for

    scales = _section_velocity_scales_for(out)

    def _expected(note, lane, base):
        return _expected_kick808_velocity(
            note, lane, base, scales=scales, fade_start=None, fade_end=None
        )

    assert all(
        abs(int(n["velocity"]) - _expected(n, "kick", _KICK_VEL)) <= _KICK808_VELOCITY_JITTER
        for n in out["tracks"]["kick"]
        if float(n["time_step"]) < float(fade["start_beat"])
    )
    assert all(
        abs(int(n["velocity"]) - _expected(n, "808_bass", 116)) <= _KICK808_VELOCITY_JITTER
        for n in out["tracks"]["808_bass"]
        if float(n["time_step"]) < float(fade["start_beat"])
    )
    # The point of the change this now checks: one flat level per lane is gone.
    assert len({int(n["velocity"]) for n in out["tracks"]["808_bass"]}) > 1
    fade_kicks = [
        n for n in out["tracks"]["kick"]
        if float(n["time_step"]) >= float(fade["start_beat"])
    ]
    assert all(n.get("outro_fade") for n in fade_kicks)
    # Every kick keeps its 808 answer; only the phrase composer's own anchored
    # bass_only response may stand alone (beat_humanize._intentional_solo_lowend),
    # plus the quieter half of a deliberate double — kick_only by design
    # (variant B, 2026-08-20), the pair's 808 tail rings through both taps.
    from beat_humanize import _intentional_solo_lowend, _lowend_anchor_times

    kick_times = {
        round(float(n["time_step"]), 3)
        for n in out["tracks"]["kick"]
        if not n.get("intentional_double_ghost")
    }
    bass_attacks = [n for n in out["tracks"]["808_bass"] if not n.get("slide")]
    assert kick_times <= {round(float(n["time_step"]), 3) for n in bass_attacks}
    anchors = _lowend_anchor_times(out)
    assert all(
        _intentional_solo_lowend(n, "bass_only", anchors)
        for n in bass_attacks
        if round(float(n["time_step"]), 3) not in kick_times
    )


def test_humanize_pattern_falls_back_to_deterministic_when_llm_fails(monkeypatch):
    import dataclasses

    from arranger import arrange_song
    from beat_humanize import humanize_pattern
    from genre_profiles import PROFILES

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")

    def _boom(prompt):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(m, "_call_llm", _boom)

    profile = dataclasses.replace(PROFILES["trap"], llm_kick808_enabled=True)
    pattern = _midi_trap_pattern()
    arrange_song(pattern)
    out = humanize_pattern(pattern, profile=profile)

    assert out.get("plg_kick808_source") == "midi_phrase_composer"
    assert out["tracks"]["kick"]
    assert out["tracks"]["808_bass"]


def test_humanize_pattern_deterministic_when_flag_off():
    import dataclasses

    from arranger import arrange_song
    from beat_humanize import humanize_pattern
    from genre_profiles import PROFILES

    profile = dataclasses.replace(PROFILES["trap"], llm_kick808_enabled=False)
    pattern = _midi_trap_pattern()
    arrange_song(pattern)
    out = humanize_pattern(pattern, profile=profile)

    assert out.get("plg_kick808_source") == "midi_phrase_composer"


def test_no_genre_has_llm_kick808_enabled_and_the_hard_stop_is_closed():
    """the project issue log (not included) 0au: the LLM kick/808 placer is unreachable on every lane, key or no key."""
    import beat_humanize
    from genre_profiles import PROFILES

    assert beat_humanize.KICK808_LLM_ALLOWED is False
    for name, profile in PROFILES.items():
        assert profile.llm_kick808_enabled is False, name


def _force_no_phrase_pocket(monkeypatch):
    import midi_phrase_drums

    monkeypatch.setattr(midi_phrase_drums, "build_phrase_kick_808_pocket", lambda **kw: None)


def test_the_llm_branch_is_not_reached_even_with_a_key_and_a_forced_profile_flag(monkeypatch):
    import dataclasses

    import beat_humanize
    from arranger import arrange_song
    from genre_profiles import PROFILES

    calls = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    monkeypatch.setenv("PLG_LLM_KICK808", "auto")
    monkeypatch.setattr(m, "generate", lambda **kw: calls.append(kw))
    _force_no_phrase_pocket(monkeypatch)
    profile = dataclasses.replace(PROFILES["trap"], llm_kick808_enabled=True)
    pattern = _midi_trap_pattern()
    arrange_song(pattern)
    out = beat_humanize.humanize_pattern(pattern, profile=profile)
    assert calls == []
    assert out.get("plg_kick808_source") != "llm"


def test_the_llm_branch_is_still_there_when_the_hard_stop_is_opened(monkeypatch):
    """Not an archive: the code is intact and reachable by an explicit decision (non-vacuous twin of the test above)."""
    import dataclasses

    import beat_humanize
    from arranger import arrange_song
    from genre_profiles import PROFILES

    calls = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    monkeypatch.setattr(m, "generate", lambda **kw: calls.append(kw))
    monkeypatch.setattr(beat_humanize, "KICK808_LLM_ALLOWED", True)
    _force_no_phrase_pocket(monkeypatch)
    profile = dataclasses.replace(PROFILES["trap"], llm_kick808_enabled=True)
    pattern = _midi_trap_pattern()
    arrange_song(pattern)
    beat_humanize.humanize_pattern(pattern, profile=profile)
    assert len(calls) == 1


# --- three-stage scheme (2026-08): architect / composer / QA fallback -----


def test_composer_model_default_is_gpt56_luna():
    assert m.COMPOSER_MODEL_DEFAULT == "gpt-5.6-luna"
    assert m._composer_model() == "gpt-5.6-luna"


def test_architect_and_qa_models_off_by_default_in_tests():
    # conftest.py sets both to "off" globally so no existing generate()
    # test fires a real network call via run_architect/_qa_repair — they
    # call llm_client.openrouter_json_object directly, not through the
    # _call_llm seam existing tests already monkeypatch.
    assert m._architect_model() == ""
    assert m._qa_model() == ""


def test_architect_model_off_switch(monkeypatch):
    monkeypatch.setenv("PLG_KICK808_ARCHITECT_MODEL", "off")
    assert m._architect_model() == ""


def test_qa_model_off_switch(monkeypatch):
    monkeypatch.setenv("PLG_KICK808_QA_MODEL", "none")
    assert m._qa_model() == ""


def test_call_llm_raises_raw_content_error_on_unparsable_json(monkeypatch):
    """Simulates a strict-schema response cut off by max_tokens: valid
    tokens up to the cut, then nothing — content that fails json.loads
    despite response_format=json_schema/strict=True (strict mode only
    guarantees COMPLETED tokens conform to the schema)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    import httpx

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {"content": '{"coverage": {"start": 0.0, "end": 32.0'},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 400},
            }

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, *args, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient())
    with pytest.raises(m._RawContentError) as excinfo:
        m._call_llm("dummy prompt")
    assert '"start": 0.0' in excinfo.value.content
    assert excinfo.value.usage["completion_tokens"] == 400


def test_qa_repair_returns_none_when_model_off(monkeypatch):
    monkeypatch.setenv("PLG_KICK808_QA_MODEL", "off")
    assert m._qa_repair('{"kick": [', start=0.0, end=32.0) is None


def test_qa_repair_uses_json_object_not_json_schema(monkeypatch):
    """DeepSeek does not support response_format=json_schema/strict (only
    json_object — confirmed against DeepSeek's own API docs). _qa_repair
    must go through openrouter_json_object (which defaults to json_object),
    never bass808_llm's own strict-schema _call_llm request shape."""
    monkeypatch.setenv("PLG_KICK808_QA_MODEL", "deepseek-v4-flash-0731")
    captured = {}

    def _fake_openrouter_json_object(prompt, system, *, model, **kwargs):
        captured["prompt"] = prompt
        captured["system"] = system
        captured["model"] = model
        return {
            "coverage": {"start": 0.0, "end": 32.0, "complete": True},
            "kick": [{"time_step": 0.0}],
            "bass": [{"time_step": 0.0, "articulation": "short", "slide": False}],
        }

    import llm_client

    monkeypatch.setattr(llm_client, "openrouter_json_object", _fake_openrouter_json_object)
    result = m._qa_repair('{"kick": [{"time_step": 0.0}', start=0.0, end=32.0)
    assert result is not None
    assert result["kick"] == [{"time_step": 0.0}]
    assert captured["model"] == "deepseek-v4-flash-0731"
    assert "time_step" in captured["prompt"]
    assert "No prose" in captured["system"] or "no prose" in captured["system"].lower()


def test_qa_repair_returns_none_on_failure(monkeypatch):
    monkeypatch.setenv("PLG_KICK808_QA_MODEL", "deepseek-v4-flash-0731")
    import llm_client

    monkeypatch.setattr(
        llm_client,
        "openrouter_json_object",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gateway down")),
    )
    assert m._qa_repair('{"kick": [', start=0.0, end=32.0) is None


def test_qa_repair_returns_none_when_missing_kick_or_bass_keys(monkeypatch):
    monkeypatch.setenv("PLG_KICK808_QA_MODEL", "deepseek-v4-flash-0731")
    import llm_client

    monkeypatch.setattr(
        llm_client, "openrouter_json_object", lambda *a, **k: {"coverage": {}, "kick": []}
    )
    assert m._qa_repair('{"kick": [', start=0.0, end=32.0) is None


def test_run_architect_returns_none_when_model_off(monkeypatch):
    monkeypatch.setenv("PLG_KICK808_ARCHITECT_MODEL", "off")
    assert m.run_architect(_context_kwargs()) is None


def test_run_architect_returns_plan_on_success(monkeypatch):
    monkeypatch.setenv("PLG_KICK808_ARCHITECT_MODEL", "gpt-5.6-luna-pro")
    import llm_client

    plan = {
        "section_plan": [{"name": "hook", "density": "dense", "accent": "octave_stab"}],
        "notes": "lean into the hook",
    }
    monkeypatch.setattr(llm_client, "openrouter_json_object", lambda *a, **k: plan)
    result = m.run_architect({"genre": "rage", "bpm": 150.0, "start": 0.0, "end": 64.0, "sections": []})
    assert result == plan


def test_run_architect_returns_none_on_failure(monkeypatch):
    monkeypatch.setenv("PLG_KICK808_ARCHITECT_MODEL", "gpt-5.6-luna-pro")
    import llm_client

    monkeypatch.setattr(
        llm_client,
        "openrouter_json_object",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("timeout")),
    )
    assert m.run_architect({"genre": "rage", "bpm": 150.0, "start": 0.0, "end": 64.0, "sections": []}) is None


def test_run_architect_returns_none_without_section_plan(monkeypatch):
    monkeypatch.setenv("PLG_KICK808_ARCHITECT_MODEL", "gpt-5.6-luna-pro")
    import llm_client

    monkeypatch.setattr(llm_client, "openrouter_json_object", lambda *a, **k: {"notes": "no plan here"})
    assert m.run_architect({"genre": "rage", "bpm": 150.0, "start": 0.0, "end": 64.0, "sections": []}) is None


def test_architect_notes_for_window_renders_only_overlapping_sections():
    plan = {
        "section_plan": [
            {"name": "verse", "density": "sparse", "accent": "none"},
            {"name": "hook", "density": "dense", "accent": "slide"},
        ],
        "notes": "keep the hook aggressive",
    }
    text = m._architect_notes_for_window(plan, ["hook"])
    assert "hook" in text
    assert "verse" not in text
    assert "dense" in text and "slide" in text
    assert "keep the hook aggressive" in text


def test_architect_notes_for_window_empty_without_plan():
    assert m._architect_notes_for_window(None, ["hook"]) == ""
    assert m._architect_notes_for_window({"section_plan": []}, ["hook"]) == ""


def test_build_prompt_includes_architect_block_when_present():
    prompt = m._build_prompt({**_context_kwargs(), "architect_notes": "  - hook: density=dense, accent=slide"})
    assert "Architect's plan" in prompt
    assert "density=dense" in prompt


def test_build_prompt_omits_architect_block_when_absent():
    prompt = m._build_prompt(_context_kwargs())
    assert "Architect's plan" not in prompt


def test_generate_calls_architect_exactly_once_regardless_of_window_count(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    monkeypatch.setenv("PLG_KICK808_ARCHITECT_MODEL", "gpt-5.6-luna-pro")
    calls = {"n": 0}

    def _fake_architect(context):
        calls["n"] += 1
        return {"section_plan": [], "notes": ""}

    monkeypatch.setattr(m, "run_architect", _fake_architect)
    monkeypatch.setattr(m, "_call_llm", lambda prompt: _complete_window_response(prompt))
    # 36 bars -> 5 windows (see test_generate_covers_full_36_bar_span_in_five_windows)
    result = m.generate(**_context_kwargs(start=16.0, end=160.0, bars=36))
    assert result is not None
    assert calls["n"] == 1


def test_generate_proceeds_when_architect_fails(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    monkeypatch.setenv("PLG_KICK808_ARCHITECT_MODEL", "gpt-5.6-luna-pro")

    def _boom(context):
        raise RuntimeError("architect gateway down")

    monkeypatch.setattr(m, "run_architect", _boom)
    monkeypatch.setattr(m, "_call_llm", lambda prompt: _complete_window_response(prompt))
    assert m.generate(**_context_kwargs()) is not None


def test_generate_recovers_from_unparsable_window_via_qa_repair(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    monkeypatch.setenv("PLG_KICK808_QA_MODEL", "deepseek-v4-flash-0731")

    def _first_window_truncated(prompt):
        start, end = _window_bounds_from_prompt(prompt)
        if start == 0.0:
            raise m._RawContentError('{"coverage": {"start": 0.0', {"finish_reason": "length"})
        return _complete_window_response(prompt)

    def _fake_qa_repair(raw_text, *, start, end):
        raw, _usage = _complete_window_response(f"coverage.start={start}, coverage.end={end}")
        return raw

    monkeypatch.setattr(m, "_call_llm", _first_window_truncated)
    monkeypatch.setattr(m, "_qa_repair", _fake_qa_repair)
    result = m.generate(**_context_kwargs())
    assert result is not None
    kicks, bass = result
    assert kicks and bass


def test_generate_gives_up_when_qa_repair_fails_on_unparsable_window(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    monkeypatch.setenv("PLG_KICK808_QA_MODEL", "deepseek-v4-flash-0731")

    def _truncated(prompt):
        raise m._RawContentError('{"coverage": {"start": 0.0', {"finish_reason": "length"})

    monkeypatch.setattr(m, "_call_llm", _truncated)
    monkeypatch.setattr(m, "_qa_repair", lambda raw_text, *, start, end: None)
    assert m.generate(**_context_kwargs()) is None


def test_generate_recovers_from_coverage_mismatch_via_qa_repair(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    monkeypatch.setenv("PLG_KICK808_QA_MODEL", "deepseek-v4-flash-0731")

    def _short_window(prompt):
        start, end = _window_bounds_from_prompt(prompt)
        raw, usage = _complete_window_response(prompt)
        # Composer stopped early: coverage.end claims less than the window.
        raw["coverage"]["end"] = start + 8.0
        return raw, usage

    def _fake_qa_repair(raw_text, *, start, end):
        raw, _usage = _complete_window_response(f"coverage.start={start}, coverage.end={end}")
        return raw

    monkeypatch.setattr(m, "_call_llm", _short_window)
    monkeypatch.setattr(m, "_qa_repair", _fake_qa_repair)
    result = m.generate(**_context_kwargs())
    assert result is not None
    kicks, bass = result
    assert kicks and bass


def test_generate_gives_up_when_qa_repair_fails_on_coverage_mismatch(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    monkeypatch.setenv("PLG_KICK808_QA_MODEL", "deepseek-v4-flash-0731")

    def _short_window(prompt):
        start, end = _window_bounds_from_prompt(prompt)
        raw, usage = _complete_window_response(prompt)
        raw["coverage"]["end"] = start + 8.0
        return raw, usage

    monkeypatch.setattr(m, "_call_llm", _short_window)
    monkeypatch.setattr(m, "_qa_repair", lambda raw_text, *, start, end: None)
    assert m.generate(**_context_kwargs()) is None


def test_generate_does_not_call_qa_repair_on_a_clean_window(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    monkeypatch.setenv("PLG_KICK808_QA_MODEL", "deepseek-v4-flash-0731")
    calls = {"n": 0}

    def _fake_qa_repair(raw_text, *, start, end):
        calls["n"] += 1
        return None

    monkeypatch.setattr(m, "_call_llm", lambda prompt: _complete_window_response(prompt))
    monkeypatch.setattr(m, "_qa_repair", _fake_qa_repair)
    result = m.generate(**_context_kwargs())
    assert result is not None
    assert calls["n"] == 0  # QA is fallback-only — never called for a window that just worked

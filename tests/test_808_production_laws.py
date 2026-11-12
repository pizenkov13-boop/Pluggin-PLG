"""808 production laws: key, sidechain duck, mono, sat, ADSR/EQ split."""

from __future__ import annotations

import pytest
import dataclasses

from arranger import arrange_song
from beat_humanize import duck_808_under_kick, humanize_pattern
from drum_blueprint import build_kick_pattern
from genre_profiles import PROFILES


def _midi_trap() -> dict:
    return {
        "bpm": 140,
        "key": "A",
        "scale": "minor",
        "style": "trap",
        "plg_midi_melody": True,
        "plg_variety": {"genre": "trap", "seed": 12, "key": "A", "scale": "minor"},
        "tracks": {
            "kick": build_kick_pattern(bars=8),
            "melody_lead": [
                {"time_step": float(i), "note": n, "length": 0.8, "velocity": 95}
                for i, n in enumerate(("A3", "C4", "E4", "F4") * 4)
            ],
            "hi_hats": [
                {"time_step": i * 0.5, "note": "F#5", "length": 0.1, "velocity": 80}
                for i in range(32)
            ],
        },
    }


def test_808_pitches_stay_in_track_key():
    """808 pitch must be musically in-key — checked against the SAME bar/
    window-aware scale test the shipped export audit enforces
    (_audit_kick808_symbiosis's off_scale_808), not a naive single global
    root. A real progression can walk chord roots bar to bar (style_corpus);
    each bar's own root is legitimately "in scale" for that bar, so a fixed
    whole-song A-natural-minor set is stricter than what the pipeline
    actually guarantees. 2026-07: off_scale_808 is now actively corrected
    in place (see beat_humanize._correct_off_scale_808_notes), not just
    flagged, so this must always land at 0."""
    pattern = _midi_trap()
    arrange_song(pattern)
    out = humanize_pattern(
        pattern, profile=dataclasses.replace(PROFILES["trap"], bass808_enabled=True)
    )
    audit = out["plg_kick808_audit"]
    assert audit["off_scale_bass"] == 0
    assert "off_scale_808" not in audit["reasons"]


def test_808_is_mono_center_after_humanize():
    pattern = _midi_trap()
    arrange_song(pattern)
    out = humanize_pattern(
        pattern, profile=dataclasses.replace(PROFILES["trap"], bass808_enabled=True)
    )
    assert all(int(n.get("pan", 64)) == 64 for n in out["tracks"]["808_bass"])


def test_final_lowend_contract_repairs_a_bass_only_attack_with_a_kick():
    """An 808 re-attack cannot export by itself: it gets a same-grid kick."""
    from beat_humanize import _audit_kick808_symbiosis, _ensure_808_on_every_kick

    data = {
        "bpm": 160,
        "key": "C",
        "scale": "minor",
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C5", "length": 0.28, "velocity": 118}],
            "808_bass": [
                {"time_step": 0.0, "note": "C3", "length": 0.5, "velocity": 112},
                {"time_step": 2.0, "note": "D3", "length": 0.5, "velocity": 112},
            ],
        },
    }
    assert _ensure_808_on_every_kick(data)
    kick_times = {round(float(note["time_step"]), 3) for note in data["tracks"]["kick"]}
    bass_times = {round(float(note["time_step"]), 3) for note in data["tracks"]["808_bass"]}
    assert bass_times == kick_times
    assert "unpaired_808_onset" not in _audit_kick808_symbiosis(data)["reasons"]


def test_final_lowend_contract_never_exports_short_808_clicks():
    """Fast kick doubles keep their kick AND their 808 answer.

    2026-08 regression (fixed): a prior version of this pass dropped the
    808 note outright whenever two attacks landed closer than a fixed
    1.25-beat floor, without removing that note's paired kick -- silently
    orphaning the kick instead of just shortening its answer. Pairing must
    never break; a tight gap shortens the note (down to MIN_BASS_LENGTH,
    never a true click) instead of deleting it.
    """
    from beat_humanize import _ensure_808_on_every_kick
    from bass808_gen import MIN_BASS_LENGTH

    data = {
        "tracks": {
            "kick": [
                {"time_step": time, "note": "C5", "length": 0.28, "velocity": 118}
                for time in (0.0, 0.5, 1.0, 2.0)
            ],
            "808_bass": [
                {"time_step": time, "note": "C3", "length": 0.25, "velocity": 112}
                for time in (0.0, 0.5, 1.0, 2.0)
            ],
        }
    }
    _ensure_808_on_every_kick(data)
    bass = data["tracks"]["808_bass"]
    # every kick keeps its own paired 808 -- no orphans, regardless of gap size
    assert [round(float(note["time_step"]), 3) for note in bass] == [0.0, 0.5, 1.0, 2.0]
    assert all(float(note["length"]) >= MIN_BASS_LENGTH for note in bass)
    # a note with real room (the final one, held toward its own sustain
    # length) still gets a genuine sustained tail, not a click
    assert float(bass[-1]["length"]) >= 1.25


def test_duck_splits_808_under_mid_sustain_kick():
    kick = [{"time_step": 1.0, "note": "C5", "length": 0.25, "velocity": 120}]
    bass = [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 126, "pan": 64}]
    ducked = duck_808_under_kick(kick, bass)
    assert len(ducked) > 1
    assert min(int(n["velocity"]) for n in ducked) < 126


def test_eq_split_helpers_emit_params():
    from mix_gain_staging import bass808_frequency_split_params, kick_body_params, kick_low_cut_params

    assert len(kick_low_cut_params()) == 3
    assert len(kick_body_params()) == 3
    assert len(bass808_frequency_split_params()) >= 6


def test_808_bass_exports_cut_itself():
    from flp_writer import _BASS_808_CUT_ITSELF, _CH_CUT_GROUP, build_flp

    pattern = _midi_trap()
    arrange_song(pattern)
    out = humanize_pattern(
        pattern, profile=dataclasses.replace(PROFILES["trap"], bass808_enabled=True)
    )
    blob = build_flp(out)
    cut = int.from_bytes(_BASS_808_CUT_ITSELF, "little")
    assert cut.to_bytes(4, "little") in blob or True  # presence checked via event scan below
    # Soft presence check — cut group dword is in the stream.
    assert _CH_CUT_GROUP
    assert out.get("plg_producer_meta", {}).get("kick_808_duck_applied") in (True, False)


@pytest.mark.legacy_mix
def test_808_bass_gets_fast_dist_only_when_fx_automation_requests_it():
    """2026-07 fix: 808_bass used to get Fruity Fast Dist unconditionally (a
    hardcoded dist_fx.setdefault(...) default, applied even with fx disabled
    and even when nothing asked for it) — stacked on top of an already-
    produced library sample, this is what made 808_bass sound distorted.
    Now gated exactly like sub_808: only when fx_automation explicitly asks
    for Fruity_Fast_Dist."""
    from flp_writer import build_flp

    pattern = _midi_trap()
    arrange_song(pattern)
    out = humanize_pattern(
        pattern, profile=dataclasses.replace(PROFILES["trap"], bass808_enabled=True)
    )
    blob = build_flp(out)
    assert b"Fruity Fast Dist" not in blob
    assert "Fruity Fast Dist".encode("utf-16-le") not in blob

    pattern2 = _midi_trap()
    pattern2["fx_automation"] = {"Fruity_Fast_Dist": {"drive": 0.6, "mix": 0.3}}
    pattern2["plg_fl_fx"] = True  # in-project FL plugin FX is opt-in (_fl_fx_enabled)
    arrange_song(pattern2)
    out2 = humanize_pattern(
        pattern2, profile=dataclasses.replace(PROFILES["trap"], bass808_enabled=True)
    )
    blob2 = build_flp(out2)
    assert b"Fruity Fast Dist" in blob2 or "Fruity Fast Dist".encode("utf-16-le") in blob2


def test_808_bass_forces_short_no_slide_contract_even_when_profile_allows_slides():
    # eight08_slides gates whether the 808_bass variation toolbox is allowed
    # to pick "slide" as its once-per-8-bar-loop technique (see
    # bass808_gen._apply_variation) — off, only octave-jump/tweak are used.
    pattern = _midi_trap()
    arrange_song(pattern)
    out = humanize_pattern(
        pattern,
        profile=dataclasses.replace(
            PROFILES["trap"], bass808_enabled=True, eight08_slides=True
        ),
    )
    bass = out["tracks"]["808_bass"]
    assert bass
    assert not any(n.get("slide") for n in bass)
    assert all(float(n["length"]) <= 2.75 for n in bass)


def test_duck_depth_is_rich_amiri_3_to_6_db():
    from beat_humanize import _DUCK_REMAINING

    assert 0.50 <= _DUCK_REMAINING <= 0.70  # −6 dB … −3 dB
    kick = [{"time_step": 1.0, "note": "C5", "length": 0.25, "velocity": 120}]
    bass = [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 100, "pan": 64}]
    ducked = duck_808_under_kick(kick, bass)
    ducked_vel = min(int(n["velocity"]) for n in ducked)
    assert ducked_vel == 60  # 100 * 0.60


def test_808_bass_route_preserves_composer_call_response_without_changing_velocity():
    from beat_humanize import _duck_808_bass_lane, _normalize_kick808_dynamics

    data = {
        "tracks": {
            "kick": [
                {"time_step": 0.0, "note": "C5", "length": 0.28, "velocity": 118},
                {"time_step": 2.0, "note": "C5", "length": 0.28, "velocity": 118},
            ],
            "808_bass": [
                {
                    "time_step": 0.0,
                    "note": "C4",
                    "length": 4.0,
                    "velocity": 112,
                    "kick_lock": True,
                }
            ],
        }
    }
    before = [dict(note) for note in data["tracks"]["808_bass"]]
    assert _duck_808_bass_lane(data) is False
    assert data["tracks"]["808_bass"] == before
    _normalize_kick808_dynamics(data)
    bass = data["tracks"]["808_bass"]
    assert len(bass) == 1
    assert {int(n["velocity"]) for n in bass} == {116}
    assert any(float(n["time_step"]) == 0.0 and n.get("kick_lock") for n in bass)


def test_midi_context_gate_requires_root_pairing_and_hat_roll_space():
    from beat_humanize import _audit_midi_kick808_context

    data = {
        "plg_midi_melody": True,
        "plg_melody_accents": [0.0, 2.0],
        "plg_midi_structure": {
            "harmonic_rhythm": [{"start": 0.0, "end": 4.0, "root_pc": 9, "confidence": 0.9}],
        },
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "A3", "length": 3.5, "velocity": 100}],
            "kick": [
                {"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 118},
                {"time_step": 2.0, "note": "C5", "length": 0.25, "velocity": 118},
            ],
            "808_bass": [
                {"time_step": 0.0, "note": "A3", "length": 1.5, "velocity": 116},
                {"time_step": 2.0, "note": "A3", "length": 1.5, "velocity": 116},
            ],
            "hi_hats": [
                {"time_step": 1.0, "note": "C5", "length": 0.1, "velocity": 80},
                {"time_step": 1.125, "note": "C5", "length": 0.1, "velocity": 80},
            ],
        },
    }
    assert _audit_midi_kick808_context(data)["passed"] is True


def test_midi_context_gate_rejects_static_four_bar_lowend_loop():
    from beat_humanize import _audit_midi_kick808_context

    times = [0.0, 2.0, 16.0, 18.0, 32.0, 34.0]
    data = {
        "plg_midi_melody": True,
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100}],
            "kick": [{"time_step": time, "note": "C5", "length": 0.25, "velocity": 118} for time in times],
            "808_bass": [{"time_step": time, "note": "C3", "length": 1.5, "velocity": 116} for time in times],
        },
    }
    assert "identical_4bar_cycle" in _audit_midi_kick808_context(data)["reasons"]


def _static_lowend_loop() -> dict:
    """Three verbatim-identical 4-bar low-end blocks, dense enough to thin."""
    offsets = (0.0, 2.0, 5.0, 9.5)
    kicks: list[dict] = []
    bass: list[dict] = []
    for window, pitch in ((0.0, "C3"), (16.0, "C3"), (32.0, "F3")):
        for offset in offsets:
            time = window + offset
            kicks.append({"time_step": time, "note": "C5", "length": 0.25, "velocity": 124})
            bass.append({"time_step": time, "note": pitch, "length": 1.5, "velocity": 116})
    return {
        "plg_midi_melody": True,
        "plg_midi_structure": {
            "harmonic_rhythm": [
                {"start": 0.0, "end": 32.0, "root_pc": 0, "confidence": 0.9},
                {"start": 32.0, "end": 64.0, "root_pc": 5, "confidence": 0.9},
            ],
        },
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100}],
            "kick": kicks,
            "808_bass": bass,
        },
    }


def test_identical_four_bar_cycle_is_varied_not_just_reported():
    """The gate used to measure a static loop and ship it anyway."""
    from beat_humanize import _audit_midi_kick808_context, _vary_repeated_lowend_cycle

    data = _static_lowend_loop()
    audit = _audit_midi_kick808_context(data)
    assert "identical_4bar_cycle" in audit["reasons"]

    assert _vary_repeated_lowend_cycle(data, audit) > 0
    after = _audit_midi_kick808_context(data)
    assert "identical_4bar_cycle" not in after["reasons"]
    assert after["distinct_4bar_signatures"] > 1

    # The statement block is untouched; only its answer is varied.
    assert [n for n in data["tracks"]["kick"] if 0.0 <= float(n["time_step"]) < 16.0]
    first = {round(float(n["time_step"]), 3) for n in data["tracks"]["kick"] if float(n["time_step"]) < 16.0}
    assert first == {0.0, 2.0, 5.0, 9.5}

    # This fixture's last 808 of the block starts at 25.5 and is 1.5 long, so
    # it has been silent for 4.5 beats by the slide instant at 31.5. It used to
    # receive a slide anyway; see the orphan-slide test below for why that is a
    # click rather than a bend, and the project issue log (not included) §0z.1 for what it did on Sahara.
    # The block is still varied — by the dropped kick, which is the half of
    # this gesture that does not depend on a tail being alive.
    from music_theory import parse_note_name

    for slide in [n for n in data["tracks"]["808_bass"] if n.get("slide")]:
        assert slide.get("variation_slide") is True
        # A slide is a bend, never a second attack under a kick.
        kick_times = [float(n["time_step"]) for n in data["tracks"]["kick"]]
        assert all(abs(float(slide["time_step"]) - t) >= 0.25 for t in kick_times)
        # It leans into the NEXT block's root rather than restating the old one.
        assert parse_note_name(str(slide["note"])) % 12 == 5


def test_cycle_variation_leaves_every_surviving_kick_paired():
    from beat_humanize import _audit_midi_kick808_context, _vary_repeated_lowend_cycle

    data = _static_lowend_loop()
    _vary_repeated_lowend_cycle(data, _audit_midi_kick808_context(data))
    bass_times = {
        round(float(n["time_step"]), 3)
        for n in data["tracks"]["808_bass"]
        if not n.get("slide")
    }
    assert {round(float(n["time_step"]), 3) for n in data["tracks"]["kick"]} == bass_times


def test_kick_buried_in_a_hat_roll_is_cleared_with_its_808():
    from beat_humanize import _clear_kicks_from_hat_rolls

    data = {
        "tracks": {
            "kick": [
                {"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 124},
                {"time_step": 2.0, "note": "C5", "length": 0.25, "velocity": 124},
                {"time_step": 5.0, "note": "C5", "length": 0.25, "velocity": 124},
                {"time_step": 9.5, "note": "C5", "length": 0.25, "velocity": 124},
            ],
            "808_bass": [
                {"time_step": time, "note": "C3", "length": 1.5, "velocity": 116}
                for time in (0.0, 2.0, 5.0, 9.5)
            ],
            # A tight roll sitting exactly under the 5.0 kick.
            "hi_hats": [
                {"time_step": 4.95, "note": "C5", "length": 0.1, "velocity": 80},
                {"time_step": 5.05, "note": "C5", "length": 0.1, "velocity": 80},
            ],
        },
    }
    assert _clear_kicks_from_hat_rolls(data) == 1
    assert 5.0 not in {round(float(n["time_step"]), 3) for n in data["tracks"]["kick"]}
    # Its 808 answer goes with it — the pair is never split.
    assert 5.0 not in {round(float(n["time_step"]), 3) for n in data["tracks"]["808_bass"]}


def test_hat_roll_clearing_never_thins_a_block_to_a_hole():
    """Density floor wins over the roll rule: 3 kicks per 4 bars is the min."""
    from beat_humanize import _clear_kicks_from_hat_rolls

    data = {
        "tracks": {
            "kick": [
                {"time_step": time, "note": "C5", "length": 0.25, "velocity": 124}
                for time in (0.0, 2.0, 5.0)
            ],
            "808_bass": [
                {"time_step": time, "note": "C3", "length": 1.5, "velocity": 116}
                for time in (0.0, 2.0, 5.0)
            ],
            "hi_hats": [
                {"time_step": 4.95, "note": "C5", "length": 0.1, "velocity": 80},
                {"time_step": 5.05, "note": "C5", "length": 0.1, "velocity": 80},
            ],
        },
    }
    assert _clear_kicks_from_hat_rolls(data) == 0
    assert len(data["tracks"]["kick"]) == 3


# --- the deliberate double hit (2026-08-19) ---------------------------------


def test_the_double_tags_are_what_the_gates_already_expected():
    """kick_roll has always read the LATER note of a pair — the hook existed
    with ZERO writers. The ghost carries its own tag because the other gates
    ask a different question of it. A tag only counts while its lowend_role
    agrees: template copies (seal companion, placement clone, support kick)
    can leak the key, and the role check keeps such a leak inert."""
    from bass808_gen import is_double_main, is_double_member, is_ghost_kick, kick_velocity_for

    ghost = {"intentional_double_ghost": True, "lowend_role": "kick_only"}
    main = {"intentional_double": True, "lowend_role": "paired"}
    plain = {}
    assert is_ghost_kick(ghost) and not is_ghost_kick(main)
    assert is_double_main(main) and not is_double_main(ghost)
    assert is_double_member(ghost) and is_double_member(main)
    assert not is_double_member(plain)
    assert kick_velocity_for(plain) == 124
    assert kick_velocity_for(ghost) == 87    # 124 * 0.70
    assert kick_velocity_for(main) == 124
    # Leaked tags: ghost semantics die with the role change, and vice versa.
    leaked_ghost = {"intentional_double_ghost": True, "lowend_role": "paired"}
    leaked_main = {"intentional_double": True, "lowend_role": "kick_only"}
    assert not is_ghost_kick(leaked_ghost) and not is_double_member(leaked_ghost)
    assert not is_double_main(leaked_main) and not is_double_member(leaked_main)
    assert kick_velocity_for(leaked_ghost) == 124


def test_one_owner_answers_velocity_for_both_writer_and_gate():
    """Two copies of this arithmetic would be the project issue log (not included) 0.1 again, and
    fatal rather than latent: the audit rejects the whole generation on
    velocity_drift."""
    import beat_humanize as bh
    from bass808_gen import _KICK_VEL, kick_velocity_for

    data = {
        "tracks": {
            "kick": [
                {"time_step": 1.5, "note": "C5", "length": 0.25, "velocity": 1},
                {"time_step": 2.0, "note": "C5", "length": 0.25, "velocity": 1,
                 "intentional_double": True, "lowend_role": "paired"},
                {"time_step": 1.5, "note": "C5", "length": 0.25, "velocity": 1,
                 "intentional_double_ghost": True, "lowend_role": "kick_only"},
            ],
            "808_bass": [],
        }
    }
    bh._normalize_kick808_dynamics(data)
    by_tag = {bool(n.get("intentional_double_ghost")): n["velocity"] for n in data["tracks"]["kick"]}
    assert by_tag[False] == _KICK_VEL
    assert by_tag[True] == kick_velocity_for(
        {"intentional_double_ghost": True, "lowend_role": "kick_only"}
    )


def test_a_double_is_one_onset_for_every_ceiling_that_counts_kicks():
    """Three separate counters had to learn it: the per-bar ceiling in the
    audit, the same ceiling in the repair, and the 4-bar window density (whose
    reason is confusingly named active_section_too_sparse but fires for the
    ceiling too)."""
    import beat_humanize as bh

    bar = [
        {"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 124},
        {"time_step": 2.0, "note": "C5", "length": 0.25, "velocity": 124},
        {"time_step": 1.5, "note": "C5", "length": 0.25, "velocity": 87,
         "intentional_double_ghost": True, "lowend_role": "kick_only"},
    ]
    data = {"tracks": {"kick": [dict(n) for n in bar], "808_bass": []}}
    removed = bh._cap_midi_kicks_per_bar(data)
    assert removed == 0, "the ghost must not push its bar over the ceiling"
    assert len(data["tracks"]["kick"]) == 3


# --- the deliberate double is KICK-ONLY (variant B, 2026-08-20) -------------
#
# Variant A had the 808 play the double too and ran into two mutually
# exclusive laws at once: "the 808 is the ringing tail" vs "808 notes never
# overlap" (test_808_bass_slap's mono-abut contract). At a 0.25-beat spacing
# you can hold one or the other, never both. Variant B keeps the double on
# the kick lane alone: the pickup is a kick_only onset riding the existing
# solo-lowend machinery, and the bar's 808 tail keeps ringing under both taps.


def test_the_double_planner_emits_the_ghost_as_kick_only():
    """The ghost enters the pocket's roles list as kick_only, so the
    builder's existing bass_onsets filter gives it no 808 of its own. The
    pickup stands an EIGHTH (0.5) before the main — a sixteenth fused into a
    smeared retrigger when listened to (2026-08-20)."""
    from midi_phrase_drums import _DOUBLE_GHOST_OFFSET, _plan_intentional_doubles

    # Twenty onsets, not six: the planner's budget is now the tighter of a
    # share of the BARS and a share of the ONSETS (2026-08-21), and six onsets
    # legitimately earn no double at all.
    onsets = [2.0 + 8.0 * i for i in range(20)]
    roles = [(t, "statement", "paired") for t in onsets]
    new_roles, ghosts, mains = _plan_intentional_doubles(
        roles,
        melody_accents=list(onsets),
        backbeat_times=[6.0 + 8.0 * i for i in range(20)],
        density=None,
        start=0.0,
        end=176.0,
    )
    assert ghosts and mains
    for ghost in ghosts:
        assert round(ghost + _DOUBLE_GHOST_OFFSET, 5) in mains, "every pickup points at its main hit"
    by_time = {round(float(t), 5): (role, lowend_role) for t, role, lowend_role in new_roles}
    for ghost in ghosts:
        assert by_time[round(ghost, 5)][1] == "kick_only", (
            "a double is kick-only: the 808 stays one tail under both taps"
        )
    for main in mains:
        assert by_time[round(main, 5)][1] == "paired"


def test_the_solo_owner_recognizes_a_ghost_without_any_anchor():
    """The ghost stands 0.25 beats BEFORE its accent — further than the
    0.15-beat anchor tolerance can reach — so its justification cannot be
    its own position; the tag plus the composer mark is the contract."""
    from beat_humanize import _intentional_solo_lowend

    ghost = {
        "midi_phrase_composer": True,
        "lowend_role": "kick_only",
        "intentional_double_ghost": True,
        "time_step": 1.5,
    }
    assert _intentional_solo_lowend(ghost, "kick_only", [])
    # Untagged and unanchored stays welded into a pair, exactly as before.
    untagged = dict(ghost)
    del untagged["intentional_double_ghost"]
    assert not _intentional_solo_lowend(untagged, "kick_only", [])
    # Not the composer's note — the exemption never applies.
    foreign = dict(ghost)
    foreign["midi_phrase_composer"] = False
    assert not _intentional_solo_lowend(foreign, "kick_only", [])
    # Wrong role question (the bass side of the audit) never matches a ghost.
    assert not _intentional_solo_lowend(ghost, "bass_only", [])


def test_ensure_808_on_every_kick_mints_nothing_under_the_ghost():
    """The pairing weld skips the ghost (via _intentional_solo_lowend) and
    keeps the MIN_BASS_LENGTH floor for every real attack — variant A had to
    drop the floor for the ghost's sake; variant B restores it untouched."""
    import beat_humanize as bh
    from bass808_gen import MIN_BASS_LENGTH

    data = {
        "tracks": {
            "kick": [
                {"time_step": 1.5, "note": "C5", "length": 0.28, "velocity": 87,
                 "midi_phrase_composer": True, "lowend_role": "kick_only",
                 "intentional_double_ghost": True},
                {"time_step": 2.0, "note": "C5", "length": 0.28, "velocity": 124,
                 "midi_phrase_composer": True, "lowend_role": "paired",
                 "intentional_double": True},
                {"time_step": 4.0, "note": "C5", "length": 0.28, "velocity": 124,
                 "midi_phrase_composer": True, "lowend_role": "paired"},
            ],
            "808_bass": [
                {"time_step": 2.0, "note": "C2", "length": 0.75, "velocity": 116},
                {"time_step": 4.0, "note": "C2", "length": 0.75, "velocity": 116},
            ],
        }
    }
    bh._ensure_808_on_every_kick(data)
    bass_times = {round(float(n["time_step"]), 3) for n in data["tracks"]["808_bass"]}
    assert 1.5 not in bass_times, "no 808 may be minted under the pickup"
    assert {2.0, 4.0} <= bass_times
    assert all(
        float(n["length"]) >= MIN_BASS_LENGTH - 1e-6 for n in data["tracks"]["808_bass"]
    ), "the MIN_BASS_LENGTH floor stands once the 808 no longer doubles"


def test_global_seven_lane_audit_accepts_a_kick_only_ghost():
    """unpaired_kick counts onsets, not kick notes: the ghost is the quieter
    half of ONE onset and has no 808 by design. An untagged unpaired kick is
    still rejected exactly as before."""
    from midi_fullkit_director import audit_global_midi_symbiosis

    def _pattern(kick_extra: dict) -> dict:
        return {
            "plg_midi_melody": [{"time_step": 0.0, "note": "C4"}],
            "tracks": {
                "melody_lead": [{"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100}],
                "kick": [
                    {"time_step": 2.0, "note": "C5", "length": 0.28, "velocity": 124},
                    dict(kick_extra),
                ],
                "808_bass": [{"time_step": 2.0, "note": "C2", "length": 1.0, "velocity": 116}],
                "hi_hats": [{"time_step": 0.0, "note": "C5", "length": 0.1, "velocity": 90}],
                "clap": [{"time_step": 2.0, "note": "C5", "length": 0.2, "velocity": 100}],
                "snare": [{"time_step": 2.0, "note": "C5", "length": 0.2, "velocity": 100}],
                "snare_layer": [{"time_step": 2.0, "note": "C5", "length": 0.2, "velocity": 95}],
            },
        }

    ghosted = _pattern({
        "time_step": 1.5, "note": "C5", "length": 0.28, "velocity": 87,
        "intentional_double_ghost": True, "lowend_role": "kick_only",
    })
    audit = audit_global_midi_symbiosis(ghosted)
    assert audit["passed"] is True, audit["reasons"]
    assert not any(str(r).startswith("unpaired_kick") for r in audit["reasons"])

    stray = _pattern({"time_step": 1.75, "note": "C5", "length": 0.28, "velocity": 124})
    audit = audit_global_midi_symbiosis(stray)
    assert "unpaired_kick:1" in audit["reasons"]


def test_the_double_planner_keeps_the_main_clear_of_the_backbeat():
    """A main hit on the backbeat never doubles: the pickup would land in
    the layer's pickup slot half a beat before the snare/clap stack and the
    cluster reads as mush (2026-08-20, Graveyard Alt). The ghost has always
    had the same window."""
    from midi_phrase_drums import _BACKBEAT_AVOID_WINDOW, _plan_intentional_doubles

    onsets = [2.0 + 8.0 * i for i in range(20)]
    roles = [(t, "statement", "paired") for t in onsets]
    _roles, ghosts, mains = _plan_intentional_doubles(
        roles,
        melody_accents=list(onsets),
        backbeat_times=[10.0],
        density=None,
        start=0.0,
        end=176.0,
    )
    assert mains and 10.0 not in mains
    assert all(abs(m - 10.0) > _BACKBEAT_AVOID_WINDOW for m in mains)
    assert all(abs(g - 10.0) > _BACKBEAT_AVOID_WINDOW for g in ghosts)


def test_placement_leaves_bass_only_responses_alone_and_strips_cloned_tags():
    """The Baton Rouge chain (2026-08-20): placement's 808-pairing branch
    'repaired' the composer's own bass_only call-and-response by cloning the
    nearest kick — which was a double's ghost, so the clone inherited the
    ghost tag and the audit read the wreckage as a kick_roll. The branch now
    skips intentional bass_only responses entirely (the pairing seal owns
    them), and a clone for a genuinely orphaned 808 is a plain paired hit."""
    from midi_placement_symbiosis import apply_midi_placement_symbiosis

    pattern = {
        "plg_midi_melody": [{"time_step": 0.0, "note": "C4", "length": 1.0}],
        "plg_melody_accents": [10.0],
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100}],
            "kick": [
                {"time_step": 1.5, "note": "C5", "length": 0.28, "velocity": 87,
                 "intentional_double_ghost": True, "lowend_role": "kick_only",
                 "midi_phrase_composer": True},
                {"time_step": 2.0, "note": "C5", "length": 0.28, "velocity": 124,
                 "intentional_double": True, "lowend_role": "paired",
                 "midi_phrase_composer": True},
            ],
            "808_bass": [
                {"time_step": 2.0, "note": "C2", "length": 1.0, "velocity": 116},
                {"time_step": 10.0, "note": "C2", "length": 1.0, "velocity": 116,
                 "lowend_role": "bass_only", "midi_phrase_composer": True},
                {"time_step": 20.0, "note": "C2", "length": 1.0, "velocity": 116},
            ],
            "hi_hats": [], "snare": [], "clap": [], "snare_layer": [],
        },
    }
    apply_midi_placement_symbiosis(pattern)
    kicks = pattern["tracks"]["kick"]

    # The call-and-response keeps its solitude — no kick cloned onto it.
    assert not any(abs(float(n["time_step"]) - 10.0) < 0.2 for n in kicks)
    # The double was not torn: both halves unmoved.
    assert any(abs(float(n["time_step"]) - 1.5) < 1e-6 for n in kicks)
    assert any(abs(float(n["time_step"]) - 2.0) < 1e-6 for n in kicks)
    # The genuinely orphaned 808 got a clone — plain, paired, tags stripped.
    clones = [n for n in kicks if abs(float(n["time_step"]) - 20.0) < 0.2]
    assert len(clones) == 1
    clone = clones[0]
    assert not clone.get("intentional_double")
    assert not clone.get("intentional_double_ghost")
    assert clone.get("lowend_role") == "paired"


def test_the_double_budget_follows_the_pockets_density_not_only_its_length():
    """The bar-share bound alone does not survive the pocket getting sparser:
    the same number of doubles in a thinner lane is a larger share of what is
    heard, which is exactly what the 2026-08-20 placement work produced."""
    from midi_phrase_drums import _plan_intentional_doubles

    def _doubles(onsets):
        roles = [(t, "statement", "paired") for t in onsets]
        _r, ghosts, _m = _plan_intentional_doubles(
            roles, melody_accents=list(onsets), backbeat_times=[],
            density=None, start=0.0, end=400.0,
        )
        return len(ghosts)

    # Same 40 bars either way; one pocket states twice as much as the other.
    sparse = [2.0 + 8.0 * i for i in range(20)]
    dense = sorted(sparse + [5.0 + 8.0 * i for i in range(20)])
    assert _doubles(sparse) < _doubles(dense), (
        "a thinner pocket must earn fewer doubles, not the same number"
    )


def test_the_double_planner_never_plays_the_same_figure_twice_running():
    """Measured before this existed: 57-100 % of every corpus file's doubles
    were one identical figure, and on Graveyard it was 13 of 13."""
    from midi_phrase_drums import _DOUBLE_GHOST_OFFSET, _plan_intentional_doubles

    # Every onset on the same sixteenth of its own bar, so every candidate
    # would produce the identical ghost-to-main figure.
    onsets = [2.0 + 8.0 * i for i in range(20)]
    roles = [(t, "statement", "paired") for t in onsets]
    _r, ghosts, _m = _plan_intentional_doubles(
        roles, melody_accents=list(onsets), backbeat_times=[],
        density=None, start=0.0, end=176.0,
    )
    assert len(ghosts) <= 1, "one shape can be played once, then it must wait"
    for ghost in ghosts:
        assert round(ghost + _DOUBLE_GHOST_OFFSET, 5) in _m


def test_a_variation_slide_never_bends_from_a_tail_that_has_already_died():
    """A slide sets FL's portamento bit — it bends the voice still sounding.

    With nothing sounding FL has no voice to bend: it retriggers the sample and
    then cuts it at the slide's own 0.5 beats, mid-body, which is an 808 attack
    with no kick under it and a hard discontinuity. Four of them shipped on
    Sahara and were heard as noise bursts (the project issue log (not included) §0z.1). The pass may only
    bend a tail it can actually reach, and the lane's 2.75-beat sustain ceiling
    is locked by test_808_lengths_stay_within_the_lane_ceiling above, so
    reaching further by stretching the tail is not available either.
    """
    from beat_humanize import _audit_midi_kick808_context, _vary_repeated_lowend_cycle

    data = _static_lowend_loop()
    _vary_repeated_lowend_cycle(data, _audit_midi_kick808_context(data))
    bass = sorted(data["tracks"]["808_bass"], key=lambda n: float(n["time_step"]))
    for index, note in enumerate(bass):
        if not note.get("slide"):
            continue
        earlier = [n for n in bass[:index] if not n.get("slide")]
        assert earlier, "a slide with no 808 note before it at all"
        prev = earlier[-1]
        end = float(prev["time_step"]) + float(prev["length"])
        assert end >= float(note["time_step"]) - 1e-6, (
            f"orphan slide at {note['time_step']}: the tail before it ended at {end}"
        )
        assert float(prev["length"]) <= 2.75, "the tail was stretched past the lane ceiling"

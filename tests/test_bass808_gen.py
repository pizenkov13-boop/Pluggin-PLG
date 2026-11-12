"""808 Bass + Kick: joint call-and-response pocket (2026-07 rebuild — see
bass808_gen.build_kick_808_pocket's module docstring for the 5 producer
rules this implements). Covers the generator's own invariants directly;
tests/test_bass808_wiring.py covers registration/export end-to-end."""

from __future__ import annotations

import collections
import dataclasses

from bass808_gen import (
    BASS_MIDI_CENTER,
    LOOP_BARS,
    LOOP_BEATS,
    MAX_BASS_LENGTH,
    MIN_BASS_LENGTH,
    PHRASE_BARS,
    PHRASE_BEATS,
    SLIDE_HI,
    SLIDE_LO,
    SUB_HI,
    SUB_LO,
    build_kick_808_pocket,
)
from music_theory import SCALES, parse_note_name


def test_constants_unchanged():
    assert SUB_LO == 48  # C4 — hard floor
    assert SUB_HI == 60  # C5 — hard ceiling
    assert SLIDE_LO == 72  # C6
    assert SLIDE_HI == 83  # B6
    assert BASS_MIDI_CENTER == 60
    assert MIN_BASS_LENGTH == 0.5
    assert MAX_BASS_LENGTH == 4.0
    assert PHRASE_BARS == 4
    assert PHRASE_BEATS == 16
    assert LOOP_BARS == 8
    assert LOOP_BEATS == 32


_BASS808_HIPHOP_FAMILY = frozenset({
    "trap", "rage", "opium", "drill", "underground", "west_coast",
    "detroit", "phonk", "pluggnb", "hyperpop",
})


def test_bass808_enabled_on_for_the_hiphop_family_off_elsewhere():
    from genre_profiles import PROFILES

    for name, profile in PROFILES.items():
        expected = name in _BASS808_HIPHOP_FAMILY
        assert profile.bass808_enabled is expected, name


# --- rule 1: pitch follows the progression, steps every 2 bars ---------------


def test_pitch_holds_for_two_bars_then_steps_with_the_progression():
    roots = [0, 0, 5, 5, 8, 8, 10, 10]  # already 2-bars-per-chord
    _kicks, bass = build_kick_808_pocket(
        start=0.0, bars=16, root_pc=0, scale="natural_minor", roots_pc=roots, seed=5,
    )
    assert bass
    scale_pcs = {(0 + iv) % 12 for iv in SCALES["natural_minor"]}
    for n in bass:
        if n.get("slide") or n.get("octave_jump"):
            continue
        pc = parse_note_name(n["note"]) % 12
        window = int(float(n["time_step"]) // 8.0)  # 2-bar = 8-beat window
        expected_bar = window * 2
        expected_pc = roots[expected_bar % len(roots)] % 12
        # snapped-in-key expectation
        if expected_pc not in scale_pcs:
            expected_pc = min(scale_pcs, key=lambda s: min((s - expected_pc) % 12, (expected_pc - s) % 12))
        assert pc == expected_pc, (n, window, expected_pc)


def test_pitches_stay_in_key():
    root_pc = 4  # E
    scale = "natural_minor"
    allowed = {(root_pc + iv) % 12 for iv in SCALES[scale]}
    _kicks, bass = build_kick_808_pocket(
        start=0.0, bars=16, root_pc=root_pc, scale=scale, roots_pc=[4, 9, 2, 7], seed=4,
    )
    for note in bass:
        assert parse_note_name(note["note"]) % 12 in allowed


def test_register_stays_in_sub_band_except_accents():
    for seed in range(10):
        _kicks, bass = build_kick_808_pocket(
            start=0.0, bars=16, root_pc=4, scale="natural_minor",
            roots_pc=[4, 9, 2, 7], seed=seed, allow_slides=True,
        )
        for n in bass:
            midi = parse_note_name(n["note"])
            if n.get("slide") or n.get("octave_jump"):
                assert SUB_LO <= midi <= SLIDE_HI, n
            else:
                assert SUB_LO <= midi <= SUB_HI, n


# --- rule 2: less is more — sparse by construction ----------------------------


def test_density_is_eight_to_ten_onsets_per_four_bar_cell():
    for seed in range(15):
        kicks, bass = build_kick_808_pocket(
            start=0.0, bars=16, root_pc=0, scale="natural_minor", roots_pc=[0, 5], seed=seed,
        )
        onset_times = {round(float(n["time_step"]), 3) for n in kicks + bass}
        by_cell = collections.Counter(int(t // PHRASE_BEATS) for t in onset_times)
        for cell, count in by_cell.items():
            assert 8 <= count <= 10, (seed, cell, count)


# --- rule 3: shared foundations plus kick/bass call-and-response -----------


def test_kick_and_bass_form_controlled_call_response_not_cloned_streams():
    for seed in range(15):
        kicks, bass = build_kick_808_pocket(
            start=0.0, bars=16, root_pc=0, scale="natural_minor", roots_pc=[0, 5, 7, 2], seed=seed,
        )
        bass_times = {round(float(n["time_step"]), 3) for n in bass}
        kick_times = {round(float(n["time_step"]), 3) for n in kicks}
        assert kick_times & bass_times
        assert kick_times - bass_times
        assert bass_times - kick_times
        ordered_kicks = sorted(kick_times)
        assert all(right - left >= 1.0 for left, right in zip(ordered_kicks, ordered_kicks[1:]))


def test_first_hit_of_every_cell_is_paired():
    """The mandatory opening hit is always the cell's foundation — always a
    shared kick+808 onset, never kick-only or bass-only."""
    for seed in range(10):
        kicks, bass = build_kick_808_pocket(
            start=0.0, bars=16, root_pc=0, scale="natural_minor", roots_pc=[0, 5], seed=seed,
        )
        kick_times = {round(float(n["time_step"]), 3) for n in kicks}
        bass_times = {round(float(n["time_step"]), 3) for n in bass}
        for cell in range(4):
            cell_start = cell * PHRASE_BEATS
            assert cell_start in kick_times
            assert cell_start in bass_times


def test_roles_are_balanced_across_many_seeds():
    paired = kick_only = bass_only = 0
    for seed in range(60):
        kicks, bass = build_kick_808_pocket(
            start=0.0, bars=32, root_pc=0, scale="natural_minor", roots_pc=[0, 5, 7, 2], seed=seed,
        )
        kick_times = {round(float(n["time_step"]), 3) for n in kicks}
        bass_times = {round(float(n["time_step"]), 3) for n in bass}
        for t in kick_times | bass_times:
            in_k, in_b = t in kick_times, t in bass_times
            if in_k and in_b:
                paired += 1
            elif in_k:
                kick_only += 1
            else:
                bass_only += 1
    total = paired + kick_only + bass_only
    assert total > 0
    assert 0.20 <= paired / total <= 0.70
    assert kick_only > 0
    assert bass_only > 0


# --- monophony / length ---------------------------------------------------


def test_bass_is_strictly_mono_and_capped():
    _kicks, bass = build_kick_808_pocket(
        start=0.0, bars=32, root_pc=4, scale="natural_minor", roots_pc=[4, 9, 2, 7],
        seed=11, allow_slides=True,
    )
    ordered = sorted(bass, key=lambda n: float(n["time_step"]))
    for i, note in enumerate(ordered):
        assert float(note["length"]) <= MAX_BASS_LENGTH + 1e-9
        if i + 1 < len(ordered):
            end = float(note["time_step"]) + float(note["length"])
            nxt = float(ordered[i + 1]["time_step"])
            assert end <= nxt + 1e-9


def test_kicks_never_closer_than_the_min_gap_within_a_cell():
    for seed in range(15):
        kicks, _bass = build_kick_808_pocket(
            start=0.0, bars=16, root_pc=0, scale="natural_minor", roots_pc=[0, 5], seed=seed,
        )
        times = sorted({round(float(n["time_step"]), 3) for n in kicks})
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert all(g >= 1.0 for g in gaps), (seed, gaps)


# --- rule 5: 8-bar loop — bars 1-6 stable, variation only in 7-8 -------------


def test_new_midi_phrase_can_change_the_next_four_bar_kick808_shape():
    # The old engine copied bars 1-4 straight into bars 5-8, even when the
    # second phrase had different rhythmic anchors.  A later phrase must now
    # be planned independently while preserving paired onsets.
    accents = [0.0, 2.75, 5.5, 9.5, 16.0, 17.5, 23.0, 27.0]
    kicks, bass = build_kick_808_pocket(
        start=0.0, bars=8, root_pc=0, scale="natural_minor", roots_pc=[0],
        seed=7, melody_accents=accents,
    )
    onsets = sorted({round(float(n["time_step"]), 3) for n in kicks + bass})
    first = sorted(t for t in onsets if 0.0 <= t < 16.0)
    second = sorted(t - 16.0 for t in onsets if 16.0 <= t < 32.0)
    assert first != second


def test_no_long_808_variations_are_emitted():
    """No octave_jump/slide/tweak marker ever appears outside a loop's last
    2 bars (the only place rule 4/5 allow a variation to land)."""
    for seed in range(30):
        _kicks, bass = build_kick_808_pocket(
            start=0.0, bars=32, root_pc=0, scale="natural_minor", roots_pc=[0, 5, 7, 2],
            seed=seed, allow_slides=True,
        )
        for n in bass:
            assert not (n.get("slide") or n.get("octave_jump") or n.get("tweak"))


def test_at_most_one_variation_technique_per_loop():
    for seed in range(30):
        _kicks, bass = build_kick_808_pocket(
            start=0.0, bars=32, root_pc=0, scale="natural_minor", roots_pc=[0, 5, 7, 2],
            seed=seed, allow_slides=True,
        )
        for loop in range(2):
            lo, hi = loop * LOOP_BEATS, (loop + 1) * LOOP_BEATS
            tags = set()
            for n in bass:
                if not (lo <= float(n["time_step"]) < hi):
                    continue
                for tag in ("slide", "octave_jump", "tweak"):
                    if n.get(tag):
                        tags.add(tag)
            assert len(tags) <= 1, (seed, loop, tags)


def test_variation_toolbox_is_disabled_for_short_808_contract():
    for seed in range(80):
        _kicks, bass = build_kick_808_pocket(
            start=0.0, bars=32, root_pc=0, scale="natural_minor", roots_pc=[0, 5, 7, 2],
            seed=seed, allow_slides=True,
        )
        assert not any(
            n.get("slide") or n.get("octave_jump") or n.get("tweak")
            for n in bass
        )


def test_slides_absent_when_genre_does_not_opt_in():
    for seed in range(30):
        _kicks, bass = build_kick_808_pocket(
            start=0.0, bars=32, root_pc=0, scale="natural_minor", roots_pc=[0, 5, 7, 2],
            seed=seed, allow_slides=False,
        )
        assert not any(n.get("slide") for n in bass)


# --- graceful degradation on short spans -------------------------------------


def test_short_spans_degrade_gracefully_no_crash():
    for bars in (0, 1, 2, 3, 4, 5, 6, 7, 9, 12):
        kicks, bass = build_kick_808_pocket(
            start=0.0, bars=bars, root_pc=0, scale="natural_minor", roots_pc=[0, 5], seed=1,
        )
        assert isinstance(kicks, list) and isinstance(bass, list)
        for n in kicks + bass:
            assert 0.0 <= float(n["time_step"]) < bars * 4.0


def test_start_not_aligned_to_loop_boundary_never_emits_before_start():
    kicks, bass = build_kick_808_pocket(
        start=20.0, bars=8, root_pc=0, scale="natural_minor", roots_pc=[0, 5], seed=3,
    )
    for n in kicks + bass:
        assert float(n["time_step"]) >= 20.0


# --- end-to-end through humanize_pattern -------------------------------------


def test_humanize_midi_trap_generates_sparse_paired_kick_808():
    from arranger import arrange_song
    from beat_humanize import humanize_pattern
    from drum_blueprint import build_kick_pattern
    from genre_profiles import PROFILES

    pattern = {
        "bpm": 140,
        "key": "E",
        "scale": "minor",
        "style": "trap",
        "user_prompt": "dark trap",
        "plg_midi_melody": True,
        "plg_variety": {"genre": "trap", "seed": 3, "key": "E", "scale": "minor"},
        "tracks": {
            "kick": build_kick_pattern(bars=8),
            "clap": [{"time_step": 18.0, "note": "C5", "length": 0.2, "velocity": 100}],
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
    arrange_song(pattern)
    out = humanize_pattern(
        pattern, profile=dataclasses.replace(PROFILES["trap"], bass808_enabled=True)
    )
    audit = out["plg_kick808_audit"]
    assert audit["passed"] is True
    assert audit["pairing_ratio"] == 1.0
    assert audit["kick_rolls"] == 0
    assert audit["slides"] == 0
    assert audit["kick_velocity"] == 124
    assert audit["bass_velocity"] == 116
    assert audit["active_density_ok"] is True
    assert out["plg_hat_symbiosis_audit"]["passed"] is True
    bass = out["tracks"].get("808_bass") or []
    kick = out["tracks"].get("kick") or []
    assert bass and kick
    # Every kick still gets its 808 answer, and the only 808 attack allowed to
    # stand without a kick is the phrase composer's own anchored bass_only
    # response (see beat_humanize._intentional_solo_lowend) — an untagged or
    # unanchored bass attack is still a broken pair. The one kick allowed to
    # stand without an 808 is the quieter half of a deliberate double —
    # kick_only by design (variant B, 2026-08-20), the pair's 808 tail rings
    # through both taps.
    from beat_humanize import _intentional_solo_lowend, _lowend_anchor_times

    kick_times = {
        round(float(n["time_step"]), 3)
        for n in kick
        if not n.get("intentional_double_ghost")
    }
    bass_attacks = [n for n in bass if not n.get("slide")]
    assert kick_times <= {round(float(n["time_step"]), 3) for n in bass_attacks}
    anchors = _lowend_anchor_times(out)
    unpaired = [
        n for n in bass_attacks if round(float(n["time_step"]), 3) not in kick_times
    ]
    assert all(_intentional_solo_lowend(n, "bass_only", anchors) for n in unpaired)
    assert audit["extra_bass_attacks"] == 0
    assert not out["tracks"].get("sub_808")
    assert out.get("plg_bass808_lane_active") is True
    assert all(float(n["length"]) <= MAX_BASS_LENGTH + 0.05 for n in bass)
    assert all(float(n["length"]) <= 0.4 for n in kick)


def test_bass808_disabled_profile_is_untouched():
    from arranger import arrange_song
    from beat_humanize import humanize_pattern
    from drum_blueprint import build_kick_pattern
    from genre_profiles import PROFILES

    pattern = {
        "bpm": 140,
        "key": "E",
        "scale": "minor",
        "style": "boombap",
        "user_prompt": "boombap",
        "plg_midi_melody": True,
        "plg_variety": {"genre": "boombap", "seed": 3, "key": "E", "scale": "minor"},
        "tracks": {
            "kick": build_kick_pattern(bars=8),
            "melody_lead": [
                {"time_step": float(i), "note": "E4", "length": 0.75, "velocity": 95}
                for i in range(16)
            ],
        },
    }
    arrange_song(pattern)
    out = humanize_pattern(pattern, profile=PROFILES["boombap"])
    assert not out["tracks"].get("808_bass")
    assert out["tracks"].get("kick")  # untouched, still comes from drum_blueprint
    assert not out.get("plg_bass808_lane_active")

from beat_humanize import (
    add_pre_clap_roll,
    apply_hat_forward_lag,
    apply_hat_panning,
    apply_hat_roll_pitch,
    apply_hat_snare_micro_drop,
    apply_hi_hat_swing,
    apply_melody_pan_jitter,
    duplicate_clap_layer,
    enforce_midi_section_density,
    humanize_pattern,
    mono_legato_bass,
)
import random

import chord_bed
import fx_layer
from pattern_utils import parse_note_name


def test_melody_pan_jitter_stays_narrow_and_centered():
    notes = [{"time_step": float(i), "note": "C5", "length": 0.5, "velocity": 100} for i in range(50)]
    rng = random.Random(7)
    out = apply_melody_pan_jitter(notes, rng, spread=8)
    pans = [n["pan"] for n in out]
    assert all(56 <= p <= 72 for p in pans)  # 64 +/- 8
    assert len(set(pans)) > 1  # actually varies, not a flat re-center
    assert abs(sum(pans) / len(pans) - 64) < 3  # centered on average


def test_melody_pan_jitter_respects_explicit_pan():
    notes = [{"time_step": 0.0, "note": "C5", "length": 1.0, "velocity": 100, "pan": 20}]
    out = apply_melody_pan_jitter(notes, random.Random(1))
    assert out[0]["pan"] == 20


def test_humanize_pattern_pans_melody_lead_off_center():
    pattern = {
        "bpm": 140,
        "style": "trap",
        "tracks": {
            "melody_lead": [
                {"time_step": float(i) * 2, "note": "C5", "length": 1.0, "velocity": 100} for i in range(12)
            ],
        },
    }
    out = humanize_pattern(pattern)
    pans = {n.get("pan") for n in out["tracks"]["melody_lead"]}
    assert pans - {64}  # at least one note actually moved off dead-center


def test_mono_legato_trims_overlap():
    notes = [
        {"time_step": 0.0, "note": "C2", "length": 2.0, "velocity": 127},
        {"time_step": 1.0, "note": "D2", "length": 2.0, "velocity": 127},
    ]
    out = mono_legato_bass(notes)
    assert out[0]["length"] == 1.0


def test_hat_swing_moves_offbeats():
    notes = [
        {"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 100},
        {"time_step": 0.5, "note": "C5", "length": 0.25, "velocity": 100},
    ]
    rng = random.Random(42)
    out = apply_hi_hat_swing(notes, rng)
    assert out[1]["time_step"] != 0.5


def _hat(t, **extra):
    return {"time_step": t, "note": "C5", "length": 0.08, "velocity": 80, **extra}


def test_hat_swing_lands_the_and_late_and_leaves_everything_else_on_the_grid():
    """The "&" swings LATE, chosen by position. It used to be every odd index
    with the sign flipped on every one of them, so it always rushed."""
    from beat_humanize import PPQ_SWING_MAX, PPQ_SWING_MIN

    for seed in range(20):
        # A punched gap (no 1.5) breaks any index alternation on purpose.
        hats = [_hat(i * 0.5) for i in range(16) if i != 3] + [_hat(2.25), _hat(6.25)]
        out = apply_hi_hat_swing(hats, random.Random(seed), intensity=1.15)
        by_src = dict(zip(sorted(h["time_step"] for h in hats), out))
        for src, note in by_src.items():
            delta = note["time_step"] - src
            if src % 1.0 == 0.5:
                assert PPQ_SWING_MIN * 1.15 - 1e-6 <= delta <= PPQ_SWING_MAX * 1.15 + 1e-6, (seed, src, delta)
            else:
                assert delta == 0.0, (seed, src, delta)


def test_hat_swing_never_touches_or_reorders_a_roll():
    """Pre-snare roll at 1/64 and 1/32 after a steady bar: the roll keeps its
    grid and its order — the old index rule swapped roll notes."""
    for step in (0.0625, 0.125):
        roll = [_hat(round(1.625 + i * step, 5), hat_roll=True)
                for i in range(int(0.375 / step))]
        hats = [_hat(0.0), _hat(0.5), _hat(1.0), _hat(1.5)] + roll + [_hat(2.5), _hat(3.0), _hat(3.5)]
        for seed in range(20):
            out = apply_hi_hat_swing(hats, random.Random(seed), intensity=1.15)
            rolls = [n["time_step"] for n in out if n.get("hat_roll")]
            assert rolls == [n["time_step"] for n in roll]
            times = [n["time_step"] for n in out]
            assert times == sorted(times)


def test_hat_swing_does_not_flam_the_and_into_the_next_hat():
    """The "&" right before a roll (or any hat closer than HAT_ROLL_GAP after
    its late position) stays on the grid instead of flamming into it."""
    from midi_lowend_director import HAT_ROLL_GAP

    hats = [_hat(1.0), _hat(1.5), _hat(1.625, hat_roll=True), _hat(1.6875, hat_roll=True),
            _hat(2.5), _hat(3.0)]
    for seed in range(20):
        out = apply_hi_hat_swing(hats, random.Random(seed), intensity=1.15)
        assert out[1]["time_step"] == 1.5
        assert out[4]["time_step"] > 2.5  # a free "&" still swings
        assert out[5]["time_step"] - out[4]["time_step"] > HAT_ROLL_GAP


def _roll_group(n: int) -> list[dict]:
    return [
        {"time_step": round(i * 0.0625, 5), "note": "C5", "length": 0.05, "velocity": 100, "hat_roll": True}
        for i in range(n)
    ]


def test_hat_roll_pitch_stays_within_2_to_3_semitones_both_directions():
    up_seen, down_seen = False, False
    for seed in range(30):
        rolled = apply_hat_roll_pitch(_roll_group(6), random.Random(seed))
        midis = [parse_note_name(n["note"]) for n in rolled]
        offsets = [m - 60 for m in midis]  # C5 == 60
        assert all(-3 <= o <= 3 for o in offsets)  # never further than the cap
        if offsets[-1] > 0:
            up_seen = True
        if offsets[-1] < 0:
            down_seen = True
    assert up_seen and down_seen  # bidirectional across seeds, not always-up


def test_hat_roll_pitch_leaves_steady_line_at_c5():
    steady = [{"time_step": float(i), "note": "C5", "length": 0.08, "velocity": 90} for i in range(4)]
    out = apply_hat_roll_pitch(steady, random.Random(3))
    assert all(n["note"] == "C5" for n in out)


def test_hat_roll_pitch_short_group_untouched():
    # Fewer than 3 rolled notes — too short to bother pitching.
    out = apply_hat_roll_pitch(_roll_group(2), random.Random(1))
    assert all(n["note"] == "C5" for n in out)


def test_hat_forward_lag_shifts_whole_track_by_one_constant():
    notes = [{"time_step": float(i) * 0.5, "note": "C5", "length": 0.08, "velocity": 90} for i in range(8)]
    out = apply_hat_forward_lag(notes, random.Random(5))
    shifts = {round(o["time_step"] - n["time_step"], 6) for n, o in zip(notes, out)}
    assert len(shifts) == 1  # one constant offset for the entire lane
    shift = next(iter(shifts))
    assert 0.025 <= shift <= 0.04  # 5-8% of the 0.5-beat 1/8 grid unit


def test_hat_panning_jitters_every_steady_note_not_a_dice_roll():
    notes = [{"time_step": float(i), "note": "C5", "length": 0.08, "velocity": 90} for i in range(40)]
    out = apply_hat_panning(notes, random.Random(11))
    pans = [n["pan"] for n in out]
    assert all(54 <= p <= 74 for p in pans)  # 64 +/- 10 (~8% width)
    assert len(set(pans)) > 1  # not a flat re-center on most notes
    assert pans.count(64) < len(pans)  # was: ~65% landed on exactly 64


def test_hat_panning_open_hat_still_fixed():
    notes = [{"time_step": 0.0, "note": "open_hat", "length": 0.4, "velocity": 90}]
    out = apply_hat_panning(notes, random.Random(2))
    assert out[0]["pan"] == 54


def test_hat_roll_pan_staircase_reaches_40_percent_at_the_ends():
    rolled = apply_hat_roll_pitch(_roll_group(5), random.Random(4))
    pans = [n["pan"] for n in rolled]
    # 40%/20%/0/20%/40% staircase — a linear L-C-R sweep IS that magnitude
    # curve sampled at 5 even points; was +/-20 (~16%), now +/-50 (~40%).
    assert abs(abs(pans[0] - 64) - 50) <= 1
    assert pans[2] == 64
    assert abs(abs(pans[-1] - 64) - 50) <= 1
    assert (pans[0] - 64) == -(pans[-1] - 64)  # symmetric, opposite ends


def test_hat_snare_micro_drop_removes_the_coincident_hat():
    hats = [{"time_step": t, "note": "C5", "length": 0.08, "velocity": 90} for t in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5)]
    snare = [{"time_step": 2.0, "note": "D1", "length": 0.28, "velocity": 118}]
    out = apply_hat_snare_micro_drop(hats, snare)
    times = [n["time_step"] for n in out]
    assert 2.0 not in times
    assert len(out) == len(hats) - 1
    # everything else survives untouched
    assert set(times) == {0.0, 0.5, 1.0, 1.5, 2.5}


def test_hat_snare_micro_drop_ignores_ghost_snare_taps():
    hats = [{"time_step": t, "note": "C5", "length": 0.08, "velocity": 90} for t in (0.0, 0.5, 1.0)]
    snare = [{"time_step": 0.5, "note": "D1", "length": 0.28, "velocity": 56, "ghost": True}]
    out = apply_hat_snare_micro_drop(hats, snare)
    assert len(out) == len(hats)  # ghost taps never trigger a drop


def test_hat_snare_micro_drop_noop_without_snare():
    hats = [{"time_step": 0.0, "note": "C5", "length": 0.08, "velocity": 90}]
    assert apply_hat_snare_micro_drop(hats, []) == hats


def test_humanize_adds_producer_meta():
    pattern = {
        "bpm": 145,
        "style": "opium rage ken carson",
        "user_prompt": "dark trap",
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.4, "velocity": 110}],
            "snare": [{"time_step": 1.0, "note": "D1", "length": 0.3, "velocity": 110}],
            "clap": [],
            "sub_808": [{"time_step": 0.0, "note": "C2", "length": 2.0, "velocity": 127}],
            "hi_hats": [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 100}],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 1.0, "velocity": 90}],
        },
    }
    out = humanize_pattern(pattern)
    assert "plg_producer_meta" in out
    assert out["tracks"]["snare_layer"]


def _base_pattern_for_gate_test() -> dict:
    return {
        "bpm": 145,
        "style": "opium rage ken carson",
        "user_prompt": "dark trap",
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.4, "velocity": 110}],
            "snare": [{"time_step": 1.0, "note": "D1", "length": 0.3, "velocity": 110}],
            "clap": [],
            "sub_808": [{"time_step": 0.0, "note": "C2", "length": 2.0, "velocity": 127}],
            "hi_hats": [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 100}],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 1.0, "velocity": 90}],
        },
    }


def test_chords_and_texture_skipped_when_midi_sourced(monkeypatch):
    """Product decision (2026-07): MIDI is mandatory for generation, and once
    melody_lead is MIDI-sourced (plg_midi_melody) the engine must not layer
    its own chord bed / texture on top of it — see beat_humanize.py's
    'if not data.get("plg_midi_melody")' gate around that whole block."""
    calls: list[str] = []
    monkeypatch.setattr(
        chord_bed, "compose_chord_bed", lambda *a, **kw: calls.append("chords") or 0
    )
    monkeypatch.setattr(
        fx_layer, "materialize_texture_notes", lambda *a, **kw: calls.append("texture")
    )

    pattern = _base_pattern_for_gate_test()
    pattern["plg_midi_melody"] = True
    humanize_pattern(pattern)

    assert calls == []


def test_chords_and_texture_run_when_not_midi_sourced(monkeypatch):
    """Control case for the test above — without plg_midi_melody set, the
    harmonic-bed/texture block still runs exactly as before."""
    calls: list[str] = []
    monkeypatch.setattr(
        chord_bed, "compose_chord_bed", lambda *a, **kw: calls.append("chords") or 0
    )
    monkeypatch.setattr(
        fx_layer, "materialize_texture_notes", lambda *a, **kw: calls.append("texture")
    )

    pattern = _base_pattern_for_gate_test()
    humanize_pattern(pattern)

    assert "chords" in calls
    assert "texture" in calls


def test_duplicate_clap_layer_delays_5_to_15ms_and_pans_wide():
    claps = [{"time_step": float(i) * 2.0, "note": "C5", "length": 0.2, "velocity": 104} for i in range(4)]
    out = duplicate_clap_layer(claps, bpm=140.0, rng=random.Random(3))
    assert len(out) == len(claps)
    delay_beats_ms = [(o["time_step"] - c["time_step"]) * (60_000.0 / 140.0) for c, o in zip(claps, out)]
    assert len(set(round(d, 6) for d in delay_beats_ms)) == 1  # one constant delay for the whole layer
    assert all(5.0 <= d <= 15.0 for d in delay_beats_ms)
    pans = [n["pan"] for n in out]
    assert len(set(pans)) == 2  # alternating wide L/R
    assert all(p != 64 for p in pans)  # never dead-center
    assert all(int(o["velocity"]) < int(c["velocity"]) for c, o in zip(claps, out))
    assert all(n.get("clap_double") for n in out)


def test_duplicate_clap_layer_delay_range_reaches_past_10ms():
    # 2026-07: widened from 5-10ms to 5-15ms per updated Snare Layer spec —
    # confirm the range is actually exercised, not just clamped at the old top.
    seen_above_10 = False
    for seed in range(30):
        claps = [{"time_step": 0.0, "note": "C5", "length": 0.2, "velocity": 104}]
        out = duplicate_clap_layer(claps, bpm=140.0, rng=random.Random(seed))
        delay_ms = out[0]["time_step"] * (60_000.0 / 140.0)
        if delay_ms > 10.0:
            seen_above_10 = True
            break
    assert seen_above_10


def test_duplicate_clap_layer_empty_without_claps():
    assert duplicate_clap_layer([], bpm=140.0, rng=random.Random(1)) == []


def test_pre_clap_roll_builds_two_stage_ladder_before_a_late_clap():
    claps = [{"time_step": 40.0, "note": "C5", "length": 0.2, "velocity": 100}]
    out = add_pre_clap_roll(claps, random.Random(0))
    micros = sorted((n for n in out if n.get("pre_clap_roll")), key=lambda n: n["time_step"])
    assert len(micros) == 2
    assert [round(n["time_step"], 5) for n in micros] == [39.5, 39.75]
    assert micros[0]["velocity"] == 40  # 40% of the main hit's 100
    assert micros[1]["velocity"] == 70  # 70%
    assert any(n["time_step"] == 40.0 and n["velocity"] == 100 for n in out)  # main hit untouched


def test_pre_clap_roll_skips_early_claps_before_the_first_window():
    # Every seed's first eligible bar is >= 4 — an early clap in bar 0 never rolls.
    claps = [{"time_step": 2.0, "note": "C5", "length": 0.2, "velocity": 100}]
    for seed in range(10):
        out = add_pre_clap_roll(claps, random.Random(seed))
        assert not any(n.get("pre_clap_roll") for n in out)


def test_pre_clap_roll_skips_fills_and_ghosts():
    claps = [{"time_step": 40.0, "note": "C5", "length": 0.2, "velocity": 100, "ghost": True}]
    out = add_pre_clap_roll(claps, random.Random(0))
    assert not any(n.get("pre_clap_roll") for n in out)


def _midi_hat_density_pattern(*, racing: bool, midi_owned: bool = False) -> dict:
    return {
        "plg_midi_melody": True,
        "plg_midi_hats": midi_owned,
        "plg_melody_energy": {
            "pace": "racing" if racing else "pocket",
            "attack": "sharp" if racing else "lush",
            "busyness": 0.85 if racing else 0.25,
        },
        "plg_arrangement": [
            {
                "name": "verse",
                "start_bar": 0,
                "bars": 4,
                "active_tracks": ["melody_lead", "hi_hats"],
            }
        ],
        "tracks": {
            "hi_hats": [
                {
                    "time_step": step * 0.25,
                    "note": "F#3",
                    "length": 0.1,
                    "velocity": 80,
                }
                for step in range(64)
            ]
        },
    }


def test_racing_midi_verse_keeps_eighth_hat_skeleton():
    pattern = _midi_hat_density_pattern(racing=True)
    enforce_midi_section_density(pattern)
    hats = pattern["tracks"]["hi_hats"]
    assert [
        sum(bar * 4.0 <= n["time_step"] < (bar + 1) * 4.0 for n in hats)
        for bar in range(4)
    ] == [8, 8, 8, 8]


def test_lush_midi_verse_keeps_sparse_contrast():
    pattern = _midi_hat_density_pattern(racing=False)
    enforce_midi_section_density(pattern)
    hats = pattern["tracks"]["hi_hats"]
    assert [
        sum(bar * 4.0 <= n["time_step"] < (bar + 1) * 4.0 for n in hats)
        for bar in range(4)
    ] == [4, 4, 4, 4]


def test_midi_owned_hats_are_not_rewritten_by_density_cap():
    pattern = _midi_hat_density_pattern(racing=True, midi_owned=True)
    before = [dict(note) for note in pattern["tracks"]["hi_hats"]]
    enforce_midi_section_density(pattern)
    assert pattern["tracks"]["hi_hats"] == before


# --- Style-DNA key source of truth (2026-08-15) --------------------------------
# The progression root must come from the real sounding notes; the filename
# hint is a last-resort fallback only (the 2026-08-14 audit: filename keys
# were wrong in 4 of 10 marketplace files and anchored the 808 a semitone
# off the sounding melody).


def test_resolve_style_dna_key_prefers_sounding_notes_over_filename():
    from beat_humanize import _resolve_style_dna_key

    melody = [
        {"time_step": 0.0, "note": "C#4", "length": 1.0, "velocity": 100},
        {"time_step": 1.0, "note": "E4", "length": 1.0, "velocity": 100},
        {"time_step": 2.0, "note": "G#4", "length": 1.0, "velocity": 100},
        {"time_step": 3.0, "note": "B4", "length": 1.0, "velocity": 100},
    ]
    data = {
        "plg_midi_passport": {
            "key": {"filename_hint": {"root_pc": 0, "scale": "natural_minor"}}
        }
    }
    root, scale, source = _resolve_style_dna_key(data, melody)
    assert source == "note_analysis"
    assert root == 1  # the notes say C#; the filename's C loses
    assert scale  # a scale is detected from the same notes


def test_resolve_style_dna_key_prefers_notes_over_embedded_meta():
    from beat_humanize import _resolve_style_dna_key

    melody = [
        {"time_step": 0.0, "note": "A3", "length": 2.0, "velocity": 100},
        {"time_step": 2.0, "note": "C4", "length": 0.5, "velocity": 100},
        {"time_step": 2.5, "note": "E4", "length": 0.5, "velocity": 100},
        {"time_step": 3.0, "note": "G4", "length": 0.5, "velocity": 100},
    ]
    data = {"plg_midi_key": {"root_pc": 5, "scale": "natural_minor", "source": "key_signature"}}
    root, _scale, source = _resolve_style_dna_key(data, melody)
    assert source == "note_analysis"
    assert root == 9  # A, from the notes — not the meta's F


def test_resolve_style_dna_key_thin_melody_falls_back_to_meta():
    from beat_humanize import _resolve_style_dna_key

    melody = [{"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 100}]
    data = {"plg_midi_key": {"root_pc": 0, "scale": "natural_minor", "source": "key_signature"}}
    root, scale, source = _resolve_style_dna_key(data, melody)
    assert source == "midi_key_signature"
    assert root == 0  # root_pc 0 must survive (not falsy-collapse)
    assert scale == "natural_minor"


def test_resolve_style_dna_key_last_resort_is_the_filename_hint():
    from beat_humanize import _resolve_style_dna_key

    data = {
        "plg_midi_passport": {
            "key": {"filename_hint": {"root_pc": 3, "scale": "natural_minor"}}
        }
    }
    root, scale, source = _resolve_style_dna_key(data, [])
    assert source == "filename_hint"
    assert root == 3
    assert scale == "natural_minor"


def test_resolve_style_dna_key_no_sources_returns_none():
    from beat_humanize import _resolve_style_dna_key

    root, scale, source = _resolve_style_dna_key({}, [])
    assert (root, scale, source) == (None, None, "none")


def test_humanize_pins_progression_to_the_sounding_notes_not_the_filename():
    """Integration: a conflicting filename hint must not anchor the pinned
    progression — regression for the 2026-08-14 Adieu Free Me semitone bug."""
    from beat_humanize import humanize_pattern

    melody = []
    for bar in range(4):
        melody.extend(
            [
                {"time_step": bar * 4.0, "note": "C#4", "length": 0.75, "velocity": 100},
                {"time_step": bar * 4.0 + 1.5, "note": "E4", "length": 0.5, "velocity": 96},
                {"time_step": bar * 4.0 + 2.5, "note": "G#4", "length": 0.5, "velocity": 92},
                {"time_step": bar * 4.0 + 3.25, "note": "B4", "length": 0.5, "velocity": 88},
            ]
        )
    pattern = {
        "bpm": 135,
        "style": "dark trap",
        "user_prompt": "dark trap 135",
        "plg_variety": {"seed": 1234, "key": "C", "scale": "minor", "drum_variant": 1, "melody_variant": 2},
        "plg_midi_passport": {
            "key": {"filename_hint": {"root_pc": 0, "scale": "natural_minor"}}
        },
        "tracks": {
            "kick": [{"time_step": b * 4.0, "note": "C5", "length": 0.25, "velocity": 116} for b in range(4)],
            "snare": [{"time_step": b * 4.0 + 2.0, "note": "D5", "length": 0.2, "velocity": 106} for b in range(4)],
            "clap": [],
            "hi_hats": [{"time_step": i * 0.5, "note": "F#5", "length": 0.1, "velocity": 78} for i in range(32)],
            "sub_808": [],
            "melody_lead": melody,
        },
    }
    out = humanize_pattern(pattern)
    assert out.get("plg_progression_root_source") == "note_analysis"
    prog = out.get("plg_progression")
    assert isinstance(prog, dict) and prog.get("roots_pc")
    assert int(prog["roots_pc"][0]) % 12 == 1  # C# from the notes, not C from the filename

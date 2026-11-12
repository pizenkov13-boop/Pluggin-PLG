"""Full-kit melody–beat symbiosis — drums answer the melody."""

from __future__ import annotations

from copy import deepcopy

import pytest

from melody_beat_symbiosis import (
    apply_melody_beat_symbiosis,
    build_melody_energy_card,
    detect_phrase_root_overrides,
    ensure_melody_energy,
    find_melody_micro_pauses,
    kit_bias_for_energy,
)
from sample_match import score_candidate


def _n(t: float, pitch: str, length: float = 0.25, vel: int = 100) -> dict:
    return {"time_step": t, "note": pitch, "length": length, "velocity": vel}


def _short_pluck_melody() -> list[dict]:
    return [
        _n(i * 0.5, pitch, 0.2)
        for i, pitch in enumerate(["B4", "E5", "G5", "B4", "F#5", "E5"] * 8)
    ]


def _lush_ep_bed() -> list[dict]:
    notes = []
    for i in range(12):
        t = i * 4.0
        for p in ("E3", "A3", "C4", "E4"):
            notes.append(_n(t, p, 3.8, 90))
    return notes


def _drum_skeleton(bars: int = 16) -> dict[str, list]:
    kick, snare, clap, hats, sub = [], [], [], [], []
    for bar in range(bars):
        base = bar * 4.0
        kick.append(_n(base, "C3", 0.5, 110))
        kick.append({**_n(base + 2.0, "C3", 0.5, 100), "kick_sync": True})
        snare.append(_n(base + 2.0, "D3", 0.5, 108))
        clap.append(_n(base + 2.0, "D3", 0.5, 96))
        for step in range(8):
            hats.append(_n(base + step * 0.5, "F#3", 0.25, 70 + (step % 3) * 8))
        sub.append(_n(base, "E1", 2.0, 120))
        sub.append(_n(base + 2.0, "E1", 1.8, 120))
    return {
        "kick": kick,
        "snare": snare,
        "clap": clap,
        "hi_hats": hats,
        "sub_808": sub,
        "snare_layer": [dict(n) for n in snare[::2]],
        "cowbell": [dict(n) for n in snare[::3]],
        "fx": [dict(n) for n in snare[::4]],
    }


def _pattern(melody: list[dict], *, role: str, attack: str, vibe: str = "sweet_dark") -> dict:
    drums = _drum_skeleton()
    arrangement = [
        {"name": "verse", "start_bar": 0, "bars": 4},
        {"name": "chorus", "start_bar": 4, "bars": 4},
        {"name": "verse", "start_bar": 8, "bars": 4},
        {"name": "chorus", "start_bar": 12, "bars": 4},
    ]
    return {
        "bpm": 150,
        "style": "dark trap",
        "tracks": {"melody_lead": melody, **drums},
        "plg_arrangement": arrangement,
        "plg_melody_role": role,
        "plg_melody_vibe": vibe,
        "plg_melody_character": {
            "tempo_band": "fast",
            "attack": attack,
            "vel_mode": "neutral",
            "pocket_muted": False,
            "dissonant": False,
            "min_onset_gap": 0.25 if attack == "sharp" else 1.5,
        },
    }


def test_energy_card_marks_short_melody_busier_than_lush_bed():
    sharp = _pattern(_short_pluck_melody(), role="pluck", attack="sharp")
    lush = _pattern(_lush_ep_bed(), role="thin_ep", attack="lush")
    c_sharp = build_melody_energy_card(sharp)
    c_lush = build_melody_energy_card(lush)
    assert c_sharp["busyness"] > c_lush["busyness"]
    assert c_lush["lushness"] > c_sharp["lushness"]
    assert sharp["plg_melody_energy"]["role"] == "pluck"
    assert lush["plg_melody_energy"]["role"] == "thin_ep"


def test_symbiosis_changes_full_kit_not_just_hats():
    sharp = _pattern(_short_pluck_melody(), role="pluck", attack="sharp", vibe="rage")
    lush = _pattern(_lush_ep_bed(), role="thin_ep", attack="lush", vibe="sweet_dark")
    build_melody_energy_card(sharp)
    build_melody_energy_card(lush)
    apply_melody_beat_symbiosis(sharp)
    apply_melody_beat_symbiosis(lush)

    def _mean_vel(pat: dict, lane: str) -> float:
        notes = pat["tracks"][lane]
        return sum(int(n["velocity"]) for n in notes) / max(1, len(notes))

    def _mean_808_len(pat: dict) -> float:
        notes = pat["tracks"]["sub_808"]
        return sum(float(n["length"]) for n in notes) / max(1, len(notes))

    # Sharp/rage kit hits harder across kick+snare+clap, not only hats.
    assert _mean_vel(sharp, "kick") > _mean_vel(lush, "kick")
    assert _mean_vel(sharp, "snare") > _mean_vel(lush, "snare")
    assert _mean_vel(sharp, "clap") > _mean_vel(lush, "clap")
    assert _mean_vel(sharp, "hi_hats") > _mean_vel(lush, "hi_hats")
    # Lush bed wants longer 808 sustain.
    assert _mean_808_len(lush) > _mean_808_len(sharp)
    assert sharp.get("plg_melody_beat_symbiosis", {}).get("applied") is True


def test_kit_bias_keywords_differ_for_pluck_vs_ep():
    sharp_card = {
        "busyness": 0.8,
        "lushness": 0.2,
        "attack": "sharp",
        "role": "pluck",
        "vibe": "rage",
    }
    lush_card = {
        "busyness": 0.2,
        "lushness": 0.8,
        "attack": "lush",
        "role": "thin_ep",
        "vibe": "sweet_dark",
    }
    sharp_boost, sharp_bury = kit_bias_for_energy(sharp_card)
    lush_boost, lush_bury = kit_bias_for_energy(lush_card)
    assert "snap" in sharp_boost or "tight" in sharp_boost or "hard" in sharp_boost
    assert "dark" in lush_boost or "deep" in lush_boost or "warm" in lush_boost
    assert sharp_boost != lush_boost

    snap = score_candidate(
        "kits/Hard_Snap_Kick.wav",
        "kick",
        prompt_tokens=set(),
        style_tokens=set(),
        prompt_raw="",
        bonus_keywords=sharp_boost,
        bury_keywords=sharp_bury,
    )
    soft = score_candidate(
        "kits/Soft_Warm_Kick.wav",
        "kick",
        prompt_tokens=set(),
        style_tokens=set(),
        prompt_raw="",
        bonus_keywords=sharp_boost,
        bury_keywords=sharp_bury,
    )
    assert snap > soft


def test_midi_owned_hats_not_rewritten():
    pattern = _pattern(_short_pluck_melody(), role="pluck", attack="sharp")
    pattern["plg_midi_hats"] = True
    before = deepcopy(pattern["tracks"]["hi_hats"])
    build_melody_energy_card(pattern)
    apply_melody_beat_symbiosis(pattern)
    assert pattern["tracks"]["hi_hats"] == before


def test_drums_lock_to_melody_accents():
    from melody_beat_symbiosis import extract_melody_accents

    melody = [
        _n(0.0, "E4", 0.5, 110),
        _n(0.5, "G4", 0.25, 80),
        _n(2.0, "B4", 0.5, 112),
        _n(4.0, "E4", 0.5, 110),
        _n(6.0, "A4", 0.5, 108),
    ]
    accents = extract_melody_accents(melody)
    assert 0.0 in accents
    assert any(abs(a - 2.0) < 0.01 for a in accents)

    pattern = _pattern(melody, role="pluck", attack="sharp")
    # Offset a kick so lock must nudge it onto the accent.
    pattern["tracks"]["kick"] = [
        _n(0.08, "C3", 0.4, 100),
        _n(2.1, "C3", 0.4, 100),
        _n(4.0, "C3", 0.4, 100),
    ]
    build_melody_energy_card(pattern)
    apply_melody_beat_symbiosis(pattern)
    kick_times = [round(float(n["time_step"]), 2) for n in pattern["tracks"]["kick"]]
    assert any(abs(t - 0.0) < 0.02 for t in kick_times)
    assert pattern["plg_melody_beat_symbiosis"]["kick_nudged"] >= 1


def test_dense_flat_velocity_melody_has_at_most_two_accents_per_bar():
    from melody_beat_symbiosis import extract_melody_accents

    melody = [
        _n(bar * 4.0 + step * 0.25, "E5", 0.2, 112)
        for bar in range(4)
        for step in range(16)
    ]
    accents = extract_melody_accents(melody)
    assert len(accents) <= 8
    for bar in range(4):
        assert sum(bar * 4.0 <= t < (bar + 1) * 4.0 for t in accents) <= 2


def test_move_kick_timing_false_boosts_velocity_without_moving_kick():
    # bass808_enabled family: kick is phase-locked to 808_bass at birth —
    # symbiosis must never move its time_step (only snare/clap/hats-style
    # velocity treatment), or the two silently drift apart downstream.
    from melody_beat_symbiosis import extract_melody_accents

    melody = [
        _n(0.0, "E4", 0.5, 110),
        _n(2.0, "B4", 0.5, 112),
        _n(4.0, "E4", 0.5, 110),
    ]
    accents = extract_melody_accents(melody)
    assert 0.0 in accents

    pattern = _pattern(melody, role="pluck", attack="sharp")
    original_kick = [
        _n(0.08, "C3", 0.4, 100),
        _n(2.1, "C3", 0.4, 100),
        _n(4.0, "C3", 0.4, 100),
    ]
    pattern["tracks"]["kick"] = [dict(n) for n in original_kick]
    build_melody_energy_card(pattern)
    apply_melody_beat_symbiosis(pattern, move_kick_timing=False)

    kick_out = pattern["tracks"]["kick"]
    kick_times = sorted(round(float(n["time_step"]), 4) for n in kick_out)
    original_times = sorted(round(float(n["time_step"]), 4) for n in original_kick)
    assert kick_times == original_times  # never moved
    assert pattern["plg_melody_beat_symbiosis"]["kick_nudged"] == 0
    # The note nearest the 0.0 accent still gets the velocity boost.
    near = next(n for n in kick_out if abs(float(n["time_step"]) - 0.08) < 0.01)
    assert int(near["velocity"]) > 100
    assert near.get("melody_accent_lock") is True


def test_808_retunes_to_melody_root():
    melody = [
        _n(0.0, "E3", 3.5, 100),
        _n(4.0, "A3", 3.5, 100),
        _n(8.0, "C4", 3.5, 100),
    ]
    pattern = _pattern(melody, role="thin_ep", attack="lush")
    pattern["tracks"]["sub_808"] = [
        {"time_step": 0.0, "note": "C1", "length": 2.0, "velocity": 120},
        {"time_step": 4.0, "note": "C1", "length": 2.0, "velocity": 120},
        {"time_step": 8.0, "note": "C1", "length": 2.0, "velocity": 120},
    ]
    build_melody_energy_card(pattern)
    apply_melody_beat_symbiosis(pattern)
    notes = [n["note"] for n in pattern["tracks"]["sub_808"]]
    # Should not stay stuck on C1 for an E/A/C melody.
    assert notes != ["C1", "C1", "C1"]
    assert pattern["plg_melody_beat_symbiosis"]["808_retuned"] >= 1


def test_section_style_modulated_by_melody_card():
    from arranger import _section_style, _section_style_for_pattern

    card = {
        "busyness": 0.8,
        "lushness": 0.2,
        "attack": "sharp",
        "role": "pluck",
        "vibe": "rage",
    }
    pattern = {"plg_melody_energy": card}
    base_chorus = _section_style("chorus")
    base_verse = _section_style("verse")
    hot = _section_style_for_pattern("chorus", pattern)
    cold = _section_style_for_pattern("verse", pattern)
    assert hot.energy >= base_chorus.energy
    assert hot.hat_density > base_chorus.hat_density
    assert cold.energy <= base_verse.energy
    assert cold.hat_density < base_verse.hat_density


def test_fast_melody_gets_racing_pace_not_lush_pocket():
    from melody_beat_symbiosis import _melody_pace

    assert (
        _melody_pace(
            bpm=150,
            onset_rate=4.5,
            short_frac=0.35,
            median_len=0.8,
            min_onset_gap=0.25,
        )
        == "racing"
    )
    pattern = _pattern(_short_pluck_melody(), role="thin_ep", attack="lush")
    card = build_melody_energy_card(pattern)
    assert card["pace"] == "racing"
    assert card["attack"] == "sharp"  # lush demoted under racing MIDI
    assert card["busyness"] >= 0.7
    assert card["lushness"] < 0.5


def test_racing_pace_forces_double_time_drum_grid():
    from arranger import arrange_song
    from drum_blueprint import apply_drum_blueprint

    pattern = _pattern(_short_pluck_melody(), role="pluck", attack="sharp")
    pattern["plg_variety"] = {"genre": "trap", "seed": 42, "drum_variant": 1}
    build_melody_energy_card(pattern)
    arrange_song(pattern)
    # Rebuild energy after arrange so pace is present for blueprint.
    build_melody_energy_card(pattern)
    apply_drum_blueprint(pattern, genre="trap")
    assert pattern.get("plg_drum_pace") == "racing"
    assert pattern.get("plg_drum_grid") in ("four_floor", "backbeat")
    # Half-time trap parks snare only on step 9 (~beat 2). Racing must land
    # snares on the double-time backbeat (beats 1.0 and 3.0 → steps 5/13).
    snare_beats = sorted(
        {
            round(float(n["time_step"]) % 4.0, 2)
            for n in pattern["tracks"]["snare"]
            if not n.get("ghost") and not n.get("fill")
        }
    )
    assert any(abs(b - 1.0) < 0.05 for b in snare_beats)
    assert any(abs(b - 3.0) < 0.05 for b in snare_beats)


def test_role_kit_bias_pluck_vs_ep_is_strong():
    pluck_boost, pluck_bury = kit_bias_for_energy(
        {"busyness": 0.7, "lushness": 0.2, "attack": "sharp", "role": "pluck", "vibe": "rage"}
    )
    ep_boost, ep_bury = kit_bias_for_energy(
        {
            "busyness": 0.25,
            "lushness": 0.75,
            "attack": "lush",
            "role": "thin_ep",
            "vibe": "sweet_dark",
        }
    )
    assert "snap" in pluck_boost or "crisp" in pluck_boost
    assert "boom" in ep_boost or "round" in ep_boost
    # Opposite characters should bury each other's sweet spots.
    assert "boom" in pluck_bury or "warm" in pluck_bury
    assert "crisp" in ep_bury or "digital" in ep_bury

    crisp_hat = score_candidate(
        "hats/Crisp_Snap_Hat.wav",
        "hi_hats",
        prompt_tokens=set(),
        style_tokens=set(),
        prompt_raw="",
        bonus_keywords=pluck_boost,
        bury_keywords=pluck_bury,
    )
    soft_hat = score_candidate(
        "hats/Soft_Warm_Long_Hat.wav",
        "hi_hats",
        prompt_tokens=set(),
        style_tokens=set(),
        prompt_raw="",
        bonus_keywords=pluck_boost,
        bury_keywords=pluck_bury,
    )
    assert crisp_hat > soft_hat + 20


# --- Phrase-level 808_bass harmony follow (detect_phrase_root_overrides) ---


def test_phrase_root_override_needs_several_notes():
    # Only 2 notes in the phrase — below the min-note confidence floor.
    melody = [_n(0.0, "A4", 2.0, 110), _n(2.0, "A4", 2.0, 110)]
    overrides = detect_phrase_root_overrides(
        melody, start=0.0, bars=4, global_root_pc=0, seed=1
    )
    assert overrides == {}


def test_phrase_root_override_skips_when_it_matches_global_root():
    # Dominant pc already equals the song root — nothing to "follow".
    melody = [_n(0.0, "C4", 2.0, 110), _n(2.0, "C4", 2.0, 110), _n(4.0 - 0.1, "C4", 0.05, 110)]
    overrides = detect_phrase_root_overrides(
        melody, start=0.0, bars=4, global_root_pc=0, seed=1
    )
    assert overrides == {}


def test_phrase_root_override_fires_rarely_on_strong_divergent_signal():
    # Phrase is dominated by A (pc=9) against a C (pc=0) song root — a real,
    # well-supported divergence. Seeded coin flip decides per-seed whether
    # the bass actually follows it that phrase (verified against two known
    # seeds — this isn't tuned to always-fire, it's meant to be rare).
    melody = [_n(0.0, "A4", 2.0, 110), _n(2.0, "A4", 2.0, 110), _n(4.0, "A4", 2.0, 110)]
    fires = detect_phrase_root_overrides(melody, start=0.0, bars=4, global_root_pc=0, seed=1)
    skips = detect_phrase_root_overrides(melody, start=0.0, bars=4, global_root_pc=0, seed=0)
    assert fires == {0: 9}
    assert skips == {}


# --- Local melody breaths (find_melody_micro_pauses) ---


def test_find_melody_micro_pauses_ignores_uniform_spacing():
    melody = [_n(i * 0.5, "E4", 0.2) for i in range(20)]
    assert find_melody_micro_pauses(melody) == []


def test_find_melody_micro_pauses_flags_an_outlier_gap():
    # Dense 0.25-spaced run, one real 2-beat breath, dense run resumes.
    melody = [_n(i * 0.25, "E4", 0.15) for i in range(12)]  # 0.0 .. 2.75
    resume_start = 5.0
    melody += [_n(resume_start + i * 0.25, "E4", 0.15) for i in range(12)]
    windows = find_melody_micro_pauses(melody)
    assert len(windows) == 1
    start, end = windows[0]
    # Window sits strictly inside the silent stretch (last note's release to
    # the next attack), not overlapping either neighboring note.
    assert 2.75 + 0.15 <= start < end <= resume_start


def test_racing_melody_gets_local_thinning_at_its_own_pause():
    # A racing melody with a real internal breath must still get cold-window
    # thinning right there — the old rule wiped `cold` to [] for any racing
    # melody regardless of internal pauses.
    melody = [n for n in _short_pluck_melody() if not (10.0 <= n["time_step"] < 11.5)]
    pattern = _pattern(melody, role="pluck", attack="sharp", vibe="rage")
    # Extras sitting exactly inside the melody's own gap — thinnable by
    # _thin_quiet_hats (soft velocity + roll tag).
    pattern["tracks"]["hi_hats"] += [
        {"time_step": 10.0, "note": "F#3", "length": 0.15, "velocity": 55, "hat_roll": True},
        {"time_step": 10.5, "note": "F#3", "length": 0.15, "velocity": 55, "hat_roll": True},
    ]
    build_melody_energy_card(pattern)
    assert pattern["plg_melody_energy"]["pace"] == "racing"
    windows = find_melody_micro_pauses(melody)
    assert any(s <= 10.0 < e for s, e in windows)

    apply_melody_beat_symbiosis(pattern)
    assert pattern["plg_melody_beat_symbiosis"]["hats_thinned"] > 0


def test_hat_densifier_reads_the_density_card():
    """2026-08-15: the densifier used to refill 16ths on quiet material right
    after the density trim removed them (quiet files ended up with MORE hats
    than dense ones). Symmetric with drum_density.thin_generated_hats: only a
    busy card (hat_multiplier >= 0.95) may densify."""
    busy = _pattern(_short_pluck_melody(), role="pluck", attack="sharp")
    build_melody_energy_card(busy)
    busy["plg_drum_density"] = {"version": 1, "hat_multiplier": 1.0}
    quiet = deepcopy(busy)
    quiet["plg_drum_density"] = {"version": 1, "hat_multiplier": 0.78}

    apply_melody_beat_symbiosis(busy, move_kick_timing=False)
    apply_melody_beat_symbiosis(quiet, move_kick_timing=False)

    busy_hats = len(busy["tracks"]["hi_hats"])
    quiet_hats = len(quiet["tracks"]["hi_hats"])
    assert busy_hats > quiet_hats, (busy_hats, quiet_hats)


# --- the accent hat joins the lane instead of landing on top of it ----------
# 2026-08-21. Reported by ear as "horrible noise on Sahara's hi-hats" and
# measured: 30 of the 108 hats this pass adds landed within 15 ms of a hat that
# was already there, the closest pair 0.7 ms apart, and they shipped at more
# than twice the lane's median velocity.


def _hat(t: float, vel: int = 44) -> dict:
    return {"time_step": t, "note": "C5", "length": 0.12, "velocity": vel,
            "hat_state": "closed", "hat_db_rule": True}


def _accent_lock(hats, accents, **kw):
    from melody_beat_symbiosis import lock_drums_to_melody_accents

    tracks = {"hi_hats": [dict(h) for h in hats]}
    lock_drums_to_melody_accents(tracks, accents, midi_hats=False, attack="sharp", **kw)
    return tracks["hi_hats"]


def test_accent_hat_is_refused_where_a_humanised_hat_already_answers():
    """The existing hats are OFF the grid by the time this pass runs.

    apply_hi_hat_swing / apply_hat_forward_lag move every hat a few
    milliseconds before apply_melody_beat_symbiosis is reached, so an exact
    key match on the accent's own time can never find the hat it means to
    reuse — which is how two copies of one transient ended up 0.7 ms apart.
    """
    # A hat 4 ms off the accent at 150 BPM (0.01 beats), as humanisation leaves it.
    hats = [_hat(0.0), _hat(1.0), _hat(2.01), _hat(3.0)]
    out = _accent_lock(hats, [2.0])
    assert len(out) == len(hats), "an answered accent must not get a second hat"


def test_accent_hat_is_still_added_where_the_lane_is_genuinely_empty():
    hats = [_hat(0.0), _hat(1.0), _hat(3.0)]
    out = _accent_lock(hats, [2.0])
    assert len(out) == len(hats) + 1
    added = [n for n in out if n.get("melody_accent_lock") and not n.get("hat_state")]
    assert len(added) == 1
    assert abs(float(added[0]["time_step"]) - 2.0) < 1e-9


def test_accent_hat_never_outshouts_the_lane_it_joins():
    """It is an accent, not the loudest thing in the lane.

    The literal 88 this used to write was exactly twice the measured median
    velocity of the hats it landed among (44 on Sahara), and every later
    multiplier scaled both alike, so 68 of Sahara's added hats shipped at 112.
    """
    # Quarter-note hats, so an eighth-grid slot is genuinely free. The pass
    # only answers accents near the eighth grid (`accent % 0.5 <= 0.12`).
    hats = [_hat(float(i), vel=40) for i in range(8)]
    out = _accent_lock(hats, [2.5])
    added = [n for n in out if n.get("melody_accent_lock") and not n.get("hat_state")]
    assert added, "the accent is on an empty slot, it should be answered"
    lane = sorted(int(n["velocity"]) for n in out if not n.get("melody_accent_lock"))
    p90 = lane[max(0, int(len(lane) * 0.9) - 1)]
    assert int(added[0]["velocity"]) <= p90
    assert int(added[0]["velocity"]) > lane[len(lane) // 2] - 1


def test_accent_hat_tolerance_cannot_eat_a_real_roll():
    """A 32nd roll is 0.125 beats apart; the gap rule stops well short of it."""
    from melody_beat_symbiosis import _ACCENT_HAT_MIN_GAP

    assert _ACCENT_HAT_MIN_GAP < 0.125


def _flat_pad(bars: int = 8) -> list[dict]:
    """One chord onset on every beat, three beats long, one flat velocity.

    This is the shape that kills the accent scorer's two musical terms: any
    gap past 0.9 beats pins the gap term at its ceiling and any note past one
    beat pins the length term, so every candidate in the bar scores the same
    and the sort order alone decides. Measured on the real corpus as an exact
    tie in 80 % of Tragic's bars and 100 % of Sahara's.
    """
    return [
        _n(bar * 4.0 + beat, pitch, 3.0, 100)
        for bar in range(bars)
        for beat, pitch in enumerate(["C5", "D#5", "G5", "A#5"])
    ]


def test_a_tied_bar_does_not_hand_its_accent_to_the_beat_before_the_backbeat():
    from midi_phrase_drums import _BACKBEAT_BAR_OFFSET

    from melody_beat_symbiosis import extract_melody_accents

    accents = extract_melody_accents(_flat_pad())
    crowding = [
        t for t in accents
        if 0.12 <= t % 4.0 < _BACKBEAT_BAR_OFFSET - 0.12
    ]
    assert not crowding, f"accents left in front of the backbeat: {crowding}"
    # The bar still gets its two accents; the tie-break chooses between them,
    # it never throws one away.
    assert len(accents) == 16


def test_the_tie_break_is_inert_when_the_material_separates_the_candidates():
    """The §0q decision in one assertion.

    A positional BONUS decided bars the material had already decided, and was
    retired for it. This term may only speak on an exact tie — so on material
    whose gaps and velocities differ, switching it off must change nothing.
    """
    import melody_beat_symbiosis as mbs

    melody = [
        _n(0.0, "C5", 0.5, 118),
        _n(0.75, "D#5", 0.25, 96),
        _n(2.0, "G5", 1.5, 104),
        _n(3.5, "A#5", 0.25, 92),
        _n(4.0, "C5", 0.5, 120),
        _n(5.25, "D#5", 0.75, 98),
        _n(6.0, "G5", 0.5, 110),
        _n(7.5, "F5", 0.25, 94),
    ]
    with_term = mbs.extract_melody_accents(melody)
    real = mbs._crowds_the_backbeat
    mbs._crowds_the_backbeat = lambda t: 0
    try:
        without_term = mbs.extract_melody_accents(melody)
    finally:
        mbs._crowds_the_backbeat = real
    assert with_term == without_term


def test_the_backbeat_position_is_read_from_its_owner_not_re_derived():
    import midi_phrase_drums

    import melody_beat_symbiosis as mbs

    # Moving the anchor must move the demoted zone with it, or the two have
    # silently disagreed — the failure mode internal notes (not included) rule 1
    # exists to prevent.
    assert mbs._crowds_the_backbeat(1.0) == 1
    assert mbs._crowds_the_backbeat(midi_phrase_drums._BACKBEAT_BAR_OFFSET) == 0
    assert mbs._crowds_the_backbeat(3.0) == 0
    assert mbs._crowds_the_backbeat(0.0) == 0


def test_firmness_separates_a_stated_rhythm_from_a_flat_pad():
    from melody_beat_symbiosis import melody_accent_firmness

    # A pad with one chord onset on every beat and notes longer than a beat
    # pins both musical terms at their ceiling, so nothing but velocity is
    # left and the bars come out tied.
    assert melody_accent_firmness(_flat_pad())["share"] == 0.0

    # The same bars, but the melody actually plays a rhythm: varied gaps,
    # varied lengths, varied velocity.
    stated = []
    for bar in range(8):
        b = bar * 4.0
        stated += [
            _n(b + 0.0, "C5", 0.5, 120),
            _n(b + 0.75, "D#5", 0.25, 96),
            _n(b + 2.0, "G5", 1.5, 112),
            _n(b + 3.5, "A#5", 0.25, 93),
        ]
    assert melody_accent_firmness(stated)["share"] == 1.0


def test_firmness_does_not_count_a_bar_that_offered_only_one_position():
    from melody_beat_symbiosis import melody_accent_firmness

    # One off-downbeat candidate per bar: the material left no choice to make,
    # which is neither a decision nor a coin-flip and must not be scored as
    # either — counting it as "firm" would let the gate fire on silence.
    melody = []
    for bar in range(4):
        b = bar * 4.0
        melody += [_n(b + 0.0, "C5", 0.5, 100), _n(b + 2.0, "G5", 0.5, 100)]
    card = melody_accent_firmness(melody)
    assert card["contested"] == 0
    assert card["share"] == 0.0


def test_the_accent_map_and_the_firmness_card_score_from_one_owner():
    """No second private copy of the accent formula — the defect class that
    cost this project most (internal notes (not included), hard rule 1)."""
    import inspect

    import melody_beat_symbiosis as mbs

    for fn in (mbs.extract_melody_accents, mbs.melody_accent_firmness):
        assert "_accent_candidates(" in inspect.getsource(fn)
    # and the formula itself lives in exactly one place
    assert inspect.getsource(mbs._accent_candidates).count("_ACCENT_DOWNBEAT_BONUS") == 1

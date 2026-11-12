"""The MIDI-derived drum density card (drum_density.py).

The behaviour these lock down is the fix for a reproduced failure: a calm,
sparse 68 BPM piano ballad got exactly two kick/808 pairs in every active bar
— including bars where the melody is silent and no confident harmonic window
exists — so the 808 landed on an unsupported root and the final kick/808 gate
rejected every generation on every genre.
"""
from __future__ import annotations

import pytest

from drum_density import (
    LATE_HAT_ACCENT_LIFT,
    MAX_PAIRS_PER_BAR,
    MIN_PAIRS_PER_BAR,
    bar_has_melody,
    build_drum_density_card,
    density_card,
    late_hat_velocity,
    melody_onsets_per_bar,
    scaled_shape,
    thin_generated_hats,
)
from genre_profiles import PROFILES


def _hats(steps, **tags):
    return [
        {"time_step": float(s), "note": "C5", "length": 0.1, "velocity": 90, **tags}
        for s in steps
    ]


def _sixteenths(bars):
    return [bar * 4.0 + i * 0.25 for bar in range(bars) for i in range(16)]


def _pattern(melody, *, bars=8, energy=None, arrangement=None, bpm=140):
    return {
        "bpm": bpm,
        "tracks": {"melody_lead": melody},
        "plg_melody_energy": energy if energy is not None else {},
        "plg_arrangement": arrangement
        if arrangement is not None
        else [{"name": "verse", "start_bar": 0, "bars": bars,
               "active_tracks": ["melody_lead", "kick", "808_bass", "hi_hats"]}],
    }


def _melody(steps):
    return [{"time_step": float(s), "note": "C4", "length": 1.0, "velocity": 90} for s in steps]


def test_sparse_midi_gets_a_sparser_low_end_than_busy_midi():
    sparse = _pattern(_melody([0.0, 8.0, 16.0, 24.0]),
                      energy={"onset_rate": 0.95, "busyness": 0.10, "pace": "pocket"})
    busy = _pattern(_melody([i * 0.5 for i in range(64)]),
                    energy={"onset_rate": 10.0, "busyness": 0.80, "pace": "racing"})

    sparse_card = build_drum_density_card(sparse, profile=PROFILES["trap"])
    busy_card = build_drum_density_card(busy, profile=PROFILES["trap"])

    assert sparse_card["pairs_per_bar"] < busy_card["pairs_per_bar"]
    # The reproduced bug was a fixed ~1.6-2.0 pairs/bar on material this
    # sparse. Anything at or above one pair per bar has not fixed it.
    assert sparse_card["pairs_per_bar"] < 1.0
    # Busy material must keep the pocket it already had.
    assert busy_card["pairs_per_bar"] >= 1.7


def test_density_stays_inside_the_musical_bounds():
    for onset_rate, busyness in ((0.0, 0.0), (0.5, 0.05), (12.0, 1.0), (100.0, 1.0)):
        card = build_drum_density_card(
            _pattern(_melody([0.0]), energy={"onset_rate": onset_rate, "busyness": busyness}),
            profile=PROFILES["rage"],
        )
        assert MIN_PAIRS_PER_BAR <= card["pairs_per_bar"] <= MAX_PAIRS_PER_BAR


def test_genre_is_a_bounded_modifier_not_the_source_of_truth():
    """The whole point of the change: swapping the genre may season the
    density, it may not decide it."""
    energy = {"onset_rate": 0.95, "busyness": 0.10, "pace": "pocket"}
    values = {}
    for name in ("trap", "rage", "opium", "drill", "west_coast", "detroit",
                 "phonk", "hyperpop", "underground", "pluggnb"):
        card = build_drum_density_card(_pattern(_melody([0.0, 8.0]), energy=energy),
                                       profile=PROFILES[name])
        values[name] = card["pairs_per_bar"]

    # Every tuned lane must land sparse on sparse material...
    assert max(values.values()) < 1.15, values
    # ...and the whole genre spread must stay smaller than the gap between
    # sparse and busy material, or genre is still driving.
    spread = max(values.values()) - min(values.values())
    busy = build_drum_density_card(
        _pattern(_melody([i * 0.5 for i in range(32)]),
                 energy={"onset_rate": 10.0, "busyness": 0.8}),
        profile=PROFILES["trap"],
    )["pairs_per_bar"]
    assert spread < (busy - min(values.values())) / 2.0, (spread, values, busy)


def test_missing_profile_is_a_no_op_multiplier():
    card = build_drum_density_card(
        _pattern(_melody([0.0, 4.0]), energy={"onset_rate": 2.0, "busyness": 0.4}),
        profile=None,
    )
    assert card["genre_multiplier"] == 1.0
    assert card["base_pairs_per_bar"] == card["pairs_per_bar"]


def test_chord_stack_counts_as_one_onset_not_four():
    chord = [{"time_step": 0.0, "note": n, "length": 2.0, "velocity": 90}
             for n in ("C4", "E4", "G4", "B4")]
    assert melody_onsets_per_bar({"tracks": {"melody_lead": chord}}, 4)[0] == 1


def test_silent_bars_are_visible_to_the_pocket():
    pattern = _pattern(_melody([0.0, 12.0]), bars=4)
    card = build_drum_density_card(pattern, profile=PROFILES["trap"])
    assert bar_has_melody(card, 0) is True
    assert bar_has_melody(card, 1) is False
    assert bar_has_melody(card, 3) is True
    # With no card at all every bar must read as playable — the pocket may
    # never go silent just because analysis is missing.
    assert bar_has_melody(None, 1) is True


def test_sparse_material_stops_anchoring_every_active_bar():
    sparse = build_drum_density_card(
        _pattern(_melody([0.0, 8.0]), energy={"onset_rate": 0.95, "busyness": 0.10}),
        profile=PROFILES["trap"],
    )
    busy = build_drum_density_card(
        _pattern(_melody([i * 0.5 for i in range(64)]),
                 energy={"onset_rate": 4.0, "busyness": 0.8}),
        profile=PROFILES["trap"],
    )
    assert sparse["anchor_every_active_bar"] is False
    assert busy["anchor_every_active_bar"] is True


def test_no_genre_can_flip_the_silent_bar_decision():
    """Whether the engine manufactures harmony in a bar the melody is silent
    in is a structural decision about the material. Measured regression: the
    calm ballad's 0.90 base reading crossed 1.0 purely because a drive=0.9
    profile multiplied it, restoring the exact behaviour being removed."""
    energy = {"onset_rate": 0.953, "busyness": 0.1036, "pace": "pocket"}
    for name, profile in PROFILES.items():
        card = build_drum_density_card(_pattern(_melody([0.0, 8.0]), energy=energy),
                                       profile=profile)
        assert card["anchor_every_active_bar"] is False, name


def test_gate_floor_relaxes_for_sparse_but_ceiling_never_moves():
    sparse = build_drum_density_card(
        _pattern(_melody([0.0, 8.0]), energy={"onset_rate": 0.95, "busyness": 0.10}),
        profile=PROFILES["trap"],
    )
    busy = build_drum_density_card(
        _pattern(_melody([i * 0.5 for i in range(64)]),
                 energy={"onset_rate": 4.0, "busyness": 0.8}),
        profile=PROFILES["trap"],
    )
    assert sparse["min_pairs_per_4bar"] < busy["min_pairs_per_4bar"]
    assert sparse["min_pairs_per_4bar"] >= 2
    assert sparse["max_pairs_per_4bar"] == busy["max_pairs_per_4bar"] == 8


def test_gap_for_busy_material_matches_the_old_fixed_rule():
    """Regression guard for the dense files: the old rule was a fixed 0.75 s,
    which is 1.69 beats at 135 BPM and 2.0 at 160. Busy material must keep
    landing in that window or this change quietly thinned working beats."""
    busy = build_drum_density_card(
        _pattern(_melody([i * 0.5 for i in range(64)]),
                 energy={"onset_rate": 10.0, "busyness": 0.8}),
        profile=PROFILES["trap"],
    )
    assert 1.5 <= busy["max_gap_beats"] <= 2.1
    sparse = build_drum_density_card(
        _pattern(_melody([0.0, 8.0]), energy={"onset_rate": 0.95, "busyness": 0.10}),
        profile=PROFILES["trap"],
    )
    assert sparse["max_gap_beats"] > 3.0


def test_scaled_shape_thins_without_inventing_positions():
    shape = (0.0, 0.75, 2.5, 4.0, 6.5)
    card = {"pocket_onsets_per_phrase": 3}
    out = scaled_shape(shape, card)
    assert len(out) == 3
    assert set(out) <= set(shape)
    assert out[0] == 0.0  # the cell still opens where it opened
    assert out == tuple(sorted(out))


def test_scaled_shape_leaves_busy_material_alone():
    shape = (0.0, 0.75, 2.5, 4.0, 6.5)
    assert scaled_shape(shape, {"pocket_onsets_per_phrase": 5}) == shape
    assert scaled_shape(shape, {"pocket_onsets_per_phrase": 9}) == shape
    assert scaled_shape(shape, None) == shape


def test_busy_material_keeps_every_existing_pocket_shape():
    """Calibration guard for the dense files. The live shapes hold 4-5
    onsets per 8-beat cell; at full drive the card must resolve to at least
    5 or this change silently thins beats that already work."""
    busy = build_drum_density_card(
        _pattern(_melody([i * 0.5 for i in range(64)]),
                 energy={"onset_rate": 10.0, "busyness": 0.8}),
        profile=PROFILES["hyperpop"],
    )
    assert busy["pocket_onsets_per_phrase"] >= 5
    for shape in ((0.0, 1.5, 3.0, 5.5), (0.0, 0.75, 3.25, 4.75, 6.75),
                  (0.0, 1.5, 3.0, 5.5, 7.0), (0.0, 0.75, 2.5, 4.0, 6.5)):
        assert scaled_shape(shape, busy) == shape

    sparse = build_drum_density_card(
        _pattern(_melody([0.0, 8.0]), energy={"onset_rate": 0.95, "busyness": 0.10}),
        profile=PROFILES["trap"],
    )
    assert sparse["pocket_onsets_per_phrase"] == 2


def test_card_discriminates_the_shipping_corpus_range():
    """2026-08-15 audit: the whole ten-file corpus sits at 4.0-9.5 onset
    clusters/bar, and the old 3.0 knee put all of it on the same plateau —
    pocket target, gate floor, anchor flag and hat trim were byte-identical
    for a 4-onset file and a 9.5-onset file. Across the real range the card
    must order strictly: denser material, busier kit."""
    cards = []
    for onset_rate in (4.0, 5.75, 7.0, 9.5):
        cards.append(build_drum_density_card(
            _pattern(_melody([i * 0.5 for i in range(64)]),
                     energy={"onset_rate": onset_rate, "busyness": 0.72, "pace": "racing"}),
            profile=PROFILES["trap"],
        ))
    # The point of this test is that the card DISCRIMINATES across the real
    # corpus range instead of plateauing — that part is unchanged. What the
    # 2026-08-19 listening pass changed is the DIRECTION for the hat lane
    # only: hats compete with the melody for the same transient register, so
    # a busier melody must get FEWER of them, not more. The low end sits in
    # its own band and stays proportional, which is what every kick/808 gate
    # and the 0i.1/0i.2 tempo calibration are built on. See the project issue log (not included) 0n.
    for field in ("pairs_per_bar", "pocket_onsets_per_phrase"):
        values = [card[field] for card in cards]
        assert values == sorted(values), (field, values)
    hat_values = [card["hat_multiplier"] for card in cards]
    assert hat_values == sorted(hat_values, reverse=True), hat_values
    assert cards[0]["pocket_onsets_per_phrase"] < cards[-1]["pocket_onsets_per_phrase"]
    assert cards[0]["hat_multiplier"] > cards[-1]["hat_multiplier"]
    assert cards[0]["pairs_per_bar"] < cards[-1]["pairs_per_bar"]
    # No plateau on either axis — the whole point of the 2026-08-15 knee fix.
    assert len(set(hat_values)) == len(hat_values)
    # The densest shipping material still reaches full pocket shapes.
    assert cards[-1]["pocket_onsets_per_phrase"] == 5


def test_card_is_a_pure_reader():
    melody = _melody([0.0, 4.0])
    pattern = _pattern(melody, energy={"onset_rate": 2.0, "busyness": 0.5})
    before = {k: v for k, v in pattern.items() if k != "plg_drum_density"}
    build_drum_density_card(pattern, profile=PROFILES["trap"])
    after = {k: v for k, v in pattern.items() if k != "plg_drum_density"}
    assert before == after
    assert density_card(pattern) is not None


def test_density_card_reader_rejects_junk():
    assert density_card({}) is None
    assert density_card({"plg_drum_density": "nope"}) is None
    assert density_card({"plg_drum_density": {}}) is None


@pytest.mark.parametrize("bars", [0, 1, 40])
def test_no_melody_at_all_still_yields_a_usable_card(bars):
    card = build_drum_density_card(_pattern([], bars=max(1, bars)), profile=PROFILES["trap"])
    assert card["pairs_per_bar"] >= MIN_PAIRS_PER_BAR
    assert card["min_pairs_per_4bar"] >= 2


# --- hat thinning -----------------------------------------------------------


def test_busy_material_leaves_the_hat_lane_untouched():
    hats = _hats(_sixteenths(8))
    kept, removed = thin_generated_hats(hats, {"hat_multiplier": 1.0})
    assert removed == 0
    assert kept == sorted(hats, key=lambda n: n["time_step"])
    # A card just under 1.0 (the measured busy value was 0.997) is still a no-op.
    assert thin_generated_hats(hats, {"hat_multiplier": 0.997})[1] == 0


def test_sparse_material_thins_towards_the_multiplier():
    hats = _hats(_sixteenths(8))
    kept, removed = thin_generated_hats(hats, {"hat_multiplier": 0.63})
    assert removed > 0
    assert len(kept) == pytest.approx(len(hats) * 0.63, abs=len(hats) * 0.12)


def test_thinning_removes_roll_interiors_before_the_pulse():
    # One bar of on-beat pulse plus a dense 1/32 roll in beat 4.
    pulse = [0.0, 1.0, 2.0, 3.0]
    roll = [3.5 + i * 0.125 for i in range(4)]
    kept, removed = thin_generated_hats(_hats(pulse + roll), {"hat_multiplier": 0.6})
    kept_steps = {n["time_step"] for n in kept}
    assert removed > 0
    for beat in pulse:
        assert beat in kept_steps, "the pulse must survive before any roll note"


def test_thinning_never_removes_another_writers_tagged_note():
    tagged = _hats([1.25], midi_motif_answer=True) + _hats([2.75], midi_boundary_fill=True)
    hats = _hats([i * 0.25 for i in range(16)]) + tagged
    kept, _removed = thin_generated_hats(hats, {"hat_multiplier": 0.5})
    kept_tags = [n for n in kept if n.get("midi_motif_answer") or n.get("midi_boundary_fill")]
    assert len(kept_tags) == 2


def test_thinning_keeps_every_bar_above_the_floor():
    """The full-kit contract fails a non-breathe phrase with no hats at all,
    so thinning must never empty a bar however sparse the card is."""
    hats = _hats(_sixteenths(6))
    kept, _removed = thin_generated_hats(hats, {"hat_multiplier": 0.5}, floor_per_bar=2)
    per_bar = {}
    for note in kept:
        bar = int(note["time_step"] // 4.0)
        per_bar[bar] = per_bar.get(bar, 0) + 1
    assert per_bar and min(per_bar.values()) >= 2


def test_thinning_is_deterministic():
    hats = _hats(_sixteenths(8))
    first = thin_generated_hats(hats, {"hat_multiplier": 0.63})
    second = thin_generated_hats(hats, {"hat_multiplier": 0.63})
    assert [n["time_step"] for n in first[0]] == [n["time_step"] for n in second[0]]


def test_thinning_handles_missing_or_junk_cards():
    hats = _hats(_sixteenths(4))
    assert thin_generated_hats(hats, None)[1] == 0
    assert thin_generated_hats(hats, {})[1] == 0
    assert thin_generated_hats(hats, {"hat_multiplier": "nope"})[1] == 0
    assert thin_generated_hats([], {"hat_multiplier": 0.5}) == ([], 0)


def test_max_gap_is_a_time_budget_not_a_beat_constant():
    """2026-08-15: max_gap_beats used to be tempo-blind, so the same card
    allowed ~0.6 s of silence at 180 BPM but ~5 s at 68. Normalized to the
    session tempo, the implied seconds stay close whatever the tempo, and the
    140 BPM value (the old calibration point) is unchanged."""
    energy = {"onset_rate": 10.0, "busyness": 0.8}
    melody = _melody([i * 0.5 for i in range(64)])
    gaps = {}
    for bpm in (95, 140, 180):
        card = build_drum_density_card(_pattern(melody, energy=energy, bpm=bpm),
                                       profile=PROFILES["trap"])
        gaps[bpm] = card["max_gap_beats"]
    assert gaps[95] < gaps[140] < gaps[180]
    seconds = {bpm: gaps[bpm] * 60.0 / bpm for bpm in gaps}
    assert max(seconds.values()) - min(seconds.values()) < 0.15, seconds


def test_tempo_dense_fallback_needs_the_cards_agreement():
    """The BPM-driven support onset may not resurrect what a quiet material's
    card just thinned: additions require drive >= TEMPO_DENSE_MIN_DRIVE."""
    from midi_lowend_director import TEMPO_DENSE_MIN_DRIVE, _tempo_dense_fallback

    shape = (0.0, 2.75, 5.0)
    quiet = {"drive": TEMPO_DENSE_MIN_DRIVE - 0.3}
    busy = {"drive": TEMPO_DENSE_MIN_DRIVE + 0.1}
    assert _tempo_dense_fallback(shape, 160.0, quiet) == list(shape)
    assert len(_tempo_dense_fallback(shape, 160.0, busy)) == len(shape) + 1
    assert len(_tempo_dense_fallback(shape, 175.0, busy)) == len(shape) + 2
    # No card at all: the pre-card behaviour is preserved.
    assert len(_tempo_dense_fallback(shape, 160.0)) == len(shape) + 1
    assert _tempo_dense_fallback(shape, 149.0, busy) == list(shape)


# --- the hat lane answers the melody INVERSELY (2026-08-19) -----------------


def _card(onsets_per_bar: int, bars: int = 8, drive: float = 0.5):
    return {
        "version": 1,
        "drive": drive,
        "melody_onsets_per_bar": [onsets_per_bar] * bars,
    }


def test_hat_budget_falls_as_the_melody_fills_the_bar():
    """The kit's job is the space the melody leaves, not a copy of its density.

    Before 2026-08-19 every hat term was PROPORTIONAL to melody busyness, so
    the busiest corpus file received hat_multiplier 0.970 — above
    thin_generated_hats' own >= 0.95 no-op cliff — and was thinned by zero,
    while the sparsest received 0.791 and lost 275 notes.
    """
    from drum_density import hat_floor_per_bar

    sparse = hat_floor_per_bar(_card(4))
    medium = hat_floor_per_bar(_card(6))
    dense = hat_floor_per_bar(_card(9))
    assert sparse > medium > dense
    assert dense >= 4  # never thinner than HAT_BUDGET_FLOOR_PER_BAR


def test_hat_budget_leaves_the_one_verified_good_reading_unconstrained():
    """Sahara is the control: the only file judged good by ear. Its own
    reading (4.3 melody clusters/bar) must come back unconstrained, or the
    change is trading a known-good result for an unproven one."""
    from drum_density import HAT_BUDGET_CEILING_PER_BAR, hat_floor_per_bar

    card = {"version": 1, "melody_onsets_per_bar": [4, 5, 4, 4, 5, 4, 4, 4]}
    assert hat_floor_per_bar(card) == HAT_BUDGET_CEILING_PER_BAR


def test_hat_multiplier_is_inverse_in_drive_and_low_end_is_not():
    """Only the hats invert. Everything the kick/808 chain reads stays
    proportional — its gates and the 0i.1/0i.2 tempo calibration depend on it."""
    from drum_density import build_drum_density_card

    def card_for(rate: float):
        pattern = {
            "plg_melody_energy": {"onset_rate": rate, "busyness": 0.7, "pace": "pocket"},
            "plg_arrangement": [{"name": "verse", "start_bar": 0, "bars": 8,
                                 "active_tracks": ["kick", "808_bass", "melody_lead"]}],
            "tracks": {"melody_lead": [{"time_step": float(i) * 0.5, "note": "C4",
                                        "length": 0.5, "velocity": 90} for i in range(64)]},
            "bpm": 140,
        }
        return build_drum_density_card(pattern)

    calm, busy = card_for(3.0), card_for(9.0)
    assert busy["drive"] > calm["drive"]
    assert busy["hat_multiplier"] < calm["hat_multiplier"]        # inverted
    assert busy["pairs_per_bar"] > calm["pairs_per_bar"]          # still proportional
    assert 0.5 <= busy["hat_multiplier"] <= 1.0                   # same band as before


def test_section_pocket_ceiling_steps_down_outside_the_peak():
    """The contrast is made by subtracting from the verse, never by adding to
    the hook: _cap_midi_kicks_per_bar leaves the hook no headroom at all."""
    from drum_density import MAX_PAIRS_PER_BAR, section_pocket_ceiling

    for peak in ("hook", "chorus", "drop", "DROP", " Hook "):
        assert section_pocket_ceiling(peak) == int(MAX_PAIRS_PER_BAR)
    for quiet in ("verse", "intro", "outro", "bridge", None, ""):
        assert section_pocket_ceiling(quiet) == int(MAX_PAIRS_PER_BAR) - 1
    assert section_pocket_ceiling("verse") >= 1


def test_scaled_shape_states_a_quiet_section_one_onset_more_simply():
    from drum_density import scaled_shape

    card = {"version": 1, "pocket_onsets_per_phrase": 4}
    shape = (0.0, 1.5, 3.0, 5.5, 7.0)
    assert scaled_shape(shape, card, section="hook") == scaled_shape(shape, card)
    quiet = scaled_shape(shape, card, section="verse")
    assert len(quiet) == 3
    # Positions are never invented, and the cell keeps its opening.
    assert set(quiet) <= set(shape) and quiet[0] == shape[0]


def test_scaled_shape_never_thins_a_cell_below_two_onsets():
    from drum_density import scaled_shape

    card = {"version": 1, "pocket_onsets_per_phrase": 2}
    assert len(scaled_shape((0.0, 1.5, 3.0, 5.5), card, section="verse")) == 2


def test_a_firm_phrase_keeps_the_cell_the_quiet_section_would_have_thinned():
    from drum_density import scaled_shape

    shape = (0.0, 2.0, 4.0, 6.0)
    card = {"pocket_onsets_per_phrase": 4}
    # Outside the peak sections the cell is stated one onset more simply...
    assert len(scaled_shape(shape, card, section="verse")) == 3
    # ...unless the melody itself already chose the rhythm, in which case
    # thinning it is an override, not a contrast (the project issue log (not included) 0x).
    assert scaled_shape(shape, card, section="verse", firm=True) == shape
    # The peak is untouched either way.
    assert scaled_shape(shape, card, section="hook") == shape
    assert scaled_shape(shape, card, section="hook", firm=True) == shape


# --- the level a hat appended after the leveller may carry (2026-08-25) ------
#
# beat_humanize.apply_six_db_hat_rule is the lane's only leveller and it runs
# at stage 10; four passes append hats after it. Each used to write a literal,
# so on the five-file corpus 33-157 notes per file shipped at a median of
# 77-96 and a maximum of 122-127 against lane medians of 37-45 -- the
# backbeat's own level, well past the 63 the leveller itself had computed.
# the project issue log (not included) 0v.8.


def _lane(median=40, spread=6, n=120):
    return [
        {"time_step": i * 0.5, "note": "C5", "length": 0.1,
         "velocity": median + (i % spread) - spread // 2}
        for i in range(n)
    ]


def test_a_late_hat_joins_the_lane_instead_of_shouting_over_it():
    lane = _lane(median=40)
    quiet = late_hat_velocity(lane, 88)
    assert quiet <= max(int(n["velocity"]) for n in lane), (
        "an added hat may be an accent, never the loudest thing in the lane"
    )
    assert quiet >= 40, "it still has to be audible as an accent"


def test_the_accent_sits_one_lift_above_the_lane_median_at_most():
    lane = [{"velocity": 40} for _ in range(100)] + [{"velocity": 90} for _ in range(4)]
    # median 40, 90th percentile 40 -> the p90 cap binds before the lift does
    assert late_hat_velocity(lane, 88) == 40
    spread = [{"velocity": v} for v in list(range(30, 80))]
    got = late_hat_velocity(spread, 88)
    assert got <= 30 + 49, "never above the lane's own 90th percentile"
    assert got <= int(sorted(n["velocity"] for n in spread)[25]) + LATE_HAT_ACCENT_LIFT


def test_a_writers_own_proportions_survive_the_ceiling():
    """The ceiling is this owner's decision; the shape inside it is not.

    enforce_midi_section_density writes 84/76 and the motif answer writes
    108/103/98. Those ratios are the writers' own accent shapes and the fix
    is about the LEVEL, not about re-deciding where their accents fall.
    """
    lane = _lane(median=44)
    loud, soft = late_hat_velocity(lane, 84, peak=84), late_hat_velocity(lane, 76, peak=84)
    assert loud > soft, "the writer's own accent must not be flattened"
    assert soft / loud == pytest.approx(76 / 84, abs=0.02)

    ramp = [late_hat_velocity(lane, 108 - v * 5, peak=108) for v in range(3)]
    assert ramp[0] > ramp[1] > ramp[2]
    assert ramp[0] == late_hat_velocity(lane, 108, peak=108)

    # A quieter member of the same palette stays below the palette's peak.
    assert late_hat_velocity(lane, 76, peak=108) < ramp[0]


def test_a_single_valued_writer_gets_the_ceiling_itself():
    lane = _lane(median=44)
    assert late_hat_velocity(lane, 76) == late_hat_velocity(lane, 76, peak=76)
    assert late_hat_velocity(lane, 88) == late_hat_velocity(lane, 999, peak=999)


def test_an_empty_lane_falls_back_without_raising():
    assert late_hat_velocity([], 88) == 88
    assert late_hat_velocity(None, 88) == 88
    assert late_hat_velocity([], 76, peak=108, default=90) == 63
    assert 1 <= late_hat_velocity(_lane(), 0, peak=0) <= 127


# --- the trim's structural protection, after humanisation (2026-08-26) ------
#
# thin_generated_hats runs at stage 11, one stage after apply_hi_hat_swing and
# apply_hat_forward_lag. Its two grid tests were exact (`< 1e-6`), so measured
# on the five-file corpus at the point it actually runs, ZERO notes out of
# 513-643 reached either branch -- and 41-62 of the notes it removed per file
# sat on a quarter. the project issue log (not included) 0v.10.


def _humanised(steps, lag=0.033, swing=0.09):
    """The lane as the trim really receives it: one constant forward lag for
    the whole track (apply_hat_forward_lag, measured on the real corpus) plus
    the off-beat eighth ("&", x.5) landing late (apply_hi_hat_swing). Since the
    2026-10-01 swing fix only the "&" moves, always later; 0.09 is the middle
    of what the trap lane draws (0.072-0.108 beats at intensity 1.15)."""
    out = []
    for step in steps:
        shift = lag + (swing if step % 1.0 == 0.5 else 0.0)
        out.append(round(step + shift, 5))
    return out


def _eighths_with_ornaments(bars=8):
    """A realistic generated bar: the 1/8 pulse plus a sixteenth ornament on
    the "e" of beats 2 and 4 — 10 hats a bar, the density the corpus runs at."""
    out = []
    for bar in range(bars):
        out += [bar * 4.0 + i * 0.5 for i in range(8)]
        out += [bar * 4.0 + 1.25, bar * 4.0 + 3.25]
    return sorted(out)


@pytest.mark.parametrize("multiplier", [0.75, 0.7, 0.6])
def test_the_pulse_survives_a_humanised_lane_not_just_a_grid_exact_one(multiplier):
    """The twin of test_thinning_removes_roll_interiors_before_the_pulse.

    That test passes on the grid-exact fixture it was written with, and went on
    passing all through the period the protection was dead — which is exactly
    why it could not catch this. Same musical content, moved off the grid the
    way the two humanising passes upstream really move it.

    Measured against the pre-2026-08-26 code on this fixture: 5, 7 and 10 hats
    removed from a quarter at the three multipliers, matching the 41-62 per
    file seen on the real corpus. The removal COUNT is unchanged either way —
    only which notes are chosen.
    """
    steps = _humanised(_eighths_with_ornaments())
    kept, removed = thin_generated_hats(_hats(steps), {"hat_multiplier": multiplier})
    assert removed > 0

    def cell(step):
        return round((step - 0.033) * 4.0)

    survived = {round(float(n["time_step"]), 5) for n in kept}
    lost = [t for t in steps if round(t, 5) not in survived]
    assert not [t for t in lost if cell(t) % 4 == 0], (
        "hats on a quarter were thinned away; the pulse protection is only "
        "reachable if the grid test tolerates humanisation"
    )
    assert sum(1 for t in lost if cell(t) % 2), (
        "the sixteenth ornaments should be the first thing taken"
    )


def test_a_roll_near_a_beat_is_still_taken_before_the_pulse():
    """The roll test has to run BEFORE the grid test, not after.

    On the real corpus 10-46 roll notes per file land within an eighth of a
    beat. If a tolerant pulse test saw them first it would protect part of
    every roll at random -- worse than the exact test it replaced.
    """
    pulse = _humanised([bar * 4.0 + b for bar in range(4) for b in (0.0, 1.0, 2.0, 3.0)])
    # a 1/32 run straddling beat 3 of each bar, i.e. right next to the pulse
    roll = [bar * 4.0 + 3.0 - 0.125 * i for bar in range(4) for i in range(4)]
    hats = _hats(sorted(pulse + roll))
    kept, removed = thin_generated_hats(hats, {"hat_multiplier": 0.7})
    kept_steps = {round(float(n["time_step"]), 4) for n in kept}
    survivors = sum(1 for t in roll if round(t, 4) in kept_steps)
    assert removed > 0
    assert survivors < len(roll), "roll interiors must still be the first to go"


def test_a_note_off_the_grid_is_never_promoted_to_pulse():
    """The fail-safe: further off than humanisation can explain means the
    question 'which sixteenth is this?' has no honest answer, so the note
    keeps the reading it had before this change rather than gaining
    protection it was never entitled to."""
    from drum_density import _HAT_SWING_REACH_BEATS

    grid = [bar * 4.0 + i * 0.5 for bar in range(4) for i in range(8)]
    stray = [bar * 4.0 + 1.0 + _HAT_SWING_REACH_BEATS * 1.6 for bar in range(4)]
    hats = _hats(sorted(grid + stray))
    kept, removed = thin_generated_hats(hats, {"hat_multiplier": 0.55})
    kept_steps = {round(float(n["time_step"]), 4) for n in kept}
    assert removed > 0
    assert any(round(t, 4) not in kept_steps for t in stray), (
        "an unplaceable note must stay removable, never be read as the pulse"
    )


def test_the_swing_reach_stays_in_step_with_the_swing_that_causes_it():
    """_HAT_SWING_REACH_BEATS is derived from beat_humanize's own swing depth
    but deliberately not imported from it (this module is a leaf; beat_humanize
    imports it). This test is the link: retune the swing and it fails here
    instead of silently widening the trim's tolerance."""
    from beat_humanize import PPQ_SWING_MAX
    from drum_density import _HAT_SWING_REACH_BEATS

    widest = PPQ_SWING_MAX * 1.15  # the intensity cap the live caller applies
    assert _HAT_SWING_REACH_BEATS <= 0.125, (
        "beyond half a sixteenth the nearest-cell question is ambiguous"
    )
    assert _HAT_SWING_REACH_BEATS <= widest + 0.005, (
        "the tolerance must not exceed the displacement it exists to absorb"
    )
    assert widest <= 0.125, (
        "swing itself now reaches past half a sixteenth — the de-lagged "
        "nearest-cell reading in thin_generated_hats is no longer safe"
    )

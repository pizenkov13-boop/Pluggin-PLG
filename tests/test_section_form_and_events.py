"""The five-part form, and a change-point at least every 16 bars.

Measured on the shipped corpus 2026-08-26, before this round: 4-5 marked
events in a 60-80 bar song, with a 23-28 bar hole between them, and a hook
that was stated once at bar 12 and never returned while verse took 56-60 % of
the song.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _arrangement(rows):
    return [
        {"name": n, "start_bar": s, "bars": b, "occurrence": 1, "energy": 1.0}
        for n, s, b in rows
    ]


def test_every_section_landing_gets_a_transition():
    """The defect this closes: a landing that was not a hook or a drop emitted
    NOTHING, so verse-following-verse — the commonest boundary in an extended
    form — was inaudible."""
    from arranger import _midi_transitions_for

    plans = _midi_transitions_for(_arrangement([
        ("intro", 0, 4), ("verse", 4, 8), ("hook", 12, 8), ("drop", 20, 8),
        ("verse", 28, 8), ("outro", 36, 4),
    ]))
    landings = [p["landing_bar"] for p in plans]

    assert landings == [4, 12, 20, 28, 36]
    assert landings == sorted(landings)  # both appliers walk the list in order
    assert {p["target"] for p in plans} == {"verse", "hook", "drop", "outro"}


def test_bar_zero_never_gets_a_transition():
    from arranger import _midi_transitions_for

    plans = _midi_transitions_for(_arrangement([("intro", 0, 8), ("hook", 8, 16)]))
    assert all(p["landing_bar"] > 0 for p in plans)


def test_the_peak_kinds_are_exactly_what_they_always_were():
    """Confirmed work: hook breathes, a peak stops. Only the verse/outro rows
    are new, so a regression here means the round reached further than it said."""
    from arranger import _midi_transitions_for

    plans = _midi_transitions_for(_arrangement([
        ("intro", 0, 4), ("hook", 4, 8), ("verse", 12, 8), ("drop", 20, 8),
    ]))
    by_target = {p["target"]: p["kind"] for p in plans}
    assert by_target["hook"] == "kick_breath_fill"
    assert by_target["drop"] == "half_bar_stop"


def test_a_long_section_gets_a_midpoint_breath_and_never_a_stop():
    """A 16-bar block whose only event is its own entrance is a 16-bar gap —
    the very edge of the requirement. A full rhythm-section stop mid-section
    would read as a landing that never arrives, so the midpoint is always the
    breath."""
    from arranger import _midi_transitions_for

    plans = _midi_transitions_for(_arrangement([
        ("intro", 0, 8), ("hook", 8, 16), ("verse", 24, 8),
    ]))
    mid = [p for p in plans if p["target"].endswith("_mid")]

    assert [p["landing_bar"] for p in mid] == [16]
    assert mid[0]["kind"] == "kick_breath_fill"
    # the 8-bar verse is short enough to need no midpoint
    assert not [p for p in plans if p["target"] == "verse_mid"]


def test_the_form_is_five_parts_and_its_second_peak_is_a_drop():
    """Load-bearing naming, not cosmetics. `arrange_song` picks the final
    chorus from ("chorus", "drop") and `half_bar_stop` lands on drops, so a
    form whose second peak is called "hook" loses the beat switch AND the stop
    on every file — measured on all five corpus BPMs."""
    from arranger import MIDI_SECTIONS

    names = [name for name, _bars, _tracks in MIDI_SECTIONS]
    assert names == ["intro", "hook", "verse", "drop", "outro"]
    assert names.index("hook") < names.index("verse") < names.index("drop")


def test_the_hook_lands_before_the_first_verse():
    """The old form opened on a verse; the reference opens on the statement."""
    from arranger import MIDI_SECTIONS

    order = [name for name, _b, _t in MIDI_SECTIONS]
    assert order[1] == "hook"


def test_the_extended_form_keeps_a_peak_and_never_runs_five_verses_deep():
    """The tail used to be cycle-filled from ("verse", "chorus", "drop"), which
    the hook is not in — hence 5-6 consecutive verses. Checked across the real
    tempo range, through the real sizing helpers."""
    from arranger import (
        MIDI_SECTIONS,
        _extend_sections_to_duration,
        _shrink_sections_to_duration,
        _target_song_bars,
    )

    for bpm in range(80, 221, 10):
        target = _target_song_bars({"bpm": bpm})
        form = _shrink_sections_to_duration(
            _extend_sections_to_duration(MIDI_SECTIONS, target), target
        )
        names = [name for name, _b, _t in form]
        assert "hook" in names, bpm
        assert any(n in ("chorus", "drop") for n in names), bpm



def test_every_tempo_gets_a_change_point_at_least_every_16_bars():
    """The standing requirement, checked through the real sizing helpers and
    the real transition planner rather than on one hand-written form.

    Note this asserts the EVENT grid, not the section names: at 170 BPM the
    extend cycle does append a 4-bar verse after a 16-bar one (a 20-bar run of
    the same role), and that is fine — the 16-bar block still takes a midpoint
    breath and the boundary between them is itself an event.
    """
    from arranger import (
        MIDI_SECTIONS,
        _extend_sections_to_duration,
        _midi_transitions_for,
        _shrink_sections_to_duration,
        _target_song_bars,
    )
    from beat_humanize import SECTION_EVENT_MAX_GAP_BARS

    for bpm in range(80, 221, 10):
        target = _target_song_bars({"bpm": bpm})
        form = _shrink_sections_to_duration(
            _extend_sections_to_duration(MIDI_SECTIONS, target), target
        )
        total = sum(bars for _n, bars, _t in form)
        arrangement = []
        cursor = 0
        for name, bars, _tracks in form:
            arrangement.append({"name": name, "start_bar": cursor, "bars": bars})
            cursor += bars

        bars_with_events = sorted({p["landing_bar"] for p in _midi_transitions_for(arrangement)})
        gaps = [b - a for a, b in zip([0] + bars_with_events, bars_with_events + [total])]
        assert max(gaps) <= SECTION_EVENT_MAX_GAP_BARS, (bpm, bars_with_events, gaps)


def test_the_event_map_reaches_the_pattern_and_flags_a_dead_stretch():
    import beat_humanize as bh

    data = {
        "bpm": 150.0,
        "plg_total_bars": 40,
        "tracks": {"kick": []},
        "plg_midi_transition_report": [
            {"landing_bar": 8, "kind": "kick_breath_fill", "target": "hook",
             "removed": 2, "fill_hits": 6, "window": [28.0, 32.0]},
        ],
    }
    summary = bh._measure_section_events(data)

    assert data["plg_section_events"] is summary
    assert summary["event_bars"] == [8]
    assert summary["gaps_bars"] == [8, 32]
    assert summary["max_gap_bars"] == 32
    assert summary["meets_change_requirement"] is False


def test_a_note_left_inside_a_cleared_window_is_recorded_not_hidden():
    """A later pass refilling a deliberate silence is the the project issue log (not included) 0v.11
    defect class. It belongs in the exported data where it can be grepped."""
    import beat_humanize as bh

    data = {
        "bpm": 150.0,
        "plg_total_bars": 16,
        "tracks": {"hi_hats": [{"time_step": 29.0, "note": "F#3",
                                "length": 0.1, "velocity": 60}]},
        "plg_midi_transition_report": [
            {"landing_bar": 8, "kind": "half_bar_stop", "target": "drop",
             "removed": 5, "fill_hits": 0, "window": [28.0, 32.0]},
        ],
    }
    summary = bh._measure_section_events(data)

    assert summary["events"][0]["survivors_in_window"] == 1
    assert summary["windows_with_survivors"] == [8]


def test_the_pre_clap_roll_countdown_runs_from_the_last_roll_not_the_section_start():
    """Three claps in a row, every bar, for ten bars — heard on the five-part
    form 2026-08-26.

    `add_pre_clap_roll`'s section-relative branch set its next threshold to
    `start_bar + 4..8` instead of `bar + 4..8`. Once a section was more than
    eight bars old every later threshold was already in the past, so the gate
    stopped blocking and a roll fired on every remaining clap. Sections used to
    be 4-8 bars, which hid it; 16-bar sections did not.
    """
    import random

    import beat_humanize as bh

    arrangement = [
        {"name": "intro", "start_bar": 0, "bars": 8},
        {"name": "hook", "start_bar": 8, "bars": 16},
        {"name": "verse", "start_bar": 24, "bars": 16},
    ]
    claps = [
        {"time_step": bar * 4.0 + 2.0, "note": "C5", "length": 0.2, "velocity": 110}
        for bar in range(40)
    ]
    out = bh.add_pre_clap_roll(
        claps, random.Random(7), arrangement=arrangement, base_seed=80820
    )

    roll_bars = sorted({int(float(n["time_step"]) // 4.0)
                        for n in out if n.get("pre_clap_roll")})
    assert roll_bars, "the gesture must still happen"
    # Never two bars running, and never closer than the declared minimum.
    gaps = [b - a for a, b in zip(roll_bars, roll_bars[1:])]
    assert all(gap >= bh._PRE_CLAP_ROLL_MIN_BARS for gap in gaps), roll_bars
    # And it must stay a MINORITY of the lane, which is what "every 4-8 bars"
    # means: before the fix it reached 55 % of every clap in the song.
    rolls = sum(1 for n in out if n.get("pre_clap_roll"))
    assert rolls < len(out) * 0.4, (rolls, len(out))

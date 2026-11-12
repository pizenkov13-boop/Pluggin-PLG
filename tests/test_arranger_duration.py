"""The arranged form targets ~120 s of listening time at ANY tempo.

Regression cover for a measured overshoot: `_target_song_bars` rounded the bar
count and then applied a hard `max(40, ...)` floor. That floor binds below
roughly 76 BPM, so a 68 BPM session asked for 34 bars, was floored to 40, and
exported 141 s against a 120 s target. Compounding it, the arranger could only
ever *extend* a form — at a slow tempo the target is fewer bars than the base
form already holds, so nothing acted on the overshoot at all.
"""
from __future__ import annotations

import pytest

from arranger import (
    BEATS_PER_BAR,
    DEFAULT_SECTIONS,
    LEGACY_MIDI_SECTIONS,
    MIDI_SECTIONS,
    TARGET_SONG_SECONDS,
    _extend_sections_to_duration,
    _shrink_sections_to_duration,
    _target_song_bars,
    midi_sections_for_target,
)

TUNED_FORMS = (MIDI_SECTIONS, LEGACY_MIDI_SECTIONS, DEFAULT_SECTIONS)

# `_shrink_sections_to_duration` is what these exercise; the form is only its
# input. They use the pre-2026-08-26 MIDI form because that is the geometry
# they were written against — a 40-bar form with repeated verses to choose
# between. The live form's own duration behaviour is covered by
# `_resolved_midi_bars` below, which goes through the production selector.
SHRINK_FIXTURE = LEGACY_MIDI_SECTIONS


def _seconds(bars: int, bpm: float) -> float:
    return bars * BEATS_PER_BAR * 60.0 / bpm


def _resolved_bars(form, bpm: float) -> int:
    target = _target_song_bars({"bpm": bpm})
    out = _shrink_sections_to_duration(_extend_sections_to_duration(form, target), target)
    return sum(bars for _name, bars, _tracks in out)


def _resolved_midi_bars(bpm: float) -> int:
    """The MIDI form exactly as `arrange_song` resolves it.

    Since 2026-08-26 the block size itself comes from the target
    (`midi_sections_for_target`): the five-part form is 16-bar blocks where
    there is room and 8-bar blocks where there is not, because the 16-bar
    version cannot be shrunk below 40 bars and a slow tempo asks for fewer.
    Calling the sizing helpers on the bare constant would test a path
    production no longer takes — and would let the 141 s regression back in
    through the door this file exists to hold shut.
    """
    return _resolved_bars(midi_sections_for_target(_target_song_bars({"bpm": bpm})), bpm)


@pytest.mark.parametrize("bpm", [68, 75, 85, 95, 110, 120, 130, 135, 140, 150, 160, 170, 180, 200])
def test_midi_form_lands_within_half_a_section_of_the_target(bpm):
    """Half a 4-bar section is the best any 4-bar-quantised form can do."""
    bars = _resolved_midi_bars(bpm)
    error = abs(_seconds(bars, bpm) - TARGET_SONG_SECONDS)
    assert error <= _seconds(2, bpm) + 1e-6, f"{_seconds(bars, bpm):.1f}s at {bpm} BPM"


def test_the_reproduced_slow_tempo_case():
    """68 BPM used to give 40 bars / 141.2 s against a 120 s target."""
    bars = _resolved_midi_bars(68)
    assert bars < 40
    assert _seconds(bars, 68) < 130.0


@pytest.mark.parametrize("bpm", [85, 95, 110, 120, 130, 135, 140, 150, 160, 170, 180, 200, 220])
def test_tempos_that_were_never_broken_are_untouched(bpm):
    """The floor only bound below ~76 BPM. Everything above it must keep the
    exact bar count it shipped, including the two tempos whose target sits
    precisely between two forms (140 -> 72, 180 -> 88)."""
    legacy = max(40, min(160, int(round(TARGET_SONG_SECONDS * bpm / (60.0 * BEATS_PER_BAR) / 4.0)) * 4))
    assert _target_song_bars({"bpm": bpm}) == legacy


def test_a_slow_tempo_no_longer_hits_the_removed_floor():
    assert _target_song_bars({"bpm": 68}) < 40
    assert _target_song_bars({"bpm": 75}) < 40


def test_bad_bpm_still_yields_a_usable_form():
    for bad in (None, "", "abc", 0):
        assert _target_song_bars({"bpm": bad}) >= 16


# --- shrink ----------------------------------------------------------------


def test_shrink_is_a_no_op_when_the_form_already_fits():
    assert _shrink_sections_to_duration(SHRINK_FIXTURE, 40) == SHRINK_FIXTURE
    assert _shrink_sections_to_duration(SHRINK_FIXTURE, 999) == SHRINK_FIXTURE
    assert _shrink_sections_to_duration((), 8) == ()


def test_shrink_keeps_intro_outro_and_the_identity_statement():
    out = _shrink_sections_to_duration(SHRINK_FIXTURE, 32)
    assert sum(bars for _n, bars, _t in out) == 32
    by_name = [(name, bars) for name, bars, _t in out]
    assert by_name[0] == ("intro", 4)
    assert by_name[-1] == ("outro", 4)
    # The first peak section states the song's identity and is never trimmed.
    first_peak = next(i for i, (n, _b, _t) in enumerate(SHRINK_FIXTURE)
                      if n in ("hook", "chorus", "drop"))
    assert out[first_peak][1] == SHRINK_FIXTURE[first_peak][1]


def test_shrink_never_drops_a_section_or_goes_under_four_bars():
    out = _shrink_sections_to_duration(SHRINK_FIXTURE, 8)
    assert len(out) == len(SHRINK_FIXTURE)
    assert all(bars >= 4 for _n, bars, _t in out)
    # Section names and their track gates survive untouched.
    assert [n for n, _b, _t in out] == [n for n, _b, _t in SHRINK_FIXTURE]
    assert [t for _n, _b, t in out] == [t for _n, _b, t in SHRINK_FIXTURE]


def test_shrink_trims_in_whole_four_bar_steps():
    out = _shrink_sections_to_duration(SHRINK_FIXTURE, 36)
    for (_name, before, _t), (_n2, after, _t2) in zip(SHRINK_FIXTURE, out):
        assert (before - after) % 4 == 0


def test_shrink_prefers_later_repeats_over_the_opening():
    """Losing the tail repeat is a producer's edit; losing the opening is not."""
    out = _shrink_sections_to_duration(SHRINK_FIXTURE, 36)
    verses = [(i, bars) for i, (name, bars, _t) in enumerate(out) if name == "verse"]
    original = [(i, bars) for i, (name, bars, _t) in enumerate(SHRINK_FIXTURE) if name == "verse"]
    assert len(verses) == len(original) >= 2
    # The later verse gave up the bars, not the first one.
    assert verses[-1][1] < original[-1][1]
    assert verses[0][1] == original[0][1]


def test_shrink_is_best_effort_when_protected_sections_already_exceed_target():
    out = _shrink_sections_to_duration(SHRINK_FIXTURE, 4)
    assert sum(bars for _n, bars, _t in out) >= 4
    assert all(bars >= 4 for _n, bars, _t in out)


@pytest.mark.parametrize("form", TUNED_FORMS)
def test_extend_and_shrink_agree_on_a_form_that_already_fits(form):
    target = sum(bars for _n, bars, _t in form)
    assert _extend_sections_to_duration(form, target) == form
    assert _shrink_sections_to_duration(form, target) == form

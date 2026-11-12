from melody_transform import (
    fragment_transform,
    inversion_transform,
    melody_variant_for_section,
    retrograde_transform,
    sequence_transform,
)
from pattern_utils import parse_note_name


def _notes(*specs):
    """specs = (time_step, note, length) tuples, velocity fixed at 100."""
    return [
        {"time_step": t, "note": n, "length": length, "velocity": 100}
        for t, n, length in specs
    ]


# --- retrograde: the flagged highest-risk transform -------------------------


def test_retrograde_mirrors_gaps_not_just_pitch_order():
    # Original (reading forward): note@0 len1 [0,1) -- gap 1 -- note@2 len0.5
    # [2,2.5) -- gap 0.5 -- note@3 len1 [3,4). Gap sequence forward: [1, 0.5].
    notes = _notes((0.0, "A4", 1.0), (2.0, "C5", 0.5), (3.0, "E5", 1.0))
    out = retrograde_transform(notes, core_beats=4.0)
    steps = [n["time_step"] for n in out]
    # Hand-verified: new_start = core_beats - old_start - length.
    assert steps == [0.0, 1.5, 3.0]
    # Reading forward, the gap sequence must be the exact REVERSE of the
    # original ([1, 0.5] -> [0.5, 1]), not just the notes in reverse order.
    ends = [n["time_step"] + n["length"] for n in out]
    gaps = [round(out[i + 1]["time_step"] - ends[i], 5) for i in range(len(out) - 1)]
    assert gaps == [0.5, 1.0]


def test_retrograde_preserves_note_lengths_and_count():
    notes = _notes((0.0, "A4", 1.0), (2.0, "C5", 0.5), (3.0, "E5", 1.0))
    out = retrograde_transform(notes, core_beats=4.0)
    assert len(out) == len(notes)
    assert sorted(n["length"] for n in out) == sorted(n["length"] for n in notes)


def test_retrograde_is_an_involution():
    """Applying retrograde twice must return exactly the original timing —
    the core correctness invariant for a time-reversal."""
    notes = _notes((0.0, "A4", 0.75), (1.5, "C5", 0.5), (2.5, "D5", 1.25), (4.0, "E5", 0.5))
    core_beats = 4.5  # deliberately exceeds the last note's end (4.0+0.5=4.5, exact fit)
    once = retrograde_transform(notes, core_beats)
    twice = retrograde_transform(once, core_beats)
    orig_steps = sorted(n["time_step"] for n in notes)
    twice_steps = sorted(n["time_step"] for n in twice)
    assert twice_steps == orig_steps


def test_retrograde_never_produces_negative_or_overlapping_notes():
    notes = _notes((0.0, "A4", 1.0), (1.0, "B4", 1.0), (2.0, "C5", 1.0), (3.0, "D5", 1.0))
    out = retrograde_transform(notes, core_beats=4.0)
    for n in out:
        assert n["time_step"] >= 0.0
    ordered = sorted(out, key=lambda n: n["time_step"])
    for a, b in zip(ordered, ordered[1:], strict=False):
        assert a["time_step"] + a["length"] <= b["time_step"] + 1e-6


def test_retrograde_clamps_overhanging_note_to_non_negative():
    # A note whose end slightly exceeds core_beats (upstream rounding) must
    # not produce a negative time_step.
    notes = _notes((3.8, "A4", 0.5))  # ends at 4.3, core_beats=4.0
    out = retrograde_transform(notes, core_beats=4.0)
    assert out[0]["time_step"] >= 0.0


def test_retrograde_empty_is_a_noop():
    assert retrograde_transform([], core_beats=4.0) == []


# --- sequence -----------------------------------------------------------


def test_sequence_transposes_diatonically_up():
    notes = _notes((0.0, "A4", 1.0))
    out = sequence_transform(notes, root_pc=9, scale="natural_minor", steps=2)  # A minor
    # A -> C is a diatonic third up in A natural minor (2 scale degrees).
    assert out[0]["note"] == "C5"


def test_sequence_preserves_rhythm():
    notes = _notes((0.5, "A4", 0.75), (2.0, "C5", 1.5))
    out = sequence_transform(notes, root_pc=9, scale="natural_minor", steps=2)
    assert [n["time_step"] for n in out] == [0.5, 2.0]
    assert [n["length"] for n in out] == [0.75, 1.5]


# --- inversion ------------------------------------------------------------


def test_inversion_leaves_the_axis_note_unchanged():
    notes = _notes((0.0, "A4", 1.0), (1.0, "C5", 1.0))
    out = inversion_transform(notes, root_pc=9, scale="natural_minor")
    assert out[0]["note"] == "A4"


def test_inversion_flips_contour_direction():
    # Second note is ABOVE the axis (A4->C5, up); after inversion it must be
    # AT OR BELOW the axis pitch (mirrored down).
    notes = _notes((0.0, "A4", 1.0), (1.0, "C5", 1.0))
    axis_midi = parse_note_name("A4")
    out = inversion_transform(notes, root_pc=9, scale="natural_minor")
    mirrored_midi = parse_note_name(out[1]["note"])
    assert mirrored_midi <= axis_midi


def test_inversion_preserves_rhythm():
    notes = _notes((0.0, "A4", 1.0), (1.5, "E5", 0.5))
    out = inversion_transform(notes, root_pc=9, scale="natural_minor")
    assert [n["time_step"] for n in out] == [0.0, 1.5]
    assert [n["length"] for n in out] == [1.0, 0.5]


# --- fragment ---------------------------------------------------------------


def test_fragment_keeps_only_first_half_and_shrinks_core_beats():
    notes = _notes((0.0, "A4", 1.0), (1.0, "B4", 1.0), (2.5, "C5", 1.0), (3.0, "D5", 1.0))
    out, new_core_beats = fragment_transform(notes, core_beats=4.0)
    assert new_core_beats == 2.0
    assert [n["time_step"] for n in out] == [0.0, 1.0]


def test_fragment_falls_back_to_full_core_if_first_half_empty():
    notes = _notes((3.5, "A4", 0.5))  # nothing before beat 2.0
    out, new_core_beats = fragment_transform(notes, core_beats=4.0)
    assert new_core_beats == 4.0
    assert len(out) == 1


# --- section -> transform dispatch ------------------------------------------


# Deliberately uneven lengths/gaps — an evenly-spaced equal-length motif would
# be its own retrograde by coincidence (a rhythmic palindrome), which would
# make retrograde-vs-identity assertions meaningless.
_MOTIF = _notes((0.0, "A4", 1.5), (1.5, "C5", 0.5), (2.0, "E5", 1.0), (3.0, "D5", 1.0))


def _dispatch(section, occurrence, is_final_chorus=False):
    return melody_variant_for_section(
        _MOTIF, 4.0,
        section=section, occurrence=occurrence, is_final_chorus=is_final_chorus,
        root_pc=9, scale="natural_minor",
    )


def test_hook_is_always_identity():
    notes, core_beats = _dispatch("hook", occurrence=1)
    assert core_beats == 4.0
    assert [n["note"] for n in notes] == [n["note"] for n in _MOTIF]
    assert [n["time_step"] for n in notes] == [n["time_step"] for n in _MOTIF]


def test_final_chorus_is_identity_not_double_transformed():
    # The existing arranger._octave_lift already handles the final-chorus
    # peak; this must NOT also apply sequence on top of it.
    notes, core_beats = _dispatch("chorus", occurrence=2, is_final_chorus=True)
    assert [n["note"] for n in notes] == [n["note"] for n in _MOTIF]


def test_non_final_chorus_gets_sequence():
    notes, _ = _dispatch("chorus", occurrence=1, is_final_chorus=False)
    assert [n["note"] for n in notes] != [n["note"] for n in _MOTIF]
    # Rhythm must still match the original core.
    assert [n["time_step"] for n in notes] == [n["time_step"] for n in _MOTIF]


def test_first_verse_is_fragment_second_verse_is_retrograde():
    v1_notes, v1_core_beats = _dispatch("verse", occurrence=1)
    v2_notes, v2_core_beats = _dispatch("verse", occurrence=2)
    assert v1_core_beats < 4.0  # fragmented, shorter loop
    assert v2_core_beats == 4.0  # retrograde keeps the full core length
    # The two verse passes must not be identical to each other (the whole
    # point — today they were the exact same tiled core twice).
    assert v1_notes != v2_notes


def test_outro_is_retrograde():
    notes, core_beats = _dispatch("outro", occurrence=1)
    assert core_beats == 4.0
    assert [n["time_step"] for n in notes] != [n["time_step"] for n in _MOTIF]


def test_intro_is_fragment():
    notes, core_beats = _dispatch("intro", occurrence=1)
    assert core_beats < 4.0


def test_unknown_section_is_identity():
    notes, core_beats = _dispatch("bridge", occurrence=1)
    assert core_beats == 4.0
    assert [n["note"] for n in notes] == [n["note"] for n in _MOTIF]


def test_empty_notes_is_a_noop():
    notes, core_beats = melody_variant_for_section(
        [], 4.0, section="verse", occurrence=1, is_final_chorus=False,
        root_pc=9, scale="natural_minor",
    )
    assert notes == []
    assert core_beats == 4.0

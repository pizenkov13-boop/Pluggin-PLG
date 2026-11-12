"""Tests for beat_humanize._correct_808_vertical_clashes (2026-08-15).

The vertical consonance owner compares each 808 attack with the melody
notes SOUNDING AT THE SAME TIME and re-pitches clashes (m2 / tritone / M7)
onto a consonant pitch class from the shared LowendRootMap scale set.
Only the ``note`` field may change — time_step / velocity / length must
survive byte-identical (the placement-only constraint).
"""

from copy import deepcopy

from beat_humanize import _correct_808_vertical_clashes


def _pattern(bass_notes, melody_notes):
    return {
        "tracks": {
            "808_bass": bass_notes,
            "melody_lead": melody_notes,
        }
    }


def _clash_intervals(pc_a, pc_b):
    diff = abs(pc_a - pc_b) % 12
    return min(diff, 12 - diff) in (1, 6, 11)


def test_clashing_808_is_repitched_to_a_consonant_pitch_class():
    # Melody holds C4 across the whole bar; the 808 lands on C#1 — a minor
    # second against the sounding melody.
    data = _pattern(
        [{"time_step": 0.0, "note": "C#1", "length": 1.0, "velocity": 100}],
        [{"time_step": 0.0, "note": "C4", "length": 4.0, "velocity": 90}],
    )
    stats = _correct_808_vertical_clashes(data)
    assert stats["checked"] == 1
    assert stats["clashes_found"] == 1
    assert stats["repaired"] == 1
    note = data["tracks"]["808_bass"][0]
    from pattern_utils import parse_note_name

    new_pc = parse_note_name(note["note"]) % 12
    assert not _clash_intervals(new_pc, 0)  # consonant with the sounding C


def test_consonant_808_is_left_untouched():
    data = _pattern(
        [{"time_step": 0.0, "note": "C1", "length": 1.0, "velocity": 100}],
        [{"time_step": 0.0, "note": "C4", "length": 4.0, "velocity": 90}],
    )
    before = deepcopy(data["tracks"]["808_bass"])
    stats = _correct_808_vertical_clashes(data)
    assert stats["clashes_found"] == 0
    assert stats["repaired"] == 0
    assert data["tracks"]["808_bass"] == before


def test_placement_fields_survive_byte_identical():
    """The repair may only rewrite the pitch name — never timing, length or
    velocity (the placement-only / no-timbre constraint)."""
    original = {"time_step": 2.5, "note": "C#1", "length": 1.75, "velocity": 97}
    data = _pattern(
        [dict(original)],
        [{"time_step": 0.0, "note": "C4", "length": 8.0, "velocity": 90}],
    )
    stats = _correct_808_vertical_clashes(data)
    assert stats["repaired"] == 1
    note = data["tracks"]["808_bass"][0]
    assert note["note"] != original["note"]
    assert note["time_step"] == original["time_step"]
    assert note["length"] == original["length"]
    assert note["velocity"] == original["velocity"]


def test_slide_notes_are_skipped():
    data = _pattern(
        [{"time_step": 0.0, "note": "C#1", "length": 1.0, "velocity": 100, "slide": True}],
        [{"time_step": 0.0, "note": "C4", "length": 4.0, "velocity": 90}],
    )
    stats = _correct_808_vertical_clashes(data)
    assert stats["skipped_slides"] == 1
    assert stats["checked"] == 0
    assert data["tracks"]["808_bass"][0]["note"] == "C#1"


def test_808_with_no_sounding_melody_is_not_checked():
    data = _pattern(
        [{"time_step": 0.0, "note": "C#1", "length": 1.0, "velocity": 100}],
        [{"time_step": 8.0, "note": "C4", "length": 1.0, "velocity": 90}],
    )
    stats = _correct_808_vertical_clashes(data)
    assert stats["checked"] == 0
    assert stats["clashes_found"] == 0
    assert data["tracks"]["808_bass"][0]["note"] == "C#1"
    assert data["plg_808_vertical"] == stats


def test_unrepairable_chromatic_cluster_is_counted_not_forced():
    # Six adjacent chromatic melody pcs forbid every natural-minor scale pc;
    # the repair must leave the note alone and report it.
    melody = [
        {"time_step": 0.0, "note": name, "length": 4.0, "velocity": 90}
        for name in ("C4", "C#4", "D4", "D#4", "E4", "F4")
    ]
    data = _pattern(
        [{"time_step": 0.0, "note": "F#1", "length": 1.0, "velocity": 100}],
        melody,
    )
    stats = _correct_808_vertical_clashes(data)
    assert stats["clashes_found"] == 1
    assert stats["unrepairable"] == 1
    assert stats["repaired"] == 0
    assert data["tracks"]["808_bass"][0]["note"] == "F#1"


def test_repair_is_deterministic():
    def build():
        return _pattern(
            [
                {"time_step": 0.0, "note": "C#1", "length": 1.0, "velocity": 100},
                {"time_step": 4.0, "note": "D1", "length": 1.0, "velocity": 100},
            ],
            [
                {"time_step": 0.0, "note": "C4", "length": 4.0, "velocity": 90},
                {"time_step": 4.0, "note": "G#3", "length": 4.0, "velocity": 90},
            ],
        )

    first, second = build(), build()
    _correct_808_vertical_clashes(first)
    _correct_808_vertical_clashes(second)
    assert first["tracks"]["808_bass"] == second["tracks"]["808_bass"]
    assert first["plg_808_vertical"] == second["plg_808_vertical"]


def test_a_clashing_colour_falls_back_to_its_own_root_not_to_the_nearest_pc():
    """Precedence after chord voicing landed (2026-08-16).

    The plan may be overruled by the ear; the harmony should not be. When a
    colour slot's third clashes with the sounding melody, this owner must try
    the window's ROOT before "nearest consonant scale pc to the note we are
    replacing" — otherwise a repaired colour can land further from the chord
    than the root the attack would have taken with no colour at all.
    """
    from music_theory import SCALES, parse_note_name

    a_minor = {(9 + i) % 12 for i in SCALES["natural_minor"]}
    # A minor window with a real third (C) and fifth (E); the next window is a
    # different root, so beat 3-4 is planned as the third.
    windows = [
        {"start": 0.0, "end": 4.0, "root_pc": 9, "confidence": 0.8,
         "chord_pcs": {"root": 9, "third": 0, "fifth": 4, "seventh": None}},
        {"start": 4.0, "end": 8.0, "root_pc": 5, "confidence": 0.8,
         "chord_pcs": {"root": 5, "third": 9, "fifth": 0, "seventh": None}},
    ]
    from midi_lowend_director import LowendRootMap

    root_map = LowendRootMap(
        harmonic_windows=windows, melody=[], roots_pc=[9], scale_pcs=a_minor
    )
    assert root_map.tone_at(2.5) == (0, "window", "third")  # C, the third

    # The melody sounds B over beat 3 — a minor second against that C, while
    # the window's own root A is perfectly consonant with it.
    data = {
        "tracks": {
            "808_bass": [{"time_step": 2.5, "note": "C1", "length": 1.0, "velocity": 100}],
            "melody_lead": [{"time_step": 2.5, "note": "B4", "length": 1.0, "velocity": 90}],
        },
        "plg_midi_structure": {"harmonic_rhythm": windows},
        "plg_progression": {"scale": "natural_minor", "roots_pc": [9]},
        "key": "A",
        "scale": "natural_minor",
    }
    stats = _correct_808_vertical_clashes(data)
    assert stats["clashes_found"] == 1
    assert stats["repaired"] == 1
    assert parse_note_name(data["tracks"]["808_bass"][0]["note"]) % 12 == 9  # the root A


def test_empty_lanes_stamp_zero_metadata_without_crashing():
    data = {"tracks": {"808_bass": [], "melody_lead": []}}
    stats = _correct_808_vertical_clashes(data)
    assert stats == {
        "checked": 0,
        "clashes_found": 0,
        "repaired": 0,
        "unrepairable": 0,
        "skipped_slides": 0,
        "root_overruled": 0,
        "off_map_substitutions": 0,
    }
    assert data["plg_808_vertical"] == stats


def _two_window_pattern(bass_note, melody_notes):
    """An A minor window followed by an F window, both confident.

    harmony_pcs is therefore {A, C, E, F} — A and F as roots, C and E as the
    tones the analysis measured. G is IN the key and NOT in that set, which is
    what makes it usable as the off-map candidate below.
    """
    windows = [
        {"start": 0.0, "end": 4.0, "root_pc": 9, "confidence": 0.8,
         "chord_pcs": {"root": 9, "third": 0, "fifth": 4, "seventh": None}},
        {"start": 4.0, "end": 8.0, "root_pc": 5, "confidence": 0.8,
         "chord_pcs": {"root": 5, "third": 9, "fifth": 0, "seventh": None}},
    ]
    return {
        "tracks": {"808_bass": [bass_note], "melody_lead": melody_notes},
        "plg_midi_structure": {"harmonic_rhythm": windows},
        "plg_progression": {"scale": "natural_minor", "roots_pc": [9]},
        "key": "A",
        "scale": "natural_minor",
    }


def test_an_overruled_root_cannot_land_on_a_pitch_class_the_harmony_never_chose():
    """The defect this owner shipped until 2026-08-20 (the project issue log (not included) §0t).

    When BOTH the planned tone and the window's own root are vetoed, the last
    resort used to pick the nearest consonant SCALE pc — a selector with no
    harmonic term, so it could land on a degree the song's harmony never used.
    It must now prefer a pitch class from LowendRootMap.harmony_pcs.
    """
    from music_theory import parse_note_name

    # Melody sounds G# and B over the A minor window: A (the root) clashes with
    # G# (m2), and so does the planned root. G is the nearest surviving SCALE
    # pc to A — one semitone away — but G is not in harmony_pcs {A, C, E, F}.
    data = _two_window_pattern(
        {"time_step": 0.0, "note": "A1", "length": 1.0, "velocity": 100},
        [
            {"time_step": 0.0, "note": "G#4", "length": 4.0, "velocity": 90},
            {"time_step": 0.0, "note": "B4", "length": 4.0, "velocity": 90},
        ],
    )
    stats = _correct_808_vertical_clashes(data)
    assert stats["clashes_found"] == 1
    assert stats["repaired"] == 1
    assert stats["root_overruled"] == 1
    assert stats["off_map_substitutions"] == 0

    shipped = parse_note_name(data["tracks"]["808_bass"][0]["note"]) % 12
    assert shipped != 7, "G is in the key but the harmony never chose it"
    assert shipped in {9, 0, 4, 5}
    assert not _clash_intervals(shipped, 8)
    assert not _clash_intervals(shipped, 11)


def test_an_empty_harmony_map_keeps_the_old_full_scale_behaviour():
    """No confident window means no harmonic map to prefer — the last resort
    must still choose from the whole key rather than refusing to repair."""
    from midi_lowend_director import LowendRootMap
    from music_theory import parse_note_name

    # confidence below the floor: the window is dropped, harmony_pcs is empty.
    windows = [{"start": 0.0, "end": 4.0, "root_pc": 9, "confidence": 0.1,
                "chord_pcs": {"root": 9, "third": 0, "fifth": 4, "seventh": None}}]
    a_minor = {9, 11, 0, 2, 4, 5, 7}
    assert LowendRootMap(
        harmonic_windows=windows, melody=[], roots_pc=[9], scale_pcs=a_minor
    ).harmony_pcs == frozenset()

    data = {
        "tracks": {
            "808_bass": [{"time_step": 0.0, "note": "C#1", "length": 1.0, "velocity": 100}],
            "melody_lead": [{"time_step": 0.0, "note": "C4", "length": 4.0, "velocity": 90}],
        },
        "plg_midi_structure": {"harmonic_rhythm": windows},
        "plg_progression": {"scale": "natural_minor", "roots_pc": [9]},
        "key": "A",
        "scale": "natural_minor",
    }
    stats = _correct_808_vertical_clashes(data)
    assert stats["repaired"] == 1
    assert stats["unrepairable"] == 0
    assert not _clash_intervals(parse_note_name(data["tracks"]["808_bass"][0]["note"]) % 12, 0)


def test_narrowing_the_pool_does_not_change_when_the_repair_fires():
    """WHAT it chooses moved; WHEN it fires did not. A consonant 808 stays
    untouched and the clash counters keep their old meaning."""
    data = _two_window_pattern(
        {"time_step": 0.0, "note": "A1", "length": 1.0, "velocity": 100},
        [{"time_step": 0.0, "note": "E4", "length": 4.0, "velocity": 90}],
    )
    stats = _correct_808_vertical_clashes(data)
    assert stats["checked"] == 1
    assert stats["clashes_found"] == 0
    assert stats["root_overruled"] == 0
    assert data["tracks"]["808_bass"][0]["note"] == "A1"

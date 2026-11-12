"""add_structural_accent_notes — the only melodic enrichment layered on top
of a user's uploaded MIDI besides humanize timing/velocity. One note at the
FIRST hook landing and the FIRST drop landing, never touching an existing
note, pitched on the same root _apply_808_bass_lane already uses."""

from __future__ import annotations

import dataclasses

from beat_humanize import add_structural_accent_notes
from genre_profiles import PROFILES
from pattern_utils import parse_note_name


def _note(t: float, note: str, length: float = 0.4, velocity: int = 95) -> dict:
    return {"time_step": t, "note": note, "length": length, "velocity": velocity}


def _arrangement() -> list[dict]:
    return [
        {"name": "intro", "start_bar": 0, "bars": 4},
        {"name": "verse", "start_bar": 4, "bars": 8},
        {"name": "hook", "start_bar": 12, "bars": 8},
        {"name": "verse", "start_bar": 20, "bars": 8},
        {"name": "drop", "start_bar": 28, "bars": 8},
        {"name": "outro", "start_bar": 36, "bars": 4},
    ]


def _base_pattern(melody: list[dict], *, key: str = "E", scale: str = "minor") -> dict:
    return {
        "bpm": 142,
        "key": key,
        "scale": scale,
        "plg_arrangement": _arrangement(),
        "tracks": {"melody_lead": melody},
    }


def test_adds_one_note_at_hook_and_one_at_drop_when_clear():
    # Off-grid melody (0.3 past each downbeat) so it never collides with a
    # landing at bar*4.0.
    melody = [_note(b * 4.0 + 0.3, "E4") for b in range(40)]
    pattern = _base_pattern(melody)
    placed = add_structural_accent_notes(pattern, PROFILES["trap"])
    assert placed == 2
    accents = sorted(
        (n for n in pattern["tracks"]["melody_lead"] if n.get("structural_accent")),
        key=lambda n: n["time_step"],
    )
    assert [round(a["time_step"], 2) for a in accents] == [48.0, 112.0]  # bar 12, bar 28


def test_skips_a_landing_that_already_has_a_note():
    melody = [_note(b * 4.0, "E4") for b in range(40)]  # exactly on every downbeat
    pattern = _base_pattern(melody)
    placed = add_structural_accent_notes(pattern, PROFILES["trap"])
    assert placed == 0
    assert not any(n.get("structural_accent") for n in pattern["tracks"]["melody_lead"])


def test_never_modifies_existing_notes():
    melody = [_note(b * 4.0 + 0.3, "E4") for b in range(40)]
    original = [dict(n) for n in melody]
    pattern = _base_pattern(melody)
    add_structural_accent_notes(pattern, PROFILES["trap"])
    survivors = [n for n in pattern["tracks"]["melody_lead"] if not n.get("structural_accent")]
    assert sorted(survivors, key=lambda n: n["time_step"]) == sorted(original, key=lambda n: n["time_step"])


def test_pitch_matches_the_808_bass_root_snapped_to_local_register():
    melody = [_note(b * 4.0 + 0.3, "G5") for b in range(40)]
    pattern = _base_pattern(melody, key="E", scale="minor")
    add_structural_accent_notes(pattern, PROFILES["trap"])
    accents = [n for n in pattern["tracks"]["melody_lead"] if n.get("structural_accent")]
    assert accents
    root_pc = parse_note_name("E4") % 12  # E natural minor root = E
    for a in accents:
        assert parse_note_name(a["note"]) % 12 == root_pc
        # snapped near the G5 register the melody was sitting in, not some
        # arbitrary octave (within a fifth of the prior note).
        assert abs(parse_note_name(a["note"]) - parse_note_name("G5")) <= 7


def test_only_first_occurrence_of_hook_and_drop_gets_an_accent():
    arrangement = [
        {"name": "hook", "start_bar": 8, "bars": 8},
        {"name": "hook", "start_bar": 24, "bars": 8},  # repeat — must NOT also accent
        {"name": "drop", "start_bar": 40, "bars": 8},
    ]
    melody = [_note(b * 4.0 + 0.3, "E4") for b in range(56)]
    pattern = {"bpm": 142, "key": "E", "scale": "minor", "plg_arrangement": arrangement,
               "tracks": {"melody_lead": melody}}
    placed = add_structural_accent_notes(pattern, PROFILES["trap"])
    assert placed == 2  # one hook (first only) + one drop


def test_noop_without_arrangement():
    melody = [_note(b * 4.0 + 0.3, "E4") for b in range(20)]
    pattern = {"bpm": 142, "tracks": {"melody_lead": melody}}
    assert add_structural_accent_notes(pattern, PROFILES["trap"]) == 0


def test_noop_without_melody():
    pattern = {"bpm": 142, "plg_arrangement": _arrangement(), "tracks": {}}
    assert add_structural_accent_notes(pattern, PROFILES["trap"]) == 0


def test_noop_when_landing_is_bar_zero():
    arrangement = [{"name": "hook", "start_bar": 0, "bars": 8}]
    melody = [_note(b * 4.0 + 0.3, "E4") for b in range(20)]
    pattern = {"bpm": 142, "key": "E", "scale": "minor", "plg_arrangement": arrangement,
               "tracks": {"melody_lead": melody}}
    assert add_structural_accent_notes(pattern, PROFILES["trap"]) == 0


def test_wired_into_full_pipeline_only_under_midi_melody():
    from arranger import arrange_song
    from beat_humanize import humanize_pattern

    melody = [{"time_step": round(0.3 + i * 0.5, 3), "note": "E4", "length": 0.4, "velocity": 95} for i in range(4)]

    midi_pattern = {
        "bpm": 142, "key": "E", "scale": "minor", "style": "trap", "user_prompt": "trap",
        "plg_midi_melody": True, "plg_total_bars": 40,
        "plg_variety": {"seed": 1, "key": "E", "scale": "minor", "genre": "trap", "bpm": 142},
        "tracks": {"melody_lead": [dict(n) for n in melody]},
    }
    arrange_song(midi_pattern)
    out = humanize_pattern(midi_pattern, profile=PROFILES["trap"])
    assert any(n.get("structural_accent") for n in out["tracks"]["melody_lead"])

    non_midi_pattern = {
        "bpm": 142, "key": "E", "scale": "minor", "style": "trap", "user_prompt": "trap",
        "plg_total_bars": 40,
        "plg_variety": {"seed": 1, "key": "E", "scale": "minor", "genre": "trap", "bpm": 142},
        "tracks": {"melody_lead": [dict(n) for n in melody]},
    }
    arrange_song(non_midi_pattern)
    out2 = humanize_pattern(non_midi_pattern, profile=PROFILES["trap"])
    assert not any(n.get("structural_accent") for n in out2["tracks"]["melody_lead"])

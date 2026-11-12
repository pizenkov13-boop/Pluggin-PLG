"""The chords / counter_melody lanes of a MIDI beat: only ever the user's own notes."""

import copy
import math
import struct
import wave
from pathlib import Path

import pytest

import midi_roles
from flp_template import active_export_channels, active_pattern_keys
from midi_validate import (
    MIDI_DISABLED_LANES,
    MIDI_ROLE_LANES,
    enforce_midi_mode,
    sanitize_midi_mode,
    validate_midi_mode,
)

CORE = ["melody_lead", "kick", "808_bass", "hi_hats", "clap", "snare", "snare_layer"]


@pytest.fixture(autouse=True)
def _roles_on(monkeypatch):
    monkeypatch.delenv("PLG_MIDI_ROLES", raising=False)
    # The lanes are opt-in (midi_roles.lanes_enabled); these tests are about them.
    monkeypatch.setenv("PLG_MIDI_ROLE_LANES", "on")


def test_the_role_lanes_are_off_by_default_and_the_whole_melody_stays_in_melody_lead(tmp_path, monkeypatch):
    monkeypatch.delenv("PLG_MIDI_ROLE_LANES", raising=False)
    pattern = _beat(tmp_path)
    before = copy.deepcopy(pattern["tracks"]["melody_lead"])

    assert midi_roles.lanes_enabled() is False
    assert midi_roles.split_into_lanes(pattern) == {}
    enforce_midi_mode(pattern)

    assert set(pattern["tracks"]) == set(CORE)
    assert midi_roles.LANES_KEY not in pattern
    assert pattern["tracks"]["melody_lead"] == before
    assert active_export_channels(pattern) == CORE


@pytest.mark.parametrize("raw,expected", [("on", True), ("1", True), ("true", True), ("off", False), ("", False)])
def test_the_lanes_switch_reads_its_own_variable(monkeypatch, raw, expected):
    monkeypatch.setenv("PLG_MIDI_ROLE_LANES", raw)
    assert midi_roles.lanes_enabled() is expected


def _wav(path: Path, *, seconds: float, hz: float) -> Path:
    rate = 8000
    payload = b"".join(
        struct.pack("<h", int(9000 * math.sin(2 * math.pi * hz * i / rate)))
        for i in range(max(1, int(rate * seconds)))
    )
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(payload)
    return path


def _n(beat, name, length):
    return {"time_step": float(beat), "note": name, "length": float(length), "velocity": 90, "midi": True}


def _loop(bars=8):
    notes = []
    for bar in range(bars):
        base = bar * 4.0
        notes += [_n(base, "A#3", 4.0), _n(base, "A#4", 2.0), _n(base, "F5", 4.0)]
        notes += [_n(base + 1, "F4", 2.0), _n(base + 2, "C#5", 2.0), _n(base + 3, "C5", 2.0)]
    return notes


def _beat(tmp_path, *, tag=True):
    melody = _loop()
    if tag:
        midi_roles.tag_roles(melody)
    kick = _wav(tmp_path / "REAL Kick.wav", seconds=0.12, hz=90)
    bass = _wav(tmp_path / "REAL 808 Bass.wav", seconds=0.8, hz=55)
    hat = _wav(tmp_path / "Closed Hat.wav", seconds=0.08, hz=3000)
    note = {"note": "C4", "time_step": 0.0, "length": 0.25, "velocity": 100}
    return {
        "bpm": 140,
        "plg_midi_melody": True,
        "plg_total_bars": 40,
        "plg_arrangement": [
            {"name": "intro", "start_bar": 0, "bars": 4},
            {"name": "verse", "start_bar": 4, "bars": 8},
            {"name": "hook", "start_bar": 12, "bars": 8},
            {"name": "verse", "start_bar": 20, "bars": 8},
            {"name": "drop", "start_bar": 28, "bars": 8},
            {"name": "outro", "start_bar": 36, "bars": 4},
        ],
        "tracks": {
            "melody_lead": melody,
            "kick": [{**note, "time_step": 16.0}],
            "808_bass": [{**note, "time_step": 16.0, "note": "C2"}],
            "hi_hats": [{**note, "time_step": 16.5}],
            "clap": [{**note, "time_step": 17.0}],
            "snare": [{**note, "time_step": 17.0}],
            "snare_layer": [{**note, "time_step": 17.0}],
        },
        "plg_sound_paths": {"kick": str(kick), "808_bass": str(bass), "hi_hats": str(hat)},
        "plg_sample_picks": {"kick": kick.name, "808_bass": bass.name, "hi_hats": hat.name},
    }


def test_the_disabled_lane_list_is_untouched_and_the_role_lanes_are_a_separate_constant():
    assert MIDI_DISABLED_LANES == ("sub_808", "counter_melody", "fx", "chords", "texture", "cowbell")
    assert MIDI_ROLE_LANES == ("chords", "counter_melody")


def test_the_split_moves_chord_and_bass_notes_to_chords_and_keeps_the_lead(tmp_path):
    pattern = _beat(tmp_path)
    total = len(pattern["tracks"]["melody_lead"])

    counts = midi_roles.split_into_lanes(pattern)

    tracks = pattern["tracks"]
    assert counts == {"chords": 8 * 3}
    assert {n["plg_role"] for n in tracks["chords"]} == {"bass", "chords"}
    assert {n["plg_role"] for n in tracks["melody_lead"]} == {"lead"}
    assert len(tracks["melody_lead"]) + len(tracks["chords"]) == total
    assert pattern[midi_roles.LANES_KEY] == counts
    assert "counter_melody" not in tracks
    starts = [n["time_step"] for n in tracks["chords"]]
    assert starts == sorted(starts)


def test_with_roles_off_nothing_is_split_and_no_lane_appears(tmp_path, monkeypatch):
    monkeypatch.setenv("PLG_MIDI_ROLES", "off")
    pattern = _beat(tmp_path, tag=False)
    before = copy.deepcopy(pattern)

    assert midi_roles.split_into_lanes(pattern) == {}
    enforce_midi_mode(pattern)

    assert {lane for lane in pattern["tracks"]} == set(CORE)
    assert midi_roles.LANES_KEY not in pattern
    assert pattern["tracks"]["melody_lead"] == before["tracks"]["melody_lead"]
    assert active_export_channels(pattern) == CORE


def test_a_split_that_would_leave_no_lead_does_not_happen(tmp_path):
    pattern = _beat(tmp_path)
    pattern["tracks"]["melody_lead"] = [n for n in pattern["tracks"]["melody_lead"] if n["plg_role"] != "lead"]

    assert midi_roles.split_into_lanes(pattern) == {}
    assert "chords" not in pattern["tracks"]


def test_a_plain_midi_with_nothing_to_split_stays_a_single_lane(tmp_path):
    pattern = _beat(tmp_path, tag=False)
    pattern["tracks"]["melody_lead"] = [_n(i * 0.5, "E4", 0.5) for i in range(16)]
    midi_roles.tag_roles(pattern["tracks"]["melody_lead"])

    assert midi_roles.split_into_lanes(pattern) == {}
    enforce_midi_mode(pattern)
    assert active_export_channels(pattern) == CORE


def test_split_lanes_survive_the_contract_and_reach_the_export_channels(tmp_path):
    pattern = _beat(tmp_path)
    midi_roles.split_into_lanes(pattern)

    report = enforce_midi_mode(pattern)

    assert report["ok"] is True
    assert validate_midi_mode(pattern) == []
    expected = CORE + ["chords"]
    assert active_export_channels(pattern) == expected
    assert active_pattern_keys(pattern) == expected
    assert sorted({c["playlist_track"] for c in pattern["plg_playlist_clips"]}) == list(range(len(expected)))
    assert {c["track_key"] for c in pattern["plg_playlist_clips"]} == set(expected)


def test_a_counter_voice_gets_its_own_lane_and_channel(tmp_path):
    pattern = _beat(tmp_path)
    pattern["tracks"]["melody_lead"] += [
        {**_n(b + 0.0, "A4", 0.5), "plg_role": "counter"} for b in range(0, 32, 4)
    ]
    pattern["tracks"]["melody_lead"].sort(key=lambda n: n["time_step"])

    counts = midi_roles.split_into_lanes(pattern)
    enforce_midi_mode(pattern)

    assert counts["counter_melody"] == 8
    assert active_export_channels(pattern) == CORE + ["counter_melody", "chords"]


def test_a_generated_lane_is_still_stripped_even_if_its_notes_are_copies_of_tagged_melody(tmp_path):
    """A generator that copies the melody copies the tags too. Without the split's
    own record the lane is not a role lane."""
    pattern = _beat(tmp_path)
    pattern["tracks"]["counter_melody"] = copy.deepcopy(pattern["tracks"]["melody_lead"][:12])
    pattern["tracks"]["chords"] = copy.deepcopy(pattern["tracks"]["melody_lead"][:12])

    sanitize_midi_mode(pattern)

    assert "counter_melody" not in pattern["tracks"] and "chords" not in pattern["tracks"]


def test_a_recorded_lane_holding_an_untagged_note_is_stripped(tmp_path):
    pattern = _beat(tmp_path)
    midi_roles.split_into_lanes(pattern)
    pattern["tracks"]["chords"].append(_n(1.0, "C3", 1.0))  # no tag: not the user's note

    sanitize_midi_mode(pattern)

    assert "chords" not in pattern["tracks"]


def test_only_the_lane_the_split_filled_is_kept(tmp_path):
    pattern = _beat(tmp_path)
    midi_roles.split_into_lanes(pattern)
    pattern["tracks"]["counter_melody"] = copy.deepcopy(pattern["tracks"]["chords"])  # not recorded

    sanitize_midi_mode(pattern)

    assert "chords" in pattern["tracks"] and "counter_melody" not in pattern["tracks"]


def test_the_other_ear_candy_lanes_never_come_back(tmp_path):
    pattern = _beat(tmp_path)
    midi_roles.split_into_lanes(pattern)
    for lane in ("fx", "texture", "cowbell", "sub_808"):
        pattern["tracks"][lane] = copy.deepcopy(pattern["tracks"]["chords"])

    sanitize_midi_mode(pattern)

    assert not {"fx", "texture", "cowbell", "sub_808"} & set(pattern["tracks"])


def test_sound_and_mixer_entries_of_a_live_role_lane_are_kept_and_a_dead_one_dropped(tmp_path):
    pattern = _beat(tmp_path)
    midi_roles.split_into_lanes(pattern)
    pad = str(_wav(tmp_path / "pad.wav", seconds=0.5, hz=220))
    pattern["plg_sound_paths"].update({"chords": pad, "counter_melody": pad})
    pattern["plg_sample_picks"].update({"chords": "pad.wav", "counter_melody": "pad.wav"})

    sanitize_midi_mode(pattern)

    assert pattern["plg_sound_paths"]["chords"] == pad
    assert "counter_melody" not in pattern["plg_sound_paths"]
    assert "counter_melody" not in pattern["plg_sample_picks"]


def test_the_chords_channel_is_styled_like_the_other_melodic_samplers():
    from flp_writer import _MELODY_SAMPLER_KEYS

    assert {"melody_lead", "counter_melody", "chords"} <= _MELODY_SAMPLER_KEYS


def _kit_pattern():
    melody = _loop(bars=4)
    midi_roles.tag_roles(melody)
    return {
        "bpm": 140,
        "style": "trap",
        "plg_midi_melody": True,
        "tracks": {"kick": [_n(0.0, "C1", 0.3)], "melody_lead": melody},
    }


def test_the_sound_attach_keeps_a_live_role_lane_and_gives_it_a_sample():
    from starter_kit import attach_sounds_to_pattern, ensure_starter_kit

    ensure_starter_kit()
    pattern = _kit_pattern()
    midi_roles.split_into_lanes(pattern)

    sound_map = attach_sounds_to_pattern(pattern, None, prompt="dark trap beat")

    assert "chords" in pattern["tracks"] and sound_map.get("chords") is not None
    assert pattern["plg_sound_paths"]["chords"] == str(sound_map["chords"])


def test_the_sound_attach_still_drops_a_generated_chords_lane():
    from starter_kit import attach_sounds_to_pattern, ensure_starter_kit

    ensure_starter_kit()
    pattern = _kit_pattern()
    pattern["tracks"]["chords"] = [_n(0.0, "C3", 4.0)]  # no split recorded: generated

    sound_map = attach_sounds_to_pattern(pattern, None, prompt="dark trap beat")

    assert "chords" not in pattern["tracks"] and "chords" not in sound_map


def test_the_role_lanes_play_in_the_lead_timbre():
    """Same source file as the lead (each lane keeps its own baked copy), not a library pick of its own."""
    from starter_kit import attach_sounds_to_pattern, ensure_starter_kit

    ensure_starter_kit()
    pattern = _kit_pattern()
    pattern["tracks"]["melody_lead"] += [{**_n(b, "A4", 0.5), "plg_role": "counter"} for b in range(0, 16, 2)]
    pattern["tracks"]["melody_lead"].sort(key=lambda n: n["time_step"])
    assert set(midi_roles.split_into_lanes(pattern)) == {"chords", "counter_melody"}

    sound_map = attach_sounds_to_pattern(pattern, None, prompt="dark trap beat")

    source = Path(sound_map["melody_lead"]).name.rsplit("hp0_", 1)[-1]
    assert source and Path(sound_map["chords"]).name.endswith(source)
    assert Path(sound_map["counter_melody"]).name.endswith(source)


@pytest.fixture(autouse=True)
def _melody_sample_on(monkeypatch):
    """This file tests the melody sample machinery: PLG_MELODY_SAMPLE is off by default (sound_policy), and a
    library without a melody file gets the starter melody only with PLG_SUBSTITUTE_SOUNDS on."""
    monkeypatch.setenv("PLG_MELODY_SAMPLE", "on")
    monkeypatch.setenv("PLG_SUBSTITUTE_SOUNDS", "on")

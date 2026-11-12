"""Variant B (2026-10, ``sound_policy``): a drum lane the user has no usable file for is NOT in the .flp.

No starter sound, no synthetic 808 / hat. Inside the generation the lane and its notes live on (the audits
read them); the choice is recorded (``plg_sample_fallback[lane]`` with ``tier`` 0, ``sample`` None) so
``midi_validate`` can tell "deliberately absent" from "a stale JSON that forgot the sample", and
``flp_template.midi_hidden_keys`` leaves exactly the recorded lanes out of the channel rack, the patterns
and the playlist. The snare layer follows the snare. The melody keeps its notes and has no sample by design.
The old behaviour is ``PLG_SUBSTITUTE_SOUNDS=on`` (``test_lane_fallback.py``).
"""

from __future__ import annotations

import math
import struct
import wave

import pytest

from lane_fallback import ensure_required_lane_samples, lane_is_absent, resolve_lane_sample
from library_catalog import scan_library
from midi_validate import validate_midi_mode
from starter_kit import attach_sounds_to_pattern, resolve_track_samples

RATE = 44100


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.delenv("PLG_SUBSTITUTE_SOUNDS", raising=False)
    monkeypatch.delenv("PLG_MELODY_SAMPLE", raising=False)
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")  # no baked copies written into assets/starter


def _write(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, int(s)))) for s in samples))


def _sine(freq, seconds, *, tau=1.2, amp=24000):
    return [
        amp * min(1.0, i / 220.0) * math.exp(-i / RATE / tau) * math.sin(2 * math.pi * freq * i / RATE)
        for i in range(int(RATE * seconds))
    ]


def _pattern():
    return {
        "bpm": 140,
        "style": "trap",
        "plg_midi_melody": True,
        "plg_variety": {"genre": "trap"},
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 110}],
            "snare": [{"time_step": 4.0, "note": "D1", "length": 0.25, "velocity": 100}],
            "clap": [{"time_step": 4.0, "note": "D#1", "length": 0.25, "velocity": 90}],
            "hi_hats": [{"time_step": 0.0, "note": "F#1", "length": 0.1, "velocity": 90}],
            "808_bass": [{"time_step": 0.0, "note": "C2", "length": 2.0, "velocity": 120}],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 100}],
        },
    }


# --- the ladder ------------------------------------------------------------------


def test_an_overdriven_808_is_not_replaced_the_lane_is_empty(tmp_path):
    lib = tmp_path / "lib"
    _write(lib / "808 harsh.wav", _sine(4000, 1.5))  # a hard failure: pre-destroyed
    catalog = scan_library(lib, quarantine=False)
    pattern = _pattern()

    assert resolve_lane_sample("808_bass", pattern, {}, catalog, lib) is None

    record = pattern["plg_sample_fallback"]["808_bass"]
    assert record["tier"] == 0 and record["sample"] is None and record["user_files"] == 1
    assert lane_is_absent(pattern, "808_bass")
    assert pattern["tracks"]["808_bass"]  # the notes stay


def test_an_empty_library_leaves_the_three_required_lanes_empty_and_the_beat_valid(tmp_path):
    pattern = _pattern()
    sound_map: dict = {}
    ensure_required_lane_samples(pattern, sound_map, None, tmp_path)

    assert sound_map == {}
    assert all(lane_is_absent(pattern, lane) for lane in ("kick", "808_bass", "hi_hats"))
    pattern["plg_sound_paths"] = {}
    assert [e for e in validate_midi_mode(pattern) if "sample" in e] == []


def test_a_record_is_what_makes_an_empty_lane_valid_not_a_forgotten_sample():
    pattern = _pattern()
    pattern["plg_sound_paths"] = {}
    errors = validate_midi_mode(pattern)
    assert "MIDI mode: no sample assigned to 808_bass" in errors

    pattern["plg_sample_fallback"] = {"808_bass": {"tier": 0, "sample": None, "reasons": [], "user_files": 0}}
    assert "MIDI mode: no sample assigned to 808_bass" not in validate_midi_mode(pattern)


def test_a_soft_failure_still_uses_the_users_own_file(tmp_path):
    lib = tmp_path / "lib"
    _write(lib / "808 bright.wav", _sine(700, 1.4, tau=0.8))  # only "too bright": a SOFT reason
    catalog = scan_library(lib, quarantine=False)
    pattern = _pattern()

    path = resolve_lane_sample("808_bass", pattern, {}, catalog, lib)

    assert path is not None and path.name == "808 bright.wav"
    assert pattern["plg_sample_fallback"]["808_bass"]["tier"] == 1
    assert not lane_is_absent(pattern, "808_bass")


# --- the kit --------------------------------------------------------------------


def test_a_kit_without_snares_leaves_snare_clap_and_layer_empty_and_never_hands_over_a_kick(tmp_path):
    lib = tmp_path / "lib"
    names = ["OFF THE METER Kick.wav", "BLAKK ROCKSTAR P2 Kick.wav", "GHOUL 808.wav", "THX 808.wav"]
    for name in names:
        _write(lib / "kits" / name, _sine(60, 0.2, tau=0.05) if "Kick" in name else _sine(41.2034, 2.0))
    catalog = scan_library(lib, quarantine=False)

    chosen = resolve_track_samples(catalog, library_root=lib)

    for lane in ("snare", "clap", "snare_layer"):
        assert lane not in chosen, f"{lane} got {chosen.get(lane)}"


def test_an_unlabelled_low_thump_is_not_a_snare(tmp_path):
    lib = tmp_path / "lib"
    _write(lib / "kits" / "Thing 01.wav", _sine(80, 0.2, tau=0.05))
    catalog = scan_library(lib, quarantine=False)
    assert "snare" not in resolve_track_samples(catalog, library_root=lib)


def test_with_no_library_at_all_nothing_of_ours_is_put_in_the_project(tmp_path):
    pattern = _pattern()
    sound_map = attach_sounds_to_pattern(pattern, None, library_root=tmp_path / "empty", prompt="dark trap beat")

    assert sound_map == {}
    assert pattern["plg_sound_paths"] == {} and pattern["samples"] == []
    assert not any("starter" in step.lower() for step in pattern["manual_steps"])
    assert set(pattern["tracks"]) >= {"kick", "snare", "clap", "hi_hats", "808_bass", "melody_lead"}  # notes kept
    assert {row["origin"] for row in pattern["plg_sample_origins"].values()} == {"absent"}
    assert all(lane_is_absent(pattern, lane) for lane in ("kick", "snare", "clap", "hi_hats", "808_bass"))


def test_with_substitutes_on_the_same_empty_library_gets_the_old_starter_sounds(tmp_path, monkeypatch):
    monkeypatch.setenv("PLG_SUBSTITUTE_SOUNDS", "on")
    pattern = _pattern()
    sound_map = attach_sounds_to_pattern(pattern, None, library_root=tmp_path / "empty", prompt="dark trap beat")

    assert {"kick", "snare", "clap", "hi_hats", "808_bass"} <= set(sound_map)
    assert not any(lane_is_absent(pattern, lane) for lane in ("kick", "snare", "clap", "808_bass", "hi_hats"))


# --- the .flp: an absent lane has no channel at all ------------------------------------


def _absent(lane: str, reasons=()) -> dict:
    return {"tier": 0, "sample": None, "reasons": list(reasons), "user_files": 0}


def test_the_layer_follows_the_snare_and_the_clap_is_recorded_on_its_own():
    from lane_fallback import mark_absent_backbeat_lanes

    pattern = _pattern()
    pattern["tracks"]["snare_layer"] = [dict(pattern["tracks"]["snare"][0])]
    layer_file = "layer.wav"
    sound_map = {"snare_layer": layer_file}  # the matcher found the layer a file, the snare none
    mark_absent_backbeat_lanes(pattern, sound_map)

    assert sound_map == {}  # the layer went with the snare
    assert all(lane_is_absent(pattern, lane) for lane in ("snare", "clap", "snare_layer"))
    assert pattern["plg_sample_fallback"]["snare_layer"]["reasons"] == ["snare_absent"]


def test_a_snare_with_a_file_keeps_its_layer_and_nothing_is_recorded(tmp_path):
    from lane_fallback import mark_absent_backbeat_lanes

    pattern = _pattern()
    pattern["tracks"]["snare_layer"] = [dict(pattern["tracks"]["snare"][0])]
    sound_map = {"snare": tmp_path / "s.wav", "clap": tmp_path / "c.wav", "snare_layer": tmp_path / "s.wav"}
    mark_absent_backbeat_lanes(pattern, sound_map)

    assert set(sound_map) == {"snare", "clap", "snare_layer"}
    assert "plg_sample_fallback" not in pattern


def test_only_an_explicit_absent_record_hides_a_lane():
    from flp_template import active_export_channels, active_pattern_keys, midi_hidden_keys

    data = {"plg_midi_melody": True, "tracks": {k: v for k, v in _pattern()["tracks"].items()}}
    assert "snare" not in midi_hidden_keys(data)  # no record, no sample: a fixture or a stale JSON, kept
    assert "snare" in active_export_channels(data)

    data["plg_sample_fallback"] = {"snare": _absent("snare"), "808_bass": _absent("808_bass")}
    hidden = midi_hidden_keys(data)
    assert {"snare", "808_bass"} <= hidden and not hidden & {"kick", "clap", "hi_hats", "melody_lead"}
    assert "snare" not in active_export_channels(data) and "snare" not in active_pattern_keys(data)

    data["plg_midi_melody"] = False  # outside the MIDI route nothing is hidden
    assert midi_hidden_keys(data) == frozenset()


def _parse_flp(blob: bytes):
    import struct

    from flp_writer import _CH_NAME, _CH_NEW, _PAT_NEW, _PAT_NOTES, _PLAYLIST

    stream = memoryview(blob)[14 + 8 :]
    names, pattern_racks, items, cur = [], {}, [], None
    off = 0
    while off < len(stream):
        eid = stream[off]
        off += 1
        if eid < 64:
            off += 1
            continue
        if eid < 128:
            val = struct.unpack_from("<H", stream, off)[0]
            off += 2
            if eid == _PAT_NEW:
                cur = val
                pattern_racks[cur] = set()
            continue
        if eid < 192:
            off += 4
            continue
        length, shift = 0, 0
        while True:
            byte = stream[off]
            off += 1
            length |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        raw = bytes(stream[off : off + length])
        off += length
        if eid == _CH_NAME:
            names.append(raw.decode("utf-16-le").rstrip("\x00"))
        elif eid == _PAT_NOTES:
            pattern_racks[cur] |= {struct.unpack_from("<IHHI", raw, i * 24)[2] for i in range(len(raw) // 24)}
        elif eid == _PLAYLIST:
            items = [struct.unpack_from("<IHHIHH", raw, i * 60) for i in range(len(raw) // 60)]
    return names, pattern_racks, items


def test_the_flp_has_no_channel_pattern_or_playlist_row_for_an_absent_lane():
    from flp_template import PATTERN_DISPLAY
    from flp_writer import _PLAYLIST_MAX_TRACKS, build_flp

    def note(t=0.0, n="C3", ln=0.5):
        return {"time_step": t, "note": n, "length": ln, "velocity": 100}

    data = {
        "bpm": 140,
        "plg_midi_melody": True,
        "tracks": {
            "melody_lead": [note(0.0, "A4", 4.0)],
            "kick": [note(0.0)],
            "808_bass": [note(0.0, "C2", 2.0)],
            "hi_hats": [note(0.25)],
            "clap": [note(1.0)],
            "snare": [note(1.0)],
            "snare_layer": [note(1.0)],
        },
        "plg_sample_fallback": {
            "808_bass": _absent("808_bass"),
            "snare": _absent("snare"),
            "snare_layer": _absent("snare_layer", ["snare_absent"]),
        },
    }
    names, pattern_racks, items = _parse_flp(build_flp(data))

    assert names == [PATTERN_DISPLAY[k] for k in ("melody_lead", "kick", "hi_hats", "clap")]
    assert all(len(racks) == 1 and max(racks) < len(names) for racks in pattern_racks.values())
    assert {next(iter(r)) for r in pattern_racks.values()} == set(range(len(names)))  # every channel is used
    rows = sorted({_PLAYLIST_MAX_TRACKS - 1 - item[4] for item in items})
    assert rows == list(range(len(names)))
    # the notes of the hidden lanes are still in the data: the audits read them
    assert data["tracks"]["808_bass"] and data["tracks"]["snare"]


def test_with_no_sounds_at_all_the_flp_keeps_only_the_melody():
    from flp_writer import build_flp

    data = {
        "bpm": 140,
        "plg_midi_melody": True,
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 100}],
            "kick": [{"time_step": 0.0, "note": "C3", "length": 0.5, "velocity": 100}],
            "hi_hats": [{"time_step": 0.25, "note": "C3", "length": 0.25, "velocity": 100}],
            "clap": [{"time_step": 1.0, "note": "C3", "length": 0.25, "velocity": 100}],
        },
        "plg_sample_fallback": {lane: _absent(lane) for lane in ("kick", "hi_hats", "clap")},
    }
    names, pattern_racks, _items = _parse_flp(build_flp(data))
    assert names == ["PLG MIDI"]
    assert all(racks == {0} for racks in pattern_racks.values())

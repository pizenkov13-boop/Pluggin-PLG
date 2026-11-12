"""A sound the user chose by hand in the sound browser is theirs: the gates yield (sound browser, step 2).

Random pins stay a preference (a bad 808 is still swapped); a pin marked ``explicit`` is kept
whatever ``sample_match`` says, the export record agrees with it, and ``_assert_exportable_808``
does not stop a beat for it.
"""

from __future__ import annotations

import math
import struct
import wave

import pytest

import pinned_kit
from lane_fallback import (
    ensure_required_lane_samples,
    explicit_lanes,
    lane_is_explicit,
    mark_absent_backbeat_lanes,
)
from library_catalog import scan_library
from midi_validate import validate_midi_mode
from sample_match import rejection_reason_for_pattern, track_candidate_rejection_reasons
from starter_kit import attach_sounds_to_pattern

RATE = 44100


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.delenv("PLG_SUBSTITUTE_SOUNDS", raising=False)
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")  # no baked copies: file names stay readable
    monkeypatch.setattr(pinned_kit, "pin_file", lambda: tmp_path / "state" / "pinned_kit.json")


def _write(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, int(s)))) for s in samples))
    return path


def _sine(freq, seconds, *, tau=1.2, amp=24000):
    return [
        amp * min(1.0, i / 220.0) * math.exp(-i / RATE / tau) * math.sin(2 * math.pi * freq * i / RATE)
        for i in range(int(RATE * seconds))
    ]


def _harsh_808(root):
    return _write(root / "808" / "808 harsh.wav", _sine(4000, 1.5))  # "overdriven_808_bass": a HARD reason


def _short_808(root):
    return _write(root / "808" / "808 short.wav", _sine(55, 0.3, tau=0.1))


def _pattern():
    return {
        "bpm": 140,
        "style": "trap",
        "plg_midi_melody": True,
        "plg_variety": {"genre": "trap"},
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 110}],
            "808_bass": [{"time_step": 0.0, "note": "C2", "length": 2.75, "velocity": 120}],
            "hi_hats": [{"time_step": 0.25, "note": "F#1", "length": 0.1, "velocity": 90}],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 100}],
        },
    }


# --- lane_fallback: the final arbiter ---------------------------------------------------


def test_a_hand_picked_overdriven_808_is_kept_and_the_record_lets_export_agree(tmp_path):
    root = tmp_path / "lib"
    harsh = _harsh_808(root)
    assert "overdriven_808_bass" in track_candidate_rejection_reasons(harsh, "808_bass")
    pattern = _pattern()
    pattern["plg_explicit_lanes"] = ["808_bass"]
    sound_map = {"808_bass": harsh}

    ensure_required_lane_samples(pattern, sound_map, None, root)

    assert sound_map["808_bass"] == harsh
    record = pattern["plg_sample_fallback"]["808_bass"]
    assert record["tier"] == 1 and record["explicit"] is True and "overdriven_808_bass" in record["reasons"]
    assert lane_is_explicit(pattern, "808_bass")
    assert rejection_reason_for_pattern(harsh, "808_bass", pattern) is None


def test_the_same_file_without_the_mark_is_still_refused(tmp_path):
    root = tmp_path / "lib"
    harsh = _harsh_808(root)
    pattern = _pattern()
    sound_map = {"808_bass": harsh}

    ensure_required_lane_samples(pattern, sound_map, None, root)

    assert "808_bass" not in sound_map  # absent: no synthetic stand-in, no overdriven file
    assert pattern["plg_sample_fallback"]["808_bass"]["tier"] == 0
    assert not lane_is_explicit(pattern, "808_bass")


def test_a_hand_picked_kick_in_the_hat_cell_is_kept(tmp_path):
    root = tmp_path / "lib"
    thump = _write(root / "kits" / "kick_9.wav", _sine(60, 0.2, tau=0.05))
    pattern = _pattern()
    pattern["plg_explicit_lanes"] = ["hi_hats"]
    sound_map = {"hi_hats": thump}

    ensure_required_lane_samples(pattern, sound_map, None, root)

    assert sound_map["hi_hats"] == thump and lane_is_explicit(pattern, "hi_hats")
    assert rejection_reason_for_pattern(thump, "hi_hats", pattern) is None


def test_a_short_hand_picked_808_gets_its_notes_shortened_not_replaced(tmp_path):
    root = tmp_path / "lib"
    short = _short_808(root)
    pattern = _pattern()
    pattern["plg_explicit_lanes"] = ["808_bass"]
    before = pattern["tracks"]["808_bass"][0]["length"]

    ensure_required_lane_samples(pattern, {"808_bass": short}, None, root)

    assert lane_is_explicit(pattern, "808_bass")
    assert pattern["tracks"]["808_bass"][0]["length"] <= before


def test_the_layer_picked_by_hand_stays_when_there_is_no_snare(tmp_path):
    layer = tmp_path / "rim.wav"
    pattern = _pattern()
    pattern["tracks"]["snare_layer"] = [{"time_step": 1.0, "note": "C3", "length": 0.25, "velocity": 90}]
    pattern["tracks"]["snare"] = [dict(pattern["tracks"]["snare_layer"][0])]
    pattern["plg_explicit_lanes"] = ["snare_layer"]
    sound_map = {"snare_layer": layer}

    mark_absent_backbeat_lanes(pattern, sound_map)

    assert sound_map == {"snare_layer": layer}  # the automatic layer would have gone with the snare
    assert explicit_lanes(pattern) == {"snare_layer"}


# --- the export ----------------------------------------------------------------------


def _export_data(path, *, explicit):
    data = _pattern()
    data["plg_sound_paths"] = {"808_bass": str(path)}
    if explicit:
        data["plg_sample_fallback"] = {
            "808_bass": {
                "tier": 1, "sample": str(path), "explicit": True, "user_files": 0,
                "reasons": list(track_candidate_rejection_reasons(path, "808_bass")),
            }
        }
    return data


def test_the_808_export_assert_lets_a_hand_picked_808_through(tmp_path):
    import flp_writer

    short = _short_808(tmp_path / "lib")
    harsh = _harsh_808(tmp_path / "lib")
    for path in (short, harsh):
        with pytest.raises(ValueError):
            flp_writer._assert_exportable_808(_export_data(path, explicit=False))
        flp_writer._assert_exportable_808(_export_data(path, explicit=True))  # no exception


def test_midi_validate_accepts_hand_picked_lanes_that_fail_the_gates(tmp_path):
    root = tmp_path / "lib"
    harsh = _harsh_808(root)
    thump = _write(root / "kits" / "kick_9.wav", _sine(60, 0.2, tau=0.05))
    pattern = _pattern()
    pattern["plg_explicit_lanes"] = ["808_bass", "hi_hats"]
    sound_map = {"808_bass": harsh, "hi_hats": thump, "kick": thump}
    ensure_required_lane_samples(pattern, sound_map, None, root)
    pattern["plg_sound_paths"] = {lane: str(path) for lane, path in sound_map.items()}

    assert [e for e in validate_midi_mode(pattern) if "sample" in e] == []


# --- through attach_sounds_to_pattern ------------------------------------------------------


def _library(tmp_path):
    root = tmp_path / "PLG_Sounds"
    _write(root / "kits" / "kick_1.wav", _sine(60, 0.2, tau=0.05))
    _write(root / "kits" / "snare_1.wav", [0.0] * 100 + _sine(3200, 0.2, tau=0.05))
    _harsh_808(root)
    return root, scan_library(root, quarantine=False)


def test_a_hand_picked_808_survives_the_whole_attach_and_a_random_one_does_not(tmp_path):
    root, catalog = _library(tmp_path)
    harsh = root / "808" / "808 harsh.wav"

    pinned_kit.save_pins({"808_bass": str(harsh)})  # Random-style pin: a preference
    soft = _pattern()
    attach_sounds_to_pattern(soft, catalog, library_root=root, prompt="")
    assert "808_bass" not in soft["plg_sound_paths"]  # the gates judged it

    assert pinned_kit.pin_by_hand("808_bass", harsh, root) is None  # the same file, chosen by hand
    hand = _pattern()
    attach_sounds_to_pattern(hand, catalog, library_root=root, prompt="")

    assert hand["plg_sound_paths"]["808_bass"].endswith("808 harsh.wav")
    assert hand["plg_explicit_lanes"] == ["808_bass"] and lane_is_explicit(hand, "808_bass")
    assert "808_bass" not in (hand.get("plg_pinned_kit") or {}).get("replaced", [])
    assert [e for e in validate_midi_mode(hand) if "sample" in e] == []
    assert hand["plg_sample_origins"]["808_bass"]["origin"] == "user"  # not "user_soft": the user chose it

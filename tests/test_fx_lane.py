"""PLG FX lane: crash one-shots on arrangement landings, byte-verified in the FLP.

One channel = one sample, so only crash/impact events are placed. The lane is
dropped by starter_kit when the library has no crash one-shot — these tests
cover the note materialization and the flp_writer channel/pattern plumbing.
"""

from __future__ import annotations

import io

from flp_writer import build_flp
from fx_layer import materialize_fx_notes


def _arranged_pattern() -> dict:
    return {
        "bpm": 150,
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.3, "velocity": 110}],
            "sub_808": [{"time_step": 0.0, "note": "E2", "length": 2.0, "velocity": 120}],
            "hi_hats": [{"time_step": 0.5, "note": "F#5", "length": 0.1, "velocity": 80}],
            "melody_lead": [{"time_step": 0.0, "note": "E5", "length": 1.0, "velocity": 95}],
        },
        "plg_arrangement": [
            {"name": "intro", "start_bar": 0, "bars": 4},
            {"name": "hook", "start_bar": 4, "bars": 8},
            {"name": "verse", "start_bar": 12, "bars": 8},
            {"name": "chorus", "start_bar": 20, "bars": 8, "final_chorus": True},
        ],
        "build_order": ["kick", "sub_808", "hi_hats", "melody_lead"],
    }


def test_materialize_places_crashes_on_landings():
    pattern = _arranged_pattern()
    placed = materialize_fx_notes(pattern)
    fx = pattern["tracks"]["fx"]
    assert placed == 2
    assert [n["time_step"] for n in fx] == [16.0, 80.0]  # hook bar 4, chorus bar 20
    assert fx[1]["velocity"] > fx[0]["velocity"]  # final chorus hits harder


def test_materialize_respects_kill_switch(monkeypatch):
    monkeypatch.setenv("PLG_FX_LANE", "off")
    pattern = _arranged_pattern()
    assert materialize_fx_notes(pattern) == 0
    assert "fx" not in pattern["tracks"]


def _read_names_and_notes(blob: bytes) -> tuple[list[str], dict[str, int]]:
    """Minimal FLP event-stream reader (no third-party deps)."""
    stream = io.BytesIO(blob)
    assert stream.read(4) == b"FLhd"
    stream.seek(22)
    names: list[str] = []
    notes: dict[str, int] = {}
    current_pattern = None
    size = len(blob)
    while stream.tell() < size:
        event_id = int.from_bytes(stream.read(1), "little")
        if event_id < 64:
            value = stream.read(1)
        elif event_id < 128:
            value = stream.read(2)
        elif event_id < 192:
            value = stream.read(4)
        else:
            length = shift = 0
            while True:
                byte = stream.read(1)[0]
                length |= (byte & 0x7F) << shift
                shift += 7
                if not byte & 0x80:
                    break
            value = stream.read(length)
        if event_id == 192:  # channel name
            names.append(value.decode("utf-16-le", "ignore").rstrip("\0"))
        elif event_id == 193:  # pattern name
            current_pattern = value.decode("utf-16-le", "ignore").rstrip("\0")
        elif event_id == 224 and current_pattern:  # note structs (24 bytes each)
            notes[current_pattern] = len(value) // 24
    return names, notes


def test_flp_contains_fx_channel_and_notes():
    pattern = _arranged_pattern()
    materialize_fx_notes(pattern)
    blob = build_flp(pattern)
    names, notes = _read_names_and_notes(blob)
    assert "PLG FX" in names
    assert notes.get("PLG FX") == 2

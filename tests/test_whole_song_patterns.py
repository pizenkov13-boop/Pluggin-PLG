"""One pattern per channel, one playlist clip per channel across the whole song (``PLG_PATTERN_SECTIONS`` off, the default).

The tester's projects came with 28-33 patterns (``PLG Hi-Hats Intro`` / ``Alt`` / ``Main`` / ``Drop`` / ``Main 2`` / ``Outro`` ...)
for 6-7 channels. The default is now one pattern per channel holding every note of the beat at its own position, named like the channel,
and one playlist clip from bar 0 to the end. ``PLG_PATTERN_SECTIONS=on`` brings the old layout back. The notes, their times, lengths,
velocities and the sounds are the same in both: a note's tail is still cut at the end of ITS section, exactly as it was when each section
was its own pattern.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

import flp_template
from flp_writer import build_flp

SECTION_WORDS = ("Intro", "Alt", "Main", "Drop", "Hook", "Outro", "Verse", "Chorus")

ARRANGEMENT = [
    {"name": "intro", "start_bar": 0, "bars": 8},
    {"name": "hook", "start_bar": 8, "bars": 8},
    {"name": "verse", "start_bar": 16, "bars": 8},
    {"name": "drop", "start_bar": 24, "bars": 8},
]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("PLG_PATTERN_SECTIONS", raising=False)
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")


def _data() -> dict:
    def n(t, key="C3", ln=0.5, vel=100):
        return {"time_step": t, "note": key, "length": ln, "velocity": vel}

    return {
        "bpm": 140,
        "plg_midi_melody": True,
        "plg_total_bars": 32,
        "plg_arrangement": [dict(section) for section in ARRANGEMENT],
        "tracks": {
            # a long note that starts in bar 7 and would run into the hook: its tail is cut at the section end
            "melody_lead": [n(0.0, "A4", 4.0), n(7 * 4 + 2.0, "A4", 5.0), n(8 * 4 + 1.0, "B4", 2.0), n(20 * 4.0, "C5", 1.0), n(24 * 4 + 1.0, "B4", 2.0)],  # the drop repeats the hook melody
            "kick": [n(8 * 4.0), n(16 * 4.0), n(24 * 4.0), n(24 * 4.0 + 2.0)],
            "hi_hats": [n(0.0, "C5", 0.25), n(8 * 4.0 + 0.5, "C5", 0.25), n(26 * 4.0, "C5", 0.25)],
            "clap": [n(9 * 4.0 + 2.0), n(17 * 4.0 + 2.0), n(25 * 4.0 + 2.0)],
            "snare": [n(9 * 4.0 + 2.0), n(25 * 4.0 + 2.0)],
        },
        "plg_sample_fallback": {
            "808_bass": {"tier": 0, "sample": None, "reasons": [], "user_files": 0},
            "snare_layer": {"tier": 0, "sample": None, "reasons": ["snare_absent"], "user_files": 0},
        },
    }


def _parse(blob: bytes):
    """channels (names), patterns {iid: {name, notes}}, playlist items [(position, iid, length, row)]."""
    stream = memoryview(blob)[22:]
    channels, patterns, items, current = [], {}, [], None
    off = 0
    while off < len(stream):
        eid = stream[off]
        off += 1
        if eid < 64:
            off += 1
        elif eid < 128:
            val = struct.unpack_from("<H", stream, off)[0]
            off += 2
            if eid == 65:
                current = val
                patterns[current] = {"name": None, "notes": []}
        elif eid < 192:
            off += 4
        else:
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
            if eid == 192:
                channels.append(raw.decode("utf-16-le").rstrip("\x00"))
            elif eid == 193 and current is not None and patterns[current]["name"] is None:
                patterns[current]["name"] = raw.decode("utf-16-le").rstrip("\x00")
            elif eid == 224 and current is not None:
                for i in range(len(raw) // 24):
                    pos, _flags, rack, ln, key, _grp, _f, _r, _m, _p, vel = struct.unpack_from("<IHHIHHBBBBB", raw, i * 24)
                    patterns[current]["notes"].append((pos, rack, ln, key, vel))
            elif eid == 233:
                for i in range(len(raw) // 60):
                    pos, _base, idx, ln, row, _g = struct.unpack_from("<IHHIHH", raw, i * 60)
                    items.append((pos, idx - 20480, ln, row))
    return channels, patterns, items


def _absolute_notes(patterns, items):
    """Every note of the song at its absolute tick: (tick, channel rack, length, key, velocity)."""
    out = []
    for clip_pos, iid, _ln, _row in items:  # a pattern reused by several clips sounds at each of them
        for pos, rack, ln, key, vel in patterns[iid]["notes"]:
            out.append((pos + clip_pos, rack, ln, key, vel))
    return sorted(out)


def test_the_default_is_one_pattern_per_channel_and_one_clip_across_the_song(monkeypatch):
    assert flp_template.pattern_sections_enabled() is False
    channels, patterns, items = _parse(build_flp(_data()))
    assert channels == ["PLG MIDI", "PLG Kick", "PLG Hi-Hats", "PLG Clap", "PLG Snare"]
    assert len(patterns) == len(channels) == 5 and len(items) == 5
    assert [p["name"] for _iid, p in sorted(patterns.items())] == channels  # plain names, in channel order
    assert not any(word in p["name"] for p in patterns.values() for word in SECTION_WORDS)
    # every clip starts at the bar of its channel's first note and runs to the end of the song (32 bars; lengths in 1/96 beat)
    by_name = {patterns[iid]["name"]: (pos, ln) for pos, iid, ln, _row in items}
    first_bar = {"PLG MIDI": 0, "PLG Kick": 8, "PLG Hi-Hats": 0, "PLG Clap": 9, "PLG Snare": 9}
    assert by_name == {name: (bar * 4 * 960, (32 - bar) * 4 * 96) for name, bar in first_bar.items()}
    assert sorted(row for *_rest, row in items) == sorted(set(row for *_rest, row in items)) and len({row for *_r, row in items}) == 5
    # each pattern holds all the notes of its channel
    counts = {p["name"]: len(p["notes"]) for p in patterns.values()}
    assert counts == {"PLG MIDI": 5, "PLG Kick": 4, "PLG Hi-Hats": 3, "PLG Clap": 3, "PLG Snare": 2}


def test_the_flag_brings_the_section_patterns_back(monkeypatch):
    for value in ("on", "1", "true", "YES"):
        monkeypatch.setenv("PLG_PATTERN_SECTIONS", value)
        assert flp_template.pattern_sections_enabled() is True
    monkeypatch.setenv("PLG_PATTERN_SECTIONS", "on")
    _channels, patterns, items = _parse(build_flp(_data()))
    assert len(patterns) > 5 and len(items) > 5
    assert any(word in p["name"] for p in patterns.values() for word in SECTION_WORDS)
    for value in ("", "off", "0", "no"):
        monkeypatch.setenv("PLG_PATTERN_SECTIONS", value)
        assert flp_template.pattern_sections_enabled() is False


def test_every_note_is_identical_in_both_layouts_including_the_tail_cut_at_the_section_end(monkeypatch):
    new_channels, new_patterns, new_items = _parse(build_flp(_data()))
    monkeypatch.setenv("PLG_PATTERN_SECTIONS", "on")
    old_channels, old_patterns, old_items = _parse(build_flp(_data()))
    assert new_channels == old_channels  # the channel rack: same channels, same order
    new, old = _absolute_notes(new_patterns, new_items), _absolute_notes(old_patterns, old_items)
    assert new == old and len(new) == 17
    melody = [note for note in new if note[1] == 0]
    cut = next(note for note in melody if note[0] == (7 * 4 + 2.0) * 960)
    assert cut[2] == int(round(8 * 4 * 960)) - cut[0]  # 5.0 beats asked for, cut at the end of the intro (bar 8)


def test_the_layout_audit_and_the_clip_record_describe_the_whole_song_layout():
    data = _data()
    clips = flp_template.build_playlist_clips(data)
    assert len(clips) == 5
    assert {c.track_key: (c.start_bar, c.length_bars) for c in clips} == {
        "melody_lead": (0, 32), "kick": (8, 24), "hi_hats": (0, 32), "clap": (9, 23), "snare": (9, 23)
    }
    assert data["plg_playlist_layout_audit"]["passed"] is True
    assert data["plg_playlist_layout_audit"]["clip_count"] == 5 and data["plg_playlist_layout_audit"]["unique_pattern_count"] == 5
    assert [c["pattern_name"] for c in data["plg_playlist_clips"]] == ["PLG MIDI", "PLG Kick", "PLG Hi-Hats", "PLG Clap", "PLG Snare"]


def test_playlist_rows_stay_compact_and_one_row_per_lane():
    data = _data()
    flp_template.build_playlist_clips(data)
    rows = sorted(c["playlist_track"] for c in data["plg_playlist_clips"])
    assert rows == list(range(5))


def test_a_beat_without_an_arrangement_still_gets_one_clip_per_channel():
    data = _data()
    data.pop("plg_arrangement")
    channels, patterns, items = _parse(build_flp(data))
    assert len(patterns) == len(channels) == 5 and len(items) == 5


def test_a_channel_that_enters_late_has_its_pattern_start_at_its_first_bar_and_the_notes_do_not_move(monkeypatch):
    """The clip's own position does it (no trim bytes): the pattern begins at the clip, the notes sound where they always did."""
    _channels, patterns, items = _parse(build_flp(_data()))
    kick_iid = next(iid for iid, p in patterns.items() if p["name"] == "PLG Kick")
    clip_pos = next(pos for pos, iid, _ln, _row in items if iid == kick_iid)
    assert clip_pos == 8 * 4 * 960
    relative = sorted(pos for pos, *_rest in patterns[kick_iid]["notes"])
    assert relative[0] == 0 and [clip_pos + r for r in relative] == [8 * 4 * 960, 16 * 4 * 960, 24 * 4 * 960, (24 * 4 + 2) * 960]
    monkeypatch.setenv("PLG_PATTERN_SECTIONS", "on")
    old = _parse(build_flp(_data()))
    assert _absolute_notes(patterns, items) == _absolute_notes(old[1], old[2])

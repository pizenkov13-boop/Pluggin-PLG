"""Registration-completeness net for the "808 Bass" lane (Part B, 2026-07).

sub_808's own history shows the real bug class here: a track key wired into
some but not all of the ~7 parallel string-keyed registries in
flp_template.py/flp_writer.py fails SILENTLY (falls back to channel/pattern/
insert 0) instead of raising. These tests target exactly that class of bug,
both generically (every registry stays internally consistent) and
specifically for 808_bass (a real export with the lane active routes its
notes to their own channel, never colliding with another track)."""

from __future__ import annotations

import dataclasses
import struct

from drum_blueprint import build_kick_pattern
from flp_template import (
    CHANNEL_MIXER_INSERT,
    CORE_TEMPLATE_CHANNELS,
    DRUM_EXPORT_KEYS,
    MIXER_TEMPLATE_INSERTS,
    PATTERN_DISPLAY,
    PATTERN_ORDER,
    PLAYLIST_TRACK_FOR,
    TEMPLATE_CHANNELS,
    active_export_channels,
    pattern_iid_for,
)
from pattern_utils import OPTIONAL_TRACK_KEYS, SAMPLE_TRACK_KEYS, TRACK_KEYS


# --- generic registration-completeness (anti "channel-index misassignment") --


def test_template_channels_has_no_duplicates():
    assert len(set(TEMPLATE_CHANNELS)) == len(TEMPLATE_CHANNELS)


def test_pattern_order_has_no_duplicates():
    assert len(set(PATTERN_ORDER)) == len(PATTERN_ORDER)


def test_channel_mixer_insert_values_are_unique():
    values = list(CHANNEL_MIXER_INSERT.values())
    assert len(set(values)) == len(values)


def test_playlist_track_for_values_are_unique():
    values = list(PLAYLIST_TRACK_FOR.values())
    assert len(set(values)) == len(values)


def test_mixer_template_inserts_indices_are_unique():
    indices = [idx for idx, _name, _master in MIXER_TEMPLATE_INSERTS]
    assert len(set(indices)) == len(indices)


def test_every_sample_track_key_is_fully_registered():
    """Every sample-based track (core or optional) must have an entry in
    every one of the parallel registries that active export/mixer wiring
    reads from — missing even one silently misroutes rather than erroring."""
    for key in SAMPLE_TRACK_KEYS:
        assert key in TEMPLATE_CHANNELS, key
        assert key in PATTERN_ORDER, key
        assert key in PATTERN_DISPLAY, key
        assert key in PLAYLIST_TRACK_FOR, key
        assert key in CHANNEL_MIXER_INSERT, key


def test_optional_track_keys_are_not_core():
    """snare_layer/counter_melody/808_bass materialize conditionally — they
    must never be forced into every export via CORE_TEMPLATE_CHANNELS."""
    for key in OPTIONAL_TRACK_KEYS:
        assert key not in CORE_TEMPLATE_CHANNELS, key


# --- 808_bass specifically ----------------------------------------------------


def test_808_bass_registered_but_not_core():
    assert "808_bass" in TRACK_KEYS
    assert "808_bass" in OPTIONAL_TRACK_KEYS
    assert "808_bass" not in CORE_TEMPLATE_CHANNELS
    assert "808_bass" in DRUM_EXPORT_KEYS


def test_808_bass_present_in_template_channels_and_pattern_order():
    """2026-07: reordering TEMPLATE_CHANNELS/PATTERN_ORDER (808_bass now
    sits next to kick/sub_808, not appended last) is safe post Part A's
    fix — a note's rack_channel comes from this export's own dynamically
    enumerated channel list, not a fixed TEMPLATE_CHANNELS position. The
    real invariant is just membership + uniqueness (see
    test_template_channels_has_no_duplicates /
    test_pattern_order_has_no_duplicates above), not a fixed tuple
    position — position is now purely a display/ordering concern."""
    assert "808_bass" in TEMPLATE_CHANNELS
    assert "808_bass" in PATTERN_ORDER


def test_808_bass_absent_from_export_when_it_has_no_notes():
    data = {"tracks": {"kick": [{"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 100}]}}
    assert "808_bass" not in active_export_channels(data)


def test_808_bass_present_in_export_when_it_has_notes():
    data = {
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 100}],
            "808_bass": [{"time_step": 0.0, "note": "C2", "length": 0.1, "velocity": 100}],
        }
    }
    assert "808_bass" in active_export_channels(data)


def test_808_bass_present_in_export_when_flag_active_even_with_no_notes():
    """The plg_bass808_lane_active flag-bypass (2026-07, replacing the removed
    kick/808_bass note generation) — see flp_template._bass808_lane_visible."""
    from flp_template import active_pattern_keys, playlist_track_map_for_export

    data = {
        "plg_bass808_lane_active": True,
        "tracks": {"kick": [{"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 100}]},
    }
    assert "808_bass" in active_export_channels(data)
    assert "808_bass" in active_pattern_keys(data)

    midi_data = {
        "plg_midi_melody": True,
        "plg_bass808_lane_active": True,
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "C4", "length": 0.5, "velocity": 100}],
        },
    }
    assert "808_bass" in playlist_track_map_for_export(midi_data)


# --- end-to-end: real export with the lane active ----------------------------


def _parse_pattern_notes(blob: bytes) -> dict[int, set[int]]:
    """pattern_iid -> set of rack_channel indices carrying notes, parsed from
    the real exported .flp event stream (same technique as
    tests/test_flp_writer.py's channel-index regression test)."""
    body = blob[14:]
    size = struct.unpack_from("<I", body, 4)[0]
    stream = memoryview(body)[8 : 8 + size]
    _PAT_NEW, _PAT_NOTES = 65, 224
    offset = 0
    cur_pat = None
    racks_by_pattern: dict[int, set[int]] = {}
    while offset < len(stream):
        eid = stream[offset]
        offset += 1
        if eid < 64:
            offset += 1
        elif eid < 128:
            val = struct.unpack_from("<H", stream, offset)[0]
            offset += 2
            if eid == _PAT_NEW:
                cur_pat = val
        elif eid < 192:
            offset += 4
        else:
            length = 0
            shift = 0
            while True:
                b = stream[offset]
                offset += 1
                length |= (b & 0x7F) << shift
                if b & 0x80 == 0:
                    break
                shift += 7
            raw = bytes(stream[offset : offset + length])
            offset += length
            if eid == _PAT_NOTES:
                racks = {struct.unpack_from("<IHHI", raw, i * 24)[2] for i in range(len(raw) // 24)}
                racks_by_pattern.setdefault(cur_pat, set()).update(racks)
    return racks_by_pattern


def test_808_bass_channel_registers_and_carries_notes_distinct_from_kick():
    """Real end-to-end export (arrange -> humanize with bass808_enabled=True
    -> build_flp): kick and 808_bass are planned together (paired/kick_only/
    bass_only onsets — see bass808_gen.build_kick_808_pocket) but must still
    land on their OWN, DISTINCT rack channels/patterns, never collide."""
    from arranger import arrange_song
    from beat_humanize import humanize_pattern
    from flp_writer import build_flp
    from genre_profiles import PROFILES

    forced = dataclasses.replace(PROFILES["trap"], bass808_enabled=True)
    kick = build_kick_pattern(bars=8)
    pattern = {
        "bpm": 140,
        "style": "trap",
        "user_prompt": "dark trap 808",
        "plg_midi_melody": True,
        "plg_variety": {"genre": "trap", "seed": 5},
        "tracks": {
            "kick": kick,
            "melody_lead": [
                {"time_step": i * 2.0, "note": ("C4", "E4", "G4")[i % 3], "length": 1.5, "velocity": 95}
                for i in range(16)
            ],
        },
    }
    arrange_song(pattern)
    out = humanize_pattern(pattern, profile=forced)
    assert out["tracks"].get("808_bass")
    assert out["tracks"].get("kick")
    assert not out["tracks"].get("sub_808")
    assert out.get("plg_bass808_lane_active") is True

    blob = build_flp(out)
    channels = active_export_channels(out)
    assert "808_bass" in channels
    assert "kick" in channels
    channel_index = {key: i for i, key in enumerate(channels)}
    assert channel_index["kick"] != channel_index["808_bass"]

    racks_by_pattern = _parse_pattern_notes(blob)
    kick_iid = pattern_iid_for("kick")
    bass_iid = pattern_iid_for("808_bass")
    assert racks_by_pattern.get(kick_iid) == {channel_index["kick"]}
    assert racks_by_pattern.get(bass_iid) == {channel_index["808_bass"]}

    assert "PLG 808 Bass".encode("utf-16-le") in blob
    assert "PLG Kick".encode("utf-16-le") in blob


# --- regression: bass808_enabled=False profiles never run the kick/808 engine



def _bass808_test_pattern(name: str) -> dict:
    kick = build_kick_pattern(bars=8)
    return {
        "bpm": 140,
        "style": name,
        "user_prompt": name,
        "plg_variety": {"genre": name, "seed": 1},
        "tracks": {
            "kick": [dict(n) for n in kick],
            "melody_lead": [
                {"time_step": i * 2.0, "note": ("C4", "E4", "G4")[i % 3], "length": 1.5, "velocity": 95}
                for i in range(16)
            ],
        },
    }


def test_808_bass_engine_is_a_no_op_outside_the_hiphop_family():
    """`_apply_808_bass_lane` (the kick/808 pocket engine) never runs for
    bass808_enabled=False profiles.

    Corrected 2026-08-11. This used to assert that the `808_bass` *lane* stays
    empty for every such profile, which contradicted the export contract:
    `midi_validate` requires a non-empty `808_bass` and deletes `sub_808`, so
    the 14 genres with `eight08=False` generated no low end at all and died in
    `enforce_midi_mode`. Their idiomatic bassline now publishes to `808_bass`
    (see beat_humanize._apply_sub_808_lane._publish_low_end).

    What must still hold is the thing this test was really protecting: the
    hip-hop kick/808 pocket engine does not touch these genres — no paired
    pocket, no seal, no `plg_kick808_source` from `midi_phrase_composer` /
    `bass808_gen`.
    """
    from arranger import arrange_song
    from beat_humanize import humanize_pattern
    from genre_profiles import PROFILES

    for name, profile in PROFILES.items():
        if profile.bass808_enabled:
            continue
        pattern = _bass808_test_pattern(name)
        arrange_song(pattern)
        out = humanize_pattern(pattern, profile=profile)

        assert out.get("plg_kick808_source") not in {
            "midi_phrase_composer",
            "bass808_llm",
            "deterministic",
        }, name
        assert not out.get("plg_kick808_audit"), name

        if profile.eight08:
            # Unchanged: these keep their own 808 on `sub_808`.
            assert not out["tracks"].get("808_bass"), name
            assert "808_bass" not in active_export_channels(out), name
        else:
            # No 808 engine at all — the genre bassline is the only low end,
            # and it has to be on the one lane the exporter allows.
            assert not out["tracks"].get("sub_808"), name


def test_808_bass_lane_is_active_for_the_whole_hiphop_family():
    """Every genre with bass808_enabled=True generates the lane end to end."""
    from arranger import arrange_song
    from beat_humanize import humanize_pattern
    from flp_writer import build_flp
    from genre_profiles import PROFILES

    enabled = [name for name, profile in PROFILES.items() if profile.bass808_enabled]
    assert enabled
    for name in sorted(enabled):
        profile = PROFILES[name]
        pattern = _bass808_test_pattern(name)
        arrange_song(pattern)
        out = humanize_pattern(pattern, profile=profile)
        assert out["tracks"].get("808_bass"), name
        assert out.get("plg_bass808_lane_active") is True, name
        assert "808_bass" in active_export_channels(out), name

        blob = build_flp(out)
        assert "PLG 808 Bass".encode("utf-16-le") in blob, name

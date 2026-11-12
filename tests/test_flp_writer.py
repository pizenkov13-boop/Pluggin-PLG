import pytest
import struct
import wave
from pathlib import Path

import numpy as np

from flp_writer import (
    MAX_PATTERN_BARS,
    MIN_PATTERN_BARS,
    PPQ,
    _FL_SUB_808_PITCH_OFFSET,
    _TIME_SIG_BEAT,
    _TIME_SIG_NUM,
    _CH_PREAMP,
    _CH_REVERB,
    _CH_ROUTED_TO,
    _CH_VOL,
    _INSERT_FLAGS,
    _INSERT_NAME,
    _MIXER_PARAMS,
    _PAT_LENGTH,
    _PAT_LOOPED,
    _PLUGIN_DATA,
    _PLUGIN_INTERNAL,
    _PLUGIN_WRAPPER,
    _db_to_fl_volume,
    build_flp,
    pattern_bars,
    pattern_length_ticks,
    prepare_pattern_for_flp_export,
)
from mix_gain_staging import (
    _PARAM_LOW_FREQ,
    _PARAM_VOLUME,
    insert_fader_value,
)
from pattern_utils import parse_note_name


@pytest.fixture(autouse=True)
def _section_patterns(monkeypatch):
    """These tests describe the sectioned layout (one pattern per lane PER SECTION); the default is one pattern per channel
    (tests/test_whole_song_patterns.py)."""
    monkeypatch.setenv("PLG_PATTERN_SECTIONS", "on")


def _note(time_step: float = 0.0, length: float = 4.0) -> dict:
    return {"time_step": time_step, "note": "C3", "length": length, "velocity": 100}


def _minimal_pattern(**overrides) -> dict:
    data = {
        "bpm": 140,
        "tracks": {
            "kick": [_note()],
            "snare": [_note(1.0, 0.5)],
            "clap": [_note(1.0, 0.5)],
            "sub_808": [_note(0.0, 2.0)],
            "hi_hats": [_note(0.25, 0.25)],
            "melody_lead": [_note(0.0, 4.0)],
        },
        "fx_automation": {
            "Channel_Precomputed": {"boost": 0.30},
            "Fruity_Fast_Dist": {"drive": 0.9, "mix": 1.0},
        },
    }
    data.update(overrides)
    return data


def _event_stream(payload: bytes) -> memoryview:
    body = payload[14:]
    assert body[:4] == b"FLdt"
    size = struct.unpack_from("<I", body, 4)[0]
    return memoryview(body)[8 : 8 + size]


def _event_ids(payload: bytes) -> list[int]:
    """Parse FLP event stream after FLdt header."""
    stream = _event_stream(payload)
    ids: list[int] = []
    offset = 0
    while offset < len(stream):
        eid = stream[offset]
        offset += 1
        ids.append(eid)
        if eid < 64:
            offset += 1
        elif eid < 128:
            offset += 2
        elif eid < 192:
            offset += 4
        else:
            length = 0
            shift = 0
            while True:
                byte = stream[offset]
                offset += 1
                length |= (byte & 0x7F) << shift
                if byte & 0x80 == 0:
                    break
                shift += 7
            offset += length
    return ids


def _text_events(payload: bytes) -> list[tuple[int, str]]:
    stream = _event_stream(payload)
    out: list[tuple[int, str]] = []
    offset = 0
    while offset < len(stream):
        eid = stream[offset]
        offset += 1
        if eid < 64:
            offset += 1
        elif eid < 128:
            offset += 2
        elif eid < 192:
            offset += 4
        else:
            length = 0
            shift = 0
            while True:
                byte = stream[offset]
                offset += 1
                length |= (byte & 0x7F) << shift
                if byte & 0x80 == 0:
                    break
                shift += 7
            payload_bytes = bytes(stream[offset : offset + length])
            offset += length
            if eid in (_INSERT_NAME, _PLUGIN_INTERNAL):
                out.append((eid, payload_bytes.decode("utf-16-le").rstrip("\x00")))
    return out


def _word_values_after(payload: bytes, event_id: int) -> list[int]:
    stream = _event_stream(payload)
    values: list[int] = []
    offset = 0
    while offset < len(stream):
        eid = stream[offset]
        offset += 1
        if eid < 64:
            offset += 1
        elif eid < 128:
            val = struct.unpack_from("<H", stream, offset)[0]
            offset += 2
            if eid == event_id:
                values.append(val)
        elif eid < 192:
            offset += 4
        else:
            length = 0
            shift = 0
            while True:
                byte = stream[offset]
                offset += 1
                length |= (byte & 0x7F) << shift
                if byte & 0x80 == 0:
                    break
                shift += 7
            offset += length
    return values


def _pattern_note_keys(blob: bytes, rack_channel: int) -> list[int]:
    """Parse MIDI keys from FLP note structs for one channel rack index."""
    stream = _event_stream(blob)
    offset = 0
    keys: list[int] = []
    while offset < len(stream):
        eid = stream[offset]
        offset += 1
        if eid < 64:
            offset += 1
        elif eid < 128:
            offset += 2
        elif eid < 192:
            offset += 4
        else:
            length = 0
            shift = 0
            while True:
                byte = stream[offset]
                offset += 1
                length |= (byte & 0x7F) << shift
                if byte & 0x80 == 0:
                    break
                shift += 7
            payload = bytes(stream[offset : offset + length])
            offset += length
            if eid == 224:  # _PAT_NOTES
                pos = 0
                while pos + 24 <= len(payload):
                    ch = struct.unpack_from("<H", payload, pos + 6)[0]
                    key = struct.unpack_from("<H", payload, pos + 12)[0]
                    if ch == rack_channel:
                        keys.append(key)
                    pos += 24
    return keys


def test_sub_808_flp_note_keeps_generator_pitch():
    """sub_808 MIDI is already C4–C5 — do not drop another octave on export."""
    from flp_template import active_export_channels

    data = _minimal_pattern(
        plg_total_bars=48,
        tracks={
            "kick": [_note(32.0, 0.4)],
            "snare": [],
            "clap": [],
            "sub_808": [{"time_step": 32.0, "note": "C4", "length": 2.0, "velocity": 127}],
            "hi_hats": [],
            "melody_lead": [_note(0.0, 4.0)],
        }
    )
    blob = build_flp(data)
    sub_808_rack_channel = active_export_channels(data).index("sub_808")
    keys = _pattern_note_keys(blob, sub_808_rack_channel)
    assert keys == [parse_note_name("C4") + _FL_SUB_808_PITCH_OFFSET]
    assert _FL_SUB_808_PITCH_OFFSET == 0
    assert keys == [parse_note_name("C4")]


def test_808_sampler_plays_oneshot_without_volume_envelope_gate():
    """Soft long-release envelope — audible in FL, not a hard chop."""
    from flp_writer import _CH_ENVLFO, _ENV_808_SLOT1, _MELODY_ENV_SLOT1, _envlfo

    hard = _envlfo(4, 1, 100, 100, 128, 100, -101)
    blob = build_flp(_minimal_pattern())
    payloads = _data_payloads(blob, _CH_ENVLFO)
    assert _ENV_808_SLOT1 in payloads
    assert hard not in payloads
    assert _MELODY_ENV_SLOT1 in payloads


def test_melody_envelope_coexists_with_808_default_envelope_slots():
    """808 soft gate + melody soft envelope + kick/clap gates all ship."""
    from flp_writer import (
        _CH_ENVLFO,
        _ENV_808_SLOT1,
        _ENV_808_SLOT2,
        _ENV_CLAP_SLOT1,
        _ENV_CLAP_SLOT2,
        _ENV_KICK_SLOT1,
        _ENV_KICK_SLOT2,
        _MELODY_ENV_SLOT1,
        _MELODY_ENV_SLOT2,
    )

    blob = build_flp(_minimal_pattern())
    payloads = _data_payloads(blob, _CH_ENVLFO)
    assert _ENV_808_SLOT1 in payloads
    assert _ENV_808_SLOT2 in payloads
    assert _ENV_KICK_SLOT1 in payloads
    assert _ENV_KICK_SLOT2 in payloads
    assert _ENV_CLAP_SLOT1 in payloads
    assert _ENV_CLAP_SLOT2 in payloads
    assert _MELODY_ENV_SLOT1 in payloads
    assert _MELODY_ENV_SLOT2 in payloads
    # sub_808 (5) + kick (5) + clap (5) + melody_lead (5) = 20 envelope events.
    assert len(payloads) == 20


def test_kick_envelope_is_short_gate_shorter_release_than_808():
    """Kick gets its own tight gate — not the 808's legato-adjacent release,
    and not FL's untouched Sampler defaults (no envelope at all)."""
    import struct

    from flp_writer import _CH_ENVLFO, _ENV_808_SLOT1, _ENV_KICK_SLOT1

    blob = build_flp(_minimal_pattern())
    payloads = _data_payloads(blob, _CH_ENVLFO)
    assert _ENV_KICK_SLOT1 in payloads

    kick_fields = struct.unpack("<17i", _ENV_KICK_SLOT1)
    bass_fields = struct.unpack("<17i", _ENV_808_SLOT1)
    kick_attack, kick_hold, kick_release = kick_fields[3], kick_fields[4], kick_fields[7]
    bass_attack, bass_hold, bass_release = bass_fields[3], bass_fields[4], bass_fields[7]
    assert 0 < kick_release < bass_release  # short, not zero, not the 808's 2500
    assert kick_hold < bass_hold
    assert kick_attack <= bass_attack


def test_clap_gets_the_same_short_gate_kick_has_not_fl_defaults():
    """Clap had no envelope at all (same gap kick had) — long-tailed clap
    samples used to ring uncontrolled."""
    from flp_writer import _CH_ENVLFO, _ENV_CLAP_SLOT1, _ENV_CLAP_SLOT2, _ENV_KICK_SLOT1

    blob = build_flp(_minimal_pattern())
    payloads = _data_payloads(blob, _CH_ENVLFO)
    assert _ENV_CLAP_SLOT1 in payloads
    assert _ENV_CLAP_SLOT2 in payloads
    assert _ENV_CLAP_SLOT1 == _ENV_KICK_SLOT1  # same short-gate shape as kick


def test_808_bass_flp_note_does_not_inherit_legacy_sub_octave_offset():
    """The fresh 808 Bass lane already emits sampler-native notes.

    Reapplying sub_808's -24 offset puts F3 at F1, which makes a valid 808
    source sound like a short crackly transient instead of bass.
    """
    from flp_template import active_export_channels

    data = _minimal_pattern(
        plg_total_bars=48,
        tracks={
            "kick": [_note(32.0, 0.4)],
            "snare": [],
            "clap": [],
            "sub_808": [],
            "808_bass": [{"time_step": 32.0, "note": "F3", "length": 0.6, "velocity": 127}],
            "hi_hats": [],
            "melody_lead": [_note(0.0, 4.0)],
        },
    )
    blob = build_flp(data)
    bass_rack_channel = active_export_channels(data).index("808_bass")
    assert _pattern_note_keys(blob, bass_rack_channel) == [parse_note_name("F3")]


def test_pattern_bars_clamped_to_eight_sixteen():
    short = _minimal_pattern()
    assert pattern_bars(short) == MIN_PATTERN_BARS

    long = _minimal_pattern(
        tracks={
            "kick": [_note(60.0, 4.0)],
            "snare": [],
            "clap": [],
            "sub_808": [_note(0.0, 4.0)],
            "hi_hats": [_note(0.25, 0.25)],
            "melody_lead": [_note(0.0, 4.0)],
        }
    )
    assert pattern_bars(long) == MAX_PATTERN_BARS


def test_pattern_length_ticks_eight_bars():
    data = _minimal_pattern()
    assert pattern_length_ticks(data) == 8 * 4 * PPQ


@pytest.mark.legacy_mix
def test_flp_contains_time_sig_pattern_length_and_fx():
    # FL plugin FX (Fast Dist/preamp/reverb) are opt-in via plg_fl_fx.
    blob = build_flp(_minimal_pattern(plg_fl_fx=True))
    ids = _event_ids(blob)
    assert _TIME_SIG_NUM in ids
    assert _TIME_SIG_BEAT in ids
    assert _PAT_LOOPED in ids
    assert _PAT_LENGTH in ids
    assert _CH_PREAMP in ids
    assert _CH_REVERB in ids
    assert _PLUGIN_INTERNAL in ids
    assert _PLUGIN_DATA in ids


@pytest.mark.legacy_mix
def test_flp_template_includes_master_soft_clipper():
    """Default lane (trap): Soft Clipper on master; 808 stays folder-faithful
    (no sampler BOOST bake). Insert FX stay opt-in."""
    from flp_writer import _PLUGIN_COLOR, _PLUGIN_ICON, _PLUGIN_SLOT_COLOR

    blob = build_flp(_minimal_pattern())
    plugin_names = [text for eid, text in _text_events(blob) if eid == _PLUGIN_INTERNAL]
    assert "Fruity Soft Clipper" in plugin_names
    assert "Fruity Blood Overdrive" not in plugin_names
    assert "Fruity Reeverb 2" not in plugin_names
    assert "Fruity Delay 3" not in plugin_names
    icon_color = (
        bytes([_PLUGIN_ICON])
        + struct.pack("<I", 0)
        + bytes([_PLUGIN_COLOR])
        + struct.pack("<I", _PLUGIN_SLOT_COLOR)
    )
    assert icon_color in blob
    ids = _event_ids(blob)
    assert _MIXER_PARAMS in ids
    # The 808 remains folder-faithful; the event present here is the dedicated
    # kick pre-gain needed after listening exposed the snare stack dominance.
    assert _CH_PREAMP in ids


@pytest.mark.legacy_mix
def test_flp_space_mix_opt_in_ships_insert_fx():
    """plg_space_mix=True brings back Reeverb 2 + Delay 3 + Blood Overdrive."""
    blob = build_flp(_minimal_pattern(plg_space_mix=True))
    plugin_names = [text for eid, text in _text_events(blob) if eid == _PLUGIN_INTERNAL]
    assert "Fruity Soft Clipper" in plugin_names
    assert "Fruity Reeverb 2" in plugin_names
    assert "Fruity Delay 3" in plugin_names
    assert "Fruity Blood Overdrive" in plugin_names


def _base_snare_layer_tracks(**extra) -> dict:
    tracks = {
        "kick": [_note(0.0, 0.4)],
        "snare": [_note(1.0, 0.3)],
        "clap": [_note(1.0, 0.3)],
        "sub_808": [],
        "hi_hats": [],
        "melody_lead": [_note(0.0, 4.0)],
    }
    tracks.update(extra)
    return tracks


@pytest.mark.legacy_mix
def test_snare_layer_gets_reverb_via_existing_space_mix_mechanism():
    """duplicate_clap_layer's "expensive" symbiosis reuses melody's own
    space_mix opt-in + reverb-mix formula — no new flag."""
    from flp_writer import _CH_REVERB
    from mix_insert_fx import melody_channel_reverb_mix

    without_layer = _minimal_pattern(plg_space_mix=True, tracks=_base_snare_layer_tracks())
    with_layer = _minimal_pattern(
        plg_space_mix=True,
        tracks=_base_snare_layer_tracks(snare_layer=[_note(1.0, 0.2)]),
    )
    reverb_without = _dword_values_after(build_flp(without_layer), _CH_REVERB)
    reverb_with = _dword_values_after(build_flp(with_layer), _CH_REVERB)
    assert len(reverb_with) == len(reverb_without) + 1
    assert melody_channel_reverb_mix() in reverb_with


def test_snare_layer_gets_no_reverb_without_space_mix():
    from flp_writer import _CH_REVERB

    pattern = _minimal_pattern(tracks=_base_snare_layer_tracks(snare_layer=[_note(1.0, 0.2)]))
    assert _dword_values_after(build_flp(pattern), _CH_REVERB) == []


@pytest.mark.legacy_mix
def test_flp_routes_channels_to_named_mixer_inserts():
    """Template architecture: each channel routes to its named insert bus."""
    blob = build_flp(_minimal_pattern())
    ids = _event_ids(blob)
    assert ids.count(_CH_ROUTED_TO) == 6
    routes = []
    stream = _event_stream(blob)
    offset = 0
    while offset < len(stream):
        eid = stream[offset]
        offset += 1
        if eid < 64:
            val = stream[offset]
            offset += 1
            if eid == _CH_ROUTED_TO:
                routes.append(val)
        elif eid < 128:
            offset += 2
        elif eid < 192:
            offset += 4
        else:
            length = 0
            shift = 0
            while True:
                byte = stream[offset]
                offset += 1
                length |= (byte & 0x7F) << shift
                if byte & 0x80 == 0:
                    break
                shift += 7
            offset += length
    # melody, kick, sub_808, hi_hats, clap, snare
    assert routes == [4, 1, 2, 3, 6, 5]

    labels = [text for eid, text in _text_events(blob) if eid == _INSERT_NAME]
    assert labels[0] == "Master"
    assert "PLG Kick" in labels
    assert "PLG Sub 808" in labels


@pytest.mark.legacy_mix
def test_mixer_insert_names_colors_and_channel_routes_share_one_lane_map():
    from flp_writer import _INSERT_COLOR, _PLG_COLORS

    data = _minimal_pattern(
        tracks={
            "melody_lead": [_note(0.0, 1.0)],
            "kick": [_note(0.0, 0.25)],
            "808_bass": [{**_note(0.0, 1.0), "note": "C2"}],
            "hi_hats": [_note(0.5, 0.25)],
            "clap": [_note(1.0, 0.25)],
            "snare": [_note(1.0, 0.25)],
        }
    )
    blob = build_flp(data)
    stream = _event_stream(blob)
    offset = 0
    current_insert = ""
    colors: dict[str, int] = {}
    while offset < len(stream):
        eid = stream[offset]
        offset += 1
        if eid < 64:
            offset += 1
        elif eid < 128:
            offset += 2
        elif eid < 192:
            value = struct.unpack_from("<I", stream, offset)[0]
            offset += 4
            if eid == _INSERT_COLOR and current_insert:
                colors[current_insert] = value
        else:
            length = 0
            shift = 0
            while True:
                byte = stream[offset]
                offset += 1
                length |= (byte & 0x7F) << shift
                if byte & 0x80 == 0:
                    break
                shift += 7
            raw = bytes(stream[offset : offset + length])
            offset += length
            if eid == _INSERT_NAME:
                current_insert = raw.decode("utf-16-le").rstrip("\x00")

    assert colors["PLG Kick"] == _PLG_COLORS["kick"]
    assert colors["PLG 808 Bass"] == _PLG_COLORS["808_bass"]
    assert colors["PLG Hi-Hats"] == _PLG_COLORS["hi_hats"]
    assert colors["PLG Clap"] == _PLG_COLORS["clap"]
    assert colors["PLG Snare"] == _PLG_COLORS["snare"]


def test_midi_project_compacts_mixer_to_active_colored_lanes_only():
    from flp_template import active_export_channels
    from flp_writer import _PLG_COLORS

    data = _minimal_pattern(
        plg_midi_melody=True,
        tracks={
            "melody_lead": [_note(0.0, 1.0)],
            "kick": [_note(16.0, 0.25)],
            "808_bass": [{**_note(16.0, 1.0), "note": "C2"}],
            "hi_hats": [_note(16.5, 0.25)],
            "clap": [_note(17.0, 0.25)],
            "snare": [_note(17.0, 0.25)],
            "snare_layer": [_note(17.0, 0.25)],
            # Disabled MIDI-mode lanes must never appear as named inserts.
            "counter_melody": [],
            "fx": [],
            "chords": [],
            "texture": [],
            "cowbell": [],
        },
    )
    channels = active_export_channels(data)
    assert channels == [
        "melody_lead", "kick", "808_bass", "hi_hats", "clap", "snare", "snare_layer"
    ]

    blob = build_flp(data)
    routes = []
    stream = _event_stream(blob)
    offset = 0
    while offset < len(stream):
        eid = stream[offset]
        offset += 1
        if eid < 64:
            value = stream[offset]
            offset += 1
            if eid == _CH_ROUTED_TO:
                routes.append(value)
        elif eid < 128:
            offset += 2
        elif eid < 192:
            offset += 4
        else:
            length = 0
            shift = 0
            while True:
                byte = stream[offset]
                offset += 1
                length |= (byte & 0x7F) << shift
                if byte & 0x80 == 0:
                    break
                shift += 7
            offset += length
    assert routes == [1, 2, 3, 4, 5, 6, 7]

    labels = [text for eid, text in _text_events(blob) if eid == _INSERT_NAME]
    assert labels[:8] == [
        "Master", "PLG MIDI", "PLG Kick", "PLG 808 Bass", "PLG Hi-Hats",
        "PLG Clap", "PLG Snare", "PLG Snare Layer",
    ]
    for forbidden in ("PLG Counter", "PLG FX", "PLG Chords", "PLG Texture", "PLG Cowbell", "PLG Sub 808"):
        assert forbidden not in labels

    params = _data_payloads(blob, _MIXER_PARAMS)[0]
    active_param_inserts = {
        (struct.unpack_from("<4sBBHi", params, index)[3] >> 6) & 0x7F
        for index in range(0, len(params), 12)
    }
    assert set(range(8)) <= active_param_inserts
    assert 13 not in active_param_inserts


def _data_payloads(payload: bytes, event_id: int) -> list[bytes]:
    stream = _event_stream(payload)
    out: list[bytes] = []
    offset = 0
    while offset < len(stream):
        eid = stream[offset]
        offset += 1
        if eid < 64:
            offset += 1
        elif eid < 128:
            offset += 2
        elif eid < 192:
            offset += 4
        else:
            length = 0
            shift = 0
            while True:
                byte = stream[offset]
                offset += 1
                length |= (byte & 0x7F) << shift
                if byte & 0x80 == 0:
                    break
                shift += 7
            chunk = bytes(stream[offset : offset + length])
            offset += length
            if eid == event_id:
                out.append(chunk)
    return out


def _dword_values_after(payload: bytes, event_id: int) -> list[int]:
    stream = _event_stream(payload)
    out: list[int] = []
    offset = 0
    while offset < len(stream):
        eid = stream[offset]
        offset += 1
        if eid < 64:
            offset += 1
        elif eid < 128:
            offset += 2
        elif eid < 192:
            if eid == event_id:
                out.append(struct.unpack_from("<I", stream, offset)[0])
            offset += 4
        else:
            length = 0
            shift = 0
            while True:
                byte = stream[offset]
                offset += 1
                length |= (byte & 0x7F) << shift
                if byte & 0x80 == 0:
                    break
                shift += 7
            offset += length
    return out


def _insert_flags(payload: bytes) -> list[bytes]:
    return _data_payloads(payload, _INSERT_FLAGS)


def test_flp_inserts_dock_left_and_master_has_separator():
    blob = build_flp(_minimal_pattern())
    flags = _insert_flags(blob)
    assert flags
    assert all(f[4:8] == b"\x0c\x00\x00\x00" or f[4:8] == b"\x0c\x04\x00\x00" for f in flags)
    assert any(f[4:8] == b"\x0c\x04\x00\x00" for f in flags)


def test_flp_embeds_mixer_gain_staging():
    blob = build_flp(_minimal_pattern())
    assert _MIXER_PARAMS in _event_ids(blob)
    payloads = _data_payloads(blob, _MIXER_PARAMS)
    assert len(payloads) == 1
    body = payloads[0]
    assert len(body) % 12 == 0
    items = [struct.unpack("<4sBBHi", body[i : i + 12]) for i in range(0, len(body), 12)]
    by_param: dict[tuple[int, int], int] = {}
    for _pad, pid, _u1, channel_data, msg in items:
        insert_idx = (channel_data - 0x7000) >> 6 if pid == _PARAM_VOLUME else (channel_data >> 6) & 0x7F
        by_param[(insert_idx, pid)] = msg
    from mix_gain_staging import DEFAULT_MIXER_FADER_VALUES

    assert by_param[(0, _PARAM_VOLUME)] == DEFAULT_MIXER_FADER_VALUES["master"]
    assert by_param[(1, _PARAM_VOLUME)] == DEFAULT_MIXER_FADER_VALUES["kick"]
    assert by_param[(4, _PARAM_VOLUME)] == DEFAULT_MIXER_FADER_VALUES["melody_lead"]


def test_flp_has_multi_track_playlist_clips():
    """Each instrument gets its own playlist track (melody bar 1, drums from bar 9)."""
    from flp_writer import _ARR_NEW, _PLAYLIST, _TRACK_DATA

    data = _minimal_pattern(plg_total_bars=48)
    blob = build_flp(data)
    ids = _event_ids(blob)
    assert ids.count(_ARR_NEW) == 1
    assert ids.count(_PLAYLIST) == 1
    assert ids.count(_TRACK_DATA) == 500

    payloads = _data_payloads(blob, _PLAYLIST)
    assert len(payloads) == 1
    item = payloads[0]
    # 60-byte items (2026-07 fix): FL Studio 2025 requires the whole PLAYLIST
    # array to be uniform 60-byte entries ("New in FL 21" per pyflp's own
    # PlaylistEvent struct) — 32-byte items caused FL to read every clip
    # after the first at the wrong offset and silently drop it. Confirmed
    # against a real FL Studio 2025 install: with 32-byte items only the
    # first clip ever appeared in the Playlist.
    assert len(item) % 60 == 0
    assert len(item) >= 60 * 5  # melody + kick + sub + hats + clap (+ snare)
    first = item[0:60]
    pos, pattern_base, item_index, length, track_rvidx, _grp = struct.unpack_from(
        "<IHHIHH", first, 0
    )
    assert pos == 0
    assert pattern_base == 20480
    assert item_index == 20481  # pattern #1 = melody
    assert track_rvidx == 499  # playlist track 1


def test_every_playlist_item_decodes_correctly_not_just_the_first():
    """2026-07 regression net for the 32-vs-60-byte item-size bug: with the
    old 32-byte stride, only the FIRST item in the array ever decoded
    correctly in real FL Studio — every item after it was read at the
    wrong byte offset and silently dropped from the Playlist (confirmed by
    opening a real generated .flp in FL Studio 2025: only the melody clip
    ever appeared, no matter how many instruments were active). This reads
    the array at the item's OWN declared size (not a hardcoded 32) and
    checks every item lands on its own distinct, correct track — the exact
    thing that broke before."""
    from flp_template import PLAYLIST_TRACK_FOR, active_export_channels, build_playlist_clips
    from flp_writer import _PLAYLIST

    data = _minimal_pattern(
        tracks={
            "kick": [_note()],
            "snare": [_note(1.0, 0.5)],
            "clap": [_note(1.0, 0.5)],
            "sub_808": [_note(0.0, 2.0)],
            "hi_hats": [_note(0.25, 0.25)],
            "melody_lead": [_note(0.0, 4.0)],
        },
        plg_total_bars=48,
    )
    blob = build_flp(data)
    clips = build_playlist_clips(data)
    assert len(clips) >= 5

    payloads = _data_payloads(blob, _PLAYLIST)
    item = payloads[0]
    n_items = len(item) // 60
    assert n_items == len(clips)
    assert len(item) % 60 == 0  # uniform 60-byte stride, no leftover bytes

    by_pattern_iid = {c.pattern_iid: c for c in clips}
    seen_tracks = set()
    for i in range(n_items):
        raw = item[i * 60 : i * 60 + 60]
        pos, pattern_base, item_index, length, track_rvidx, _grp = struct.unpack_from("<IHHIHH", raw, 0)
        assert pattern_base == 20480
        pattern_iid = item_index - pattern_base
        clip = by_pattern_iid[pattern_iid]
        track_index = 499 - track_rvidx
        assert track_index == clip.playlist_track, clip.track_key
        assert track_index not in seen_tracks, f"track {track_index} used by more than one clip"
        seen_tracks.add(track_index)
        assert track_index == PLAYLIST_TRACK_FOR[clip.track_key]


def test_arranged_playlist_is_sectioned_but_preserves_every_note_once():
    """Playlist ergonomics must not alter the generated musical material."""
    from flp_template import build_playlist_clips

    data = _minimal_pattern(
        plg_total_bars=12,
        plg_arrangement=[
            {"name": "intro", "occurrence": 1, "start_bar": 0, "bars": 4},
            {"name": "verse", "occurrence": 1, "start_bar": 4, "bars": 4},
            {"name": "hook", "occurrence": 1, "start_bar": 8, "bars": 4},
        ],
        tracks={
            # The 808 crosses verse -> hook: the verse clip keeps the exact
            # tail while the hook remains independently editable.
            "kick": [_note(16.0, 0.25), _note(33.0, 0.25)],
            "sub_808": [_note(30.0, 4.5)],
            "hi_hats": [_note(16.5, 0.25), _note(32.5, 0.25)],
            "snare": [],
            "clap": [],
            "melody_lead": [_note(0.0, 2.0), _note(17.0, 1.0), _note(34.0, 1.0)],
        },
    )

    clips = build_playlist_clips(data)
    kick = [clip for clip in clips if clip.track_key == "kick"]
    bass = [clip for clip in clips if clip.track_key == "sub_808"]
    melody = [clip for clip in clips if clip.track_key == "melody_lead"]

    assert [(clip.start_bar, clip.length_bars) for clip in kick] == [(4, 4), (8, 4)]
    assert len(bass) == 1
    assert bass[0].start_bar == 4
    assert bass[0].source_end_bar == 8
    assert bass[0].length_bars == 4.625
    assert [(clip.start_bar, clip.length_bars) for clip in melody] == [(0, 4), (4, 4), (8, 4)]

    for key in ("kick", "sub_808", "hi_hats", "melody_lead"):
        source = data["tracks"][key]
        lane_clips = [clip for clip in clips if clip.track_key == key]
        rendered = sum(
            1
            for note in source
            for clip in lane_clips
            if clip.start_bar * 4 <= note["time_step"] < clip.source_end_bar * 4
        )
        assert rendered == len(source), key
    assert data["plg_playlist_layout_audit"]["passed"] is True
    assert data["plg_playlist_layout_audit"]["fallback_lanes"] == []
    assert data["plg_playlist_layout_audit"]["tail_overlap_clips"] == 1


def test_playlist_coverage_gate_falls_back_for_arrangement_gap():
    """Malformed form metadata must never make a valid note disappear."""
    from flp_template import build_playlist_clips

    data = _minimal_pattern(
        plg_total_bars=12,
        plg_arrangement=[
            {"name": "intro", "start_bar": 0, "bars": 4},
            # Deliberate 4-bar metadata hole: a note lives in it.
            {"name": "hook", "start_bar": 8, "bars": 4},
        ],
        tracks={
            "kick": [_note(17.0, 0.25)],
            "sub_808": [],
            "hi_hats": [],
            "snare": [],
            "clap": [],
            "melody_lead": [_note(0.0, 1.0), _note(17.0, 1.0), _note(33.0, 1.0)],
        },
    )

    clips = build_playlist_clips(data)
    melody = [clip for clip in clips if clip.track_key == "melody_lead"]
    assert len(melody) == 1
    assert melody[0].start_bar == 0
    assert melody[0].length_bars == 12
    assert data["plg_playlist_layout_audit"]["passed"] is True
    assert "melody_lead" in data["plg_playlist_layout_audit"]["fallback_lanes"]


def test_fallback_clip_for_a_drum_lane_does_not_stretch_to_the_whole_song():
    """Reported bug: a percussion lane's single fallback clip (used when one
    onset falls outside every arrangement section) used to span the WHOLE
    remaining song regardless of where that lane's real content stopped —
    "PLG Snare Layer Main 6" visually stretching to a three-digit bar with a
    long empty tail, even though its last actual note was decades of bars
    earlier. The clip must now stay close to the lane's own last onset."""
    from flp_template import build_playlist_clips

    data = _minimal_pattern(
        plg_total_bars=40,
        plg_arrangement=[
            {"name": "verse", "start_bar": 0, "bars": 10},
            {"name": "hook", "start_bar": 10, "bars": 10},
            # Deliberate metadata gap (bars 20-25): one stray onset lives in
            # it, which is enough to fail _windows_cover_notes_once for this
            # lane and trigger the single-clip fallback.
            {"name": "verse", "start_bar": 25, "bars": 15},
        ],
        tracks={
            "kick": [],
            "sub_808": [],
            "hi_hats": [],
            "snare": [_note(t, 0.2) for t in (8.0, 18.0, 28.0, 38.0, 48.0)],
            "clap": [],
            "snare_layer": [_note(t, 0.2) for t in (8.0, 18.0, 28.0, 38.0, 48.0)] + [_note(21.0 * 4.0, 0.08)],
            "melody_lead": [],
        },
    )

    clips = build_playlist_clips(data)
    layer = [clip for clip in clips if clip.track_key == "snare_layer"]
    snare = [clip for clip in clips if clip.track_key == "snare"]

    assert "snare_layer" in data["plg_playlist_layout_audit"]["fallback_lanes"]
    assert len(layer) == 1
    # The stray onset (bar 21) is the last real content; the clip must end
    # a short, bounded distance after it — never anywhere near bar 40.
    assert 21 < layer[0].start_bar + layer[0].length_bars <= 25
    # snare itself stays cleanly sectioned (no stray onset), unaffected.
    assert len(snare) == 2
    assert "snare" not in data["plg_playlist_layout_audit"]["fallback_lanes"]
    assert data["plg_playlist_layout_audit"]["passed"] is True
    # Every real onset is still inside its clip — the tighter bound never
    # drops content, it only stops padding past it.
    assert data["plg_playlist_layout_audit"]["assigned_notes"] == data["plg_playlist_layout_audit"]["source_notes"]


def test_fallback_clip_with_no_notes_still_gets_a_legal_length():
    from flp_template import _fallback_clip_end_bar

    assert _fallback_clip_end_bar([], start_bar=4, bars=40) == 40


def test_drum_pattern_bank_reuses_main_and_writes_song_markers():
    from flp_template import build_playlist_clips
    from flp_writer import _PAT_NEW, _PLAYLIST, _TIME_MARKER_NAME, _TIME_MARKER_POSITION

    data = _minimal_pattern(
        plg_total_bars=20,
        plg_arrangement=[
            {"name": "intro", "occurrence": 1, "start_bar": 0, "bars": 4},
            {"name": "verse", "occurrence": 1, "start_bar": 4, "bars": 4},
            {"name": "hook", "occurrence": 1, "start_bar": 8, "bars": 4},
            {"name": "verse", "occurrence": 2, "start_bar": 12, "bars": 4},
            {"name": "outro", "occurrence": 1, "start_bar": 16, "bars": 4},
        ],
        tracks={
            "kick": [_note(16.0, 0.25), _note(48.0, 0.25)],
            "sub_808": [],
            "hi_hats": [],
            "snare": [],
            "clap": [],
            "melody_lead": [_note(0.0, 1.0)],
        },
    )

    clips = build_playlist_clips(data)
    kicks = [clip for clip in clips if clip.track_key == "kick"]
    assert len(kicks) == 2
    assert kicks[0].pattern_iid == kicks[1].pattern_iid
    assert kicks[0].pattern_name == "PLG Kick Main"
    assert data["plg_playlist_layout_audit"]["reused_clip_count"] == 1

    blob = build_flp(data)
    assert len(_word_values_after(blob, _PAT_NEW)) == len({clip.pattern_iid for clip in clips})
    assert len(_data_payloads(blob, _PLAYLIST)[0]) // 60 == len(clips)
    assert _dword_values_after(blob, _TIME_MARKER_POSITION) == [0, 15360, 30720, 46080, 61440]
    marker_names = [raw.decode("utf-16-le").rstrip("\x00") for raw in _data_payloads(blob, _TIME_MARKER_NAME)]
    assert marker_names == ["Intro", "Verse", "Hook", "Verse 2", "Outro"]


def test_last_playlist_clip_uses_96_ppq_and_explicit_trim():
    """Regression for the visible 69->145 bar Snare Layer tail in real FL."""
    from flp_template import build_playlist_clips
    from flp_writer import _PLAYLIST, _PLAYLIST_LENGTH_PPQ

    data = _minimal_pattern(
        plg_total_bars=80,
        plg_arrangement=[
            {"name": "verse", "occurrence": 1, "start_bar": 68, "bars": 8},
        ],
        tracks={
            "kick": [],
            "sub_808": [],
            "hi_hats": [],
            "snare": [],
            "clap": [],
            "snare_layer": [_note(69 * 4.0, 0.25)],
            "melody_lead": [],
        },
    )
    clips = build_playlist_clips(data)
    assert len(clips) == 1
    blob = build_flp(data)
    raw = _data_payloads(blob, _PLAYLIST)[0]
    position, _base, _item, length = struct.unpack_from("<IHHI", raw, 0)
    assert position == 68 * 4 * PPQ
    assert length == 8 * 4 * _PLAYLIST_LENGTH_PPQ
    assert length == 3072  # not 30720 (which FL displays as 80 bars)
    assert struct.unpack_from("<II", raw, 24) == (0, length * PPQ // _PLAYLIST_LENGTH_PPQ)


def test_every_lane_last_clip_ends_within_arbitrary_song_length():
    from flp_template import build_playlist_clips
    from flp_writer import _PLAYLIST, _PLAYLIST_LENGTH_PPQ

    total = 77
    lanes = ("kick", "808_bass", "hi_hats", "clap", "snare", "snare_layer")
    tracks = {lane: [_note(72 * 4.0, 0.25)] for lane in lanes}
    tracks["melody_lead"] = [_note(0.0, 0.25)]
    data = _minimal_pattern(
        plg_total_bars=total,
        plg_arrangement=[{"name": "outro", "start_bar": 72, "bars": 5}],
        tracks=tracks,
    )
    clips = build_playlist_clips(data)
    assert data["plg_playlist_layout_audit"]["visual_bounds_ok"] is True
    assert max(clip.start_bar + clip.length_bars for clip in clips) <= total

    raw = _data_payloads(build_flp(data), _PLAYLIST)[0]
    for offset in range(0, len(raw), 60):
        position, _base, _item, length = struct.unpack_from("<IHHI", raw, offset)
        start_bar = position / (4 * PPQ)
        length_bars = length / (4 * _PLAYLIST_LENGTH_PPQ)
        assert start_bar + length_bars <= total


def test_note_rack_channel_matches_actual_channel_index_when_a_channel_is_skipped():
    """2026-07 audit: a note's rack_channel field used to come from
    flp_template.rack_index(key) — the key's FIXED position in
    TEMPLATE_CHANNELS. But _CH_NEW channel indices are assigned by
    enumerate() over active_export_channels(data), which SKIPS any non-core
    channel with no notes (e.g. "fx" when this generation has no crash/riser
    content). Every channel listed AFTER a skipped one in TEMPLATE_CHANNELS
    then shifts down by one _CH_NEW slot, while rack_index() kept returning
    the unshifted position — silently routing a track's notes to the WRONG
    channel, or past the actual channel count entirely for trailing tracks.

    Confirmed as the real cause of FL Studio's "There are invalid notes in
    this project" warning + a FLEngine crash on Play, on a real generation
    with chords/texture content but no fx content. This reproduces the exact
    shape: chords and texture present, fx/snare_layer/counter_melody/cowbell
    absent (all skipped from active_export_channels)."""
    data = _minimal_pattern(
        tracks={
            "kick": [_note()],
            "snare": [_note(1.0, 0.5)],
            "clap": [_note(1.0, 0.5)],
            "sub_808": [_note(0.0, 2.0)],
            "hi_hats": [_note(0.25, 0.25)],
            "melody_lead": [_note(0.0, 4.0)],
            "chords": [_note(0.0, 1.0)],
            "texture": [_note(0.0, 1.0)],
        },
    )
    blob = build_flp(data)

    from flp_template import active_export_channels, build_playlist_clips
    from flp_writer import _CH_NEW, _PAT_NEW, _PAT_NOTES

    channels = active_export_channels(data)
    assert "fx" not in channels  # sanity: the gap this test targets is real
    channel_index = {key: i for i, key in enumerate(channels)}
    # Pattern IDs are deliberately unique per Playlist clip now. That keeps
    # section-local patterns independently editable in FL Studio.
    iid_to_key = {clip.pattern_iid: clip.track_key for clip in build_playlist_clips(data)}

    stream = _event_stream(blob)
    offset = 0
    n_channels = 0
    cur_pat_iid = None
    rack_by_pattern: dict[int, set[int]] = {}
    while offset < len(stream):
        eid = stream[offset]
        offset += 1
        if eid < 64:
            offset += 1
        elif eid < 128:
            val = struct.unpack_from("<H", stream, offset)[0]
            offset += 2
            if eid == _CH_NEW:
                n_channels += 1
            elif eid == _PAT_NEW:
                cur_pat_iid = val
        elif eid < 192:
            offset += 4
        else:
            length = 0
            shift = 0
            while True:
                byte = stream[offset]
                offset += 1
                length |= (byte & 0x7F) << shift
                if byte & 0x80 == 0:
                    break
                shift += 7
            raw = bytes(stream[offset : offset + length])
            offset += length
            if eid == _PAT_NOTES:
                racks = {
                    struct.unpack_from("<IHHI", raw, i * 24)[2]
                    for i in range(len(raw) // 24)
                }
                rack_by_pattern[cur_pat_iid] = racks

    assert rack_by_pattern  # sanity: notes were actually found
    for iid, racks in rack_by_pattern.items():
        key = iid_to_key[iid]
        assert racks == {channel_index[key]}, (key, racks, channel_index[key])
        assert all(r < n_channels for r in racks), (key, racks, n_channels)


@pytest.mark.legacy_mix
def test_flp_master_soft_clipper_and_melody_forward():
    blob = build_flp(
        _minimal_pattern(
            plg_fl_fx=True,
            plg_mixer_levels={
                "kick": 12800,
                "sub_808": 12800,
                "melody_lead": _db_to_fl_volume(-6.0),  # humanize's vocal duck...
            },
        )
    )
    plugin_names = [text for eid, text in _text_events(blob) if eid == _PLUGIN_INTERNAL]
    assert "Fruity Soft Clipper" in plugin_names
    assert _PLUGIN_WRAPPER in _event_ids(blob)

    # ...is overridden by the dB gain-staging table: kick is king, the driven
    # 808 glues at -6, the melody sets the vibe from the back (-12).
    from flp_writer import _DEFAULT_CHANNEL_VOL, _channel_level, _channels_with_layout

    vols = _word_values_after(blob, _CH_VOL)
    assert _channel_level("kick", {}, clipper_glue=True) in vols
    assert _channel_level("sub_808", {}, clipper_glue=True) in vols
    assert _DEFAULT_CHANNEL_VOL["melody_lead"] in vols       # -12 dB vibe layer
    assert _DEFAULT_CHANNEL_VOL["hi_hats"] in vols           # hats tamed
    assert _DEFAULT_CHANNEL_VOL["kick"] == max(_DEFAULT_CHANNEL_VOL.values())
    # every channel got an explicit volume (no silent FL-default lanes)
    assert len(vols) == len(_channels_with_layout(_minimal_pattern()))


def test_sub_808_channel_volume_at_commercial_ratio():
    from flp_writer import _DEFAULT_CHANNEL_VOL

    # 2026-07 producer-law correction: kick and 808 are NOT at unity. The
    # kick is the peak king; the 808 is held just below it while the channel
    # preamp/clipper establishes the audible drive.
    assert _DEFAULT_CHANNEL_VOL["kick"] == _db_to_fl_volume(0.75)
    assert _DEFAULT_CHANNEL_VOL["sub_808"] == _db_to_fl_volume(-0.5)
    assert _DEFAULT_CHANNEL_VOL["808_bass"] == _db_to_fl_volume(-0.5)
    assert _DEFAULT_CHANNEL_VOL["melody_lead"] == _db_to_fl_volume(-12.0)
    assert _DEFAULT_CHANNEL_VOL["kick"] > _DEFAULT_CHANNEL_VOL["sub_808"]
    assert _DEFAULT_CHANNEL_VOL["kick"] > _DEFAULT_CHANNEL_VOL["808_bass"]


def test_sub_808_sampler_resamples_with_time_zero():
    """The 808 must pitch by RESAMPLING, not by a stretch algorithm.

    Changed 2026-08-15 (was StretchMode.Stretch = -1). FL itself writes
    mode=0 on every one-shot sample channel in the real FL-authored projects
    available here — tests/fixtures/FL_20.8.4.flp and FL Studio 2025's factory
    NewStuff.flp — including "SFX SubBass Drop.wav"; -1 appears in none of
    them and cannot be selected in FL's TIME panel. See the project issue log (not included) §0j.
    """
    from pyflp.channel import ParametersEvent, StretchMode

    from flp_writer import _CH_PARAMETERS, _SUB_808_STRETCH_PARAMS

    blob = build_flp(_minimal_pattern())
    params = _data_payloads(blob, _CH_PARAMETERS)
    assert _SUB_808_STRETCH_PARAMS in params
    parsed = ParametersEvent.STRUCT.parse(_SUB_808_STRETCH_PARAMS, len=len(_SUB_808_STRETCH_PARAMS))
    assert parsed.get("stretching.mode") == StretchMode.Resample
    assert parsed.get("stretching.time") == (0, 0, 0)
    # Nothing else in the sampler page moved with it.
    assert parsed.get("stretching.multiplier") == 1.0
    assert parsed.get("stretching.pitch") == 0
    from flp_writer import (
        _CH_CUT_GROUP,
        _CH_POLYPHONY,
        _SUB_808_CUT_ITSELF,
        _SUB_808_POLYPHONY_MONO,
    )

    blob = build_flp(_minimal_pattern())
    cut_vals = _dword_values_after(blob, _CH_CUT_GROUP)
    poly_payloads = _data_payloads(blob, _CH_POLYPHONY)
    assert int.from_bytes(_SUB_808_CUT_ITSELF, "little") in cut_vals
    assert _SUB_808_POLYPHONY_MONO in poly_payloads


def test_melody_sampler_uses_stretch_mode_not_resample():
    from pyflp.channel import ParametersEvent, StretchMode

    from flp_writer import _CH_PARAMETERS, _MELODY_STRETCH_PARAMS

    blob = build_flp(_minimal_pattern())
    params = _data_payloads(blob, _CH_PARAMETERS)
    assert _MELODY_STRETCH_PARAMS in params
    parsed = ParametersEvent.STRUCT.parse(_MELODY_STRETCH_PARAMS, len=len(_MELODY_STRETCH_PARAMS))
    assert parsed.get("stretching.mode") == StretchMode.Stretch
    assert parsed.get("stretching.time") == (0, 0, 0)


def test_counter_melody_sampler_uses_stretch_time_zero():
    from pyflp.channel import ParametersEvent, StretchMode

    from flp_writer import _CH_PARAMETERS, _MELODY_STRETCH_PARAMS

    pattern = _minimal_pattern()
    pattern["tracks"]["counter_melody"] = [
        {"time_step": 12.0, "note": "C6", "length": 1.0, "velocity": 80}
    ]
    blob = build_flp(pattern)
    params = _data_payloads(blob, _CH_PARAMETERS)
    assert _MELODY_STRETCH_PARAMS in params
    parsed = ParametersEvent.STRUCT.parse(_MELODY_STRETCH_PARAMS, len=len(_MELODY_STRETCH_PARAMS))
    assert parsed.get("stretching.mode") == StretchMode.Stretch
    assert parsed.get("stretching.time") == (0, 0, 0)


def test_melody_channel_gets_a_real_volume_envelope():
    """Without this, FL's envelope page is untouched/disabled: raw sample
    starts at full amplitude (audible click if not a zero-crossing) and gets
    hard-cut on note-off with no fade (digital crackle) — reported live.
    melody_lead is a MANDATORY channel (not in OPTIONAL_TRACK_KEYS), so even
    a kick-only pattern still lays it out — sub_808, kick and clap are
    mandatory too, so their own 5-event gates are always present alongside
    melody's 5."""
    from flp_writer import _CH_ENVLFO, _MELODY_ENV_SLOT1, _MELODY_ENV_SLOT2

    pattern = {
        "bpm": 140,
        "tracks": {"melody_lead": [{"time_step": 0.0, "note": "C5", "length": 4.0, "velocity": 100}]},
    }
    blob = build_flp(pattern)
    payloads = _data_payloads(blob, _CH_ENVLFO)
    assert _MELODY_ENV_SLOT1 in payloads
    assert _MELODY_ENV_SLOT2 in payloads
    # sub_808's own 5-event gate + kick's 5-event gate + clap's 5-event gate
    # (all mandatory channels) + melody_lead's 5.
    assert len(payloads) == 20


def test_counter_melody_channel_gets_a_real_volume_envelope():
    from flp_writer import _CH_ENVLFO, _MELODY_ENV_SLOT1, _MELODY_ENV_SLOT2

    pattern = {
        "bpm": 140,
        "tracks": {"counter_melody": [{"time_step": 0.0, "note": "C6", "length": 2.0, "velocity": 90}]},
    }
    blob = build_flp(pattern)
    payloads = _data_payloads(blob, _CH_ENVLFO)
    assert _MELODY_ENV_SLOT1 in payloads
    assert _MELODY_ENV_SLOT2 in payloads
    # sub_808 (5, mandatory) + kick (5, mandatory) + clap (5, mandatory) +
    # melody_lead (5, mandatory) + counter_melody (5, optional but present
    # here since it has notes).
    assert len(payloads) == 25


def _sine_wav(path: Path, amp: float, seconds: float = 3.0, sr: int = 44100, freq: float = 440.0) -> None:
    t = np.arange(int(sr * seconds)) / sr
    x = (amp * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


def test_melody_gain_correction_boosts_a_quiet_file(tmp_path: Path):
    """A file recorded well under the -18dBFS target gets nudged UP so it
    doesn't sit quieter than every other candidate just because of how it was
    originally rendered."""
    from flp_writer import _melody_gain_correction_db

    quiet = tmp_path / "quiet.wav"
    _sine_wav(quiet, amp=0.05)  # ~ -29 dBFS, well under -18 target
    correction = _melody_gain_correction_db(quiet)
    assert correction > 0.0


def test_melody_gain_correction_cuts_a_loud_file(tmp_path: Path):
    from flp_writer import _melody_gain_correction_db

    loud = tmp_path / "loud.wav"
    _sine_wav(loud, amp=0.9)  # ~ -3.9 dBFS, well over -18 target
    correction = _melody_gain_correction_db(loud)
    assert correction < 0.0


def test_melody_gain_correction_clamped_and_missing_path_is_noop(tmp_path: Path):
    from flp_writer import _MELODY_GAIN_CORRECTION_CLAMP_DB, _melody_gain_correction_db

    very_loud = tmp_path / "very_loud.wav"
    _sine_wav(very_loud, amp=1.0)
    correction = _melody_gain_correction_db(very_loud)
    assert abs(correction) <= _MELODY_GAIN_CORRECTION_CLAMP_DB + 1e-6

    assert _melody_gain_correction_db(None) == 0.0
    assert _melody_gain_correction_db(tmp_path / "missing.wav") == 0.0


def test_melody_gain_correction_only_applies_to_melody_lanes(tmp_path: Path):
    """The correction is scoped to melody_lead/counter_melody — a kick lane
    at the same measured loudness must not get the same treatment."""
    from flp_writer import _channel_level

    quiet = tmp_path / "quiet.wav"
    _sine_wav(quiet, amp=0.05)
    kick_level = _channel_level("kick", {}, sample_path=quiet)
    melody_level = _channel_level("melody_lead", {}, sample_path=quiet)
    from flp_writer import _DEFAULT_CHANNEL_VOL

    assert kick_level == _DEFAULT_CHANNEL_VOL["kick"]  # untouched
    assert melody_level > _DEFAULT_CHANNEL_VOL["melody_lead"]  # boosted


@pytest.mark.legacy_mix
def test_build_flp_applies_melody_gain_correction_end_to_end(tmp_path: Path):
    """Two otherwise-identical patterns whose only difference is how loud the
    picked melody_lead .wav was originally recorded should NOT come out of
    build_flp at the same channel volume."""
    from flp_template import active_export_channels

    from flp_writer import _DEFAULT_CHANNEL_VOL, _CH_VOL

    quiet = tmp_path / "quiet_melody.wav"
    loud = tmp_path / "loud_melody.wav"
    _sine_wav(quiet, amp=0.05)
    _sine_wav(loud, amp=0.9)

    def _pattern_with(path: Path) -> dict:
        return {
            "bpm": 140,
            "tracks": {"melody_lead": [{"time_step": 0.0, "note": "C5", "length": 2.0, "velocity": 100}]},
            "plg_sound_paths": {"melody_lead": str(path)},
        }

    quiet_pattern = _pattern_with(quiet)
    loud_pattern = _pattern_with(loud)
    # build_flp orders/selects channels via flp_template.active_export_channels
    # (NOT _channels_with_layout, which only backs build_template_flp).
    melody_index = active_export_channels(quiet_pattern).index("melody_lead")
    assert active_export_channels(loud_pattern).index("melody_lead") == melody_index

    quiet_vol = _word_values_after(build_flp(quiet_pattern), _CH_VOL)[melody_index]
    loud_vol = _word_values_after(build_flp(loud_pattern), _CH_VOL)[melody_index]
    assert quiet_vol > _DEFAULT_CHANNEL_VOL["melody_lead"]
    assert loud_vol < _DEFAULT_CHANNEL_VOL["melody_lead"]
    assert quiet_vol > loud_vol


# --- per-note 808 register: pinned regression for the reported D3/E3 bug ----


@pytest.mark.legacy_mix
def test_prepare_pattern_for_flp_export_runs_all_finalization_rules(monkeypatch):
    """Canonical finalization stays outside byte serialization."""
    import flp_writer
    import mix_insert_fx

    calls: list[str] = []
    monkeypatch.setattr(
        flp_writer, "_repair_808_kick_pairs", lambda data: calls.append("pairs")
    )
    monkeypatch.setattr(
        flp_writer, "_apply_808_passport_register", lambda data: calls.append("register")
    )
    monkeypatch.setattr(
        mix_insert_fx, "configure_space_mix", lambda data: calls.append("space_mix")
    )

    prepare_pattern_for_flp_export({"tracks": {}})
    assert calls == ["pairs", "register", "space_mix"]


def test_808_passport_register_uses_pitch_class_map_not_a_flat_window(tmp_path, monkeypatch):
    """Every final 808 trigger preserves its pitch class in C2-C3."""
    from flp_writer import _apply_808_passport_register
    import sample_pitch_passport as spp

    ghoul_passport = {
        "status": "ok",
        "preferred_note_octave": 2,
        "register_layout": "top_anchor",
        "pitch_class_map": {
            "0": 36, "1": 25, "2": 26, "3": 27, "4": 28,
            "5": 29, "6": 30, "7": 31, "8": 32, "9": 33, "10": 34, "11": 35,
        },
        "safe_note_min": 24,
        "safe_note_max": 36,
    }
    sample = tmp_path / "GHOUL 808.wav"
    sample.write_bytes(b"RIFF0000WAVEfmt ")  # never actually read; passport is stubbed
    monkeypatch.setattr(spp, "sample_pitch_passport", lambda path: ghoul_passport)

    data = {
        "plg_sound_paths": {"808_bass": str(sample)},
        "tracks": {
            "808_bass": [
                {"time_step": 0.0, "note": "D3", "length": 1.0, "velocity": 116},
                {"time_step": 4.0, "note": "E3", "length": 1.0, "velocity": 116},
                {"time_step": 8.0, "note": "F3", "length": 1.0, "velocity": 116},
                {"time_step": 12.0, "note": "G3", "length": 1.0, "velocity": 116},
            ]
        },
    }
    _apply_808_passport_register(data)

    from music_theory import parse_note_name

    notes_by_time = {float(n["time_step"]): parse_note_name(str(n["note"])) for n in data["tracks"]["808_bass"]}
    assert notes_by_time[0.0] == 26  # D2
    assert notes_by_time[4.0] == 28  # E2
    assert notes_by_time[8.0] == 29  # F2
    assert notes_by_time[12.0] == 31  # G2
    assert data["plg_808_register"]["pitch_class_map"] == ghoul_passport["pitch_class_map"]


def test_808_passport_register_falls_back_to_flat_window_when_map_missing(tmp_path, monkeypatch):
    """A degenerate/unavailable passport (no pitch_class_map) must not crash
    or leave notes untouched — it keeps the old flat-window behavior."""
    from flp_writer import _apply_808_passport_register
    import sample_pitch_passport as spp

    degenerate_passport = {"status": "unavailable", "fl_root_note": 60}
    sample = tmp_path / "broken.wav"
    sample.write_bytes(b"not a real wav")
    monkeypatch.setattr(spp, "sample_pitch_passport", lambda path: degenerate_passport)

    data = {
        "plg_sound_paths": {"808_bass": str(sample)},
        "tracks": {"808_bass": [{"time_step": 0.0, "note": "D3", "length": 1.0, "velocity": 116}]},
    }
    _apply_808_passport_register(data)
    from music_theory import parse_note_name

    assert parse_note_name(str(data["tracks"]["808_bass"][0]["note"])) % 12 == 2  # pitch class preserved


def test_the_last_gate_repairs_a_published_sample_holding_the_wrong_audio(tmp_path, caplog, monkeypatch):
    """A whole-but-wrong file used to ship: the gate only asked whether the
    WAV was loadable. It now asks whether it is the audio this export chose,
    preserves the bad copy, and republishes (the project issue log (not included) 0y.1)."""
    import logging
    import struct

    import numpy as np

    import flp_writer
    from flp_writer import _verify_published_sample
    from wav_compat import convert_to_pcm24, describe_wav_damage, published_content_mismatch

    # The preserver copies aside into the user's data dir; keep the test out of it.
    monkeypatch.setattr(flp_writer, "_sample_quarantine_dir", lambda: tmp_path / "quarantine")

    def write_float(path, freq):
        n = 4410
        x = 0.8 * np.sin(2 * np.pi * freq * np.arange(n) / 44100)
        data = np.asarray(x, dtype="<f4").tobytes()
        fmt = struct.pack("<HHIIHH", 3, 1, 44100, 44100 * 4, 4, 32)
        path.write_bytes(
            b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(data)) + data
        )
        return path

    src = write_float(tmp_path / "src.wav", 32.5)
    other = write_float(tmp_path / "other.wav", 110.0)
    dest = tmp_path / "published_pcm24.wav"
    assert convert_to_pcm24(other, dest)          # the wrong audio is published
    assert describe_wav_damage(dest) is None      # and it is a perfectly good WAV

    with caplog.at_level(logging.ERROR):
        out = _verify_published_sample("808_bass", src, dest, converted=True)

    assert out == dest
    assert published_content_mismatch(src, dest) is None, "the gate did not repair it"
    assert any("WRONG AUDIO" in r.getMessage() for r in caplog.records),         "the mismatch was not reported"

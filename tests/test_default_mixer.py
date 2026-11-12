"""Default mixer: the old structure, nothing on it (unless PLG_LEGACY_MIX=1).

Default mode keeps the inserts named and coloured per lane and every channel
routed to its own insert, but loads no plugin, writes no gain-staging block and
no channel volume, no preamp/clip, and centres every note. The earlier mixer
automation is kept and covered by ``legacy_mix`` tests.
"""

import struct

import pytest

from flp_writer import (
    _CH_PREAMP,
    _CH_ROUTED_TO,
    _CH_VOL,
    _INSERT_COLOR,
    _INSERT_NAME,
    _MIXER_PARAMS,
    _PAN,
    _PLUGIN_INTERNAL,
    _note_struct,
    build_flp,
    legacy_mix_enabled,
)
from test_flp_writer import (
    _data_payloads,
    _dword_values_after,
    _event_ids,
    _event_stream,
    _minimal_pattern,
    _text_events,
    _word_values_after,
)


def _byte_values_after(blob: bytes, event_id: int) -> list[int]:
    stream = _event_stream(blob)
    values: list[int] = []
    offset = 0
    while offset < len(stream):
        eid = stream[offset]
        offset += 1
        if eid < 64:
            if eid == event_id:
                values.append(stream[offset])
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
    return values


def _loud_opt_ins() -> dict:
    """Everything that used to switch mixer work on."""
    return _minimal_pattern(plg_space_mix=True, plg_fl_fx=True, plg_midi_melody=True)


def test_default_is_not_legacy():
    assert legacy_mix_enabled() is False


def _mixer_records(payload: bytes):
    return [struct.unpack_from("<IHHi", payload, i) for i in range(0, len(payload), 12)]


@pytest.mark.skip(reason="requires third-party assets not included")
def test_default_mixer_has_no_plugins_and_one_mixer_state_block():
    blob = build_flp(_loud_opt_ins())
    assert [t for eid, t in _text_events(blob) if eid == _PLUGIN_INTERNAL] == []
    ids = _event_ids(blob)
    assert ids.count(_MIXER_PARAMS) == 1
    assert _CH_PREAMP not in ids


@pytest.mark.skip(reason="requires third-party assets not included")
def test_default_mixer_state_holds_flps_own_default_for_every_fader_field():
    """Defaults are read from FL's own file (126 of 127 inserts agree), not from our constants."""
    import collections
    from pathlib import Path

    fl_file = Path(__file__).parent / "fixtures" / "FL_20.8.4.flp"
    fl_state = _data_payloads(fl_file.read_bytes(), _MIXER_PARAMS)[0]
    modal = {}
    seen = collections.defaultdict(collections.Counter)
    for _pad, param, _slot, value in _mixer_records(fl_state):
        seen[param][value] += 1
    for param, counter in seen.items():
        modal[param] = counter.most_common(1)[0][0]

    state = _data_payloads(build_flp(_loud_opt_ins()), _MIXER_PARAMS)[0]
    records = _mixer_records(state)
    assert records
    for _pad, param, _slot, value in records:
        assert value == modal[param], (hex(param), value, modal[param])


@pytest.mark.skip(reason="requires third-party assets not included")
def test_default_mixer_state_only_changes_fader_fields_of_the_reference():
    from mix_gain_staging import _locked_mixer_params_payload

    reference = _mixer_records(_locked_mixer_params_payload())
    state = _mixer_records(_data_payloads(build_flp(_loud_opt_ins()), _MIXER_PARAMS)[0])
    assert len(state) == len(reference)
    changed_params = set()
    for (pad_a, param_a, slot_a, val_a), (pad_b, param_b, slot_b, val_b) in zip(reference, state):
        assert (pad_a, param_a, slot_a) == (pad_b, param_b, slot_b)
        if val_a != val_b:
            changed_params.add(param_a)
    assert changed_params <= {0x0000, 0x1FC0, 0x1FC1, 0x1FC2, 0x1FD0, 0x1FD1, 0x1FD2,
                              0x1FD8, 0x1FD9, 0x1FDA, 0x1FE0, 0x1FE1, 0x1FE2}
    assert 0x1F00 not in changed_params and 0x1F01 not in changed_params


def test_default_mixer_keeps_insert_names_colours_and_routing_of_the_legacy_mode(monkeypatch):
    def structure(legacy: bool):
        if legacy:
            monkeypatch.setenv("PLG_LEGACY_MIX", "1")
        else:
            monkeypatch.delenv("PLG_LEGACY_MIX", raising=False)
        blob = build_flp(_loud_opt_ins())
        return (
            [t for eid, t in _text_events(blob) if eid == _INSERT_NAME],
            _dword_values_after(blob, _INSERT_COLOR),
            _byte_values_after(blob, _CH_ROUTED_TO),
        )

    names, colours, routes = structure(legacy=False)
    assert (names, colours, routes) == structure(legacy=True)
    assert names[0] == "Master" and "PLG Kick" in names
    assert colours
    assert routes and set(routes) != {0}


def test_default_mixer_writes_no_channel_volume():
    assert _word_values_after(build_flp(_loud_opt_ins()), _CH_VOL) == []


def test_default_mixer_centres_note_pan():
    entry = {"time_step": 0.0, "note": "C3", "length": 0.25, "velocity": 90, "pan": 10}
    packed = _note_struct(0, entry, channel_key="hi_hats")
    assert struct.unpack("<IHHIHHBBBBBBBB", packed)[10] == _PAN


@pytest.mark.legacy_mix
def test_legacy_flag_restores_the_earlier_mixer():
    assert legacy_mix_enabled() is True
    blob = build_flp(_loud_opt_ins())
    plugin_names = [t for eid, t in _text_events(blob) if eid == _PLUGIN_INTERNAL]
    assert "Fruity Soft Clipper" in plugin_names
    assert _MIXER_PARAMS in _event_ids(blob)
    assert set(_byte_values_after(blob, _CH_ROUTED_TO)) != {0}
    assert _word_values_after(blob, _CH_VOL)


@pytest.mark.legacy_mix
def test_legacy_flag_keeps_note_pan():
    entry = {"time_step": 0.0, "note": "C3", "length": 0.25, "velocity": 90, "pan": 10}
    packed = _note_struct(0, entry, channel_key="hi_hats")
    assert struct.unpack("<IHHIHHBBBBBBBB", packed)[10] == 10


def _stub_export_neighbours(monkeypatch):
    """Isolate the one decision under test: validation/finalisation need a full pattern."""
    import flp_writer
    import midi_validate

    monkeypatch.setattr(midi_validate, "enforce_midi_mode", lambda pattern: pattern)
    monkeypatch.setattr(flp_writer, "prepare_pattern_for_flp_export", lambda pattern: None)


def test_export_artifacts_skip_vocal_ready_mix_by_default(monkeypatch):
    import mix_gain_staging
    from backend_core import _export_pattern_artifacts

    _stub_export_neighbours(monkeypatch)
    calls = []
    monkeypatch.setattr(mix_gain_staging, "attach_vocal_ready_mix", calls.append)
    _export_pattern_artifacts(_minimal_pattern(), export_midi=False)
    assert calls == []


@pytest.mark.legacy_mix
def test_export_artifacts_run_vocal_ready_mix_under_legacy_flag(monkeypatch):
    import mix_gain_staging
    from backend_core import _export_pattern_artifacts

    _stub_export_neighbours(monkeypatch)
    calls = []
    monkeypatch.setattr(mix_gain_staging, "attach_vocal_ready_mix", calls.append)
    _export_pattern_artifacts(_minimal_pattern(), export_midi=False)
    assert len(calls) == 1

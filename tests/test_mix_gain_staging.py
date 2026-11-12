import struct

from mix_gain_staging import (
    DEFAULT_808_BASS_INSERT_DB,
    DEFAULT_BACKGROUND_HIGH_CUT_HZ,
    DEFAULT_BACKGROUND_HIGH_GAIN,
    DEFAULT_BACKGROUND_INSERT_DB,
    DEFAULT_MASTER_AIR_GAIN,
    DEFAULT_MASTER_AIR_HZ,
    DEFAULT_MASTER_FADER_DB,
    DEFAULT_MELODY_INSERT_DB,
    DEFAULT_MELODY_LOW_CUT_HZ,
    DEFAULT_SUB_INSERT_DB,
    HATS_LOW_CUT_HZ,
    HATS_LOW_GAIN,
    KICK_LOW_CUT_HZ,
    KICK_LOW_GAIN,
    _PARAM_HIGH_FREQ,
    _PARAM_HIGH_GAIN,
    _PARAM_LOW_FREQ,
    _PARAM_LOW_GAIN,
    _PARAM_MID_FREQ,
    _PARAM_MID_GAIN,
    _PARAM_VOLUME,
    attach_vocal_ready_mix,
    background_bus_params,
    eq_freq_msg,
    gain_staging_payload,
    hats_low_cut_params,
    insert_fader_value,
    kick_low_cut_params,
    master_air_params,
    melody_high_cut_params,
    melody_low_cut_params,
    staging_from_pattern,
)


def _params_by_insert(blob: bytes) -> dict[tuple[int, int], int]:
    items = [struct.unpack("<4sBBHi", blob[i : i + 12]) for i in range(0, len(blob), 12)]
    by_param: dict[tuple[int, int], int] = {}
    for _pad, pid, _u1, channel_data, msg in items:
        insert_idx = (channel_data - 0x7000) >> 6 if pid == _PARAM_VOLUME else (channel_data >> 6) & 0x7F
        by_param[(insert_idx, pid)] = msg
    return by_param


def test_insert_fader_master_headroom():
    # Loud into the master soft clipper: -3 dB, not the old -11 dB whisper.
    val = insert_fader_value(DEFAULT_MASTER_FADER_DB)
    assert 8500 < val < 10500
    assert val < insert_fader_value(0.0)


def test_melody_insert_unity_channel_holds_minus_12():
    # Tecca/Travis: -12 dB lives on the channel rack; insert stays at unity
    # so we never stack -12 + insert tuck into plastic burial.
    assert DEFAULT_MELODY_INSERT_DB == 0.0
    assert insert_fader_value(DEFAULT_MELODY_INSERT_DB) == insert_fader_value(0.0)


def test_eq_freq_msg_increases_with_hz():
    assert eq_freq_msg(150) < eq_freq_msg(180) < eq_freq_msg(500)


def test_gain_staging_payload_has_master_kick_sub_melody():
    blob = gain_staging_payload({1, 2, 4})
    assert len(blob) % 12 == 0
    by_param = _params_by_insert(blob)
    from mix_gain_staging import DEFAULT_MIXER_FADER_VALUES

    assert by_param[(0, _PARAM_VOLUME)] == DEFAULT_MIXER_FADER_VALUES["master"]
    assert by_param[(1, _PARAM_VOLUME)] == DEFAULT_MIXER_FADER_VALUES["kick"]
    assert by_param[(2, _PARAM_VOLUME)] == DEFAULT_MIXER_FADER_VALUES["sub_808"]
    assert by_param[(4, _PARAM_VOLUME)] == DEFAULT_MIXER_FADER_VALUES["melody_lead"]
    assert by_param[(4, _PARAM_LOW_FREQ)] == eq_freq_msg(DEFAULT_MELODY_LOW_CUT_HZ)


def test_gain_staging_payload_tucks_808_bass_under_kick():
    blob = gain_staging_payload({1, 13})
    by_param = _params_by_insert(blob)

    from mix_gain_staging import DEFAULT_MIXER_FADER_VALUES

    assert by_param[(1, _PARAM_VOLUME)] == DEFAULT_MIXER_FADER_VALUES["kick"]
    assert by_param[(13, _PARAM_VOLUME)] == DEFAULT_MIXER_FADER_VALUES["808_bass"]


def test_bass808_low_band_cuts_sub_20_25hz_not_boosts():
    from mix_gain_staging import BASS808_LOW_CUT_GAIN, BASS808_LOW_CUT_HZ

    blob = gain_staging_payload({13})
    by_param = _params_by_insert(blob)

    assert BASS808_LOW_CUT_HZ <= 25.0
    assert BASS808_LOW_CUT_GAIN < 0  # a cut, not the old +350 boost shelf
    assert by_param[(13, _PARAM_LOW_FREQ)] == eq_freq_msg(BASS808_LOW_CUT_HZ)
    assert by_param[(13, _PARAM_LOW_GAIN)] == BASS808_LOW_CUT_GAIN


def test_kick_high_band_gets_crunch_boost_without_touching_low_mid():
    from mix_gain_staging import (
        KICK_BODY_GAIN,
        KICK_BODY_HZ,
        KICK_CRUNCH_GAIN,
        KICK_CRUNCH_HZ,
    )

    blob = gain_staging_payload({1})
    by_param = _params_by_insert(blob)

    assert 3000.0 <= KICK_CRUNCH_HZ <= 5000.0
    assert 0 < KICK_CRUNCH_GAIN <= 200  # +1.5-2 dB scale, a lift not a wall
    assert by_param[(1, _PARAM_HIGH_FREQ)] == eq_freq_msg(KICK_CRUNCH_HZ)
    assert by_param[(1, _PARAM_HIGH_GAIN)] == KICK_CRUNCH_GAIN
    # Existing low-cut + mid-punch bands are untouched by the new high band.
    assert by_param[(1, _PARAM_LOW_FREQ)] == eq_freq_msg(KICK_LOW_CUT_HZ)
    assert by_param[(1, _PARAM_LOW_GAIN)] == KICK_LOW_GAIN
    assert by_param[(1, _PARAM_MID_FREQ)] == eq_freq_msg(KICK_BODY_HZ)
    assert by_param[(1, _PARAM_MID_GAIN)] == KICK_BODY_GAIN


def test_snare_gets_low_cut_and_crunch_on_its_own_insert():
    from mix_gain_staging import (
        SNARE_CRUNCH_GAIN,
        SNARE_CRUNCH_HZ,
        SNARE_INSERT,
        SNARE_LOW_CUT_HZ,
        SNARE_LOW_GAIN,
    )

    blob = gain_staging_payload({SNARE_INSERT})
    by_param = _params_by_insert(blob)

    assert 150.0 <= SNARE_LOW_CUT_HZ <= 200.0
    assert SNARE_LOW_GAIN < 0  # a hard cut
    assert 3000.0 <= SNARE_CRUNCH_HZ <= 5000.0
    assert 0 < SNARE_CRUNCH_GAIN <= 200  # +1.5-2 dB lift, not a wall
    assert by_param[(SNARE_INSERT, _PARAM_LOW_FREQ)] == eq_freq_msg(SNARE_LOW_CUT_HZ)
    assert by_param[(SNARE_INSERT, _PARAM_LOW_GAIN)] == SNARE_LOW_GAIN
    assert by_param[(SNARE_INSERT, _PARAM_HIGH_FREQ)] == eq_freq_msg(SNARE_CRUNCH_HZ)
    assert by_param[(SNARE_INSERT, _PARAM_HIGH_GAIN)] == SNARE_CRUNCH_GAIN


def test_clap_gets_low_cut_and_density_bump_on_its_own_insert():
    from mix_gain_staging import (
        CLAP_DENSITY_GAIN,
        CLAP_DENSITY_HZ,
        CLAP_INSERT,
        CLAP_LOW_CUT_HZ,
        CLAP_LOW_GAIN,
    )

    blob = gain_staging_payload({CLAP_INSERT})
    by_param = _params_by_insert(blob)

    assert 200.0 <= CLAP_LOW_CUT_HZ <= 250.0
    assert CLAP_LOW_GAIN < 0  # a hard cut
    assert 1000.0 <= CLAP_DENSITY_HZ <= 2500.0
    assert 200 <= CLAP_DENSITY_GAIN <= 300  # +2-3 dB lift
    assert by_param[(CLAP_INSERT, _PARAM_LOW_FREQ)] == eq_freq_msg(CLAP_LOW_CUT_HZ)
    assert by_param[(CLAP_INSERT, _PARAM_LOW_GAIN)] == CLAP_LOW_GAIN
    # Density bump lives on MID (lower than kick/snare's HIGH-band crunch).
    assert by_param[(CLAP_INSERT, _PARAM_MID_FREQ)] == eq_freq_msg(CLAP_DENSITY_HZ)
    assert by_param[(CLAP_INSERT, _PARAM_MID_GAIN)] == CLAP_DENSITY_GAIN


def test_snare_layer_gets_low_cut_and_air_lift_on_its_own_insert():
    from mix_gain_staging import (
        SNARE_LAYER_CRUNCH_GAIN,
        SNARE_LAYER_CRUNCH_HZ,
        SNARE_LAYER_INSERT,
        SNARE_LAYER_LOW_CUT_HZ,
        SNARE_LAYER_LOW_GAIN,
    )

    blob = gain_staging_payload({SNARE_LAYER_INSERT})
    by_param = _params_by_insert(blob)

    assert 400.0 <= SNARE_LAYER_LOW_CUT_HZ <= 500.0
    assert SNARE_LAYER_LOW_GAIN < 0  # a hard cut
    assert 6000.0 <= SNARE_LAYER_CRUNCH_HZ <= 10000.0
    assert SNARE_LAYER_CRUNCH_GAIN > 0  # a lift, not a cut
    assert by_param[(SNARE_LAYER_INSERT, _PARAM_LOW_FREQ)] == eq_freq_msg(SNARE_LAYER_LOW_CUT_HZ)
    assert by_param[(SNARE_LAYER_INSERT, _PARAM_LOW_GAIN)] == SNARE_LAYER_LOW_GAIN
    assert by_param[(SNARE_LAYER_INSERT, _PARAM_HIGH_FREQ)] == eq_freq_msg(SNARE_LAYER_CRUNCH_HZ)
    assert by_param[(SNARE_LAYER_INSERT, _PARAM_HIGH_GAIN)] == SNARE_LAYER_CRUNCH_GAIN


def test_gain_staging_payload_has_kick_hats_low_cut_by_default():
    blob = gain_staging_payload({1, 3})
    items = [struct.unpack("<4sBBHi", blob[i : i + 12]) for i in range(0, len(blob), 12)]
    by_param: dict[tuple[int, int], int] = {}
    for _pad, pid, _u1, channel_data, msg in items:
        insert_idx = (channel_data - 0x7000) >> 6 if pid == _PARAM_VOLUME else (channel_data >> 6) & 0x7F
        by_param[(insert_idx, pid)] = msg
    assert by_param[(1, _PARAM_VOLUME)] == 12800  # kick fader untouched — EQ only
    assert by_param[(1, _PARAM_LOW_FREQ)] == eq_freq_msg(KICK_LOW_CUT_HZ)
    assert by_param[(1, _PARAM_LOW_GAIN)] == KICK_LOW_GAIN
    assert by_param[(3, _PARAM_VOLUME)] == 12800  # hats fader untouched — EQ only
    assert by_param[(3, _PARAM_LOW_FREQ)] == eq_freq_msg(HATS_LOW_CUT_HZ)
    assert by_param[(3, _PARAM_LOW_GAIN)] == HATS_LOW_GAIN
    # Dull the metal top — high shelf, not the old 6 kHz body-kill.
    from mix_gain_staging import HATS_HIGH_CUT_HZ, HATS_HIGH_GAIN

    assert by_param[(3, _PARAM_HIGH_FREQ)] == eq_freq_msg(HATS_HIGH_CUT_HZ)
    assert by_param[(3, _PARAM_HIGH_GAIN)] == HATS_HIGH_GAIN


def test_drum_eq_kill_switch(monkeypatch):
    monkeypatch.setenv("PLG_DRUM_EQ", "off")
    blob = gain_staging_payload({1, 3})
    items = [struct.unpack("<4sBBHi", blob[i : i + 12]) for i in range(0, len(blob), 12)]
    for _pad, pid, _u1, channel_data, _msg in items:
        insert_idx = (channel_data - 0x7000) >> 6 if pid == _PARAM_VOLUME else (channel_data >> 6) & 0x7F
        assert not (insert_idx in (1, 3) and pid == _PARAM_LOW_FREQ)


def test_kick_low_cut_params_count():
    assert len(kick_low_cut_params()) == 3


def test_hats_low_cut_params_count():
    assert len(hats_low_cut_params()) == 6  # low rumble + high dull


def test_melody_low_cut_params_count():
    assert len(melody_low_cut_params()) == 3


def test_melody_high_cut_params_count():
    assert len(melody_high_cut_params()) == 3


def test_staging_adds_high_cut_when_space_mix():
    blob = staging_from_pattern({"plg_use_template": True}, {4})
    assert len(blob) > 12 * 4


def test_staging_respects_explicit_melody_high_cut_without_space_mix():
    """Split+dark/rage Stage Rhodes stamps melody_high_cut_hz — must apply dull shelf."""
    data = {"plg_producer_meta": {"melody_high_cut_hz": 4000.0}}
    blob = staging_from_pattern(data, {4})  # insert 4 = melody
    items = [struct.unpack("<4sBBHi", blob[i : i + 12]) for i in range(0, len(blob), 12)]
    by_param: dict[tuple[int, int], int] = {}
    for _pad, pid, _u1, channel_data, msg in items:
        insert_idx = (channel_data - 0x7000) >> 6 if pid == _PARAM_VOLUME else (channel_data >> 6) & 0x7F
        by_param[(insert_idx, pid)] = msg
    assert by_param[(4, _PARAM_HIGH_FREQ)] == eq_freq_msg(4000.0)


def test_master_air_params_count():
    assert len(master_air_params()) == 3


def test_gain_staging_payload_includes_master_air_by_default():
    blob = gain_staging_payload({1})
    items = [struct.unpack("<4sBBHi", blob[i : i + 12]) for i in range(0, len(blob), 12)]
    by_param: dict[tuple[int, int], int] = {}
    for _pad, pid, _u1, channel_data, msg in items:
        insert_idx = (channel_data - 0x7000) >> 6 if pid == _PARAM_VOLUME else (channel_data >> 6) & 0x7F
        by_param[(insert_idx, pid)] = msg
    assert by_param[(0, _PARAM_HIGH_FREQ)] == eq_freq_msg(DEFAULT_MASTER_AIR_HZ)
    assert by_param[(0, _PARAM_HIGH_GAIN)] == DEFAULT_MASTER_AIR_GAIN


def test_master_air_kill_switch(monkeypatch):
    monkeypatch.setenv("PLG_MASTER_AIR", "off")
    blob = gain_staging_payload({1})
    items = [struct.unpack("<4sBBHi", blob[i : i + 12]) for i in range(0, len(blob), 12)]
    for _pad, pid, _u1, channel_data, _msg in items:
        insert_idx = (channel_data - 0x7000) >> 6 if pid == _PARAM_VOLUME else (channel_data >> 6) & 0x7F
        assert not (insert_idx == 0 and pid == _PARAM_HIGH_FREQ)


def test_background_bus_params_count():
    assert len(background_bus_params(11)) == 3


def test_background_bus_eq_off_by_default_leaves_texture_at_unity():
    # texture = insert 11; default behaviour (flag off) is unchanged unity volume.
    blob = gain_staging_payload({1, 11})
    items = [struct.unpack("<4sBBHi", blob[i : i + 12]) for i in range(0, len(blob), 12)]
    by_param: dict[tuple[int, int], int] = {}
    for _pad, pid, _u1, channel_data, msg in items:
        insert_idx = (channel_data - 0x7000) >> 6 if pid == _PARAM_VOLUME else (channel_data >> 6) & 0x7F
        by_param[(insert_idx, pid)] = msg
    assert by_param[(11, _PARAM_VOLUME)] == 12800
    assert (11, _PARAM_HIGH_FREQ) not in by_param


def test_background_bus_eq_opt_in_dulls_texture_and_cowbell(monkeypatch):
    monkeypatch.setattr("flp_template.CHANNEL_MIXER_INSERT", {"texture": 11, "cowbell": 12})
    blob = gain_staging_payload({1, 4, 11, 12}, background_bus_eq=True)
    items = [struct.unpack("<4sBBHi", blob[i : i + 12]) for i in range(0, len(blob), 12)]
    by_param: dict[tuple[int, int], int] = {}
    for _pad, pid, _u1, channel_data, msg in items:
        insert_idx = (channel_data - 0x7000) >> 6 if pid == _PARAM_VOLUME else (channel_data >> 6) & 0x7F
        by_param[(insert_idx, pid)] = msg
    for bg_insert in (11, 12):
        assert by_param[(bg_insert, _PARAM_VOLUME)] == insert_fader_value(DEFAULT_BACKGROUND_INSERT_DB)
        assert by_param[(bg_insert, _PARAM_HIGH_FREQ)] == eq_freq_msg(DEFAULT_BACKGROUND_HIGH_CUT_HZ)
        assert by_param[(bg_insert, _PARAM_HIGH_GAIN)] == DEFAULT_BACKGROUND_HIGH_GAIN
    # melody_lead (front lane, insert 4) keeps its own untouched treatment.
    assert by_param[(4, _PARAM_VOLUME)] == insert_fader_value(DEFAULT_MELODY_INSERT_DB)
    assert by_param[(4, _PARAM_LOW_FREQ)] == eq_freq_msg(DEFAULT_MELODY_LOW_CUT_HZ)
    assert (4, _PARAM_HIGH_FREQ) not in by_param


def test_background_bus_eq_reads_from_pattern_meta():
    data = {"plg_producer_meta": {"background_bus_eq": True}}
    blob = staging_from_pattern(data, {1, 11})
    items = [struct.unpack("<4sBBHi", blob[i : i + 12]) for i in range(0, len(blob), 12)]
    found = any(
        (((channel_data - 0x7000) >> 6 if pid == _PARAM_VOLUME else (channel_data >> 6) & 0x7F), pid) == (11, _PARAM_HIGH_FREQ)
        for _pad, pid, _u1, channel_data, _msg in items
    )
    assert found


def test_attach_vocal_ready_mix_records_midi_pocket_without_duplicate_manual_steps():
    data = {"plg_midi_melody": True, "manual_steps": []}

    attach_vocal_ready_mix(data)
    attach_vocal_ready_mix(data)

    meta = data["plg_producer_meta"]
    assert meta["vocal_ready_mix"]["profile"] == "midi_vocal_ready"
    assert meta["808_bass_insert_db"] == DEFAULT_808_BASS_INSERT_DB
    assert len(data["manual_steps"]) == 1
    assert "PLG 808 Bass" in data["manual_steps"][0]


def test_attach_vocal_ready_mix_reports_existing_master_headroom():
    data = {"plg_producer_meta": {"master_fader_db": -3.5}}

    attach_vocal_ready_mix(data)

    assert data["plg_producer_meta"]["vocal_ready_mix"]["master_headroom_db"] == 3.5


def test_midi_rough_master_overrides_hot_genre_settings():
    from mix_gain_staging import SAFE_MIDI_MASTER_DB

    data = {
        "plg_midi_melody": True,
        "plg_producer_meta": {
            "master_fader_db": -1.0,
            "master_soft_clip": False,
            "max_drive": True,
            "melody_insert_db": -9.0,
        },
    }

    meta = attach_vocal_ready_mix(data)

    assert meta["master_fader_db"] == SAFE_MIDI_MASTER_DB
    assert meta["master_soft_clip"] is True
    assert meta["max_drive"] is False
    assert meta["melody_insert_db"] == DEFAULT_MELODY_INSERT_DB
    assert meta["preview_master"]["mode"] == "safe_rough_preview"
    assert meta["preview_master"]["release_master"] is False


def test_build_guide_prints_vocal_ready_mix_section():
    from pattern_utils import format_build_guide

    data = {
        "bpm": 140,
        "style": "trap",
        "tracks": {"kick": [], "melody_lead": []},
        "plg_producer_meta": {
            "vocal_ready_mix": {
                "profile": "midi_vocal_ready",
                "master_headroom_db": 3.0,
                "notes": ["Kick stays front; vocal has room."],
            }
        },
    }

    guide = format_build_guide(data)

    assert "Rough mix:" in guide
    assert "midi_vocal_ready" in guide
    assert "Kick stays front" in guide

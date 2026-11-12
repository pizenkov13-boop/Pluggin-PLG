"""Max-drive loudness: kick+808 pushed into the Master Soft Clipper on demand."""

from __future__ import annotations

from flp_writer import _DB, _DEFAULT_CHANNEL_VOL, _FL_VOL_MAX, _channel_level


def test_default_balance_untouched_without_drive():
    assert _channel_level("kick", {}) == _DEFAULT_CHANNEL_VOL["kick"]
    assert _channel_level("sub_808", {}) == _DEFAULT_CHANNEL_VOL["sub_808"]
    assert _channel_level("kick", {}) > _channel_level("snare", {})
    assert _channel_level("snare", {}) == _DB(-10.0)
    assert _channel_level("clap", {}) == _DB(-10.0)
    assert _channel_level("snare_layer", {}) == _DB(-18.0)


def test_max_drive_pushes_kick_and_808_hotter():
    kick = _channel_level("kick", {}, max_drive=True)
    sub = _channel_level("sub_808", {}, max_drive=True)
    assert kick > _DEFAULT_CHANNEL_VOL["kick"]
    assert sub > _DEFAULT_CHANNEL_VOL["sub_808"]
    assert kick <= _FL_VOL_MAX and sub <= _FL_VOL_MAX
    # ~+2.5 dB, not a blowout
    assert kick == min(_FL_VOL_MAX, int(round(_DEFAULT_CHANNEL_VOL["kick"] * 10 ** (2.5 / 20))))


def test_max_drive_leaves_other_lanes_alone():
    assert _channel_level("melody_lead", {}, max_drive=True) == _DEFAULT_CHANNEL_VOL["melody_lead"]
    assert _channel_level("hi_hats", {}, max_drive=True) == _DEFAULT_CHANNEL_VOL["hi_hats"]

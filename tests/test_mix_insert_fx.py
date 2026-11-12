import struct

from mix_insert_fx import (
    PLUGIN_BLOOD_OVERDRIVE,
    PLUGIN_DELAY_3,
    PLUGIN_REVERB_2,
    _patch_blood_overdrive,
    _patch_reverb_2,
    reverb_2_plugin_data,
    space_mix_enabled,
)


def test_space_mix_is_opt_in_for_template():
    assert space_mix_enabled({"plg_use_template": True}) is False
    assert space_mix_enabled({"plg_space_mix": True}) is True
    assert space_mix_enabled({"plg_space_mix": False}) is False


def test_reverb_patch_wet_and_decay():
    base = b"\x00" * 52
    out = _patch_reverb_2(base, wet_pct=35.0, decay_sec=2.5)
    vals = struct.unpack("<13I", out[:52])
    assert vals[0] == 250
    assert vals[7] == int(round(0.35 * 256))


def test_delay_3_patch_scales_level_defaults():
    from mix_insert_fx import _DELAY_3_DEFAULT_D5, _patch_delay_3

    half = _patch_delay_3(_DELAY_3_DEFAULT_D5, wet_scale=0.5)
    base_vals = list(struct.unpack(f"<{len(_DELAY_3_DEFAULT_D5) // 4}I", _DELAY_3_DEFAULT_D5[: (len(_DELAY_3_DEFAULT_D5) // 4) * 4]))
    half_vals = list(struct.unpack(f"<{len(half) // 4}I", half[: (len(half) // 4) * 4]))
    # Every factory 6000/3000 level must be halved; flags (0/1/2) untouched.
    for b, h in zip(base_vals, half_vals):
        if b in (3000, 6000):
            assert h == b // 2
        elif b in (0, 1, 2):
            assert h == b


def test_apply_variant_mix_macros_contrast_forces_wet_space():
    from mix_insert_fx import CONTRAST_REVERB_WET_PCT, apply_variant_mix_macros

    pattern: dict = {"style": "trap", "user_prompt": "dark"}
    macro = apply_variant_mix_macros(pattern, mode="contrast", seed=1)
    assert pattern["plg_space_mix"] is True
    assert pattern["plg_mix_macro"]["force_space"] is True
    # ±20% around the 50% contrast base.
    assert CONTRAST_REVERB_WET_PCT * 0.8 <= macro["wet"] <= CONTRAST_REVERB_WET_PCT * 1.2
    assert 0.2 <= macro["delay_wet_scale"] <= 1.0


def test_apply_variant_mix_macros_safe_stays_within_jitter_band():
    from mix_insert_fx import REVERB_WET_PCT, apply_variant_mix_macros

    pattern: dict = {"style": "trap"}
    macro = apply_variant_mix_macros(pattern, mode="safe", seed=42)
    # Without a genre recipe wet, falls back to module default ±20% (clamped).
    assert REVERB_WET_PCT * 0.75 <= macro["wet"] <= REVERB_WET_PCT * 1.25
    assert macro["force_space"] is False


def test_configure_space_mix_reads_plg_mix_macro_overrides():
    from mix_insert_fx import configure_space_mix, reverb_2_plugin_data

    pattern = {
        "plg_mix_macro": {"wet": 50.0, "decay": 3.0, "delay_wet_scale": 0.5, "force_space": True}
    }
    active = configure_space_mix(pattern)
    assert active["wet"] == 50.0
    assert active["decay"] == 3.0
    assert active["delay_wet_scale"] == 0.5
    data = reverb_2_plugin_data()
    vals = struct.unpack("<13I", data[:52])
    assert vals[7] == int(round(0.50 * 256))
    assert vals[0] == 300  # 3.0s * 100
    configure_space_mix(None)  # no sticky global recipe for later tests


def test_blood_overdrive_preamp_boost():
    base = struct.pack("<9I", 3, 4000, 5000, 2000, 0, 0, 2500, 10000, 0)
    out = _patch_blood_overdrive(base, preamp_boost_db=3.0)
    vals = struct.unpack("<9I", out[:36])
    assert vals[2] > 5000


def test_reverb_plugin_data_nonempty():
    from mix_insert_fx import configure_space_mix

    configure_space_mix(None)
    data = reverb_2_plugin_data()
    assert len(data) >= 52
    vals = struct.unpack("<13I", data[:52])
    assert 80 <= vals[7] <= 95


def test_plugin_internal_names():
    assert "Reeverb" in PLUGIN_REVERB_2
    assert PLUGIN_DELAY_3 == "Fruity Delay 3"
    assert PLUGIN_BLOOD_OVERDRIVE == "Fruity Blood Overdrive"


def test_plugin_state_is_the_embedded_default_regardless_of_fl_install():
    # The FX state must not depend on anything installed on the user's machine.
    from mix_insert_fx import (
        _MAXIMUS_DEFAULT_D5,
        _WRAPPER_MAXIMUS,
        maximus_plugin_data,
        maximus_wrapper,
    )

    assert maximus_plugin_data() == _MAXIMUS_DEFAULT_D5
    assert maximus_wrapper() == _WRAPPER_MAXIMUS
    assert len(_MAXIMUS_DEFAULT_D5) == 1371

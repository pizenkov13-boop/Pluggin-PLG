"""Travis Scott / Mike Dean space mix — mixer insert FX for PLG .flp export.

Embeds verified native FL plugin state captured from FL Studio 2025 factory
presets (Fruity Reeverb 2, Fruity Delay 3, Fruity Blood Overdrive) on the
PLG MIDI and PLG Sub 808 mixer buses.
"""

from __future__ import annotations

import struct
from typing import Any

# FL internal plugin names (spelling must match Image-Line binaries).
PLUGIN_REVERB_2 = "Fruity Reeverb 2"
PLUGIN_DELAY_3 = "Fruity Delay 3"
PLUGIN_BLOOD_OVERDRIVE = "Fruity Blood Overdrive"
PLUGIN_MAXIMUS = "Maximus"

# Target mix recipe (stadium psychedelic space) — the trap/legacy defaults.
# Per-genre values come from style_corpus.MIX_RECIPES via configure_space_mix.
REVERB_WET_PCT = 35.0
REVERB_DECAY_SEC = 2.5
SUB_PREAMP_BOOST_DB = 3.0
MELODY_LPF_HZ = 5000.0

# Active per-generation recipe (set from the pattern right before mixer build).
_ACTIVE: dict[str, Any] = {}


def configure_space_mix(data: dict[str, Any] | None) -> dict[str, Any]:
    """Load the pattern's genre mix DNA into the active recipe (idempotent).

    Unknown lane / corpus off → empty recipe → the legacy behaviour: chains stay
    opt-in via ``plg_space_mix`` and use the module constants.

    Multi-export variants (2026-07) may stash ``plg_mix_macro`` on the pattern
    (wet / decay / delay_wet_scale) — those override the recipe after load so
    each Track_Vn.flp gets its own Random Macro jitter without rewriting the
    genre DNA tables.
    """
    global _ACTIVE
    _ACTIVE = {}
    if isinstance(data, dict):
        try:
            from style_corpus import mix_recipe_for_pattern

            _ACTIVE = dict(mix_recipe_for_pattern(data) or {})
        except Exception:  # noqa: BLE001 — mix DNA must never break export
            _ACTIVE = {}
        macro = data.get("plg_mix_macro")
        if isinstance(macro, dict):
            if "wet" in macro:
                try:
                    _ACTIVE["wet"] = float(macro["wet"])
                except (TypeError, ValueError):
                    pass
            if "decay" in macro:
                try:
                    _ACTIVE["decay"] = float(macro["decay"])
                except (TypeError, ValueError):
                    pass
            if "delay_wet_scale" in macro:
                try:
                    _ACTIVE["delay_wet_scale"] = float(macro["delay_wet_scale"])
                except (TypeError, ValueError):
                    pass
            # A deliberate macro always wants the melody-space intent on so the
            # channel reverb send + (when allowed) insert FX actually fire.
            if macro.get("force_space"):
                _ACTIVE["melody_space"] = True
    return _ACTIVE


def _active_wet_pct() -> float:
    return float(_ACTIVE.get("wet", REVERB_WET_PCT))


def _active_decay_sec() -> float:
    return float(_ACTIVE.get("decay", REVERB_DECAY_SEC))


def _active_sub_boost_db() -> float:
    return float(_ACTIVE.get("sub_boost_db", SUB_PREAMP_BOOST_DB))


def _active_delay_wet_scale() -> float:
    """1.0 = factory Delay 3 levels; 0.5 = half wet; clamped to keep FX audible
    but not runaway. Driven by plg_mix_macro.delay_wet_scale."""
    raw = float(_ACTIVE.get("delay_wet_scale", 1.0))
    return max(0.2, min(1.5, raw))


# Random Macro (±20%) for multi-export variants. Contrast (V2) starts from a
# deliberately wet ~50% base so a short pluck still grows an artificial tail.
MIX_MACRO_JITTER = 0.20
CONTRAST_REVERB_WET_PCT = 50.0
CONTRAST_DELAY_WET_SCALE = 0.50


def apply_variant_mix_macros(
    pattern: dict[str, Any],
    *,
    mode: str = "safe",
    seed: int | None = None,
) -> dict[str, float]:
    """Stamp ``plg_mix_macro`` (+ force ``plg_space_mix`` for contrast) on the
    pattern so build_flp's configure_space_mix / Delay-3 patcher diverge per
    Track_Vn. Returns the resolved macro dict for logging/tests.

    - safe (V1): genre recipe wet/decay ±20%.
    - contrast (V2): force space mix, base wet≈50% + delay_wet_scale≈0.5, then ±20%.
    - chaos (V3): genre recipe wet/decay ±20%, space intent on so the random
      sample still sits in a room.
    """
    import random

    rng = random.Random(seed if seed is not None else 0)

    def _jitter(base: float) -> float:
        return base * (1.0 + rng.uniform(-MIX_MACRO_JITTER, MIX_MACRO_JITTER))

    # Seed the recipe so we know the genre defaults before jittering.
    recipe = configure_space_mix(pattern)
    base_wet = float(recipe.get("wet", REVERB_WET_PCT))
    base_decay = float(recipe.get("decay", REVERB_DECAY_SEC))

    if mode == "contrast":
        wet = _jitter(CONTRAST_REVERB_WET_PCT)
        decay = _jitter(max(base_decay, 2.5))
        delay_scale = _jitter(CONTRAST_DELAY_WET_SCALE)
        pattern["plg_space_mix"] = True  # Delay 3 + Reverb 2 inserts on
        force_space = True
    elif mode == "chaos":
        wet = _jitter(base_wet)
        decay = _jitter(base_decay)
        delay_scale = _jitter(1.0)
        pattern["plg_space_mix"] = True
        force_space = True
    else:
        wet = _jitter(base_wet)
        decay = _jitter(base_decay)
        delay_scale = _jitter(1.0)
        force_space = bool(pattern.get("plg_space_mix"))

    # Keep wet in a musically sane band (audible but not a wash for V1).
    wet = max(8.0, min(70.0, wet))
    decay = max(0.8, min(6.0, decay))
    delay_scale = max(0.2, min(1.5, delay_scale))

    macro = {
        "wet": round(wet, 2),
        "decay": round(decay, 3),
        "delay_wet_scale": round(delay_scale, 3),
        "force_space": force_space,
        "mode": mode,
        "seed": seed,
    }
    pattern["plg_mix_macro"] = macro
    return macro


def insert_chains_enabled() -> bool:
    """Mixer-insert plugin chains (Reverb 2 / Delay 3 / Blood Overdrive).

    QUARANTINED by default: a user screenshot showed Fruity Delay 3 sitting on
    the MASTER insert of a generated project — reverb+delay over the whole mix.
    Until the insert stream is byte-verified against a live FL session, chains
    load only with an explicit opt-in. Channel-level DSP (precomputed CLIP /
    BOOST / reverb send) is unaffected — that path is template-proven.
    """
    return _env_flag_raw("PLG_INSERT_SPACE_FX", "off") in ("1", "true", "yes", "on")


def _env_flag_raw(name: str, default: str) -> str:
    import os

    return os.environ.get(name, default).strip().lower()


def sub_drive_enabled(data: dict[str, Any]) -> bool:
    """808 Blood Overdrive INSERT bus.

    Explicit ``plg_space_mix=True`` (deliberate opt-in) always works; the AUTO
    genre-recipe path additionally needs the insert-chain env opt-in. The
    sampler-level CLIP/BOOST drive (flp_writer) covers the дисторшн by default."""
    if space_mix_enabled(data):
        return True
    return insert_chains_enabled() and bool(_ACTIVE.get("sub_drive"))


def melody_space_enabled(data: dict[str, Any]) -> bool:
    """Melody space INTENT (drives the channel-level precalc reverb send). The
    Reverb 2 + Delay 3 INSERT chain additionally needs the insert opt-in."""
    if space_mix_enabled(data):
        return True
    return bool(_ACTIVE.get("melody_space"))


def melody_insert_chain_enabled(data: dict[str, Any]) -> bool:
    if space_mix_enabled(data):
        return True
    return insert_chains_enabled() and melody_space_enabled(data)


def reverb_only_enabled() -> bool:
    """Fruity Reeverb 2 ALONE on the PLG MIDI insert (one plugin, one slot)
    — a separate, narrower opt-in than insert_chains_enabled(), independent
    of the Delay 3/Blood Overdrive quarantine.

    Isolated specifically because the SINGLE-plugin-per-insert byte shape
    was forensically checked against a real FL-Studio-authored project
    (tests/fixtures/FL_20.8.4.flp): the Master Soft Clipper slot this repo
    already ships un-quarantined uses that exact shape and matches the real
    file's own single/multi-plugin slot encoding for the plugin blocks
    themselves. The combined Reverb+Delay TWO-plugin-per-insert chain
    (build_melody_fx_chain) has NOT been checked this way and stays fully
    behind insert_chains_enabled().

    Still off by default. Isolating the risk to one verified-shape plugin is
    not the same as declaring it safe in real FL Studio 2025 — that needs a
    human to actually open the generated project and confirm the channel
    rack and insert placement (see the verification checklist/script)."""
    return _env_flag_raw("PLG_REVERB_INSERT", "off") in ("1", "true", "yes", "on")


def melody_reverb_only_chain_enabled(data: dict[str, Any]) -> bool:
    """Never overrides the FULL combined chain — an explicit plg_space_mix
    request (or the insert_chains_enabled quarantine already being lifted)
    always wins and gets both plugins, not the solo one."""
    if melody_insert_chain_enabled(data):
        return False
    return reverb_only_enabled() and melody_space_enabled(data)


def delay_only_enabled() -> bool:
    """Fruity Delay 3 ALONE on the PLG MIDI insert (one plugin, one slot)
    — same isolation as reverb_only_enabled(), independent flag, independent
    of the Reverb 2/Blood Overdrive quarantine. See reverb_only_enabled()'s
    docstring for the full reasoning (generalizes identically to Delay 3 —
    same shared serializer, same verified single-plugin-per-insert shape).
    Still off by default."""
    return _env_flag_raw("PLG_DELAY_INSERT", "off") in ("1", "true", "yes", "on")


def melody_delay_only_chain_enabled(data: dict[str, Any]) -> bool:
    """Never overrides the full combined chain (same rule as reverb-only).
    Also stands down if reverb-only is active — both target the same single
    slot 0 on the same insert; mutual exclusion, reverb-only takes priority
    (arbitrary but deterministic — running both flags together is not a
    supported configuration, exercised explicitly in tests)."""
    if melody_insert_chain_enabled(data):
        return False
    if reverb_only_enabled():
        return False
    return delay_only_enabled() and melody_space_enabled(data)


def maximus_master_only_enabled() -> bool:
    """Maximus on the Master insert, ahead of the existing Soft Clipper
    (multiband dynamics before the final clipping stage) — same isolation
    pattern as reverb_only_enabled()/delay_only_enabled(), independent flag,
    off by default.

    Differs from the Melody-bus isolations in one respect: Master already
    unconditionally carries Soft Clipper (_master_soft_clip_enabled), so this
    is additive alongside it rather than the only occupant — Soft Clipper is
    pushed to the next slot, not replaced. When this flag is off,
    _master_insert_events() emits byte-identical output to before this
    feature existed (see the regression test locking that in).

    Maximus's default state (1371 bytes) is embedded below. Still only
    verified structurally (single-plugin-per-insert framing against
    tests/fixtures/FL_20.8.4.flp); actual FL Studio 2025 parser/playback
    behavior needs a human to confirm (see the verification script)."""
    return _env_flag_raw("PLG_MAXIMUS_INSERT", "off") in ("1", "true", "yes", "on")


# Captured from FL 2025 factory .fst wrappers (52-byte D4 blobs).
_WRAPPER_REVERB_2 = bytes.fromhex(
    "0000000006000000020000000000000001000000da05c800d0d4f37f98cdf37fc700000004000000"
    "7c000000c0010000a0000000"
)
_WRAPPER_DELAY_3 = bytes.fromhex(
    "000000000000000002000000000000004501000000000000000000000000000000000000a0000000"
    "8a000000eb020000d9000000"
)
_WRAPPER_BLOOD_OVERDRIVE = bytes.fromhex(
    "000000000000000002000000000000004103000000000000000000000000000000000000b8000000"
    "3a010000a4010000b4000000"
)
# Maximus Default preset state: 52B wrapper + 1371B plugin data
# (see maximus_master_only_enabled()).
_WRAPPER_MAXIMUS = bytes.fromhex(
    "0200000004000000020000000000000041010000000000000000000000000000000000005f000000"
    "e80000007202000081010000"
)

# Base plugin state (D5) — Default / Boost presets, patched at export time.
_REVERB_2_DEFAULT_D5 = bytes.fromhex(
    "4b000000280000000000000032000000640000000f0000002800000064000000f401000000000000"
    "80000000800000008700000000"
)
_DELAY_3_DEFAULT_D5 = bytes.fromhex(
    "010000007017000001000000010000000100000090000000b80b0000f401000070170000b80b0000"
    "fb04000000000000000000000000000001000000b80b000002000000ea0d00005a080000b5090000"
    "000000000000000060ea000070170000701700000000000070170000"
)
_BLOOD_OVERDRIVE_BOOST_D5 = bytes.fromhex(
    "03000000a00f000088130000d007000000000000c4090000102700000000000000000000"
)
_MAXIMUS_DEFAULT_D5 = bytes.fromhex(
    "03000e00584d000001000000c1bb0000010000005b040000e80300000080000000000000e8030000"
    "e80300000000000000000000e80300005b04000000000000020000008813000003000000420b0000"
    "0000000000000000e8030000e80300000000000000000000e80300005b0400000000000002000000"
    "8813000003000000420b00000000000000000000e8030000e80300000000000000000000e8030000"
    "5b04000000000000020000008813000003000000420b00000000000000000000e8030000e8030000"
    "0000000000000000e80300005b04000000000000020000008813000003000000420b000000000000"
    "584d000001000000c1bb0000010000005b040000e80300000080000000000000e8030000e8030000"
    "0000000000000000e80300005b04000000000000020000008813000003000000420b000000000000"
    "00000000e8030000e80300000000000000000000e80300005b040000000000000200000088130000"
    "03000000420b00000000000000000000e8030000e80300000000000000000000e80300005b040000"
    "00000000020000008813000003000000420b00000000000000000000e8030000e803000000000000"
    "00000000e80300005b04000000000000020000008813000003000000420b00000000000032000000"
    "03000000000000000001010001000100000100000000000000000000000002000000000000001403"
    "00000001000000020000000200000002000000030000000300000003000000000000000000000000"
    "000000000000000000000000000000000000000000e03f000000000000e03f000000000000000200"
    "0000000000e03f000000000000e03f000000000000000200000000ffffffffffffffffffffffffff"
    "ffffff03000000030000000300000000000000000000000000000000000000000000000000000000"
    "0000000000e03f000000000000e03f0000000000000002000000000000e03f000000000000e03f00"
    "0000000000000200000000ffffffffffffffffffffffffffffffff03000000030000000300000000"
    "0000000000000000000000000000000000000000000000000000000000e03f000000000000e03f00"
    "00000000000002000000000000e03f000000000000e03f000000000000000200000000ffffffffff"
    "ffffffffffffffffffffff0300000003000000030000000000000000000000000000000000000000"
    "00000000000000000000000000e03f000000000000e03f0000000000000002000000000000e03f00"
    "0000000000e03f000000000000000200000000ffffffffffffffffffffffffffffffff0300000003"
    "00000003000000000000000000000000000000000000000000000000000000000000000000e03f00"
    "0000000000e03f0000000000000002000000000000e03f000000000000e03f000000000000000200"
    "000000ffffffffffffffffffffffffffffffff030000000300000003000000000000000000000000"
    "000000000000000000000000000000000000000000e03f000000000000e03f000000000000000200"
    "0000000000e03f000000000000e03f000000000000000200000000ffffffffffffffffffffffffff"
    "ffffff03000000030000000300000000000000000000000000000000000000000000000000000000"
    "0000000000e03f000000000000e03f0000000000000002000000000000e03f000000000000e03f00"
    "0000000000000200000000ffffffffffffffffffffffffffffffff03000000030000000300000000"
    "0000000000000000000000000000000000000000000000000000000000e03f000000000000e03f00"
    "00000000000002000000000000e03f000000000000e03f000000000000000200000000ffffffffff"
    "ffffffffffffffffffffff"
)


def _patch_reverb_2(
    data: bytes,
    *,
    wet_pct: float = REVERB_WET_PCT,
    decay_sec: float = REVERB_DECAY_SEC,
) -> bytes:
    """Wet ~35 %, decay ~2.5 s (DEC scale ≈ 100 ms per unit on factory presets)."""
    if len(data) < 52:
        return data
    vals = list(struct.unpack("<13I", data[:52]))
    vals[0] = max(1, min(400, int(round(decay_sec * 100.0))))
    vals[7] = max(0, min(256, int(round(wet_pct / 100.0 * 256.0))))
    return struct.pack("<13I", *vals) + data[52:]


def _patch_blood_overdrive(
    data: bytes,
    *,
    preamp_boost_db: float = SUB_PREAMP_BOOST_DB,
) -> bytes:
    """Light breed — bump pre-amp ~+3 dB on the Boost factory curve."""
    if len(data) < 12:
        return data
    vals = list(struct.unpack("<9I", data[:36]))
    gain = float(vals[2]) * (10.0 ** (preamp_boost_db / 20.0))
    vals[2] = max(0, min(20000, int(round(gain))))
    return struct.pack("<9I", *vals) + data[36:]


def _patch_delay_3(data: bytes, *, wet_scale: float = 1.0) -> bytes:
    """Scale Fruity Delay 3's level-like factory params by ``wet_scale``.

    Factory Default.fst stores several 3000/6000 ints (≈30%/60% of FL's common
    0–10000 level scale) that behave as wet/feedback-adjacent levels across
    the stock preset — confirmed by the duplicated 0x1770 (=6000) and 0x0BB8
    (=3000) values in ``_DELAY_3_DEFAULT_D5``. Time/mode flags (0/1/2 and
    out-of-band values) are left alone. Clamped so a ±20% Random Macro never
    zeroes the delay or pushes past the factory ceiling.
    """
    if len(data) < 4 or wet_scale == 1.0:
        return data
    n = (len(data) // 4) * 4
    vals = list(struct.unpack(f"<{n // 4}I", data[:n]))
    scale = max(0.2, min(1.5, float(wet_scale)))
    for i, value in enumerate(vals):
        # Level-like factory defaults only — leave flags/times alone.
        if value in (3000, 6000):
            vals[i] = max(0, min(10000, int(round(value * scale))))
    return struct.pack(f"<{len(vals)}I", *vals) + data[n:]


def reverb_2_plugin_data() -> bytes:
    return _patch_reverb_2(
        _REVERB_2_DEFAULT_D5, wet_pct=_active_wet_pct(), decay_sec=_active_decay_sec()
    )


def delay_3_plugin_data() -> bytes:
    return _patch_delay_3(_DELAY_3_DEFAULT_D5, wet_scale=_active_delay_wet_scale())


def blood_overdrive_plugin_data() -> bytes:
    return _patch_blood_overdrive(
        _BLOOD_OVERDRIVE_BOOST_D5, preamp_boost_db=_active_sub_boost_db()
    )


def reverb_2_wrapper() -> bytes:
    return _WRAPPER_REVERB_2


def delay_3_wrapper() -> bytes:
    return _WRAPPER_DELAY_3


def blood_overdrive_wrapper() -> bytes:
    return _WRAPPER_BLOOD_OVERDRIVE


def maximus_plugin_data() -> bytes:
    return _MAXIMUS_DEFAULT_D5


def maximus_wrapper() -> bytes:
    return _WRAPPER_MAXIMUS


def space_mix_enabled(data: dict[str, Any]) -> bool:
    """Psychedelic insert FX are opt-in.

    Keeping Reeverb/Delay/Blood Overdrive on by default caused long tails and
    overloaded playback when users muted playlist/channels in FL. The clean
    default is samples + notes + gain staging; vibe FX can be enabled explicitly.
    """
    return bool(data.get("plg_space_mix") is True)


def melody_channel_reverb_mix() -> int:
    """Sampler precalc reverb — mirrors insert wet for extra depth."""
    return max(0, min(256, int(round(_active_wet_pct() / 100.0 * 256.0))))


def melody_fx_slot_count() -> int:
    return 2


def melody_fx_slot_count_reverb_only() -> int:
    return 1


def melody_fx_slot_count_delay_only() -> int:
    return 1


def sub_808_fx_slot_count() -> int:
    return 1


def build_melody_reverb_only_chain(native_slot_events) -> bytes:
    """Fruity Reeverb 2 ALONE on PLG MIDI insert (slot 0) — the isolated,
    forensically-checked single-plugin path. See reverb_only_enabled()."""
    return native_slot_events(
        0,
        plugin_name=PLUGIN_REVERB_2,
        wrapper=reverb_2_wrapper(),
        data=reverb_2_plugin_data(),
    )


def build_melody_delay_only_chain(native_slot_events) -> bytes:
    """Fruity Delay 3 ALONE on PLG MIDI insert (slot 0) — same isolated
    path as build_melody_reverb_only_chain, for Delay 3."""
    return native_slot_events(
        0,
        plugin_name=PLUGIN_DELAY_3,
        wrapper=delay_3_wrapper(),
        data=delay_3_plugin_data(),
    )


def build_melody_fx_chain(native_slot_events) -> bytes:
    """Reverb 2 + Delay 3 on PLG MIDI insert (slots 0–1)."""
    events = bytearray()
    events += native_slot_events(
        0,
        plugin_name=PLUGIN_REVERB_2,
        wrapper=reverb_2_wrapper(),
        data=reverb_2_plugin_data(),
    )
    events += native_slot_events(
        1,
        plugin_name=PLUGIN_DELAY_3,
        wrapper=delay_3_wrapper(),
        data=delay_3_plugin_data(),
    )
    return bytes(events)


def build_master_maximus_chain(native_slot_events) -> bytes:
    """Maximus ALONE in slot 0 — the isolated single-plugin path for the
    Master insert. See maximus_master_only_enabled(). Unlike the Melody/Sub
    808 isolations, the caller (flp_writer._master_insert_events) is
    responsible for placing Soft Clipper in the next slot when it's also
    enabled — Master already has an existing occupant, so this builder only
    owns the Maximus slot, not the whole insert's slot layout."""
    return native_slot_events(
        0,
        plugin_name=PLUGIN_MAXIMUS,
        wrapper=maximus_wrapper(),
        data=maximus_plugin_data(),
    )


def build_sub_808_fx_chain(native_slot_events) -> bytes:
    """Blood Overdrive on PLG Sub 808 — mid-range grit under the sub."""
    return native_slot_events(
        0,
        plugin_name=PLUGIN_BLOOD_OVERDRIVE,
        wrapper=blood_overdrive_wrapper(),
        data=blood_overdrive_plugin_data(),
    )

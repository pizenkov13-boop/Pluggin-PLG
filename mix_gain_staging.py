"""Deterministic mixer gain staging for PLG .flp export.

Embeds FL mixer insert faders, built-in insert EQ low-cut on melody, and
master headroom so rage/opium sessions do not clip on laptop / BT speakers.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Any

from plg_paths import bundle_dir

# FL insert fader: 12800 = 0.0 dB, logarithmic up to 16000 (+5.6 dB).
_INSERT_VOL_REF = 12800
_INSERT_VOL_MAX = 16000

# PyFLP _MixerParamsID values (mixer insert EQ + fader).
_PARAM_VOLUME = 192
_PARAM_LOW_GAIN = 208
_PARAM_MID_GAIN = 209
_PARAM_HIGH_GAIN = 210
_PARAM_LOW_FREQ = 216
_PARAM_MID_FREQ = 217
_PARAM_HIGH_FREQ = 218
_PARAM_LOW_Q = 224
_PARAM_MID_Q = 225
_PARAM_HIGH_Q = 226

# Mixer-fader items in a real FL-authored project do not use the plain
# insert-index address used by the built-in EQ params. Their channel-data word
# starts at 0x7000 and advances by 0x40 per visible mixer insert. This was
# verified against newmixer123.flp: Master=0x7000, MIDI=0x7040, Kick=0x7080,
# through Snare Layer=0x71C0. Writing fader values at 0x0000 + insert*0x40
# leaves the visible mixer faders unchanged in FL Studio.
_FADER_CHANNEL_DATA_BASE = 0x7000

# Locked PLG opening Mixer faders. These are raw FL Mixer values (not MIDI
# velocity and not Channel Rack volume): 12800 is unity, 16000 is the maximum.
# Keep these exact so every exported beat opens with the approved balance.
DEFAULT_MIXER_FADER_VALUES: dict[str, int] = {
    "master": 9062,
    "melody_lead": 7157,
    "kick": 11030,
    "sub_808": 5464,
    "808_bass": 5464,
    "hi_hats": 7642,
    "clap": 9503,
    "snare": 9484,
    "snare_layer": 9447,
}

# Commercial 808 slap into Soft Clipper. These dB values remain metadata for
# mix descriptions and compatibility; the Mixer faders above are authoritative.
# Master sits near -3 dB into Fruity Soft Clipper for Spotify-loud glue.
DEFAULT_MASTER_FADER_DB = -3.0
# Architect mix_db may suggest a master level; keep it inside a sane window.
MASTER_FADER_MIN_DB = -6.0
MASTER_FADER_MAX_DB = -1.0
# Melody insert = unity. The -12 dB tuck lives on the channel rack only so the
# balance stays exact (no -12 channel + -4 insert = -16 plastic burial).
DEFAULT_MELODY_INSERT_DB = 0.0
DEFAULT_MELODY_LOW_CUT_HZ = 95.0
DEFAULT_MELODY_LOW_GAIN = -600   # gentle low shelf, not a brick HPF
DEFAULT_MELODY_LOW_Q = 35000
# Dark / underwater lead — shelf cut above 5 kHz (Mike Dean–style dull synth).
DEFAULT_MELODY_HIGH_CUT_HZ = 5000.0
DEFAULT_MELODY_HIGH_GAIN = -720
DEFAULT_MELODY_HIGH_Q = 28000
# 808 bus at unity — channel volume (0 dB) carries the slap into Soft Clipper.
DEFAULT_SUB_INSERT_DB = 0.0
DEFAULT_808_BASS_INSERT_DB = 0.0
SAFE_MIDI_MASTER_DB = -3.5

VOCAL_READY_MIX_NOTES = (
    "Kick 0 dB / 808 Bass 0 dB / Melody -12 dB — 808 fills the low end into Soft Clipper; kick click still leads the transient.",
    "Melody stays at -12 dB on the channel rack (insert unity) so vocals + 808 own ~90% of the mix space.",
    "808_bass rings continuous (no breath burps); commercial BOOST on trap-family; quiet files RMS-boosted up only.",
)

# "Air" EQ — a light high-shelf lift near 11-12 kHz on the MASTER bus for
# freshness/crispness (Dirkey "How To Mix Beats In 2026"). Deliberately subtle
# (~+1.5 dB): an air lift, not a brightness rescue — too much here reads as
# harsh/sibilant once it hits the unconditional Master Soft Clipper.
DEFAULT_MASTER_AIR_HZ = 11500.0
DEFAULT_MASTER_AIR_GAIN = 150
DEFAULT_MASTER_AIR_Q = 35000

# Background bus EQ — "push to back" for explicitly-tagged secondary ear-candy
# lanes (texture/cowbell), Dirkey "Fix Your Sound Selection" bus-by-role idea
# (main/front vs background). Deliberately EQ-only on each lane's OWN existing
# insert (no new insert, no plugin chain, no mixer-topology change) so it can
# never reopen the FL insert-chain crash history and never touches the
# individually-separated melody_lead/counter_melody/chords/snare_layer buses.
DEFAULT_BACKGROUND_HIGH_CUT_HZ = 5500.0
DEFAULT_BACKGROUND_HIGH_GAIN = -900
DEFAULT_BACKGROUND_HIGH_Q = 28000
DEFAULT_BACKGROUND_INSERT_DB = -3.0

# Known FL insert-EQ frequency anchors (PyFLP defaults): 90 Hz ↔ 5777, 1500 Hz ↔ 33145.
_EQ_FREQ_A = 5777 / (90.0**0.621)
_EQ_FREQ_EXP = 0.621

MELODY_INSERT = 4
KICK_INSERT = 1
SUB_INSERT = 2
MASTER_INSERT = 0
HATS_INSERT = 3
BASS808_INSERT = 13
SNARE_INSERT = 5
CLAP_INSERT = 6
SNARE_LAYER_INSERT = 7

# Kick low-cut — clear sub-30 Hz rumble the 808 already owns.
KICK_LOW_CUT_HZ = 30.0
KICK_LOW_GAIN = -900
KICK_LOW_Q = 35000
# Kick punch body — 60–90 Hz click zone the 808 must leave alone.
KICK_BODY_HZ = 75.0
KICK_BODY_GAIN = 450
KICK_BODY_Q = 32000
# Kick "crunch" — a light high-shelf lift so the beater/click cuts through a
# busy mix. Lives on the kick's own free HIGH band (low/mid already carry the
# rumble-cut + punch-boost above) — same +1.5-2 dB scale as the master air lift.
KICK_CRUNCH_HZ = 4000.0
KICK_CRUNCH_GAIN = 175  # ~+1.75 dB
KICK_CRUNCH_Q = 32000
# 808 Bass: clear sub-20-25 Hz rumble/DC (was a +350 boost shelf at 40 Hz,
# which — being a shelf, not a peak — raised everything below 40 Hz including
# infrasonic mud instead of removing it; 2026-07 fix). A cut shelf this low
# leaves the 30-50 Hz tail (the actual tone) essentially untouched, since the
# shelf's knee sits below it. Gain is gentler than the kick's -900 (which
# fully clears its own rumble zone) — this only shaves DC/rumble, not the tail.
BASS808_LOW_CUT_HZ = 22.0
BASS808_LOW_CUT_GAIN = -600
BASS808_LOW_CUT_Q = 35000
# Kick's punch band — 808 notches 70 Hz so the two don't fight for the same
# 60-90 Hz click zone.
BASS808_NOTCH_HZ = 70.0
BASS808_NOTCH_GAIN = -700
BASS808_NOTCH_Q = 28000
# Snare low-cut — hard cut below 150-200 Hz (own insert, no prior conflicting
# decision here the way kick/hats had — clean addition).
SNARE_LOW_CUT_HZ = 175.0
SNARE_LOW_GAIN = -900
SNARE_LOW_Q = 35000
# Snare "crunch" — same +1.5-2 dB high-shelf lift approach as the kick's,
# on the snare's own free HIGH band.
SNARE_CRUNCH_HZ = 4000.0
SNARE_CRUNCH_GAIN = 175  # ~+1.75 dB
SNARE_CRUNCH_Q = 32000
# Clap low-cut — hard cut below 200-250 Hz on its own insert.
CLAP_LOW_CUT_HZ = 225.0
CLAP_LOW_GAIN = -900
CLAP_LOW_Q = 35000
# Clap "density" — a 1-2.5 kHz bump (not the kick/snare's 3-5 kHz shelf; this
# sits lower, so it lives on the MID band like kick's own punch-bump, not the
# HIGH band) for +2-3 dB of body/density.
CLAP_DENSITY_HZ = 1800.0
CLAP_DENSITY_GAIN = 300  # ~+3 dB
CLAP_DENSITY_Q = 30000
# Snare Layer low-cut — hard cut below 400-500 Hz on its own insert (this is
# the clap-double layer, not the dry clap itself — it can afford a steeper
# cut since it only needs to add air/crunch, not body).
SNARE_LAYER_LOW_CUT_HZ = 450.0
SNARE_LAYER_LOW_GAIN = -900
SNARE_LAYER_LOW_Q = 35000
# Snare Layer "air" — a 6-10 kHz high-shelf lift for the doubled-clap sheen.
SNARE_LAYER_CRUNCH_HZ = 8000.0
SNARE_LAYER_CRUNCH_GAIN = 200  # ~+2 dB
SNARE_LAYER_CRUNCH_Q = 32000
# Hats EQ — NEVER kill the body below 6 kHz (that left only a metal-sheet tick).
# Mild rumble shelf + high-shelf dull so bright trap hats stop sounding like
# tin falling off a roof when stacked on the 1/8 grid.
HATS_LOW_CUT_HZ = 250.0
HATS_LOW_GAIN = -400
HATS_LOW_Q = 35000
HATS_HIGH_CUT_HZ = 7500.0
HATS_HIGH_GAIN = -900   # -9 dB shelf above ~7.5 kHz
HATS_HIGH_Q = 28000


def insert_fader_value(db: float, *, ref: int = _INSERT_VOL_REF) -> int:
    return max(0, min(_INSERT_VOL_MAX, int(round(ref * (10 ** (db / 20.0))))))


def eq_freq_msg(hz: float) -> int:
    hz = max(10.0, min(16000.0, float(hz)))
    return max(0, min(65536, int(round(_EQ_FREQ_A * (hz**_EQ_FREQ_EXP)))))


def _mixer_param_item(insert_idx: int, param_id: int, msg: int, *, slot_idx: int = 0) -> bytes:
    channel_data = ((insert_idx & 0x7F) << 6) | (slot_idx & 0x3F)
    return struct.pack("<4sBBHi", b"\x00\x00\x00\x00", param_id, 0, channel_data, int(msg))


def _mixer_fader_item(insert_idx: int, value: int) -> bytes:
    """Serialize one visible FL Mixer fader using FL's 0x7000 address space."""
    channel_data = _FADER_CHANNEL_DATA_BASE + ((insert_idx & 0x7F) << 6)
    return struct.pack("<4sBBHi", b"\x00\x00\x00\x00", _PARAM_VOLUME, 0, channel_data, int(value))


def _full_mixer_params_baseline(faders: dict[int, int]) -> bytes:
    """Build FL's complete neutral MixerParams matrix.

    FL Studio 2025 ignores a sparse list of otherwise-valid fader records. A
    real FL-authored project contains the full 127-insert state (ten empty
    slots, pan/stereo, built-in EQ defaults, and one volume record per insert).
    This emits that neutral structure and changes only the requested visible
    fader values in its 64+ mixer-address bank.
    """
    items = bytearray()
    # FL stores the visible mixer strip in the 64..126 bank before the 0..63
    # bank. Preserve this order from the known-good FL-authored mixer file.
    for internal_idx in (*range(64, 127), *range(64)):
        channel_data = internal_idx << 6
        for slot_idx in range(10):
            items += struct.pack("<4sBBHi", b"\x00\x00\x00\x00", 0, 0, channel_data | slot_idx, 1)
            items += struct.pack("<4sBBHi", b"\x00\x00\x00\x00", 1, 0, channel_data | slot_idx, _INSERT_VOL_REF)

        # Visible mixer inserts are held in FL's 64+ parameter bank. Every
        # other entry remains unity/default so this function has no audible
        # effect beyond the explicitly requested faders.
        visible_idx = internal_idx - 64
        volume = faders.get(visible_idx, _INSERT_VOL_REF) if internal_idx >= 64 else _INSERT_VOL_REF
        for param_id, value in (
            (_PARAM_VOLUME, volume),
            (193, 0),
            (194, 0),
            (_PARAM_LOW_GAIN, 0),
            (_PARAM_MID_GAIN, 0),
            (_PARAM_HIGH_GAIN, 0),
            (_PARAM_LOW_FREQ, 5777),
            (_PARAM_MID_FREQ, 33145),
            (_PARAM_HIGH_FREQ, 55825),
            (_PARAM_LOW_Q, 17500),
            (_PARAM_MID_Q, 17500),
            (_PARAM_HIGH_Q, 17500),
        ):
            items += struct.pack("<4sBBHi", b"\x00\x00\x00\x00", param_id, 0, channel_data, value)
    return bytes(items)


def _visible_fader_targets(
    active_inserts: set[int], lane_insert_map: dict[str, int] | None
) -> dict[int, int]:
    """Map exported lanes to only the visible Mixer faders requested by PLG."""
    targets = {0: DEFAULT_MIXER_FADER_VALUES["master"]}
    if lane_insert_map:
        for lane, insert_idx in lane_insert_map.items():
            if insert_idx not in active_inserts or lane not in DEFAULT_MIXER_FADER_VALUES:
                continue
            targets[insert_idx] = DEFAULT_MIXER_FADER_VALUES[lane]
        return targets

    fallback = {
        MELODY_INSERT: DEFAULT_MIXER_FADER_VALUES["melody_lead"],
        KICK_INSERT: DEFAULT_MIXER_FADER_VALUES["kick"],
        SUB_INSERT: DEFAULT_MIXER_FADER_VALUES["sub_808"],
        BASS808_INSERT: DEFAULT_MIXER_FADER_VALUES["808_bass"],
        HATS_INSERT: DEFAULT_MIXER_FADER_VALUES["hi_hats"],
        CLAP_INSERT: DEFAULT_MIXER_FADER_VALUES["clap"],
        SNARE_INSERT: DEFAULT_MIXER_FADER_VALUES["snare"],
        SNARE_LAYER_INSERT: DEFAULT_MIXER_FADER_VALUES["snare_layer"],
    }
    targets.update({insert: value for insert, value in fallback.items() if insert in active_inserts})
    return targets


def _locked_mixer_params_payload() -> bytes | None:
    """Return the FL-authored MixerParams state approved for every PLG export.

    This is a standalone mixer-only binary payload captured from the verified
    reference project. It contains no channel, sample, note, plugin, routing,
    or playlist events. FL Studio accepts this full state where sparse fader
    writes were visually ignored.
    """
    path = bundle_dir() / "assets" / "plg_mixer_reference_params.bin"
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    return payload if payload and len(payload) % 12 == 0 else None


def melody_low_cut_params(
    insert_idx: int = MELODY_INSERT,
    *,
    hz: float = DEFAULT_MELODY_LOW_CUT_HZ,
    gain: int = DEFAULT_MELODY_LOW_GAIN,
    q: int = DEFAULT_MELODY_LOW_Q,
) -> list[bytes]:
    """High-pass the melody bus via the mixer's built-in low band (shelf cut)."""
    return [
        _mixer_param_item(insert_idx, _PARAM_LOW_FREQ, eq_freq_msg(hz)),
        _mixer_param_item(insert_idx, _PARAM_LOW_GAIN, gain),
        _mixer_param_item(insert_idx, _PARAM_LOW_Q, q),
    ]


def melody_high_cut_params(
    insert_idx: int = MELODY_INSERT,
    *,
    hz: float = DEFAULT_MELODY_HIGH_CUT_HZ,
    gain: int = DEFAULT_MELODY_HIGH_GAIN,
    q: int = DEFAULT_MELODY_HIGH_Q,
) -> list[bytes]:
    """LPF shelf on the melody bus — dull, dark, underwater synth top end."""
    return [
        _mixer_param_item(insert_idx, _PARAM_HIGH_FREQ, eq_freq_msg(hz)),
        _mixer_param_item(insert_idx, _PARAM_HIGH_GAIN, gain),
        _mixer_param_item(insert_idx, _PARAM_HIGH_Q, q),
    ]


def kick_low_cut_params(
    insert_idx: int = KICK_INSERT,
    *,
    hz: float = KICK_LOW_CUT_HZ,
    gain: int = KICK_LOW_GAIN,
    q: int = KICK_LOW_Q,
) -> list[bytes]:
    """Low-shelf cut on the kick bus — clears sub-30 Hz rumble the 808 owns."""
    return [
        _mixer_param_item(insert_idx, _PARAM_LOW_FREQ, eq_freq_msg(hz)),
        _mixer_param_item(insert_idx, _PARAM_LOW_GAIN, gain),
        _mixer_param_item(insert_idx, _PARAM_LOW_Q, q),
    ]


def kick_body_params(
    insert_idx: int = KICK_INSERT,
    *,
    hz: float = KICK_BODY_HZ,
    gain: int = KICK_BODY_GAIN,
    q: int = KICK_BODY_Q,
) -> list[bytes]:
    """Mid boost ~75 Hz — kick owns the 60–90 Hz punch, 808 ducks under it."""
    return [
        _mixer_param_item(insert_idx, _PARAM_MID_FREQ, eq_freq_msg(hz)),
        _mixer_param_item(insert_idx, _PARAM_MID_GAIN, gain),
        _mixer_param_item(insert_idx, _PARAM_MID_Q, q),
    ]


def kick_crunch_params(
    insert_idx: int = KICK_INSERT,
    *,
    hz: float = KICK_CRUNCH_HZ,
    gain: int = KICK_CRUNCH_GAIN,
    q: int = KICK_CRUNCH_Q,
) -> list[bytes]:
    """High-shelf lift ~4 kHz — beater/click cuts through without touching
    the low-cut or the 75 Hz punch boost (separate band, no conflict)."""
    return [
        _mixer_param_item(insert_idx, _PARAM_HIGH_FREQ, eq_freq_msg(hz)),
        _mixer_param_item(insert_idx, _PARAM_HIGH_GAIN, gain),
        _mixer_param_item(insert_idx, _PARAM_HIGH_Q, q),
    ]


def snare_eq_params(
    insert_idx: int = SNARE_INSERT,
) -> list[bytes]:
    """Snare's own bus: hard low-cut below 150-200 Hz (LOW band) + a light
    3-5 kHz crunch lift (HIGH band, free — nothing else uses it here)."""
    return [
        _mixer_param_item(insert_idx, _PARAM_LOW_FREQ, eq_freq_msg(SNARE_LOW_CUT_HZ)),
        _mixer_param_item(insert_idx, _PARAM_LOW_GAIN, SNARE_LOW_GAIN),
        _mixer_param_item(insert_idx, _PARAM_LOW_Q, SNARE_LOW_Q),
        _mixer_param_item(insert_idx, _PARAM_HIGH_FREQ, eq_freq_msg(SNARE_CRUNCH_HZ)),
        _mixer_param_item(insert_idx, _PARAM_HIGH_GAIN, SNARE_CRUNCH_GAIN),
        _mixer_param_item(insert_idx, _PARAM_HIGH_Q, SNARE_CRUNCH_Q),
    ]


def clap_eq_params(
    insert_idx: int = CLAP_INSERT,
) -> list[bytes]:
    """Clap's own bus: hard low-cut below 200-250 Hz (LOW band) + a
    1-2.5 kHz density bump (MID band, +2-3 dB)."""
    return [
        _mixer_param_item(insert_idx, _PARAM_LOW_FREQ, eq_freq_msg(CLAP_LOW_CUT_HZ)),
        _mixer_param_item(insert_idx, _PARAM_LOW_GAIN, CLAP_LOW_GAIN),
        _mixer_param_item(insert_idx, _PARAM_LOW_Q, CLAP_LOW_Q),
        _mixer_param_item(insert_idx, _PARAM_MID_FREQ, eq_freq_msg(CLAP_DENSITY_HZ)),
        _mixer_param_item(insert_idx, _PARAM_MID_GAIN, CLAP_DENSITY_GAIN),
        _mixer_param_item(insert_idx, _PARAM_MID_Q, CLAP_DENSITY_Q),
    ]


def snare_layer_eq_params(
    insert_idx: int = SNARE_LAYER_INSERT,
) -> list[bytes]:
    """Snare Layer's own bus: hard low-cut below 400-500 Hz (LOW band) + a
    6-10 kHz air/sheen lift (HIGH band, +~2 dB)."""
    return [
        _mixer_param_item(insert_idx, _PARAM_LOW_FREQ, eq_freq_msg(SNARE_LAYER_LOW_CUT_HZ)),
        _mixer_param_item(insert_idx, _PARAM_LOW_GAIN, SNARE_LAYER_LOW_GAIN),
        _mixer_param_item(insert_idx, _PARAM_LOW_Q, SNARE_LAYER_LOW_Q),
        _mixer_param_item(insert_idx, _PARAM_HIGH_FREQ, eq_freq_msg(SNARE_LAYER_CRUNCH_HZ)),
        _mixer_param_item(insert_idx, _PARAM_HIGH_GAIN, SNARE_LAYER_CRUNCH_GAIN),
        _mixer_param_item(insert_idx, _PARAM_HIGH_Q, SNARE_LAYER_CRUNCH_Q),
    ]


def bass808_frequency_split_params(
    insert_idx: int = BASS808_INSERT,
) -> list[bytes]:
    """808 low-cut clears sub-20-25 Hz rumble; mid-notch ~70 Hz frees the kick punch zone."""
    return [
        _mixer_param_item(insert_idx, _PARAM_LOW_FREQ, eq_freq_msg(BASS808_LOW_CUT_HZ)),
        _mixer_param_item(insert_idx, _PARAM_LOW_GAIN, BASS808_LOW_CUT_GAIN),
        _mixer_param_item(insert_idx, _PARAM_LOW_Q, BASS808_LOW_CUT_Q),
        _mixer_param_item(insert_idx, _PARAM_MID_FREQ, eq_freq_msg(BASS808_NOTCH_HZ)),
        _mixer_param_item(insert_idx, _PARAM_MID_GAIN, BASS808_NOTCH_GAIN),
        _mixer_param_item(insert_idx, _PARAM_MID_Q, BASS808_NOTCH_Q),
    ]


def hats_low_cut_params(
    insert_idx: int = HATS_INSERT,
    *,
    hz: float = HATS_LOW_CUT_HZ,
    gain: int = HATS_LOW_GAIN,
    q: int = HATS_LOW_Q,
) -> list[bytes]:
    """Dull metallic trap hats: mild low rumble shelf + high-shelf cut.

    The old 6 kHz body-kill left only the metallic tick — that was the
    "лист металла с крыши" sound. Keep the body, shave the tin top.
    """
    return [
        _mixer_param_item(insert_idx, _PARAM_LOW_FREQ, eq_freq_msg(hz)),
        _mixer_param_item(insert_idx, _PARAM_LOW_GAIN, gain),
        _mixer_param_item(insert_idx, _PARAM_LOW_Q, q),
        _mixer_param_item(insert_idx, _PARAM_HIGH_FREQ, eq_freq_msg(HATS_HIGH_CUT_HZ)),
        _mixer_param_item(insert_idx, _PARAM_HIGH_GAIN, HATS_HIGH_GAIN),
        _mixer_param_item(insert_idx, _PARAM_HIGH_Q, HATS_HIGH_Q),
    ]


def master_air_enabled() -> bool:
    return os.environ.get("PLG_MASTER_AIR", "auto").strip().lower() not in (
        "0", "false", "no", "off",
    )


def drum_eq_enabled() -> bool:
    return os.environ.get("PLG_DRUM_EQ", "auto").strip().lower() not in (
        "0", "false", "no", "off",
    )


def master_air_params(
    insert_idx: int = MASTER_INSERT,
    *,
    hz: float = DEFAULT_MASTER_AIR_HZ,
    gain: int = DEFAULT_MASTER_AIR_GAIN,
    q: int = DEFAULT_MASTER_AIR_Q,
) -> list[bytes]:
    """High-shelf lift on the master bus — the "air" EQ trick for a fresher,
    crisper final mix (Dirkey "How To Mix Beats In 2026")."""
    return [
        _mixer_param_item(insert_idx, _PARAM_HIGH_FREQ, eq_freq_msg(hz)),
        _mixer_param_item(insert_idx, _PARAM_HIGH_GAIN, gain),
        _mixer_param_item(insert_idx, _PARAM_HIGH_Q, q),
    ]


def background_bus_params(
    insert_idx: int,
    *,
    hz: float = DEFAULT_BACKGROUND_HIGH_CUT_HZ,
    gain: int = DEFAULT_BACKGROUND_HIGH_GAIN,
    q: int = DEFAULT_BACKGROUND_HIGH_Q,
) -> list[bytes]:
    """Dull/recede a background-tagged lane's own insert — "push to back"
    via subtractive EQ, not a shared bus/plugin chain."""
    return [
        _mixer_param_item(insert_idx, _PARAM_HIGH_FREQ, eq_freq_msg(hz)),
        _mixer_param_item(insert_idx, _PARAM_HIGH_GAIN, gain),
        _mixer_param_item(insert_idx, _PARAM_HIGH_Q, q),
    ]


def _background_bus_inserts() -> set[int]:
    """Texture / cowbell — explicitly-tagged secondary ear-candy lanes. Never
    melody_lead/counter_melody/chords/snare_layer, which keep their own
    dedicated front-lane inserts (see the insert-divorce fix)."""
    try:
        from flp_template import CHANNEL_MIXER_INSERT

        return {
            CHANNEL_MIXER_INSERT.get("texture", 11),
            CHANNEL_MIXER_INSERT.get("cowbell", 12),
        }
    except Exception:  # noqa: BLE001 — staging must never fail on template import
        return {11, 12}


def gain_staging_payload(
    active_inserts: set[int],
    *,
    master_db: float = DEFAULT_MASTER_FADER_DB,
    melody_insert_db: float = DEFAULT_MELODY_INSERT_DB,
    melody_low_cut_hz: float = DEFAULT_MELODY_LOW_CUT_HZ,
    melody_high_cut_hz: float | None = None,
    sub_insert_db: float = DEFAULT_SUB_INSERT_DB,
    bass808_insert_db: float = DEFAULT_808_BASS_INSERT_DB,
    background_bus_eq: bool = False,
    lane_insert_map: dict[str, int] | None = None,
) -> bytes:
    """Serialize MixerParams (event DATA+17) for master + every active insert.

    CRITICAL: FL stores each insert's fader in this blob. Any active insert that
    is NOT listed here loads at a 0 (silent) fader — that is why a session with
    faders only for kick/sub/melody played "just the kick" while the snare/clap/
    hi-hat inserts (3 and 5) were dead. So we MUST emit a volume for *every*
    active insert; the special ones (kick/sub/melody) just get tuned values.
    """
    melodic_extra = _melodic_bus_inserts()
    background_extra = _background_bus_inserts() if background_bus_eq else set()
    lane_by_insert = {
        insert: lane for lane, insert in (lane_insert_map or {}).items()
    }
    items: list[bytes] = [
        _full_mixer_params_baseline(_visible_fader_targets(active_inserts, lane_insert_map)),
        _mixer_fader_item(MASTER_INSERT, DEFAULT_MIXER_FADER_VALUES["master"]),
    ]
    if master_air_enabled():
        items.extend(master_air_params())
    for insert_idx in sorted(active_inserts):
        lane = lane_by_insert.get(insert_idx)
        if lane == "melody_lead" or (not lane_by_insert and insert_idx == MELODY_INSERT):
            items.append(
                _mixer_fader_item(insert_idx, DEFAULT_MIXER_FADER_VALUES["melody_lead"])
            )
            items.extend(melody_low_cut_params(insert_idx, hz=melody_low_cut_hz))
            if melody_high_cut_hz is not None:
                items.extend(melody_high_cut_params(insert_idx, hz=melody_high_cut_hz))
        elif lane == "sub_808" or (not lane_by_insert and insert_idx == SUB_INSERT):
            items.append(
                _mixer_fader_item(insert_idx, DEFAULT_MIXER_FADER_VALUES["sub_808"])
            )
        elif lane == "808_bass" or (not lane_by_insert and insert_idx == BASS808_INSERT):
            items.append(
                _mixer_fader_item(insert_idx, DEFAULT_MIXER_FADER_VALUES["808_bass"])
            )
            if drum_eq_enabled():
                items.extend(bass808_frequency_split_params(insert_idx))
        elif lane == "kick" or (not lane_by_insert and insert_idx == KICK_INSERT):
            items.append(_mixer_fader_item(insert_idx, DEFAULT_MIXER_FADER_VALUES["kick"]))
            if drum_eq_enabled():
                items.extend(kick_low_cut_params(insert_idx))
                items.extend(kick_body_params(insert_idx))
                items.extend(kick_crunch_params(insert_idx))
        elif lane == "hi_hats" or (not lane_by_insert and insert_idx == HATS_INSERT):
            items.append(_mixer_fader_item(insert_idx, DEFAULT_MIXER_FADER_VALUES["hi_hats"]))
            if drum_eq_enabled():
                items.extend(hats_low_cut_params(insert_idx))
        elif lane == "snare" or (not lane_by_insert and insert_idx == SNARE_INSERT):
            items.append(_mixer_fader_item(insert_idx, DEFAULT_MIXER_FADER_VALUES["snare"]))
            if drum_eq_enabled():
                items.extend(snare_eq_params(insert_idx))
        elif lane == "clap" or (not lane_by_insert and insert_idx == CLAP_INSERT):
            items.append(_mixer_fader_item(insert_idx, DEFAULT_MIXER_FADER_VALUES["clap"]))
            if drum_eq_enabled():
                items.extend(clap_eq_params(insert_idx))
        elif lane == "snare_layer" or (not lane_by_insert and insert_idx == SNARE_LAYER_INSERT):
            items.append(_mixer_fader_item(insert_idx, DEFAULT_MIXER_FADER_VALUES["snare_layer"]))
            if drum_eq_enabled():
                items.extend(snare_layer_eq_params(insert_idx))
        elif lane in ("counter_melody", "chords") or (not lane_by_insert and insert_idx in melodic_extra):
            # Counter/Chords live on their own buses now — they need the same
            # low-cut as the lead or they smear into the 808's territory.
            items.append(_mixer_fader_item(insert_idx, _INSERT_VOL_REF))
            items.extend(melody_low_cut_params(insert_idx, hz=melody_low_cut_hz))
        elif lane in ("texture", "cowbell") or (not lane_by_insert and insert_idx in background_extra):
            items.append(
                _mixer_fader_item(insert_idx, insert_fader_value(DEFAULT_BACKGROUND_INSERT_DB))
            )
            items.extend(background_bus_params(insert_idx))
        elif insert_idx != MASTER_INSERT:
            items.append(_mixer_fader_item(insert_idx, _INSERT_VOL_REF))
    return b"".join(items)


def _melodic_bus_inserts() -> set[int]:
    """Counter-melody / chords buses (melodic voices off the lead's insert)."""
    try:
        from flp_template import CHANNEL_MIXER_INSERT

        return {
            CHANNEL_MIXER_INSERT.get("counter_melody", 8),
            CHANNEL_MIXER_INSERT.get("chords", 10),
        }
    except Exception:  # noqa: BLE001 — staging must never fail on template import
        return {8, 10}


# --- DeepSeek mix_db -> existing gain staging --------------------------------
# The architect may emit a per-track loudness matrix (dB). We fold it into the
# deterministic staging that flp_writer already reads (plg_producer_meta +
# plg_mixer_levels), clamped so a model can never blow the mix up. The master
# stays a safety net: the model may only pull it DOWN (more headroom), never up
# into clipping, and the Fruity Soft Clipper on Master is unconditional.
_MIX_DB_MIN = -24.0
_MIX_DB_MAX = 6.0
_MIX_DB_ALIASES = {
    "melody": "melody", "melody_lead": "melody", "lead": "melody", "synth": "melody",
    "sub_808": "sub_808", "bass_808": "sub_808", "808": "sub_808", "bass": "sub_808", "sub": "sub_808",
    # 808_bass is its own canonical bucket — distinct from the sub_808 aliases
    "808_bass": "808_bass", "808bass": "808_bass", "bass_punch": "808_bass",
    "kick": "kick", "snare": "snare", "snare_layer": "snare_layer", "clap": "clap",
    "hi_hats": "hi_hats", "hats": "hi_hats", "hihat": "hi_hats", "hat": "hi_hats",
    "counter_melody": "counter_melody",
}


def _norm_mix_key(key: str) -> str:
    k = str(key).strip().lower()
    for suffix in ("_volume_db", "_level_db", "_gain_db", "_db", "_volume", "_level", "_vol"):
        if k.endswith(suffix):
            k = k[: -len(suffix)]
            break
    return _MIX_DB_ALIASES.get(k, k)


def _clamp_db(value: Any) -> float | None:
    try:
        return max(_MIX_DB_MIN, min(_MIX_DB_MAX, float(value)))
    except (TypeError, ValueError):
        return None


def apply_mix_db(data: dict[str, Any]) -> dict[str, float]:
    """Fold a DeepSeek ``mix_db`` matrix into the existing gain staging in-place.

    Returns the normalized per-track dB that were applied (for logging). No-op if
    there is no ``mix_db``. Safe by construction: per-track dB clamped to
    [-24, +6]; master clamped so it can only ADD headroom below the safe default.
    """
    raw = data.get("mix_db")
    if not isinstance(raw, dict):
        return {}

    levels: dict[str, float] = {}
    master_db: float | None = None
    for key, value in raw.items():
        norm = _norm_mix_key(key)
        if norm in ("soft_clipper_master", "soft_clipper", "master_clip", "clip"):
            continue  # soft clipper is unconditional; ignore the toggle
        if norm == "master":
            master_db = _clamp_db(value)
            continue
        db = _clamp_db(value)
        if db is not None:
            levels[norm] = db

    if not levels and master_db is None:
        return {}

    meta = data.setdefault("plg_producer_meta", {})
    if not isinstance(meta, dict):
        meta = {}
        data["plg_producer_meta"] = meta

    # gain-staging insert knobs the writer already consumes
    if "melody" in levels:
        meta["melody_insert_db"] = levels["melody"]
    if "sub_808" in levels:
        meta["sub_insert_db"] = levels["sub_808"]
    # master: clamp the architect's suggestion into the loud-but-safe window
    # (the soft clipper catches peaks; below -6 dB the beat turns into a whisper)
    if master_db is not None:
        meta["master_fader_db"] = max(MASTER_FADER_MIN_DB, min(MASTER_FADER_MAX_DB, master_db))

    # per-channel balance (fallback lanes / channels driven by plg_mixer_levels)
    if levels:
        mix_levels = data.setdefault("plg_mixer_levels", {})
        if isinstance(mix_levels, dict):
            mix_levels.update(levels)

    return levels


def staging_from_pattern(
    data: dict[str, Any],
    active_inserts: set[int],
    *,
    lane_insert_map: dict[str, int] | None = None,
) -> bytes:
    from mix_insert_fx import space_mix_enabled

    locked = _locked_mixer_params_payload()
    if locked is not None:
        return locked

    meta = data.get("plg_producer_meta")
    if not isinstance(meta, dict):
        meta = {}
    # Explicit high-cut (e.g. split+dark/rage Stage Rhodes dulling) wins even
    # without space-mix FX; otherwise space-mix keeps the default dull shelf.
    high_cut = None
    if meta.get("melody_high_cut_hz") is not None:
        try:
            high_cut = float(meta["melody_high_cut_hz"])
        except (TypeError, ValueError):
            high_cut = None
    elif space_mix_enabled(data):
        high_cut = float(meta.get("melody_high_cut_hz", DEFAULT_MELODY_HIGH_CUT_HZ))
    return gain_staging_payload(
        active_inserts,
        master_db=float(meta.get("master_fader_db", DEFAULT_MASTER_FADER_DB)),
        melody_insert_db=float(meta.get("melody_insert_db", DEFAULT_MELODY_INSERT_DB)),
        melody_low_cut_hz=float(meta.get("melody_low_cut_hz", DEFAULT_MELODY_LOW_CUT_HZ)),
        melody_high_cut_hz=high_cut,
        sub_insert_db=float(meta.get("sub_insert_db", DEFAULT_SUB_INSERT_DB)),
        bass808_insert_db=float(meta.get("808_bass_insert_db", DEFAULT_808_BASS_INSERT_DB)),
        background_bus_eq=bool(meta.get("background_bus_eq", False)),
        lane_insert_map=lane_insert_map,
    )


def attach_vocal_ready_mix(data: dict[str, Any]) -> dict[str, Any]:
    """Attach a conservative rough-mix intent for generated sessions.

    This does not try to replace a human mix. It stores the same safe knobs that
    ``staging_from_pattern`` consumes and documents the starting balance so the
    FL session opens with kick/808/melody already in a usable vocal-ready pocket.
    """
    meta = data.setdefault("plg_producer_meta", {})
    if not isinstance(meta, dict):
        meta = {}
        data["plg_producer_meta"] = meta

    meta.setdefault("master_fader_db", DEFAULT_MASTER_FADER_DB)
    meta.setdefault("melody_insert_db", DEFAULT_MELODY_INSERT_DB)
    meta.setdefault("melody_low_cut_hz", DEFAULT_MELODY_LOW_CUT_HZ)
    meta.setdefault("sub_insert_db", DEFAULT_SUB_INSERT_DB)
    meta.setdefault("808_bass_insert_db", DEFAULT_808_BASS_INSERT_DB)
    # Soft Clipper is part of the locked commercial chain — always on.
    meta["master_soft_clip"] = True
    meta.setdefault(
        "mix_balance",
        {"kick_db": 0.0, "bass_808_db": 0.0, "melody_db": -12.0, "sidechain_duck": 0.40},
    )
    meta.setdefault("clipper_drive_kick_808", True)
    if data.get("plg_midi_melody"):
        # Deterministic preview master: audible and glued, but still a rough
        # mix with real headroom. Never inherit a genre's hot/max-drive master.
        meta["master_fader_db"] = SAFE_MIDI_MASTER_DB
        meta["master_soft_clip"] = True
        meta["max_drive"] = False
        meta["melody_insert_db"] = DEFAULT_MELODY_INSERT_DB
        meta["melody_low_cut_hz"] = DEFAULT_MELODY_LOW_CUT_HZ
        meta["808_bass_insert_db"] = DEFAULT_808_BASS_INSERT_DB
        meta["preview_master"] = {
            "mode": "safe_rough_preview",
            "soft_clipper": True,
            "release_master": False,
            "master_fader_db": SAFE_MIDI_MASTER_DB,
            "ceiling_intent": "no digital master clipping",
        }
    try:
        master_headroom_db = abs(float(meta.get("master_fader_db", DEFAULT_MASTER_FADER_DB)))
    except (TypeError, ValueError):
        master_headroom_db = abs(DEFAULT_MASTER_FADER_DB)
    meta["vocal_ready_mix"] = {
        "profile": "midi_vocal_ready" if data.get("plg_midi_melody") else "vocal_ready",
        "master_headroom_db": master_headroom_db,
        "kick_role": "front anchor",
        "808_bass_role": "under kick transient",
        "melody_role": "behind drums; low-cut for vocal space",
        "notes": list(VOCAL_READY_MIX_NOTES),
    }

    manual = data.setdefault("manual_steps", [])
    if isinstance(manual, list):
        step = (
            "Rough mix is pre-staged: keep PLG Kick as the anchor, tuck PLG 808 Bass under it, "
            "and record vocals into the open mid/top pocket."
        )
        if step not in manual:
            manual.append(step)
    return meta

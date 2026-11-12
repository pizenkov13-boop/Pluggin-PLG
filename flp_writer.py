"""Generate a minimal FL Studio project (.flp) from a PLG pattern.

Why this exists
---------------
FL Studio has no CLI MIDI import, ``.mid`` argv is ignored, and menu/drag
automation proved unreliable (RU/EN UI, focus, OLE drag). The one thing FL
*does* do reliably is open a project file passed on the command line:

    FL64.exe C:\\PLG\\PLG_Session.flp

So PLG writes a tiny, valid ``.flp`` containing named Sampler channels
(Kick / Snare / Clap / Sub 808 / Hi-Hats) plus a Sampler melody channel on the
melody lane loaded as a Sampler one-shot preview.
Drums and 808 stay Sampler + wav via ``plg_sound_paths``; melody no longer
loads a one-shot wav preview; the producer swaps in their own synth.

MIXER (2026-10-22): by default the mixer keeps its structure — inserts named and
coloured per lane, each channel routed to its own insert — and nothing else. No
plugin (no Soft Clipper), no gain staging (the mixer-state block is FL's own
captured reference with every fader/pan/EQ field at FL's default), no sidechain,
no channel preamp/clip, notes centred, channel volumes at FL's defaults.
``legacy_mix_enabled()`` (env ``PLG_LEGACY_MIX=1``) restores the description below.

Also embeds: 4/4 time signature, pattern length (8–16 bars), sampler pre-FX /
Fruity Fast Dist on 808, light reverb on melody, per-track mixer inserts
(Kick / Sub 808 / Hi-Hats / Melody / Snare), Fruity Soft Clipper on Master,
and genre-tuned channel levels from ``plg_mixer_levels``. Automatic gain staging
embeds master headroom (-3 dB), Fruity Soft Clipper on Master, per-insert
faders at unity (Kick/808/Melody tuck lives on channel rack: -2 / -3 / -12 dB),
and a ~95 Hz low-cut on the melody bus.

FLP format (TLV event stream), grounded in the PyFLP project:
  - Header  "FLhd" + u32 size(=6) + i16 format(=0) + u16 channel_count + u16 ppq
  - Data    "FLdt" + u32 size + event stream
  - Events  id 0-63   -> 1 byte value
            id 64-127 -> 2 byte value (LE)
            id 128-191-> 4 byte value (LE)
            id 192-255-> variable: LEB128 length, then that many bytes
  - Note event (224) payload = N * 24-byte note structs.

Nothing here imports FL modules, so it runs as plain Python for tests.
"""

from __future__ import annotations

import math
import hashlib
import logging
import os
import shutil
import struct
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger("plg.flp_writer")

from fl_detect import flp_header_version
from mix_gain_staging import staging_from_pattern
from pattern_utils import (
    CHANNEL_NAMES,
    OPTIONAL_TRACK_KEYS,
    TRACK_KEYS,
    parse_note_name,
    step_to_beats,
    track_notes,
)

# Pulses per quarter note stored in the file header. 960 = atomic timeline
# resolution so humanize micro-shifts (swing, groove-lag, 808 attack) land
# precisely instead of quantizing to a ~4.5 ms grid. 960 is a standard FL PPQ.
PPQ = 960
MIN_PATTERN_BARS = 8
MAX_PATTERN_BARS = 16

# --- event id bases (see PyFLP _events.py) ---
_BYTE = 0
_WORD = 64
_DWORD = 128
_TEXT = 192
_DATA = 208

# project
_VERSION = _TEXT + 7        # 199, ASCII version string
_TEMPO = _DWORD + 28        # 156, bpm * 1000
_TIME_SIG_NUM = 17          # ArrangementsID.TimeSigNum
_TIME_SIG_BEAT = 18         # ArrangementsID.TimeSigBeat
# channel
_CH_NEW = _WORD + 0         # 64, u16 channel index (starts a channel block)
_CH_TYPE = 21               # u8 channel kind, 0 = Sampler
_CH_NAME = _TEXT + 0        # 192, channel display name
_CH_SAMPLE_PATH = _TEXT + 4  # 196, absolute path to loaded sample (PyFLP SamplePath)
_CH_FX_FLAGS = _WORD + 6     # 70
_CH_PREAMP = _WORD + 10     # 74
_CH_VOL = _WORD + 8         # 72 — channel rack volume (0..12800, 10000 = 0 dB)
_CH_ROUTED_TO = 22          # mixer insert index (0 = Master, 1 = Insert 1, …)
_CH_CUT_GROUP = _DWORD + 4  # 132 — (cut self, cut by); same value = Cut Itself
_CH_ROOT_NOTE = _DWORD + 7  # 135 — Sampler keyboard root note (C5 = 60)
_CH_REVERB = _DWORD + 11    # 139 — precalc reverb mix (type A + wet)
_CH_PARAMETERS = _DATA + 7  # 215 — sampler page (time stretch mode, etc.)
_CH_POLYPHONY = _DATA + 13  # 221 — max voices / mono flags
_PLUGIN_INTERNAL = _TEXT + 9  # 201
_PLUGIN_NAME = _TEXT + 11     # 203 — plugin display name (generators)
_PLUGIN_WRAPPER = _DATA + 4   # 212 — mixer slot wrapper blob
_PLUGIN_DATA = _DATA + 5    # 213
_PLUGIN_COLOR = _DWORD + 0  # 128 — plugin slot color (RGBA)
_PLUGIN_ICON = _DWORD + 27  # 155 — plugin icon id
_CH_ENABLED = 41            # byte flag present on real Native generator channels
# ChannelType (pyflp): Sampler=0, Native=2. Every PLG channel is a Sampler;
# _CH_TYPE_NATIVE stays defined because the plugin-event primitives below are
# generic FL wiring, not tied to any one generator.
_CH_TYPE_SAMPLER = 0
_CH_TYPE_NATIVE = 2
# mixer insert
_INSERT_NAME = _TEXT + 12   # 204
_INSERT_COLOR = _DWORD + 21  # 149, InsertID.Color
_INSERT_OUTPUT = _DWORD + 19  # 147
_INSERT_INPUT = _DWORD + 26   # 154
_INSERT_ROUTING = _DATA + 27  # 235
_INSERT_FLAGS = _DATA + 28    # 236
_MIXER_PARAMS = _DATA + 17    # 225 — insert faders + built-in EQ
_SLOT_INDEX = _WORD + 34      # 98
# pattern
_PAT_NEW = _WORD + 1        # 65, u16 pattern number (selects current pattern)
_PAT_LOOPED = 26            # PatternID.Looped
_PAT_NAME = _TEXT + 1       # 193, pattern name
_PAT_COLOR = _DWORD + 22    # 150, pattern clip color (PyFLP PatternID.Color)
_PAT_LENGTH = _DWORD + 36   # 164, pattern length in PPQ ticks
_PAT_NOTES = _DATA + 16     # 224, array of 24-byte note structs

# --- Arrangement / Playlist (so FL plays the SONG, not just the PAT) ----------
# Without a playlist clip, switching FL to SONG mode + Space plays silence. We
# place the one PLG pattern as a full-length clip on playlist track 1, and write
# the 500 default playlist tracks exactly as FL 2025 itself does (bytes captured
# from a real FL 2025 .flp) so the project opens clean and plays on Space.
_ARR_NEW = _WORD + 35       # 99  — ArrangementID.New (arrangement index)
_ARR_CURRENT = _WORD + 36   # 100 — current arrangement
_ARR_NAME = _TEXT + 49      # 241 — arrangement name
_ARR_EXTRA = _TEXT + 50     # 242 — arrangement trailer (zoom/scroll state)
_ARR_BYTE36 = 36            # per-arrangement byte flag (FL writes 146)
_ARR_TRACK_SEP = 43         # per-track trailer byte FL writes after each track
_PLAYLIST = _DATA + 25      # 233 — ArrangementID.Playlist (array of clip structs)
_TRACK_DATA = _DATA + 30    # 238 — TrackID.Data (one per playlist track)

_TIME_MARKER_POSITION = _DWORD + 20  # 148, TimeMarkerID.Position
_TIME_MARKER_NAME = _TEXT + 13       # 205, TimeMarkerID.Name

_PLAYLIST_MAX_TRACKS = 500
_PLAYLIST_PATTERN_BASE = 20480  # item_index = base + pattern_iid marks a pattern clip
# FL stores Playlist item positions in the project's PPQ, but clip *lengths*
# in a fixed 96-PPQ scale. Using the project PPQ=960 made every cube 10x too
# long; later cubes visually covered the error until the final lane clip.
_PLAYLIST_LENGTH_PPQ = 96
# Default playlist-track body after the u32 track id — captured verbatim from FL 2025.
_TRACK_TAIL = bytes.fromhex(
    "4851560000000000010000803ff0ffffff0000000000000000000500000000000000"
    "010000000000000000000000000000ffffffffffffffff010000000000000000"
)
# Arrangement trailer payload (event 242) — captured verbatim from FL 2025.
_ARR_EXTRA_PAYLOAD = bytes.fromhex(
    "000000000000000000000000000000a0999919400000000010000000"
)

_FX_FLAG_CLIP = 1 << 2

# neutral note byte values (PyFLP NotesEvent defaults)
_FINE_PITCH = 120   # 0 cents
_RELEASE = 64
_PAN = 64           # centred
_MOD_X = 128        # filter cutoff, neutral (no modulation)
_MOD_Y = 128        # resonance, neutral
# 2026-07 fix: a note FL Studio 25.2.2 draws itself carries midi_channel=128,
# not 0 (0 was PyFLP's stale documented default, which we'd copied). Without
# it — and without _NOTE_FLAG_SELECTABLE below — every generated note played
# back fine but rendered as a gray "ghost" note in the piano roll: FL drew it
# (playback reads each note's own rack_channel field, unaffected either way)
# but refused to select/right-click-delete/rubber-band/Ctrl+A it, as if it
# belonged to a foreign pattern. Confirmed by hand-drawing a control note
# alongside generated ones in the same pattern in a real FL 25.2.2 session:
# only the hand-drawn one (midi_channel=128, flag bit 14 set) was editable,
# both before and after matching every other field. Root cause of the FL
# bridge's "can't select/delete generated notes" report.
_MIDI_CH = 128
# Note flags field (PyFLP's _NoteFlags enum only documents Slide = 1 << 3 —
# FL's native portamento bit, pitch-bends continuously from the PREVIOUS note
# on the same channel instead of retriggering the sample's attack). Bit 14
# isn't in PyFLP's enum at all, but FL 25.2.2 sets it on every note it draws
# itself — see _MIDI_CH above for how this pair was found; a generated note
# without it renders as a non-selectable "ghost" note despite playing back
# fine.
_NOTE_FLAG_SLIDE = 1 << 3
_NOTE_FLAG_SELECTABLE = 1 << 14

# The legacy sub_808 path used a -24 sampler compensation when MIDI lived in a
# different register. Generators now emit C4–C5 (SUB_LO/SUB_HI) for FL's
# default C5-rooted sampler — stacking another -24 dropped everything into
# mud. Keep 0; 808_bass never used this offset.
_FL_SUB_808_PITCH_OFFSET = 0

# Channel rack → mixer insert. All lanes go straight to Master (0): our hand-built
# named inserts 1–5 were incomplete vs a real FL project (~125 inserts) and FL
# only passed audio through insert 1 (kick) — everything else was silent. Balance
# lives on each channel's CH_VOL instead (_DEFAULT_CHANNEL_VOL).
_MIXER_ROUTE: dict[str, int] = {
    "kick": 0,
    "sub_808": 0,
    "808_bass": 0,
    "hi_hats": 0,
    "melody_lead": 0,
    "counter_melody": 0,
    "snare": 0,
    "snare_layer": 0,
    "clap": 0,
}
_FL_VOL_DEFAULT = 10000
_FL_VOL_MAX = 12800
# Enabled + EnableEffects, docked left (no DockMiddle) so Master stays on-screen.
_INSERT_FLAGS_LEFT = bytes.fromhex("000000000c00000000000000")
# Master: same left dock + separator before drum buses.
_INSERT_FLAGS_MASTER = bytes.fromhex("000000000c04000000000000")
_INSERT_FLAGS_DEFAULT = _INSERT_FLAGS_LEFT
_ROUTING_MASTER = bytes(127)
_ROUTING_TO_MASTER = bytes([0x01]) + bytes(126)
# Native mixer slot layout captured from FL 20.8.4 "Plugin Test" insert (slot 0).
_PLUGIN_SLOT_COLOR = 0x00565148  # granite gray — FL default plugin slot color
_SOFT_CLIP_WRAPPER_SLOT0 = bytes.fromhex(
    "130000000000000002000000000000004001000000000000000000000000000000000000"
    "5a000000ae0000000000000000000000"
)
_SOFT_CLIP_DATA = bytes.fromhex("6400000080000000")  # threshold 100, post 128 (~80% wet)
_TEMPLATE_MIXER_REV = 7  # bump when master slot / mixer byte layout changes

# Sampler misc — FL Stretch (e3 Generic): TIME knob 0, no chipmunk on long sustains.
# Applied to PLG Melody + PLG Counter Melody + PLG Chords via _MELODY_SAMPLER_KEYS.
_MELODY_STRETCH_PARAMS = bytes.fromhex(
    "ffffffff0000000001000001ffffffff3c0000000000803f0000803f0000803f0000803f0000803f"
    "0000000001000000ffffffff000400003000000000000100a705000000000000000100000000000000"
    "000000000000000000000001000000000000000000000000000000ffffffff02000000feffffffff"
    "ffffff000000000000000000000000000000000000f03f0000000000000000ffffffff0101"
)
_SUB_808_POLYPHONY_MAX1 = bytes.fromhex("01000000")  # max voices = 1
_SUB_808_POLYPHONY_MONO = bytes.fromhex("0100000001")  # max=1 + mono flag (belt & suspenders)
_SUB_808_CUT_ITSELF = struct.pack("<HH", 1, 1)  # cut self = cut by → Cut Itself
# Own cut group so a fresh 808_bass hit never cuts a ringing sub_808 (and vice
# versa) if both ever coexist. Group 2 is unused elsewhere in this writer.
_BASS_808_CUT_ITSELF = struct.pack("<HH", 2, 2)
# Closed hats on an 1/8 grid: Cut Itself so a slightly long sample can't stack
# into a metallic wash with the next tick.
_HAT_CUT_ITSELF = struct.pack("<HH", 3, 3)
# Channel volumes now live in _DEFAULT_CHANNEL_VOL (dB-based US-studio staging).
# 808 sampler page: RESAMPLE (StretchMode.Resample = 0), TIME knob at 0.
#
# This used to hold -1 (StretchMode.Stretch) with a comment claiming "e3
# Generic" — which E3Generic (=1) is not, so the recorded rationale never
# described the shipped value. Changed 2026-08-15 after measuring what FL
# itself writes: every one-shot sample channel in the two real FL-authored
# projects available here (tests/fixtures/FL_20.8.4.flp and FL Studio 2025's
# factory NewStuff.flp) is mode=0 — kicks, hats, claps, SFX, including
# "SFX SubBass Drop.wav". Only genuinely time-stretched risers use mode=5
# (Auto). -1 appears in ZERO FL-authored channels and is not an option FL's
# TIME panel can select: it is a legacy value nobody regression-tests, which
# is exactly the wrong thing to ship to machines running other FL builds.
#
# Resample is also what an 808 wants musically — pitch and length coupled is
# how every trap 808 is transposed. An A/B of the two values (identical FLPs
# differing in these 4 bytes) was listened to on FL 2025: no audible
# difference, so this costs nothing on the verified configuration and removes
# an unknown everywhere else. See the project issue log (not included) §0j.
_SUB_808_STRETCH_PARAMS = bytes.fromhex(
    "ffffffff0000000001000001ffffffff3c0000000000803f0000803f0000803f0000803f0000803f"
    "0000000001000000ffffffff000400003000000000000100a7050000000000000001000000000000"
    "000000000000000000000000010000000000000000000000000000000000000002000000feffffff"
    "ffffffff000000000000000000000000000000000000f03f0000000000000000ffffffff0101"
)
_MELODY_SAMPLER_KEYS = frozenset({"melody_lead", "counter_melody", "chords"})


def _leb128(value: int) -> bytes:
    """FL variable-length size: 7 bits/byte, little-endian, MSB = continue."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _byte_event(eid: int, value: int) -> bytes:
    return bytes([eid, value & 0xFF])


def _word_event(eid: int, value: int) -> bytes:
    return bytes([eid]) + struct.pack("<H", value & 0xFFFF)


def _dword_event(eid: int, value: int) -> bytes:
    return bytes([eid]) + struct.pack("<I", value & 0xFFFFFFFF)


def _text_event(eid: int, text: str, *, unicode: bool = True) -> bytes:
    if unicode:
        data = text.encode("utf-16-le") + b"\x00\x00"
    else:
        data = text.encode("ascii", "replace") + b"\x00"
    return bytes([eid]) + _leb128(len(data)) + data


def _data_event(eid: int, payload: bytes) -> bytes:
    return bytes([eid]) + _leb128(len(payload)) + payload


def _ticks(time_step: float) -> int:
    return int(round(step_to_beats(time_step) * PPQ))


def _note_struct(
    rack_channel: int,
    entry: dict[str, Any],
    *,
    channel_key: str | None = None,
    max_ticks: int | None = None,
) -> bytes:
    """``max_ticks`` — the containing FL pattern's own declared length (the
    same value written to the _PAT_LENGTH event for this channel). Upstream
    postprocesses (groove-lag, humanize jitter, kick-locked 808 sustains…)
    only clamp the FLOOR of a note's position — nothing enforces that a
    note's tail stays inside the pattern it's declared to belong to. FL
    Studio's loader flags any note whose end runs past its pattern's length
    as "invalid" (2026-07 audit: confirmed present in a real generated .flp —
    melody/808 notes sustaining a few ticks past the outro's final bar).
    ``position`` was already floored at 0 below; this adds the missing
    ceiling so no note can ever claim ticks outside its own pattern.

    ``entry["glide"]``/``entry["slide"]`` (2026-07): sets FL's own native
    portamento bit (bit 3 of the flags field, value 8 — confirmed against
    PyFLP's reverse-engineered _NoteFlags.Slide) so this note pitch-bends
    continuously FROM the previous note on the same channel instead of
    retriggering the sample's attack. Previously this field was hardcoded to
    0, so notes already tagged "slide" (add_808_bar_slides' high-register
    stab) never actually glided in FL despite the name — this was always the
    intent, just never wired through."""
    position = max(0, _ticks(entry["time_step"]))
    length = max(1, _ticks(entry["length"]))
    if max_ticks is not None and max_ticks > 0:
        position = min(position, max_ticks - 1)
        length = min(length, max_ticks - position)
    key = max(0, min(131, parse_note_name(entry["note"])))
    if channel_key == "sub_808":
        key = max(0, min(131, key + _FL_SUB_808_PITCH_OFFSET))
    velocity = int(entry.get("velocity", 100))
    velocity = max(1, min(128, velocity))
    pan = _PAN
    if legacy_mix_enabled():
        pan = max(0, min(127, int(entry.get("pan", _PAN))))
    flags = _NOTE_FLAG_SELECTABLE
    if entry.get("glide") or entry.get("slide"):
        flags |= _NOTE_FLAG_SLIDE
    return struct.pack(
        "<IHHIHHBBBBBBBB",
        position,        # position (ticks)
        flags,           # flags (bit 3 = slide/portamento)
        rack_channel,    # rack channel
        length,          # length (ticks)
        key,             # key (MIDI note, C5 = 60)
        0,               # group
        _FINE_PITCH,     # fine pitch
        0,               # _u1
        _RELEASE,        # release
        _MIDI_CH,        # midi channel
        pan,             # pan
        velocity,        # velocity
        _MOD_X,          # mod x (filter cutoff)
        _MOD_Y,          # mod y (resonance)
    )


def _db_to_fl_volume(db: float, *, ref: int = _FL_VOL_DEFAULT) -> int:
    return max(0, min(_FL_VOL_MAX, int(round(ref * (10 ** (db / 20.0))))))


def _mixer_levels_from_data(data: dict[str, Any]) -> dict[str, int]:
    raw = data.get("plg_mixer_levels")
    if isinstance(raw, dict) and raw:
        levels: dict[str, int] = {}
        for key, value in raw.items():
            try:
                levels[str(key)] = max(0, min(_FL_VOL_MAX, int(value)))
            except (TypeError, ValueError):
                continue
        if levels:
            return levels
    meta = data.get("plg_producer_meta")
    if isinstance(meta, dict):
        nested = meta.get("mixer_levels")
        if isinstance(nested, dict) and nested:
            return _mixer_levels_from_data({"plg_mixer_levels": nested})
    return {
        "kick": _FL_VOL_MAX,
        "sub_808": _FL_VOL_MAX,
        "melody_lead": _db_to_fl_volume(-6.0),
        "counter_melody": _db_to_fl_volume(-6.0),
    }


def legacy_mix_enabled() -> bool:
    """The single owner of "does PLG touch FL's mixer at all".

    Default (off): the mixer keeps its structure (inserts named and coloured
    per lane, every channel routed to its own insert) and carries nothing else:
    no plugins, no Soft Clipper, no gain-staging block, no sidechain, no
    channel preamp/clip, notes centred; insert faders and channel volumes stay
    at FL's defaults. ``PLG_LEGACY_MIX=1`` restores the earlier behaviour (gain
    staging, ``mix_insert_fx`` chains, Master Soft Clipper, genre channel
    levels, hat panning, ``sidechain_curve``). The legacy code is kept, not
    deleted, so this can be switched back without a revert."""
    return os.environ.get("PLG_LEGACY_MIX", "").strip().lower() in ("1", "true", "yes", "on")


def _fl_fx_enabled(data: dict[str, Any]) -> bool:
    """In-project FL plugin FX (Fast Dist on 808, master Soft Clipper, preamp) are
    OFF by default. The hand-rolled plugin-state blobs proved unreliable across FL
    builds — a wrong blob makes FL playback a harsh clipped mess ('broken
    speaker'). A clean .flp (samples + notes + mixer faders) matches the good
    in-app preview. Opt back in per-pattern with ``plg_fl_fx: true`` once a blob is
    verified against a real FL plugin state."""
    return bool(data.get("plg_fl_fx", False))


def _master_soft_clip_enabled(data: dict[str, Any]) -> bool:
    """Template export always ships Master Soft Clipper (verified in plg_template.flp)."""
    if data.get("plg_use_template", True) is not False:
        return True
    if not _fl_fx_enabled(data):
        return False
    meta = data.get("plg_producer_meta")
    if isinstance(meta, dict) and "master_soft_clip" in meta:
        return bool(meta.get("master_soft_clip"))
    return True


def _insert_slot_indices(*, first_slot: int | None = None) -> bytes:
    events = bytearray()
    if first_slot is not None:
        events += _word_event(_SLOT_INDEX, first_slot)
        start = first_slot + 1
    else:
        start = 0
    for index in range(start, 10):
        events += _word_event(_SLOT_INDEX, index)
    return bytes(events)


def _native_mixer_slot_events(
    slot: int,
    *,
    plugin_name: str,
    wrapper: bytes,
    data: bytes,
) -> bytes:
    """FL native effect slot: name → wrapper → icon → color → data → slot index."""
    events = bytearray()
    events += _text_event(_PLUGIN_INTERNAL, plugin_name)
    events += _data_event(_PLUGIN_WRAPPER, wrapper)
    events += _dword_event(_PLUGIN_ICON, 0)
    events += _dword_event(_PLUGIN_COLOR, _PLUGIN_SLOT_COLOR)
    events += _data_event(_PLUGIN_DATA, data)
    events += _word_event(_SLOT_INDEX, slot)
    return bytes(events)


def _soft_clipper_slot_events(slot: int = 0) -> bytes:
    """Fruity Soft Clipper in Master — FL 20.8.4 wire order. Slot 0 by
    default; slot 1 when Maximus occupies slot 0 ahead of it (see
    _master_insert_events). The wrapper blob itself is slot-position-
    agnostic — slot placement is carried entirely by the trailing
    SLOT_INDEX event, the same pattern already proven by build_melody_fx_chain
    reusing delay_3_wrapper() unmodified at slot 1."""
    return _native_mixer_slot_events(
        slot,
        plugin_name="Fruity Soft Clipper",
        wrapper=_SOFT_CLIP_WRAPPER_SLOT0,
        data=_SOFT_CLIP_DATA,
    )


def _master_insert_events(*, soft_clip: bool, maximus_chain: bytes = b"") -> bytes:
    events = bytearray()
    events += _text_event(_INSERT_NAME, "Master")
    events += _data_event(_INSERT_FLAGS, _INSERT_FLAGS_MASTER)
    if maximus_chain:
        # Multiband dynamics (Maximus) ahead of the final clipping stage —
        # additive alongside the existing Soft Clipper, not a replacement.
        # maximus_chain defaults to b"" (off by default), which leaves this
        # branch byte-identical to the pre-Maximus code path below.
        events += maximus_chain
        if soft_clip:
            events += _soft_clipper_slot_events(slot=1)
            events += _insert_slot_indices(first_slot=2)
        else:
            events += _insert_slot_indices(first_slot=1)
    elif soft_clip:
        events += _soft_clipper_slot_events()
        events += _insert_slot_indices(first_slot=1)
    else:
        events += _insert_slot_indices()
    events += _data_event(_INSERT_ROUTING, _ROUTING_MASTER)
    events += _dword_event(_INSERT_INPUT, 0xFFFFFFFF)
    events += _dword_event(_INSERT_OUTPUT, 0)
    return bytes(events)


def _named_insert_events(
    name: str,
    *,
    fx_chain: bytes = b"",
    fx_slot_count: int = 0,
    color: int | None = None,
) -> bytes:
    events = bytearray()
    events += _text_event(_INSERT_NAME, name)
    if color is not None:
        events += _dword_event(_INSERT_COLOR, color)
    events += _data_event(_INSERT_FLAGS, _INSERT_FLAGS_DEFAULT)
    if fx_chain:
        events += fx_chain
    events += _insert_slot_indices(
        first_slot=fx_slot_count if fx_slot_count > 0 else None
    )
    events += _data_event(_INSERT_ROUTING, _ROUTING_TO_MASTER)
    events += _dword_event(_INSERT_INPUT, 0xFFFFFFFF)
    events += _dword_event(_INSERT_OUTPUT, 0xFFFFFFFF)
    return bytes(events)


def _fl_dt_payload(blob: bytes) -> memoryview:
    pos = blob.find(b"FLdt")
    if pos < 0:
        raise ValueError("Not an FLP file")
    size = struct.unpack_from("<I", blob, pos + 4)[0]
    return memoryview(blob)[pos + 8 : pos + 8 + size]


def _read_text_event(payload: bytes) -> str:
    return payload.decode("utf-16-le", errors="replace").rstrip("\0")


def _next_event_bounds(stream: memoryview, offset: int) -> tuple[int, int, int, bytes | int]:
    """Return (start, end, event_id, value_or_payload)."""
    start = offset
    eid = stream[offset]
    offset += 1
    if eid < 64:
        return start, offset + 1, eid, int(stream[offset])
    if eid < 128:
        return start, offset + 2, eid, int.from_bytes(stream[offset : offset + 2], "little")
    if eid < 192:
        return start, offset + 4, eid, int.from_bytes(stream[offset : offset + 4], "little")
    length = 0
    shift = 0
    pos = offset
    while True:
        byte = stream[pos]
        pos += 1
        length |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            break
        shift += 7
    end = pos + length
    return start, end, eid, bytes(stream[pos:end])


def _template_has_valid_master_clipper(blob: bytes) -> bool:
    """True when template carries a real native Master Soft Clipper slot (FL wire order)."""
    if "Fruity Soft Clipper".encode("utf-16-le") not in blob:
        return False
    icon_color = (
        bytes([_PLUGIN_ICON])
        + struct.pack("<I", 0)
        + bytes([_PLUGIN_COLOR])
        + struct.pack("<I", _PLUGIN_SLOT_COLOR)
    )
    return icon_color in blob


def _extract_mixer_events_bytes(template_blob: bytes) -> bytes | None:
    """Slice mixer inserts + params from ``assets/plg_template.flp``."""
    stream = _fl_dt_payload(template_blob)
    offset = 0
    mixer_start: int | None = None
    while offset < len(stream):
        start, end, eid, payload = _next_event_bounds(stream, offset)
        if eid == _INSERT_NAME and isinstance(payload, bytes):
            if _read_text_event(payload) == "Master":
                peek_off = end
                if peek_off < len(stream):
                    _, _, next_id, _ = _next_event_bounds(stream, peek_off)
                    if next_id == _INSERT_FLAGS:
                        mixer_start = start
                        break
        offset = end
    if mixer_start is None:
        return None
    return bytes(stream[mixer_start:])


def _mixer_events_without_params(mixer_bytes: bytes) -> bytes:
    stream = memoryview(mixer_bytes)
    out = bytearray()
    offset = 0
    while offset < len(stream):
        start, end, eid, _payload = _next_event_bounds(stream, offset)
        if eid != _MIXER_PARAMS:
            out += bytes(stream[start:end])
        offset = end
    return bytes(out)


def _mixer_events_with_insert_colors(mixer_bytes: bytes) -> bytes:
    """Inject PLG lane colors into a captured/template mixer stream."""
    from flp_template import CHANNEL_MIXER_INSERT

    color_by_insert = {
        insert: _PLG_COLORS[key]
        for key, insert in CHANNEL_MIXER_INSERT.items()
        if key in _PLG_COLORS
    }
    stream = memoryview(mixer_bytes)
    out = bytearray()
    offset = 0
    insert_index = -1
    while offset < len(stream):
        start, end, eid, _payload = _next_event_bounds(stream, offset)
        out += bytes(stream[start:end])
        if eid == _INSERT_NAME:
            insert_index += 1
            color = color_by_insert.get(insert_index)
            if color is not None:
                out += _dword_event(_INSERT_COLOR, color)
        offset = end
    return bytes(out)


# The mixer-state block (event 225) is a list of 12-byte records
# ``<I pad, H param, H slot, i value>``: ``param`` low byte 0xc0 = insert volume,
# 0xc1 = pan, 0xc2 = stereo separation, 0xd0-0xd2 = EQ gain (low/mid/high),
# 0xd8-0xda = EQ frequency, 0xe0-0xe2 = EQ bandwidth; param 0x0000 (slot 0x4000)
# is the first record of the block. These are FL's default values for them: the
# value shared by 126 of 127 inserts in the FL-authored tests/fixtures/FL_20.8.4.flp
# and by every untouched insert of assets/plg_mixer_reference_params.bin.
_MIXER_STATE_RECORD = struct.Struct("<IHHi")
_MIXER_STATE_FADER_DEFAULTS: dict[int, int] = {
    0x0000: 12800,
    0x1FC0: 12800, 0x1FC1: 0, 0x1FC2: 0,
    0x1FD0: 0, 0x1FD1: 0, 0x1FD2: 0,
    0x1FD8: 5777, 0x1FD9: 33145, 0x1FDA: 55825,
    0x1FE0: 17500, 0x1FE1: 17500, 0x1FE2: 17500,
}


def _default_mixer_state() -> bytes | None:
    """The FL-authored reference mixer state with every fader, pan, stereo
    separation and EQ field set to FL's default. Slot records (param 0x1f00 /
    0x1f01) and the record layout stay exactly as captured."""
    from mix_gain_staging import _locked_mixer_params_payload

    reference = _locked_mixer_params_payload()
    if not reference:
        return None
    out = bytearray(reference)
    for offset in range(0, len(out), _MIXER_STATE_RECORD.size):
        pad, param, slot, _value = _MIXER_STATE_RECORD.unpack_from(out, offset)
        default = _MIXER_STATE_FADER_DEFAULTS.get(param)
        if default is not None:
            _MIXER_STATE_RECORD.pack_into(out, offset, pad, param, slot, default)
    return bytes(out)


def _mixer_events_build(
    data: dict[str, Any],
    channels: list[str],
    route_map: dict[str, int] | None = None,
) -> bytes:
    """Programmatic mixer strip (125 inserts): lane names, colours and routing.

    Gain staging and the insert fx chains are written only with
    ``legacy_mix_enabled()``; otherwise no plugin is loaded and every fader
    keeps FL's default."""
    legacy = legacy_mix_enabled()
    from flp_template import CHANNEL_MIXER_INSERT, MIXER_TEMPLATE_INSERTS

    events = bytearray()
    if route_map is None:
        route_map = {key: CHANNEL_MIXER_INSERT.get(key, 0) for key in channels}
    if data.get("plg_midi_melody"):
        from flp_template import PATTERN_DISPLAY

        named = {0: ("Master", True)}
        named.update({
            insert: (PATTERN_DISPLAY.get(key, key), False)
            for key, insert in route_map.items()
        })
    else:
        named = {idx: (name, master) for idx, name, master in MIXER_TEMPLATE_INSERTS}
    insert_colors = {
        insert: _PLG_COLORS[key]
        for key, insert in route_map.items()
        if key in _PLG_COLORS
    }
    from flp_template import FL_MIXER_INSERT_COUNT

    if legacy:
        from mix_insert_fx import (
            build_master_maximus_chain,
            build_melody_delay_only_chain,
            build_melody_fx_chain,
            build_melody_reverb_only_chain,
            build_sub_808_fx_chain,
            maximus_master_only_enabled,
            melody_delay_only_chain_enabled,
            melody_fx_slot_count,
            melody_fx_slot_count_delay_only,
            melody_fx_slot_count_reverb_only,
            melody_insert_chain_enabled,
            melody_reverb_only_chain_enabled,
            sub_808_fx_slot_count,
            sub_drive_enabled,
        )

        melody_space = melody_insert_chain_enabled(data)
        melody_reverb_solo = melody_reverb_only_chain_enabled(data)
        melody_delay_solo = melody_delay_only_chain_enabled(data)
        sub_drive = sub_drive_enabled(data)
    else:
        melody_space = melody_reverb_solo = melody_delay_solo = sub_drive = False
    melody_insert = route_map.get("melody_lead", CHANNEL_MIXER_INSERT.get("melody_lead", 4))
    sub_insert = route_map.get(
        "808_bass",
        route_map.get("sub_808", CHANNEL_MIXER_INSERT.get("sub_808", 2)),
    )

    for idx in range(FL_MIXER_INSERT_COUNT):
        if idx in named:
            name, is_master = named[idx]
            if is_master:
                maximus_chain = (
                    build_master_maximus_chain(_native_mixer_slot_events)
                    if legacy and maximus_master_only_enabled()
                    else b""
                )
                events += _master_insert_events(
                    soft_clip=legacy and _master_soft_clip_enabled(data),
                    maximus_chain=maximus_chain,
                )
            elif melody_space and idx == melody_insert:
                fx_chain = build_melody_fx_chain(_native_mixer_slot_events)
                events += _named_insert_events(
                    name,
                    fx_chain=fx_chain,
                    fx_slot_count=melody_fx_slot_count(),
                    color=insert_colors.get(idx),
                )
            elif melody_reverb_solo and idx == melody_insert:
                fx_chain = build_melody_reverb_only_chain(_native_mixer_slot_events)
                events += _named_insert_events(
                    name,
                    fx_chain=fx_chain,
                    fx_slot_count=melody_fx_slot_count_reverb_only(),
                    color=insert_colors.get(idx),
                )
            elif melody_delay_solo and idx == melody_insert:
                fx_chain = build_melody_delay_only_chain(_native_mixer_slot_events)
                events += _named_insert_events(
                    name,
                    fx_chain=fx_chain,
                    fx_slot_count=melody_fx_slot_count_delay_only(),
                    color=insert_colors.get(idx),
                )
            elif sub_drive and idx == sub_insert:
                fx_chain = build_sub_808_fx_chain(_native_mixer_slot_events)
                events += _named_insert_events(
                    name,
                    fx_chain=fx_chain,
                    fx_slot_count=sub_808_fx_slot_count(),
                    color=insert_colors.get(idx),
                )
            else:
                events += _named_insert_events(name, color=insert_colors.get(idx))
        else:
            events += _named_insert_events(f"Insert {idx}")

    if legacy:
        active_inserts = {route_map.get(key, 0) for key in channels}
        active_inserts.add(0)
        staging = staging_from_pattern(data, active_inserts, lane_insert_map=route_map)
        if staging:
            events += _data_event(_MIXER_PARAMS, staging)
    else:
        # FL ignores insert data without the full mixer-state block (the earlier
        # sparse fader writes were "visually ignored", mix_gain_staging
        # ._locked_mixer_params_payload). Write that FL-authored state with every
        # fader / pan / EQ field at FL's default: nothing is gain-staged.
        mixer_state = _default_mixer_state()
        if mixer_state:
            events += _data_event(_MIXER_PARAMS, mixer_state)
    return bytes(events)


def _mixer_events_from_template(data: dict[str, Any], channels: list[str]) -> bytes | None:
    """Load pre-built mixer strip from ``assets/plg_template.flp`` when valid."""
    from flp_template import template_flp_path

    path = template_flp_path()
    if not path.is_file():
        return None
    template_blob = path.read_bytes()
    if not _template_has_valid_master_clipper(template_blob):
        return None
    mixer_bytes = _extract_mixer_events_bytes(template_blob)
    if not mixer_bytes:
        return None

    from flp_template import CHANNEL_MIXER_INSERT

    active_inserts = {CHANNEL_MIXER_INSERT.get(key, 0) for key in channels}
    active_inserts.add(0)
    staging = staging_from_pattern(data, active_inserts)
    colored_mixer = _mixer_events_with_insert_colors(mixer_bytes)
    events = bytearray(_mixer_events_without_params(colored_mixer))
    if staging:
        events += _data_event(_MIXER_PARAMS, staging)
    return bytes(events)


def _mixer_events(
    data: dict[str, Any],
    channels: list[str],
    route_map: dict[str, int] | None = None,
) -> bytes:
    """Mixer from template when available; space-mix builds inserts with FX.

    Default mode always builds the strip (names, colours, routing), never the
    template (which carries the Soft Clipper) and never the per-genre mix DNA."""
    if not legacy_mix_enabled():
        return _mixer_events_build(data, channels, route_map=route_map)
    from mix_insert_fx import (
        configure_space_mix,
        maximus_master_only_enabled,
        melody_delay_only_chain_enabled,
        melody_insert_chain_enabled,
        melody_reverb_only_chain_enabled,
        sub_drive_enabled,
    )

    configure_space_mix(data)  # load per-genre mix DNA (808 drive / space / LPF)
    if data.get("plg_midi_melody"):
        return _mixer_events_build(data, channels, route_map=route_map)
    if (
        sub_drive_enabled(data)
        or melody_insert_chain_enabled(data)
        or melody_reverb_only_chain_enabled(data)
        or melody_delay_only_chain_enabled(data)
        or maximus_master_only_enabled()
    ):
        return _mixer_events_build(data, channels, route_map=route_map)
    loaded = _mixer_events_from_template(data, channels)
    if loaded:
        return loaded
    return _mixer_events_build(data, channels, route_map=route_map)


def _channel_route(channel_key: str, route_map: dict[str, int] | None = None) -> int:
    from flp_template import CHANNEL_MIXER_INSERT

    if route_map is not None:
        return route_map.get(channel_key, 0)
    return CHANNEL_MIXER_INSERT.get(channel_key, 0)


# THE mix balance — commercial trap peaks (into Soft Clipper):
# Kick 0 dB (click/ram), 808 Bass 0 dB (fills the entire low end — soft clipper
# glues the push). Melody -12 dB. Quiet library 808s get RMS-BOOSTED up; hot
# ones stay hot (never turned down into "another room").
_DB = lambda db: int(round(10000 * (10 ** (db / 20.0))))  # noqa: E731
_DEFAULT_CHANNEL_VOL: dict[str, int] = {
    "kick": _DB(0.0),           # 0 dB — transient ram
    "snare": _DB(-10.0),        # below the kick; the clap/layer stack adds up
    "clap": _DB(-10.0),
    # NOT unity with the kick. The 808's long slabs occupy most of the
    # timeline, so at matched level they eat the headroom, bury the kick and
    # turn the low end to mush. Kick peaks, 808 carries mass ~3.5 dB under it
    # (with the clipper glue applied on top this lands at 12589 vs 8413).
    "sub_808": _DB(-0.5),
    # The dedicated user-facing 808 lane must leave the Channel Rack at its
    # physical maximum.  Its loudness is controlled by sample choice, the
    # mono envelope and the mixer/clipper — not by silently parking the rack
    # fader below full scale.
    "808_bass": _DB(-0.5),
    "snare_layer": _DB(-18.0),  # texture only; never louder than the kick
    "hi_hats": _DB(-12.0),      # tucked — never a metal wash over the kit
    "melody_lead": _DB(-12.0),  # -12 dB — back of the mix; never fights the 808
    "counter_melody": _DB(-14.0),  # 2000
    "fx": _DB(-10.0),           # 3162 — crash/impact ear candy: felt, not smashed
    "chords": _DB(-15.0),       # 1778 — harmonic bed UNDER everything, glues not competes
    "texture": _DB(-19.0),      # 1122 — the scene (rain/vinyl/siren), felt not heard
    "cowbell": _DB(-11.0),      # 2818 — Carti signature accent, present not loud
}


# The F1lthy/BNYX loudness move: in max-drive mode kick+808 are pushed HOT into
# the Master Soft Clipper, which turns the excess into crunchy distortion
# instead of digital clipping. Only when the prompt asks for it (мясо/брутал).
# Kick is the peak/attack leader. This modest rack gain is intentionally below
# FL's ceiling; the lower clipper push below keeps headroom for max-drive.
_DEFAULT_CHANNEL_VOL["kick"] = _DB(0.75)
_DRIVE_PUSH_DB: dict[str, float] = {"kick": 2.5, "sub_808": 2.5, "808_bass": 2.5}
# Rich Amiri Soft Clipper glue — milder always-on push when master Soft Clipper
# is on (separate from max_drive). Kick+808 punch the clipper without redline chaos.
# 2026-07: kick pushed harder than the 808 (2.0 vs 1.5 dB). They used to sit at
# an identical level, but they are NOT equal partners in the low end — the kick
# is a ~90 ms transient that has to cut THROUGH the 808's sustained mass and
# "вышибать воздух"; matched levels let the (now long-tailed) 808 mask it.
# Deliberately capped at 2.0: kick's base is 10000 and _FL_VOL_MAX is 12800
# (only ~2.14 dB of headroom), so anything higher clamps to the ceiling and
# would collide with _DRIVE_PUSH_DB's max-drive mode, which must stay strictly
# hotter. The heavy lifting for kick-vs-808 separation is the sidechain duck
# (duck_808_under_kick), which only became live once 808 notes got long enough
# for a following kick to land inside a sustain.
_CLIPPER_GLUE_DB: dict[str, float] = {"kick": 1.3, "sub_808": 1.5, "808_bass": 1.5}


# RMS gain-matching for the melody lane (2026-07): the static -12/-14 dB
# channel volumes above assume every candidate file was recorded/rendered at
# roughly the same loudness — false across a pool of one-shots/pads from
# different packs (one bank file at -10dBFS, another at -24dBFS both get the
# exact same channel volume, so one plays way louder than the other despite
# identical MIDI velocity). Corrected here, not baked into the static table,
# because the correction is PER FILE (whichever sample actually got picked
# this generation), not per lane.
_MELODY_TARGET_RMS_DBFS = -18.0
# Never let a wildly quiet/loud outlier file swing past the deliberately
# hand-tuned balance above — this is a corrective nudge, not a re-mix.
_MELODY_GAIN_CORRECTION_CLAMP_DB = 6.0

# Folder 808s vary ~10 dB RMS (TRAP_JUMP ~-14 vs BLAKK ~-5). Without matching,
# a quiet file at channel -1 dB still whispers under the kick.
_808_TARGET_RMS_DBFS = -6.0
_808_GAIN_CORRECTION_CLAMP_DB = 8.0
_808_GAIN_KEYS = frozenset({"sub_808", "808_bass"})


def _melody_gain_correction_db(sample_path: Path | str | None) -> float:
    """dB nudge so ``sample_path``'s measured body loudness lands near
    _MELODY_TARGET_RMS_DBFS instead of whatever it happened to be recorded
    at. 0.0 (no-op) if there's no path, the file is unreadable, or analysis
    fails for any reason — this must never block a build."""
    if not sample_path:
        return 0.0
    try:
        from audio_features import rms_dbfs

        measured = rms_dbfs(sample_path)
    except Exception:  # noqa: BLE001 — best-effort correction, never fatal
        return 0.0
    if measured is None:
        return 0.0
    correction = _MELODY_TARGET_RMS_DBFS - measured
    return max(-_MELODY_GAIN_CORRECTION_CLAMP_DB, min(_MELODY_GAIN_CORRECTION_CLAMP_DB, correction))


def _808_gain_correction_db(sample_path: Path | str | None) -> float:
    """Boost quiet library 808s up toward -8 dBFS. Never turn hot ones down —
    that made fat files whisper 'in another room' while quiet ones stayed timid."""
    if not sample_path:
        return 0.0
    try:
        from audio_features import rms_dbfs

        measured = rms_dbfs(sample_path)
    except Exception:  # noqa: BLE001 — best-effort correction, never fatal
        return 0.0
    if measured is None:
        return 0.0
    correction = _808_TARGET_RMS_DBFS - measured
    # Up only — Soft Clipper catches the push on already-hot files.
    return max(0.0, min(_808_GAIN_CORRECTION_CLAMP_DB, correction))


def _channel_level(
    channel_key: str,
    levels: dict[str, int],
    *,
    max_drive: bool = False,
    clipper_glue: bool = False,
    sample_path: Path | str | None = None,
) -> int:
    # The deliberate balance wins. plg_mixer_levels is only a fallback for lanes
    # we don't explicitly balance (it used to bury the hook and dominate w/ drums).
    if channel_key in _DEFAULT_CHANNEL_VOL:
        base = _DEFAULT_CHANNEL_VOL[channel_key]
        push = _DRIVE_PUSH_DB.get(channel_key)
        if max_drive and push:
            base = min(_FL_VOL_MAX, int(round(base * 10 ** (push / 20.0))))
        elif clipper_glue:
            glue = _CLIPPER_GLUE_DB.get(channel_key)
            if glue:
                base = min(_FL_VOL_MAX, int(round(base * 10 ** (glue / 20.0))))
        if channel_key in _MELODY_SAMPLER_KEYS:
            correction_db = _melody_gain_correction_db(sample_path)
            if correction_db:
                base = max(0, min(_FL_VOL_MAX, int(round(base * 10 ** (correction_db / 20.0)))))
        elif channel_key in _808_GAIN_KEYS:
            correction_db = _808_gain_correction_db(sample_path)
            if correction_db:
                base = max(0, min(_FL_VOL_MAX, int(round(base * 10 ** (correction_db / 20.0)))))
        return base
    return levels.get(channel_key, 10000)


def _channels_with_layout(data: dict[str, Any]) -> list[str]:
    """Lay out PLG channels; skip optional tracks with no notes."""
    channels: list[str] = []
    for key in TRACK_KEYS:
        if key in OPTIONAL_TRACK_KEYS and not track_notes(data, key):
            continue
        channels.append(key)
    return channels


# FL Studio resolves only plain printable-ASCII sample paths. Library folders
# here use look-alike Unicode (e.g. U+2024 ONE DOT LEADER "4‧ ROOT OF ALL EVIL")
# and control chars (U+007F). Windows/Python open these fine, but FL loads them
# "red" / silent — which is why only the ASCII-pathed drums (kick/clap/snare
# layer) played while the 808, snare, hats and melody went missing. We copy any
# non-ASCII-path sample into a clean ASCII folder and point the FLP at the copy.
_FL_SAFE_DIRNAME = "PLG_Sounds_FL"


def _is_fl_safe_path(text: str) -> bool:
    return all(0x20 <= ord(ch) <= 0x7E for ch in text)


def _fl_safe_filename(key: str, path: Path) -> str:
    # Keep the low-end sample name deliberately boring. FL's older Sampler
    # builds have mishandled library paths containing control chars and vendor
    # punctuation; a canonical ASCII basename prevents a valid 808 from
    # appearing as a silent channel even though the MIDI notes are present.
    if key == "808_bass":
        # A shared PLG_808.wav made every exported FLP point at whichever 808
        # happened to be converted last. Keep FL-safe ASCII while making each
        # source sample immutable and independently audible.
        digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
        return f"PLG_808_{digest}.wav"
    if key == "sub_808":
        return "PLG_Sub808.wav"
    # NFKD turns look-alikes (U+2024 → ".") into ASCII before we strip the rest.
    stem = unicodedata.normalize("NFKD", path.stem)
    cleaned = "".join(ch if 0x20 <= ord(ch) <= 0x7E else "_" for ch in stem)
    cleaned = "_".join(cleaned.split()).strip("._") or "sample"
    suffix = "".join(ch for ch in path.suffix.lower() if 0x20 <= ord(ch) <= 0x7E) or ".wav"
    return f"{key}__{cleaned}{suffix}"


def _fl_safe_dest_root(data: dict[str, Any]) -> Path:
    lib = data.get("sample_library")
    if lib and _is_fl_safe_path(str(Path(str(lib)).resolve().parent)):
        return Path(str(lib)).resolve().parent / _FL_SAFE_DIRNAME
    cwd = Path.cwd()
    if _is_fl_safe_path(str(cwd)):
        return cwd / _FL_SAFE_DIRNAME
    return Path(tempfile.gettempdir()) / _FL_SAFE_DIRNAME


def _ensure_fl_safe_sample(key: str, path: Path, dest_root: Path) -> Path:
    from wav_compat import convert_to_pcm24, needs_fl_conversion

    # FL's sampler plays 32-bit float WAVs as silence (snare/clap/hats/melody were
    # silent while the PCM kick/808 hit). Transcode those to 24-bit PCM — even when
    # the source path is already ASCII-safe.
    must_convert = needs_fl_conversion(path)
    if _is_fl_safe_path(str(path)) and not must_convert:
        return path

    suffix = "_pcm24.wav" if must_convert else None
    base = _fl_safe_filename(key, path)
    dest_name = base[: -len(Path(base).suffix)] + suffix if suffix else base
    dest = dest_root / dest_name
    if not _is_fl_safe_path(str(dest)):
        dest = Path(tempfile.gettempdir()) / _FL_SAFE_DIRNAME / dest_name

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Check BEFORE republishing. The publish below rewrites whatever is
        # there, so a file damaged since the last export would otherwise be
        # silently healed and we would never learn it had been damaged — which
        # is exactly how the 2026-08-14 evidence was lost.
        stale_damage = _quarantine_if_damaged(key, path, dest)
        if must_convert:
            # Always refresh the canonical file: a previous generation may
            # have selected a different 808 while keeping the same safe name.
            if not convert_to_pcm24(path, dest):
                return path
        elif (stale_damage or not dest.is_file()
              or not _same_bytes(path, dest)):
            # Was a size comparison, which cannot tell two different samples of
            # equal length apart — and these names are keyed by the source
            # path, so a same-size mismatch is exactly the case that would
            # never self-heal. 2026-08-25.
            shutil.copy2(path, dest)
    except OSError:
        return path  # fall back to original; better a maybe-red sample than crash
    return _verify_published_sample(key, path, dest, must_convert)


def _same_bytes(a: Path, b: Path) -> bool:
    """Whether two published-sample-sized files are byte-identical."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _sample_quarantine_dir() -> Path:
    from plg_paths import user_data_dir

    return user_data_dir() / "damaged_samples"


def _quarantine_if_damaged(key: str, source: Path, dest: Path, *,
                           stage: str = "found damaged before republish") -> str | None:
    """Preserve a copy of a damaged published sample. Returns the damage reason.

    The 2026-08-14 808 investigation cost a full session and still ended at
    ~85 % confidence for one reason: the damaged intermediate wav had already
    been overwritten by the next generation, so there was nothing left to
    measure. The fixes (atomic publish, headroom) make that damage very
    unlikely — this makes it *provable* if it ever happens anyway.
    """
    from wav_compat import describe_wav_damage

    if not dest.is_file():
        return None
    damage = describe_wav_damage(dest)
    if damage is None:
        return None
    _preserve_bad_sample(key, source, dest, damage, stage)
    return damage


def _preserve_bad_sample(key: str, source: Path, dest: Path, reason: str, stage: str,
                        *, marker: str = "DAMAGED SAMPLE") -> None:
    """Copy a published sample aside, whatever is wrong with it.

    Split out 2026-08-25 so the content check can use it too: a file holding
    the WRONG audio is structurally perfect, so routing it through the damage
    check would preserve nothing at all.
    """

    logger.error(
        "%s on lane '%s' (%s): %s — %s (source: %s)",
        marker, key, stage, dest.name, reason, source.name,
    )
    try:
        quarantine = _sample_quarantine_dir()
        quarantine.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        keep = quarantine / f"{stamp}__{key}__{dest.name}"
        shutil.copy2(dest, keep)
        (keep.with_suffix(keep.suffix + ".txt")).write_text(
            f"lane: {key}\nsource: {source}\npublished: {dest}\n"
            f"reason: {reason}\nstage: {stage}\nwhen: {stamp}\n",
            encoding="utf-8",
        )
        logger.error("Preserved the bad file for diagnosis: %s", keep)
    except OSError as exc:  # noqa: BLE001 — never let diagnostics break an export
        logger.error("Could not preserve the bad sample (%s)", exc)


def _verify_published_sample(key: str, source: Path, dest: Path, converted: bool) -> Path:
    """Last gate: never let a channel ship pointing at a damaged OR WRONG wav.

    Two questions, and the second was missing until 2026-08-25. "Is this a
    loadable WAV" is what `describe_wav_damage` answers; "is this the audio
    this export chose" is what the content check answers, and a structurally
    perfect file carrying some other sample passes the first while failing the
    second. Measured cost of the content half: see the timing note in
    the project issue log (not included) 0y.1 — it is a decode of one one-shot, not of the song.
    """
    from wav_compat import convert_to_pcm24, describe_wav_damage, published_content_mismatch

    damage = _quarantine_if_damaged(key, source, dest,
                                    stage="still damaged after publish")
    if damage is None:
        wrong = (
            published_content_mismatch(source, dest) if converted
            else (None if _same_bytes(source, dest) else "copy differs from its source")
        )
        if wrong is None:
            return dest
        logger.error(
            "WRONG AUDIO on lane '%s': %s does not hold what %s converts to (%s)",
            key, dest.name, source.name, wrong,
        )
        _preserve_bad_sample(key, source, dest, wrong,
                             "content mismatch before republish", marker="WRONG AUDIO")
        try:
            if converted:
                convert_to_pcm24(source, dest)
            else:
                shutil.copy2(source, dest)
        except OSError as exc:  # noqa: BLE001
            logger.error("Republish of %s failed (%s)", dest.name, exc)
            return dest
        still = (
            published_content_mismatch(source, dest) if converted
            else (None if _same_bytes(source, dest) else "copy still differs")
        )
        if still is None:
            logger.warning("Republished %s successfully after a content mismatch", dest.name)
        else:
            logger.error(
                "%s still holds the wrong audio after republish (%s); the file is "
                "most likely held open by FL — the project will use it as it is",
                dest.name, still,
            )
        return dest

    try:
        if converted:
            convert_to_pcm24(source, dest)
        else:
            shutil.copy2(source, dest)
    except OSError as exc:  # noqa: BLE001
        logger.error("Repair of %s failed (%s)", dest.name, exc)
        return source
    if describe_wav_damage(dest) is None:
        logger.warning("Republished %s successfully after damage", dest.name)
        return dest
    logger.error("%s still damaged after repair; shipping the source wav", dest.name)
    return source


_STORE_DIRNAME = "_sounds"  # the content-addressed store next to the project folders (Projects/_sounds)


def project_sounds_enabled() -> bool:
    return os.environ.get("PLG_PROJECT_SOUNDS", "").strip().lower() not in ("0", "off", "false", "no")


def _publish_project_sound(source: Path, sounds_dir: Path, store_dir: Path) -> Path:
    """Put one sound into the project's own folder and return the path the .flp should load.

    A beat's channels used to load caches (``%TEMP%/PLG_Sounds_FL``, ``%LOCALAPPDATA%/PLG/starter/_warm``) or the user's
    library: clear Temp, move the library or open the project on another computer and every channel was empty. The file
    now lives in ``<project>_sounds/`` next to the .flp. Identical content is stored once: the project's file is a hard link
    to ``Projects/_sounds/<sha1>.<ext>`` (a copy when linking is impossible, e.g. another volume). Any failure returns the
    source unchanged - better the old path than no sound, and never a failed export."""
    try:
        data = source.read_bytes()
        digest = hashlib.sha1(data).hexdigest()
        dest = sounds_dir / source.name
        if not _is_fl_safe_path(str(dest)):
            return source  # FL loads only plain printable-ASCII paths
        if dest.is_file() and dest.stat().st_size == len(data) and hashlib.sha1(dest.read_bytes()).hexdigest() == digest:
            return dest  # a re-open of the same project: already there
        store = store_dir / f"{digest}{source.suffix.lower() or '.wav'}"
        store_dir.mkdir(parents=True, exist_ok=True)
        if not store.is_file() or store.stat().st_size != len(data):
            partial = store.with_name(store.name + ".part")
            partial.write_bytes(data)
            os.replace(partial, store)
        sounds_dir.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        try:
            os.link(store, dest)
        except OSError:
            shutil.copy2(store, dest)
        if dest.stat().st_size != len(data):
            return source
        return dest
    except OSError as exc:
        logger.warning("Could not put %s into the project's sounds folder (%s); the project keeps the old path", source.name, exc)
        return source


def _sample_paths_from_data(
    data: dict[str, Any], sounds_dir: Path | None = None, store_dir: Path | None = None
) -> dict[str, Path]:
    raw = data.get("plg_sound_paths")
    if not isinstance(raw, dict):
        return {}
    dest_root = _fl_safe_dest_root(data)
    paths: dict[str, Path] = {}
    for key, value in raw.items():
        if not value:
            continue
        path = Path(str(value))
        if path.is_file():
            safe = _ensure_fl_safe_sample(str(key), path.resolve(), dest_root)
            if sounds_dir is not None and store_dir is not None and Path(safe).is_file():
                safe = _publish_project_sound(Path(safe), sounds_dir, store_dir)
            paths[str(key)] = safe
    if sounds_dir is not None:
        data["plg_project_sounds"] = {key: str(path) for key, path in paths.items() if Path(path).parent == sounds_dir}
        # The folder is PLG's own: a rewrite with another set of sounds must not leave the old files behind.
        used = {Path(path).name for path in paths.values() if Path(path).parent == sounds_dir}
        try:
            for stale in sounds_dir.iterdir():
                if stale.is_file() and stale.name not in used:
                    stale.unlink()
        except OSError:
            pass
    return paths


def _assert_exportable_808(data: dict[str, Any]) -> None:
    """Prevent stale JSON/LLM picks from bypassing the 808 audio passport."""
    if data.get("plg_midi_melody"):
        kick_times = {
            round(float(note.get("time_step", 0.0) or 0.0), 5)
            for note in track_notes(data, "kick")
            if not note.get("slide")
        }
        unpaired = [
            note for note in track_notes(data, "808_bass")
            if not note.get("slide")
            and round(float(note.get("time_step", 0.0) or 0.0), 5) not in kick_times
        ]
        if unpaired:
            raise ValueError(
                f"FLP export blocked: {len(unpaired)} 808 attack(s) have no simultaneous kick"
            )
    raw = data.get("plg_sound_paths")
    if not isinstance(raw, dict) or not raw.get("808_bass"):
        return
    from sample_match import rejection_reason_for_pattern

    path = Path(str(raw["808_bass"]))
    reason = rejection_reason_for_pattern(path, "808_bass", data)
    if reason is not None:
        raise ValueError(
            f"FLP export blocked: 808_bass failed audio passport ({reason}): {path.name}"
        )
    from sample_pitch_passport import sample_pitch_passport

    # An 808 the user chose by hand in the sound browser is theirs: neither the tail gate nor a
    # pitch class it plays badly stops the export (the notes were shortened to fit in lane_fallback).
    from lane_fallback import lane_is_explicit

    explicit = lane_is_explicit(data, "808_bass")
    passport = sample_pitch_passport(path)
    from library_quality import bass_808_tail_rejection_reason

    tail_reason = bass_808_tail_rejection_reason(path, track_notes(data, "808_bass"), float(data.get("bpm", 140)), passport)
    if tail_reason is not None and not explicit:
        raise ValueError(f"FLP export blocked: 808_bass failed tail gate ({tail_reason}): {path.name}")
    weak = set(passport.get("weak_pitch_classes") or ())
    if explicit:
        weak = set()
    bass = track_notes(data, "808_bass")
    for note in bass:
        if note.get("slide"):
            continue
        midi = parse_note_name(str(note.get("note") or "C2"))
        if not 24 <= midi <= 36:
            raise ValueError(f"FLP export blocked: 808 note outside C2-C3: {note.get('note')}")
        if midi % 12 in weak:
            raise ValueError(
                f"FLP export blocked: 808 cannot cover harmony pitch class {midi % 12}: {path.name}"
            )


def _repair_808_kick_pairs(data: dict[str, Any]) -> int:
    """Give every MIDI-mode 808 attack one simultaneous kick.

    This is deliberately narrow: it only adds missing kick companions and
    leaves MIDI, hats, snare, clap, mixer, velocities, and all existing notes
    untouched.
    """
    if not data.get("plg_midi_melody"):
        return 0
    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        return 0
    kicks = tracks.get("kick")
    bass = tracks.get("808_bass")
    if not isinstance(kicks, list) or not kicks or not isinstance(bass, list):
        return 0
    kick_times = {round(float(note.get("time_step", 0.0) or 0.0), 5) for note in kicks}
    added = 0
    for bass_note in bass:
        if not isinstance(bass_note, dict) or bass_note.get("slide"):
            continue
        time = round(float(bass_note.get("time_step", 0.0) or 0.0), 5)
        if time in kick_times:
            continue
        template = min(kicks, key=lambda note: abs(float(note.get("time_step", 0.0) or 0.0) - time))
        companion = dict(template)
        companion["time_step"] = time
        companion["kick_808_lock"] = True
        companion["midi_kick808_sealed"] = True
        companion["lowend_role"] = "paired"
        kicks.append(companion)
        kick_times.add(time)
        bass_note["kick_lock"] = True
        bass_note["lowend_role"] = "paired"
        added += 1
    if added:
        kicks.sort(key=lambda note: float(note.get("time_step", 0.0) or 0.0))
        data.setdefault("plg_808_pair_repair", {})["added_kicks"] = added
    return added


def _time_signature(data: dict[str, Any]) -> tuple[int, int]:
    raw = data.get("time_signature")
    if isinstance(raw, dict):
        num = int(raw.get("numerator", 4) or 4)
        beat = int(raw.get("denominator", 4) or 4)
        return max(1, num), max(1, beat)
    return 4, 4


def _fx_automation(data: dict[str, Any]) -> dict[str, Any]:
    fx = data.get("fx_automation")
    if isinstance(fx, dict) and fx:
        return fx
    # Default = clean warmth only. NO heavy distortion by default — a fully-wet
    # Fruity Fast Dist on the (loudest) 808 was turning FL playback into a harsh
    # clipped mess ("broken speaker") even though the preview sounded great.
    return {"Channel_Precomputed": {"boost": 0.12}}


# Hard safety caps so the 808 distortion can never destroy FL playback again.
# Fast Dist runs as a *parallel* blend (low wet mix) under the clean sub.
_DIST_MAX_WET = 0.28        # ≤28% wet — saturation, not full-wet destruction
_DIST_MAX_THRESHOLD = 4     # gentle clip threshold (out of 10), never near-max
# Channel pre-gain cap: 102/256 ≈ 40% BOOST — the top of the Yeat/Kanye drive
# window. Headroom is safe now that the 808 fader sits at -6 dB in the mix table.
_PREAMP_MAX = 102
# The kick fader already reaches FL's channel-volume ceiling, so an audible
# lift cannot come from another fader increment. A modest precomputed boost
# makes the kick own the transient while the Master Soft Clipper catches the
# peak; this is intentionally below the 808 drive ceiling.
_KICK_PREAMP = 80


def _max_beat_end(data: dict[str, Any], channels: list[str]) -> float:
    max_end = 0.0
    for key in channels:
        for entry in track_notes(data, key):
            end = step_to_beats(float(entry.get("time_step", 0))) + step_to_beats(
                float(entry.get("length", 0.25))
            )
            max_end = max(max_end, end)
    return max_end


def pattern_bars(data: dict[str, Any], *, channels: list[str] | None = None) -> int:
    """Pattern length in bars — clamped to 8–16 for FL piano roll headroom."""
    layout = channels or _channels_with_layout(data)
    beats_per_bar, _ = _time_signature(data)
    max_beats = _max_beat_end(data, layout)
    bars = max(1, math.ceil(max_beats / beats_per_bar))
    return max(MIN_PATTERN_BARS, min(MAX_PATTERN_BARS, bars))


def pattern_length_ticks(data: dict[str, Any], *, channels: list[str] | None = None) -> int:
    beats_per_bar, _ = _time_signature(data)
    bars = pattern_bars(data, channels=channels)
    return bars * beats_per_bar * PPQ


def full_song_ticks(data: dict[str, Any], *, channels: list[str] | None = None) -> int:
    """Whole-arrangement length in ticks (NO 8–16 bar clamp) so the single PLG
    pattern — which already holds the full hook/verse/chorus/outro — plays end to
    end in FL SONG mode. Uses ``plg_total_bars`` from the arranger when present."""
    layout = channels or _channels_with_layout(data)
    beats_per_bar, _ = _time_signature(data)
    total_bars = data.get("plg_total_bars")
    if isinstance(total_bars, int) and total_bars > 0:
        bars = total_bars
    else:
        bars = max(1, math.ceil(_max_beat_end(data, layout) / beats_per_bar))
    bars = max(MIN_PATTERN_BARS, min(512, bars))
    return bars * beats_per_bar * PPQ


def _playlist_pattern_clip(
    *,
    position_ticks: int,
    pattern_iid: int,
    length_ticks: int,
    track_index: int = 0,
    explicit_trim: bool = False,
) -> bytes:
    """One 60-byte playlist item placing a PATTERN clip on a playlist track.

    2026-07 audit: this was previously a 32-byte item (the pre-FL21 legacy
    size). FL Studio 2025 requires the whole PLAYLIST array to be uniform
    60-byte items ("New in FL 21" per pyflp's own PlaylistEvent struct,
    ``new = not len(data) % 60``) — with 32-byte items, FL reads the array
    at the wrong stride past the very first entry, so every clip after the
    first was silently dropped. Confirmed by generating a real .flp with
    11 clips and opening it in a real FL Studio 2025 install: with 32-byte
    items only the first (melody) clip appeared anywhere in the Playlist,
    all 10 others were simply absent (not on a wrong track — genuinely
    never placed, matching the bug report exactly); switching to 60-byte
    items made all 11 clips appear on their correct track and bar. This
    was broken since this exporter's very first version — every generation
    ever made required the user to manually drag every pattern into place.

    Byte layout + the 'always' fields are taken from real FL 2025 projects:
    position, pattern_base(20480), item_index(=base+iid), length, track_rvidx
    (tracks are stored reversed: track 1 = max-1), group, (120,0), flags(64),
    (64,100,128,128), start_offset(-1.0), end_offset(-1.0 = untrimmed),
    then 28 reserved/unused tail bytes (zeroed) required for FL21+'s item size.
    The terminal Snare Layer clip instead receives explicit uint32 bounds.
    """
    item_index = _PLAYLIST_PATTERN_BASE + pattern_iid
    track_rvidx = _PLAYLIST_MAX_TRACKS - 1 - track_index
    trim_end_ticks = int(round(max(1, length_ticks) * PPQ / _PLAYLIST_LENGTH_PPQ))
    offset_bytes = (
        struct.pack("<II", 0, trim_end_ticks)
        if explicit_trim
        else struct.pack("<ff", -1.0, -1.0)
    )
    return (
        struct.pack(
            "<IHHIHH",
            max(0, position_ticks),
            _PLAYLIST_PATTERN_BASE,
            item_index,
            max(1, length_ticks),
            track_rvidx,
            0,
        )
        + bytes([120, 0])
        + struct.pack("<H", 64)
        + bytes([64, 100, 128, 128])
        + offset_bytes
        + bytes(28)
    )


# Premium per-voice palette. Stored as R | G<<8 | B<<16 (FL RGBA, alpha 0) —
# playlist tracks, channel buttons and pattern clips share one color per voice.
def _rgb(r: int, g: int, b: int) -> int:
    return (r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16)


_PLG_COLORS: dict[str, int] = {
    # 2026-07 final palette (user-specified hex, converted to RGB):
    "melody_lead": _rgb(255, 255, 255),    # #FFFFFF — pure white, "PLG MIDI"
    "kick": _rgb(37, 37, 37),              # #252525 — charcoal black
    "808_bass": _rgb(124, 32, 32),         # #7C2020 — punch 808
    "sub_808": _rgb(74, 0, 0),             # #4A0000 — dark maroon
    "hi_hats": _rgb(184, 57, 152),         # #B83998 — acid pink
    "clap": _rgb(110, 25, 128),            # #6E1980 — deep purple
    "snare": _rgb(218, 218, 218),          # #DADADA — light titanium gray
    "snare_layer": _rgb(143, 143, 143),    # #8F8F8F — neutral gray
    # Unchanged — not part of the 2026-07 palette update:
    "counter_melody": _rgb(126, 106, 214), # lilac — the answer
    "chords": _rgb(64, 178, 166),          # teal — the bed
    "fx": _rgb(108, 200, 240),             # sky — ear candy
    "texture": _rgb(120, 136, 158),        # mist — the scene
    "cowbell": _rgb(214, 168, 84),         # brass — the Carti clank
}


def _track_color_map(clips: list[Any] | None = None) -> dict[int, int]:
    """Playlist track iid (1-based) -> color for tracks that carry PLG clips."""
    from flp_template import PLAYLIST_TRACK_FOR

    if clips is not None:
        return {
            int(clip.playlist_track) + 1: _PLG_COLORS[clip.track_key]
            for clip in clips
            if getattr(clip, "track_key", None) in _PLG_COLORS
        }
    return {
        index + 1: _PLG_COLORS[key]
        for key, index in PLAYLIST_TRACK_FOR.items()
        if key in _PLG_COLORS
    }


def _track_events(clips: list[Any] | None = None) -> bytes:
    """The 500 default playlist tracks, exactly as FL 2025 writes them —
    PLG's own tracks recolored per voice (color = bytes 4..8 of the struct)."""
    colors = _track_color_map(clips)
    out = bytearray()
    for iid in range(1, _PLAYLIST_MAX_TRACKS + 1):
        tail = _TRACK_TAIL
        color = colors.get(iid)
        if color is not None:
            tail = struct.pack("<I", color) + _TRACK_TAIL[4:]
        out += _data_event(_TRACK_DATA, struct.pack("<I", iid) + tail)
        out += _byte_event(_ARR_TRACK_SEP, 0)
    return bytes(out)


def _arrangement_events(
    *,
    clips: list[Any],
    beats_per_bar: int = 4,
    sections: list[dict[str, Any]] | None = None,
) -> bytes:
    """Playlist: one clip per instrument on its own track."""
    events = bytearray()
    events += _word_event(_ARR_NEW, 0)
    events += _text_event(_ARR_NAME, "PLG Arrangement")
    events += _byte_event(_ARR_BYTE36, 146)

    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            try:
                start_bar = max(0, int(section.get("start_bar", 0) or 0))
                occurrence = int(section.get("occurrence", 0) or 0)
            except (TypeError, ValueError):
                continue
            name = str(section.get("name") or "Section").title()
            if occurrence > 1:
                name = f"{name} {occurrence}"
            events += _dword_event(_TIME_MARKER_POSITION, start_bar * beats_per_bar * PPQ)
            events += _text_event(_TIME_MARKER_NAME, name)

    playlist = bytearray()
    terminal_snare_layer = max(
        (candidate.start_bar for candidate in clips if candidate.track_key == "snare_layer"),
        default=None,
    )
    for clip in clips:
        start_ticks = clip.start_bar * beats_per_bar * PPQ
        # The section boundary owns visual clip length.  A stale tail from a
        # sustained note must never turn the last drum block into a 60+ bar
        # rectangle in FL's Playlist (the Snare Layer Main 6 failure).
        source_end = clip.source_end_bar or (clip.start_bar + clip.length_bars)
        safe_bars = max(0.001, min(float(clip.length_bars), float(source_end) - float(clip.start_bar)))
        length_ticks = int(round(safe_bars * beats_per_bar * _PLAYLIST_LENGTH_PPQ))
        playlist += _playlist_pattern_clip(
            position_ticks=start_ticks,
            pattern_iid=clip.pattern_iid,
            length_ticks=length_ticks,
            track_index=clip.playlist_track,
            explicit_trim=(
                clip.track_key == "snare_layer"
                and terminal_snare_layer is not None
                and clip.start_bar == terminal_snare_layer
            ),
        )
    if playlist:
        events += _data_event(_PLAYLIST, bytes(playlist))

    events += _track_events(clips)
    events += _data_event(_ARR_EXTRA, _ARR_EXTRA_PAYLOAD)
    events += _word_event(_ARR_CURRENT, 0)
    return bytes(events)


def _preamp_from_fx(fx: dict[str, Any]) -> int:
    boost = 0.0
    pre = fx.get("Channel_Precomputed")
    if isinstance(pre, dict):
        boost += float(pre.get("boost", 0.0) or 0.0)
    shaper = fx.get("Fruity_WaveShaper")
    if isinstance(shaper, dict):
        boost += float(shaper.get("boost", 0.0) or 0.0) * 0.5
    return max(0, min(_PREAMP_MAX, int(round(boost * 256))))


def _fast_dist_plugin(fx: dict[str, Any]) -> bytes:
    dist = fx.get("Fruity_Fast_Dist")
    if not isinstance(dist, dict):
        return b""
    drive = float(dist.get("drive", 0.9) or 0.9)
    mix = float(dist.get("mix", 1.0) or 1.0)
    # Clamp to a tasteful parallel saturation regardless of what the LLM asked for.
    threshold = max(1, min(_DIST_MAX_THRESHOLD, int(round(1 + drive * 3))))
    safe_mix = min(_DIST_MAX_WET, max(0.0, mix))
    mix_val = max(0, min(128, int(round(safe_mix * 128))))
    payload = struct.pack("<IIIII", 128, threshold, 0, mix_val, 128)
    return _text_event(_PLUGIN_INTERNAL, "Fruity Fast Dist") + _data_event(_PLUGIN_DATA, payload)


_CH_ENVLFO = _DATA + 10  # 218 — sampler envelope/LFO page, 5 events per channel

# Without a Volume envelope FL's Sampler ignores note-off and plays the sample
# to its end — every 808 rings past its note and the pocket's breathing gaps
# (ТУК-УУУМ…тишина) never happen. The gate: instant attack, full sustain while
# the note holds, instant release on note-off. Baselines captured VERBATIM from
# a real FL save (flp_templates/sidechain_pump.flp); struct layout = PyFLP
# EnvelopeLFOEvent (17 × int32). FL's stream starts with the flags=4 /
# release_tension=-101 variant (the volume envelope per PyFLP's own notes);
# the gate goes on BOTH first slots — on the pan slot amount=0 keeps it inert.
def _envlfo(flags: int, enabled: int, attack: int, hold: int, sustain: int,
            release: int, rel_tension: int) -> bytes:
    return struct.pack(
        "<17i",
        flags, enabled, 100, attack, hold, 30000, sustain, release,
        0,          # envelope.amount (volume env ignores it; pan stays put)
        100, 20000, 0, 32950, 0,   # LFO block — FL defaults, disabled
        0, 0, rel_tension,
    )


# 808 volume envelope: sustain while the MIDI note is held (note-length =
# audible length), then a FAST release so the tail dies when the key lifts —
# frees the next hit / Cut Itself handoff. release=2500 is snappy enough to
# stop infinite hum without the old release=100 hard-click chop.
_ENV_808_SLOT1 = _envlfo(4, 1, 320, 50, 128, 2500, -120)
_ENV_808_SLOT2 = _envlfo(0, 1, 320, 50, 128, 2500, 0)
# Back-compat aliases (tests / older call sites).
_ENV_GATE_SLOT1 = _ENV_808_SLOT1
_ENV_GATE_SLOT2 = _ENV_808_SLOT2
_ENV_DEFAULT = _envlfo(0, 0, 20000, 20000, 50, 20000, 0)

# Kick volume envelope (2026-07): kick had no envelope at all — full FL
# Sampler defaults, no forced note-off cutoff. Tighter than the 808's gate
# (attack 40 vs 80, hold 20 vs 50 — a shorter "15-20%" hold, not the 808's
# legato-adjacent gate) and a much shorter release (600 vs 2500) since a kick
# transient needs no anti-click cushion the way a sustained 808 tail does —
# just enough to avoid a hard-cut click if the sample doesn't zero-cross.
_ENV_KICK_SLOT1 = _envlfo(4, 1, 40, 20, 128, 600, -120)
_ENV_KICK_SLOT2 = _envlfo(0, 1, 40, 20, 128, 600, 0)

# Clap volume envelope (2026-07): same gap as kick had — no envelope at all,
# so a clap sample with a long room-tail rings uncontrolled. Same short-gate
# shape as kick's (this is a safety net for long-tail samples, not a tone
# shaper — most clap one-shots are already short and this is inaudible on
# them).
_ENV_CLAP_SLOT1 = _envlfo(4, 1, 40, 20, 128, 600, -120)
_ENV_CLAP_SLOT2 = _envlfo(0, 1, 40, 20, 128, 600, 0)

# Melody/counter-melody volume envelope (2026-07): without ANY envelope event,
# FL's Sampler leaves the page untouched/disabled — the raw sample starts at
# full amplitude with no fade-in (audible click if the waveform isn't at a
# zero-crossing) and, on a short MIDI note, playback either rings straight
# through past note-off (no release at all) or gets hard-cut with no fade
# (digital "treck"/crackle) — reported live as exactly this. attack=500
# (a few ms — enough to kill an onset click, far too short to dull a
# transient) and release=20000 are deliberately NOT invented numbers:
# release=20000 is FL's own stock default for a freshly-enabled envelope
# (verbatim from the same real-FL-save capture as _ENV_DEFAULT above, just
# with enabled=1 here) — a fresh channel's un-touched release slider is
# already a musically sane fade, not a guess at milliseconds. sustain=128
# (max) so a long held note does NOT lose volume while held — only the
# release stage shapes the tail after note-off, preserving the "coverage"
# a long sustained sample already provides (see melody_sample_bank.py).
_MELODY_ENV_SLOT1 = _envlfo(4, 1, 500, 100, 128, 20000, -101)
_MELODY_ENV_SLOT2 = _envlfo(0, 1, 500, 100, 128, 20000, 0)


def _melody_oneshot_root_note(data: dict[str, Any], channel_key: str) -> int | None:
    """The Sampler root note of a melodic channel that plays one of PLG's own melody one-shots: 60 (FL's C5) plus the
    file's offset from ``melody_oneshots.root_offset`` (-12 for a file an octave under MIDI 60, so root 48: MIDI 60
    still sounds at the pitch it says). None for any other sound, which keeps FL's default root, as before."""
    record = data.get("plg_melody_oneshot")
    paths = data.get("plg_sound_paths")
    if not isinstance(record, dict) or not isinstance(paths, dict):
        return None
    prepared = record.get("prepared")
    if not prepared or str(paths.get(channel_key) or "") != str(prepared):
        return None
    try:
        return 60 + int(record.get("root_offset", 0))
    except (TypeError, ValueError):
        return None


def _channel_sampler_settings_events(channel_key: str, *, root_note: int | None = None) -> bytes:
    """Per-channel sampler: 808 cut/mono + stretch TIME=0; melody stretch."""
    events = bytearray()
    if (channel_key in ("sub_808", "808_bass") or channel_key in _MELODY_SAMPLER_KEYS) and root_note is not None:
        events += _dword_event(_CH_ROOT_NOTE, max(0, min(131, int(root_note))))
    if channel_key == "sub_808":
        events += _dword_event(_CH_CUT_GROUP, int.from_bytes(_SUB_808_CUT_ITSELF, "little"))
        events += _data_event(_CH_POLYPHONY, _SUB_808_POLYPHONY_MONO)
        events += _data_event(_CH_PARAMETERS, _SUB_808_STRETCH_PARAMS)
        # Soft volume envelope REQUIRED for audible FL 2025 playback — fully
        # disabled envelopes made the 808 lane silent. Long release + max
        # sustain keeps the body; raw wav (no warm/punch) stays in the path.
        events += _data_event(_CH_ENVLFO, _ENV_808_SLOT1)
        events += _data_event(_CH_ENVLFO, _ENV_808_SLOT2)
        for _ in range(3):
            events += _data_event(_CH_ENVLFO, _ENV_DEFAULT)
    elif channel_key == "808_bass":
        events += _dword_event(_CH_CUT_GROUP, int.from_bytes(_BASS_808_CUT_ITSELF, "little"))
        events += _data_event(_CH_POLYPHONY, _SUB_808_POLYPHONY_MONO)
        events += _data_event(_CH_PARAMETERS, _SUB_808_STRETCH_PARAMS)
        events += _data_event(_CH_ENVLFO, _ENV_808_SLOT1)
        events += _data_event(_CH_ENVLFO, _ENV_808_SLOT2)
        for _ in range(3):
            events += _data_event(_CH_ENVLFO, _ENV_DEFAULT)
    elif channel_key == "kick":
        # 2026-07: kick had no envelope at all (FL Sampler defaults, no
        # forced note-off) — see _ENV_KICK_SLOT1 docstring for the gate shape.
        events += _data_event(_CH_ENVLFO, _ENV_KICK_SLOT1)
        events += _data_event(_CH_ENVLFO, _ENV_KICK_SLOT2)
        for _ in range(3):
            events += _data_event(_CH_ENVLFO, _ENV_DEFAULT)
    elif channel_key == "clap":
        events += _data_event(_CH_ENVLFO, _ENV_CLAP_SLOT1)
        events += _data_event(_CH_ENVLFO, _ENV_CLAP_SLOT2)
        for _ in range(3):
            events += _data_event(_CH_ENVLFO, _ENV_DEFAULT)
    elif channel_key == "hi_hats":
        events += _dword_event(_CH_CUT_GROUP, int.from_bytes(_HAT_CUT_ITSELF, "little"))
    elif channel_key in _MELODY_SAMPLER_KEYS:
        events += _data_event(_CH_PARAMETERS, _MELODY_STRETCH_PARAMS)
        # Soft attack + real release (see _MELODY_ENV_SLOT1 docstring above) —
        # no click on note-on, no hard digital cutoff on note-off.
        events += _data_event(_CH_ENVLFO, _MELODY_ENV_SLOT1)
        events += _data_event(_CH_ENVLFO, _MELODY_ENV_SLOT2)
        for _ in range(3):  # Mod X / Mod Y / Pitch — untouched FL defaults
            events += _data_event(_CH_ENVLFO, _ENV_DEFAULT)
    return bytes(events)


def _recipe_808_boost(data: dict[str, Any] | None) -> int:
    """Sampler-level Precomputed BOOST for the 808, 25–40% by genre drive.

    This is the sound the sampler screenshot was missing: a clean sine 808 with
    every knob at zero. The mix recipe's sub_boost_db (rage 7 dB … pop 1 dB)
    maps into the 0.25–0.40 window so the waveform saturates into harmonics
    that survive phone speakers."""
    if not isinstance(data, dict):
        return 0
    try:
        from style_corpus import mix_recipe_for_pattern

        recipe = mix_recipe_for_pattern(data) or {}
    except Exception:  # noqa: BLE001 — export must never fail on recipe lookup
        return 0
    if not recipe.get("sub_drive"):
        return 0
    intensity = max(0.0, min(1.0, float(recipe.get("sub_boost_db", 3.0)) / 9.0))
    return int(round((0.25 + 0.15 * intensity) * 256))


def _channel_fx_events(
    channel_key: str,
    fx: dict[str, Any],
    *,
    enabled: bool,
    data: dict[str, Any] | None = None,
) -> bytes:
    from mix_insert_fx import melody_channel_reverb_mix, melody_space_enabled

    events = bytearray()
    space = data is not None and melody_space_enabled(data)
    if channel_key == "kick":
        events += _word_event(_CH_PREAMP, _KICK_PREAMP)
        events += _word_event(_CH_FX_FLAGS, _FX_FLAG_CLIP)
        return bytes(events)
    if channel_key in ("sub_808", "808_bass"):
        # Commercial slap: Precomputed BOOST + CLIP whenever the genre recipe
        # wants sub_drive (trap/rage/drill…). Quiet sine 808s need harmonics
        # that survive phone speakers; Soft Clipper on Master catches the push.
        preamp = _preamp_from_fx(fx) if enabled else 0
        meta = data.get("plg_producer_meta") if isinstance(data, dict) else None
        # ``sub_808`` is the intentionally driven legacy bass lane.  The
        # user-facing ``808_bass`` lane is often an already-saturated library
        # sample, so applying recipe drive there regardless of intent stacked
        # clipping and caused the farty distortion.  Its destructive path is
        # therefore an explicit max-drive choice only.
        recipe_boost = _recipe_808_boost(data) if channel_key == "sub_808" else 0
        if channel_key == "808_bass" and isinstance(meta, dict) and meta.get("max_drive"):
            recipe_boost = _recipe_808_boost(data)
        if recipe_boost:
            preamp = max(preamp, recipe_boost)
        preamp = min(_PREAMP_MAX, preamp)
        if preamp > 0:
            events += _word_event(_CH_PREAMP, preamp)
            events += _word_event(_CH_FX_FLAGS, _FX_FLAG_CLIP)
        # Saturation / light drive — harmonics so the 808 reads on phones.
        # 2026-07 fix: 808_bass used to get Fruity_Fast_Dist unconditionally
        # (a hardcoded default via dist_fx.setdefault(...), applied even when
        # fx_automation never asked for it and even with enabled=False) —
        # stacked on top of the preamp/clip above and an already-produced
        # library sample, this is what made 808_bass sound distorted/"farty".
        # Now gated exactly like sub_808: only when the FX recipe explicitly
        # requested Fruity_Fast_Dist.
        if enabled and channel_key in ("sub_808", "808_bass") and fx.get("Fruity_Fast_Dist"):
            events += _fast_dist_plugin(fx)
        return bytes(events)
    if not enabled and not space:
        return b""
    if channel_key in _MELODY_SAMPLER_KEYS and space:
        events += _dword_event(_CH_REVERB, melody_channel_reverb_mix())
    elif channel_key == "melody_lead" and enabled:
        events += _dword_event(_CH_REVERB, 64)
    elif channel_key == "snare_layer" and space:
        # Clap-double symbiosis (see duplicate_clap_layer): wide + reverberant
        # for the "expensive" stacked-clap sound, reusing the same space_mix
        # opt-in and reverb-mix formula melody already uses — no new flag.
        events += _dword_event(_CH_REVERB, melody_channel_reverb_mix())
    return bytes(events)


def build_template_flp(*, fl_exe: Path | None = None) -> bytes:
    """Empty template: named channels, mixer buses, Master Soft Clipper, no notes."""
    from flp_template import CORE_TEMPLATE_CHANNELS, PATTERN_DISPLAY, pattern_iid_for

    data: dict[str, Any] = {
        "bpm": 140,
        "plg_use_template": True,
        "plg_total_bars": 8,
        "tracks": {},
    }
    channels = list(CORE_TEMPLATE_CHANNELS)
    events = bytearray()
    events += _text_event(_VERSION, flp_header_version(fl_exe), unicode=False)
    events += _dword_event(_TEMPO, 140_000)
    events += _byte_event(_TIME_SIG_NUM, 4)
    events += _byte_event(_TIME_SIG_BEAT, 4)

    for index, key in enumerate(channels):
        events += _word_event(_CH_NEW, index)
        events += _byte_event(_CH_TYPE, 0)
        events += _text_event(_CH_NAME, PATTERN_DISPLAY.get(key, key))
        events += _byte_event(_CH_ROUTED_TO, _channel_route(key))
        events += _channel_sampler_settings_events(key)
        if legacy_mix_enabled():
            events += _word_event(_CH_VOL, _DEFAULT_CHANNEL_VOL.get(key, _FL_VOL_DEFAULT))

    pat_ticks = 8 * 4 * PPQ
    # 2026-07: use pattern_iid_for(key) per core channel, not a slice of
    # PATTERN_ORDER — PATTERN_ORDER is no longer required to start with
    # CORE_TEMPLATE_CHANNELS as a literal contiguous prefix (see
    # PATTERN_ORDER's own definition), so a positional slice could silently
    # pick up the wrong keys/IIDs.
    for key in CORE_TEMPLATE_CHANNELS:
        iid = pattern_iid_for(key)
        events += _word_event(_PAT_NEW, iid)
        events += _text_event(_PAT_NAME, PATTERN_DISPLAY.get(key, key))
        events += _byte_event(_PAT_LOOPED, 1)
        events += _dword_event(_PAT_LENGTH, pat_ticks)

    events += _mixer_events_build(data, channels)
    header = b"FLhd" + struct.pack("<IhHH", 6, 0, len(channels), PPQ)
    body = b"FLdt" + struct.pack("<I", len(events)) + bytes(events)
    return header + body


def _apply_808_passport_register(data: dict[str, Any]) -> None:
    """Last-write octave calibration for the actual selected 808 WAV.

    Humanization can rebuild low-end notes after sample selection.  Running
    this immediately before FLP serialization ensures the audible WAV
    passport, rather than an earlier C3 default, owns the final register.
    """
    paths = data.get("plg_sound_paths")
    bass_path = paths.get("808_bass") if isinstance(paths, dict) else None
    bass = track_notes(data, "808_bass")
    if not bass_path or not bass:
        return
    try:
        from music_theory import name_for_midi
        from sample_pitch_passport import sample_pitch_passport

        passport = sample_pitch_passport(Path(str(bass_path)))
        octave = max(2, min(3, int(passport.get("preferred_note_octave", 2))))
        low = octave * 12
        pitch_class_map = passport.get("pitch_class_map")
        if isinstance(pitch_class_map, dict) and pitch_class_map:
            # Each pitch class gets ITS OWN best-measured register (see
            # sample_pitch_passport._preferred_808_register) rather than every
            # note being squeezed into one flat window built from just the
            # octave-root scores. A sample's optimal register commonly SPLITS
            # across pitch classes (comb-filtering/formant nulls shift with
            # the transposition ratio) — a single window cannot represent
            # that, and previously pushed whichever notes didn't fit down a
            # full octave from where they actually sound good (the reported
            # "D3/E3 weak, F3/G3 fine on the same sample" bug).
            for note in bass:
                pc = parse_note_name(str(note.get("note") or "C3")) % 12
                target = pitch_class_map.get(str(pc))
                if target is None:
                    target = low + pc
                target = int(target)
                if target % 12 != pc or not 24 <= target <= 36:
                    raise ValueError("808 passport produced an invalid C2-C3 harmonic target")
                note["note"] = name_for_midi(target)
        else:
            # Passport unavailable/degenerate (no pitch_class_map): fall back
            # to the old flat-window behavior rather than leaving notes
            # untouched.
            safe_min = int(passport.get("safe_note_min", low))
            safe_max = int(passport.get("safe_note_max", low + 11))
            for note in bass:
                pc = parse_note_name(str(note.get("note") or "C3")) % 12
                candidates = [pc + 12 * octave_index for octave_index in range(1, 7) if safe_min <= pc + 12 * octave_index <= safe_max]
                if candidates:
                    target = min(candidates, key=lambda midi: abs(midi - (low + pc)))
                else:
                    target = min(36, max(24, low + pc))
                note["note"] = name_for_midi(target)
        data.setdefault("plg_sample_pitch_passports", {})["808_bass"] = passport
        data["plg_808_register"] = {
            "octave": octave,
            "safe_min": int(passport.get("safe_note_min", low)),
            "safe_max": int(passport.get("safe_note_max", low + 11)),
            "pitch_class_map": pitch_class_map if isinstance(pitch_class_map, dict) else None,
            "source": "sample_pitch_passport_final_export",
        }
    except Exception:
        # A non-WAV/missing path must never prevent a user from exporting.
        return


def prepare_pattern_for_flp_export(data: dict[str, Any]) -> None:
    """Apply deterministic FLP prerequisites to the canonical pattern.

    This is deliberately separate from byte serialization. Normal generation,
    rebake and Open-in-FL call it before saving JSON/MIDI so an 808 register or
    MIDI-only pairing repair cannot exist only inside the final .flp.  The
    writer calls it too for direct/legacy callers, where idempotence keeps the
    compatibility fallback safe.
    """
    if data.get("plg_midi_melody"):
        # This is also called by direct/legacy FLP writers, which may bypass
        # backend_core's normal MIDI validation. The uploaded MIDI contract is
        # still absolute at that boundary: no generated extra lane may enter
        # the final Playlist merely because a caller skipped the usual route.
        from midi_validate import sanitize_midi_mode

        sanitize_midi_mode(data)

    _repair_808_kick_pairs(data)
    _apply_808_passport_register(data)
    if legacy_mix_enabled():
        from mix_insert_fx import configure_space_mix

        configure_space_mix(data)


def build_flp(
    data: dict[str, Any],
    *,
    fl_exe: Path | None = None,
    sounds_dir: Path | None = None,
    store_dir: Path | None = None,
) -> bytes:
    """Template-based export — isolated patterns per instrument + multi-track playlist."""
    from flp_template import (
        PATTERN_DISPLAY,
        active_export_channels,
        active_pattern_keys,
        build_playlist_clips,
        total_bars,
    )
    prepare_pattern_for_flp_export(data)
    _assert_exportable_808(data)

    bpm = float(data.get("bpm", 140))
    channels = active_export_channels(data)
    if not channels:
        channels = list(active_export_channels({"tracks": {"kick": [{"time_step": 0, "note": "C1", "length": 0.25, "velocity": 100}]}}))
    # A note's rack_channel MUST reference the channel's own _CH_NEW index —
    # i.e. its position in THIS export's active/filtered channel list, not its
    # fixed position in flp_template.TEMPLATE_CHANNELS. Channels are skipped
    # from `channels` whenever they're non-core and have no notes (e.g. no
    # "fx" content this generation) — every channel AFTER a skipped one then
    # shifts down by one _CH_NEW slot, while the old rack_index(key) lookup
    # kept returning the unshifted TEMPLATE_CHANNELS position. That silently
    # pointed notes at the WRONG channel (or, for the last channel(s), at an
    # index past the actual channel count entirely) — confirmed as the real
    # cause of FL's "There are invalid notes in this project" warning and the
    # FLEngine crash on Play (2026-07 audit: a real generation with chords +
    # texture content but no fx content reproduced this exactly).
    channel_index = {key: index for index, key in enumerate(channels)}
    legacy_mix = legacy_mix_enabled()
    if data.get("plg_midi_melody"):
        route_map = {key: index + 1 for index, key in enumerate(channels)}
    else:
        from flp_template import CHANNEL_MIXER_INSERT

        route_map = {key: CHANNEL_MIXER_INSERT.get(key, 0) for key in channels}

    sample_paths = (
        _sample_paths_from_data(data, sounds_dir, store_dir) if sounds_dir is not None else _sample_paths_from_data(data)
    )
    fx = _fx_automation(data)
    fl_fx = _fl_fx_enabled(data)
    mixer_levels = _mixer_levels_from_data(data)
    meta = data.get("plg_producer_meta") if isinstance(data.get("plg_producer_meta"), dict) else {}
    max_drive = bool(meta.get("max_drive") or data.get("plg_filth_mode") or data.get("plg_clipper_mode"))
    # Soft Clipper glue: push kick+808 into Master Soft Clipper (Rich Amiri),
    # unless max_drive already applies the hotter push.
    clipper_glue = bool(
        meta.get("clipper_drive_kick_808", True)
        and _master_soft_clip_enabled(data)
        and not max_drive
    )
    sig_num, sig_beat = _time_signature(data)
    bars = total_bars(data)
    beats_per_bar, _ = _time_signature(data)
    clips = build_playlist_clips(data)
    layout_audit = data.get("plg_playlist_layout_audit")
    if not isinstance(layout_audit, dict) or not layout_audit.get("passed"):
        raise ValueError(f"Playlist export blocked: invalid clip coverage/bounds: {layout_audit}")

    events = bytearray()
    events += _text_event(_VERSION, flp_header_version(fl_exe), unicode=False)
    events += _dword_event(_TEMPO, int(round(bpm * 1000)))
    events += _byte_event(_TIME_SIG_NUM, sig_num)
    events += _byte_event(_TIME_SIG_BEAT, sig_beat)

    for index, key in enumerate(channels):
        events += _word_event(_CH_NEW, index)
        events += _byte_event(_CH_TYPE, _CH_TYPE_SAMPLER)
        events += _text_event(_CH_NAME, CHANNEL_NAMES.get(key, PATTERN_DISPLAY.get(key, key)))
        if key in _PLG_COLORS:
            events += _dword_event(_PLUGIN_COLOR, _PLG_COLORS[key])

        sample_path = sample_paths.get(key)
        if sample_path is not None:
            events += _text_event(_CH_SAMPLE_PATH, str(sample_path))
        root_note = None
        if sample_path is not None and key in ("sub_808", "808_bass"):
            try:
                from sample_pitch_passport import sample_pitch_passport

                # ``sample_path`` may be a float-WAV converted to PCM24
                # for FL compatibility. That conversion deliberately
                # rewrites the RIFF container and can drop its ``smpl``
                # author root tag. Calibrate from the original selected
                # WAV (or the passport stamped before conversion), never
                # from the export copy.
                raw_paths = data.get("plg_sound_paths")
                source_path = raw_paths.get(key) if isinstance(raw_paths, dict) else None
                source = Path(str(source_path or sample_path))
                passport = sample_pitch_passport(source)
                data.setdefault("plg_sample_pitch_passports", {})[key] = passport
                # Same trust rule the sample gate used to accept this file
                # (sample_match: 808_root_unverified). Setting the root only
                # for the strict "ok" would leave a gate-approved 808 with
                # FL's default root and play it at the wrong pitch.
                if passport.get("root_trusted", passport.get("status") == "ok"):
                    root_note = int(passport["fl_root_note"])
            except Exception as exc:  # noqa: BLE001
                logger.info("808 pitch passport skipped for %s (%s)", sample_path, exc)
        elif sample_path is not None and key in _MELODY_SAMPLER_KEYS:
            root_note = _melody_oneshot_root_note(data, key)
        events += _channel_sampler_settings_events(key, root_note=root_note)
        if legacy_mix:
            events += _channel_fx_events(key, fx, enabled=fl_fx, data=data)
        events += _byte_event(_CH_ROUTED_TO, _channel_route(key, route_map))
        # Default mixer mode writes no channel volume: FL keeps its own default.
        level = (
            _channel_level(
                key,
                mixer_levels,
                max_drive=max_drive,
                clipper_glue=clipper_glue,
                sample_path=sample_path,
            )
            if legacy_mix
            else None
        )
        if level is not None:
            events += _word_event(_CH_VOL, level)

    pattern_clips: list[Any] = []
    seen_pattern_ids: set[int] = set()
    for clip in clips:
        if clip.pattern_iid in seen_pattern_ids:
            continue
        seen_pattern_ids.add(clip.pattern_iid)
        pattern_clips.append(clip)

    # One whole-song pattern per channel (the default): a note's tail was always cut at the end of its SECTION (it used to
    # be a section pattern, and a note may not run past its own pattern). The cut is kept exactly, so the beat sounds as
    # before; only the splitting into patterns is gone.
    from flp_template import _arrangement_sections, pattern_sections_enabled

    section_ends: list[tuple[float, float]] = []
    if not pattern_sections_enabled():
        section_ends = [
            (section["start"] * beats_per_bar, section["end"] * beats_per_bar)
            for section in _arrangement_sections(data, bars)
        ]

    for clip in pattern_clips:
        key = clip.track_key
        iid = clip.pattern_iid
        rack_ch = channel_index[key]
        start_beat = clip.start_bar * beats_per_bar
        source_end_bar = clip.source_end_bar or (clip.start_bar + clip.length_bars)
        end_beat = source_end_bar * beats_per_bar
        raw_notes = [
            dict(note)
            for note in track_notes(data, key)
            if start_beat <= float(note.get("time_step", 0.0)) < end_beat
        ]
        pat_notes = []
        for note in raw_notes:
            note["time_step"] = round(float(note.get("time_step", 0.0)) - start_beat, 5)
            pat_notes.append(note)
        # Same bound for the pattern declaration itself.  Playlist and
        # Pattern can no longer disagree about the visible end of a drum clip.
        source_extent = max(0.001, float(source_end_bar) - float(clip.start_bar))
        pat_bars = max(0.001, min(float(clip.length_bars), source_extent))
        pat_ticks = max(PPQ, int(round(pat_bars * beats_per_bar * PPQ)))

        events += _word_event(_PAT_NEW, iid)
        base_name = PATTERN_DISPLAY.get(key, CHANNEL_NAMES.get(key, key))
        events += _text_event(_PAT_NAME, clip.pattern_name or base_name)
        if key in _PLG_COLORS:
            events += _dword_event(_PAT_COLOR, _PLG_COLORS[key])
        # A section pattern already contains its complete local MIDI.  Leaving
        # it looped lets FL extend a terminal drum pattern visually/audio-wise
        # when a malformed or legacy Playlist length is encountered.
        events += _byte_event(_PAT_LOOPED, 0)
        events += _dword_event(_PAT_LENGTH, pat_ticks)

        notes = bytearray()
        for entry in pat_notes:
            limit = pat_ticks
            if section_ends:
                at = float(entry.get("time_step", 0.0)) + start_beat  # the song position of this note
                end = next((section_end for section_start, section_end in section_ends if section_start <= at < section_end), None)
                if end is not None:
                    limit = min(pat_ticks, int(round((end - start_beat) * PPQ)))
            try:
                notes += _note_struct(rack_ch, entry, channel_key=key, max_ticks=limit)
            except (KeyError, ValueError, TypeError):
                continue
        if notes:
            events += _data_event(_PAT_NOTES, bytes(notes))

    if clips:
        events += _arrangement_events(
            clips=clips,
            beats_per_bar=beats_per_bar,
            sections=data.get("plg_arrangement"),
        )
    events += _mixer_events(data, channels, route_map=route_map)

    header = b"FLhd" + struct.pack("<IhHH", 6, 0, len(channels), PPQ)
    body = b"FLdt" + struct.pack("<I", len(events)) + bytes(events)
    return header + body


def flp_base_word(pattern: dict[str, Any]) -> str:
    """First word of the source MIDI's filename, sanitized for a filename.

    midi_ingest.merge_notes_into_pattern tags every ingestion tier (uploaded,
    library-matched, corpus) with pattern["plg_midi_ingest"]["name"] — the
    real filename regardless of which tier supplied the melody. Falls back
    to "Session" when no MIDI actually fed it (e.g. the LLM composed its
    own), which is also the historical name so old sessions stay legible.
    """
    ingest = pattern.get("plg_midi_ingest")
    name = str((ingest or {}).get("name") or "").strip()
    if not name:
        return "Session"
    stem = Path(name).stem
    for sep in (" ", "_", "-", "."):
        stem = stem.replace(sep, "\x00")
    first_word = stem.split("\x00", 1)[0]
    safe = "".join(ch for ch in first_word if ch.isalnum())
    return safe or "Session"


def next_versioned_flp_path(root: Path, base_word: str) -> Path:
    """Next unused ``PLG_<word>__vNN.flp`` under root.

    Every generation gets its own file instead of silently overwriting the
    last one — the same problem Windows Explorer solves with "(1)", "(2)"
    on a duplicate save, but with a readable version tag instead.
    """
    for version in range(1, 1000):
        candidate = root / f"PLG_{base_word}__v{version:02d}.flp"
        if not candidate.exists():
            return candidate
    return root / f"PLG_{base_word}__v999.flp"


def project_sounds_dirs(output_path: Path) -> tuple[Path, Path] | None:
    """``(<project>_sounds, Projects/_sounds)`` for a .flp, or None when the path cannot hold FL-loadable sounds."""
    output_path = Path(output_path)
    sounds_dir = output_path.with_name(output_path.stem + "_sounds")
    store_dir = output_path.parent / _STORE_DIRNAME
    if not (_is_fl_safe_path(str(sounds_dir)) and _is_fl_safe_path(str(store_dir))):
        return None
    return sounds_dir, store_dir


def write_flp_session(
    data: dict[str, Any],
    output_path: Path,
    *,
    fl_exe: Path | None = None,
    bundle_sounds: bool = False,
) -> Path:
    """Write ``PLG_Session.flp`` from template architecture + pattern data.

    ``bundle_sounds`` puts the project's sounds into ``<name>_sounds/`` next to the .flp (see ``_publish_project_sound``) so the
    project opens after Temp is cleaned or the library moves. Off for a bare call; ``PLG_PROJECT_SOUNDS=off`` turns it off
    for the app."""
    from flp_template import ensure_template_file

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_template_file()
    dirs = project_sounds_dirs(output_path) if bundle_sounds and project_sounds_enabled() else None
    if bundle_sounds and project_sounds_enabled() and dirs is None:
        logger.warning("Project sounds folder skipped: %s is not a plain-ASCII path (FL cannot load samples from it)", output_path.parent)
    if dirs is not None:
        output_path.write_bytes(build_flp(data, fl_exe=fl_exe, sounds_dir=dirs[0], store_dir=dirs[1]))
    else:
        output_path.write_bytes(build_flp(data, fl_exe=fl_exe))
    return output_path


if __name__ == "__main__":
    import json
    import sys

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output_pattern.json")
    pattern = json.loads(src.read_text(encoding="utf-8"))
    out = write_flp_session(pattern, Path("PLG_Session.flp"))
    print(f"Wrote {out} ({out.stat().st_size} bytes)")

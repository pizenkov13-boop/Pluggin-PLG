"""PLG FLP template — fixed channel rack, mixer inserts, and playlist skeleton.

The export loads ``assets/plg_template.flp`` (or builds it on first run) so FL Studio
opens a project with named channels, routed mixer buses, and Master Soft Clipper
already in place. ``flp_writer`` injects per-instrument patterns and playlist clips.
"""

from __future__ import annotations

import logging
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from plg_paths import bundle_dir
from pattern_utils import track_notes

logger = logging.getLogger("plg.flp_template")

BEATS_PER_BAR = 4.0
PPQ = 960

# Governs note rack_channel assignment ONLY (via active_export_channels()'s
# enumerate() — see flp_writer.build_flp), NOT the visual Channel Rack/
# Patterns list order in FL Studio itself — that's PATTERN_ORDER, below.
# 2026-07: confirmed by reordering THIS tuple alone (moving "808_bass" next
# to "kick") and opening the result in a real FL Studio 2025 — playback/note
# routing was unaffected (as expected, rack_channel is dynamically computed
# per export, not read from a fixed position — the old rack_index() helper
# that WOULD have broken on reorder was removed after confirming nothing in
# build_flp() still used it), but the Channel Rack's on-screen order did NOT
# change to match; it kept following PATTERN_ORDER instead. Reordering this
# tuple is safe (for note-routing correctness) but on its own does not move
# anything on screen.
TEMPLATE_CHANNELS: tuple[str, ...] = (
    "melody_lead",
    "kick",
    "808_bass",
    "sub_808",
    "hi_hats",
    "clap",
    "snare",
    "snare_layer",
    "counter_melody",
    "fx",
    "chords",
    "texture",
    "cowbell",
)

CORE_TEMPLATE_CHANNELS: tuple[str, ...] = (
    "melody_lead",
    "kick",
    "sub_808",
    "hi_hats",
    "clap",
)

# Pattern IID 1..N — one isolated pattern per instrument (fixes piano-roll
# clutter). 2026-07 finding: THIS tuple's order is what FL Studio's Channel
# Rack / Patterns list actually displays top-to-bottom (confirmed visually
# in a real FL Studio 2025 — reordering TEMPLATE_CHANNELS alone did not move
# anything on screen; reordering this one did). Deliberately NOT derived as
# `CORE_TEMPLATE_CHANNELS + tail` anymore — that formula forced any new
# entry placed among the core channels to also become core (always-present
# even with no notes), which "808_bass" here should NOT be. Every key still
# needs a valid pattern (build_playlist_clips/active_pattern_keys iterate
# this directly), core-ness is governed independently by
# CORE_TEMPLATE_CHANNELS/active_export_channels — build_template_flp() uses
# pattern_iid_for(key) per core channel rather than assuming this tuple
# starts with them, so core/non-core can interleave here freely.
PATTERN_ORDER: tuple[str, ...] = (
    "melody_lead", "kick", "808_bass", "sub_808", "hi_hats", "clap",
    "snare", "snare_layer", "counter_melody", "fx", "chords", "texture", "cowbell",
)

PATTERN_DISPLAY: dict[str, str] = {
    "melody_lead": "PLG MIDI",
    "kick": "PLG Kick",
    "808_bass": "PLG 808 Bass",
    "sub_808": "PLG Sub 808",
    "hi_hats": "PLG Hi-Hats",
    "clap": "PLG Clap",
    "snare": "PLG Snare",
    "snare_layer": "PLG Snare Layer",
    "counter_melody": "PLG Counter Melody",
    "fx": "PLG FX",
    "chords": "PLG Chords",
    "texture": "PLG Texture",
    "cowbell": "PLG Cowbell",
}

# Playlist track index (0 = FL track 1). Independent of TEMPLATE_CHANNELS'
# order — kept in the same visual order here so the Playlist's track rows
# match the Channel Rack's order too.
PLAYLIST_TRACK_FOR: dict[str, int] = {
    "melody_lead": 0,
    "kick": 1,
    "808_bass": 2,
    "sub_808": 3,
    "hi_hats": 4,
    "clap": 5,
    "snare": 6,
    "snare_layer": 7,
    "counter_melody": 8,
    "fx": 9,
    "chords": 10,
    "texture": 11,
    "cowbell": 12,
}

# 0-indexed bar where drums enter the playlist (bar 9 in FL UI).
DRUM_PLAYLIST_START_BAR = 8

DRUM_EXPORT_KEYS = frozenset(
    {"kick", "sub_808", "808_bass", "hi_hats", "clap", "snare", "snare_layer", "cowbell"}
)

MIDI_HIDDEN_EXPORT_KEYS = frozenset(
    {"sub_808", "counter_melody", "fx", "chords", "texture", "cowbell"}
)

# Channel rack → mixer insert (matches template mixer names below).
# Every voice gets its OWN insert: sharing (counter on the melody bus, layer on
# the snare bus) merged their FX/levels and the voices couldn't be mixed apart.
CHANNEL_MIXER_INSERT: dict[str, int] = {
    "melody_lead": 4,
    "kick": 1,
    "sub_808": 2,
    "hi_hats": 3,
    "clap": 6,
    "snare": 5,
    "snare_layer": 7,
    "counter_melody": 8,
    "fx": 9,
    "chords": 10,
    "texture": 11,
    "cowbell": 12,
    "808_bass": 13,
}

# (insert_index, display_name, master_with_soft_clip)
MIXER_TEMPLATE_INSERTS: tuple[tuple[int, str, bool], ...] = (
    (0, "Master", True),
    (1, "PLG Kick", False),
    (2, "PLG Sub 808", False),
    (3, "PLG Hi-Hats", False),
    (4, "PLG MIDI", False),
    (5, "PLG Snare", False),
    (6, "PLG Clap", False),
    (7, "PLG Snare Layer", False),
    (8, "PLG Counter", False),
    (9, "PLG FX", False),
    (10, "PLG Chords", False),
    (11, "PLG Texture", False),
    (12, "PLG Cowbell", False),
    (13, "PLG 808 Bass", False),
)

FL_MIXER_INSERT_COUNT = 125


@dataclass(frozen=True)
class PlaylistClip:
  """One pattern clip on the FL playlist timeline."""

  track_key: str
  pattern_iid: int
  playlist_track: int
  start_bar: int
  length_bars: float
  section_name: str = ""
  source_end_bar: int = 0
  pattern_name: str = ""


_DRUM_PATTERN_BANK_KEYS = frozenset(
    {"kick", "hi_hats", "clap", "snare", "snare_layer", "cowbell"}
)


def _section_role(name: str) -> str:
    lowered = name.lower()
    if "intro" in lowered:
        return "Intro"
    if "outro" in lowered:
        return "Outro"
    if "drop" in lowered:
        return "Drop"
    if "hook" in lowered or "chorus" in lowered:
        return "Alt"
    return "Main"


# Lanes whose cosmetic dust magnitude has actually been measured (see the
# constants cited below) — _clip_content_signature only tolerates jitter on
# these; every other track_key stays on today's exact-match behavior.
# hi_hats is deliberately excluded: apply_hi_hat_swing's PPQ_SWING_MIN/MAX
# (beat_humanize.py, 6-9 ticks @ PPQ96 ~= 0.06-0.09 beat) is an order of
# magnitude bigger than the drum/bass dust below and sits uncomfortably
# close to real 1/32 spacing — extending tolerance there needs its own
# profiling pass, not a guess.
_FUZZY_SIGNATURE_TRACK_KEYS = frozenset({"kick", "808_bass", "clap", "snare"})

# Half-width of these grids must stay comfortably above every known dust
# source in the lanes above and comfortably below the finest real spacing
# those lanes use, so quantizing can only ever make two ALREADY-identical
# (pitch, count, structure) clips compare equal — never merge genuinely
# different content. Known dust: bass808_gen._KICK808_TIMING_JITTER_MS =
# (2.0, 6.0) ms, bass808_gen._KICK808_VELOCITY_JITTER = 6,
# beat_humanize.PRE_SNARE_SHIFT_MS = (2.0, 5.0) ms. Finest real spacing:
# drum_blueprint's 1/16 grid (0.25 beat), bass808_gen._MIN_GAP_BEATS = 0.75.
# If a future dust source in these 4 lanes ever exceeds half of either grid
# below, this tolerance needs revisiting.
_SIG_TIME_GRID = 0.05  # beats; half-width 0.025, ~5x below the 0.25-beat 1/16 grid
_SIG_VELOCITY_GRID = 16  # half-width 8, vs known jitter of 6 and real tiers 60+ apart


def _quantize(value: float, grid: float) -> float:
    return round(round(float(value) / grid) * grid, 5)


def _clip_final_chorus(data: dict[str, Any], clip: PlaylistClip) -> bool:
    """Whether this clip's section is the arrangement's final-chorus peak.

    Folded into the signature explicitly rather than left to emerge from
    velocity differences alone: the final chorus is deliberately louder
    (arranger._develop_section's ~5-8% energy bump) than a regular repeat
    of the same section, but for a quiet note that could be only a few
    velocity points — potentially inside _SIG_VELOCITY_GRID by coincidence.
    An explicit flag means the peak section can never accidentally collapse
    into a regular repeat, regardless of how the velocity numbers round.
    """
    arrangement = data.get("plg_arrangement")
    if not isinstance(arrangement, list):
        return False
    for section in arrangement:
        if isinstance(section, dict) and int(section.get("start_bar", -1) or 0) == clip.start_bar:
            return bool(section.get("final_chorus"))
    return False


def _clip_content_signature(data: dict[str, Any], clip: PlaylistClip) -> tuple[Any, ...]:
    """Local pattern content; equal signatures are safe to reuse.

    Pitch, note count/order-as-content, and every boolean/categorical note
    flag (slide/ghost/fill/tweak/kick_lock/bass808_gen/kick_808_lock/pan/
    section/...) always compare exact, for every track_key — two clips
    whose content genuinely differs in shape always stay distinct patterns,
    exactly like before this change. Only for _FUZZY_SIGNATURE_TRACK_KEYS,
    the continuous-valued time_step/length/velocity fields are quantized to
    a tolerance grid before comparing, so cosmetic dust (a few ms of timing,
    a few points of velocity — see the constants above) no longer defeats
    reuse of an otherwise-identical repeat. This is NOT the "role-based
    forced reuse" this module already tried and reverted (see
    _apply_pattern_bank's docstring) — that merged clips by label without
    looking at content at all; this still requires every note's pitch and
    count to already match, note-for-note.
    """
    start_beat = clip.start_bar * BEATS_PER_BAR
    source_end = (clip.source_end_bar or (clip.start_bar + clip.length_bars)) * BEATS_PER_BAR
    fuzzy = clip.track_key in _FUZZY_SIGNATURE_TRACK_KEYS
    encoded: list[str] = []
    for raw in track_notes(data, clip.track_key):
        time = float(raw.get("time_step", 0.0))
        if not start_beat <= time < source_end:
            continue
        note = dict(raw)
        note["time_step"] = round(time - start_beat, 5)
        if fuzzy:
            note["time_step"] = _quantize(note["time_step"], _SIG_TIME_GRID)
            if "length" in note:
                note["length"] = _quantize(note["length"], _SIG_TIME_GRID)
            if "velocity" in note:
                note["velocity"] = _quantize(note["velocity"], _SIG_VELOCITY_GRID)
            # Pure diagnostic record of a raw dust value already reflected
            # (tolerantly) in time_step above — e.g. beat_humanize.
            # apply_pre_snare_shift stores the exact ms it shifted a clap/
            # snare note by. Left in place, its full-precision value would
            # silently defeat the quantizing just above it: two notes whose
            # actual timing now compares equal would still never match
            # because each carries its own distinct shift amount on the
            # note itself. Not a note-identity field (nothing downstream
            # reads it for anything beyond logging/debugging), so it's
            # dropped for the fuzzy lanes rather than quantized.
            note.pop("pre_snare_shift_ms", None)
        encoded.append(json.dumps(note, sort_keys=True, ensure_ascii=False, default=str))
    # Order-independent: still exact-content comparison, just insensitive to
    # track_notes' iteration order — strictly reduces false negatives (never
    # causes a false merge, since it's the same set of encoded notes either
    # way).
    encoded.sort()
    return (
        clip.track_key,
        round(clip.length_bars, 5),
        _clip_final_chorus(data, clip),
        tuple(encoded),
    )


def _apply_pattern_bank(data: dict[str, Any], clips: list[PlaylistClip]) -> list[PlaylistClip]:
    """Reuse only byte-for-byte equivalent local MIDI patterns.

    Role-based forced reuse looked tidy but silently changed kick/808 pairing
    and drum density. Content addressing gives the producer-friendly win when
    a real repeat exists, while guaranteeing the exported song is unchanged.
    """
    signature_to_iid: dict[tuple[Any, ...], int] = {}
    signature_to_name: dict[tuple[Any, ...], str] = {}
    # An uploaded MIDI is the author's song idea.  Anchor only its melody
    # refrain.  Reusing the entire kit made the song a literal drum loop:
    # verse/hook/drop labels changed while kick, bass, hats and backbeat did
    # not.  The melody carries recognition; each drum lane remains free to
    # develop between sections.
    hook_anchors: dict[tuple[str, float], tuple[Any, ...]] = {}
    role_counts: dict[tuple[str, str], int] = {}
    next_iid = 1
    out: list[PlaylistClip] = []
    for clip in clips:
        signature = _clip_content_signature(data, clip)
        if data.get("plg_midi_melody") and clip.track_key == "melody_lead":
            section = str(clip.section_name or "").lower()
            anchor_key = (clip.track_key, round(clip.length_bars, 5))
            if section.startswith("hook"):
                hook_anchors.setdefault(anchor_key, signature)
            elif section.startswith(("drop", "chorus")):
                signature = hook_anchors.get(anchor_key, signature)
        if signature not in signature_to_iid:
            signature_to_iid[signature] = next_iid
            base_name = PATTERN_DISPLAY.get(clip.track_key, clip.track_key)
            role = "Hook" if data.get("plg_midi_melody") and clip.track_key == "melody_lead" and str(clip.section_name or "").lower().startswith(("hook", "drop", "chorus")) else (_section_role(clip.section_name) if clip.section_name else "Main")
            count_key = (clip.track_key, role)
            role_counts[count_key] = role_counts.get(count_key, 0) + 1
            suffix = "" if role_counts[count_key] == 1 else f" {role_counts[count_key]}"
            signature_to_name[signature] = f"{base_name} {role}{suffix}"
            next_iid += 1
        out.append(replace(
            clip,
            pattern_iid=signature_to_iid[signature],
            pattern_name=signature_to_name[signature],
        ))
    return out


def template_flp_path() -> Path:
    return bundle_dir() / "assets" / "plg_template.flp"


def pattern_iid_for(track_key: str) -> int:
    try:
        return PATTERN_ORDER.index(track_key) + 1
    except ValueError:
        return 1


def midi_hidden_keys(data: dict[str, Any]) -> frozenset[str]:
    """Lanes a MIDI-owned beat suppresses in FL.

    LLM remnants (counter/fx/chords/texture) always go, except the two lanes
    ``midi_roles`` filled from the user's own notes. The empty core
    ``sub_808`` slot also goes whenever ``808_bass`` is the live 808 — otherwise
    FL keeps a blank row between Kick and Hi-Hats (the classic index gap).
    Genres that only have ``sub_808`` keep that one lane.

    Variant B (2026-10): a drum lane the user has no usable file for is recorded
    as absent (``lane_fallback.lane_is_absent``) and is left out of the .flp
    too: no channel, no pattern, no playlist row. Its notes stay in the pattern
    data (the generation and the audits read them). The melody is never hidden
    this way: it keeps its notes and carries no sample by design.
    """
    if not data.get("plg_midi_melody"):
        return frozenset()
    from lane_fallback import ABSENT_LANES, lane_is_absent
    from midi_roles import role_lane_live

    hidden = {key for key in MIDI_HIDDEN_EXPORT_KEYS if not role_lane_live(data, key)}
    hidden.update(key for key in ABSENT_LANES if lane_is_absent(data, key))
    return frozenset(hidden)


_BASS808_POCKET_KEYS = frozenset({"kick", "808_bass"})


def _bass808_lane_visible(data: dict[str, Any], key: str) -> bool:
    """Dormant safety net for the bass808_enabled family (kick/808_bass —
    see beat_humanize.build_kick_808_pocket). Both lanes normally DO have
    notes again (generation was restored 2026-07 after a brief removal),
    so this rarely has anything to add — active_pattern_keys/
    playlist_track_map_for_export filter on track_notes, with no core
    exemption for "kick", so if a genuinely degenerate/very short span ever
    makes the generator return zero notes for either lane, it would
    silently lose its PATTERN/PLAYLIST ROW (and ``808_bass``, not being
    core, would vanish from the CHANNEL list too). ``plg_bass808_lane_active``
    (set unconditionally whenever the profile opts in) keeps both visible —
    same registered sample + EQ, same pattern/clip/rack slot, contiguous
    kick->808_bass block, not a gap — even in that edge case. Additive
    only — never changes the CORE_TEMPLATE_CHANNELS/track_notes checks
    these functions already do."""
    return key in _BASS808_POCKET_KEYS and bool(data.get("plg_bass808_lane_active"))


def active_export_channels(data: dict[str, Any]) -> list[str]:
    """Channels present in the pattern, in fixed template order — no blank slots."""
    hidden = midi_hidden_keys(data)
    out: list[str] = []
    for key in TEMPLATE_CHANNELS:
        if key in hidden:
            continue
        if key in CORE_TEMPLATE_CHANNELS or track_notes(data, key) or _bass808_lane_visible(data, key):
            out.append(key)
    return out


def active_pattern_keys(data: dict[str, Any]) -> list[str]:
    hidden = midi_hidden_keys(data)
    return [
        key
        for key in PATTERN_ORDER
        if key not in hidden and (track_notes(data, key) or _bass808_lane_visible(data, key))
    ]


def playlist_track_map_for_export(data: dict[str, Any]) -> dict[str, int]:
    """Playlist rows for this export — dense 0..N-1, no reserved holes."""
    if not data.get("plg_midi_melody"):
        return dict(PLAYLIST_TRACK_FOR)
    hidden = midi_hidden_keys(data)
    # Only lanes that actually ship clips — assigning every non-hidden key
    # (even empty ones) re-created the Kick / 808 / [gap] / Hats hole.
    ordered = [
        key
        for key, _index in sorted(PLAYLIST_TRACK_FOR.items(), key=lambda item: item[1])
        if key not in hidden and (track_notes(data, key) or _bass808_lane_visible(data, key))
    ]
    return {key: index for index, key in enumerate(ordered)}


def total_bars(data: dict[str, Any]) -> int:
    raw = data.get("plg_total_bars")
    if isinstance(raw, int) and raw > 0:
        return raw
    max_end = 0.0
    for key in TEMPLATE_CHANNELS:
        for entry in track_notes(data, key):
            end = float(entry.get("time_step", 0)) + float(entry.get("length", 0.25))
            max_end = max(max_end, end)
    return max(8, int(max_end // BEATS_PER_BAR) + (1 if max_end % BEATS_PER_BAR else 0))


def shift_notes_for_pattern(
    track_key: str,
    notes: list[dict[str, Any]],
    *,
    drum_start_bar: int = DRUM_PLAYLIST_START_BAR,
) -> list[dict[str, Any]]:
    """Drum patterns are relative to their playlist clip start."""
    if track_key not in DRUM_EXPORT_KEYS:
        return notes
    entry = drum_start_bar * BEATS_PER_BAR
    out: list[dict[str, Any]] = []
    for raw in notes:
        step = float(raw.get("time_step", 0))
        if step < entry - 1e-6:
            continue
        note = dict(raw)
        note["time_step"] = round(step - entry, 5)
        out.append(note)
    return out


def playlist_start_bar_for(data: dict[str, Any], track_key: str) -> int:
    """Return the real first bar for a playlist lane.

    The arranger stores absolute song-time notes. A historical fixed bar-8
    offset discarded valid early drums whenever an intro was shorter than
    eight bars, so the clip offset must follow the actual first note.
    """
    if track_key not in DRUM_EXPORT_KEYS:
        return 0
    notes = track_notes(data, track_key)
    if not notes:
        return 0
    first_step = min(max(0.0, float(note.get("time_step", 0.0))) for note in notes)
    return int(first_step // BEATS_PER_BAR)


def _arrangement_sections(data: dict[str, Any], bars: int) -> list[dict[str, Any]]:
    """Return ordered, valid song sections used solely for Playlist layout."""
    raw = data.get("plg_arrangement")
    if not isinstance(raw, list):
        return []
    sections: list[dict[str, Any]] = []
    for section in raw:
        if not isinstance(section, dict):
            continue
        start = max(0, int(section.get("start_bar", 0) or 0))
        length = max(1, int(section.get("bars", 0) or 0))
        if start >= bars:
            continue
        sections.append({
            "start": start,
            "end": min(bars, start + length),
            "name": str(section.get("name") or "section").title(),
            "occurrence": int(section.get("occurrence", 0) or 0),
        })
    return sorted(sections, key=lambda section: section["start"])


def _section_label(section: dict[str, Any]) -> str:
    occurrence = int(section.get("occurrence", 0) or 0)
    return f"{section['name']} {occurrence}" if occurrence > 0 else str(section["name"])


def _lane_section_windows(notes: list[dict[str, Any]], sections: list[dict[str, Any]]) -> list[tuple[int, int, float, str]]:
    """Make editable section windows without cutting any note at a boundary.

    Notes belong to the section containing their onset. When a note sustains
    past the boundary, only that section clip is extended by the exact tail;
    the next section still starts on-grid as its own pattern. The small visual
    overlap preserves playback without chaining an entire lane into one clip.
    """
    if not notes or not sections:
        return []
    prepared = [
        (float(note.get("time_step", 0.0)), float(note.get("length", 0.0)))
        for note in notes
    ]
    windows: list[tuple[int, int, float, str]] = []
    for section in sections:
        start_beat = section["start"] * BEATS_PER_BAR
        end_beat = section["end"] * BEATS_PER_BAR
        starts_here = [
            (time, length)
            for time, length in prepared
            if start_beat <= time < end_beat
        ]
        if not starts_here:
            continue
        tail_end_beat = max(time + max(0.0, length) for time, length in starts_here)
        playback_end_beat = min(sections[-1]["end"] * BEATS_PER_BAR, max(end_beat, tail_end_beat))
        length_bars = (playback_end_beat - start_beat) / BEATS_PER_BAR
        windows.append((section["start"], section["end"], length_bars, _section_label(section)))
    return windows


def _windows_cover_notes_once(
    notes: list[dict[str, Any]],
    windows: list[tuple[int, int, float, str]],
) -> bool:
    """Coverage gate: section layout may never lose or duplicate an onset."""
    for note in notes:
        time = float(note.get("time_step", 0.0))
        matches = sum(
            1
            for start, source_end, _length, _label in windows
            if start * BEATS_PER_BAR <= time < source_end * BEATS_PER_BAR
        )
        if matches != 1:
            return False
    return True


def _fallback_clip_end_bar(notes: list[dict[str, Any]], *, start_bar: int, bars: int) -> int:
    """Where a lane's single fallback clip should visually end.

    This path only runs when the per-section windowed layout can't be used
    for a lane (``_windows_cover_notes_once`` failed — one onset landed
    outside every arrangement section, e.g. a phrase-bound accent with no
    covering section). The clip used to unconditionally span
    ``[start_bar, bars)`` — the WHOLE remaining song — regardless of where
    that lane's actual content stops. One stray onset was then enough to
    stretch the clip's visible length across an empty tail dozens of bars
    long (the reported "Snare Layer lands on a three-digit bar" symptom:
    the clip, not any note, was drawn out to the song's total bar count).
    Bounding it to the lane's own last onset (+1 bar of breathing room for
    its tail/decay) keeps the clip honest about what it actually contains,
    while still never exceeding the real song length.
    """
    last_end_beat = max(
        (
            float(note.get("time_step", 0.0) or 0.0) + max(0.0, float(note.get("length", 0.0) or 0.0))
            for note in notes
            if isinstance(note, dict)
        ),
        default=None,
    )
    if last_end_beat is None:
        return bars
    natural_end_bar = int(last_end_beat // BEATS_PER_BAR) + 1
    return max(start_bar + 1, min(bars, natural_end_bar + 1))


def pattern_sections_enabled() -> bool:
    """``PLG_PATTERN_SECTIONS=on`` brings back one pattern per lane PER SECTION (Intro / Alt / Main / Drop ...).

    Off by default: one pattern per channel holding the whole beat, one clip on the playlist across the whole song."""
    return os.environ.get("PLG_PATTERN_SECTIONS", "").strip().lower() in ("1", "on", "true", "yes")


def _whole_song_clips(data: dict[str, Any], bars: int, playlist_tracks: dict[str, int]) -> list[PlaylistClip]:
    """One pattern per channel with every note of the beat, and one playlist clip that runs from the bar of the channel's FIRST
    note to the end of the song.

    The notes, their times and the sounds are the sectioned layout's exactly; only the cutting into patterns is gone, and the pattern
    names are the plain channel names ("PLG Kick"), with no section suffix. A channel that enters at bar 9 gets a clip that starts at
    bar 9 (its pattern then begins there too): the clip's own position does it, no trim bytes are involved, so it is the same for every
    FL version."""
    clips = []
    for index, key in enumerate(active_pattern_keys(data), start=1):
        first = min((max(0.0, float(note.get("time_step", 0.0) or 0.0)) for note in track_notes(data, key)), default=0.0)
        start_bar = min(max(0, int(first // BEATS_PER_BAR)), max(0, bars - 1))
        clips.append(
            PlaylistClip(key, index, playlist_tracks.get(key, 0), start_bar, bars - start_bar, "", bars, PATTERN_DISPLAY.get(key, key))
        )
    return clips


def build_playlist_clips(data: dict[str, Any]) -> list[PlaylistClip]:
    """Map arranged notes to Playlist patterns: one whole-song pattern per channel (default), or short editable
    section patterns with ``PLG_PATTERN_SECTIONS=on``.

    The sectioned layout falls back to the historical single clip per lane when no valid arranger
    sections are available, preserving exports from older projects.
    """
    bars = total_bars(data)
    playlist_tracks = playlist_track_map_for_export(data)
    whole_song = not pattern_sections_enabled()
    sections = [] if whole_song else _arrangement_sections(data, bars)
    clips: list[PlaylistClip] = []
    fallback_lanes: list[str] = []
    next_pid = 1
    for key in [] if whole_song else active_pattern_keys(data):
        pl_track = playlist_tracks.get(key, 0)
        notes = track_notes(data, key)
        windows = _lane_section_windows(notes, sections)
        if windows and not _windows_cover_notes_once(notes, windows):
            windows = []
            fallback_lanes.append(key)
        if windows:
            for start, source_end, length, label in windows:
                clips.append(PlaylistClip(key, next_pid, pl_track, start, length, label, source_end))
                next_pid += 1
            continue

        # Older/non-arranged exports retain their original, single-clip layout.
        if key in ("melody_lead", "counter_melody", "fx", "chords", "texture"):
            clips.append(PlaylistClip(key, next_pid, pl_track, 0, bars, "", bars))
        else:
            start_bar = playlist_start_bar_for(data, key)
            end_bar = _fallback_clip_end_bar(notes, start_bar=start_bar, bars=bars)
            clips.append(PlaylistClip(key, next_pid, pl_track, start_bar, max(1, end_bar - start_bar), "", bars))
        next_pid += 1
    clips = _whole_song_clips(data, bars, playlist_tracks) if whole_song else _apply_pattern_bank(data, clips)
    data["plg_playlist_clips"] = [
        {
            "track_key": c.track_key,
            "pattern_iid": c.pattern_iid,
            "playlist_track": c.playlist_track,
            "start_bar": c.start_bar,
            "length_bars": c.length_bars,
            "section_name": c.section_name,
            "source_end_bar": c.source_end_bar,
            "pattern_name": c.pattern_name,
        }
        for c in clips
    ]
    assigned_notes = 0
    source_notes = 0
    for key in active_pattern_keys(data):
        notes = track_notes(data, key)
        source_notes += len(notes)
        lane_clips = [clip for clip in clips if clip.track_key == key]
        for note in notes:
            time = float(note.get("time_step", 0.0))
            assigned_notes += sum(
                1
                for clip in lane_clips
                if clip.start_bar * BEATS_PER_BAR
                <= time
                < (clip.source_end_bar or (clip.start_bar + clip.length_bars)) * BEATS_PER_BAR
            )
    data["plg_playlist_layout_audit"] = {
        "passed": assigned_notes == source_notes and all(
            0 <= clip.start_bar
            and clip.length_bars > 0
            and clip.start_bar + clip.length_bars <= bars + 1e-6
            for clip in clips
        ),
        "sectioned": bool(sections),
        "clip_count": len(clips),
        "source_notes": source_notes,
        "assigned_notes": assigned_notes,
        "visual_bounds_ok": all(
            0 <= clip.start_bar
            and clip.length_bars > 0
            and clip.start_bar + clip.length_bars <= bars + 1e-6
            for clip in clips
        ),
        "song_end_bar": bars,
        "max_visual_end_bar": round(
            max((clip.start_bar + clip.length_bars for clip in clips), default=0.0), 5
        ),
        "fallback_lanes": fallback_lanes,
        "tail_overlap_clips": sum(
            1
            for clip in clips
            if clip.source_end_bar
            and clip.length_bars > (clip.source_end_bar - clip.start_bar) + 1e-6
        ),
        "unique_pattern_count": len({clip.pattern_iid for clip in clips}),
        "reused_clip_count": len(clips) - len({clip.pattern_iid for clip in clips}),
        "pattern_bank_mode": "exact_content",
        "deduplicated_lanes": sorted({
            clip.track_key
            for clip in clips
            if sum(1 for other in clips if other.pattern_iid == clip.pattern_iid) > 1
        }),
    }
    return clips


def ensure_template_file(path: Path | None = None) -> Path:
    """Create or refresh ``assets/plg_template.flp`` (Master Soft Clipper baked in)."""
    target = path or template_flp_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    meta = target.with_suffix(".meta")
    from flp_writer import _TEMPLATE_MIXER_REV, _template_has_valid_master_clipper, build_template_flp

    rev_ok = meta.is_file() and meta.read_text(encoding="utf-8").strip() == str(_TEMPLATE_MIXER_REV)
    if (
        target.is_file()
        and target.stat().st_size > 256
        and rev_ok
        and _template_has_valid_master_clipper(target.read_bytes())
    ):
        return target
    target.write_bytes(build_template_flp())
    meta.write_text(str(_TEMPLATE_MIXER_REV), encoding="utf-8")
    logger.info("Wrote FLP template %s (%d bytes)", target, target.stat().st_size)
    return target

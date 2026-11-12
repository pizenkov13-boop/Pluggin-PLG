"""2026-07 audit: FL Studio 2025 warned "There are invalid notes in this
project" on a real generated .flp. Traced to two independent, pre-existing
defects (neither introduced by the same day's counter-melody/hook-buildup
work — see tests/test_sparse_counter_melody_fallback.py and
tests/test_hook_buildup.py, both proven clean/inert in the flagged file):

1. flp_writer._note_struct floored a note's position at 0 but never capped
   position+length against the containing FL pattern's own declared length —
   groove-lag (apply_melody_groove_lag) pushes tail-end notes a few ticks past
   the arrangement boundary with no ceiling check, so FL sees a note
   sustaining past its own pattern's end (confirmed in the flagged file:
   melody_lead/sub_808/texture notes ending at 208.03-208.05 beats vs a
   208.0-beat total).
2. chord_bed.py's per-note humanize jitter (rng.uniform(-0.015, 0.02)) had no
   floor, so the very first chord of the song (bar 0, start=0.0) had ~43%
   odds of landing at a negative time_step (confirmed: -0.0042). Masked by
   flp_writer's own floor clamp before hitting the binary, but wrong at the
   source and reaches any other consumer of the pattern JSON.

A third bug surfaced in the same investigation (not a crash risk, but found
while tracing why BUILDUP_SECTIONS never appeared in the flagged file):
generation_variety.attach_meta() rebuilt pattern["plg_variety"] from a
hand-picked key allowlist that didn't include "hook_entry", so the
buildup-vs-cold-open roll from build_context() never reached arrange_song()
— the feature was completely inert in production."""

from __future__ import annotations

import struct

import dataclasses

from chord_bed import compose_chord_bed
from flp_writer import PPQ, _note_struct, build_flp
from generation_variety import attach_meta, build_context
from genre_profiles import PROFILES


def _note(time_step: float = 0.0, length: float = 4.0) -> dict:
    return {"time_step": time_step, "note": "C3", "length": length, "velocity": 100}


# --- Fix 1: flp_writer.py upper clamp on position+length ------------------


def _unpack_position_length(blob: bytes) -> tuple[int, int]:
    body = blob[14:]
    assert body[:4] == b"FLdt"
    size = struct.unpack_from("<I", body, 4)[0]
    stream = memoryview(body)[8 : 8 + size]
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
            payload = bytes(stream[offset : offset + length])
            offset += length
            if eid == 224 and payload:  # _PAT_NOTES
                position = struct.unpack_from("<I", payload, 0)[0]
                note_length = struct.unpack_from("<I", payload, 8)[0]
                return position, note_length
    raise AssertionError("no _PAT_NOTES event found")


def test_note_struct_without_max_ticks_is_unchanged():
    entry = {"time_step": 4.0, "note": "C3", "length": 2.0, "velocity": 100}
    packed = _note_struct(0, entry)
    position, _flags, _rack, length = struct.unpack_from("<IHHI", packed, 0)
    assert position == 4 * PPQ
    assert length == 2 * PPQ


def test_note_struct_clamps_position_plus_length_to_max_ticks():
    max_ticks = 8 * PPQ
    entry = {"time_step": 7.0, "note": "C3", "length": 4.0, "velocity": 100}  # would end at 11 beats
    packed = _note_struct(0, entry, max_ticks=max_ticks)
    position, _flags, _rack, length = struct.unpack_from("<IHHI", packed, 0)
    assert position + length <= max_ticks
    assert length >= 1  # never collapses to a zero-length (invalid) note


def test_note_struct_leaves_notes_inside_bounds_untouched():
    max_ticks = 8 * PPQ
    entry = {"time_step": 1.0, "note": "C3", "length": 2.0, "velocity": 100}
    packed = _note_struct(0, entry, max_ticks=max_ticks)
    position, _flags, _rack, length = struct.unpack_from("<IHHI", packed, 0)
    assert position == PPQ
    assert length == 2 * PPQ


def test_end_to_end_tail_overshoot_note_is_clamped_in_the_written_flp():
    """The exact real-world shape: a melody note pushed by groove-lag to end
    a few ticks past the arrangement's declared total length."""
    data = {
        "bpm": 150,
        "plg_total_bars": 52,
        "tracks": {
            "kick": [_note(0.0, 0.4)],
            "snare": [_note(1.0, 0.5)],
            "clap": [_note(1.0, 0.5)],
            "sub_808": [_note(0.0, 2.0)],
            "hi_hats": [_note(0.25, 0.25)],
            # 206.035 + 2.0 = 208.035 beats -- past the 52-bar (208-beat) end.
            "melody_lead": [_note(206.035, 2.0)],
        },
        "fx_automation": {},
    }
    blob = build_flp(data)
    total_ticks = 52 * 4 * PPQ
    position, length = _unpack_position_length(blob)
    assert position + length <= total_ticks


# --- Fix 2: chord_bed.py floor on the humanize jitter ----------------------


def _progression_pattern(seed: int) -> dict:
    return {
        "bpm": 140,
        "plg_progression": {
            "chord_tones_pc": [[0, 3, 7], [5, 8, 0], [8, 0, 3], [3, 7, 10]],
            "bar_degrees": [0, 3, 4, 2],
            "key": "A",
            "scale": "natural_minor",
        },
        "plg_arrangement": [{"name": "hook", "start_bar": 0, "bars": 8, "occurrence": 1}],
        "plg_variety": {"seed": seed},
        "tracks": {"chords": []},
    }


def test_bar_zero_chord_never_goes_negative_across_many_seeds():
    for seed in range(200):
        data = _progression_pattern(seed)
        compose_chord_bed(data)
        chords = data["tracks"].get("chords") or []
        assert chords, f"seed {seed} produced no chords"
        bar_zero = [n for n in chords if n["time_step"] < 4.0]
        assert bar_zero, f"seed {seed} has no bar-0 chord notes"
        for note in bar_zero:
            assert note["time_step"] >= 0.0, (seed, note)


def test_chord_bed_jitter_still_off_grid_away_from_bar_zero():
    """The floor only bites at the very start of the song — humanization
    elsewhere (interior bars) must still read as off-grid, not snapped."""
    data = _progression_pattern(seed=1)
    compose_chord_bed(data)
    chords = data["tracks"]["chords"]
    interior = [n for n in chords if 4.0 <= n["time_step"] < 8.0]
    assert interior
    assert any(n["time_step"] not in (4.0, 6.5) for n in interior)


# --- Fix 3: generation_variety.py hook_entry allowlist ---------------------


def test_attach_meta_carries_hook_entry_through():
    ctx = {"seed": 1, "hook_entry": "buildup"}
    pattern: dict = {}
    attach_meta(pattern, ctx)
    assert pattern["plg_variety"]["hook_entry"] == "buildup"


def test_build_context_hook_entry_survives_attach_meta_round_trip(monkeypatch):
    forced = dataclasses.replace(PROFILES["trap"], hook_buildup_chance=1.0)
    monkeypatch.setitem(PROFILES, "trap", forced)
    ctx = build_context("dark trap 140", genre_hint="trap")
    assert ctx["hook_entry"] == "buildup"
    pattern: dict = {}
    attach_meta(pattern, ctx)
    assert pattern["plg_variety"]["hook_entry"] == "buildup"


def test_buildup_sections_actually_fire_end_to_end_now(monkeypatch):
    """The real regression this closes: before the fix, hook_entry was rolled
    but silently dropped before arrange_song ever saw it — BUILDUP_SECTIONS
    was provably inert in production. Wire the exact same real functions
    (build_context -> attach_meta -> arrange_song) and confirm "prehook"
    actually appears now."""
    from arranger import arrange_song

    forced = dataclasses.replace(PROFILES["trap"], hook_buildup_chance=1.0)
    monkeypatch.setitem(PROFILES, "trap", forced)
    ctx = build_context("dark trap 140", genre_hint="trap")
    assert ctx["hook_entry"] == "buildup"

    pattern = {
        "bpm": 140,
        "tracks": {
            "kick": [_note(0.0, 0.4)],
            "snare": [_note(1.0, 0.5)],
            "clap": [_note(1.0, 0.5)],
            "sub_808": [_note(0.0, 2.0)],
            "hi_hats": [_note(0.25, 0.25)],
            "melody_lead": [_note(0.0, 4.0)],
        },
    }
    attach_meta(pattern, ctx)
    arrange_song(pattern)
    assert "prehook" in [s["name"] for s in pattern["plg_arrangement"]]

""""Sub 808 as its own instrument" audit (2026-07): real FL portamento
between root changes (apply_sub_808_glide + flp_writer's flags=8 wiring),
and a one-note register-lift accent at hook/chorus/drop landings
(apply_808_register_lift). Both opt-in per genre — see genre_profiles.py's
sub_808_glide / sub_808_register_lift."""

from __future__ import annotations

import struct

from beat_humanize import apply_808_breath, apply_808_register_lift, apply_sub_808_glide
from flp_template import DRUM_PLAYLIST_START_BAR
from flp_writer import _NOTE_FLAG_SLIDE, _note_struct
from pattern_utils import parse_note_name


def _note(time_step: float, note: str, length: float = 4.0) -> dict:
    return {"time_step": time_step, "note": note, "length": length, "velocity": 100}


# --- apply_sub_808_glide ----------------------------------------------------


def test_glide_tags_every_note_but_the_first():
    notes = [_note(0.0, "C2"), _note(4.0, "D2"), _note(8.0, "E2")]
    out = apply_sub_808_glide(notes)
    assert not out[0].get("glide")
    assert out[1].get("glide") is True
    assert out[2].get("glide") is True


def test_glide_empty_notes_is_a_noop():
    assert apply_sub_808_glide([]) == []


# --- apply_808_breath: must respect glide on EITHER side of the gap --------


def test_breath_does_not_cut_a_gap_before_a_glide_destination_note():
    """The off-by-one this fix closes: FL's slide flag (and apply_sub_808_
    glide's tagging) lives on the DESTINATION note — the one arriving via
    portamento — not the one before it. A gap-cutting check that only looked
    at the note BEFORE the boundary would still chop silence into the very
    gap the glide needs closed. Two notes 2 beats apart, second tagged
    glide: breath must leave the first note's length untouched (no gap)."""
    notes = [_note(0.0, "C2", length=2.0), {**_note(2.0, "D2", length=2.0), "glide": True}]
    out = apply_808_breath(notes)
    first = next(n for n in out if n["time_step"] == 0.0)
    assert first["length"] == 2.0  # untouched — reaches all the way to the next note


def test_breath_still_cuts_a_gap_between_two_ordinary_notes():
    """Sanity: the breath/pocket feature must still work normally when
    neither side of the boundary is glide/slide-tagged."""
    notes = [_note(0.0, "C2", length=2.0), _note(2.0, "D2", length=2.0)]
    out = apply_808_breath(notes)
    first = next(n for n in out if n["time_step"] == 0.0)
    assert first["length"] < 2.0  # a breath gap WAS cut


def test_glide_does_not_mutate_the_input_list():
    notes = [_note(0.0, "C2"), _note(4.0, "D2")]
    apply_sub_808_glide(notes)
    assert "glide" not in notes[1]  # original dicts untouched (deepcopy)


# --- _note_struct: real FL slide/portamento bit -----------------------------


def _unpack_flags(blob: bytes) -> int:
    _pos, flags, _rack, _len = struct.unpack_from("<IHHI", blob, 0)
    return flags


def test_note_struct_sets_slide_bit_for_glide_tagged_notes():
    entry = {"time_step": 4.0, "note": "C3", "length": 2.0, "velocity": 100, "glide": True}
    packed = _note_struct(0, entry)
    assert _unpack_flags(packed) & _NOTE_FLAG_SLIDE


def test_note_struct_sets_slide_bit_for_slide_tagged_notes():
    """The pre-existing add_808_bar_slides stab — same flag, same mechanism."""
    entry = {"time_step": 4.0, "note": "C3", "length": 0.125, "velocity": 118, "slide": True}
    packed = _note_struct(0, entry)
    assert _unpack_flags(packed) & _NOTE_FLAG_SLIDE


def test_note_struct_flags_carry_no_slide_bit_for_an_ordinary_note():
    # 2026-07: flags is no longer 0 for a plain note — bit 14 (_NOTE_FLAG_SELECTABLE)
    # is now always set (see flp_writer.py's _MIDI_CH/_NOTE_FLAG_SELECTABLE comment:
    # without it FL Studio renders the note as a non-selectable "ghost"). Only the
    # slide bit is conditional, so that's what an "ordinary note" test should assert.
    entry = {"time_step": 4.0, "note": "C3", "length": 2.0, "velocity": 100}
    packed = _note_struct(0, entry)
    assert not (_unpack_flags(packed) & _NOTE_FLAG_SLIDE)


# --- apply_808_register_lift -------------------------------------------------


def _arrangement():
    return [
        {"name": "intro", "start_bar": 0, "bars": 4},
        {"name": "hook", "start_bar": 4, "bars": 8},
        {"name": "verse", "start_bar": 12, "bars": 8},
        {"name": "chorus", "start_bar": 20, "bars": 8},
    ]


def test_register_lift_raises_only_the_landing_note():
    data = {"plg_arrangement": _arrangement()}
    notes = [
        _note(0.0, "C2"),    # intro — untouched
        _note(16.0, "C2"),   # hook landing (bar 4 * 4 beats) — lifted
        _note(20.0, "C2"),   # still inside hook, not the landing beat — untouched
        _note(80.0, "C2"),   # chorus landing (bar 20 * 4 beats) — lifted
    ]
    out = apply_808_register_lift(notes, data)
    by_step = {round(n["time_step"], 2): n for n in out}
    assert parse_note_name(by_step[0.0]["note"]) == parse_note_name("C2")
    assert parse_note_name(by_step[16.0]["note"]) == parse_note_name("C2") + 12
    assert by_step[16.0]["register_lift"] is True
    assert parse_note_name(by_step[20.0]["note"]) == parse_note_name("C2")
    assert parse_note_name(by_step[80.0]["note"]) == parse_note_name("C2") + 12


def test_register_lift_noop_without_hook_chorus_drop_sections():
    data = {"plg_arrangement": [{"name": "verse", "start_bar": 0, "bars": 8}]}
    notes = [_note(0.0, "C2")]
    out = apply_808_register_lift(notes, data)
    assert parse_note_name(out[0]["note"]) == parse_note_name("C2")
    assert "register_lift" not in out[0]


def test_register_lift_noop_without_arrangement():
    notes = [_note(0.0, "C2")]
    assert apply_808_register_lift(notes, {}) == notes


# --- genre gating -------------------------------------------------------------


def test_genre_flags_default_off_and_opt_in_where_enabled():
    from genre_profiles import PROFILES

    assert PROFILES["rage"].sub_808_glide is False
    assert PROFILES["rage"].sub_808_register_lift is False
    assert PROFILES["toliver"].sub_808_glide is True
    assert PROFILES["trap"].sub_808_register_lift is True
    assert PROFILES["drill"].sub_808_register_lift is True


# --- end-to-end: full pipeline through to the actual exported .flp bytes ----


def test_glide_reaches_the_actual_exported_flp_bytes_for_a_glide_genre():
    """The full chain for real: arrange_song -> humanize_pattern (toliver,
    sub_808_glide=True) -> build_flp. At least one sub_808 note in the real
    exported binary must carry the FL slide bit — this is what the live FL
    Studio check verifies audibly; this test verifies it structurally on
    every run. Goes through arrange_song (not a short hand-built pattern) so
    the song is long enough for sub_808 notes to actually survive
    flp_template's bar-8 drum-playlist shift (DRUM_EXPORT_KEYS includes
    sub_808) — a too-short test pattern would silently export zero sub_808
    notes and this test would pass for the wrong reason."""
    from arranger import arrange_song
    from beat_humanize import humanize_pattern
    from drum_blueprint import build_kick_pattern
    from flp_writer import build_flp

    kick = build_kick_pattern(bars=8)
    pattern = {
        "bpm": 132,
        "style": "toliver",
        "user_prompt": "floating toliver vibe",
        "plg_variety": {"genre": "toliver", "seed": 3},
        "plg_progression": {"roots_pc": [5, 8, 0, 3, 5, 8, 0, 3]},
        "tracks": {
            "kick": kick,
            "melody_lead": [
                {"time_step": i * 2.0, "note": ("C4", "E4", "G4")[i % 3], "length": 1.5, "velocity": 95}
                for i in range(16)
            ],
        },
    }
    arrange_song(pattern)
    assert pattern["plg_total_bars"] > DRUM_PLAYLIST_START_BAR + 1  # sanity: long enough to matter
    out = humanize_pattern(pattern)
    assert any(n.get("glide") for n in out["tracks"]["sub_808"])

    blob = build_flp(out)
    body = blob[14:]
    size = struct.unpack_from("<I", body, 4)[0]
    stream = memoryview(body)[8 : 8 + size]

    _PAT_NEW, _PAT_LENGTH, _PAT_NOTES = 65, 164, 224
    offset = 0
    cur_pat = None
    slide_flags_seen: set[int] = set()
    while offset < len(stream):
        eid = stream[offset]
        offset += 1
        if eid < 64:
            offset += 1
        elif eid < 128:
            val = struct.unpack_from("<H", stream, offset)[0]
            offset += 2
            if eid == _PAT_NEW:
                cur_pat = val
        elif eid < 192:
            offset += 4
        else:
            length = 0
            shift = 0
            while True:
                b = stream[offset]
                offset += 1
                length |= (b & 0x7F) << shift
                if b & 0x80 == 0:
                    break
                shift += 7
            raw = bytes(stream[offset : offset + length])
            offset += length
            if eid == _PAT_NOTES:
                for i in range(len(raw) // 24):
                    _pos, flags, _rack, _len = struct.unpack_from("<IHHI", raw, i * 24)
                    if flags & _NOTE_FLAG_SLIDE:
                        slide_flags_seen.add(cur_pat)

    from flp_template import pattern_iid_for

    sub_808_iid = pattern_iid_for("sub_808")
    assert sub_808_iid in slide_flags_seen, "no note in the real exported sub_808 pattern carries the slide bit"

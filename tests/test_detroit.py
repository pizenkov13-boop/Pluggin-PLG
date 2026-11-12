"""Jay Cactus + Lollipopbeatz ("Detroit type beat") — two sources, combined
into a real "detroit" GenreProfile (same two-source pattern as West Coast/
Pluggnb; confirmed earlier there was no code-level detroit->drill fallback
— an unqualified "detroit type beat" prompt used to resolve to the generic
"trap" default).

Techniques ported, now enabled on "detroit":
- piano top-line: only the melody's upper half survives (lower notes muted),
  pitched up an octave, on counter_melody's own preset.
- alternating bass: chorus/hook/drop move sub_808's notes to the fx lane (a
  genuinely different "spins" bass), never simultaneously with sub_808.
- an open hat lands just before each snare hit, quieter — the existing
  open-hat choke naturally "cuts" its tail.
- the whole melody_lead layer transposes up an octave for the intro only.
- (Lollipopbeatz, 2nd source) the sub_808 register band shifts up a fifth
  (F4-F5 instead of C4-C5) — "808 hits harder tuned higher" — a register
  shift, not a fixed pitch class, so it stays correct in any key.
"""

from __future__ import annotations

from beat_humanize import (
    add_open_hat_before_snare,
    apply_alternating_bass_sections,
    apply_intro_octave_up,
    materialize_muted_top_layer,
)
from genre_profiles import GENRE_KEYWORDS, HIPHOP_LANES, PRIORITY, PROFILES, detect_genre
from style_corpus import BPM_RANGES, DRUM_CELLS, MIX_RECIPES, PROGRESSIONS, RECIPES, _FX_FLAVOR
from sub_808_gen import SUB_HI, SUB_HI_HIGH, SUB_LO, SUB_LO_HIGH, clamp_sub_register_notes


_DETROIT_FLAGS = (
    "melody_top_layer_muted_lows",
    "alternating_bass_sections",
    "open_hat_before_snare_cut",
    "intro_melody_octave_up",
    "sub_808_register_high",
)


def test_detroit_profile_exists_with_all_five_flags():
    profile = PROFILES["detroit"]
    assert profile.melody_scale == "natural_minor"
    for flag in _DETROIT_FLAGS:
        assert getattr(profile, flag) is True, flag


def test_only_detroit_has_these_five_flags():
    for name, profile in PROFILES.items():
        if name == "detroit":
            continue
        for flag in _DETROIT_FLAGS:
            assert getattr(profile, flag) is False, (name, flag)


def test_detroit_bpm_range():
    lo, hi = BPM_RANGES["detroit"]
    assert lo == 100 and hi == 105


def test_detroit_has_full_corpus():
    for table in (PROGRESSIONS, BPM_RANGES, DRUM_CELLS, RECIPES, MIX_RECIPES, _FX_FLAVOR):
        assert "detroit" in table


def test_detroit_in_lanes_and_priority():
    assert "detroit" in HIPHOP_LANES
    assert "detroit" in PRIORITY
    assert "detroit" in GENRE_KEYWORDS


def test_detroit_prompt_now_routes_to_detroit_not_trap():
    assert detect_genre("detroit type beat") == "detroit"


def test_sub_808_register_high_shifts_the_band_up_a_fifth():
    assert SUB_LO_HIGH == SUB_LO + 5
    assert SUB_HI_HIGH == SUB_HI + 5


def test_clamp_sub_register_notes_default_band_unchanged():
    notes = [{"time_step": 0.0, "note": "F5", "length": 1.0, "velocity": 100}]
    out = clamp_sub_register_notes(notes)
    assert out[0]["note"] == "F4"  # folds into the default band (SUB_LO..SUB_HI)


def test_clamp_sub_register_notes_high_band():
    notes = [{"time_step": 0.0, "note": "C5", "length": 1.0, "velocity": 100}]
    out = clamp_sub_register_notes(notes, lo=SUB_LO_HIGH, hi=SUB_HI_HIGH)
    assert out[0]["note"] == "C5"  # already inside the higher band (F4..F5)


def test_muted_top_layer_keeps_only_upper_half_transposed():
    data = {
        "tracks": {
            "melody_lead": [
                {"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 90},  # low, muted
                {"time_step": 1.0, "note": "C5", "length": 1.0, "velocity": 90},  # high, kept
            ],
        },
    }
    placed = materialize_muted_top_layer(data)
    assert placed == 1
    bell = data["tracks"]["counter_melody"]
    assert bell[0]["note"] == "C6"  # C5 (median, kept) +12
    assert bell[0]["velocity"] < 90


def test_muted_top_layer_noop_without_melody():
    data = {"tracks": {}}
    assert materialize_muted_top_layer(data) == 0
    assert "counter_melody" not in data["tracks"]


def test_alternating_bass_moves_notes_in_chorus_only():
    data = {
        "tracks": {
            "sub_808": [
                {"time_step": 0.0, "note": "C1", "length": 2.0, "velocity": 100},  # verse
                {"time_step": 8.0, "note": "C1", "length": 2.0, "velocity": 100},  # chorus
            ],
        },
        "plg_arrangement": [
            {"name": "verse", "start_bar": 0, "bars": 2},
            {"name": "chorus", "start_bar": 2, "bars": 2},
        ],
    }
    moved = apply_alternating_bass_sections(data)
    assert moved == 1
    assert len(data["tracks"]["sub_808"]) == 1
    assert data["tracks"]["sub_808"][0]["time_step"] == 0.0
    assert data["tracks"]["fx"][0]["time_step"] == 8.0
    assert data["plg_fx_kind"] == "alt_bass_spins"


def test_alternating_bass_skips_when_fx_already_claimed():
    data = {
        "tracks": {
            "sub_808": [{"time_step": 8.0, "note": "C1", "length": 2.0, "velocity": 100}],
            "fx": [{"time_step": 0.0, "note": "C5", "length": 1.0, "velocity": 80}],
        },
        "plg_arrangement": [{"name": "chorus", "start_bar": 2, "bars": 2}],
    }
    assert apply_alternating_bass_sections(data) == 0


def test_alternating_bass_noop_without_chorus_sections():
    data = {
        "tracks": {"sub_808": [{"time_step": 0.0, "note": "C1", "length": 2.0, "velocity": 100}]},
        "plg_arrangement": [{"name": "verse", "start_bar": 0, "bars": 2}],
    }
    assert apply_alternating_bass_sections(data) == 0


def test_open_hat_before_snare_inserts_when_slot_empty():
    hats = [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 90}]
    snare = [{"time_step": 2.0, "note": "D1", "length": 0.25, "velocity": 100}]
    out = add_open_hat_before_snare(hats, snare)
    inserted = [n for n in out if round(n["time_step"], 3) == 1.75]
    assert len(inserted) == 1
    assert inserted[0]["hat_type"] == "open"
    assert inserted[0]["velocity"] < 100


def test_open_hat_before_snare_reuses_existing_note_in_window():
    hats = [{"time_step": 1.75, "note": "C5", "length": 0.25, "velocity": 90}]
    snare = [{"time_step": 2.0, "note": "D1", "length": 0.25, "velocity": 100}]
    out = add_open_hat_before_snare(hats, snare)
    assert len(out) == 1
    assert out[0]["hat_type"] == "open"
    assert out[0]["length"] >= 0.35
    assert out[0]["velocity"] < 90


def test_open_hat_before_snare_noop_without_snare():
    hats = [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 90}]
    assert add_open_hat_before_snare(hats, []) == hats


def test_intro_octave_up_transposes_only_intro_window():
    data = {
        "tracks": {
            "melody_lead": [
                {"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 90},  # intro
                {"time_step": 20.0, "note": "C4", "length": 1.0, "velocity": 90},  # verse
            ],
        },
        "plg_arrangement": [
            {"name": "intro", "start_bar": 0, "bars": 4},
            {"name": "verse", "start_bar": 4, "bars": 8},
        ],
    }
    raised = apply_intro_octave_up(data)
    assert raised == 1
    notes = data["tracks"]["melody_lead"]
    assert notes[0]["note"] == "C5"
    assert notes[1]["note"] == "C4"


def test_intro_octave_up_noop_without_intro_section():
    data = {
        "tracks": {"melody_lead": [{"time_step": 0.0, "note": "C4", "length": 1.0, "velocity": 90}]},
        "plg_arrangement": [{"name": "verse", "start_bar": 0, "bars": 8}],
    }
    assert apply_intro_octave_up(data) == 0

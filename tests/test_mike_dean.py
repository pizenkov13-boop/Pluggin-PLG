"""Mike Dean (Travis Scott) production techniques: reversed-chords riser into
the drop (P.1), arp mirrors the chord preset (P.2), live-sounding bass on
chorus/hook/drop vs. synth 808 elsewhere (P.3, backlog item "Live vs synth
bass differentiation"), simple intro/outro bass vs. spicier chorus/drop by
contrast (P.4).

P.3 rides the PLG Texture lane rather than a new channel — same idle-channel
trick Detroit's apply_alternating_bass_sections proved, but ADDITIVE, not a
swap: sub_808 keeps its full synth part everywhere (protects the 808
listenability floor and the audit-protected octave-layer texture claim),
and the live-sounding bass doubles it on chorus/hook/drop only, on texture,
so it never fights the trap riser (fx_riser_from_chords) for the fx lane."""

from __future__ import annotations

from arranger import _simplify_808_notes, arrange_song
from beat_humanize import apply_live_bass_texture_sections
from fx_layer import materialize_riser_notes
from genre_profiles import PROFILES


def test_simplify_808_keeps_one_hit_per_bar():
    notes = [
        {"time_step": 0.0, "note": "E2", "length": 1.0, "velocity": 120},
        {"time_step": 1.5, "note": "G2", "length": 0.5, "velocity": 90},   # same bar as above
        {"time_step": 4.0, "note": "E2", "length": 1.0, "velocity": 120},  # bar 1
        {"time_step": 5.75, "note": "A2", "length": 0.25, "velocity": 85},  # same bar
    ]
    out = _simplify_808_notes(notes)
    assert [n["time_step"] for n in out] == [0.0, 4.0]


def test_simplify_808_noop_on_empty():
    assert _simplify_808_notes([]) == []


def test_west_coast_intro_bass_is_simpler_than_a_dense_core_loop():
    # West Coast's intro already carries sub_808 (unlike the DEFAULT intro,
    # which is melody-only) — a good place to see the simplification bite.
    pattern = {
        "bpm": 96,
        "plg_variety": {"genre": "west_coast"},
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.4, "velocity": 112}],
            "clap": [{"time_step": 2.0, "note": "E1", "length": 0.25, "velocity": 100}],
            "hi_hats": [{"time_step": i * 0.5, "note": "C5", "length": 0.2, "velocity": 94} for i in range(8)],
            "sub_808": [
                {"time_step": 0.0, "note": "C2", "length": 1.0, "velocity": 120},
                {"time_step": 1.5, "note": "D2", "length": 0.5, "velocity": 90},
                {"time_step": 2.0, "note": "C2", "length": 1.0, "velocity": 120},
                {"time_step": 3.5, "note": "D2", "length": 0.5, "velocity": 90},
            ],
            "melody_lead": [{"time_step": 2.0, "note": "A4", "length": 4.0, "velocity": 100}],
        },
    }
    arrange_song(pattern)
    intro = next(s for s in pattern["plg_arrangement"] if s["name"] == "intro")
    lo, hi = intro["start_bar"] * 4.0, (intro["start_bar"] + intro["bars"]) * 4.0
    intro_808 = [n for n in pattern["tracks"]["sub_808"] if lo <= n["time_step"] < hi]
    # 4 bars in the intro, core loop had 2 hits/bar -> simplified to 1/bar = 4 total
    assert len(intro_808) == 4


def test_riser_placed_one_bar_before_the_landing():
    pattern = {
        "plg_arrangement": [
            {"name": "verse", "start_bar": 0, "bars": 8},
            {"name": "chorus", "start_bar": 8, "bars": 8, "final_chorus": False},
        ],
        "tracks": {},
    }
    written = materialize_riser_notes(pattern)
    fx = pattern["tracks"]["fx"]
    assert written == len(fx) == 1
    assert fx[0]["time_step"] == 7 * 4.0  # the bar immediately before bar 8
    assert fx[0]["length"] == 4.0
    assert pattern["plg_fx_kind"] == "riser_from_chords"


def test_riser_skips_a_landing_at_bar_zero():
    pattern = {
        "plg_arrangement": [{"name": "hook", "start_bar": 0, "bars": 8}],
        "tracks": {},
    }
    assert materialize_riser_notes(pattern) == 0
    assert "fx" not in pattern["tracks"]


def test_riser_kill_switch(monkeypatch):
    monkeypatch.setenv("PLG_FX_LANE", "off")
    pattern = {
        "plg_arrangement": [
            {"name": "verse", "start_bar": 0, "bars": 8},
            {"name": "chorus", "start_bar": 8, "bars": 8},
        ],
        "tracks": {},
    }
    assert materialize_riser_notes(pattern) == 0


def test_trap_opts_into_riser_and_counter_melody_mirror():
    profile = PROFILES["trap"]
    assert profile.fx_riser_from_chords is True
    assert profile.counter_melody_mirrors_chords is True


def test_live_bass_texture_doubles_notes_in_chorus_only():
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
    placed = apply_live_bass_texture_sections(data)
    assert placed == 1
    # additive — sub_808 keeps BOTH notes, verse and chorus alike
    assert len(data["tracks"]["sub_808"]) == 2
    assert [n["time_step"] for n in data["tracks"]["sub_808"]] == [0.0, 8.0]
    assert data["tracks"]["texture"][0]["time_step"] == 8.0
    assert data["plg_texture_kind"] == "live_bass"


def test_live_bass_texture_skips_when_texture_already_claimed():
    data = {
        "tracks": {
            "sub_808": [{"time_step": 8.0, "note": "C1", "length": 2.0, "velocity": 100}],
            "texture": [{"time_step": 0.0, "note": "C5", "length": 4.0, "velocity": 80}],
        },
        "plg_arrangement": [{"name": "chorus", "start_bar": 2, "bars": 2}],
    }
    assert apply_live_bass_texture_sections(data) == 0


def test_live_bass_texture_noop_without_chorus_sections():
    data = {
        "tracks": {"sub_808": [{"time_step": 0.0, "note": "C1", "length": 2.0, "velocity": 100}]},
        "plg_arrangement": [{"name": "verse", "start_bar": 0, "bars": 2}],
    }
    assert apply_live_bass_texture_sections(data) == 0


def test_trap_no_longer_double_claims_the_texture_lane():
    # 2026-07 audit: trap had BOTH melody_octave_down_layer and
    # live_bass_texture_sections targeting the Texture lane —
    # materialize_melody_octave_down_layer runs first in beat_humanize.py,
    # so live_bass_texture_sections silently never fired. Dropped from trap;
    # no genre currently enables it (kept off pending a genre where it
    # wouldn't collide with an octave-layer trick already claiming the lane).
    for genre in ("trap", "rage", "opium", "phonk", "detroit"):
        assert PROFILES[genre].live_bass_texture_sections is False, genre
    assert PROFILES["trap"].melody_octave_down_layer is True

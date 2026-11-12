"""Audit follow-up (2026-07): the cold-open hook (full band from bar 0 of the
hook — DEFAULT_SECTIONS' only behavior before this fix) is ONE legal outcome,
not the only one. Reference producers (Dirkey/Slayr/Nick Mira/"Still Countin")
stagger the rhythm section in before the hook lands. BUILDUP_SECTIONS adds
that second outcome; arrange_song() picks between the two off
plg_variety["hook_entry"], which generation_variety.py rolls once per
generation, weighted by GenreProfile.hook_buildup_chance. Neither outcome is
forced globally — see test_generation_variety.py for the per-genre weighting."""

from __future__ import annotations

import os

from arranger import BUILDUP_SECTIONS, DEFAULT_SECTIONS, arrange_song


def _core() -> dict:
    return {
        "bpm": 144,
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.4, "velocity": 112}],
            "snare": [{"time_step": 2.0, "note": "D1", "length": 0.25, "velocity": 106}],
            "clap": [{"time_step": 2.0, "note": "E1", "length": 0.25, "velocity": 100}],
            "hi_hats": [{"time_step": i * 0.5, "note": "C5", "length": 0.2, "velocity": 94} for i in range(8)],
            "sub_808": [{"time_step": 0.0, "note": "C2", "length": 2.0, "velocity": 127}],
            "melody_lead": [{"time_step": 2.0, "note": "A4", "length": 4.0, "velocity": 100}],
        },
        "plg_variety": {"genre": "trap", "hook_entry": "buildup"},
    }


def test_no_hook_entry_key_keeps_the_exact_old_cold_open_behavior():
    """Every existing caller (no plg_variety, or variety without hook_entry)
    must see identical output to before this change — the roll is opt-in via
    an explicit key, arrange_song never dices on its own."""
    p = _core()
    del p["plg_variety"]["hook_entry"]
    arrange_song(p)
    assert [s["name"] for s in p["plg_arrangement"]] == [
        "intro", "hook", "verse", "chorus", "verse", "chorus", "outro",
    ]
    intro = next(s for s in p["plg_arrangement"] if s["name"] == "intro")
    assert intro["bars"] == 4
    assert not any(n["time_step"] < 16.0 for n in p["tracks"].get("kick", []))


def test_buildup_inserts_a_prehook_section_same_total_bars():
    p = _core()
    arrange_song(p)
    assert [s["name"] for s in p["plg_arrangement"]] == [
        "intro", "prehook", "hook", "verse", "chorus", "verse", "chorus", "outro",
    ]
    assert p["plg_total_bars"] == 52  # identical song length to cold-open


def test_buildup_intro_is_still_drumless():
    p = _core()
    arrange_song(p)
    intro = next(s for s in p["plg_arrangement"] if s["name"] == "intro")
    assert intro["bars"] == 2
    lo, hi = intro["start_bar"] * 4.0, (intro["start_bar"] + intro["bars"]) * 4.0
    assert not any(lo <= n["time_step"] < hi for n in p["tracks"].get("kick", []))
    assert not any(lo <= n["time_step"] < hi for n in p["tracks"].get("snare", []))


def test_buildup_prehook_has_kick_and_808_but_no_snare_clap_hats():
    p = _core()
    arrange_song(p)
    prehook = next(s for s in p["plg_arrangement"] if s["name"] == "prehook")
    assert prehook["bars"] == 2
    lo, hi = prehook["start_bar"] * 4.0, (prehook["start_bar"] + prehook["bars"]) * 4.0
    assert any(lo <= n["time_step"] < hi for n in p["tracks"].get("kick", []))
    assert any(lo <= n["time_step"] < hi for n in p["tracks"].get("sub_808", []))
    assert not any(lo <= n["time_step"] < hi for n in p["tracks"].get("snare", []))
    assert not any(lo <= n["time_step"] < hi for n in p["tracks"].get("clap", []))
    assert not any(lo <= n["time_step"] < hi for n in p["tracks"].get("hi_hats", []))


def test_buildup_hook_lands_full_band():
    p = _core()
    arrange_song(p)
    hook = next(s for s in p["plg_arrangement"] if s["name"] == "hook")
    lo, hi = hook["start_bar"] * 4.0, (hook["start_bar"] + hook["bars"]) * 4.0
    for key in ("kick", "snare", "clap", "hi_hats", "sub_808", "melody_lead"):
        assert any(lo <= n["time_step"] < hi for n in p["tracks"].get(key, [])), key


def test_west_coast_ignores_hook_entry_keeps_its_own_intro():
    from arranger import WEST_COAST_SECTIONS

    p = _core()
    p["plg_variety"]["genre"] = "west_coast"
    arrange_song(p)
    assert [s["name"] for s in p["plg_arrangement"]] == [name for name, _, _ in WEST_COAST_SECTIONS]


def test_explicit_sections_argument_still_wins_over_hook_entry():
    p = _core()
    arrange_song(p, sections=DEFAULT_SECTIONS)
    assert [s["name"] for s in p["plg_arrangement"]] == [
        "intro", "hook", "verse", "chorus", "verse", "chorus", "outro",
    ]


def test_kill_switch_forces_cold_open_even_when_variety_asks_for_buildup():
    p = _core()
    os.environ["PLG_HOOK_BUILDUP"] = "0"
    try:
        arrange_song(p)
    finally:
        del os.environ["PLG_HOOK_BUILDUP"]
    assert "prehook" not in [s["name"] for s in p["plg_arrangement"]]


def test_buildup_sections_and_default_sections_total_the_same_bars():
    assert sum(bars for _, bars, _ in BUILDUP_SECTIONS) == sum(bars for _, bars, _ in DEFAULT_SECTIONS)

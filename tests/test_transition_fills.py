"""Transition glue — accelerating snare fills into every chorus landing."""

from __future__ import annotations

from typing import Any

import pytest

from arranger import arrange_song
from beat_humanize import humanize_pattern


def _core(genre: str, prompt: str) -> dict[str, Any]:
    return {
        "bpm": 142,
        "style": "",
        "user_prompt": prompt,
        "plg_variety": {"seed": 5, "genre": genre, "key": "F", "scale": "minor"},
        "tracks": {
            "kick": [{"time_step": t, "note": "C1", "length": 0.3, "velocity": 112} for t in (0.0, 2.75, 4.0, 6.5)],
            "snare": [{"time_step": t, "note": "D1", "length": 0.3, "velocity": 105} for t in (2.0, 6.0)],
            "hi_hats": [{"time_step": i * 0.5, "note": "F#1", "length": 0.2, "velocity": 90} for i in range(16)],
            "melody_lead": [
                {"time_step": float(i), "note": n, "length": 0.9, "velocity": 96}
                for i, n in enumerate(("F5", "G#5", "C6", "A#5", "F5", "C6", "G#5", "F5"))
            ],
        },
    }


def _chorus_starts(pattern: dict[str, Any]) -> list[int]:
    return [
        int(s["start_bar"])
        for s in pattern["plg_arrangement"]
        if s["name"] in ("chorus", "hook", "drop") and int(s.get("start_bar", 0)) > 0
    ]


def _bar_notes(notes: list[dict[str, Any]], bar: int) -> list[dict[str, Any]]:
    lo, hi = bar * 4.0, bar * 4.0 + 4.0
    return [n for n in notes if lo <= float(n["time_step"]) < hi]


def test_fill_builds_into_every_chorus_except_the_switch_cut():
    pattern = _core("trap", "dark trap fills test")
    arrange_song(pattern)
    out = humanize_pattern(pattern)
    cut_bar = out["plg_beat_switch"]["cut_bar"]

    for start in _chorus_starts(out):
        fill_bar = start - 1
        fills = [n for n in _bar_notes(out["tracks"]["snare"], fill_bar) if n.get("fill")]
        if fill_bar == cut_bar:
            # the pre-final-chorus bar is the beat-switch silence — no fill, no drums
            assert not _bar_notes(out["tracks"]["snare"], fill_bar)
            continue
        assert len(fills) >= 4, f"no fill before chorus at bar {start}"
        ordered = sorted(fills, key=lambda n: float(n["time_step"]))
        velocities = [int(n["velocity"]) for n in ordered]
        assert velocities == sorted(velocities), "fill must crescendo"
        assert velocities[-1] - velocities[0] >= 30


def test_kick_breathes_under_the_fill():
    pattern = _core("trap", "dark trap breathe test")
    arrange_song(pattern)
    out = humanize_pattern(pattern)
    cut_bar = out["plg_beat_switch"]["cut_bar"]
    fill_bars = [s - 1 for s in _chorus_starts(out) if s - 1 != cut_bar]
    assert fill_bars
    for bar in fill_bars:
        late_kicks = [
            n for n in _bar_notes(out["tracks"]["kick"], bar)
            if float(n["time_step"]) % 4.0 >= 2.5
        ]
        assert not late_kicks, f"kick should clear the fill in bar {bar}"


def test_four_floor_keeps_pulse_but_gets_short_build():
    pattern = _core("house", "house club fills")
    arrange_song(pattern)
    out = humanize_pattern(pattern)
    switch = out.get("plg_beat_switch") or {}
    fill_bars = [s - 1 for s in _chorus_starts(out) if s - 1 != switch.get("cut_bar")]
    assert fill_bars
    bar = fill_bars[0]
    fills = [n for n in _bar_notes(out["tracks"]["snare"], bar) if n.get("fill")]
    assert len(fills) >= 3
    # four-on-floor: the kick pulse is NOT cleared in the fill bar — same hits
    # as any other bar of the same section (the build rides on top).
    section_bars = [
        b for s in out["plg_arrangement"] if s["start_bar"] + s["bars"] - 1 == bar
        for b in range(s["start_bar"], s["start_bar"] + s["bars"] - 1)
    ]
    assert section_bars
    reference = len(_bar_notes(out["tracks"]["kick"], section_bars[0]))
    assert len(_bar_notes(out["tracks"]["kick"], bar)) == reference


def test_fill_velocities_survive_humanize_randomization():
    pattern = _core("pop", "pop transition test")  # pop profile randomizes drum velocity
    arrange_song(pattern)
    out = humanize_pattern(pattern)
    switch = out.get("plg_beat_switch") or {}
    fill_bars = [s - 1 for s in _chorus_starts(out) if s - 1 != switch.get("cut_bar")]
    for bar in fill_bars:
        fills = sorted(
            (n for n in _bar_notes(out["tracks"]["snare"], bar) if n.get("fill")),
            key=lambda n: float(n["time_step"]),
        )
        if not fills:
            continue
        velocities = [int(n["velocity"]) for n in fills]
        assert velocities == sorted(velocities)


# --- transition_fill is now genre-gated (2026-07 audit) — it used to fire for
# almost every genre via a hardcoded exclusion list, which is why it read as
# "always on" regardless of genre/prompt. ---


def _any_fill(genre: str, prompt: str = "") -> bool:
    pattern = _core(genre, prompt or f"{genre} test")
    arrange_song(pattern)
    out = humanize_pattern(pattern)
    return any(n.get("fill") for n in out["tracks"].get("snare", []))


def test_transition_fill_opted_in_genres_get_fills():
    for genre in ("trap", "drill", "house", "techno", "dnb", "dubstep", "garage"):
        assert _any_fill(genre), f"{genre} should have transition_fill=True"


def test_transition_fill_off_by_default_for_unrelated_genres():
    # A representative sample outside the opt-in list — none of these opt in.
    for genre in ("rage", "opium", "underground", "phonk", "hyperpop", "detroit",
                  "west_coast", "kanye", "toliver", "grind", "lofi", "boombap",
                  "reggaeton", "afrobeats", "rock"):
        assert not _any_fill(genre), f"{genre} should NOT have transition_fill"


def test_transition_fill_does_not_double_up_with_snare_riser_genres():
    # trance/hardstyle already build into the drop via snare_riser (a longer,
    # pitch-rising roll) — transition_fill must stay off there so the two
    # techniques never stack on the same landing.
    from genre_profiles import PROFILES

    for genre in ("trance", "hardstyle", "edm"):
        assert PROFILES[genre].snare_riser is True
        assert PROFILES[genre].transition_fill is False
        assert not _any_fill(genre)


def test_midi_transitions_use_only_drum_edits_and_land_full_on_drop():
    pattern = _core("trap", "midi arrangement transition test")
    pattern["plg_midi_melody"] = True
    pattern["plg_preserve_melody_sections"] = True
    arrange_song(pattern)
    out = humanize_pattern(pattern)

    assert not out["tracks"].get("fx")
    assert not out["tracks"].get("sub_808")
    # Derived, not hardcoded: the intro's length belongs to the form, and the
    # five-part form (2026-08-26) made it 8 bars.
    intro = next(s for s in out["plg_arrangement"] if s["name"] == "intro")
    intro_end = (float(intro["start_bar"]) + float(intro["bars"])) * 4.0
    # One narrow, deliberate exception (2026-08-26): a landing's pickup may
    # sound in the final BEAT before it, and since the five-part form opens
    # `intro -> hook` that beat is the intro's last. A bare intro slamming
    # into the hook with no lead-in is worse music, and the pickup is bounded
    # by construction — `_fill`'s offsets are all inside (-1.0, 0). Anything
    # else in the intro is the the project issue log (not included) 0v.11 leak and still fails here.
    for lane in ("kick", "snare", "clap", "hi_hats", "808_bass"):
        assert not [
            note
            for note in out["tracks"].get(lane, [])
            if float(note["time_step"]) < intro_end
            and not (note.get("midi_transition")
                     and float(note["time_step"]) >= intro_end - 4.0)
        ], lane
    # Density across the whole kit per BAR, not hats per section.
    #
    # Two reasons, both measured. Sections are no longer all one length since
    # the five-part form, so a raw count compares 16 bars against 8. And hats
    # do not mark sections at all -- the 2026-08-26 structure audit put
    # hook/verse hat ratios at 0.91-1.11x on nine of ten pairs, so a hats-only
    # test was reading noise: on this fixture the drop ships 119 hats against
    # the verse's 123 and always would have. What actually carries the lift is
    # the kit as a whole (16.62 against 14.56 per bar here), and inside it
    # `snare_layer` above all (21 notes against 4).
    def section_density(section: dict) -> float:
        lo = float(section["start_bar"]) * 4.0
        hi = lo + float(section["bars"]) * 4.0
        notes = sum(
            lo <= float(note["time_step"]) < hi
            for lane in ("kick", "808_bass", "clap", "snare", "snare_layer", "hi_hats")
            for note in out["tracks"].get(lane, [])
        )
        return notes / max(1.0, float(section["bars"]))

    # "Lands full on drop" means every hook/drop arrives BUSIER THAN WHAT CAME
    # BEFORE IT — checked for each one, in song order.
    #
    # This used to read section_hat_count("verse") < section_hat_count("drop")
    # off a {name: section} dict, so it compared exactly one pair — the LAST
    # verse against the LAST drop — and said nothing about the others. The
    # margin there is a single note, so any change that shifts the shared
    # humanize rng stream flips it: apply_pre_snare_shift draws one uniform
    # per snare/clap note, so a different intermediate note count re-rolls
    # every later jitter, including melody_lead's. Measured 2026-08-19 while
    # the backbeat moved to a per-bar walk (final counts in every lane
    # identical, micro-timing not).
    #
    # Deliberately NOT "every verse < every drop": that is false on this
    # fixture and always was — verse#1 ships 63 hats against 57 and 61 in the
    # two drops. The old assertion passed by picking the one pair where it
    # happened to hold, not because the property was true.
    ordered = [s for s in out["plg_arrangement"] if s["name"] not in ("intro", "outro")]
    landings = [(i, s) for i, s in enumerate(ordered) if s["name"] in ("hook", "drop")]
    assert len(landings) >= 2
    for index, section in landings:
        if index == 0:
            # Since the five-part form (2026-08-26) the song OPENS on its hook,
            # so the first landing has nothing before it to be fuller than.
            # That is the point of the form, not a gap in the check.
            continue
        before = ordered[index - 1]
        assert section_density(section) > section_density(before), (
            f"{section['name']}@{section['start_bar']} did not land fuller than "
            f"{before['name']}@{before['start_bar']}"
        )
    plans = out["plg_midi_transition_report"]
    hook_plan = next(item for item in plans if item["target"] == "hook")
    drop_plan = next(item for item in plans if item["target"] == "drop")
    assert hook_plan["fill_hits"] > 0
    assert drop_plan["removed"] > 0

    hook_beat = hook_plan["landing_bar"] * 4.0
    assert not [
        note
        for note in out["tracks"]["kick"]
        if hook_beat - 1.0 <= float(note["time_step"]) < hook_beat
    ]
    assert [
        note
        for note in out["tracks"]["snare"]
        if note.get("midi_transition") == "hook_pickup"
    ]

    drop_beat = drop_plan["landing_bar"] * 4.0
    for lane in ("kick", "snare", "clap", "hi_hats", "808_bass"):
        assert not [
            note
            for note in out["tracks"].get(lane, [])
            if drop_beat - 2.0 <= float(note["time_step"]) < drop_beat
        ], lane
    # "Lands full on drop": kick/808_bass note generation was removed
    # (2026-07 — always empty for bass808_enabled genres like trap now), so
    # hi_hats is the lane that still proves the drop actually lands full
    # rather than staying cleared.
    assert [
        note
        for note in out["tracks"]["hi_hats"]
        if drop_beat <= float(note["time_step"]) < drop_beat + 1.0
    ]

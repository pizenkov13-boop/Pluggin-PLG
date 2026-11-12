from arranger import arrange_song
import pytest


@pytest.fixture(autouse=True)
def _section_patterns(monkeypatch):
    """These tests describe the sectioned layout (one pattern per lane PER SECTION); the default is one pattern per channel
    (tests/test_whole_song_patterns.py)."""
    monkeypatch.setenv("PLG_PATTERN_SECTIONS", "on")


def _core() -> dict:
    return {
        "bpm": 144,
        "tracks": {
            "kick": [{"time_step": 0.0, "note": "C1", "length": 0.4, "velocity": 112}],
            "clap": [{"time_step": 2.0, "note": "E1", "length": 0.25, "velocity": 100}],
            "hi_hats": [{"time_step": i * 0.5, "note": "C5", "length": 0.2, "velocity": 94} for i in range(8)],
            "sub_808": [{"time_step": 0.0, "note": "C2", "length": 2.0, "velocity": 127}],
            "melody_lead": [{"time_step": 2.0, "note": "A4", "length": 4.0, "velocity": 100}],
        },
    }


def test_arrange_targets_two_minutes_instead_of_a_fixed_bar_count():
    p = _core()
    arrange_song(p)
    assert p["plg_total_bars"] == 72  # 72 bars at 144 BPM = 120 seconds
    # Short 4-bar intro, then the FULL beat states its identity (press play → it knocks),
    # with real verse↔chorus alternation so the beat changes across the song.
    assert [s["name"] for s in p["plg_arrangement"]][:6] == [
        "intro", "hook", "verse", "chorus", "verse", "chorus",
    ]
    assert p["plg_arrangement"][-1]["name"] == "outro"
    assert p["plg_outro_fade"]["end_beat"] == 72 * 4


def _hat_density(notes, start_bar, bars):
    lo = start_bar * 4.0
    hi = (start_bar + bars) * 4.0
    return sum(1 for n in notes if lo <= n["time_step"] < hi)


def test_chorus_beat_differs_from_verse():
    """The core complaint: one loop must NOT run the whole song. The chorus has
    a denser hat pattern and driving kicks the verse does not."""
    p = _core()
    arrange_song(p)
    sections = {s["name"]: s for s in p["plg_arrangement"]}
    verse = next(s for s in p["plg_arrangement"] if s["name"] == "verse")
    chorus = next(s for s in p["plg_arrangement"] if s["name"] == "chorus")

    hats = p["tracks"]["hi_hats"]
    verse_hats = _hat_density(hats, verse["start_bar"], verse["bars"])
    chorus_hats = _hat_density(hats, chorus["start_bar"], chorus["bars"])
    assert chorus_hats > verse_hats  # chorus is busier than the verse

    kicks = p["tracks"]["kick"]
    verse_kicks = _hat_density(kicks, verse["start_bar"], verse["bars"])
    chorus_kicks = _hat_density(kicks, chorus["start_bar"], chorus["bars"])
    assert chorus_kicks > verse_kicks  # chorus drives harder

    # Verse stays on steady hats (no rolls allowed there); chorus rolls.
    assert sections["verse"]["rolls"] is False
    assert sections["chorus"]["rolls"] is True


def test_section_dynamics_tagged_on_notes():
    p = _core()
    arrange_song(p)
    assert any(n.get("section_drive") for n in p["tracks"]["kick"])
    assert any(n.get("steady_hat") for n in p["tracks"]["hi_hats"])


def test_hook_has_melody_from_bar_zero():
    p = _core()
    arrange_song(p)
    intro_end = 4 * 4.0
    assert any(n["time_step"] < intro_end for n in p["tracks"].get("melody_lead", []))
    # Drums stay out of the 4-bar intro, then knock from the hook onward.
    assert not any(n["time_step"] < intro_end for n in p["tracks"].get("kick", []))
    assert any(intro_end <= n["time_step"] < intro_end + 32 for n in p["tracks"].get("kick", []))


def test_melody_anchored_to_start_of_core():
    p = _core()
    arrange_song(p)
    assert min(n["time_step"] for n in p["tracks"]["melody_lead"]) < 1.0


def test_song_runs_to_the_end():
    p = _core()
    arrange_song(p)
    mel = [n["time_step"] for n in p["tracks"]["melody_lead"]]
    assert max(mel) >= 40 * 4  # melody reaches the outro near bar 48


def test_arrange_exports_playlist_clips():
    p = _core()
    arrange_song(p)
    clips = p.get("plg_playlist_clips") or []
    assert clips
    melody = next(c for c in clips if c["track_key"] == "melody_lead")
    kick = next(c for c in clips if c["track_key"] == "kick")
    assert melody["start_bar"] == 0
    assert kick["start_bar"] == 4


def test_midi_arrangement_has_explicit_song_form_and_transition_plan():
    p = _core()
    p["plg_midi_melody"] = True
    p["plg_preserve_melody_sections"] = True
    p["tracks"]["808_bass"] = p["tracks"].pop("sub_808")

    arrange_song(p)

    # The reference five-part shape (2026-08-26): the song opens on its
    # statement, not on a verse, and the second peak is a `drop` — a name five
    # confirmed mechanisms key off, see arranger.MIDI_SECTIONS' comment.
    assert [section["name"] for section in p["plg_arrangement"]][:5] == [
        "intro",
        "hook",
        "verse",
        "drop",
        "verse",
    ]
    assert p["plg_total_bars"] == 72
    assert p["plg_arrangement"][-1]["name"] == "outro"
    assert "sub_808" not in p["tracks"]
    approved = {
        "melody_lead", "kick", "808_bass", "hi_hats", "clap", "snare", "snare_layer"
    }
    for section in p["plg_arrangement"]:
        assert set(section["active_tracks"]) <= approved
    assert "808_bass" in next(
        section["active_tracks"] for section in p["plg_arrangement"] if section["name"] == "hook"
    )
    assert {item["kind"] for item in p["plg_midi_transitions"]} == {
        "kick_breath_fill",
        "half_bar_stop",
    }

    sections = {section["name"]: section for section in p["plg_arrangement"]}
    kicks = p["tracks"]["kick"]
    verse_density = _hat_density(
        kicks, sections["verse"]["start_bar"], sections["verse"]["bars"]
    )
    drop_density = _hat_density(
        kicks, sections["drop"]["start_bar"], sections["drop"]["bars"]
    )
    assert drop_density > verse_density

    # Playlist is intentionally sectioned now: the first visible kick/808
    # block begins where the arrangement introduces the low end, not at an
    # arbitrary repeated-pattern offset.
    # Derived from the arrangement, not hardcoded: the bar the low end enters
    # on is a property of the form (it is where the melody-only intro ends),
    # and the intro's length is the form's to choose.
    low_end_enters = next(
        section["start_bar"] for section in p["plg_arrangement"]
        if "kick" in section["active_tracks"]
    )
    clips = p["plg_playlist_clips"]
    assert min(c["start_bar"] for c in clips if c["track_key"] == "kick") == low_end_enters
    bass_clips = [c for c in clips if c["track_key"] == "808_bass"]
    if bass_clips:  # this test core may deliberately produce no usable 808
        assert min(c["start_bar"] for c in bass_clips) == low_end_enters


def test_midi_drop_reuses_hook_melody_but_keeps_drum_development():
    """The refrain repeats; the drum arrangement must still evolve."""
    p = _core()
    p["plg_midi_melody"] = True
    p["plg_preserve_melody_sections"] = True
    p["tracks"]["808_bass"] = p["tracks"].pop("sub_808")
    arrange_song(p)

    clips = p["plg_playlist_clips"]
    hook = next(clip for clip in clips if clip["track_key"] == "melody_lead" and clip["section_name"].lower().startswith("hook"))
    drop = next(clip for clip in clips if clip["track_key"] == "melody_lead" and clip["section_name"].lower().startswith("drop"))
    assert drop["pattern_iid"] == hook["pattern_iid"]
    assert drop["pattern_name"] == hook["pattern_name"]

    drum_pairs = [
        (lane, next((clip for clip in clips if clip["track_key"] == lane and clip["section_name"].lower().startswith("hook")), None), next((clip for clip in clips if clip["track_key"] == lane and clip["section_name"].lower().startswith("drop")), None))
        for lane in ("kick", "808_bass", "hi_hats", "clap", "snare", "snare_layer")
    ]
    assert any(hook_clip and drop_clip and hook_clip["pattern_iid"] != drop_clip["pattern_iid"] for _lane, hook_clip, drop_clip in drum_pairs)


def test_midi_octave_layer_runs_through_middle_not_only_first_hook():
    p = _core()
    p["bpm"] = 160
    p["plg_midi_melody"] = True
    p["plg_preserve_melody_sections"] = True
    arrange_song(p)
    layer = [note for note in p["tracks"]["melody_lead"] if note.get("midi_sustained_octave_layer")]
    assert layer
    # Producer-visible bars 13..59 = zero-based [12, 59), never intro/outro.
    assert min(float(note["time_step"]) for note in layer) >= 12 * 4
    assert max(float(note["time_step"]) for note in layer) < 59 * 4
    assert any(20 * 4 <= float(note["time_step"]) < 59 * 4 for note in layer)


def test_section_tags_and_snare_in_hook():
    p = _core()
    p["tracks"]["snare"] = [{"time_step": 2.0, "note": "D1", "length": 0.3, "velocity": 108}]
    arrange_song(p)
    assert all("section" in n for n in p["tracks"]["melody_lead"])
    snare = p["tracks"].get("snare", [])
    assert snare
    assert any(n.get("section") == "verse" for n in snare)


def _tracks_active_in_first_16_beats(p: dict) -> set[str]:
    return {
        key
        for key in ("kick", "clap", "hi_hats", "sub_808", "melody_lead")
        if any(0.0 <= n["time_step"] < 16.0 for n in p["tracks"].get(key, []))
    }


def test_west_coast_intro_is_piano_and_bass_only():
    p = _core()
    p["plg_variety"] = {"genre": "west_coast"}
    arrange_song(p)
    assert _tracks_active_in_first_16_beats(p) == {"melody_lead", "sub_808"}


def test_other_genres_keep_the_default_melody_only_intro():
    p = _core()
    p["plg_variety"] = {"genre": "trap"}
    arrange_song(p)
    active = _tracks_active_in_first_16_beats(p)
    assert active == {"melody_lead"}  # DEFAULT intro = _HOOK_TRACKS, no bass/drums


def test_explicit_sections_override_still_wins_over_genre():
    from arranger import DEFAULT_SECTIONS

    p = _core()
    p["plg_variety"] = {"genre": "west_coast"}
    arrange_song(p, sections=DEFAULT_SECTIONS)
    assert _tracks_active_in_first_16_beats(p) == {"melody_lead"}


def test_west_coast_shaker_is_selective_across_verse_occurrences():
    p = _core()
    p["plg_variety"] = {"genre": "west_coast"}
    arrange_song(p)
    verses = [s for s in p["plg_arrangement"] if s["name"] == "verse"]
    assert len(verses) >= 2
    hats = p["tracks"]["hi_hats"]
    counts = [_hat_density(hats, v["start_bar"], v["bars"]) for v in verses[:2]]
    # occurrence 1 (shaker16, a denser 1/16 grid) must differ from occurrence 2
    # (falls back to steady8) — "only in some passes", not every pass.
    assert counts[0] != counts[1]


def test_funk_shaker_applies_to_every_verse_occurrence():
    p = _core()
    p["plg_variety"] = {"genre": "funk"}
    arrange_song(p)
    verses = [s for s in p["plg_arrangement"] if s["name"] == "verse"]
    hats = p["tracks"]["hi_hats"]
    counts = [_hat_density(hats, v["start_bar"], v["bars"]) for v in verses]
    # funk is NOT in the selective set — both occurrences get the same shaker16 treatment
    assert counts[0] == counts[1]


# --- melody section-development integration (melody_transform.py wiring) ---


def _richer_core() -> dict:
    p = _core()
    p["tracks"]["melody_lead"] = [
        {"time_step": 0.0, "note": "A4", "length": 1.5, "velocity": 100},
        {"time_step": 1.5, "note": "C5", "length": 0.5, "velocity": 100},
        {"time_step": 2.0, "note": "E5", "length": 1.0, "velocity": 100},
        {"time_step": 3.0, "note": "D5", "length": 1.0, "velocity": 100},
    ]
    return p


def _melody_in(p: dict, section_entry: dict) -> list[dict]:
    lo = section_entry["start_bar"] * 4.0
    hi = lo + section_entry["bars"] * 4.0
    return sorted(
        (n for n in p["tracks"]["melody_lead"] if lo <= n["time_step"] < hi),
        key=lambda n: n["time_step"],
    )


def _pitches(notes: list[dict]) -> list[str]:
    return [n["note"] for n in notes]


def test_hook_melody_matches_the_original_composer_core_exactly():
    p = _richer_core()
    original = _pitches(p["tracks"]["melody_lead"])
    arrange_song(p)
    hook = next(s for s in p["plg_arrangement"] if s["name"] == "hook")
    first_repeat = _melody_in(p, hook)[: len(original)]
    assert _pitches(first_repeat) == original


def test_two_verse_passes_are_no_longer_identical():
    """The bug this whole feature fixes: verse #1 and verse #2 used to be the
    exact same tiled core."""
    p = _richer_core()
    arrange_song(p)
    verses = [s for s in p["plg_arrangement"] if s["name"] == "verse"]
    assert len(verses) >= 2
    v1_pitches = _pitches(_melody_in(p, verses[0]))
    v2_pitches = _pitches(_melody_in(p, verses[1]))
    assert v1_pitches != v2_pitches


def test_non_final_chorus_pitches_differ_from_the_hook():
    p = _richer_core()
    original = _pitches(p["tracks"]["melody_lead"])
    arrange_song(p)
    chorus_sections = [s for s in p["plg_arrangement"] if s["name"] == "chorus"]
    first_chorus_pitches = _pitches(_melody_in(p, chorus_sections[0]))[: len(original)]
    assert first_chorus_pitches != original


def test_final_chorus_still_gets_the_existing_octave_lift():
    """Regression: the final chorus must stay the IDENTITY core (not sequence-
    transposed) so arranger._octave_lift's existing beat-switch peak treatment
    isn't compounded with a second pitch transform on the same moment. Its
    effect (an added octave-up doubling layer) must still fire."""
    p = _richer_core()
    arrange_song(p)
    hook = next(s for s in p["plg_arrangement"] if s["name"] == "hook")
    chorus_sections = [s for s in p["plg_arrangement"] if s["name"] == "chorus"]
    final = chorus_sections[-1]
    assert final.get("final_chorus") is True
    # Both are 8-bar sections tiling the same 4-beat core (8 repeats each);
    # the final chorus must have MORE melody notes than the hook because the
    # octave-lift doubling layer is still being added on top of the identity core.
    assert len(_melody_in(p, final)) > len(_melody_in(p, hook))


def test_melody_section_variation_kill_switch(monkeypatch):
    monkeypatch.setenv("PLG_MELODY_SECTION_VARIATION", "off")
    p = _richer_core()
    arrange_song(p)
    verses = [s for s in p["plg_arrangement"] if s["name"] == "verse"]
    v1_pitches = _pitches(_melody_in(p, verses[0]))
    v2_pitches = _pitches(_melody_in(p, verses[1]))
    assert v1_pitches == v2_pitches  # back to verbatim tiling, pre-feature behaviour


# --- long real core (uploaded MIDI / reference-reverse): _tile middle-loss fix ---


def _long_core(bars: int = 20) -> dict:
    """A 20-bar (80-beat) melody — one tagged note per bar — standing in for
    a real uploaded/transcribed take. src_bar lets tests see exactly which
    part of the core ended up in which section without caring about pitch."""
    p = _core()
    p["tracks"]["melody_lead"] = [
        {"time_step": bar * 4.0, "note": "C4", "length": 1.0, "velocity": 100, "src_bar": bar}
        for bar in range(bars)
    ]
    return p


def _src_bars(notes: list[dict]) -> set[int]:
    return {n["src_bar"] for n in notes if "src_bar" in n}


def test_long_flagged_core_reaches_the_middle_of_the_core():
    """The bug: hook/chorus/verse-1 always drew from the SAME [0, length_beats)
    head of the core, and only verse-2/outro's retrograde transform exposed
    the TAIL (by reversing the whole core then truncating) — so bars 8-11 of
    a 20-bar core were the genuinely unreachable middle: not the head (shown
    everywhere) and not the tail (shown via retrograde). With plg_midi_melody
    set and a core longer than the sections it's poured into, that middle
    chunk must actually surface somewhere in the song."""
    p = _long_core(20)
    p["plg_midi_melody"] = True
    arrange_song(p)
    touched = _src_bars(p["tracks"]["melody_lead"])
    assert {8, 9, 10, 11} <= touched, f"true middle of the core never surfaced: {sorted(touched)}"
    # Head and tail are still reachable too (nothing lost either way).
    assert 0 in touched
    assert 19 in touched


def test_long_flagged_core_distributes_sequentially_not_from_zero_every_time():
    """Two different sections must show two different windows of the core —
    not the same leading slice tiled into both (the old, lossy behavior)."""
    p = _long_core(20)
    p["plg_midi_melody"] = True
    arrange_song(p)
    hook = next(s for s in p["plg_arrangement"] if s["name"] == "hook")
    verse1 = next(s for s in p["plg_arrangement"] if s["name"] == "verse" and s["occurrence"] == 1)
    hook_bars = _src_bars(_melody_in(p, hook))
    verse1_bars = _src_bars(_melody_in(p, verse1))
    assert hook_bars and verse1_bars
    assert hook_bars != verse1_bars
    # Disjoint is not the property — it is geometry. Since the five-part form
    # (2026-08-26) a section is 16 bars, so two of them cannot carve disjoint
    # windows out of a 20-bar core at all. What the feature actually promises
    # is that the core's TAIL stops being structurally unreachable, which the
    # old tile-from-zero behaviour never delivered.
    core_bars = _src_bars(_long_core(20)["tracks"]["melody_lead"])
    tail = {bar for bar in core_bars if bar >= max(core_bars) - 3}
    assert tail <= (hook_bars | verse1_bars)


def test_long_core_without_plg_midi_melody_flag_keeps_old_tile_behavior():
    """Opt-in gate: the SAME long core, minus the flag, must keep today's
    exact tile/retrograde behavior — head (via most sections) and tail (via
    verse-2/outro's pre-existing retrograde transform) are reachable, but the
    TRUE middle (bars 8-11, reachable by neither path) must stay lost. This
    is the LLM's short-motif path and must not regress."""
    p = _long_core(20)  # no plg_midi_melody flag
    arrange_song(p)
    touched = _src_bars(p["tracks"]["melody_lead"])
    assert not ({8, 9, 10, 11} & touched), f"unflagged long core should not reach the true middle: {sorted(touched)}"


# --- plg_preserve_melody_sections: explicit upload melody stays untouched
# (2026-07 product decision) — always on for explicit MIDI/reference-audio
# upload, no opt-out, no effect on the corpus/library AUTO-match tiers -------


def test_preserve_melody_sections_disables_transforms_on_a_short_core():
    """The exact scenario the feature targets: a SHORT uploaded/reference
    melody (well under a section's length) would normally get sequence/
    retrograde/fragment per section (see test_two_verse_passes_are_no_longer_
    identical / test_non_final_chorus_pitches_differ_from_the_hook). With
    plg_preserve_melody_sections set, every section must play the exact same
    verbatim pitches — no transform, just tiling."""
    p = _richer_core()
    p["plg_preserve_melody_sections"] = True
    original = _pitches(p["tracks"]["melody_lead"])
    arrange_song(p)

    verses = [s for s in p["plg_arrangement"] if s["name"] == "verse"]
    chorus_sections = [s for s in p["plg_arrangement"] if s["name"] == "chorus"]
    hook = next(s for s in p["plg_arrangement"] if s["name"] == "hook")

    hook_pitches = _pitches(_melody_in(p, hook))[: len(original)]
    v1_pitches = _pitches(_melody_in(p, verses[0]))[: len(original)]
    v2_pitches = _pitches(_melody_in(p, verses[1]))[: len(original)]
    chorus_pitches = _pitches(_melody_in(p, chorus_sections[0]))[: len(original)]

    assert hook_pitches == original
    assert v1_pitches == original       # no fragment_transform on verse 1
    assert v2_pitches == original       # no retrograde on verse 2
    assert chorus_pitches == original   # no sequence_transform (diatonic third up)


def test_preserve_melody_sections_does_not_disable_the_long_core_cursor_fix():
    """The two mechanisms are independent: preserve_melody_sections only
    gates the SHORT-core transform branch — a long real core must still get
    the cursor-tiling fix (its middle must still surface), not fall back to
    the old lossy tile-from-zero behavior."""
    p = _long_core(20)
    p["plg_midi_melody"] = True
    p["plg_preserve_melody_sections"] = True
    arrange_song(p)
    touched = _src_bars(p["tracks"]["melody_lead"])
    assert {8, 9, 10, 11} <= touched, f"cursor fix should still reach the middle: {sorted(touched)}"


def test_preserve_melody_sections_absent_keeps_transforms_for_llm_composed_core():
    """Sanity: an ordinary LLM-composed core (no upload, flag absent) must
    keep getting real per-section development — this feature must not
    accidentally become the new default for everyone."""
    p = _richer_core()  # no plg_preserve_melody_sections
    original = _pitches(p["tracks"]["melody_lead"])
    arrange_song(p)
    verses = [s for s in p["plg_arrangement"] if s["name"] == "verse"]
    v1_pitches = _pitches(_melody_in(p, verses[0]))
    v2_pitches = _pitches(_melody_in(p, verses[1]))
    assert v1_pitches != v2_pitches  # still varies, exactly as before this feature

"""Three styles are three worlds — a Carti move must not land on a Travis beat."""

from __future__ import annotations

from chord_bed import compose_chord_bed
from fx_layer import materialize_cowbell_notes
from pattern_utils import parse_note_name
from style_family import is_carti, is_travis


def _chord_pattern(genre: str) -> dict:
    return {
        "bpm": 144,
        "plg_variety": {"seed": 9, "genre": genre},
        "tracks": {"melody_lead": [{"time_step": 0.0, "note": "E5", "length": 1.0, "velocity": 95}],
                   "kick": [{"time_step": b * 4.0, "note": "C1", "length": 0.3, "velocity": 110} for b in range(8)]},
        "plg_progression": {
            "chord_tones_pc": [[8, 11, 3], [1, 4, 8]],  # G# minor triads
            "roots_pc": [8, 1], "bar_degrees": [0, 4], "key": "G#", "scale": "natural_minor",
        },
        "plg_arrangement": [{"name": "hook", "start_bar": 0, "bars": 8, "final_chorus": True}],
    }


def test_families_are_disjoint():
    from style_family import is_ken

    assert is_carti("opium") and is_carti("plugg")
    assert not is_carti("rage"), "rage is the KEN world (clean progressions), not Carti"
    assert is_ken("rage") and is_ken("twizzy")
    assert not is_ken("opium") and not is_ken("trap")
    assert is_travis("trap")
    assert not is_travis("opium") and not is_travis("rage")


def test_carti_chords_have_semitone_cluster():
    pattern = _chord_pattern("opium")
    compose_chord_bed(pattern)
    chords = pattern["tracks"]["chords"]
    # a bar with two voices a semitone apart = the Carti cluster
    by_bar: dict[int, set[int]] = {}
    for n in chords:
        bar = int((float(n["time_step"]) + 0.1) // 4)
        by_bar.setdefault(bar, set()).add(parse_note_name(n["note"]) % 12)
    clustered = sum(
        1 for pcs in by_bar.values()
        if any((pc + 1) % 12 in pcs for pc in pcs)
    )
    assert clustered >= 6, "Carti chords must carry semitone-cluster tension"


def test_travis_chords_have_no_forced_cluster():
    pattern = _chord_pattern("trap")
    compose_chord_bed(pattern)
    chords = pattern["tracks"]["chords"]
    # Travis/melodic use 7th/9th extensions, not deliberate half-step clusters.
    # The bed still exists and follows the triad.
    assert chords
    triad = {8, 11, 3}  # G# minor
    bar0 = {parse_note_name(n["note"]) % 12 for n in chords if float(n["time_step"]) < 4}
    assert triad <= bar0


def test_cowbell_only_for_carti():
    carti = _chord_pattern("opium")
    assert materialize_cowbell_notes(carti) > 0
    assert carti["tracks"]["cowbell"]

    for genre in ("trap", "pop_rap", "rage"):  # rage = Ken world, no cowbell
        other = _chord_pattern(genre)
        assert materialize_cowbell_notes(other) == 0, f"{genre} must not get cowbell"
        assert "cowbell" not in other["tracks"]


def test_ken_chords_are_clean_not_clustered():
    pattern = _chord_pattern("rage")
    compose_chord_bed(pattern)
    chords = pattern["tracks"]["chords"]
    assert chords
    by_bar: dict[int, set[int]] = {}
    for n in chords:
        bar = int((float(n["time_step"]) + 0.1) // 4)
        by_bar.setdefault(bar, set()).add(parse_note_name(n["note"]) % 12)
    clustered = sum(
        1 for pcs in by_bar.values() if any((pc + 1) % 12 in pcs for pc in pcs)
    )
    # Ken/rage = clean progressions; incidental semitones from the scale's own
    # 7th can occur, but the DELIBERATE every-bar cluster must be absent.
    assert clustered < len(by_bar), "rage chords must not be forced into clusters"


def test_808_switch_up_for_ken_and_carti_only():
    from beat_humanize import apply_808_section_switch

    notes = [
        {"time_step": b * 4.0, "note": "E2", "length": 2.0, "velocity": 120}
        for b in range(8)
    ]
    arrangement = [
        {"name": "verse", "start_bar": 0, "bars": 4},
        {"name": "hook", "start_bar": 4, "bars": 4},
    ]
    ken = {"plg_variety": {"genre": "rage"}, "plg_arrangement": arrangement}
    out = apply_808_section_switch(notes, ken)
    verse = [n for n in out if float(n["time_step"]) < 16]
    hook = [n for n in out if float(n["time_step"]) >= 16]
    assert all(float(n["length"]) <= 0.5 for n in verse), "verse must play bow stabs"
    assert all(float(n["length"]) > 0.6 for n in hook), "hook keeps long sustains"

    travis = {"plg_variety": {"genre": "trap"}, "plg_arrangement": arrangement}
    out2 = apply_808_section_switch(notes, travis)
    assert all(float(n["length"]) > 0.6 for n in out2), "Travis 808 keeps sustains everywhere"


def test_cowbell_is_sparse():
    pattern = _chord_pattern("opium")
    materialize_cowbell_notes(pattern)
    cowbell = pattern["tracks"]["cowbell"]
    bars = 8
    # sparse Carti signature: nowhere near a hit on every 16th
    assert len(cowbell) < bars * 3


def test_toliver_is_its_own_world():
    from style_family import is_carti, is_ken, is_toliver, is_travis

    assert is_toliver("toliver")
    assert not (is_carti("toliver") or is_ken("toliver") or is_travis("toliver"))

    from artist_styles import resolve_artist_style
    resolved = resolve_artist_style("бит в стиле don toliver")
    assert resolved is not None and resolved.get("genre") == "toliver"

    from style_corpus import BPM_RANGES
    lo, hi = BPM_RANGES["toliver"]
    assert 128 <= lo and hi <= 138  # Tiramisu tempo zone, slower than Carti/Ken


def test_toliver_no_aggression_moves():
    from beat_humanize import add_808_ascent, apply_808_section_switch
    from fx_layer import materialize_cowbell_notes

    notes = [
        {"time_step": b * 4.0, "note": "E2", "length": 2.0, "velocity": 120}
        for b in range(8)
    ]
    arrangement = [
        {"name": "verse", "start_bar": 0, "bars": 4},
        {"name": "chorus", "start_bar": 4, "bars": 4},
    ]
    data = {"plg_variety": {"genre": "toliver"}, "plg_arrangement": arrangement,
            "tracks": {"kick": [{"time_step": 0.0, "note": "C1", "length": 0.3, "velocity": 100}]}}
    assert not any(n.get("ascent") for n in add_808_ascent(notes, data))
    assert all(float(n["length"]) > 0.6 for n in apply_808_section_switch(notes, data))
    assert materialize_cowbell_notes(data) == 0


def test_kanye_is_its_own_sample_world():
    from style_family import is_carti, is_ken, is_toliver, is_travis, is_kanye

    assert is_kanye("kanye")
    assert not (is_carti("kanye") or is_ken("kanye") or is_toliver("kanye") or is_travis("kanye"))

    from artist_styles import resolve_artist_style
    resolved = resolve_artist_style("kanye west type beat")
    assert resolved is not None and resolved.get("genre") == "kanye"

    from style_corpus import BPM_RANGES
    lo, hi = BPM_RANGES["kanye"]
    assert lo >= 72 and hi <= 92  # slow soul-flip tempo, not trap


def test_kanye_chords_are_lush_gospel_not_cluster():
    pattern = _chord_pattern("kanye")
    pattern["plg_progression"]["scale"] = "dorian"
    compose_chord_bed(pattern)
    chords = pattern["tracks"]["chords"]
    assert chords
    # gospel stack: MORE voices than a bare triad on every bar, no forced cluster
    by_bar: dict[int, list[int]] = {}
    for n in chords:
        bar = int((float(n["time_step"]) + 0.1) // 4)
        by_bar.setdefault(bar, []).append(parse_note_name(n["note"]))
    for bar, voices in by_bar.items():
        assert len(voices) >= 4, f"bar {bar}: kanye needs a lush stack, got {len(voices)}"


def test_kanye_no_aggression_or_float_moves():
    from beat_humanize import add_808_ascent, apply_808_section_switch
    from fx_layer import materialize_cowbell_notes

    notes = [{"time_step": b * 4.0, "note": "E2", "length": 2.0, "velocity": 118} for b in range(8)]
    arrangement = [{"name": "verse", "start_bar": 0, "bars": 4}, {"name": "hook", "start_bar": 4, "bars": 4}]
    data = {"plg_variety": {"genre": "kanye"}, "plg_arrangement": arrangement,
            "tracks": {"kick": [{"time_step": 0.0, "note": "C1", "length": 0.3, "velocity": 100}]}}
    assert not any(n.get("ascent") for n in add_808_ascent(notes, data))
    assert all(float(n["length"]) > 0.6 for n in apply_808_section_switch(notes, data))
    assert materialize_cowbell_notes(data) == 0


def test_kanye_fx_recipe_is_vinyl_not_metal():
    from sample_fx import MELODY_FX_RECIPES

    chain = MELODY_FX_RECIPES["kanye"]
    names = [name for name, _ in chain]
    assert "vinyl_wow" in names and "tape_echo" in names
    assert "bitcrush" not in names and "ring_mod" not in names  # no rage/carti metal

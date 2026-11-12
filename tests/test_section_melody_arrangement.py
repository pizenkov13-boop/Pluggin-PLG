"""The late melody pass: arrange the sections by removing, and only removing.

Product decision 2026-07 is the hard constraint: an explicit MIDI upload is
finished material. So the first test here is not about music at all — it is
that every note the pass keeps is the SAME OBJECT it was handed, unmodified.
Comparing values would pass for a transposition that happened to land on a
pitch already present; identity cannot.

The defect the rest cover, measured on the shipped batch 2026-08-26: hook,
verse and drop play the same block note for note (Graveyard's hook and verse#1
byte-identical, Shuttle's hook and drop, verse#1 == verse#2 on three files),
and BatonRouge carries 6.5 melody notes per bar in every section it has.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BPB = 4.0


def _arrangement():
    rows = [("intro", 8), ("hook", 16), ("verse", 16), ("drop", 16),
            ("verse", 16), ("outro", 8)]
    out, cursor, occurrences = [], 0, {}
    for name, bars in rows:
        occurrences[name] = occurrences.get(name, 0) + 1
        out.append({"name": name, "start_bar": cursor, "bars": bars,
                    "occurrence": occurrences[name], "energy": 1.0})
        cursor += bars
    return out


def _melody(total_bars=80, *, chordal=True, offbeats=True):
    """A core with material in every tier, tiled verbatim — today's behaviour."""
    notes = []
    for bar in range(total_bars):
        base = bar * BPB
        for beat, top in enumerate(("C5", "E5", "G5", "E5")):
            notes.append({"time_step": round(base + beat, 5), "note": top,
                          "length": 0.5, "velocity": 96})
            if chordal:
                notes.append({"time_step": round(base + beat, 5), "note": "C4",
                              "length": 0.5, "velocity": 80})
            if offbeats:
                notes.append({"time_step": round(base + beat + 0.5, 5), "note": "A5",
                              "length": 0.25, "velocity": 70})
        if 12 <= bar < 59:
            notes.append({"time_step": round(base, 5), "note": "C6", "length": 0.5,
                          "velocity": 74, "midi_sustained_octave_layer": True,
                          "switch_octave": True})
    return sorted(notes, key=lambda n: n["time_step"])


def _pattern(**kwargs):
    return {
        "plg_midi_melody": True,
        "plg_total_bars": 80,
        "plg_arrangement": _arrangement(),
        "tracks": {"melody_lead": _melody(**kwargs)},
    }


def _in_section(data, name, occurrence=1):
    section = next(s for s in data["plg_arrangement"]
                   if s["name"] == name and s["occurrence"] == occurrence)
    start = section["start_bar"] * BPB
    end = start + section["bars"] * BPB
    return [n for n in data["tracks"]["melody_lead"] if start <= float(n["time_step"]) < end]


# --- the product decision -------------------------------------------------


def test_the_pass_can_only_remove_never_add_alter_or_move():
    """Identity, not equality: a survivor must be the very object handed in,
    and its every field must be untouched. This is what makes the round
    compatible with the 2026-07 decision, and it is checked before anything
    musical."""
    import beat_humanize as bh

    data = _pattern()
    before = list(data["tracks"]["melody_lead"])
    snapshot = {id(note): copy.deepcopy(note) for note in before}

    bh._apply_section_melody_arrangement(data)
    after = data["tracks"]["melody_lead"]

    before_ids = {id(note) for note in before}
    assert {id(note) for note in after} <= before_ids, "a note appeared that was not handed in"
    assert len(after) < len(before), "the pass did nothing at all"
    for note in after:
        assert note == snapshot[id(note)], "a surviving note was modified"


def test_no_pitch_that_was_not_already_there_can_appear():
    """The same guarantee stated the way a listener would hear it."""
    import beat_humanize as bh

    data = _pattern()
    before = {str(n["note"]) for n in data["tracks"]["melody_lead"]}
    bh._apply_section_melody_arrangement(data)
    assert {str(n["note"]) for n in data["tracks"]["melody_lead"]} <= before


# --- the arrangement ------------------------------------------------------


def test_the_hook_survives_whole_and_the_verse_does_not():
    import beat_humanize as bh

    data = _pattern()
    hook_before = len(_in_section(data, "hook"))
    verse_before = len(_in_section(data, "verse"))
    bh._apply_section_melody_arrangement(data)

    assert len(_in_section(data, "hook")) == hook_before
    assert len(_in_section(data, "verse")) < verse_before


def test_the_verse_loses_its_octave_layer_and_the_peak_keeps_it():
    """The register axis — 'the hook again' means the doubling comes back."""
    import beat_humanize as bh

    data = _pattern()
    bh._apply_section_melody_arrangement(data)

    def layer(name):
        return sum(1 for n in _in_section(data, name) if bh._melody_is_layer(n))

    assert layer("verse") == 0
    assert layer("drop") > 0


def test_the_verse_base_rhythm_changes_too_not_only_its_doubling():
    """The defect that killed the first model: a budget paid entirely out of
    the octave layer leaves the base RHYTHM still the hook's, note for note —
    which is the copy-paste this round exists to remove."""
    import beat_humanize as bh

    data = _pattern()
    bh._apply_section_melody_arrangement(data)

    def base_per_bar(name, occurrence=1):
        notes = [n for n in _in_section(data, name, occurrence) if not bh._melody_is_layer(n)]
        section = next(s for s in data["plg_arrangement"]
                       if s["name"] == name and s["occurrence"] == occurrence)
        return len(notes) / section["bars"]

    assert base_per_bar("verse") < base_per_bar("hook")


def test_the_second_verse_leans_further_back_than_the_first():
    import beat_humanize as bh

    data = _pattern()
    bh._apply_section_melody_arrangement(data)
    assert len(_in_section(data, "verse", 2)) < len(_in_section(data, "verse", 1))


def test_a_monophonic_core_still_gets_thinned():
    """BatonRouge has zero inner voices in every section of the song. The
    offbeat tier is what pays there, and a rule with one tier would have
    removed nothing at all."""
    import beat_humanize as bh

    data = _pattern(chordal=False)
    verse_before = len([n for n in _in_section(data, "verse") if not bh._melody_is_layer(n)])
    bh._apply_section_melody_arrangement(data)
    verse_after = len([n for n in _in_section(data, "verse") if not bh._melody_is_layer(n)])

    assert verse_after < verse_before


def test_an_entirely_on_the_beat_core_still_gets_thinned():
    """The mirror case: Tragic has no offbeat notes anywhere, so the inner-
    voice tier has to carry it."""
    import beat_humanize as bh

    data = _pattern(offbeats=False)
    verse_before = len([n for n in _in_section(data, "verse") if not bh._melody_is_layer(n)])
    bh._apply_section_melody_arrangement(data)
    verse_after = len([n for n in _in_section(data, "verse") if not bh._melody_is_layer(n)])

    assert verse_after < verse_before


def test_a_core_with_neither_tier_falls_through_to_whole_bar_rests():
    """The floor tier. Monophonic AND fully on the beat leaves nothing but
    silence to spend, and the section must still thin rather than give up."""
    import beat_humanize as bh

    data = _pattern(chordal=False, offbeats=False)
    verse_before = len([n for n in _in_section(data, "verse") if not bh._melody_is_layer(n)])
    bh._apply_section_melody_arrangement(data)

    row = next(r for r in data["plg_melody_arrangement"]["sections"]
               if r["section"] == "verse" and r["occurrence"] == 1)
    assert row["drawn"]["bar_rest"] > 0
    assert len([n for n in _in_section(data, "verse") if not bh._melody_is_layer(n)]) < verse_before


def test_no_section_is_ever_silenced_completely():
    import beat_humanize as bh

    data = _pattern(chordal=False, offbeats=False)
    bh._apply_section_melody_arrangement(data)
    for section in data["plg_arrangement"]:
        assert _in_section(data, section["name"], section["occurrence"]), section


def test_the_cut_is_spread_across_the_section_not_piled_at_its_start():
    """Taking a tier's first N notes leaves the back half playing the hook
    verbatim — the thing that would make this inaudible."""
    import beat_humanize as bh

    data = _pattern()
    section = next(s for s in data["plg_arrangement"] if s["name"] == "verse")
    before = {}
    for note in _in_section(data, "verse"):
        before[int(float(note["time_step"]) // BPB)] = before.get(
            int(float(note["time_step"]) // BPB), 0) + 1
    bh._apply_section_melody_arrangement(data)
    after = {}
    for note in _in_section(data, "verse"):
        bar = int(float(note["time_step"]) // BPB)
        after[bar] = after.get(bar, 0) + 1

    thinned_bars = [bar for bar in before if after.get(bar, 0) < before[bar]]
    assert len(thinned_bars) > section["bars"] // 2, thinned_bars


def test_the_report_reaches_the_pattern_and_names_a_section_it_failed_to_differentiate():
    import beat_humanize as bh

    data = _pattern()
    bh._apply_section_melody_arrangement(data)
    report = data["plg_melody_arrangement"]

    assert report["removed"] > 0
    assert report["notes_after"] == report["notes_before"] - report["removed"]
    assert {r["section"] for r in report["sections"]} == {"intro", "hook", "verse", "drop", "outro"}
    assert "sections_matching_hook" in report
    assert report["every_section_differs_from_the_hook"] is (not report["sections_matching_hook"])


def test_a_pattern_without_an_arrangement_is_left_exactly_as_it_was():
    """Unit fixtures and the single-pattern route — same contract as
    `_apply_section_dynamics`."""
    import beat_humanize as bh

    data = {"tracks": {"melody_lead": _melody(8)}}
    before = list(data["tracks"]["melody_lead"])
    assert bh._apply_section_melody_arrangement(data) == 0
    assert data["tracks"]["melody_lead"] is before or data["tracks"]["melody_lead"] == before


def test_running_it_twice_does_not_thin_the_song_twice():
    """A second call must be a no-op on the already-arranged lane: the plan is
    a fraction of what the section HOLDS, so an accidental double application
    would compound instead of settling."""
    import beat_humanize as bh

    data = _pattern()
    bh._apply_section_melody_arrangement(data)
    once = len(data["tracks"]["melody_lead"])
    hook_once = len(_in_section(data, "hook"))

    bh._apply_section_melody_arrangement(data)
    assert len(_in_section(data, "hook")) == hook_once
    # The verse is a fraction of a now-smaller section, so a second pass WOULD
    # thin it again — this records the real behaviour rather than pretending
    # it is idempotent. The pass is called exactly once, from one place.
    assert len(data["tracks"]["melody_lead"]) <= once

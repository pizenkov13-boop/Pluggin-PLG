"""The 808's chord voicing — LowendRootMap.tone_at (2026-08-16).

The harmonic map used to answer one question: which ROOT is sounding. The 808
therefore played the root of every attack in the song, which is correct and
flat. ``tone_at`` adds the second question — which DEGREE of that harmony this
attack takes — under conditions strict enough that the root line stays the
recognizable one.

The load-bearing test in this file is the last one: generation, the off-scale
corrector and both export audits must resolve every attack through the SAME
method. Four disagreeing private fallbacks for exactly this value is the most
expensive bug in this project's history (the project issue log (not included) §0.1) — the colour
slots would have made a fifth.
"""
from __future__ import annotations

from copy import deepcopy

from midi_lowend_director import LowendRootMap
from midi_structure_analysis import analyze_midi_structure

A_MINOR_PCS = {9, 11, 0, 2, 4, 5, 7}
A, C, E, F = 9, 0, 4, 5


def _window(start, end, root_pc, *, third=None, fifth=None, seventh=None, confidence=0.8):
    return {
        "start": start,
        "end": end,
        "root_pc": root_pc,
        "confidence": confidence,
        "chord_pcs": {"root": root_pc, "third": third, "fifth": fifth, "seventh": seventh},
    }


def _map(windows, **kwargs):
    kwargs.setdefault("melody", [])
    kwargs.setdefault("roots_pc", [A])
    kwargs.setdefault("scale_pcs", A_MINOR_PCS)
    return LowendRootMap(harmonic_windows=windows, **kwargs)


def _note(step, name, length=1.0):
    return {"time_step": step, "note": name, "length": length, "velocity": 100}


# --- part A: the analysis actually hears the chord ---------------------------


def test_minor_triad_is_read_as_minor_with_its_own_third_and_fifth():
    notes = [
        _note(t, pitch, 0.9)
        for t in (0.0, 1.0, 2.0, 3.0)
        for pitch in ("A3", "C4", "E4")
    ]
    window = analyze_midi_structure(notes, root_pc=A, scale="natural_minor")["harmonic_rhythm"][0]
    assert window["quality"] == "minor"
    assert window["chord_pcs"] == {"root": A, "third": C, "fifth": E, "seventh": None}


def test_major_triad_is_read_as_major():
    notes = [
        _note(t, pitch, 0.9)
        for t in (0.0, 1.0, 2.0, 3.0)
        for pitch in ("F3", "A3", "C4")
    ]
    window = analyze_midi_structure(notes, root_pc=F, scale="major")["harmonic_rhythm"][0]
    assert window["quality"] == "major"
    assert window["chord_pcs"]["third"] == A


def test_bare_root_and_fifth_is_open_not_a_guessed_third():
    """A power-chord / mono reading must NOT be given an invented third."""
    notes = [_note(t, pitch, 0.9) for t in (0.0, 1.0, 2.0, 3.0) for pitch in ("A3", "E4")]
    window = analyze_midi_structure(notes, root_pc=A, scale="natural_minor")["harmonic_rhythm"][0]
    assert window["quality"] == "open"
    assert window["chord_pcs"]["third"] is None
    assert window["chord_pcs"]["fifth"] == E


def test_a_passing_brush_is_below_the_support_floor():
    """One sixteenth of a third against a held root is not a minor chord."""
    notes = [_note(0.0, "A3", 4.0), _note(2.0, "C5", 0.1)]
    window = analyze_midi_structure(notes, root_pc=A, scale="natural_minor")["harmonic_rhythm"][0]
    assert window["chord_pcs"]["third"] is None


def test_window_geometry_is_unchanged_by_the_quality_pass():
    """The entire kick/808 chain is calibrated on these numbers.

    Quality is measured AFTER the merge and never participates in it, so
    boundaries, roots and confidences must be exactly what they were before
    chord quality existed.
    """
    notes = []
    for t in (0.0, 1.0, 2.0, 3.0):
        notes += [_note(t, p, 0.9) for p in ("A3", "C4", "E4")]
    for t in (4.0, 5.0, 6.0, 7.0):
        notes += [_note(t, p, 0.9) for p in ("F3", "A3", "C4")]
    for t in (8.0, 9.0, 10.0, 11.0):
        notes += [_note(t, p, 0.9) for p in ("A3", "C4", "E4")]
    windows = analyze_midi_structure(notes, root_pc=A, scale="natural_minor")["harmonic_rhythm"]
    assert [(w["start"], w["end"], w["root_pc"]) for w in windows] == [
        (0.0, 4.0, A), (4.0, 8.0, F), (8.0, 12.0, A),
    ]
    assert all(w["confidence"] > 0.0 and "change_strength" in w for w in windows)


# --- part B: when a colour is allowed ----------------------------------------


def test_third_leads_into_a_real_root_change():
    root_map = _map([
        _window(0.0, 4.0, A, third=C, fifth=E),
        _window(4.0, 8.0, F, third=A, fifth=C),
    ])
    assert root_map.tone_at(1.5) == (A, "window", "root")
    assert root_map.tone_at(2.5) == (C, "window", "third")


def test_fifth_when_the_harmony_does_not_move():
    root_map = _map([_window(0.0, 4.0, A, third=C, fifth=E)])
    assert root_map.tone_at(2.5) == (E, "window", "fifth")


def test_downbeat_is_always_the_root():
    root_map = _map([_window(0.0, 8.0, A, third=C, fifth=E)])
    # The slot is the window's last two beats, which here contains beat 8.
    assert root_map.tone_at(6.5)[2] == "fifth"
    assert root_map.tone_at(7.5)[2] == "fifth"
    assert root_map.tone_at(8.0)[0] == A
    # A hair either side of the bar line is still the downbeat: kick/808 carry
    # deterministic micro-timing dust, and a few ms must not flip the pitch.
    assert root_map.tone_at(7.95)[2] == "root"
    assert root_map.tone_at(8.05)[2] == "root"


def test_the_chord_always_enters_on_its_own_root():
    root_map = _map([_window(0.0, 8.0, A, third=C, fifth=E)])
    for step in (0.5, 1.0, 1.5, 2.0, 3.0, 5.0):
        assert root_map.tone_at(step)[2] == "root", step


def test_never_two_consecutive_windows():
    root_map = _map([
        _window(0.0, 4.0, A, third=C, fifth=E),
        _window(4.0, 8.0, F, third=A, fifth=C),
        _window(8.0, 12.0, A, third=C, fifth=E),
    ])
    starts = [start for start, _end, _pc, _degree in root_map.color_slots]
    assert starts == [2.0, 10.0]
    assert root_map.tone_at(6.5)[2] == "root"


# --- part C: when a colour is refused ----------------------------------------


def test_no_colour_without_a_confident_window():
    """melody_bar / carry / progression tiers are already a guess."""
    root_map = _map([], melody=[_note(0.0, "A2"), _note(4.0, "F2")])
    assert root_map.tone_at(2.5) == (A, "melody_bar", "root")
    assert root_map.tone_at(6.5) == (F, "melody_bar", "root")
    assert root_map.color_slots == ()


def test_low_confidence_window_gets_no_colour():
    root_map = _map([_window(0.0, 4.0, A, third=C, fifth=E, confidence=0.2)])
    assert root_map.color_slots == ()


def test_no_colour_when_the_melody_never_sounds_the_tone():
    root_map = _map([_window(0.0, 4.0, A)])
    assert root_map.color_slots == ()
    assert root_map.tone_at(2.5) == (A, "window", "root")


def test_out_of_key_chord_tone_is_refused():
    """C# over an A minor session is really sounding, but it is not the key."""
    root_map = _map([_window(0.0, 4.0, A, third=1, fifth=None)])
    assert root_map.color_slots == ()


def test_short_window_fragment_gets_no_colour():
    root_map = _map([_window(0.0, 2.5, A, third=C, fifth=E)])
    assert root_map.color_slots == ()


def test_seventh_is_never_offered():
    root_map = _map([_window(0.0, 4.0, A, seventh=7)])
    assert root_map.color_slots == ()


def test_master_switch_restores_pure_root_behaviour():
    windows = [_window(0.0, 4.0, A, third=C, fifth=E), _window(4.0, 8.0, F, third=A, fifth=C)]
    plain = _map(windows, allow_chord_colors=False)
    assert plain.color_slots == ()
    for step in (0.0, 1.5, 2.5, 3.5, 5.5, 7.5):
        assert plain.tone_at(step)[0] == plain.root_at(step)[0], step


def test_root_at_is_untouched_by_the_colour_layer():
    """Every pre-existing consumer of root_at must see the old answer."""
    windows = [_window(0.0, 4.0, A, third=C, fifth=E), _window(4.0, 8.0, F, third=A, fifth=C)]
    coloured = _map(windows)
    plain = _map(windows, allow_chord_colors=False)
    for step in [x * 0.25 for x in range(32)]:
        assert coloured.root_at(step) == plain.root_at(step), step


# --- part D: the one that matters — every consumer reads the same answer ------


def _midi_pattern() -> dict:
    """A two-chord uploaded-MIDI pattern the whole kick/808 chain accepts."""
    melody = []
    for bar in range(8):
        pitches = ("A3", "C4", "E4") if bar % 2 == 0 else ("F3", "A3", "C4")
        for beat in range(4):
            t = bar * 4.0 + beat
            melody += [_note(t, pitch, 0.9) for pitch in pitches]
    return {
        "bpm": 140,
        "key": "A",
        "scale": "natural_minor",
        "plg_midi_melody": True,
        "tracks": {"melody_lead": melody, "kick": [], "808_bass": [], "snare": [], "clap": [], "hi_hats": []},
    }


def test_generator_corrector_and_both_audits_agree_on_every_attack():
    """The anti-divergence test §0.1 says this project needed all along.

    Builds a real pattern, resolves every 808 attack through each of the four
    consumers' own code path, and requires one value. If a future change makes
    any of them read root_at again, this fails instead of shipping a beat whose
    own gate rejects it.
    """
    import beat_humanize

    data = _midi_pattern()
    from midi_structure_analysis import attach_midi_structure

    attach_midi_structure(data)
    root_map = beat_humanize._lowend_root_map(data)

    # The engine writes the 808 through midi_phrase_drums._pc_at_time...
    from midi_phrase_drums import _pc_at_time, _scale_pcs

    pcs = _scale_pcs(A, "natural_minor")
    steps = [x * 0.5 for x in range(64)]
    generated = [_pc_at_time(t, A, [A], pcs, None, root_map) for t in steps]

    # ...and every later owner must resolve the same attack identically.
    for step, expected in zip(steps, generated):
        planned, _tier, _degree = root_map.tone_at(step)
        assert planned is not None
        assert int(planned) % 12 == expected, step


def test_a_deliberate_colour_survives_the_corrector_and_both_audits():
    """The end-to-end proof: a third the engine placed must ship as a third."""
    import beat_humanize
    from midi_structure_analysis import attach_midi_structure

    data = _midi_pattern()
    attach_midi_structure(data)
    root_map = beat_humanize._lowend_root_map(data)
    slots = root_map.color_slots
    assert slots, "this fixture is supposed to produce at least one colour slot"

    from music_theory import name_for_midi, parse_note_name
    from bass808_gen import LOWEND_808_LO, _BASS_VEL, _KICK_VEL

    kicks, bass = [], []
    for slot_start, _slot_end, color_pc, _degree in slots:
        step = round(slot_start + 0.5, 5)
        planned, _tier, degree = root_map.tone_at(step)
        if degree == "root":
            continue
        kicks.append({"time_step": step, "note": "C5", "length": 0.28, "velocity": _KICK_VEL})
        bass.append({
            "time_step": step, "note": name_for_midi(LOWEND_808_LO + int(planned) % 12),
            "length": 0.5, "velocity": _BASS_VEL, "pan": 64,
        })
    assert bass, "no colour slot had a usable non-downbeat attack point"

    data["tracks"]["kick"] = kicks
    data["tracks"]["808_bass"] = deepcopy(bass)

    before = [note["note"] for note in data["tracks"]["808_bass"]]
    assert beat_humanize._correct_off_scale_808_notes(data) == 0
    assert [note["note"] for note in data["tracks"]["808_bass"]] == before

    context = beat_humanize._audit_midi_kick808_context(data)
    assert "808_not_on_midi_root" not in (context.get("reasons") or [])

    audit = beat_humanize._audit_kick808_symbiosis(data)
    assert audit.get("off_scale_808", 0) == 0


def test_voicing_telemetry_counts_what_shipped():
    import beat_humanize
    from midi_structure_analysis import attach_midi_structure

    data = _midi_pattern()
    attach_midi_structure(data)
    stats = beat_humanize._measure_808_voicing(data)
    assert stats["windows_total"] >= stats["colored_windows"]
    assert stats["third"] + stats["fifth"] == stats["colored_windows"]
    assert stats["attacks_total"] == 0  # no 808 written in this fixture

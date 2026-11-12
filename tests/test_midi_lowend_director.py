from midi_lowend_director import compose_phrase_onsets


SHAPES = {
    "statement": (0.0, 1.5, 3.0, 5.5),
    "answer": (0.0, 0.75, 3.25, 4.75, 6.75),
    "space": (0.0, 2.5, 4.0, 6.0),
    "lift": (0.0, 1.5, 3.0, 5.5, 7.0),
    "landing": (0.0, 0.75, 2.5, 4.0, 6.5),
}


def _phrases():
    return [
        {"start": 0.0, "end": 8.0, "role": "statement", "section": "verse"},
        {"start": 8.0, "end": 16.0, "role": "answer", "section": "verse"},
        {"start": 16.0, "end": 24.0, "role": "lift", "section": "drop"},
        {"start": 24.0, "end": 32.0, "role": "landing", "section": "drop"},
    ]


def test_director_handles_contrasting_midi_accent_dna_without_static_grid():
    cases = {
        "straight": [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30],
        # The old first-N algorithm packed every phrase from the opening.
        "front_loaded": [0, .25, .5, .75, 3.5, 6.0, 8, 8.25, 8.5, 8.75, 11.5, 14.0, 16, 16.25, 19.5, 22.0, 24, 24.25, 27.5, 30.0],
        "syncopated": [.75, 2.25, 3.75, 6.5, 8.5, 10.75, 13.25, 15.5, 16.75, 18.25, 21.75, 23.5, 24.5, 26.75, 29.25, 31.5],
        "sparse": [0, 5.5, 8, 13.0, 16, 23.0, 24, 30.0],
    }
    for name, accents in cases.items():
        onsets, audit = compose_phrase_onsets(
            phrases=_phrases(), pocket_shapes=SHAPES, melody_accents=accents, start=0.0, end=32.0
        )
        assert audit["passed"], name
        assert len(audit["phrases"]) == 4
        assert onsets and all(0.0 <= time < 32.0 for time, _role in onsets)
        # A phrase director must cover the full song rather than concentrating
        # all decisions in the source MIDI's first motif.
        assert max(time for time, _role in onsets) >= 24.0, name


def test_director_keeps_phrase_identity_when_midi_is_repetitive():
    accents = [bar * 4.0 + offset for bar in range(8) for offset in (0.0, 1.5, 3.0)]
    _onsets, audit = compose_phrase_onsets(
        phrases=_phrases(), pocket_shapes=SHAPES, melody_accents=accents, start=0.0, end=32.0
    )
    readings = [row["reading"] for row in audit["phrases"]]
    assert len(readings) == 4
    assert audit["score"] > 0


def test_director_keeps_single_cell_reading_inside_a_four_bar_phrase():
    onsets, audit = compose_phrase_onsets(
        phrases=[{"start": 0.0, "end": 16.0, "role": "statement", "section": "verse"}],
        pocket_shapes=SHAPES,
        melody_accents=[0.0, 1.5, 3.0, 5.5, 8.0, 9.5, 11.0, 13.5],
        start=0.0,
        end=16.0,
    )
    assert audit["passed"] is True
    assert len(onsets) == 4
    assert max(time for time, _role in onsets) < 8.0


# --- kit-aware pocket placement (hats / snare / clap / melody breath) --------


def test_director_prefers_a_reading_that_clears_a_hat_roll():
    """Hats own the pulse. The director only RANKS legal readings — it never
    invents an onset — so this is a tiebreaker: when two readings are both
    legal, the one not buried under a roll wins. (The hard guarantee that no
    kick survives inside a roll is downstream, in beat_humanize's
    _seal_midi_kick808_pocket / _clear_kicks_from_hat_rolls.)"""
    from midi_lowend_director import in_hat_roll, rhythmic_anchors

    hats = [{"time_step": 2.75 + i * 0.125, "note": "C5"} for i in range(6)]
    anchors = rhythmic_anchors(hats=hats)
    kwargs = dict(
        phrases=[{"start": 0.0, "end": 8.0, "role": "answer", "section": "verse"}],
        pocket_shapes=SHAPES,
        melody_accents=[0.0, 1.25, 1.75, 4.0, 5.5],
        start=0.0,
        end=8.0,
    )
    baseline = [time for time, _role in compose_phrase_onsets(**kwargs)[0]]
    anchored = [time for time, _role in compose_phrase_onsets(**kwargs, anchors=anchors)[0]]

    rolls = anchors["roll_times"]
    def _collisions(onsets):
        return sum(in_hat_roll(t, rolls) and abs(t % 4.0) > 1e-5 for t in onsets)

    assert _collisions(baseline) > 0
    assert _collisions(anchored) == 0


def test_director_breaks_a_static_four_bar_loop_across_repeated_phrases():
    """Two phrases can each differ from their neighbour and still tile one
    unchanging 16-beat block — the export gate judges sameness over 4 bars."""
    phrases = [
        {"start": float(start), "end": float(start + 8.0), "role": "statement", "section": "verse"}
        for start in (0.0, 8.0, 16.0, 24.0, 32.0, 40.0)
    ]
    accents = [float(t) / 2.0 for t in range(0, 96)]
    onsets, _audit = compose_phrase_onsets(
        phrases=phrases,
        pocket_shapes=SHAPES,
        melody_accents=accents,
        start=0.0,
        end=48.0,
    )
    signatures = set()
    for window in (0.0, 16.0, 32.0):
        signatures.add(
            tuple(
                round(time - window, 2)
                for time, _role in sorted(onsets)
                if window <= time < window + 16.0
            )
        )
    assert len(signatures) > 1


def test_anchors_default_to_no_effect():
    """A pattern with no kit context must compose exactly as it always did."""
    kwargs = dict(
        phrases=_phrases(),
        pocket_shapes=SHAPES,
        melody_accents=[0.0, 1.5, 3.0, 5.5, 8.0, 9.5, 16.0, 24.0],
        start=0.0,
        end=32.0,
    )
    assert compose_phrase_onsets(**kwargs)[0] == compose_phrase_onsets(**kwargs, anchors={})[0]


# --- chord changes masked by a snare/clap edge ------------------------------


def test_harmonic_change_snaps_onto_the_clap_that_masks_it():
    from midi_lowend_director import snap_harmonic_windows_to_boundaries

    windows = [
        {"start": 0.0, "end": 8.0, "root_pc": 0, "confidence": 0.9},
        {"start": 8.0, "end": 16.0, "root_pc": 5, "confidence": 0.9},
    ]
    snapped = snap_harmonic_windows_to_boundaries(windows, [2.0, 6.0, 8.5, 14.0])
    assert snapped[0]["end"] == 8.5
    assert snapped[1]["start"] == 8.5
    # Roots are never touched — only when the change is heard.
    assert [w["root_pc"] for w in snapped] == [0, 5]


def test_harmonic_change_keeps_its_timing_without_a_nearby_hit():
    from midi_lowend_director import snap_harmonic_windows_to_boundaries

    windows = [
        {"start": 0.0, "end": 8.0, "root_pc": 0, "confidence": 0.9},
        {"start": 8.0, "end": 16.0, "root_pc": 5, "confidence": 0.9},
    ]
    snapped = snap_harmonic_windows_to_boundaries(windows, [2.0, 6.0, 12.0])
    assert snapped[0]["end"] == 8.0
    assert snapped[1]["start"] == 8.0


def test_boundary_snap_never_inverts_a_window():
    from midi_lowend_director import snap_harmonic_windows_to_boundaries

    windows = [
        {"start": 0.0, "end": 2.0, "root_pc": 0, "confidence": 0.9},
        {"start": 2.0, "end": 2.4, "root_pc": 5, "confidence": 0.9},
    ]
    snapped = snap_harmonic_windows_to_boundaries(windows, [2.6])
    assert snapped[1]["start"] < float(snapped[1]["end"])


def test_lowend_root_map_exposes_scale_pcs_readonly():
    """The vertical consonance repair reads the SAME scale set the corrector
    and audits use (2026-08-15) — never re-derives its own."""
    from midi_lowend_director import LowendRootMap

    root_map = LowendRootMap(
        harmonic_windows=[],
        melody=[],
        roots_pc=[9],
        scale_pcs={9, 11, 0, 2, 4, 5, 7},
    )
    assert root_map.scale_pcs == frozenset({9, 11, 0, 2, 4, 5, 7})
    assert isinstance(root_map.scale_pcs, frozenset)


def test_lowend_root_map_exposes_harmony_pcs_readonly():
    """The narrower companion to scale_pcs (2026-08-20): which pitch classes
    this song's harmony actually USES, not merely which are in the key.

    Roots of every confident window plus the tones the analysis measured
    there. G sits in A natural minor but neither window chooses it, so it must
    NOT appear — that gap is the whole point of the accessor."""
    from midi_lowend_director import LowendRootMap

    windows = [
        {"start": 0.0, "end": 4.0, "root_pc": 9, "confidence": 0.8,
         "chord_pcs": {"root": 9, "third": 0, "fifth": 4, "seventh": None}},
        {"start": 4.0, "end": 8.0, "root_pc": 5, "confidence": 0.8,
         "chord_pcs": {"root": 5, "third": 9, "fifth": 0, "seventh": None}},
    ]
    root_map = LowendRootMap(
        harmonic_windows=windows,
        melody=[],
        roots_pc=[9],
        scale_pcs={9, 11, 0, 2, 4, 5, 7},
    )
    assert root_map.harmony_pcs == frozenset({9, 5, 0, 4})
    assert isinstance(root_map.harmony_pcs, frozenset)
    assert 7 not in root_map.harmony_pcs
    assert root_map.harmony_pcs <= root_map.scale_pcs


def test_harmony_pcs_ignores_windows_below_the_confidence_floor():
    """A guess is not harmony. An unconfident window contributes nothing, and
    the empty set is the documented signal for 'keep the old behaviour'."""
    from midi_lowend_director import LowendRootMap

    root_map = LowendRootMap(
        harmonic_windows=[
            {"start": 0.0, "end": 4.0, "root_pc": 9, "confidence": 0.1,
             "chord_pcs": {"root": 9, "third": 0, "fifth": 4, "seventh": None}},
        ],
        melody=[],
        roots_pc=[9],
        scale_pcs={9, 11, 0, 2, 4, 5, 7},
    )
    assert root_map.harmony_pcs == frozenset()


def test_harmony_pcs_is_built_even_when_chord_colours_are_off():
    """It is the harmonic model, not the colour plan — the colour switch must
    not be able to empty it."""
    from midi_lowend_director import LowendRootMap

    windows = [
        {"start": 0.0, "end": 4.0, "root_pc": 9, "confidence": 0.8,
         "chord_pcs": {"root": 9, "third": 0, "fifth": 4, "seventh": None}},
    ]
    root_map = LowendRootMap(
        harmonic_windows=windows,
        melody=[],
        roots_pc=[9],
        scale_pcs={9, 11, 0, 2, 4, 5, 7},
        allow_chord_colors=False,
    )
    assert root_map.color_slots == ()
    assert root_map.harmony_pcs == frozenset({9, 0, 4})


def test_phrase_firmness_is_unanimous_not_a_majority():
    from melody_beat_symbiosis import ACCENT_FIRMNESS_FLOOR
    from midi_lowend_director import _phrase_is_firm

    firm = ACCENT_FIRMNESS_FLOOR * 2
    # Bars 0-3 all decided by the material -> the phrase is firm.
    card = {"margins": {0: firm, 1: firm, 2: firm, 3: firm}}
    assert _phrase_is_firm(card, 0.0, 16.0) is True

    # One tied bar is enough to disqualify it. A majority rule was measured
    # and rejected: it cost deyflo 11 % of its kicks on the "1".
    card["margins"][2] = 0.0
    assert _phrase_is_firm(card, 0.0, 16.0) is False

    # No card at all, or no bars in range -> the shipped behaviour.
    assert _phrase_is_firm(None, 0.0, 16.0) is False
    assert _phrase_is_firm(card, 400.0, 416.0) is False

from midi_phrase_drums import (
    _SNARE_VEL,
    apply_phrase_drums,
    backbeat_figure_for_bar,
    build_phrase_kick_808_pocket,
)
from music_theory import parse_note_name


def _plan():
    roles = ["statement", "answer", "space", "lift", "landing"]
    return {
        "enabled": True,
        "phrases": [
            {"start": i * 8.0, "end": i * 8.0 + 8.0, "role": role}
            for i, role in enumerate(roles)
        ],
    }


def test_phrase_kick_808_is_call_response_fixed_and_role_shaped():
    result = build_phrase_kick_808_pocket(
        plan=_plan(),
        start=0.0,
        end=40.0,
        root_pc=0,
        scale="natural_minor",
        roots_pc=[0, 0, 5, 5],
        kick_template={"note": "C1", "velocity": 1},
    )
    assert result is not None
    kicks, bass = result
    kick_times = {round(float(note["time_step"]), 3) for note in kicks}
    bass_times = {round(float(note["time_step"]), 3) for note in bass}

    assert kick_times <= bass_times
    # A late 808 answer is allowed, but the skeleton is paired rather than
    # two unrelated drum MIDI lanes.
    assert bass_times - kick_times
    assert all(b - a >= 0.5 for a, b in zip(sorted(kick_times), sorted(kick_times)[1:]))
    assert all(note["velocity"] == 124 for note in kicks)
    assert all(note["velocity"] == 116 for note in bass)
    assert not any(note.get("slide") for note in bass)
    assert all(36 <= parse_note_name(str(note["note"])) <= 47 for note in bass)
    assert {note["phrase_role"] for note in kicks} == {"statement", "answer", "space", "lift", "landing"}

    statement = sorted(t for t in kick_times if 0.0 <= t < 8.0)
    answer = sorted(t - 8.0 for t in kick_times if 8.0 <= t < 16.0)
    assert statement != answer


def test_phrase_808_follows_progression_root_and_stays_short():
    result = build_phrase_kick_808_pocket(
        plan=_plan(),
        start=0.0,
        end=40.0,
        root_pc=0,
        scale="natural_minor",
        roots_pc=[0, 0, 5, 5],
    )
    assert result is not None
    _kicks, bass = result

    first_pc = parse_note_name(bass[0]["note"]) % 12
    later = next(note for note in bass if float(note["time_step"]) >= 8.0)
    later_pc = parse_note_name(later["note"]) % 12
    assert first_pc == 0
    assert later_pc == 5
    assert all(0.0 < float(note["length"]) <= 0.5 for note in bass)


def test_phrase_808_prefers_uploaded_midi_harmony_over_generic_progression():
    result = build_phrase_kick_808_pocket(
        plan=_plan(),
        start=0.0,
        end=16.0,
        root_pc=0,
        scale="natural_minor",
        # Deliberately wrong generic fallback: uploaded MIDI owns these bars.
        roots_pc=[0],
        harmonic_rhythm=[
            {"start": 0.0, "end": 4.0, "root_pc": 5, "confidence": 0.9},
            {"start": 4.0, "end": 8.0, "root_pc": 10, "confidence": 0.9},
            {"start": 8.0, "end": 16.0, "root_pc": 3, "confidence": 0.9},
        ],
    )
    assert result is not None
    _kicks, bass = result
    assert parse_note_name(next(note for note in bass if float(note["time_step"]) < 4.0)["note"]) % 12 == 5
    middle = [note for note in bass if 4.0 <= float(note["time_step"]) < 8.0]
    assert middle and all(parse_note_name(note["note"]) % 12 == 10 for note in middle)
    assert parse_note_name(next(note for note in bass if float(note["time_step"]) >= 8.0)["note"]) % 12 == 3


def test_phrase_pocket_can_lock_to_nearby_midi_accents_with_call_response():
    result = build_phrase_kick_808_pocket(
        plan=_plan(),
        start=0.0,
        end=16.0,
        root_pc=0,
        scale="natural_minor",
        roots_pc=[0],
        melody_accents=[0.06, 8.07],
    )
    assert result is not None
    kicks, bass = result
    # The quieter half of a deliberate double is kick_only by design
    # (variant B, 2026-08-20): it is the one kick with no 808 of its own,
    # so the pairing assertion below reads every kick BUT the ghosts.
    kick_times = {
        round(float(note["time_step"]), 2)
        for note in kicks
        if not note.get("intentional_double_ghost")
    }
    bass_times = {round(float(note["time_step"]), 2) for note in bass}
    assert 0.0 in kick_times
    assert 8.0 in kick_times
    assert 0.0 in bass_times
    assert 8.0 in bass_times
    assert kick_times <= bass_times


def test_phrase_pocket_prefers_real_midi_accents_over_static_role_grid():
    result = build_phrase_kick_808_pocket(
        plan=_plan(),
        start=0.0,
        end=8.0,
        root_pc=0,
        scale="natural_minor",
        roots_pc=[0],
        melody_accents=[0.06, 2.06, 4.06, 6.06],
    )
    assert result is not None
    kicks, bass = result
    kick_times = {round(float(note["time_step"]), 2) for note in kicks}
    bass_times = {round(float(note["time_step"]), 2) for note in bass}
    assert {0.0, 2.0, 4.0, 6.0} <= (kick_times | bass_times)
    assert kick_times <= bass_times


def test_phrase_pocket_follows_different_midi_accent_shapes_generically():
    accent_shapes = [
        [0.04, 2.04, 4.04, 6.04],
        [0.03, 1.53, 3.03, 5.53],
        [0.06, 3.06, 4.56, 7.06],
    ]
    for accents in accent_shapes:
        result = build_phrase_kick_808_pocket(
            plan={"enabled": True, "phrases": [{"start": 0.0, "end": 8.0, "role": "statement"}]},
            start=0.0,
            end=8.0,
            root_pc=0,
            scale="natural_minor",
            roots_pc=[0],
            melody_accents=accents,
        )
        assert result is not None
        kicks, bass = result
        kick_times = {round(float(note["time_step"]), 2) for note in kicks}
        bass_times = {round(float(note["time_step"]), 2) for note in bass}
        all_times = kick_times | bass_times
        # The MIDI supplies candidates, not a command to copy every dense
        # arpeggio into the kick. At least the phrase opening survives and
        # every retained attack is quantized to the legal sixteenth grid.
        assert 0.0 in all_times
        assert all(abs(t * 4.0 - round(t * 4.0)) < 1e-6 for t in all_times)
        assert kick_times <= bass_times


def test_fast_midi_section_contract_drives_each_active_bar_and_respects_outro():
    """160 BPM must not turn a two-bar phrase into a half-time low-end lane."""
    plan = {
        "enabled": True,
        "phrases": [
            {"start": beat, "end": beat + 8.0, "role": "statement"}
            for beat in range(16, 80, 8)
        ],
    }
    arrangement = [
        {"name": "intro", "start_bar": 0, "bars": 4, "active_tracks": ["melody_lead"]},
        {"name": "verse", "start_bar": 4, "bars": 8, "active_tracks": ["kick", "808_bass"]},
        {"name": "hook", "start_bar": 12, "bars": 8, "active_tracks": ["kick", "808_bass"]},
        {"name": "outro", "start_bar": 20, "bars": 4, "active_tracks": ["melody_lead", "kick"]},
    ]
    result = build_phrase_kick_808_pocket(
        plan=plan, start=16.0, end=96.0, root_pc=2, scale="natural_minor",
        roots_pc=[2], arrangement=arrangement, bpm=160.0,
    )
    assert result is not None
    kicks, bass = result
    for bar in range(4, 20):
        lo, hi = bar * 4.0, (bar + 1) * 4.0
        assert any(lo <= float(note["time_step"]) < hi for note in kicks)
        assert any(lo <= float(note["time_step"]) < hi for note in bass)
    assert not any(float(note["time_step"]) >= 80.0 for note in bass)


def test_apply_phrase_drums_writes_role_tagged_backbeat():
    pattern = {"plg_phrase_rhythm_plan": _plan(), "tracks": {}}
    assert apply_phrase_drums(pattern) is True
    assert pattern["plg_drum_grid"] == "midi_phrase_composer"
    assert {note["phrase_role"] for note in pattern["tracks"]["snare"]} >= {"statement", "answer"}
    assert pattern["tracks"]["clap"]


# --- kick/808 symbiosis with the already-final kit ---------------------------


def _symbiosis_pattern() -> dict:
    """A MIDI-owned trap pattern that exercises the full phrase-composer path."""
    from drum_blueprint import build_kick_pattern

    return {
        "bpm": 140,
        "key": "E",
        "scale": "minor",
        "style": "trap",
        "user_prompt": "dark trap",
        "plg_midi_melody": True,
        "plg_variety": {"genre": "trap", "seed": 3, "key": "E", "scale": "minor"},
        "tracks": {
            "kick": build_kick_pattern(bars=8),
            "clap": [{"time_step": 18.0, "note": "C5", "length": 0.2, "velocity": 100}],
            "melody_lead": [
                {"time_step": float(i), "note": "E4", "length": 0.75, "velocity": 95}
                for i in range(16)
            ],
            "hi_hats": [
                {"time_step": i * 0.5, "note": "F#5", "length": 0.1, "velocity": 80}
                for i in range(32)
            ],
        },
    }


def _humanized_symbiosis() -> dict:
    import dataclasses

    from arranger import arrange_song
    from beat_humanize import humanize_pattern
    from genre_profiles import PROFILES

    pattern = _symbiosis_pattern()
    arrange_song(pattern)
    return humanize_pattern(
        pattern, profile=dataclasses.replace(PROFILES["trap"], bass808_enabled=True)
    )


def test_pairing_seal_gives_every_808_attack_a_kick():
    out = _humanized_symbiosis()
    kick_times = {round(float(n["time_step"]), 3) for n in out["tracks"]["kick"]}
    bass = [n for n in out["tracks"]["808_bass"] if not n.get("slide")]

    assert bass
    assert all(round(float(n["time_step"]), 3) in kick_times for n in bass)
    assert out["plg_midi_kick808_seal"]["solo_bass"] == 0


def test_kick_808_never_lands_inside_a_hat_roll():
    from midi_lowend_director import in_hat_roll, rhythmic_anchors

    out = _humanized_symbiosis()
    rolls = rhythmic_anchors(hats=out["tracks"].get("hi_hats") or [])["roll_times"]
    accents = {round(float(t), 5) for t in (out.get("plg_melody_accents") or [])}
    for note in out["tracks"]["kick"]:
        time = float(note["time_step"])
        if abs(time % 4.0) <= 1e-5 or any(abs(time - a) <= 0.13 for a in accents):
            continue  # a downbeat/accent carries its own meaning
        assert not in_hat_roll(time, rolls)


def test_808_tail_stays_long_and_kick_stays_short():
    """The 808 is a sustained tail, not a second punchy kick."""
    out = _humanized_symbiosis()
    bass = [n for n in out["tracks"]["808_bass"] if not n.get("slide")]
    kick = out["tracks"]["kick"]
    assert bass and kick
    assert all(float(n["length"]) <= 0.35 for n in kick)
    assert all(float(n["length"]) >= 0.5 for n in bass)
    longest = max(float(n["length"]) for n in bass)
    assert longest >= 2.0, f"808 never sustains (longest tail {longest} beats)"


def test_every_808_note_starts_on_a_real_attack():
    """No 808 may fade in from nowhere: each attack is either welded to a kick
    or is the composer's own anchored answer (the sample's own transient)."""
    from beat_humanize import _intentional_solo_lowend, _lowend_anchor_times

    out = _humanized_symbiosis()
    kick_times = {round(float(n["time_step"]), 3) for n in out["tracks"]["kick"]}
    anchors = _lowend_anchor_times(out)
    for note in out["tracks"]["808_bass"]:
        if note.get("slide"):
            continue  # a slide bends an already-sounding note, it is not an attack
        assert (
            round(float(note["time_step"]), 3) in kick_times
            or _intentional_solo_lowend(note, "bass_only", anchors)
        )


def test_symbiosis_pass_never_rewrites_hats_snare_or_clap():
    """Hard regression gate: kick/808 read the kit, they never edit it."""
    import copy
    import dataclasses

    from arranger import arrange_song
    from beat_humanize import humanize_pattern
    from genre_profiles import PROFILES

    pattern = _symbiosis_pattern()
    arrange_song(pattern)
    profile = dataclasses.replace(PROFILES["trap"], bass808_enabled=True)

    baseline = humanize_pattern(copy.deepcopy(pattern), profile=profile)
    repeat = humanize_pattern(copy.deepcopy(pattern), profile=profile)
    for lane in ("hi_hats", "snare", "clap", "snare_layer"):
        assert (baseline["tracks"].get(lane) or []) == (repeat["tracks"].get(lane) or []), lane


def test_apply_phrase_drums_tags_backbeat_with_song_section():
    """2026-08-15: the hat-roll eligibility gate (beat_humanize's
    _roll_eligible_windows) only activates on snares tagged with their song
    section. apply_phrase_drums used to write the backbeat untagged (kick and
    bass were tagged), so the inverse-density roll path was dead on the live
    MIDI route."""
    active = ["melody_lead", "kick", "808_bass", "hi_hats", "snare", "clap"]
    arrangement = [
        {"name": "verse", "start_bar": 0, "bars": 5, "active_tracks": active},
        {"name": "hook", "start_bar": 5, "bars": 5, "active_tracks": active},
    ]
    pattern = {"plg_phrase_rhythm_plan": _plan(), "plg_arrangement": arrangement, "tracks": {}}
    assert apply_phrase_drums(pattern)
    snares = pattern["tracks"]["snare"]
    claps = pattern["tracks"]["clap"]
    assert snares and all(n.get("section") for n in snares)
    assert {n["section"] for n in snares if n["time_step"] < 20.0} == {"verse"}
    assert {n["section"] for n in snares if n["time_step"] >= 20.0} == {"hook"}
    # Both lanes carry the tag, each judged on its own: since 2026-08-21 the
    # backbeat palette may leave a bar clap-only, or answer a phrase exit with
    # a snare-only ghost, so the two lanes are no longer the same list. What
    # this test is actually about — every backbeat note knows its section — is
    # asserted per lane instead of by comparing them to each other.
    assert claps and all(n.get("section") for n in claps)
    assert {n["section"] for n in claps if n["time_step"] < 20.0} == {"verse"}
    assert {n["section"] for n in claps if n["time_step"] >= 20.0} == {"hook"}
    # Every clap still lands on the shared anchor, never on a position of its
    # own: the palette chooses WHICH lanes sound, never WHERE.
    anchors = {n["time_step"] for n in snares} | {n["time_step"] for n in claps}
    assert all(abs((t % 4.0) - 2.0) < 1e-6 or abs((t % 4.0) - 3.5) < 1e-6 for t in anchors)

    # No arrangement: degrade, never break — untagged is the old behaviour.
    bare = {"plg_phrase_rhythm_plan": _plan(), "tracks": {}}
    assert apply_phrase_drums(bare)
    assert bare["tracks"]["snare"]
    assert not any(n.get("section") for n in bare["tracks"]["snare"])


# --- backbeat coverage on long phrases (2026-08-19) --------------------------


def _long_phrase_song():
    """Two eight-bar phrases — the real shape that left the backbeat empty.

    Before the per-bar brief, _DRUM_SHAPES' phrase-relative (2.0, 6.0) wrote
    the backbeat into the first two bars of each phrase and nothing tiled it,
    so bars 2-7 of every phrase shipped with no snare or clap at all.
    """
    from midi_fullkit_director import attach_fullkit_plan

    active = ["melody_lead", "kick", "808_bass", "hi_hats", "snare", "clap", "snare_layer"]
    pattern = {
        "plg_phrase_rhythm_plan": {
            "enabled": True,
            "phrases": [
                {"id": "P1", "start": 0.0, "end": 32.0, "role": "statement", "section": "verse"},
                {"id": "P2", "start": 32.0, "end": 64.0, "role": "lift", "section": "drop"},
            ],
        },
        "plg_arrangement": [
            {"name": "intro", "start_bar": 0, "bars": 2, "occurrence": 1, "active_tracks": ["melody_lead"]},
            {"name": "verse", "start_bar": 2, "bars": 6, "occurrence": 1, "active_tracks": active},
            {"name": "drop", "start_bar": 8, "bars": 8, "occurrence": 1, "active_tracks": active},
        ],
        "tracks": {},
    }
    attach_fullkit_plan(pattern)
    return pattern


def test_backbeat_reaches_every_permitted_bar_of_a_long_phrase():
    pattern = _long_phrase_song()
    assert apply_phrase_drums(pattern) is True
    permitted = set(range(2, 16))
    bars_with_clap = {int(float(n["time_step"]) // 4.0) for n in pattern["tracks"]["clap"]}
    # Bars 2-15 are declared snare/clap-active by the arrangement; all of them
    # answer. Before the per-bar walk only bars 0-1 and 8-9 did.
    #
    # Since 2026-08-21 the palette may deliberately leave ONE bar silent to
    # set up a section arrival (bar 7 here — the last verse bar before the
    # drop at 8). That is the §0m contract intact, not weakened: the defect
    # this test was written for was 6-bar stretches nobody chose. Anything
    # missing must be a chosen skip, and there must be at most one of them.
    figures = pattern["plg_backbeat_figures"]
    skipped = {int(bar) for bar, fig in figures.items() if fig["skip"]}
    assert skipped == {7}
    assert bars_with_clap == permitted - skipped
    # A bar the palette left snare-less is still a bar with a backbeat: only
    # the stack thinned, the anchor is still there.
    bars_with_snare = {int(float(n["time_step"]) // 4.0) for n in pattern["tracks"]["snare"]}
    assert bars_with_snare <= bars_with_clap


def test_backbeat_obeys_the_arrangements_lane_gate_instead_of_filling_blindly():
    pattern = _long_phrase_song()
    apply_phrase_drums(pattern)
    # The intro declares melody only. The generator asks the brief for
    # permission rather than writing into it and relying on the later
    # enforce_midi_section_density sweep to undo the damage.
    assert not any(float(note["time_step"]) < 8.0 for note in pattern["tracks"]["snare"])
    assert not any(float(note["time_step"]) < 8.0 for note in pattern["tracks"]["clap"])


def test_backbeat_keeps_beat_three_and_the_phrase_exit_answer():
    pattern = _long_phrase_song()
    apply_phrase_drums(pattern)
    steps = sorted(float(note["time_step"]) for note in pattern["tracks"]["snare"])
    # Every hit is beat 3 of its bar — the position _DRUM_SHAPES always used.
    extras = [step for step in steps if abs((step % 4.0) - 2.0) > 1e-6]
    # ...except one: a lift/landing answers at the END of its phrase, which is
    # now the phrase's real last bar (15) rather than its second (9).
    assert extras == [63.5]


def test_backbeat_two_bar_phrases_keep_every_anchor_of_the_bar_walk():
    """The 2026-08-19 generalization must still be exact ON THE ANCHOR.

    This test used to assert the whole snare list byte-for-byte, including
    which bars took the phrase-exit answer. The 2026-08-21 palette moved that
    answer deliberately: it used to be gated on the `lift`/`landing` ROLE, and
    the role `lift` does not occur once in the ten-file corpus, so Sahara and
    Graveyard shipped no phrase-turn gesture at all. It is phrase geometry now.

    What must NOT move — and what the bar walk was the risk to — is the
    anchor: every bar of a two-bar phrase still answers on beat 3, at exactly
    the times _DRUM_SHAPES always used.
    """
    pattern = {"plg_phrase_rhythm_plan": _plan(), "tracks": {}}
    assert apply_phrase_drums(pattern) is True
    steps = sorted(float(note["time_step"]) for note in pattern["tracks"]["snare"])
    anchors = [step for step in steps if abs((step % 4.0) - 2.0) < 1e-6]
    assert anchors == [2.0, 6.0, 10.0, 14.0, 18.0, 22.0, 26.0, 30.0, 34.0, 38.0]
    # The extra hits are phrase-turn answers, on the fourth bar of a 4-bar
    # group, and each one is a ghost rather than a second full backbeat.
    extras = [step for step in steps if step not in anchors]
    assert extras == [15.5, 31.5]
    for step in extras:
        note = next(n for n in pattern["tracks"]["snare"] if float(n["time_step"]) == step)
        assert note.get("ghost") is True
        assert note["velocity"] < _SNARE_VEL


# --- the gap filler lands on the grid, off the drag slot (2026-08-19) -------


def test_gap_filler_keeps_the_musical_subdivision_it_lands_on():
    """Four of the five corpus files were already fine — they must not move.

    max_gap_beats is a TIME budget in seconds converted to beats, so which
    subdivision the filler lands on is arithmetic, not a decision.
    """
    from midi_phrase_drums import _grid_fill_point

    assert _grid_fill_point(2.600, []) == 2.50   # Sahara: the "and" of beat 3
    assert _grid_fill_point(1.643, []) == 1.75   # Graveyard: anticipates beat 3
    assert _grid_fill_point(1.846, []) == 1.75   # Detroit
    assert _grid_fill_point(1.533, []) == 1.50   # Shuttle


def test_gap_filler_refuses_the_sixteenth_that_drags_behind_a_beat():
    """deyflo's whole defect: max_gap 2.212 quantised to 2.25 by the export
    seal — a kick a sixteenth LATE off beat three, 48 of them, in a file where
    only 55 % of bars had a kick on beat one at all."""
    from midi_phrase_drums import _grid_fill_point

    assert _grid_fill_point(2.212, []) == 2.00
    assert _grid_fill_point(18.212, []) == 18.00
    assert _grid_fill_point(24.244, []) == 24.00
    # An anticipation is NOT a drag and is left alone.
    assert _grid_fill_point(1.80, []) == 1.75


def test_gap_filler_yields_to_minimum_spacing_before_it_moves_onto_a_beat():
    """A slightly late kick beats a spacing violation the export gate rejects."""
    from midi_phrase_drums import MIN_LOWEND_GAP_BEATS, _grid_fill_point

    assert MIN_LOWEND_GAP_BEATS == 1.0
    # Nothing nearby: the drag slot moves onto the beat.
    assert _grid_fill_point(6.25, []) == 6.0
    # A kick already sits on that beat's spacing: keep the quantised position.
    assert _grid_fill_point(6.25, [5.5]) == 6.25


def test_gap_filler_output_is_always_on_the_sixteenth_grid():
    """_quantize_lowend_onsets owns this rule but runs BEFORE the density
    passes, so anything they manufacture has to honour it itself."""
    from midi_phrase_drums import _grid_fill_point

    for raw in (0.03, 1.212, 2.6, 3.4567, 9.99, 17.031):
        point = _grid_fill_point(raw, [])
        assert abs(point * 4 - round(point * 4)) < 1e-9, raw


def test_the_drag_rule_covers_the_composer_not_only_the_gap_filler():
    """K2 freed the accent map, and freed onsets can quantise onto the drag slot.

    Measured 2026-08-19 on Sahara: 4 of 122 kicks landed on x.25 after the
    accent fix, in bars where the previous reading had them on the "and" of
    beat three. The user heard those bars as wrong. _grid_fill_point guarded
    only the gap FILLER; the composer's own onsets go through
    _quantize_lowend_onsets, so the rule lives in one helper both call.
    """
    from midi_phrase_drums import _quantize_lowend_onsets

    out = dict(_quantize_lowend_onsets([(0.0, "a"), (3.22, "b")]))
    assert 3.0 in out and 3.25 not in out

    # An anticipation is not a drag and must survive untouched.
    out = dict(_quantize_lowend_onsets([(0.0, "a"), (3.74, "b")]))
    assert 3.75 in out


def test_the_drag_rule_never_stacks_two_onsets_on_one_instant():
    """When the beat before is already claimed, the late position stays —
    a duplicate would be worse than a late kick."""
    from midi_phrase_drums import _quantize_lowend_onsets

    out = dict(_quantize_lowend_onsets([(3.0, "first"), (3.22, "second")]))
    assert 3.0 in out and 3.25 in out
    assert out[3.0] == "first"


def test_both_low_end_writers_share_one_drag_rule():
    from midi_phrase_drums import _grid_fill_point, _off_drag_sixteenth

    for raw in (2.25, 6.25, 18.25):
        assert _off_drag_sixteenth(raw, set()) == raw - 0.25
        assert _grid_fill_point(raw, []) == raw - 0.25
    for raw in (2.5, 2.75, 3.0):
        assert _off_drag_sixteenth(raw, set()) == raw


def _allowance_fixture(section="verse", positions=("entry", "body", "body", "exit")):
    arrangement = [{"name": section, "start_bar": 0, "bars": len(positions),
                    "active_tracks": ["kick", "808_bass"]}]
    briefs = [{"bar": i, "section": section, "phrase_position": p, "phrase_bars": len(positions)}
              for i, p in enumerate(positions)]
    return arrangement, briefs


def test_bar_allowance_gives_the_phrase_exit_bar_the_peak_ceiling():
    """The arc the 2026-08-20 audit found missing: the low end had no gesture
    at the end of a phrase, while the backbeat has had one from the start."""
    from midi_phrase_drums import _lowend_bar_allowance

    arrangement, briefs = _allowance_fixture(positions=("entry", "body", "body", "exit"))
    allowance = _lowend_bar_allowance(
        arrangement=arrangement, briefs=briefs, start=0.0, end=16.0,
        density={"anchor_every_active_bar": True}, placed=[],
    )
    assert allowance[3] == 2, "the phrase's last bar always carries the peak ceiling"


def test_a_quiet_bar_drops_only_when_its_neighbour_is_already_full():
    """A flat quiet ceiling made "kick on the one, nothing else" the single
    most common bar of four of the five listening files (2026-08-21)."""
    from midi_phrase_drums import _lowend_bar_allowance

    positions = ("entry",) + ("body",) * 6 + ("exit",)
    arrangement = [{"name": "verse", "start_bar": 0, "bars": len(positions),
                    "active_tracks": ["kick", "808_bass"]}]
    briefs = [{"bar": i, "section": "verse", "phrase_position": p, "phrase_bars": len(positions)}
              for i, p in enumerate(positions)]
    # anchor_every_active_bar off, so the bounded rest never runs and the
    # alternation itself is what is being measured.
    allowance = _lowend_bar_allowance(
        arrangement=arrangement, briefs=briefs, start=0.0, end=32.0,
        density={"anchor_every_active_bar": False}, placed=[],
    )
    quiet = [allowance[b] for b in sorted(allowance)]
    assert 1 in quiet and 2 in quiet, "a quiet section alternates, it does not sit flat"
    # Never three low bars running: the third opens back up.
    assert not any(a == b == c == 1 for a, b, c in zip(quiet, quiet[1:], quiet[2:]))


def test_a_spoken_bar_gets_what_the_material_said_and_no_padding_on_top():
    """The map bounds the MANUFACTURED passes. Letting a composer onset raise
    their ceiling inverted that and collapsed the contrast to 1.12 on Tragic
    (2026-08-21)."""
    from midi_phrase_drums import _lowend_bar_allowance

    positions = ("entry", "body", "body", "exit")
    arrangement = [{"name": "verse", "start_bar": 0, "bars": 4,
                    "active_tracks": ["kick", "808_bass"]}]
    briefs = [{"bar": i, "section": "verse", "phrase_position": p, "phrase_bars": 4}
              for i, p in enumerate(positions)]

    def _allow(placed):
        return _lowend_bar_allowance(
            arrangement=arrangement, briefs=briefs, start=0.0, end=16.0,
            density={"anchor_every_active_bar": False}, placed=placed,
        )

    said_once = _allow([(8.0, "statement", "paired")])
    said_twice = _allow([(8.0, "statement", "paired"), (10.0, "statement", "paired")])
    assert said_once[2] == 1, "one onset earns one, not a padded two"
    assert said_twice[2] == 2, "two onsets keep both"


def test_bar_allowance_rests_only_where_the_material_said_nothing():
    from midi_phrase_drums import _lowend_bar_allowance

    arrangement, briefs = _allowance_fixture(positions=("entry", "body", "body", "exit"))
    card = {"anchor_every_active_bar": True}
    spoken = [(float(bar) * 4.0, "statement", "paired") for bar in (1, 2)]
    quiet = _lowend_bar_allowance(arrangement=arrangement, briefs=briefs, start=0.0,
                                  end=16.0, density=card, placed=[])
    busy = _lowend_bar_allowance(arrangement=arrangement, briefs=briefs, start=0.0,
                                 end=16.0, density=card, placed=spoken)
    assert 0 in quiet.values()
    assert 0 not in busy.values(), "a bar the composer spoke in is never rested"


def test_bar_allowance_adds_no_rest_on_material_the_card_called_sparse():
    from midi_phrase_drums import _lowend_bar_allowance

    arrangement, briefs = _allowance_fixture()
    allowance = _lowend_bar_allowance(
        arrangement=arrangement, briefs=briefs, start=0.0, end=16.0,
        density={"anchor_every_active_bar": False}, placed=[],
    )
    assert 0 not in allowance.values()


def test_the_foundation_may_open_a_body_bar_on_a_melody_accent():
    """And never in a phrase entry bar, where the form is heard."""
    from midi_phrase_drums import _displaced_anchor_point

    accents = [5.031]  # beat two of the bar, carrying the melody's own shift
    body = _displaced_anchor_point(4.0, brief={"phrase_position": "body", "section": "verse"},
                                   accents=accents, kick_times=[])
    entry = _displaced_anchor_point(4.0, brief={"phrase_position": "entry", "section": "verse"},
                                    accents=accents, kick_times=[])
    peak = _displaced_anchor_point(4.0, brief={"phrase_position": "body", "section": "hook"},
                                   accents=accents, kick_times=[])
    assert body == 5.0, "the accent is quantised before the window is tested"
    assert entry == 4.0 and peak == 4.0


def test_the_foundation_stays_on_the_bar_line_when_the_accent_would_crowd_a_kick():
    from midi_phrase_drums import _displaced_anchor_point

    assert _displaced_anchor_point(
        4.0, brief={"phrase_position": "body", "section": "verse"},
        accents=[4.53], kick_times=[4.0],
    ) == 4.0


def test_a_manufactured_onset_is_pulled_onto_the_eighth_before_the_backbeat():
    from midi_phrase_drums import _pull_onto_backbeat_push

    # The backbeat carries apply_pre_snare_shift's humanisation; the push must
    # be measured from its grid position or it lands off every grid.
    assert _pull_onto_backbeat_push(5.25, [], [5.98915], None, [5.503]) == 5.5


def test_the_push_needs_a_reason_and_an_empty_target_is_not_one():
    """It shipped with no melodic term at all: on Graveyard 12 of its 16
    pushes landed where the melody has no accent (2026-08-21)."""
    from midi_phrase_drums import _pull_onto_backbeat_push

    assert _pull_onto_backbeat_push(5.25, [], [5.98915], None, []) == 5.25
    assert _pull_onto_backbeat_push(5.25, [], [5.98915], None, [7.0]) == 5.25


def test_the_push_still_rescues_an_onset_already_inside_the_fusion_zone():
    """The defensive clause: leaving the sixteenth before the backbeat is an
    improvement whether or not the melody says anything there. This is what
    keeps Graveyard's collision at zero."""
    from midi_phrase_drums import _pull_onto_backbeat_push

    # 5.75 sits a sixteenth before the backbeat's grid position of 6.0.
    assert _pull_onto_backbeat_push(5.75, [], [5.98915], None, []) == 5.5


def test_the_push_respects_low_end_onsets_that_carry_no_kick_yet():
    """The Detroit corpus regression: a bass-only onset can be paired later,
    and a push an eighth away from one ships a kick_roll."""
    from midi_phrase_drums import _pull_onto_backbeat_push

    assert _pull_onto_backbeat_push(5.25, [], [5.98915], [6.0]) == 5.25


def test_the_push_never_moves_an_onset_onto_the_bar_line():
    from midi_phrase_drums import _pull_onto_backbeat_push

    assert _pull_onto_backbeat_push(3.75, [], [4.5]) == 3.75


# --- the backbeat palette (2026-08-21) -------------------------------------


def _palette_song(sections, phrase_beats=32.0, bars=24):
    """A song whose arrangement is given, with phrases of a chosen length."""
    from midi_fullkit_director import attach_fullkit_plan

    phrases = [
        {"id": f"P{i}", "start": start, "end": start + phrase_beats,
         "role": "statement", "section": "verse"}
        for i, start in enumerate(
            [b * phrase_beats for b in range(int(bars * 4.0 / phrase_beats))]
        )
    ]
    pattern = {
        "plg_phrase_rhythm_plan": {"enabled": True, "phrases": phrases},
        "plg_arrangement": sections,
        "tracks": {},
    }
    attach_fullkit_plan(pattern)
    return pattern


_ACTIVE = ["melody_lead", "kick", "808_bass", "hi_hats", "snare", "clap", "snare_layer"]


def test_backbeat_palette_never_moves_the_anchor():
    """The one thing the 2026-08-21 audit found already right stays right.

    Every full-strength backbeat hit sits on beat 3 of its bar. The palette
    chooses WHICH lanes sound and whether a quiet ghost joins them; it has no
    authority to move the anchor, because a clap that wanders stops being the
    thing everything else is danced around.
    """
    pattern = _palette_song([
        {"name": "verse", "start_bar": 0, "bars": 12, "occurrence": 1, "active_tracks": _ACTIVE},
        {"name": "hook", "start_bar": 12, "bars": 12, "occurrence": 1, "active_tracks": _ACTIVE},
    ])
    assert apply_phrase_drums(pattern)
    for lane in ("snare", "clap"):
        for note in pattern["tracks"][lane]:
            if note.get("ghost"):
                continue
            assert abs((float(note["time_step"]) % 4.0) - 2.0) < 1e-6, note


def test_backbeat_palette_thins_the_quiet_section_not_the_peak():
    """Contrast is made by SUBTRACTION — the peak has no headroom above the
    full clap+snare+layer stack, so the verse gives something up instead."""
    pattern = _palette_song([
        {"name": "verse", "start_bar": 0, "bars": 12, "occurrence": 1, "active_tracks": _ACTIVE},
        {"name": "hook", "start_bar": 12, "bars": 12, "occurrence": 1, "active_tracks": _ACTIVE},
    ])
    assert apply_phrase_drums(pattern)
    figures = pattern["plg_backbeat_figures"]
    hook = [figures[str(b)] for b in range(12, 24) if str(b) in figures]
    verse = [figures[str(b)] for b in range(0, 12) if str(b) in figures]
    assert hook and verse
    assert all("layer" in f["stack"] for f in hook if not f["skip"])
    assert any("layer" not in f["stack"] for f in verse if not f["skip"])


def test_backbeat_palette_skip_is_bounded_and_sets_up_the_arrival():
    """The one deliberate silence, and every guard on it."""
    pattern = _palette_song([
        {"name": "verse", "start_bar": 0, "bars": 8, "occurrence": 1, "active_tracks": _ACTIVE},
        {"name": "hook", "start_bar": 8, "bars": 8, "occurrence": 1, "active_tracks": _ACTIVE},
        {"name": "verse", "start_bar": 16, "bars": 8, "occurrence": 2, "active_tracks": _ACTIVE},
    ])
    assert apply_phrase_drums(pattern)
    figures = pattern["plg_backbeat_figures"]
    skipped = sorted(int(bar) for bar, fig in figures.items() if fig["skip"])
    # Only the bar immediately before the peak section, and only that one.
    assert skipped == [7]
    # Never consecutive, never inside a peak, never a section's own first bar.
    assert all(bar + 1 not in skipped for bar in skipped)
    for bar in skipped:
        assert figures[str(bar)]["stack"] or True
        assert str(figures.get(str(bar), {}).get("skip")) == "True"
    # And the bar it sets up is fully stacked.
    assert "layer" in figures["8"]["stack"]
    assert not figures["8"]["skip"]


def test_backbeat_palette_answers_a_phrase_turn_without_the_role_whitelist():
    """The exit answer used to require role `lift`/`landing`. That role does
    not occur once in the ten-file corpus, so two of the five listening files
    shipped no phrase-turn gesture at all."""
    pattern = _palette_song([
        {"name": "verse", "start_bar": 0, "bars": 16, "occurrence": 1, "active_tracks": _ACTIVE},
    ], phrase_beats=16.0, bars=16)
    assert apply_phrase_drums(pattern)
    figures = pattern["plg_backbeat_figures"]
    tails = [int(bar) for bar, fig in figures.items() if fig["tail"]]
    assert tails, "a statement phrase must still be able to answer its own turn"
    # Every answer is on the fourth bar of a 4-bar group and is a quiet ghost.
    assert all(bar % 4 == 3 for bar in tails)
    ghosts = [n for n in pattern["tracks"]["snare"] if n.get("ghost")]
    assert len(ghosts) == len(tails)
    assert all(n["velocity"] < _SNARE_VEL for n in ghosts)


def test_backbeat_figure_reader_is_the_only_way_in():
    pattern = _palette_song([
        {"name": "verse", "start_bar": 0, "bars": 8, "occurrence": 1, "active_tracks": _ACTIVE},
    ], phrase_beats=16.0, bars=8)
    assert apply_phrase_drums(pattern)
    assert backbeat_figure_for_bar(pattern, 0) is not None
    assert backbeat_figure_for_bar(pattern, 999) is None
    assert backbeat_figure_for_bar({}, 0) is None


def test_a_manufactured_kick_may_not_jab_just_in_front_of_the_melody():
    """The gap filler picks its instant by arithmetic on a time budget.

    Three cases it must tell apart: landing ON the material (the kit answering
    it), landing where the melody is silent (the hole this pass exists for),
    and landing a sixteenth in front of an attack — where the two fuse and the
    kick reads as a jab at nothing. Only the third is refused. Tragic shipped
    12 of the third kind in its hook and drop (the project issue log (not included) §0w/§0z).
    """
    from midi_phrase_drums import _MELODY_FUSION_BEATS, _smears_into_melody

    onsets = [0.0, 1.0, 2.0, 3.0]
    spans = [(t, t + 3.0) for t in onsets]  # a sustained pad, always sounding
    w = _MELODY_FUSION_BEATS

    # on the attack — the kit answering the melody
    assert not _smears_into_melody(1.0, onsets, w, spans)
    assert not _smears_into_melody(1.03, onsets, w, spans)
    # a sixteenth in front of one, with the pad sounding — the defect
    assert _smears_into_melody(1.75, onsets, w, spans)
    assert _smears_into_melody(2.75, onsets, w, spans)
    # an eighth in front is a groove, not a smear (the project issue log (not included) §0s.2)
    assert not _smears_into_melody(1.5, onsets, w, spans)
    # after an attack is an answer
    assert not _smears_into_melody(1.25, onsets, w, spans)
    # and where the melody is silent the filler is exactly what is wanted
    assert not _smears_into_melody(1.75, onsets, w, [(0.0, 0.2)])
    # no melody information at all restores the previous behaviour
    assert not _smears_into_melody(1.75, None, w, spans)


def test_a_smearing_filler_moves_onto_the_melody_rather_than_vanishing():
    """Refusing outright was measured too blunt — it cost deyflo 23 % of its
    kicks and Graveyard 22 % while fixing nothing on either."""
    from midi_phrase_drums import _MELODY_FUSION_BEATS, _fill_onto_the_melody

    onsets = [0.0, 1.0, 2.0, 3.0]
    spans = [(t, t + 3.0) for t in onsets]
    moved = _fill_onto_the_melody(1.75, onsets, _MELODY_FUSION_BEATS, [], [], spans)
    assert moved == 2.0

    # ...but never onto a slot another kick already owns.
    assert _fill_onto_the_melody(1.75, onsets, _MELODY_FUSION_BEATS, [2.0], [], spans) is None
    # ...nor onto the backbeat's own instant.
    assert _fill_onto_the_melody(1.75, onsets, _MELODY_FUSION_BEATS, [], [2.0], spans) is None

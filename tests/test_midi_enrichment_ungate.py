"""Final MIDI architecture: the user's uploaded MIDI is the sole melodic
source, transformed only by (a) humanize timing/velocity and (b) the narrow
structure-anchored accent (add_structural_accent_notes) — never a separate
counter_melody/chords/texture/fx lane, whether composed from scratch OR
derived from the existing melody (harmonizer/octave-layer/delay-accent all
stay MIDI-gated, same as chord_bed/bell-octave/etc).

Also covers the root-cause fix for symbolic_composer.apply_symbolic_composition,
which used to silently replace melody_lead/counter_melody for rage/opium
regardless of plg_midi_melody."""

from __future__ import annotations

import dataclasses

from arranger import arrange_song
from beat_humanize import build_sidechain_hints, humanize_pattern
from genre_profiles import PROFILES


def _melody(bars: int = 16) -> list[dict]:
    notes = ["E4", "G4", "B4", "A4", "G4", "E4", "D4", "E4"]
    out = []
    for bar in range(bars):
        for i, n in enumerate(notes):
            out.append({
                "time_step": round(bar * 4.0 + i * 0.5, 3),
                "note": n,
                "length": 0.45,
                "velocity": 92 + (i % 3) * 6,
            })
    return out


def _midi_pattern(genre: str, *, bars: int = 16) -> dict:
    pattern = {
        "bpm": 142,
        "key": "E",
        "scale": "minor",
        "style": genre,
        "user_prompt": f"{genre} type beat",
        "plg_midi_melody": True,
        "plg_total_bars": bars,
        "plg_variety": {"seed": 77, "key": "E", "scale": "minor", "genre": genre, "bpm": 142},
        "tracks": {
            "melody_lead": _melody(bars),
            "kick": [{"time_step": float(b) * 4.0, "note": "C1", "length": 0.3, "velocity": 112} for b in range(bars)],
        },
    }
    arrange_song(pattern)
    return pattern


def test_harmonizer_stays_gated_under_midi_melody():
    pattern = _midi_pattern("toliver")
    assert pattern["plg_midi_melody"] is True
    out = humanize_pattern(pattern, profile=PROFILES["toliver"])
    assert not out["tracks"].get("counter_melody")


def test_octave_down_layer_stays_gated_under_midi_melody():
    pattern = _midi_pattern("trap")
    out = humanize_pattern(pattern, profile=PROFILES["trap"])
    assert not out["tracks"].get("texture")


def test_octave_up_shimmer_stays_gated_under_midi_melody():
    pattern = _midi_pattern("minimal_trap")
    out = humanize_pattern(pattern, profile=PROFILES["minimal_trap"])
    assert not out["tracks"].get("texture")


def test_delay_accent_stays_gated_under_midi_melody():
    for seed in range(20):
        pattern = _midi_pattern("toliver")
        pattern["plg_variety"]["seed"] = seed
        out = humanize_pattern(pattern, profile=PROFILES["toliver"])
        assert not out["tracks"].get("fx")


def test_chord_bed_and_counter_melody_stay_gated_under_midi_melody():
    pattern = _midi_pattern("toliver")
    out = humanize_pattern(pattern, profile=PROFILES["toliver"])
    assert not out["tracks"].get("chords")
    assert not out["tracks"].get("counter_melody")


def test_bell_octave_stays_gated_under_midi_melody():
    pattern = _midi_pattern("pluggnb")
    out = humanize_pattern(pattern, profile=PROFILES["pluggnb"])
    assert not out["tracks"].get("counter_melody")


def test_rage_melody_is_not_replaced_by_symbolic_opium_composer():
    """Root-cause regression guard: apply_symbolic_composition used to
    overwrite tracks["melody_lead"] with a from-scratch composed hook for
    rage/opium (should_apply_symbolic fires whenever markov_hats=True,
    unconditionally of plg_midi_melody) — the user's own uploaded notes must
    survive intact.

    The count half of this check was rewritten 2026-08-26 and STRENGTHENED in
    the process. It read `original <= final or len(final) >= len(original)`,
    whose second branch a from-scratch overwrite passes outright as long as it
    composes at least as many notes as it destroyed — so against the defect it
    is named for, it was close to a no-op.

    Exactly one owner may now shrink this lane —
    `beat_humanize._apply_section_melody_arrangement`, which removes and only
    removes (proved by object identity in
    tests/test_section_melody_arrangement.py). So the property worth asserting
    is that every note the lane LOST is accounted for by that owner's own
    published report. A composer that overwrote the lane would move the count
    without moving the report, and fails here.
    """
    pattern = _midi_pattern("rage")
    original = len(pattern["tracks"]["melody_lead"])
    out = humanize_pattern(pattern, profile=PROFILES["rage"])
    final = len(out["tracks"]["melody_lead"])

    report = out.get("plg_melody_arrangement") or {}
    assert report, "nothing but the section arrangement may thin melody_lead"
    assert report["notes_after"] == final
    assert report["notes_before"] - report["removed"] == final
    # Everything else in humanize may still only ADD to the lane.
    assert report["notes_before"] >= original
    assert not out.get("plg_symbolic_opium_melody")
    assert not out["tracks"].get("counter_melody")


def test_opium_melody_is_not_replaced_by_symbolic_opium_composer():
    pattern = _midi_pattern("opium")
    out = humanize_pattern(pattern, profile=PROFILES["opium"])
    assert not out.get("plg_symbolic_opium_melody")
    assert not out["tracks"].get("counter_melody")


def test_kick_syncopation_stays_dormant_for_bass808_family():
    # build_trap_kick_times owns density/syncopation for bass808_enabled
    # profiles — add_kick_syncopation's output must be discarded (its call
    # runs before _apply_808_bass_lane rewrites the kick track wholesale).
    profile = dataclasses.replace(PROFILES["trap"], kick_syncopation=1.0)
    pattern = _midi_pattern("trap")
    out = humanize_pattern(pattern, profile=profile)
    kick = out["tracks"].get("kick") or []
    assert not any(n.get("kick_sync") for n in kick)


def test_sidechain_hint_targets_808_bass_when_bass808_enabled():
    # Routing (active_808_lane in beat_humanize) must be profile-driven
    # (bass808_enabled), never note-count-driven — sub_808 is intentionally
    # dropped for this family regardless of how many 808_bass notes exist.
    pattern = _midi_pattern("trap")
    out = humanize_pattern(pattern, profile=PROFILES["trap"])
    assert out["tracks"].get("808_bass")
    assert not out["tracks"].get("sub_808")
    targets = {h["target"] for h in out["plg_producer_meta"]["sidechain"]}
    assert targets == {"808_bass"}


def test_sidechain_hint_targets_sub_808_when_808_bass_absent():
    pattern = _midi_pattern("boombap")
    out = humanize_pattern(pattern, profile=PROFILES["boombap"])
    assert not out["tracks"].get("808_bass")
    targets = {h["target"] for h in out["plg_producer_meta"]["sidechain"]}
    assert targets <= {"sub_808"}


def test_build_sidechain_hints_respects_target_param():
    kicks = [{"time_step": 0.0}, {"time_step": 4.0}]
    hints = build_sidechain_hints(kicks, target="808_bass")
    assert all(h["target"] == "808_bass" for h in hints)
    assert len(hints) == 6  # 3 automation points per kick

    default_hints = build_sidechain_hints(kicks)
    assert all(h["target"] == "sub_808" for h in default_hints)


def test_midi_hidden_export_keys_never_hides_melody_lead():
    from flp_template import MIDI_HIDDEN_EXPORT_KEYS, active_export_channels

    assert "melody_lead" not in MIDI_HIDDEN_EXPORT_KEYS
    pattern = _midi_pattern("trap")
    out = humanize_pattern(pattern, profile=PROFILES["trap"])
    assert "melody_lead" in active_export_channels(out)

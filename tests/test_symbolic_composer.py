import random
from unittest.mock import patch

import symbolic_composer as sc
from drum_blueprint import build_kick_pattern
from pattern_utils import parse_note_name, track_notes
from symbolic_composer import (
    apply_opium_melody,
    apply_opium_octave_jumps,
    apply_symbolic_composition,
    clear_steps,
    compose_kick_locked_808,
    compose_markov_hats,
    compose_opium_counter_melody,
    compose_opium_melody_hook,
    drop_generator,
    ensure_opium_melody_if_needed,
    generate_snare_roll,
    rebuild_sub_808_from_kick,
)
from music_theory import SCALES


def test_markov_hat_roll_probability(monkeypatch):
    monkeypatch.setenv("PLG_USE_SYMBOLIC", "1")
    rng = random.Random(42)
    hats = compose_markov_hats(4, rng)
    assert len(hats) >= 8
    assert any(n.get("hat_steady") for n in hats)
    rolls = [n for n in hats if n.get("hat_roll")]
    assert rolls, "seed 42 should produce at least one hat roll in 4 bars"


def test_hat_markov_matrix_sums_to_one():
    for state, transitions in sc.HAT_MARKOV.items():
        assert abs(sum(transitions.values()) - 1.0) < 1e-6, state


def test_clear_steps_removes_window():
    pattern = {
        "tracks": {
            "hi_hats": [
                {"time_step": 28.0, "note": "C5", "length": 0.25, "velocity": 90},
                {"time_step": 29.0, "note": "C5", "length": 0.25, "velocity": 90},
                {"time_step": 33.0, "note": "C5", "length": 0.25, "velocity": 90},
            ]
        }
    }
    removed = clear_steps(pattern, 28.0, 32.0, ("hi_hats",))
    assert removed == 2
    assert len(pattern["tracks"]["hi_hats"]) == 1
    assert pattern["tracks"]["hi_hats"][0]["time_step"] == 33.0


def test_drop_generator_bar_8(monkeypatch):
    monkeypatch.setenv("PLG_USE_SYMBOLIC", "1")
    pattern = {
        "bpm": 145,
        "style": "rage",
        "tracks": {
            "hi_hats": [
                {"time_step": t, "note": "C5", "length": 0.25, "velocity": 90}
                for t in (27.5, 29.0, 30.5)
            ],
            "clap": [{"time_step": 30.0, "note": "E1", "length": 0.25, "velocity": 100}],
            "snare": [{"time_step": 30.0, "note": "D1", "length": 0.25, "velocity": 110}],
        },
    }
    rng = random.Random(1)
    assert drop_generator(pattern, rng, probability=1.0) is True
    assert not any(28.0 <= n["time_step"] < 32.0 for n in pattern["tracks"]["hi_hats"])
    rolls = [n for n in pattern["tracks"]["snare"] if n.get("snare_roll")]
    assert rolls
    assert rolls[0]["velocity"] >= sc.SNARE_ROLL_VEL_MIN
    assert rolls[-1]["velocity"] <= 127


def test_snare_roll_velocity_ladder():
    rng = random.Random(0)
    notes = generate_snare_roll(28.0, 32.0, rng)
    assert len(notes) >= 4
    assert notes[0]["velocity"] <= notes[-1]["velocity"]


def test_pipeline_calls_validator(monkeypatch):
    # The GrooVAE humanization step between the composer and the validator was
    # retired 2026-08-27; the rest of the chain is unchanged.
    monkeypatch.setenv("PLG_USE_SYMBOLIC", "1")
    pattern = {"bpm": 140, "style": "rage", "tracks": {"kick": []}}
    from validator import ValidationReport

    with (
        patch.object(sc, "apply_symbolic_composition", side_effect=lambda p, **_: p) as sym,
        patch(
            "validator.validate_and_sanitize_pattern",
            return_value=(pattern, ValidationReport(ok=True)),
        ) as val,
        patch.object(sc, "rebuild_sub_808_from_kick") as rebuild,
    ):
        sc.apply_drum_symbolic_pipeline(pattern)
    sym.assert_called_once()
    val.assert_called_once()
    rebuild.assert_called_once()


def test_kick_locked_808_replaces_hat_grid():
    kick = build_kick_pattern(bars=2)
    garbage = [
        {"time_step": i * 0.25, "note": "C5", "length": 0.2, "velocity": 127, "hat_roll": True}
        for i in range(32)
    ]
    pattern = {
        "bpm": 145,
        "tracks": {
            "kick": kick,
            "sub_808": garbage,
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 8.0, "velocity": 95}],
        },
    }
    rebuild_sub_808_from_kick(pattern)
    bass = track_notes(pattern, "sub_808")
    assert len(bass) == len(kick)
    assert all(n.get("kick_locked") for n in bass)
    assert not any(n.get("hat_roll") for n in bass)
    for i, note in enumerate(bass[:-1]):
        assert float(note["length"]) == round(float(bass[i + 1]["time_step"]) - float(note["time_step"]), 5)


def test_compose_kick_locked_808_no_overlaps():
    pattern = {
        "tracks": {
            "kick": [
                {"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 110},
                {"time_step": 2.0, "note": "C1", "length": 0.25, "velocity": 110},
            ],
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 4.0, "velocity": 90}],
        }
    }
    bass = compose_kick_locked_808(pattern)
    assert len(bass) == 2
    assert bass[0]["length"] == 2.0
    assert bass[0]["time_step"] + bass[0]["length"] <= bass[1]["time_step"]


def _hook_key(seed: int = 0, *, key: str = "E", scale: str = "minor", variant: int = 0) -> dict:
    ctx = {"key": key, "scale": scale, "plg_variety": {"melody_variant": variant}}
    return sc._key_cfg_from_pattern(ctx, random.Random(seed))


def test_opium_hook_uses_dark_keys_and_sustained_notes():
    rng = random.Random(7)
    key_cfg = _hook_key(7, key="F#", scale="phrygian", variant=2)
    hook, key = compose_opium_melody_hook(rng, key_cfg=key_cfg, melody_variant=2)
    assert key["label"] == "F# phrygian"
    assert len(hook) == 4
    assert all(float(n["length"]) >= 2.0 for n in hook)
    root_pc = int(key["root_pc"])
    scale = str(key["scale"])
    pcs = {(root_pc + i) % 12 for i in SCALES[scale]}
    for note in hook:
        midi = parse_note_name(str(note["note"]))
        assert midi % 12 in pcs
        assert sc.OPIUM_MELODY_LO <= midi <= sc.OPIUM_MELODY_HI


def test_opium_hook_walks_darkly_without_seesaw():
    rng = random.Random(99)
    key_cfg = _hook_key(99, key="A", scale="minor", variant=1)
    hook, key = compose_opium_melody_hook(rng, key_cfg=key_cfg, melody_variant=1)
    midis = [parse_note_name(str(n["note"])) for n in hook]
    # dark stepwise motion: small in-scale moves, no wide register leaps…
    for a, b in zip(midis, midis[1:]):
        assert sc._semitone_distance(a, b) <= 5 or a % 12 == b % 12
        assert abs(a - b) <= 12
    # …and real pitch variety instead of the old two-note see-saw
    assert len(set(midis)) >= 3


def test_opium_dark_keys_and_sustain_lengths():
    key_cfg = _hook_key(1, key="D", scale="minor", variant=3)
    hook, _ = compose_opium_melody_hook(random.Random(1), key_cfg=key_cfg, melody_variant=3)
    assert all(float(note["length"]) in sc.OPIUM_SUSTAIN_LENGTHS for note in hook)


def test_opium_octave_jumps_in_bars_three_and_four():
    rng = random.Random(3)
    key_cfg = _hook_key(3, key="E", scale="minor", variant=0)
    hook, _ = compose_opium_melody_hook(rng, key_cfg=key_cfg, melody_variant=0)
    tiled = sc.tile_melody_loop(hook, 8)
    out = apply_opium_octave_jumps(tiled, rng)
    jumps = [n for n in out if n.get("opium_octave_jump")]
    assert jumps
    for jump in jumps:
        t = float(jump["time_step"])
        assert any(
            bar3 <= t < bar4
            for bar3 in (8.0, 24.0)
            for bar4 in (16.0, 32.0)
            if bar3 <= t < bar4
        )
        base = next(
            n
            for n in tiled
            if float(n["time_step"]) == t and not n.get("opium_octave_jump")
        )
        base_midi = parse_note_name(str(base["note"]))
        jump_midi = parse_note_name(str(jump["note"]))
        assert jump_midi - base_midi in (12, 24)


def test_opium_counter_on_bars_four_and_eight():
    rng = random.Random(11)
    key = _hook_key(11, key="E", scale="minor")
    counter = compose_opium_counter_melody(rng, key, total_bars=8)
    assert len(counter) == 2
    bars = {int(float(n["time_step"]) // sc.BEATS_PER_BAR) + 1 for n in counter}
    assert bars == {4, 8}
    for note in counter:
        midi = parse_note_name(str(note["note"]))
        assert sc.COUNTER_MIDI_LO <= midi <= sc.COUNTER_MIDI_HI


def test_apply_symbolic_composition_injects_opium_melody(monkeypatch):
    monkeypatch.setenv("PLG_USE_SYMBOLIC", "1")
    pattern = {
        "bpm": 145,
        "style": "rage",
        "user_prompt": "dark opium trap",
        "tracks": {"kick": build_kick_pattern(bars=8)},
    }
    out = apply_symbolic_composition(pattern)
    melody = track_notes(out, "melody_lead")
    counter = track_notes(out, "counter_melody")
    assert melody
    assert counter
    assert out.get("plg_symbolic_opium_melody") is True
    assert isinstance(out.get("plg_key"), str) and out.get("plg_key")


def test_ensure_opium_melody_repairs_thin_trap_lead():
    pattern = {
        "bpm": 140,
        "style": "trap",
        "user_prompt": "dark beat",
        "tracks": {
            "kick": build_kick_pattern(bars=8),
            "melody_lead": [
                {"time_step": 0.0, "note": "E4", "length": 0.25, "velocity": 90},
                {"time_step": 2.0, "note": "G4", "length": 0.25, "velocity": 88},
            ],
        },
    }
    assert ensure_opium_melody_if_needed(pattern) is True
    melody = track_notes(pattern, "melody_lead")
    assert len(melody) >= 8
    sustained = [n for n in melody if float(n.get("length", 0)) >= 2.0]
    assert len(sustained) >= 4
    assert pattern.get("plg_melody_repaired") is True

"""Within-role character layer: vibe Plan A/B, BPM attack, velocity, pocket."""

from __future__ import annotations

from melody_character import (
    resolve_melody_character,
    resolve_vibe_plan_ab,
    score_character_bonus,
)


def _n(t: float, pitch: str, length: float, vel: int = 90) -> dict:
    return {"time_step": t, "note": pitch, "length": length, "velocity": vel}


def test_vibe_plan_a_tags():
    assert resolve_vibe_plan_ab(prompt="dark opium trap", style="") == "sweet_dark"
    assert resolve_vibe_plan_ab(prompt="rage distort", style="") == "rage"
    assert resolve_vibe_plan_ab(prompt="bright happy pop", style="") == "bright"


def test_vibe_plan_b_scale_and_dissonance():
    assert resolve_vibe_plan_ab(scale="natural_minor") == "sweet_dark"
    assert resolve_vibe_plan_ab(scale="major") == "bright"
    # Semitone clusters (E+F) → dirty → dark.
    notes = []
    for i in range(6):
        t = i * 2.0
        notes.append(_n(t, "E4", 1.5))
        notes.append(_n(t, "F4", 1.5))
        notes.append(_n(t, "B4", 1.5))
    assert resolve_vibe_plan_ab(notes=notes) == "sweet_dark"


def test_vibe_ignores_genre_lane_words_from_every_source():
    # 2026-08-13: on the live route the MIDI source names
    # appends pattern["style"] (always a lane name, e.g. "trap") to
    # source_names; the 2026-08-11 style-param exclusion missed that path,
    # so every generation was forced to sweet_dark (measured 10/10 corpus).
    # Lane names are not vibe evidence — a major-scale MIDI must reach the
    # bright slot even when "trap" rides in via source_names.
    assert (
        resolve_vibe_plan_ab(scale="major", source_names=["loop 01", "trap"])
        == "bright"
    )
    assert (
        resolve_vibe_plan_ab(scale="major", source_names=["loop 01", "rage"])
        == "bright"
    )
    # Real mood words in the same text survive the lane strip.
    assert (
        resolve_vibe_plan_ab(scale="major", source_names=["dark trap love"])
        == "sweet_dark"
    )
    # The prompt is free text and is NOT stripped: "rage" there is a mood
    # word, not a lane label.
    assert (
        resolve_vibe_plan_ab(scale="major", prompt="rage trap beat")
        == "rage"
    )
    # Underscored multi-word lanes strip too (west_coast is not a vibe token
    # anyway — this guards the normalizer).
    assert (
        resolve_vibe_plan_ab(scale="major", source_names=["loop", "west_coast"])
        == "bright"
    )


def test_fast_dense_wants_sharp_attack():
    notes = [_n(i * 0.25, "A4", 0.15, vel=100) for i in range(32)]
    ch = resolve_melody_character(notes, bpm=150.0)
    assert ch.tempo_band == "fast"
    assert ch.attack == "sharp"
    assert ch.vel_mode == "digital"


def test_slow_sparse_wants_lush():
    notes = [_n(i * 4.0, "E4", 3.5, vel=v) for i, v in enumerate([40, 70, 55, 90, 60, 85, 45, 95])]
    ch = resolve_melody_character(notes, bpm=85.0)
    assert ch.tempo_band == "slow"
    assert ch.attack == "lush"
    assert ch.vel_mode == "dynamic"


def test_pocket_collision_marks_muted():
    melody = []
    pad = []
    for i in range(8):
        t = i * 4.0
        for p in ("E4", "G4", "B4", "D5"):
            melody.append(_n(t, p, 3.5))
            pad.append(_n(t, p, 4.0))
    pattern = {"tracks": {"melody_lead": melody, "pad": pad}}
    ch = resolve_melody_character(melody, bpm=120.0, pattern=pattern)
    assert ch.pocket_muted is True


def test_character_bonus_prefers_sharp_over_lush_pad():
    from melody_character import MelodyCharacter

    ch = MelodyCharacter(
        tempo_band="fast",
        attack="sharp",
        vel_mode="digital",
        pocket_muted=False,
        dissonant=False,
        min_onset_gap=0.25,
    )
    sharp = score_character_bonus("mobile synth pluck synpluck anger sharp", ch, vibe="rage")
    lush = score_character_bonus("arksun cityscape creeper drone lush warm pad", ch, vibe="rage")
    assert sharp > lush

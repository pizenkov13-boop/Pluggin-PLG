"""Style corpus — the 808 must walk real chord changes, deterministically."""

from __future__ import annotations

from typing import Any

from music_theory import SCALES
from style_corpus import (
    BPM_RANGES,
    DRUM_CELLS,
    PROGRESSIONS,
    attach_progression,
    bpm_range,
    dna_addendum,
    dna_for_context,
    progression_roots_pc,
    style_dna,
)


def _note(t: float, name: str, length: float = 0.5, velocity: int = 110, **extra: Any) -> dict[str, Any]:
    out = {"time_step": t, "note": name, "length": length, "velocity": velocity}
    out.update(extra)
    return out


def test_style_dna_deterministic():
    a = style_dna("trap", key="F", scale="minor", seed=42)
    b = style_dna("trap", key="F", scale="minor", seed=42)
    assert a == b


def test_style_dna_varies_with_seed():
    picks = {
        style_dna("trap", key="F", scale="minor", seed=s)["progression"]["name"]
        for s in range(12)
    }
    assert len(picks) > 1


def test_progressions_diatonic_for_all_genres():
    for genre in PROGRESSIONS:
        dna = style_dna(genre, key="A", scale="natural_minor", seed=7)
        intervals = SCALES[dna["scale"]]
        scale_pcs = {(dna["root_pc"] + i) % 12 for i in intervals}
        prog = dna["progression"]
        assert len(prog["bar_degrees"]) == 8
        assert all(pc in scale_pcs for pc in prog["roots_pc"])
        for triad in prog["chord_tones_pc"]:
            assert all(pc in scale_pcs for pc in triad)


def test_progression_starts_on_tonic_for_dark_genres():
    for genre in ("trap", "opium", "rage", "drill", "phonk"):
        for seed in range(8):
            dna = style_dna(genre, key="C", scale="phrygian", seed=seed)
            assert dna["progression"]["roots_pc"][0] == dna["root_pc"]


def test_bpm_ranges_sane():
    for genre, (lo, hi) in BPM_RANGES.items():
        assert 60 <= lo < hi <= 210, genre
    assert bpm_range("unknown-genre") == BPM_RANGES["trap"]


def test_drum_cells_within_two_bars():
    for genre, cells in DRUM_CELLS.items():
        for cell in cells:
            assert all(0 <= t < 8 for t in cell["kick"]), (genre, cell["name"])
            assert all(0 <= t < 8 for t in cell["snare"]), (genre, cell["name"])


def test_dna_addendum_mentions_foundation():
    dna = style_dna("trap", key="F#", scale="minor", seed=3)
    text = dna_addendum(dna)
    assert "PLG STYLE DNA" in text
    assert dna["progression"]["name"] in text
    assert "F#" in text


def test_dna_for_context_matches_attach(monkeypatch):
    monkeypatch.delenv("PLG_USE_STYLE_CORPUS", raising=False)
    ctx = {"seed": 555, "key": "G", "scale": "minor"}
    prompt = "dark opium beat"
    dna = dna_for_context(ctx, prompt)

    pattern = {
        "user_prompt": prompt,
        "key": "G",
        "scale": "minor",
        "plg_variety": {"seed": 555, "key": "G", "scale": "minor"},
    }
    prog = attach_progression(pattern)
    assert prog is not None
    assert prog["name"] == dna["progression"]["name"]
    assert prog["roots_pc"] == dna["progression"]["roots_pc"]


def test_attach_progression_root_override():
    pattern = {
        "user_prompt": "drill",
        "plg_variety": {"seed": 5, "key": "C", "scale": "minor"},
    }
    prog = attach_progression(pattern, root_pc=6)  # F#
    assert prog is not None
    assert prog["roots_pc"][0] == 6  # drill progressions anchor bar 1 on the tonic
    assert progression_roots_pc(pattern) == prog["roots_pc"]


def test_attach_progression_respects_kill_switch(monkeypatch):
    monkeypatch.setenv("PLG_USE_STYLE_CORPUS", "0")
    pattern = {"user_prompt": "trap", "plg_variety": {"seed": 1}}
    assert attach_progression(pattern) is None
    assert "plg_progression" not in pattern


def test_generate_sub_808_walks_progression():
    from sub_808_gen import generate_sub_808
    from pattern_utils import parse_note_name

    roots = [5, 5, 8, 8, 0, 0, 3, 3]  # F F G# G# C C D# D#
    kick = [_note(bar * 4.0, "C5", 0.25, 116) for bar in range(8)]
    bass = generate_sub_808([], kick, bars=8, roots_pc=roots)
    # 2026-07 same-root merge: consecutive bars sharing a root (F+F, G#+G#,
    # C+C, D#+D#) no longer retrigger — each pair merges into ONE sustained
    # 2-bar note, so 8 one-bar kicks now produce 4 notes, not 8.
    assert len(bass) == 4
    for entry in bass:
        bar = int(entry["time_step"] // 4)
        assert parse_note_name(entry["note"]) % 12 == roots[bar]
        assert entry["length"] == 8.0  # spans the full 2-bar pair it merged


def test_generate_sub_808_retriggers_when_root_actually_changes():
    """The other half of the same fix: when the root DOES move every bar,
    nothing merges — one note per bar, exactly as before."""
    from sub_808_gen import generate_sub_808
    from pattern_utils import parse_note_name

    roots = [5, 8, 0, 3, 5, 8, 0, 3]  # never repeats back-to-back
    kick = [_note(bar * 4.0, "C5", 0.25, 116) for bar in range(8)]
    bass = generate_sub_808([], kick, bars=8, roots_pc=roots)
    assert len(bass) == 8
    for entry in bass:
        bar = int(entry["time_step"] // 4)
        assert parse_note_name(entry["note"]) % 12 == roots[bar]


def test_kick_locked_808_uses_pattern_progression():
    from symbolic_composer import compose_kick_locked_808
    from pattern_utils import parse_note_name

    roots = [9, 9, 4, 4, 7, 7, 2, 2]
    pattern = {
        "bpm": 140,
        "tracks": {
            "kick": [_note(bar * 4.0, "C5", 0.25, 116) for bar in range(8)],
            "melody_lead": [],
        },
        "plg_progression": {"roots_pc": roots},
    }
    bass = compose_kick_locked_808(pattern)
    assert bass
    for entry in bass:
        bar = int(entry["time_step"] // 4)
        assert parse_note_name(entry["note"]) % 12 == roots[bar]


def test_humanize_pins_progression_and_walks(monkeypatch):
    monkeypatch.delenv("PLG_USE_STYLE_CORPUS", raising=False)
    from beat_humanize import humanize_pattern
    from pattern_utils import parse_note_name

    melody = []
    for bar in range(8):
        melody.extend(
            [
                _note(bar * 4.0, "F4", 0.75, 100),
                _note(bar * 4.0 + 1.5, "G#4", 0.5, 96),
                _note(bar * 4.0 + 2.5, "C5", 0.5, 92),
                _note(bar * 4.0 + 3.25, "A#4", 0.5, 88),
            ]
        )
    pattern = {
        "bpm": 142,
        "style": "dark trap",
        "user_prompt": "dark trap 142",
        "key": "F",
        "scale": "minor",
        "plg_variety": {"seed": 777, "key": "F", "scale": "minor", "drum_variant": 1, "melody_variant": 2},
        "tracks": {
            "kick": [_note(b * 4.0, "C5", 0.25, 116) for b in range(8)]
            + [_note(b * 4.0 + 2.75, "C5", 0.25, 110) for b in range(8)],
            "snare": [_note(b * 4.0 + 2.0, "D5", 0.2, 106) for b in range(8)],
            "clap": [],
            "hi_hats": [_note(i * 0.5, "F#5", 0.1, 78) for i in range(64)],
            "sub_808": [],
            "melody_lead": melody,
        },
    }

    out = humanize_pattern(pattern)
    prog = out.get("plg_progression")
    assert isinstance(prog, dict) and prog.get("roots_pc")
    roots = [int(pc) % 12 for pc in prog["roots_pc"]]
    assert len(set(roots)) > 1  # the 808 actually WALKS, no single-pitch drone

    bass = [n for n in out["tracks"]["sub_808"] if not n.get("slide")]
    assert bass
    for entry in bass:
        bar = int(float(entry["time_step"]) // 4)
        assert parse_note_name(str(entry["note"])) % 12 == roots[bar % len(roots)]


def test_deterministic_fallback_uses_corpus(monkeypatch):
    monkeypatch.delenv("PLG_USE_STYLE_CORPUS", raising=False)
    from llm_pipeline import deterministic_pattern_from_skeleton
    from pattern_utils import parse_note_name

    skeleton = {"bpm": 140, "key": "F", "scale": "minor", "style": "dark trap"}
    pattern = deterministic_pattern_from_skeleton("dark trap", skeleton, reason="test", variety_seed=11)
    prog = pattern.get("plg_progression")
    assert isinstance(prog, dict) and len(prog["roots_pc"]) == 8
    for entry in pattern["tracks"]["sub_808"]:
        bar = int(float(entry["time_step"]) // 4)
        assert parse_note_name(str(entry["note"])) % 12 == prog["roots_pc"][bar % 8]


def test_build_context_uses_genre_bpm(monkeypatch):
    monkeypatch.delenv("PLG_USE_STYLE_CORPUS", raising=False)
    monkeypatch.setenv("PLG_LANES", "all")  # rnb lane — full router semantics
    from generation_variety import build_context

    for _ in range(4):
        drill = build_context("uk drill type beat", regenerate=True)
        assert 138 <= drill["bpm"] <= 148
        rnb = build_context("smooth rnb ballad", regenerate=True)
        assert 85 <= rnb["bpm"] <= 108


def test_best_of_variants_env(monkeypatch):
    from backend_core import _best_of_variants

    monkeypatch.delenv("PLG_BEST_OF_VARIANTS", raising=False)
    assert _best_of_variants() == 3
    monkeypatch.setenv("PLG_BEST_OF_VARIANTS", "1")
    assert _best_of_variants() == 1
    monkeypatch.setenv("PLG_BEST_OF_VARIANTS", "99")
    assert _best_of_variants() == 6
    monkeypatch.setenv("PLG_BEST_OF_VARIANTS", "junk")
    assert _best_of_variants() == 3

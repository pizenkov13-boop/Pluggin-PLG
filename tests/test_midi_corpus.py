"""MIDI corpus — the user's collection feeds melody/808/hats with variation."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import mido
import pytest

from midi_corpus import (
    ingest_from_corpus,
    key_from_name,
    pick_corpus_midi,
    scan_midi_corpus,
    vary_melody_notes,
)
from music_theory import SCALES
from pattern_utils import parse_note_name


def _write_mid(path: Path, notes: list[tuple[int, float, float, int]]) -> None:
    """notes: (midi_pitch, start_beat, length_beats, velocity)"""
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    events: list[tuple[int, mido.Message]] = []
    for pitch, start, length, vel in notes:
        events.append((int(start * 480), mido.Message("note_on", note=pitch, velocity=vel)))
        events.append((int((start + length) * 480), mido.Message("note_off", note=pitch)))
    events.sort(key=lambda pair: pair[0])
    cursor = 0
    for tick, msg in events:
        msg.time = tick - cursor
        cursor = tick
        track.append(msg)
    path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(path))


@pytest.fixture()
def library(tmp_path: Path) -> Path:
    root = tmp_path / "lib"
    # melodic file in C minor (name carries the key). A real moving hook —
    # ingest now rejects 1-2 pitch stubs (they are not melodies).
    _write_mid(
        root / "melodies" / "Night_Bells_-_Cmin_140.mid",
        [
            (72 + off, float(i), 0.75, 100 - i)
            for i, off in enumerate((0, 3, 7, 5, 2, 0, -2, 3))
        ]
        + [(75, 8.0, 1.5, 96)],
    )
    # deconstruction-kit style lanes
    _write_mid(
        root / "KIT" / "SONG 808 [ MIDI ].mid",
        [(29, 0.0, 1.5, 120), (31, 2.0, 1.0, 118), (29, 4.0, 2.0, 122), (34, 6.5, 1.0, 116)],
    )
    _write_mid(
        root / "KIT" / "SONG Hat [ MIDI ].mid",
        [(66, i * 0.5, 0.1, 70 + (i % 4) * 8) for i in range(16)],
    )
    return root


def test_scan_detects_lanes_and_keys(library: Path):
    entries = scan_midi_corpus(library)
    lanes = {e["name"]: e["lane"] for e in entries}
    assert lanes["Night_Bells_-_Cmin_140.mid"] == "melody"
    assert lanes["SONG 808 [ MIDI ].mid"] == "808"
    assert lanes["SONG Hat [ MIDI ].mid"] == "hats"
    melodic = next(e for e in entries if e["lane"] == "melody")
    assert melodic["key_pc"] == 0  # Cmin
    assert melodic["key_scale"] == "natural_minor"


def test_key_from_name_variants():
    assert key_from_name("Adieu - Detroit - Cmin 180 BPM") == (0, "natural_minor")
    assert key_from_name("groove F# minor") == (6, "natural_minor")
    assert key_from_name("sunny Abmaj lead") == (8, "major")
    assert key_from_name("no key here") is None


def test_bpm_from_name_variants():
    from midi_corpus import bpm_from_name

    assert bpm_from_name("ExamplePack_-_Anguish_-_130_BPM_G_Min") == 130.0
    assert bpm_from_name("Adieu - Detroit - Cmin 180 BPM") == 180.0
    assert bpm_from_name("loop_140bpm_dark") == 140.0
    assert bpm_from_name("no tempo here") is None


def test_pick_is_seeded_and_lane_scoped(library: Path):
    entries = scan_midi_corpus(library)
    rng_a = random.Random(1)
    rng_b = random.Random(1)
    pick_a = pick_corpus_midi(entries, "808", rng=rng_a)
    pick_b = pick_corpus_midi(entries, "808", rng=rng_b)
    assert pick_a == pick_b
    assert pick_a["lane"] == "808"


# --- variation ------------------------------------------------------------------


_ROOTS = [5, 5, 1, 1, 8, 8, 3, 3]
_TONES = [[5, 8, 0]] * 2 + [[1, 5, 8]] * 2 + [[8, 0, 3]] * 2 + [[3, 7, 10]] * 2


def _raw_notes() -> list[dict[str, Any]]:
    return [
        {"time_step": float(i), "note": f"C{5 + (i % 2)}", "length": 0.75, "velocity": 100}
        for i in range(16)
    ]


def test_variation_transposes_and_stays_in_scale():
    out = vary_melody_notes(
        _raw_notes(),
        random.Random(7),
        root_pc=5,  # F
        scale="natural_minor",
        roots_pc=_ROOTS,
        chord_tones_pc=_TONES,
        source_root_pc=0,  # C source
    )
    assert out
    scale_pcs = {(5 + i) % 12 for i in SCALES["natural_minor"]}
    for note in out:
        assert parse_note_name(str(note["note"])) % 12 in scale_pcs
        assert note["midi_varied"] is True


def test_variation_is_never_a_copy_and_seeded():
    src = _raw_notes()
    a = vary_melody_notes(src, random.Random(3), root_pc=0, scale="natural_minor")
    b = vary_melody_notes(src, random.Random(3), root_pc=0, scale="natural_minor")
    c = vary_melody_notes(src, random.Random(4), root_pc=0, scale="natural_minor")
    assert a == b  # deterministic per seed
    assert a != c or len(a) != len(c)  # different seed → different variation
    assert a != src  # never 1:1


# --- ingest ---------------------------------------------------------------------


def _pattern(prompt: str) -> dict[str, Any]:
    return {
        "bpm": 140,
        "style": "dark trap",
        "user_prompt": prompt,
        "plg_variety": {"seed": 21, "genre": "trap", "key": "F", "scale": "minor"},
        "tracks": {"melody_lead": [], "kick": [], "hi_hats": [], "sub_808": []},
    }


def test_ingest_uses_all_lanes_when_seed_allows(library: Path, monkeypatch):
    # force the probabilistic gates open so the test is deterministic
    monkeypatch.setattr("midi_corpus.random.Random.random", lambda self: 0.0)
    pattern = _pattern("night bells vibe")
    assert ingest_from_corpus(pattern, library_root=library, prompt="night bells vibe", style="trap")
    assert pattern["plg_midi_melody"] is True
    assert pattern["plg_midi_808"] is True
    assert pattern["plg_midi_hats"] is True
    assert set(pattern["plg_midi_corpus"]) == {"melody", "sub_808", "hi_hats"}
    assert all(n.get("midi_varied") for n in pattern["tracks"]["melody_lead"])
    assert all(n.get("sub_808_gen") for n in pattern["tracks"]["sub_808"])
    assert len(pattern["tracks"]["hi_hats"]) == 16
    # Scope boundary (2026-07 product decision): the corpus AUTO-pick tier
    # keeps its own vary_melody_notes development — melody-preservation is
    # only for explicit uploads (midi_ingest.ingest_uploaded_midi /
    # reference_reverse.ingest_reference_from_file), never set here.
    assert "plg_preserve_melody_sections" not in pattern


def test_ingest_respects_kill_switch(library: Path, monkeypatch):
    monkeypatch.setenv("PLG_MIDI_CORPUS", "off")
    pattern = _pattern("night bells vibe")
    assert not ingest_from_corpus(pattern, library_root=library, prompt="night bells", style="")


def test_melody_engines_respect_midi_melody(library: Path):
    from hook_composer import ensure_progression_melody_if_needed
    from symbolic_composer import ensure_opium_melody_if_needed

    pattern = _pattern("bells")
    pattern["plg_midi_melody"] = True
    pattern["tracks"]["melody_lead"] = [
        {"time_step": 0.0, "note": "C5", "length": 0.5, "velocity": 90, "midi": True}
    ]
    assert not ensure_opium_melody_if_needed(pattern)
    assert not ensure_progression_melody_if_needed(pattern, rng=random.Random(1))


def test_humanize_keeps_midi_hats_and_ships_one_808_lane(library: Path, monkeypatch):
    from beat_humanize import humanize_pattern

    monkeypatch.setattr("midi_corpus.random.Random.random", lambda self: 0.0)
    pattern = _pattern("night bells vibe")
    pattern["tracks"]["kick"] = [
        {"time_step": t, "note": "C1", "length": 0.3, "velocity": 112} for t in (0.0, 2.5, 4.0, 6.5)
    ]
    ingest_from_corpus(pattern, library_root=library, prompt="night bells vibe", style="trap")
    hat_grid_before = [round(float(n["time_step"]), 4) for n in pattern["tracks"]["hi_hats"]]
    out = humanize_pattern(pattern)
    hats_after = [n for n in out["tracks"]["hi_hats"] if not n.get("hat_roll")]
    assert [round(float(n["time_step"]), 4) for n in hats_after] == hat_grid_before
    # One 808 instrument, never two: a MIDI-owned beat ships 808_bass and the
    # legacy sub_808 lane is gone, so FL shows a single 808 channel.
    assert "sub_808" not in out["tracks"]
    assert out["tracks"]["808_bass"]
    assert out.get("plg_bass808_lane_active") is True


# --- PLG_MIDI as its own top-level sibling (2026-07) — merged in, never
# replacing the legacy library_root layout ---------------------------------


def test_scan_midi_corpus_merges_extra_root(tmp_path: Path, library: Path):
    """A separate PLG_MIDI folder's files must show up alongside whatever's
    still under the legacy library_root — merge, not replace."""
    extra_root = tmp_path / "PLG_MIDI"
    _write_mid(extra_root / "Sunset_Keys_-_Amaj_128.mid", [(69, 0.0, 1.0, 100), (73, 1.0, 1.0, 95)])

    entries = scan_midi_corpus(library, extra_root=extra_root)
    names = {e["name"] for e in entries}
    assert "Sunset_Keys_-_Amaj_128.mid" in names  # from the new top-level root
    assert "Night_Bells_-_Cmin_140.mid" in names  # legacy layout still found too
    new_entry = next(e for e in entries if e["name"] == "Sunset_Keys_-_Amaj_128.mid")
    assert new_entry["lane"] == "melody"
    assert new_entry["key_pc"] == 9  # Amaj


def test_scan_midi_corpus_extra_root_missing_is_harmless(library: Path, tmp_path: Path):
    entries = scan_midi_corpus(library, extra_root=tmp_path / "does_not_exist")
    assert {e["name"] for e in entries} >= {"Night_Bells_-_Cmin_140.mid"}


def test_ingest_from_corpus_uses_midi_root(tmp_path: Path, monkeypatch):
    """library_root has nothing at all — the only melody lives in the new
    top-level PLG_MIDI sibling, passed as midi_root."""
    monkeypatch.setattr("midi_corpus.random.Random.random", lambda self: 0.0)
    library_root = tmp_path / "library"
    library_root.mkdir()
    midi_root = tmp_path / "PLG_MIDI"
    _write_mid(
        midi_root / "Night_Bells_-_Cmin_140.mid",
        [(72 + off, float(i), 0.75, 100 - i) for i, off in enumerate((0, 3, 7, 5, 2, 0, -2, 3))]
        + [(75, 8.0, 1.5, 96)],
    )

    pattern = _pattern("night bells vibe")
    ok = ingest_from_corpus(
        pattern, library_root=library_root, prompt="night bells vibe", style="trap", midi_root=midi_root
    )
    assert ok is True
    assert pattern["plg_midi_melody"] is True

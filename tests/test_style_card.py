"""The write-only Style Card (style_card.py).

Phase 1 contract: the card is stamped, nobody reads it, and stamping it must
not change anything else on the pattern.
"""
from __future__ import annotations

import copy
import inspect
import json
import random

import pytest

import style_card
from style_card import STYLE_CARD_KEY, attach_style_card, build_style_card


def _notes(pitches, *, step=1.0):
    return [
        {"time_step": i * step, "note": p, "length": 0.5, "velocity": 90}
        for i, p in enumerate(pitches)
    ]


def _full_pattern():
    """A pattern as it looks AFTER humanize_pattern."""
    return {
        "bpm": 140.0,
        "tracks": {"melody_lead": _notes(["A3", "C4", "E4", "A4", "G4", "E4"])},
        "plg_midi_tempo_source": "midi_tempo",
        "plg_midi_passport": {
            "tempo": {"bpm": 140.0, "source": "midi_tempo"},
            "key": {"root_pc": 9, "scale": "natural_minor", "source": "note_analysis"},
            "role": {"musical_role": "melodic_phrase"},
            "safety": {"tempo_reliable": True, "key_reliable": True, "warnings": []},
        },
        "plg_progression": {"key": "A", "scale": "natural_minor", "roots_pc": [9, 5, 0, 7]},
        "plg_progression_root_source": "notes",
        "plg_melody_energy": {
            "busyness": 0.4,
            "lushness": 0.3,
            "pace": "driving",
            "onset_rate": 2.0,
            "attack": "sharp",
            "role": "pluck",
            "vibe": "dark",
            "coverage": 0.6,
            "median_len": 0.5,
            "section_density": [{"start": 0.0, "end": 16.0, "ratio": 1.1}],
        },
        "plg_drum_density": {"drive": 0.5, "pairs_per_bar": 1.8, "bars": 16},
        "plg_arrangement": [{"start_bar": 0, "bars": 8}, {"start_bar": 8, "bars": 8}],
    }


# --- shape ---------------------------------------------------------------


def test_full_pattern_yields_every_block_and_is_json_safe():
    card = build_style_card(_full_pattern())
    assert card["version"] == 1
    assert set(card) == {
        "version", "tempo", "key", "energy", "density", "mood",
        "role", "sections", "kit_target", "confidence",
    }
    assert card["tempo"]["bpm"] == 140.0 and card["tempo"]["feel"] == "mid"
    assert card["key"]["name"] == "A" and card["key"]["mode"] == "minor"
    assert card["key"]["source"] == "notes"
    assert card["density"]["pairs_per_bar"] == 1.8
    assert card["sections"]["count"] == 2
    assert card["role"]["melody_vibe"] == "dark"
    assert json.loads(json.dumps(card)) == card
    for value in card["mood"].values():
        assert 0.0 <= value <= 1.0


def test_dark_low_minor_reads_darker_than_bright_high_major():
    dark = _full_pattern()
    bright = _full_pattern()
    bright["tracks"]["melody_lead"] = _notes(["C6", "E6", "G6", "C6"])
    bright["plg_progression"] = {"key": "C", "scale": "major"}
    bright["plg_midi_passport"]["key"] = {"root_pc": 0, "scale": "major", "source": "note_analysis"}
    assert (
        build_style_card(dark)["mood"]["darkness"]
        > build_style_card(bright)["mood"]["darkness"]
    )
    assert build_style_card(bright)["mood"]["brightness"] > build_style_card(dark)["mood"]["brightness"]


# --- order of assembly -----------------------------------------------------


def test_key_follows_the_humanized_progression_not_the_passport():
    """After humanize the pinned progression is the answer; the passport is evidence."""
    pattern = _full_pattern()
    pattern["plg_progression"] = {"key": "D", "scale": "natural_minor"}
    key = build_style_card(pattern)["key"]
    assert key["root_pc"] == 2
    assert key["conflict"] is True  # the passport still says A


def test_card_degrades_to_passport_when_built_before_humanize():
    """Built too early (no progression, no density yet) it falls back, never fails."""
    pattern = _full_pattern()
    del pattern["plg_progression"], pattern["plg_progression_root_source"]
    del pattern["plg_drum_density"]
    card = build_style_card(pattern)
    assert card["key"]["root_pc"] == 9
    assert card["key"]["source"] == "note_analysis"
    assert card["density"] == {"drive": None, "pairs_per_bar": None, "bars": None}


def test_backend_stamps_after_every_humanize_call():
    import backend_core

    source = inspect.getsource(backend_core._humanize_and_ingest)
    calls = source.count("humanize_pattern(")
    wrapped = source.count("_stamp_style_card(humanize_pattern(")
    # one import line mentions the name without a call; every real call is wrapped
    assert wrapped == 2
    assert source.count("humanize_pattern(") - wrapped == 0
    assert calls == wrapped


def test_stamp_helper_stamps_the_returned_object_and_never_raises(monkeypatch):
    import backend_core

    returned = _full_pattern()
    assert backend_core._stamp_style_card(returned) is returned
    assert STYLE_CARD_KEY in returned

    def boom(_pattern):
        raise RuntimeError("card exploded")

    monkeypatch.setattr(style_card, "attach_style_card", boom)
    survivor = _full_pattern()
    assert backend_core._stamp_style_card(survivor) is survivor
    assert STYLE_CARD_KEY not in survivor


# --- empty MIDI --------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [
        {},
        {"bpm": 120.0},
        {"tracks": {"melody_lead": []}},
        {"tracks": {"melody_lead": [{"time_step": 0, "note": "???", "length": 1}]}},
        {"plg_melody_energy": "not a dict", "plg_midi_passport": None, "bpm": "fast"},
    ],
)
def test_empty_or_garbage_pattern_still_yields_a_neutral_card(pattern):
    card = build_style_card(pattern)
    assert json.loads(json.dumps(card)) == card
    assert card["confidence"]["mood"] == 0.0
    assert card["key"]["root_pc"] is None and card["key"]["reliable"] is False
    for value in card["mood"].values():
        assert 0.0 <= value <= 1.0
    assert card["kit_target"]["centroid"] in {"low", "mid", "high"}


def test_attach_on_empty_pattern_adds_only_its_own_key():
    pattern: dict = {}
    attach_style_card(pattern)
    assert set(pattern) == {STYLE_CARD_KEY}


# --- tempo / key conflicts -------------------------------------------------------


def test_tempo_conflict_between_pattern_and_passport_is_flagged():
    pattern = _full_pattern()
    pattern["bpm"] = 150.0  # passport says 140
    tempo = build_style_card(pattern)["tempo"]
    assert tempo["conflict"] is True
    assert tempo["reliable"] is False
    assert build_style_card(pattern)["confidence"]["tempo"] == pytest.approx(0.3)


def test_passport_warnings_mark_conflicts_even_when_values_agree():
    pattern = _full_pattern()
    pattern["plg_midi_passport"]["safety"]["warnings"] = [
        "tempo_conflict_embedded_vs_filename",
        "key_conflict_embedded_vs_filename",
    ]
    card = build_style_card(pattern)
    assert card["tempo"]["conflict"] and card["key"]["conflict"]
    assert card["confidence"]["tempo"] < 1.0 and card["confidence"]["key"] < 1.0


def test_clean_pattern_is_fully_confident_in_tempo_and_key():
    card = build_style_card(_full_pattern())
    assert card["tempo"]["conflict"] is False and card["key"]["conflict"] is False
    assert card["confidence"]["tempo"] == 1.0 and card["confidence"]["key"] == 1.0


# --- no RNG ------------------------------------------------------------------


def test_module_does_not_import_or_use_randomness():
    source = inspect.getsource(style_card)
    for needle in ("import random", "from random", "numpy.random", "secrets", "uuid", "time.time"):
        assert needle not in source


def test_card_is_identical_even_when_the_global_rng_is_poisoned(monkeypatch):
    first = build_style_card(_full_pattern())

    def forbidden(*_a, **_k):
        raise AssertionError("style card touched the RNG")

    monkeypatch.setattr(random, "random", forbidden)
    monkeypatch.setattr(random, "randint", forbidden)
    monkeypatch.setattr(random, "choice", forbidden)
    monkeypatch.setattr(random, "Random", forbidden)
    random.seed(1)
    second = build_style_card(_full_pattern())
    random.seed(2)
    third = build_style_card(_full_pattern())
    assert first == second == third


# --- the rest of the pattern is untouched -------------------------------------------


def test_attach_changes_nothing_but_its_own_key():
    pattern = _full_pattern()
    before = copy.deepcopy(pattern)
    card = attach_style_card(pattern)
    assert card is not None
    assert pattern.pop(STYLE_CARD_KEY) == card
    assert pattern == before


def test_card_does_not_alias_mutable_pattern_state():
    pattern = _full_pattern()
    card = attach_style_card(pattern)
    snapshot = copy.deepcopy(card)
    pattern["plg_melody_energy"]["busyness"] = 0.99
    pattern["plg_melody_energy"]["section_density"].append({"ratio": 9.0})
    pattern["plg_drum_density"]["drive"] = 0.99
    assert card == snapshot


def test_attach_is_idempotent_and_rebuilds_from_current_state():
    pattern = _full_pattern()
    first = attach_style_card(pattern)
    again = attach_style_card(pattern)
    assert first == again
    pattern["bpm"] = 90.0
    pattern["plg_midi_passport"]["tempo"]["bpm"] = 90.0
    assert attach_style_card(pattern)["tempo"]["feel"] == "slow"


def test_pattern_with_card_round_trips_through_the_output_json():
    """Open in FL reloads output_pattern.json with plain json.loads/dumps."""
    pattern = _full_pattern()
    attach_style_card(pattern)
    assert json.loads(json.dumps(pattern, ensure_ascii=False, indent=2)) == pattern


# --- one reader only ---------------------------------------------------------------


def test_only_the_kit_coherence_scorer_reads_the_card():
    """Phase 1 was write-only; Phase 3's scorer (behind PLG_KIT_COHERENCE) is the first reader."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    readers = []
    for path in root.glob("*.py"):
        if path.name in {"style_card.py", "backend_core.py", "kit_coherence.py"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "plg_style_card" in text or "style_card" in text:
            readers.append(path.name)
    assert readers == []

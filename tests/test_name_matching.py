"""The matcher must not read a keyword out of the middle of an ordinary word.

Scoring, the "avoid" penalty, the hard cross-type reject and lane identity all
matched keywords as SUBSTRINGS, so "That Clap" carried "hat" (avoid for a clap),
"Bride Kick" carried "ride", "Atom Kick" carried "tom", and "Phat Snare" was a
perfectly good HAT. Short keywords ("hat", "bd", "sd", "hh", "rim", "tom", "sub",
"low", "arp", "pad", "vox", ...) and a few longer ones ("ride", "lead", "bell",
"keys") now match whole words only. And a drum-machine kit puts "808" in every
name ("808 Snare", "TR-808 Clap"): a SHORT file that also carries its own lane
word is that drum, so its "808" is not an avoid marker (a long "Kick 808" is).
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from sample_match import (
    _is_whole_word_key,
    _kw_in,
    _words_of,
    score_candidate,
    track_candidate_rejection_reason,
)

RATE = 44100
PICKABLE = 5  # pick_best_for_track returns None below this


@pytest.fixture(autouse=True)
def _names_only(monkeypatch):
    """These tests are about the NAME layer. Since sound_type reads unlabelled
    files by their audio, a plain tone would be identified (or rejected) by its
    sound; the sound layer has its own tests (test_sound_type.py)."""
    monkeypatch.setenv("PLG_SOUND_TYPE", "off")


def _score(lane: str, rel: str, group: str = "kits", duration: float | None = None) -> int:
    return score_candidate(
        rel, lane, prompt_tokens=set(), style_tokens=set(), prompt_raw="", group=group, duration=duration
    )


def _tone(path: Path, seconds: float, freq: float = 200.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = b"".join(
        struct.pack("<h", int(18000 * math.exp(-i / RATE / 0.03) * math.sin(2 * math.pi * freq * i / RATE)))
        for i in range(int(RATE * seconds))
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)
    return path


# --- the word rule itself --------------------------------------------------------


@pytest.mark.parametrize("keyword", ["hat", "bd", "sd", "hh", "rim", "tom", "sub", "low", "arp", "pad", "vox", "ride", "lead", "bell", "keys"])
def test_risky_keywords_are_whole_word_only(keyword):
    assert _is_whole_word_key(keyword)


@pytest.mark.parametrize("keyword", ["808", "kick", "snare", "clap", "hihat", "hi_hat", "bass_drum", "subbass", "distort"])
def test_distinctive_keywords_stay_substring_matches(keyword):
    assert not _is_whole_word_key(keyword)


@pytest.mark.parametrize(
    ("keyword", "name", "hit"),
    [
        ("hat", "Chat 01.wav", False), ("hat", "What.wav", False), ("hat", "Phat Snare.wav", False),
        ("hat", "Whatever.wav", False), ("hat", "Hat01.wav", True), ("hat", "Hats 02.wav", True),
        ("hat", "ClosedHat.wav", True), ("hat", "Hi-Hat.wav", True),
        ("ride", "Bride Kick.wav", False), ("ride", "Override.wav", False), ("ride", "Ride 01.wav", True),
        ("tom", "Atom Kick.wav", False), ("tom", "Custom.wav", False), ("tom", "Toms 01.wav", True),
        ("sub", "Subtle.wav", False), ("sub", "Sub 01.wav", True),
        ("arp", "Sharp Snare.wav", False), ("arp", "Arp 01.wav", True),
        ("lead", "Leader Snare.wav", False), ("lead", "Lead 01.wav", True),
        ("rim", "Prime Snare.wav", False), ("rim", "Rim 01.wav", True),
        ("bd", "BD_01.wav", True), ("bd", "Abdul.wav", False),
        ("low", "Slow Kick.wav", False), ("low", "Low End.wav", True),
    ],
)
def test_whole_word_matching(keyword, name, hit):
    norm = name.lower().replace("-", "_").replace(" ", "_")
    assert _kw_in(keyword, norm, _words_of(name)) is hit


# --- names that used to score -14 (below the pick floor) ----------------------------


@pytest.mark.parametrize(
    ("lane", "name"),
    [
        ("kick", "Bride Kick.wav"), ("kick", "Atom Kick.wav"), ("kick", "Override Kick.wav"),
        ("snare", "Phat Snare.wav"), ("snare", "Whatever Snare.wav"), ("snare", "Sharp Snare.wav"),
        ("clap", "That Clap.wav"), ("clap", "Chatter Clap.wav"), ("clap", "Isabelle Clap.wav"),
    ],
)
def test_an_ordinary_word_inside_a_name_is_not_an_avoid_marker(lane, name):
    assert _score(lane, name) >= PICKABLE
    assert _score(lane, name) == _score(lane, {"kick": "Kick.wav", "snare": "Snare.wav", "clap": "Clap.wav"}[lane])


@pytest.mark.parametrize(
    ("lane", "name"),
    [("kick", "808 Kick 01.wav"), ("snare", "808 Snare 01.wav"), ("snare", "TR-808 Snare.wav"),
     ("clap", "808 Clap 01.wav"), ("hi_hats", "808 Hat 01.wav"), ("kick", "Kick 808.wav")],
)
def test_a_short_drum_machine_name_is_that_drum(lane, name):
    group = "hats" if lane == "hi_hats" else "kits"
    assert _score(lane, name, group, duration=0.15) >= PICKABLE


@pytest.mark.parametrize(("lane", "name"), [("kick", "Kick 808.wav"), ("snare", "Snare 808.wav"), ("clap", "808 Clap.wav")])
def test_a_long_or_unmeasured_file_keeps_the_808_penalty(lane, name):
    assert _score(lane, name, duration=1.2) < PICKABLE      # really an 808
    assert _score(lane, name, duration=None) < PICKABLE     # unknown length: stay conservative


def test_an_808_without_its_own_lane_word_is_still_rejected_for_a_drum_lane():
    assert _score("kick", "808 Bass 01.wav", duration=0.15) < PICKABLE
    assert _score("snare", "GHOUL 808.wav", duration=0.15) == -50_000  # hard cross-type reject unchanged


# --- lane identity -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("lane", "name", "reason"),
    [
        ("hi_hats", "Chat.wav", "missing_track_identity"),
        ("hi_hats", "What.wav", "missing_track_identity"),
        ("hi_hats", "Whatever.wav", "missing_track_identity"),
        ("kick", "Abdul.wav", "missing_track_identity"),
    ],
)
def test_a_word_that_merely_contains_the_keyword_has_no_identity(tmp_path, lane, name, reason):
    _tone(tmp_path / name, 0.06 if lane == "hi_hats" else 0.14)
    assert track_candidate_rejection_reason(tmp_path / name, lane, library_root=tmp_path) == reason


@pytest.mark.parametrize(
    ("lane", "rel", "seconds"),
    [
        ("hi_hats", "CH.wav", 0.06), ("hi_hats", "CH_01.wav", 0.06), ("hi_hats", "CHH 02.wav", 0.06),
        ("hi_hats", "808 Hat 01.wav", 0.06), ("hi_hats", "Hat Vol.2/04.wav", 0.06), ("hi_hats", "Hats Vol 2/05.wav", 0.06),
        ("kick", "KK 01.wav", 0.14), ("kick", "BD_01.wav", 0.14), ("kick", "Kick Vol.2/05.wav", 0.14),
        ("kick", "Bass Drums/06.wav", 0.14), ("kick", "Kick Pack 2/07.wav", 0.14),
    ],
)
def test_realistic_names_and_dedicated_folders_have_identity(tmp_path, lane, rel, seconds):
    _tone(tmp_path / rel, seconds)
    assert track_candidate_rejection_reason(tmp_path / rel, lane, library_root=tmp_path) is None


@pytest.mark.parametrize(("lane", "rel"), [("hi_hats", "Whatever Vol.1/01.wav"), ("hi_hats", "Chat Pack/02.wav"), ("kick", "808 Heavy Drum Kit/03.wav")])
def test_a_folder_that_is_not_dedicated_gives_no_identity(tmp_path, lane, rel):
    _tone(tmp_path / rel, 0.06 if lane == "hi_hats" else 0.14)
    # no identity of its own; a whole-kit folder that also says "808" is a cross-type conflict
    assert track_candidate_rejection_reason(tmp_path / rel, lane, library_root=tmp_path) in (
        "missing_track_identity",
        "name_or_path_conflicts_with_track",
    )


# --- nothing that used to work stops working -----------------------------------------


@pytest.mark.parametrize(
    ("lane", "name"),
    [("snare", "Rimshot.wav"), ("snare", "Rim 01.wav"), ("hi_hats", "Closed Hat 01.wav"), ("hi_hats", "Hi-Hat.wav"),
     ("hi_hats", "HH_01.wav"), ("clap", "Snap 01.wav"), ("snare_layer", "Rim.wav"), ("kick", "Kick.wav")],
)
def test_the_real_words_still_score(lane, name):
    group = "hats" if lane == "hi_hats" else "kits"
    assert _score(lane, name, group, duration=0.1) >= PICKABLE


def test_open_hats_and_rides_are_still_kept_off_the_hat_lane():
    assert _score("hi_hats", "Open Hat 01.wav", "hats") == -50_000
    assert _score("hi_hats", "Ride 01.wav", "hats") == -50_000
    assert _score("hi_hats", "Bride Hat.wav", "hats") > -50_000  # "ride" is not in Bride


def test_a_melody_named_like_a_word_that_contains_hat_is_not_a_drum():
    assert _score("melody_lead", "Hat Pad.wav", "melodies") == -50_000
    assert _score("melody_lead", "Chat Pad.wav", "melodies") > -50_000

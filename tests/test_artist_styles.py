"""Lollipopbeatz Juice WRLD video: guitar-lead + deep/boomy 808 preference.
Dirkey Lil Uzi Vert video: wider rage BPM floor (133) + complex-drums vibe.
Lauren Chief Keef video: new artist_style entry (epic pad/brass keywords).
Dirkey Destroy Lonely video: weird/unique synth-layer keywords.

Artist styles have no separate "sound selection" parameter slot — the vibe
string IS the mechanism (fed into the prompt via sanitize_artist_names, which
sample_match.py's keyword scoring then reads). So both requested touches are
just sharper vibe wording plus the two missing sub_808 keyword synonyms it
depends on.
"""

from __future__ import annotations

from artist_styles import resolve_artist_style, sanitize_artist_names
from genre_profiles import PROFILES
from sample_match import TRACK_PROFILES
from style_corpus import BPM_RANGES, bpm_range


def test_juice_wrld_vibe_leads_with_guitar_and_deep_808():
    hit = resolve_artist_style("juice wrld type beat")
    assert hit is not None
    assert hit["genre"] == "trap"
    assert "guitar" in hit["vibe"]
    assert "deep" in hit["vibe"] and "boomy" in hit["vibe"]


def test_sanitize_replaces_juice_wrld_with_vibe_text():
    out = sanitize_artist_names("make a juice wrld type beat")
    assert "juice wrld" not in out.lower()
    assert "guitar" in out
    assert "boomy" in out


def test_sub_808_recognizes_deep_and_boomy_keywords():
    strong = TRACK_PROFILES["sub_808"]["strong"]
    assert "deep" in strong
    assert "boomy" in strong


def test_lil_uzi_vert_resolves_to_rage_with_complex_drums_vibe():
    hit = resolve_artist_style("lil uzi vert type beat")
    assert hit is not None
    assert hit["genre"] == "rage"
    assert "complex" in hit["vibe"] or "busy" in hit["vibe"]


def test_rage_bpm_floor_widened_to_cover_133():
    lo, hi = bpm_range("rage")
    assert lo <= 133 <= hi
    assert BPM_RANGES["rage"][0] == 132  # explicit floor, not just incidentally in range


def test_rage_opts_into_wider_chord_randomization():
    # "no right or wrong, every beat was very experimental" — rage is the
    # first (and, as of this change, only) genre to enable either flag.
    profile = PROFILES["rage"]
    assert profile.chord_voicing_spread == 0.2
    assert profile.chord_section_variation is True
    for name, other in PROFILES.items():
        if name == "rage":
            continue
        assert other.chord_voicing_spread == 0.0, name
        assert other.chord_section_variation is False, name


def test_chief_keef_resolves_to_trap_with_epic_brass_vibe():
    hit = resolve_artist_style("chief keef type beat")
    assert hit is not None
    assert hit["genre"] == "trap"
    assert "epic" in hit["vibe"]
    assert "brass" in hit["vibe"]


def test_sanitize_replaces_chief_keef_with_vibe_text():
    out = sanitize_artist_names("make a chief keef type beat")
    assert "chief keef" not in out.lower()
    assert "brass" in out


def test_melody_and_chords_recognize_brass_and_epic_keywords():
    for lane in ("melody_lead", "chords"):
        strong = TRACK_PROFILES[lane]["strong"]
        assert "brass" in strong, lane
        assert "epic" in strong, lane


def test_destroy_lonely_vibe_mentions_weird_unique_layers():
    hit = resolve_artist_style("destroy lonely type beat")
    assert hit is not None
    assert hit["genre"] == "opium"
    assert "weird" in hit["vibe"]
    assert "unique" in hit["vibe"]


def test_melody_and_chords_recognize_weird_unique_keywords():
    for lane in ("melody_lead", "chords"):
        strong = TRACK_PROFILES[lane]["strong"]
        assert "weird" in strong, lane
        assert "unique" in strong, lane

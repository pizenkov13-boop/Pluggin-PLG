"""Hip-hop focus gate — the MVP router clamps every lane to the hip-hop family."""

from __future__ import annotations

import pytest

from genre_profiles import (
    HIPHOP_LANES,
    PROFILES,
    _FOCUS_FALLBACK,
    allowed_lanes,
    clamp_to_focus,
    detect_genre,
)


def test_default_focus_is_hiphop(monkeypatch):
    monkeypatch.delenv("PLG_LANES", raising=False)
    assert allowed_lanes() == HIPHOP_LANES


def test_all_lanes_escape_hatch(monkeypatch):
    monkeypatch.setenv("PLG_LANES", "all")
    assert allowed_lanes() == frozenset(PROFILES)
    assert clamp_to_focus("house") == "house"


def test_every_gated_lane_has_a_hiphop_fallback():
    gated = set(PROFILES) - HIPHOP_LANES
    missing = gated - set(_FOCUS_FALLBACK)
    assert not missing, f"gated lanes without fallback: {missing}"
    for target in _FOCUS_FALLBACK.values():
        assert target in HIPHOP_LANES


@pytest.mark.parametrize(
    ("prompt", "genre"),
    [
        # non-hip-hop asks land on the nearest hip-hop cousin
        ("deep house groove", "trap"),
        ("uplifting trance anthem", "trap"),
        ("epic cinematic trailer music", "trap"),
        ("catchy k-pop idol song", "trap"),
        ("radio pop anthem", "trap"),
        ("dark synthwave pop", "trap"),
        ("smooth rnb pocket", "trap"),
        ("pop rap club banger", "trap"),
        # 2026-08-11: HIPHOP_LANES was narrowed to DETAILED_GENRES, so the
        # untuned hip-hop cousins (lofi / boombap / jersey / toliver / kanye /
        # minimal_trap) now clamp too. They used to pass through and then get
        # refused by plg_api.create_beat's is_genre_detailed gate with
        # "Упс, этот жанр пока не доступен" — a detected genre could block
        # generation outright.
        ("motown soul ballad", "west_coast"),
        ("funky slap bass funk", "west_coast"),
        ("filthy dubstep wobble", "rage"),
        ("punk rock energy", "rage"),
        # tuned hip-hop lanes pass straight through
        ("dark trap banger", "rage"),
        ("opium type minimal", "opium"),
        ("uk drill sliding 808", "drill"),
        ("lofi study beat", "west_coast"),
        ("90s boom bap", "west_coast"),
        ("jersey club bounce", "drill"),
    ],
)
def test_detect_genre_clamps_to_hiphop(monkeypatch, prompt: str, genre: str):
    monkeypatch.delenv("PLG_LANES", raising=False)
    assert detect_genre("", prompt) == genre


def test_artist_references_clamp_to_hiphop(monkeypatch):
    monkeypatch.delenv("PLG_LANES", raising=False)
    # non-hip-hop artist asks → melodic trap under core focus
    assert detect_genre("", "the weeknd night drive") == "trap"
    assert detect_genre("", "martin garrix festival") == "trap"
    assert detect_genre("", "nicki minaj club banger") == "trap"
    # hip-hop artists unaffected
    assert detect_genre("", "playboi carti type beat") == "opium"
    assert detect_genre("", "central cee type beat") == "drill"


def test_genre_brain_menu_respects_focus(monkeypatch):
    import genre_brain

    monkeypatch.delenv("PLG_LANES", raising=False)
    menu = genre_brain.genre_brain_system()
    # 2026-08-11: the menu is now exactly DETAILED_GENRES. west_coast,
    # underground, detroit and pluggnb were missing from _LANES entirely, so
    # the brain could never route to them; boombap/lofi/jersey/toliver/kanye/
    # minimal_trap left the menu when HIPHOP_LANES narrowed to the tuned ten.
    from genre_profiles import DETAILED_GENRES

    for lane in DETAILED_GENRES:
        assert f"- {lane}:" in menu, lane
    assert "- boombap:" not in menu and "- lofi:" not in menu
    assert "- jersey:" not in menu and "- toliver:" not in menu
    assert "- house:" not in menu and "- kpop:" not in menu
    assert "- rnb:" not in menu and "- pop_rap:" not in menu

    monkeypatch.setenv("PLG_LANES", "all")
    full = genre_brain.genre_brain_system()
    assert "- house:" in full and "- cinematic:" in full and "- rnb:" in full

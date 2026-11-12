"""Any-genre engine — routing, honest drum grids, basslines, no crashes.

These tests exercise the FULL router (every lane), which stays supported under
``PLG_LANES=all``. The shipping default is the hip-hop focus gate — covered by
tests/test_lane_focus.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from genre_profiles import PROFILES, detect_genre
from pattern_utils import parse_note_name


@pytest.fixture(autouse=True)
def _all_lanes(monkeypatch):
    monkeypatch.setenv("PLG_LANES", "all")


def _note(t: float, name: str, length: float = 0.5, velocity: int = 100, **extra: Any) -> dict[str, Any]:
    out = {"time_step": t, "note": name, "length": length, "velocity": velocity}
    out.update(extra)
    return out


# --- Genre routing -----------------------------------------------------------

@pytest.mark.parametrize(
    ("prompt", "genre"),
    [
        ("deep house groove", "house"),
        ("тёмное берлинское techno", "techno"),
        ("uplifting trance anthem", "trance"),
        ("rawstyle hardstyle kick", "hardstyle"),
        ("liquid drum and bass", "dnb"),
        ("filthy dubstep wobble", "dubstep"),
        ("uk garage 2-step", "garage"),
        ("lofi study beat", "lofi"),
        ("90s boom bap", "boombap"),
        ("reggaeton dembow", "reggaeton"),
        ("dancehall riddim", "dancehall"),
        ("afrobeats summer vibe", "afrobeats"),
        ("amapiano log drum", "amapiano"),
        ("funky slap bass funk", "funk"),
        ("motown soul ballad", "soul"),
        ("punk rock energy", "rock"),
        ("epic cinematic trailer music", "cinematic"),
        ("гитарный рок бит", "rock"),
        ("бум бэп олдскул", "boombap"),
    ],
)
def test_detect_genre_routes_new_genres(prompt: str, genre: str):
    assert detect_genre("", prompt) == genre


def test_all_genres_have_corpus_banks():
    from style_corpus import BPM_RANGES, DRUM_CELLS, PROGRESSIONS, RECIPES

    for genre in PROFILES:
        assert genre in PROGRESSIONS, f"no progressions for {genre}"
        assert genre in BPM_RANGES, f"no bpm range for {genre}"
        assert genre in DRUM_CELLS, f"no drum cell for {genre}"
        assert genre in RECIPES, f"no recipe for {genre}"


# --- Honest drum grids -------------------------------------------------------

def _blueprint_pattern(genre: str) -> dict[str, Any]:
    pattern = {
        "bpm": 124,
        "style": genre,
        "user_prompt": f"{genre} beat",
        "tracks": {"kick": [], "snare": [], "clap": [], "hi_hats": []},
        "plg_arrangement": [
            {"name": "verse", "start_bar": 0, "bars": 2},
            {"name": "hook", "start_bar": 2, "bars": 2},
        ],
    }
    from drum_blueprint import apply_drum_blueprint

    apply_drum_blueprint(pattern, bars=4, genre=genre)
    return pattern


def _beats_in_bar(notes: list[dict[str, Any]], bar: int) -> set[float]:
    return {
        round(float(n["time_step"]) - bar * 4.0, 4)
        for n in notes
        if bar * 4.0 <= float(n["time_step"]) < (bar + 1) * 4.0
    }


def test_house_blueprint_is_four_on_floor():
    pattern = _blueprint_pattern("house")
    hook_kicks = _beats_in_bar(pattern["tracks"]["kick"], 2)
    assert {0.0, 1.0, 2.0, 3.0} <= hook_kicks
    snare_beats = _beats_in_bar(pattern["tracks"]["snare"], 2)
    assert {1.0, 3.0} <= snare_beats  # backbeat 2 & 4, not the trap step-9


def test_reggaeton_blueprint_is_dembow():
    pattern = _blueprint_pattern("reggaeton")
    snare_beats = _beats_in_bar(
        [n for n in pattern["tracks"]["snare"] if not n.get("ghost")], 2
    )
    assert snare_beats == {0.75, 1.5, 2.75, 3.5}


def test_boombap_blueprint_backbeat_two_and_four():
    pattern = _blueprint_pattern("boombap")
    snare_beats = _beats_in_bar(
        [n for n in pattern["tracks"]["snare"] if not n.get("ghost")], 0
    )
    assert snare_beats == {1.0, 3.0}


def test_dnb_blueprint_two_step_kick():
    pattern = _blueprint_pattern("dnb")
    verse_kicks = _beats_in_bar(pattern["tracks"]["kick"], 0)
    assert verse_kicks == {0.0, 2.5}


def test_trap_blueprint_unchanged():
    pattern = _blueprint_pattern("trap")
    verse_kicks = _beats_in_bar(pattern["tracks"]["kick"], 0)
    # "classic half-time": the downbeat carries bar 0. The 16th before the bar
    # turn (2.75) moved out of bar 0 when the drum RNG seed stopped mixing in a
    # synth preset name (2026-08-28, FLEX retirement) — the pocket cell is
    # unchanged, the seed that walks it is not.
    assert verse_kicks == {0.0}
    snare_beats = _beats_in_bar(pattern["tracks"]["snare"], 0)
    assert snare_beats == {2.0}


# --- Basslines for non-808 genres -------------------------------------------

def test_synthesize_bassline_walks_progression():
    from style_corpus import synthesize_bassline

    roots = [0, 5, 3, 7]
    notes = synthesize_bassline("house", roots, 4)
    assert notes, "house must synthesize an off-beat bassline"
    for entry in notes:
        bar = int(float(entry["time_step"]) // 4)
        assert parse_note_name(entry["note"]) % 12 == roots[bar]
        assert float(entry["time_step"]) % 1 == 0.5  # off-beat 8ths


def test_walk_bass_to_progression_moves_bars():
    from beat_humanize import walk_bass_to_progression

    bass = [_note(bar * 4.0, "C2", 3.5, 110) for bar in range(4)]
    out = walk_bass_to_progression(bass, [0, 5, 8, 10])
    pcs = [parse_note_name(n["note"]) % 12 for n in sorted(out, key=lambda n: n["time_step"])]
    assert pcs == [0, 5, 8, 10]


# --- Full humanize smoke: every genre must produce a playable beat -----------

@pytest.mark.parametrize("genre", sorted(PROFILES))
def test_humanize_smoke_every_genre(genre: str, monkeypatch):
    monkeypatch.delenv("PLG_USE_STYLE_CORPUS", raising=False)
    from beat_humanize import humanize_pattern

    melody = []
    for bar in range(4):
        melody.extend(
            [
                _note(bar * 4.0, "A4", 0.75, 100),
                _note(bar * 4.0 + 1.5, "C5", 0.5, 96),
                _note(bar * 4.0 + 2.5, "E5", 0.5, 92),
                _note(bar * 4.0 + 3.0, "D5", 0.5, 88),
            ]
        )
    pattern = {
        "bpm": 120,
        "style": genre,
        "user_prompt": f"{genre} beat",
        "key": "A",
        "plg_variety": {"seed": 4242, "key": "A", "scale": "minor", "drum_variant": 2, "melody_variant": 3},
        "tracks": {
            "kick": [_note(b * 4.0, "C5", 0.25, 116) for b in range(4)],
            "snare": [_note(b * 4.0 + 2.0, "D5", 0.2, 106) for b in range(4)],
            "clap": [],
            "hi_hats": [_note(i * 0.5, "F#5", 0.1, 78) for i in range(32)],
            "sub_808": [],
            "melody_lead": melody,
        },
    }

    out = humanize_pattern(pattern, profile=PROFILES[genre])
    tracks = out["tracks"]
    assert tracks.get("melody_lead"), genre
    profile = PROFILES[genre]
    if profile.eight08:
        assert tracks.get("sub_808"), f"{genre}: 808 lane must exist"
    else:
        from style_corpus import BASSLINE_CELLS

        if BASSLINE_CELLS.get(genre) and out.get("plg_progression"):
            # 2026-08-11: the synthesized genre bassline lands on `808_bass`,
            # the only permitted low-end lane. It used to be written to
            # `sub_808`, which midi_validate.sanitize_midi_mode deletes, so
            # every non-808 genre shipped with no bass and then failed
            # enforce_midi_mode with "required lane is empty: 808_bass".
            bass = tracks.get("808_bass") or []
            assert bass, f"{genre}: bassline must be synthesized from the progression"
            assert not tracks.get("sub_808"), f"{genre}: sub_808 is a retired lane"
            roots = [int(pc) % 12 for pc in out["plg_progression"]["roots_pc"]]
            for entry in bass:
                if entry.get("slide"):
                    continue
                bar = int(float(entry["time_step"]) // 4)
                assert parse_note_name(str(entry["note"])) % 12 == roots[bar % len(roots)], genre


# --- Artist "type beat" resolution ------------------------------------------

@pytest.mark.parametrize(
    ("prompt", "genre"),
    [
        ("nessa barrett type beat", "dark_pop"),
        ("бит как у дуа липы", "disco_pop"),
        ("ariana grande vibe", "pop"),
        ("nicki minaj club banger", "pop_rap"),
        ("тип бит как у ники минаж", "pop_rap"),
        ("the weeknd night drive", "dark_pop"),
        ("сделай как зе викенд", "dark_pop"),
        ("justin bieber smooth", "pop"),
        ("бит в стиле бибера", "pop"),
        ("playboi carti type beat", "opium"),
        ("как у карти", "opium"),
        ("martin garrix festival", "edm"),
        ("бит как у мартина гаррикса", "edm"),
        ("bad bunny party", "reggaeton"),
        ("skrillex drop", "dubstep"),
        ("hans zimmer epic", "cinematic"),
        ("billie eilish dark minimal", "dark_pop"),
    ],
)
def test_artist_type_beat_resolves_genre(prompt: str, genre: str):
    assert detect_genre("", prompt) == genre


def test_explicit_genre_word_beats_artist_reference():
    # user says drill + mentions a non-drill artist: the explicit genre wins
    assert detect_genre("", "drill beat like travis scott") == "drill"


def test_sanitize_artist_names_removes_names_keeps_vibe():
    from artist_styles import sanitize_artist_names

    out = sanitize_artist_names("dark beat like the weeknd and playboi carti")
    low = out.lower()
    assert "weeknd" not in low
    assert "carti" not in low
    assert "synth-pop" in low  # weeknd vibe descriptor injected
    assert "opium" in low  # carti vibe descriptor injected


def test_sanitize_handles_russian_inflections():
    from artist_styles import sanitize_artist_names

    out = sanitize_artist_names("сделай бит как у дуа липы и бибера")
    low = out.lower()
    assert "дуа лип" not in low
    assert "бибер" not in low
    assert "nu-disco" in low


def test_resolve_artist_no_false_positive_on_plain_words():
    from artist_styles import resolve_artist_style

    assert resolve_artist_style("dark trap with heavy 808 and fast hats") is None
    assert resolve_artist_style("сделай ремастер этого бита") is None


def test_build_context_bpm_for_new_genres(monkeypatch):
    monkeypatch.delenv("PLG_USE_STYLE_CORPUS", raising=False)
    from generation_variety import build_context

    house = build_context("deep house mix", regenerate=True)
    assert 120 <= house["bpm"] <= 126
    assert house["scale"] == "dorian"
    dnb = build_context("liquid drum and bass roller", regenerate=True)
    assert 170 <= dnb["bpm"] <= 176
    lofi = build_context("lofi study beat", regenerate=True)
    assert 72 <= lofi["bpm"] <= 92

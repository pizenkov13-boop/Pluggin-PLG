"""nightiger — second Hyperpop/Digicore source (after Felix), combined onto
the same "hyperpop" GenreProfile ("digicore" added as an alias keyword, not
a separate profile — the video itself treats them as one scene). Two
genuinely new, measurable rules ported and enabled on "hyperpop":

- melody drawn predominantly from root/5th/7th scale degrees (nightiger
  calls this "the most common progression in digicore") —
  apply_root_fifth_seventh_bias.
- sidechain depth depends on the bass TYPE: a long/sustained 808 gets the
  genre's full depth, a short bass-pluck gets a much shallower pump —
  bass_type_aware_sidechain_depth.
"""

from __future__ import annotations

from beat_humanize import apply_root_fifth_seventh_bias, bass_type_aware_sidechain_depth
from genre_profiles import GENRE_KEYWORDS, PROFILES, detect_genre


def test_hyperpop_has_the_two_new_flags():
    profile = PROFILES["hyperpop"]
    assert profile.melody_root_fifth_seventh_bias is True
    assert profile.sidechain_depth_bass_type_aware is True


def test_only_hyperpop_has_these_two_flags():
    for name, profile in PROFILES.items():
        if name == "hyperpop":
            continue
        assert profile.melody_root_fifth_seventh_bias is False, name
        assert profile.sidechain_depth_bass_type_aware is False, name


def test_digicore_is_a_hyperpop_alias_keyword():
    assert "digicore" in GENRE_KEYWORDS["hyperpop"]


def test_digicore_prompt_routes_to_hyperpop_under_full_lanes(monkeypatch):
    monkeypatch.setenv("PLG_LANES", "all")
    assert detect_genre("digicore type beat") == "hyperpop"


def test_root_fifth_seventh_bias_snaps_offdegree_notes():
    # root_pc=0 (C), natural_minor intervals (0,2,3,5,7,8,10) -> root=0, 5th(deg4)=7, 7th(deg6)=10
    notes = [
        {"time_step": 0.0, "note": "C4", "length": 0.5, "velocity": 90},   # already root (pc 0) - untouched
        {"time_step": 1.0, "note": "D4", "length": 0.5, "velocity": 90},   # pc 2, not in {0,7,10} -> snaps
    ]
    out = apply_root_fifth_seventh_bias(notes, root_pc=0, scale="natural_minor")
    assert out[0]["note"] == "C4"
    from pattern_utils import parse_note_name
    snapped_pc = parse_note_name(out[1]["note"]) % 12
    assert snapped_pc in (0, 7, 10)


def test_root_fifth_seventh_bias_noop_on_empty():
    assert apply_root_fifth_seventh_bias([], 0, "natural_minor") == []


def test_bass_type_aware_depth_shrinks_for_plucks():
    plucks = [{"time_step": float(i), "note": "C2", "length": 0.2, "velocity": 100} for i in range(4)]
    depth = bass_type_aware_sidechain_depth(plucks, base_depth=0.65)
    assert depth < 0.65
    assert depth == round(0.65 * 0.35, 3)


def test_bass_type_aware_depth_keeps_base_for_sustained_808():
    eight_oh_eight = [{"time_step": float(i * 4), "note": "C2", "length": 3.5, "velocity": 100} for i in range(4)]
    depth = bass_type_aware_sidechain_depth(eight_oh_eight, base_depth=0.65)
    assert depth == 0.65


def test_bass_type_aware_depth_noop_without_notes():
    assert bass_type_aware_sidechain_depth([], base_depth=0.65) == 0.65

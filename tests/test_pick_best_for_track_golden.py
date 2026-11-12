"""Golden test for the Phase 3 extraction of pick_best_for_track's scoring loop.

``sample_match.pick_best_for_track`` was split into ``_score_track_pool`` +
``_settle_pool`` + the unchanged rank/variety tail so the Kit Coherence Scorer
can read a whole ranked pool. The contract of that split:

* the SAME file and score come back for the same inputs, and
* the SAME number of random draws is consumed (``Random.getstate()`` matches),
  because ``kit_variety.pick_with_variety`` threads ONE rng through every lane —
  a lane that draws differently would shift every lane after it.

The reference is a frozen copy of the pre-refactor function
(tests/legacy_pick_best_for_track.py). Delete both once Phase 3 is accepted.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

import audio_features
import library_catalog
import sample_match
from kit_library_fixture import build_library
from legacy_pick_best_for_track import legacy_pick_best_for_track

SR = 44100
LANES = ("kick", "snare", "clap", "hi_hats", "808_bass", "sub_808", "snare_layer", "melody_lead")
SEEDS = range(12)


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    return build_library(tmp_path_factory.mktemp("golden_lib"))


@pytest.fixture(scope="module")
def catalog(library):
    return library_catalog.scan_library(library)


@pytest.fixture(scope="module", autouse=True)
def _private_feature_cache(tmp_path_factory):
    """Keep the user's real audio_features.json out of it — once per module, so
    each synthetic file is analysed once, not once per test."""
    patch = pytest.MonkeyPatch()
    patch.setattr(audio_features, "CACHE_FILE", tmp_path_factory.mktemp("af") / "audio_features.json")
    patch.setattr(audio_features, "_cache", None)
    yield
    patch.undo()


def _both(catalog, root, track, seed, **kwargs):
    """Run old and new with identically seeded rngs; return both results + states."""
    old_rng = None if seed is None else random.Random(seed)
    new_rng = None if seed is None else random.Random(seed)
    old = legacy_pick_best_for_track(catalog, track, root, variety_rng=old_rng, **kwargs)
    new = sample_match.pick_best_for_track(catalog, track, root, variety_rng=new_rng, **kwargs)
    old_state = None if old_rng is None else old_rng.getstate()
    new_state = None if new_rng is None else new_rng.getstate()
    return old, new, old_state, new_state


def _assert_identical(catalog, root, track, seed, **kwargs):
    old, new, old_state, new_state = _both(catalog, root, track, seed, **kwargs)
    assert new == old, f"{track} seed={seed} {kwargs}: {new} != {old}"
    assert new_state == old_state, f"{track} seed={seed}: random stream diverged"


def test_the_library_actually_offers_a_choice(catalog):
    """A golden test on a pool of one proves nothing."""
    for lane in ("kick", "snare", "clap", "hi_hats", "808_bass"):
        assert len(sample_match.ranked_pool_for_track(catalog, lane, Path(catalog["root"]))) >= 6, lane


# --- same file, same score, same random stream ------------------------------------


@pytest.mark.parametrize("track", LANES)
@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_pick_matches_the_frozen_original(catalog, library, track, seed):
    _assert_identical(catalog, library, track, seed)


@pytest.mark.parametrize("track", LANES)
def test_argmax_pick_without_an_rng_matches(catalog, library, track):
    _assert_identical(catalog, library, track, None)


@pytest.mark.parametrize("track", ("kick", "snare", "clap", "hi_hats", "808_bass", "melody_lead"))
@pytest.mark.parametrize("seed", range(3))
def test_every_optional_argument_still_flows_through(catalog, library, track, seed):
    pool = sample_match.ranked_pool_for_track(catalog, track, library)
    some_paths = {str(path.resolve()) for _score, path in pool[:3]}
    snare_ref = next(p for _s, p in sample_match.ranked_pool_for_track(catalog, "snare", library))
    energies = (
        {"busyness": 0.9, "lushness": 0.1, "attack": "sharp", "role": "pluck", "pace": "racing"},
        {"busyness": 0.1, "lushness": 0.9, "attack": "lush", "role": "thick_pad", "pace": "pocket"},
    )
    variants = [
        {"exclude": some_paths},
        {"bonus_keywords": ("tight", "snap"), "bury_keywords": ("boom", "dirty")},
        {"melody_energy": energies[seed % 2]},
        {"audio_target": {"centroid": "low"}, "use_clap": False},
        {"audio_target": {"attack": "low", "decay": "low"}, "audio_top_k": 3, "use_clap": False},
        {"role_reference": snare_ref},
        {"prompt": "dark hard punchy", "style": "trap", "use_clap": False},
        {"exclude": some_paths, "bonus_keywords": ("crisp",), "melody_energy": energies[0], "use_clap": False},
    ]
    for kwargs in variants:
        _assert_identical(catalog, library, track, seed, **kwargs)


def test_a_fully_excluded_pool_still_returns_the_same_nothing(catalog, library):
    everything = {
        str((library / rel).resolve())
        for rels in catalog["filtered_audio"].values()
        for rel in rels
    }
    for seed in (None, 3):
        _assert_identical(catalog, library, "kick", seed, exclude=everything)
    assert sample_match.pick_best_for_track(catalog, "kick", library, exclude=everything) == (None, -10_000)


def test_one_rng_threaded_through_a_whole_kit_stays_in_lockstep(catalog, library):
    """kit_variety passes ONE rng to seven lanes; a single extra draw would move every lane after it."""
    for seed in (0, 1, 2, 3, 99):
        old_rng, new_rng = random.Random(seed), random.Random(seed)
        old_kit, new_kit = [], []
        for lane in ("sub_808", "kick", "snare", "clap", "hi_hats", "melody_lead", "snare_layer"):
            old_kit.append(legacy_pick_best_for_track(catalog, lane, library, variety_rng=old_rng))
            new_kit.append(sample_match.pick_best_for_track(catalog, lane, library, variety_rng=new_rng))
            assert old_rng.getstate() == new_rng.getstate(), f"{lane} seed={seed}"
        assert old_kit == new_kit


def test_role_tier_fallback_is_unchanged(catalog, library):
    """Every clap 'collides' with its own reference → the role-vetoed tier is used."""
    only_claps = {
        "audio": {"claps": catalog["filtered_audio"].get("claps", [])},
        "filtered_audio": {"claps": catalog["filtered_audio"].get("claps", [])},
        "audio_total": len(catalog["filtered_audio"].get("claps", [])),
    }
    clap = next(iter(sample_match.ranked_pool_for_track(catalog, "clap", library)))[1]
    for seed in range(6):
        _assert_identical(only_claps, library, "clap", seed, role_reference=clap)


def test_the_comparison_would_catch_a_stray_random_draw(catalog, library, monkeypatch):
    """The tripwire must be able to fail: one extra draw has to show up."""
    real = sample_match._score_track_pool

    def draws_once_too_often(*args, **kwargs):
        if kwargs.get("variety_rng") is not None:
            kwargs["variety_rng"].random()
        return real(*args, **kwargs)

    monkeypatch.setattr(sample_match, "_score_track_pool", draws_once_too_often)
    _old, _new, old_state, new_state = _both(catalog, library, "snare", 4)
    assert old_state != new_state


def test_unknown_track_still_raises_the_same_way(catalog, library):
    with pytest.raises(KeyError):
        legacy_pick_best_for_track(catalog, "not_a_lane", library)
    with pytest.raises(KeyError):
        sample_match.pick_best_for_track(catalog, "not_a_lane", library)


# --- the same check on the real library, when this machine has one -------------------------


@pytest.mark.skipif(not (Path(__file__).resolve().parent.parent / "PLG_Sounds").is_dir(), reason="no dev library")
@pytest.mark.parametrize("track", ("kick", "snare", "clap", "hi_hats", "808_bass", "snare_layer", "melody_lead"))
def test_real_library_matches_on_many_seeds(track):
    root = Path(__file__).resolve().parent.parent / "PLG_Sounds"
    catalog = library_catalog.scan_library(root)
    for seed in range(5):
        _assert_identical(catalog, root, track, seed)


# --- ranked_pool_for_track ------------------------------------------------------------------


@pytest.mark.parametrize("track", ("kick", "snare", "clap", "hi_hats", "808_bass"))
def test_ranked_pool_head_is_the_argmax_pick(catalog, library, track):
    pool = sample_match.ranked_pool_for_track(catalog, track, library)
    path, score = sample_match.pick_best_for_track(catalog, track, library)
    assert (pool[0][1], pool[0][0]) == (path, score)


@pytest.mark.parametrize("track", ("kick", "snare", "clap", "hi_hats", "808_bass"))
def test_ranked_pool_is_sorted_unique_and_above_the_floor(catalog, library, track):
    pool = sample_match.ranked_pool_for_track(catalog, track, library)
    scores = [score for score, _p in pool]
    assert scores == sorted(scores, reverse=True)
    assert len({str(p.resolve()) for _s, p in pool}) == len(pool)
    assert min(scores) >= 5


@pytest.mark.parametrize("track", ("kick", "snare", "clap", "hi_hats", "808_bass"))
def test_every_seeded_pick_comes_from_the_ranked_pool(catalog, library, track):
    members = {str(p.resolve()) for _s, p in sample_match.ranked_pool_for_track(catalog, track, library)}
    for seed in SEEDS:
        path, _score = sample_match.pick_best_for_track(catalog, track, library, variety_rng=random.Random(seed))
        assert str(path.resolve()) in members


def test_ranked_pool_honours_exclude_and_never_lists_unreadable_files_first(catalog, library):
    full = sample_match.ranked_pool_for_track(catalog, "kick", library)
    banned = {str(full[0][1].resolve())}
    reduced = sample_match.ranked_pool_for_track(catalog, "kick", library, exclude=banned)
    assert [p for _s, p in reduced] == [p for _s, p in full[1:]]
    assert "broken" not in full[0][1].name.lower()


def test_ranked_pool_uses_no_randomness(catalog, library, monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("ranked_pool_for_track touched the RNG")

    for name in ("random", "randint", "choice", "choices", "shuffle", "uniform", "Random"):
        monkeypatch.setattr(random, name, forbidden)
    first = sample_match.ranked_pool_for_track(catalog, "snare", library)
    assert first == sample_match.ranked_pool_for_track(catalog, "snare", library)


def test_ranked_pool_of_an_empty_catalog_is_empty(library):
    empty = {"audio": {}, "filtered_audio": {}, "audio_total": 0}
    assert sample_match.ranked_pool_for_track(empty, "kick", library) == []

"""Kit Coherence Scorer (kit_coherence.py): the terms, the search, the contract.

The terms are tested on hand-built candidates (no audio), the search on a
deterministic synthetic library with real passports.
"""
from __future__ import annotations

import json
import random
import statistics
from pathlib import Path

import pytest

import audio_features
import kit_coherence as kc
import library_catalog
import sample_match
import sample_passport
from kit_library_fixture import build_library
from kit_coherence import Cand, Context, LanePool

W = kc.WEIGHTS


# --- hand-built kits --------------------------------------------------------------


def _ctx(**kwargs) -> Context:
    pools = {lane: LanePool(lane=lane, ranked=[], best=30) for lane in kc.COHERENT_LANES}
    return Context(bpm=kwargs.pop("bpm", 140.0), library_root=None, pools=pools, last_paths=set(), **kwargs)


def _cand(lane: str, name: str = "", **kwargs) -> Cand:
    kwargs.setdefault("base", 30)
    kwargs.setdefault("measured", True)
    return Cand(lane=lane, path=Path(f"/lib/{lane}/{name or lane}.wav"), **kwargs)


def _kit(*cands: Cand) -> dict[str, Cand]:
    return {c.lane: c for c in cands}


def _terms(ctx: Context, *cands: Cand) -> dict[str, float]:
    return ctx.score(_kit(*cands))[1]


def test_an_empty_kit_scores_nothing():
    assert _ctx().score({}) == (0.0, {})


def test_base_score_only_ever_subtracts():
    ctx = _ctx()
    assert _terms(ctx, _cand("kick", base=30)) == {}
    assert _terms(ctx, _cand("kick", base=10))["base"] == pytest.approx(-10.0)
    assert _terms(ctx, _cand("kick", base=50)).get("base", 0.0) > 0  # above the lane's best: never expected, but not hidden


def test_recent_and_unmeasured_samples_are_penalised():
    ctx = _ctx()
    assert _terms(ctx, _cand("kick", recent=True))["recent"] == -W["recent"]
    assert _terms(ctx, _cand("kick", measured=False))["unmeasured"] == -W["unmeasured"]


# --- pack ----------------------------------------------------------------------------------


def test_pack_rewards_one_set_over_one_pack_over_nothing():
    ctx = _ctx()
    same_set = _terms(ctx, _cand("kick", pack="p", pack_set="s"), _cand("snare", pack="p", pack_set="s"))
    same_pack = _terms(ctx, _cand("kick", pack="p", pack_set="s1"), _cand("snare", pack="p", pack_set="s2"))
    producer = _terms(ctx, _cand("kick", producer="x"), _cand("snare", producer="x"))
    none = _terms(ctx, _cand("kick", pack="p", pack_set="s1"), _cand("snare", pack="q", pack_set="s2"))
    assert same_set["pack"] == pytest.approx(W["pack"])
    assert same_pack["pack"] == pytest.approx(W["pack"] / 2)
    assert producer["pack"] == pytest.approx(W["pack"] / 2)
    assert "pack" not in none


def test_pack_is_a_share_of_pairs_and_ignores_the_snare_layer():
    ctx = _ctx()
    kit = _terms(
        ctx,
        _cand("kick", pack_set="s"), _cand("snare", pack_set="s"), _cand("clap", pack_set="other"),
        _cand("snare_layer", pack_set="zzz"),
    )
    assert kit["pack"] == pytest.approx(W["pack"] * 1 / 3)  # 3 pairs among kick/snare/clap, one matches


# --- brightness -----------------------------------------------------------------------------


def test_brightness_is_judged_against_the_style_card_target():
    dark_kit = [_cand(l, bright=0.15) for l in ("snare", "clap", "hi_hats")]
    bright_kit = [_cand(l, bright=0.9) for l in ("snare", "clap", "hi_hats")]
    low = _ctx(kit_target="low")
    assert _terms(low, *dark_kit)["bright_target"] > _terms(low, *bright_kit)["bright_target"]  # less negative
    high = _ctx(kit_target="high")
    assert _terms(high, *bright_kit)["bright_target"] > _terms(high, *dark_kit)["bright_target"]
    assert "bright_target" not in _terms(_ctx(), *dark_kit)  # no card, no target


def test_a_scattered_top_of_the_kit_costs_but_a_uniform_one_does_not():
    ctx = _ctx()
    scattered = _terms(ctx, _cand("snare", bright=0.05), _cand("clap", bright=0.95), _cand("hi_hats", bright=0.5))
    uniform = _terms(ctx, *[_cand(l, bright=0.7) for l in ("snare", "clap", "hi_hats")])
    assert scattered["bright_spread"] < 0
    assert "bright_spread" not in uniform
    assert "bright_spread" not in _terms(ctx, _cand("snare", bright=0.05), _cand("clap", bright=0.95))  # needs 3 lanes


def test_a_dark_808_under_bright_hats_is_not_punished_for_contrast():
    ctx = _ctx(kit_target="mid")
    contrast = _terms(ctx, _cand("808_bass", bright=0.0), _cand("kick", bright=0.1), _cand("hi_hats", bright=0.5))
    assert "bright_spread" not in contrast and "bright_target" not in {k for k, v in contrast.items() if v < -0.01}


# --- tails (tempo-relative) ----------------------------------------------------------------------


def test_a_hat_tail_longer_than_a_sixteenth_is_penalised_relative_to_tempo():
    sixteenth_140 = 15000.0 / 140
    ctx = _ctx(bpm=140)
    assert "tail_hat" not in _terms(ctx, _cand("hi_hats", t20=sixteenth_140 * 0.9))
    assert _terms(ctx, _cand("hi_hats", t20=sixteenth_140 * 2))["tail_hat"] == pytest.approx(-W["tail_hat"] / 2)
    assert _terms(ctx, _cand("hi_hats", t20=sixteenth_140 * 9))["tail_hat"] == pytest.approx(-W["tail_hat"])
    # the same 150 ms hat is fine at 90 BPM (a 16th is 167 ms) and too long at 170
    assert "tail_hat" not in _terms(_ctx(bpm=90), _cand("hi_hats", t20=150))
    assert _terms(_ctx(bpm=170), _cand("hi_hats", t20=150))["tail_hat"] < 0


def test_snare_and_clap_with_very_different_tails_cost_a_little():
    ctx = _ctx()
    assert "tail_pair" not in _terms(ctx, _cand("snare", t20=200), _cand("clap", t20=200))
    assert _terms(ctx, _cand("snare", t20=100), _cand("clap", t20=400))["tail_pair"] == pytest.approx(
        -W["tail_pair"] * (4 and 1.3863 / 1.5), rel=1e-2
    )


def test_a_kick_still_ringing_three_quarters_of_a_beat_later_is_penalised():
    beat = 60000.0 / 140
    ctx = _ctx()
    assert "tail_kick" not in _terms(ctx, _cand("kick", t40=0.7 * beat))
    assert _terms(ctx, _cand("kick", t40=0.75 * beat + beat))["tail_kick"] == pytest.approx(-W["tail_kick"])


# --- levels: only where the fader is fixed -----------------------------------------------------------


def test_levels_count_on_the_five_uncompensated_lanes_only():
    ctx = _ctx()
    for lane in kc.LEVEL_LANES:
        assert _terms(ctx, _cand(lane, level_delta=9.0))["level"] == pytest.approx(-W["level"])
    assert "level" not in _terms(ctx, _cand("808_bass", level_delta=20.0))  # flp_writer lifts the 808 per file
    assert "level" not in _terms(ctx, _cand("kick", level_delta=2.9))      # inside the 3 dB dead zone


def test_level_penalty_is_the_mean_over_the_lanes_present():
    ctx = _ctx()
    terms = _terms(ctx, _cand("kick", level_delta=9.0), _cand("snare", level_delta=0.0))
    assert terms["level"] == pytest.approx(-W["level"] / 2)


# --- role clashes ------------------------------------------------------------------------------------------


def test_snare_and_clap_sharing_a_role_is_a_heavy_penalty(monkeypatch):
    monkeypatch.setattr(audio_features, "roles_collide", lambda a, b: True)
    terms = _terms(_ctx(), _cand("snare"), _cand("clap"))
    assert terms["collide_backbeat"] == -W["collide_backbeat"]
    monkeypatch.setattr(audio_features, "roles_collide", lambda a, b: False)
    assert "collide_backbeat" not in _terms(_ctx(), _cand("snare"), _cand("clap"))


def test_a_layer_that_collapses_into_the_snare_costs_less(monkeypatch):
    monkeypatch.setattr(audio_features, "roles_collide", lambda a, b: True)
    terms = _terms(_ctx(), _cand("snare"), _cand("snare_layer"))
    assert terms["collide_layer"] == -W["collide_layer"] and W["collide_layer"] < W["collide_backbeat"]


def test_collision_checks_are_cached_per_pair(monkeypatch):
    calls = []
    monkeypatch.setattr(audio_features, "roles_collide", lambda a, b: calls.append(1) or False)
    ctx = _ctx()
    snare, clap = _cand("snare"), _cand("clap")
    for _ in range(5):
        ctx.score(_kit(snare, clap))
    assert len(calls) == 1


def test_a_pure_sub_kick_over_a_pure_sub_808_is_flagged():
    ctx = _ctx()
    assert _terms(ctx, _cand("kick", sub_share=0.9), _cand("808_bass", sub_share=0.95))["two_subs"] == -W["two_subs"]
    assert "two_subs" not in _terms(ctx, _cand("kick", sub_share=0.4), _cand("808_bass", sub_share=0.95))
    assert "two_subs" not in _terms(ctx, _cand("kick", sub_share=0.9, measured=False), _cand("808_bass", sub_share=0.95))


def test_one_lane_much_cleaner_or_dirtier_than_the_rest_is_penalised_with_a_cap():
    ctx = _ctx()
    assert "dirt" not in _terms(ctx, *[_cand(l, crest_delta=0.0) for l in ("808_bass", "kick", "snare")])
    mild = _terms(ctx, _cand("808_bass", crest_delta=0), _cand("kick", crest_delta=0), _cand("snare", crest_delta=3))["dirt"]
    huge = _terms(ctx, _cand("808_bass", crest_delta=0), _cand("kick", crest_delta=0), _cand("snare", crest_delta=40))["dirt"]
    assert -W["dirt"] < mild < 0 and huge == -W["dirt"]


# --- 808 root against the key, small on purpose --------------------------------------------------------------


def _key_ctx():
    return _ctx(key_root_pc=9, key_scale_pcs=frozenset({9, 11, 0, 2, 4, 5, 7}))  # A natural minor


def test_808_root_outside_the_scale_costs_a_little_and_the_tonic_earns_a_little():
    ctx = _key_ctx()
    assert _terms(ctx, _cand("808_bass", root_pc=10, root_trusted=True))["root_scale"] == -W["root_scale"]
    assert _terms(ctx, _cand("808_bass", root_pc=9, root_trusted=True))["root_tonic"] == W["root_tonic"]
    assert _terms(ctx, _cand("808_bass", root_pc=0, root_trusted=True)) == {}      # in scale, not tonic
    assert W["root_scale"] <= 6 and W["root_tonic"] <= 2


def test_an_untrusted_or_unknown_root_and_a_missing_key_are_neutral():
    assert _terms(_key_ctx(), _cand("808_bass", root_pc=10, root_trusted=False)) == {}
    assert _terms(_key_ctx(), _cand("808_bass", root_pc=None, root_trusted=True)) == {}
    assert _terms(_ctx(), _cand("808_bass", root_pc=10, root_trusted=True)) == {}


# --- helpers -----------------------------------------------------------------------------------------------------


def test_percentile_is_rank_based_and_handles_ties_and_empty():
    values = [1.0, 2.0, 3.0, 4.0]
    assert kc._percentile(values, 0.5) == 0.0
    assert kc._percentile(values, 5.0) == 1.0
    assert kc._percentile([2.0, 2.0], 2.0) == 0.5
    assert kc._percentile([], 3.0) == 0.5


def test_scale_pcs_from_the_style_card():
    root, pcs = kc._scale_pcs({"key": {"root_pc": 9, "scale": "natural_minor"}})
    assert root == 9 and pcs == frozenset({9, 11, 0, 2, 4, 5, 7})
    assert kc._scale_pcs({"key": {"root_pc": None, "scale": "natural_minor"}}) == (None, None)
    assert kc._scale_pcs({"key": {"root_pc": 1, "scale": "not_a_scale"}}) == (None, None)
    assert kc._scale_pcs({}) == (None, None)


@pytest.mark.parametrize("raw,expected", [(None, "on"), ("", "on"), ("  ", "on"), ("off", "off"), ("OFF", "off"),
                                          ("shadow", "shadow"), ("SHADOW", "shadow"), ("on", "on"), ("1", "on"),
                                          ("true", "on"), ("banana", "off"), ("0", "off")])
def test_mode_switch_parsing(monkeypatch, raw, expected):
    """Unset means the default (on); a value that is not understood means OFF, so a typo can never enable it."""
    if raw is None:
        monkeypatch.delenv(kc.MODE_ENV, raising=False)
    else:
        monkeypatch.setenv(kc.MODE_ENV, raw)
    assert kc.coherence_mode() == expected


def test_the_default_is_on_and_off_is_always_one_variable_away():
    assert kc.DEFAULT_MODE == "on"


def test_pack_identity_reads_the_kit_folder_not_the_instrument_word():
    root = Path("/lib")
    deep = root / "Drumkits" / "Travis_Scott_Kit" / "Deconstructed" / "12 BACC" / "Kick [ Rack ].wav"
    assert kc.pack_identity(deep, root) == ("travis_scott_kit", "12_bacc", "")
    assert kc.pack_identity(root / "Snare & Rim" / "Snare @producera.wav", root) == ("", "", "producera")
    assert kc.pack_identity(root / "imports" / "808" / "808.wav", root) == ("", "", "")
    assert kc.pack_identity(Path("/elsewhere/Kit A/Kick.wav"), root)[1] == "kit_a"  # outside the root: the nearest folders


def test_stratified_shortlist_spans_the_whole_brightness_range():
    ctx = _ctx()
    band = []
    for i in range(30):
        path = Path(f"/lib/snare/s{i:02d}.wav")
        ctx.passports[str(path.resolve())] = {
            "status": "ok",
            "features": {"centroid": 500 + 300 * i},
            "character": {"rolloff85_hz": 900 + 400 * i},
        }
        band.append((31, path))
    chosen = kc._stratified(band, ctx, 10)
    names = [p.name for _s, p in chosen]
    assert len(chosen) == 10 and names[0] == "s00.wav"           # the top-scoring one is always kept
    indexes = [int(n[1:3]) for n in names]
    assert max(indexes) == 29 and any(10 <= i <= 19 for i in indexes)  # brightest end and the middle are both in play
    assert len(set(names)) == 10
    assert kc._stratified(band[:6], ctx, 10) == band[:6]         # small bands pass through


# --- the search, on a real (synthetic) library --------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _private_caches(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(audio_features, "CACHE_FILE", tmp_path_factory.mktemp("af") / "audio_features.json")
    patch.setattr(audio_features, "_cache", None)
    patch.setattr(sample_passport, "PASSPORT_FILE", tmp_path_factory.mktemp("sp") / "sample_passports.json")
    sample_passport.reset_memory_cache()
    yield
    patch.undo()
    sample_passport.reset_memory_cache()


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    return build_library(tmp_path_factory.mktemp("kit_lib"))


@pytest.fixture(scope="module")
def catalog(library):
    return library_catalog.scan_library(library)


def _pattern(**extra):
    base = {
        "bpm": 140.0,
        "plg_style_card": {
            "key": {"root_pc": 9, "scale": "natural_minor"},
            "kit_target": {"centroid": "mid"},
        },
    }
    base.update(extra)
    return base


def _plan(catalog, library, seed=1, **kwargs):
    kwargs.setdefault("pattern", _pattern())
    kwargs.setdefault("melody_energy", None)
    return kc.plan_kit(catalog, library, seed=seed, **kwargs)


def test_a_plan_picks_a_distinct_file_for_every_lane(catalog, library):
    plan = _plan(catalog, library)
    assert set(plan.picks) == set(kc.COHERENT_LANES)
    assert len({str(p.resolve()) for p in plan.picks.values()}) == len(plan.picks)
    assert all(p.is_file() for p in plan.picks.values())
    assert plan.applied is False and plan.seed is not None


def test_every_pick_comes_from_its_lanes_ranked_pool(catalog, library):
    plan = _plan(catalog, library, seed=5)
    for lane, path in plan.picks.items():
        pool = {str(p.resolve()) for _s, p in sample_match.ranked_pool_for_track(catalog, lane, library)}
        assert str(path.resolve()) in pool, lane


def test_the_same_seed_gives_the_same_kit_and_other_seeds_can_differ(catalog, library):
    first = _plan(catalog, library, seed=11)
    again = _plan(catalog, library, seed=11)
    assert first.picks == again.picks and first.total == again.total
    kits = {tuple(sorted((l, p.name) for l, p in _plan(catalog, library, seed=s).picks.items())) for s in range(10)}
    assert len(kits) > 1


def test_unseeded_plans_are_still_valid(catalog, library):
    assert len(_plan(catalog, library, seed=None).picks) >= 3


def test_the_global_random_module_is_never_used(catalog, library, monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("kit_coherence used the global RNG")

    for name in ("random", "randint", "choice", "choices", "shuffle", "uniform", "sample", "randrange", "seed"):
        monkeypatch.setattr(random, name, forbidden)
    assert _plan(catalog, library, seed=3).picks


def test_the_genre_never_matters(catalog, library):
    a = _plan(catalog, library, seed=4, pattern=_pattern(plg_genre="trap", plg_variety={"genre": "trap"}))
    b = _plan(catalog, library, seed=4, pattern=_pattern(plg_genre="drill", plg_variety={"genre": "drill"}))
    assert a.picks == b.picks


def test_blocked_names_reserved_paths_and_recent_kits_are_respected(catalog, library):
    base = _plan(catalog, library, seed=2)
    victim = base.picks["snare"]
    blocked = _plan(catalog, library, seed=2, blocked_names={victim.name})
    assert blocked.picks["snare"].name != victim.name
    reserved = _plan(catalog, library, seed=2, reserved_paths={str(base.picks["hi_hats"].resolve())})
    assert str(reserved.picks["hi_hats"].resolve()) != str(base.picks["hi_hats"].resolve())
    recent = _plan(catalog, library, seed=2, last_paths={str(p.resolve()) for p in base.picks.values()})
    assert all(str(recent.picks[l].resolve()) != str(base.picks[l].resolve()) for l in base.picks)
    # a recent file's DUPLICATE under another folder is as recent as the original
    assert "recent" not in recent.terms


def test_the_808_gate_filters_the_shortlist_and_a_crashing_gate_admits_nothing(catalog, library):
    allowed = sorted(p.name for _s, p in sample_match.ranked_pool_for_track(catalog, "808_bass", library))[3]
    plan = _plan(catalog, library, gate_808=lambda p: p.name == allowed)
    assert plan.picks["808_bass"].name == allowed

    def crashing(_p):
        raise RuntimeError("gate exploded")

    no808 = _plan(catalog, library, gate_808=crashing)
    assert "808_bass" not in no808.picks and len(no808.picks) >= 3


def test_a_library_with_too_little_to_choose_from_yields_no_plan(library):
    empty = {"audio": {}, "filtered_audio": {}, "audio_total": 0}
    assert kc.plan_kit(empty, library, pattern=_pattern(), melody_energy=None, seed=1) is None
    only_kicks = {"audio": {"kits": []}, "filtered_audio": {}, "audio_total": 0}
    assert kc.plan_kit(only_kicks, library, pattern=_pattern(), melody_energy=None, seed=1) is None


def test_the_plan_beats_the_old_path_on_its_own_terms(catalog, library):
    """Circular by construction (the scorer judges its own metric) — it proves the
    search works, NOT that the result sounds better. That is what the listening
    pairs are for."""
    plan_totals, legacy_totals = [], []
    for seed in range(10):
        plan = _plan(catalog, library, seed=seed)
        plan_totals.append(plan.total)
        rng = random.Random(seed)
        used: set[str] = set()
        legacy = {}
        for lane in ("808_bass", "kick", "snare", "clap", "hi_hats", "snare_layer"):
            path, _score = sample_match.pick_best_for_track(catalog, lane, library, exclude=used, variety_rng=rng)
            if path is not None:
                legacy[lane] = path
                used.add(str(path.resolve()))
        legacy_totals.append(plan.context.evaluate(legacy)["total"])
    assert statistics.fmean(plan_totals) > statistics.fmean(legacy_totals)


def test_evaluate_scores_a_kit_somebody_else_chose(catalog, library):
    plan = _plan(catalog, library, seed=6)
    verdict = plan.context.evaluate(plan.picks)
    assert verdict["total"] == pytest.approx(plan.total, abs=0.01)
    assert plan.context.evaluate({"melody_lead": plan.picks["kick"]}) == {"total": 0.0, "terms": {}}  # not a coherence lane


def test_telemetry_is_json_safe_and_carries_both_kits(catalog, library):
    plan = _plan(catalog, library, seed=7)
    legacy = {"kick": plan.picks["snare"], "snare": plan.picks["kick"]}
    data = plan.telemetry(mode="shadow", legacy=legacy)
    assert json.loads(json.dumps(data)) == data
    assert data["mode"] == "shadow" and data["applied"] is False
    assert set(data["proposal"]["picks"]) == set(plan.picks)
    assert data["legacy"]["names"] == {"kick": plan.picks["snare"].name, "snare": plan.picks["kick"].name}
    assert len(data["alternatives"]) <= 3 and "total" in data["legacy"]
    assert "legacy" not in plan.telemetry(mode="on")


def test_planning_does_not_touch_the_library(catalog, library):
    before = {p: p.read_bytes() for p in library.rglob("*.wav")}
    _plan(catalog, library, seed=8)
    assert {p: p.read_bytes() for p in library.rglob("*.wav")} == before


def test_planning_persists_new_passports_once(catalog, library, tmp_path, monkeypatch):
    monkeypatch.setattr(sample_passport, "PASSPORT_FILE", tmp_path / "fresh" / "sp.json")
    sample_passport.reset_memory_cache()
    writes = []
    real = audio_features.atomic_write_text
    monkeypatch.setattr(audio_features, "atomic_write_text", lambda p, t: (writes.append(p), real(p, t))[1])
    _plan(catalog, library, seed=9)
    assert (tmp_path / "fresh" / "sp.json").is_file()
    assert 1 <= len(writes) <= 12   # one flush, plus one per 808 root (fill_root persists), never one per sample


def _runaway_winner(monkeypatch):
    """Both clap and snare_layer get the SAME file lifted 60 points above everything else."""
    real = sample_match.ranked_pool_for_track
    shared = {}

    def biased(cat, lane, root, **kwargs):
        ranked = real(cat, lane, root, **kwargs)
        if lane in ("clap", "snare_layer") and len(ranked) > 5:
            winner = shared.setdefault("path", ranked[0][1])
            rest = [(s, p) for s, p in ranked if p != winner][:8]
            return [(ranked[0][0] + 60, winner)] + rest
        return ranked

    monkeypatch.setattr(sample_match, "ranked_pool_for_track", biased)


def test_a_strong_bias_that_leaves_two_lanes_one_shared_candidate_still_plans(catalog, library, monkeypatch):
    """Regression (found on the user's own PLG_Sounds under a racing melody): the keyword bias lifted ONE
    file more than BAND above the rest of both the clap and snare-layer pools, so the shortlist of each was
    that same file and the search found no kit. The band now never drops below MIN_BAND candidates."""
    _runaway_winner(monkeypatch)
    plan = _plan(catalog, library, seed=3)
    assert plan is not None
    assert plan.candidates["clap"] >= kc.MIN_BAND and plan.candidates["snare_layer"] >= kc.MIN_BAND
    assert plan.picks["clap"] != plan.picks["snare_layer"]
    assert set(plan.picks) == set(kc.COHERENT_LANES)


def test_the_same_setup_without_the_minimum_band_reproduces_the_failure(catalog, library, monkeypatch):
    """Proves the regression test above is not vacuous."""
    _runaway_winner(monkeypatch)
    monkeypatch.setattr(kc, "MIN_BAND", 1)
    assert _plan(catalog, library, seed=3) is None


def test_a_lane_that_cannot_be_filled_means_no_plan_not_a_half_kit(catalog, library, monkeypatch):
    real = sample_match.ranked_pool_for_track

    def one_file_for_two_lanes(cat, lane, root, **kwargs):
        ranked = real(cat, lane, root, **kwargs)
        if lane in ("clap", "snare_layer"):
            only = real(cat, "clap", root, **kwargs)[:1]
            return only
        return ranked

    monkeypatch.setattr(sample_match, "ranked_pool_for_track", one_file_for_two_lanes)
    assert _plan(catalog, library, seed=3) is None

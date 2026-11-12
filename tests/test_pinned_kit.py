""""Random from library": the user's own files only, pinned until Random or Auto."""

import json
import random
from pathlib import Path

import pytest

import pinned_kit
import plg_api
import sample_match
import starter_kit


LANE_WORDS = {"808_bass": "808", "kick": "kick", "snare": "snare", "snare_layer": "rim", "clap": "clap", "hi_hats": "hat"}


def _library(tmp_path: Path, names: list[str]) -> tuple[Path, dict]:
    root = tmp_path / "PLG_Sounds"
    for name in names:
        (root / "kits").mkdir(parents=True, exist_ok=True)
        (root / "kits" / name).write_bytes(b"RIFF" + b"0" * 64)
    catalog = {"root": str(root), "audio_total": len(names), "audio": {"kits": [f"kits/{n}" for n in names]}}
    return root, catalog


@pytest.fixture(autouse=True)
def _pin_store(tmp_path, monkeypatch):
    monkeypatch.setattr(pinned_kit, "pin_file", lambda: tmp_path / "state" / "pinned_kit.json")

    def judge(path, lane, **_kw):
        return [] if LANE_WORDS[lane] in path.stem.lower() else ["missing_track_identity"]

    def score(rel, lane, **_kw):
        return 30 if LANE_WORDS[lane] in Path(rel).stem.lower() else 0

    monkeypatch.setattr(sample_match, "track_candidate_rejection_reasons", judge)
    monkeypatch.setattr(sample_match, "score_candidate", score)


ALL = [f"{w}_{i}.wav" for w in ("808", "kick", "snare", "rim", "clap", "hat") for i in (1, 2, 3)]


def test_every_pick_is_a_usable_file_of_the_users_library_for_its_own_lane(tmp_path):
    root, catalog = _library(tmp_path, ALL)

    picks, without = pinned_kit.pick_random_kit(catalog, root, rng=random.Random(1))

    assert without == [] and set(picks) == set(pinned_kit.PIN_LANES)
    for lane, path in picks.items():
        assert LANE_WORDS[lane] in path.stem and path.parent == (root / "kits").resolve()
    assert len({str(p) for p in picks.values()}) == len(picks), "one file is never used for two lanes"


def test_a_file_whose_name_says_nothing_about_the_lane_is_never_drawn(tmp_path, monkeypatch):
    root, catalog = _library(tmp_path, ["clap_1.wav", "evolution_fx_roll.wav"])
    # the export gate lets the FX file through for the clap lane (no identity rule) ...
    monkeypatch.setattr(sample_match, "track_candidate_rejection_reasons", lambda *a, **k: [])

    for seed in range(15):
        picks, _ = pinned_kit.pick_random_kit(catalog, root, rng=random.Random(seed))
        assert picks["clap"].name == "clap_1.wav"  # ... but a name without the word scores under the floor


def test_a_lane_without_a_usable_file_is_reported_and_left_out(tmp_path):
    root, catalog = _library(tmp_path, [n for n in ALL if not n.startswith("snare")])

    picks, without = pinned_kit.pick_random_kit(catalog, root, rng=random.Random(2))

    assert without == ["snare"] and "snare" not in picks and len(picks) == 5


def test_a_second_draw_changes_a_lane_that_has_alternatives(tmp_path):
    root, catalog = _library(tmp_path, ALL)
    first, _ = pinned_kit.pick_random_kit(catalog, root, rng=random.Random(3))

    for seed in range(20):
        again, _ = pinned_kit.pick_random_kit(catalog, root, previous=first, rng=random.Random(seed))
        assert all(again[lane] != first[lane] for lane in first)


def test_the_only_usable_file_may_be_drawn_again(tmp_path):
    root, catalog = _library(tmp_path, ["kick_1.wav"])
    first, _ = pinned_kit.pick_random_kit(catalog, root, rng=random.Random(4))

    again, without = pinned_kit.pick_random_kit(catalog, root, previous=first, rng=random.Random(5))

    assert again["kick"] == first["kick"] and "kick" not in without


def test_pins_that_left_the_library_or_vanished_are_ignored(tmp_path):
    root, catalog = _library(tmp_path, ["kick_1.wav"])
    inside = (root / "kits" / "kick_1.wav").resolve()
    outside = tmp_path / "assets" / "starter" / "PLG_starter_kick.wav"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"RIFF0")
    pinned_kit.save_pins({"kick": str(inside), "snare": str(outside), "clap": str(root / "kits" / "gone.wav")})

    assert pinned_kit.valid_pins(root) == {"kick": inside}
    assert pinned_kit.pinned_names(root) == {"kick": "kick_1.wav"}


def test_clear_removes_the_pins_and_is_safe_twice(tmp_path):
    pinned_kit.save_pins({"kick": "x.wav"})
    pinned_kit.clear_pins()
    pinned_kit.clear_pins()

    assert pinned_kit.load_pins() == {}


# --- starter_kit: pinned lanes are left to the user ------------------------------


def test_kit_coherence_skips_the_pinned_lanes_and_reserves_their_files(tmp_path, monkeypatch):
    import kit_coherence

    seen = {}

    def fake_plan(catalog, root, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(kit_coherence, "plan_kit", fake_plan)
    monkeypatch.setattr(kit_coherence, "coherence_mode", lambda: "on")
    pin = tmp_path / "kick_1.wav"
    pin.write_bytes(b"RIFF0")
    profile = type("P", (), {"bass808_enabled": False})()

    starter_kit._kit_coherence_stage(
        {}, {"audio_total": 3}, tmp_path, {"kick": pin}, None, profile, True, {"kick": pin.resolve()}
    )

    assert "kick" not in seen["lanes"] and "snare" in seen["lanes"]
    assert str(pin.resolve()) in seen["reserved_paths"]


def test_kit_coherence_does_nothing_when_every_lane_is_pinned(tmp_path, monkeypatch):
    import kit_coherence

    monkeypatch.setattr(kit_coherence, "coherence_mode", lambda: "on")
    monkeypatch.setattr(kit_coherence, "plan_kit", lambda *a, **k: pytest.fail("must not search"))
    pinned = {lane: tmp_path / f"{lane}.wav" for lane in kit_coherence.COHERENT_LANES}
    profile = type("P", (), {"bass808_enabled": True})()

    result = starter_kit._kit_coherence_stage({}, {"audio_total": 3}, tmp_path, {}, None, profile, True, pinned)

    assert result["plan"] is None and "pinned" in result["note"]


# --- plg_api ---------------------------------------------------------------------


@pytest.fixture
def api(tmp_path, monkeypatch):
    root, catalog = _library(tmp_path, ALL)
    catalog_file = tmp_path / "sample_catalog.json"
    catalog_file.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr(plg_api, "CATALOG_FILE", catalog_file)
    monkeypatch.setattr(plg_api, "get_samples_dir", lambda: root)
    monkeypatch.setattr(plg_api, "_jobs", {})
    monkeypatch.setattr(plg_api, "_material_import_roots", {})
    import library_paths

    monkeypatch.setattr(library_paths, "DEFAULT_LIBRARY_DIR", root)
    monkeypatch.setattr(library_paths, "LEGACY_LIBRARY_DIR", tmp_path / "PLG_Library")
    return root


def test_random_kit_pins_one_file_per_lane_and_the_status_shows_them(api):
    result = plg_api.random_kit()

    assert result["ok"] is True and result["kept"] == [] and set(result["picked"]) == set(pinned_kit.PIN_LANES)
    assert set(plg_api._pinned_kit_names()) == set(pinned_kit.PIN_LANES)
    assert result["pinned"] == plg_api._pinned_kit_names()


def test_a_lane_without_a_file_keeps_its_earlier_pin(api):
    plg_api.random_kit()
    before = plg_api._pinned_kit_names()["snare"]
    catalog = json.loads(plg_api.CATALOG_FILE.read_text(encoding="utf-8"))
    catalog["audio"]["kits"] = [rel for rel in catalog["audio"]["kits"] if "snare" not in rel]
    plg_api.CATALOG_FILE.write_text(json.dumps(catalog), encoding="utf-8")

    result = plg_api.random_kit()

    assert result["kept"] == ["snare"] and plg_api._pinned_kit_names()["snare"] == before


def test_clear_pinned_kit_goes_back_to_automatic(api):
    plg_api.random_kit()

    assert plg_api.clear_pinned_kit() == {"ok": True}
    assert plg_api._pinned_kit_names() == {}


def test_random_kit_is_refused_while_a_job_runs_and_without_a_library(api):
    plg_api._jobs["j"] = plg_api._Job(id="j", kind="beat")
    assert plg_api.random_kit()["error_type"] == "busy"
    plg_api._jobs.clear()

    plg_api.CATALOG_FILE.write_text(json.dumps({"root": str(api), "audio_total": 0}), encoding="utf-8")
    assert plg_api.random_kit()["error_type"] == "no_library"
    assert plg_api._pinned_kit_names() == {}


def test_the_beat_result_names_the_lanes_where_the_pin_was_swapped():
    assert plg_api._pinned_replaced_lanes({"plg_pinned_kit": {"replaced": ["808_bass"]}}) == ["808_bass"]
    assert plg_api._pinned_replaced_lanes({}) == []


# --- the sound browser: cells chosen by hand ("explicit") --------------------------------


def _raw(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _real_wav(path: Path) -> Path:
    import struct
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(struct.pack("<h", 0) * 400)
    return path


def test_the_pin_file_stays_version_1_and_explicit_is_additive(tmp_path):
    pinned_kit.save_pins({"kick": str(tmp_path / "a.wav")})
    assert json.loads(pinned_kit.pin_file().read_text(encoding="utf-8")) == {
        "version": 1, "picks": {"kick": str(tmp_path / "a.wav")},
    }
    assert pinned_kit.load_explicit() == set()
    pinned_kit.save_pins({"kick": "k", "snare": "s"}, explicit={"snare", "clap"})  # clap has no pin: dropped
    assert json.loads(pinned_kit.pin_file().read_text(encoding="utf-8"))["explicit"] == ["snare"]
    assert pinned_kit.load_explicit() == {"snare"}


def test_the_layer_is_a_cell_like_the_others():
    assert pinned_kit.PIN_LANES == ("808_bass", "kick", "snare", "snare_layer", "clap", "hi_hats")


def test_a_hand_pick_is_taken_even_when_the_name_and_the_sound_say_another_drum(tmp_path):
    root = tmp_path / "PLG_Sounds"
    kick_like = _real_wav(root / "kits" / "kick_9.wav")

    assert pinned_kit.pin_by_hand("hi_hats", kick_like, root) is None  # a kick in the hat cell: the user's call

    assert pinned_kit.valid_pins(root) == {"hi_hats": kick_like.resolve()}
    assert pinned_kit.valid_explicit(root) == {"hi_hats"}


@pytest.mark.parametrize(
    "lane, make, expected",
    [
        ("sub", lambda root: _real_wav(root / "kits" / "a.wav"), "unknown_lane"),
        ("kick", lambda root: root / "kits" / "gone.wav", "not_found"),
        ("kick", lambda root: _real_wav(root.parent / "elsewhere" / "a.wav"), "outside_library"),
        ("kick", lambda root: _raw(root / "kits" / "bad.wav", b"nope"), "unreadable"),
        ("kick", lambda root: _raw(root / "kits" / "a.txt", b"x"), "not_found"),
    ],
)
def test_a_hand_pick_is_refused_only_for_a_missing_foreign_or_unreadable_file(tmp_path, lane, make, expected):
    root = tmp_path / "PLG_Sounds"
    root.mkdir()
    path = make(root)
    assert pinned_kit.pin_by_hand(lane, path, root) == expected
    assert pinned_kit.load_pins() == {}


def test_unpin_empties_one_cell_and_the_last_one_removes_the_file(tmp_path):
    root = tmp_path / "PLG_Sounds"
    a, b = _real_wav(root / "kits" / "a.wav"), _real_wav(root / "kits" / "b.wav")
    pinned_kit.pin_by_hand("kick", a, root)
    pinned_kit.pin_by_hand("clap", b, root)

    pinned_kit.unpin("kick", root)
    assert set(pinned_kit.valid_pins(root)) == {"clap"} and pinned_kit.valid_explicit(root) == {"clap"}
    pinned_kit.unpin("clap", root)
    pinned_kit.unpin("clap", root)  # twice is safe
    assert not pinned_kit.pin_file().exists()


def test_a_hand_pick_is_not_touched_by_random_when_that_lane_has_no_file(api):
    root = api
    layer_free = [rel for rel in json.loads(plg_api.CATALOG_FILE.read_text())["audio"]["kits"] if "rim" not in rel]
    catalog = json.loads(plg_api.CATALOG_FILE.read_text(encoding="utf-8"))
    mine = _real_wav(root / "kits" / "my_own_layer.wav")
    catalog["audio"]["kits"] = layer_free
    plg_api.CATALOG_FILE.write_text(json.dumps(catalog), encoding="utf-8")
    assert pinned_kit.pin_by_hand("snare_layer", mine, root) is None

    result = plg_api.random_kit()  # Random finds no layer in the catalog: the hand pick stays, still explicit

    assert "snare_layer" in result["kept"] and result["explicit"] == ["snare_layer"]
    assert plg_api._pinned_kit_names()["snare_layer"] == "my_own_layer.wav"


def test_random_redraws_a_hand_picked_lane_it_can_fill_and_it_stops_being_explicit(api):
    root = api
    assert pinned_kit.pin_by_hand("kick", _real_wav(root / "kits" / "my_kick.wav"), root) is None

    result = plg_api.random_kit()

    assert "kick" in result["picked"] and result["explicit"] == []


def test_list_pin_and_unpin_through_the_api(api):
    root = api
    listed = plg_api.list_sounds()
    names = [s["name"] for s in listed["sounds"]]
    assert listed["ok"] is True and len(names) == len(ALL) and names == sorted(names, key=str.casefold)
    assert listed["pinned"] == {} and listed["explicit"] == []

    for rel_name in ("kick_1.wav", "rim_2.wav"):
        _real_wav(root / "kits" / rel_name)
    assert plg_api.pin_sound("kick", "kits/kick_1.wav")["pinned"] == {"kick": "kick_1.wav"}
    out = plg_api.pin_sound("snare_layer", "kits/rim_2.wav")
    assert out["explicit"] == ["kick", "snare_layer"]
    assert plg_api._pinned_explicit_lanes() == ["kick", "snare_layer"]

    assert plg_api.pin_sound("kick", "../outside.wav")["error_type"] in ("outside_library", "not_found")
    assert plg_api.pin_sound("kick", "")["error_type"] == "not_found"
    assert plg_api.unpin_sound("kick")["pinned"] == {"snare_layer": "rim_2.wav"}

    plg_api._jobs["j"] = plg_api._Job(id="j", kind="beat")
    assert plg_api.pin_sound("kick", "kits/kick_1.wav")["error_type"] == "busy"
    assert plg_api.unpin_sound("snare_layer")["error_type"] == "busy"
    plg_api._jobs.clear()


def test_the_status_lists_which_cells_are_the_users_own(api):
    root = api
    _real_wav(root / "kits" / "kick_1.wav")
    plg_api.pin_sound("kick", "kits/kick_1.wav")
    assert plg_api._pinned_explicit_lanes() == ["kick"]

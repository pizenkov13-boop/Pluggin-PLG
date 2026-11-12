"""A kit named like a real one: "BD #11 @producerc.wav", "CLP #7 @producerd.wav", "SN #31 @producere.wav", "CH #2 @x.wav".

The tester's beats had a clap that played a snare file, a snare that played a clap or a closed hat, and (in 3 of 5
files) no kick at all. Measured causes, all in how a NAME was read:

* ``SN`` / ``CLP`` / ``CH`` meant nothing to the matcher (only ``BD`` and ``HH`` did): every unknown file scored the same
  for every lane, so the backbeat lanes took any of them;
* the nickname after ``@`` counted as a description: ``@producerd`` contains "perc", so a kick lost 45 points and a clap
  gained 15 (the same BD file was a bad kick and a good clap);
* a foreign word in a FOLDER name ("808 Heavy", "808s & Drums") threw out every nameless file below it, even when the
  sound itself was clearly the lane's;
* a snare / clap with no file passing the strict gates was simply dropped, with no "closest file of yours" step.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

import lane_fallback
import sample_match
import sound_type
from library_paths import strip_handles
from sample_match import score_candidate, track_candidate_rejection_reasons


def _score(name: str, lane: str, **kw) -> int:
    return score_candidate(name, lane, prompt_tokens=set(), style_tokens=set(), prompt_raw="", group="kits", **kw)


# --- the nickname is not a description ------------------------------------------------------------------------


def test_a_handle_is_not_part_of_the_name():
    assert strip_handles("BD #11 @producerd.wav").split() == ["BD", "#11", ".wav"]
    assert "perc" not in strip_handles("CLP #7 @producerd.wav").lower()
    assert strip_handles("no handle.wav") == "no handle.wav"
    assert strip_handles("a @x-y_z.wav").count("@") == 0


def test_the_same_file_is_not_a_worse_kick_or_a_better_clap_because_of_the_nickname():
    for lane in ("kick", "clap", "snare", "snare_layer", "hi_hats"):
        assert _score("kit/BD #3 @producerd.wav", lane) == _score("kit/BD #3 @producerc.wav", lane), lane


def test_a_kick_by_producerd_is_a_kick():
    assert _score("kit/BD #3 @producerd.wav", "kick") > 0
    assert _score("kit/CLP #7 @producerd.wav", "clap") > 0


# --- abbreviations ----------------------------------------------------------------------------------------------

LANE_OF = {
    "BD #11 @producerc.wav": "kick",
    "HH #5 @producerc.wav": "hi_hats",
    "CH #2 @producerc.wav": "hi_hats",
    "CLP #7 @producerd.wav": "clap",
    "CLP #14 @producere.wav": "clap",
    "CP 3.wav": "clap",
    "SN #7 @producerc.wav": "snare",
    "SN #31 @producere.wav": "snare",
    "SD 4.wav": "snare",
}


@pytest.mark.parametrize("name,lane", sorted(LANE_OF.items()))
def test_an_abbreviated_file_belongs_to_its_own_lane_only(name, lane):
    own = _score("kit/" + name, lane)
    assert own > 0
    for other in ("kick", "clap", "snare", "hi_hats"):
        if other != lane:
            assert _score("kit/" + name, other) < own, (name, other)


def test_an_open_hat_abbreviation_is_not_the_main_hat():
    assert _score("kit/OH #3 @x.wav", "hi_hats") <= -50_000
    assert _score("kit/CH #3 @x.wav", "hi_hats") > 0


# --- a folder name does not veto a file whose sound fits ------------------------------------------------------


def test_a_foreign_word_in_a_folder_vetoes_only_when_the_audio_does_not_vouch():
    path = "808s & Drums/Claps/01.wav"
    assert _score(path, "clap") <= -50_000
    assert _score(path, "clap", audio_vouches=True) > -50_000


def test_a_foreign_word_in_the_file_name_still_vetoes_even_when_the_audio_vouches():
    assert _score("pack/Kick 01.wav", "clap", audio_vouches=True) <= -50_000


def _wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(44100)
        out.writeframes(b"\x01\x00" * 4410)  # 0.1 s
    return path


@pytest.fixture
def audio_is(monkeypatch):
    """Pretend the audio features of files by name: {file name: (duration s, centroid Hz, zcr)}."""
    table: dict[str, tuple[float, float, float]] = {}

    def features(path):
        row = table.get(Path(path).name)
        if row is None:
            return None
        return {"duration_s": row[0], "centroid": row[1], "zcr": row[2]}

    monkeypatch.setattr(sound_type, "_features", features)
    return table


def test_sound_type_vouches_for_the_lane_the_audio_says(audio_is, tmp_path):
    audio_is.update({"k.wav": (0.2, 200.0, 0.01), "s.wav": (0.15, 4500.0, 0.2), "h.wav": (0.06, 9000.0, 0.4), "808.wav": (1.2, 120.0, 0.0)})
    k, s, h, b = (_wav(tmp_path / n) for n in ("k.wav", "s.wav", "h.wav", "808.wav"))
    assert sound_type.vouches(k, "kick") and not sound_type.vouches(k, "clap")
    assert sound_type.vouches(s, "clap") and sound_type.vouches(s, "snare") and not sound_type.vouches(s, "kick")
    assert sound_type.vouches(h, "hi_hats") and not sound_type.vouches(h, "clap")  # a bright hat is no backbeat
    assert sound_type.vouches(b, "808_bass") and not sound_type.vouches(b, "snare")
    assert not sound_type.vouches(tmp_path / "unknown.wav", "clap")  # no reading, no vouching


def test_the_gate_lets_a_nameless_snare_through_a_misleading_folder_and_still_stops_a_kick(audio_is, tmp_path):
    root = tmp_path / "lib"
    snare = _wav(root / "808 Heavy Kit" / "01.wav")
    kick = _wav(root / "808 Heavy Kit" / "02.wav")
    audio_is.update({"01.wav": (0.15, 4500.0, 0.2), "02.wav": (0.2, 200.0, 0.01)})
    assert "name_or_path_conflicts_with_track" not in track_candidate_rejection_reasons(snare, "snare", library_root=root)
    # a kick-like file is still no snare (sound_type.conflicts), folder or no folder
    assert track_candidate_rejection_reasons(kick, "snare", library_root=root)


# --- the soft pass for snare and clap ---------------------------------------------------------------------------


@pytest.fixture
def soft_kit(monkeypatch, tmp_path):
    files = {n: _wav(tmp_path / "lib" / "kit" / n) for n in ("a.wav", "Kick 1.wav", "b.wav")}
    reasons: dict[str, list[str]] = {}
    monkeypatch.setattr(sample_match, "iter_track_candidates_grouped", lambda catalog, lane: [(f"kit/{n}", "kits") for n in files])
    monkeypatch.setattr(sample_match, "track_candidate_rejection_reasons", lambda path, lane, **kw: list(reasons.get(Path(path).name, [])))
    monkeypatch.setattr(sound_type, "conflicts", lambda path, lane: False)
    return files, reasons, tmp_path / "lib"


def test_the_soft_pass_takes_the_users_closest_file_turned_down_only_for_its_folder(soft_kit):
    files, reasons, root = soft_kit
    reasons["a.wav"] = ["name_or_path_conflicts_with_track"]
    found = lane_fallback._soft_backbeat_file("clap", set(), {"x": 1}, root)
    assert found is not None and found[0].name == "a.wav"


def test_the_soft_pass_never_takes_a_hard_failure_a_used_file_or_a_file_named_for_another_drum(soft_kit):
    files, reasons, root = soft_kit
    reasons["a.wav"] = ["unreadable"]  # hard
    reasons["b.wav"] = ["name_or_path_conflicts_with_track"]
    reasons["Kick 1.wav"] = ["name_or_path_conflicts_with_track"]  # the FILE name says kick
    used = {str(files["b.wav"].resolve())}
    assert lane_fallback._soft_backbeat_file("clap", used, {"x": 1}, root) is None


def test_the_soft_pass_does_not_take_a_file_whose_audio_is_another_drum(soft_kit, monkeypatch):
    files, reasons, root = soft_kit
    reasons["a.wav"] = ["name_or_path_conflicts_with_track"]
    monkeypatch.setattr(sound_type, "conflicts", lambda path, lane: True)
    assert lane_fallback._soft_backbeat_file("snare", set(), {"x": 1}, root) is None


def _pattern():
    note = {"time_step": 0.0, "note": "C3", "length": 0.25, "velocity": 100}
    return {"plg_midi_melody": True, "tracks": {"snare": [note], "clap": [note], "snare_layer": [note]}}


def test_a_clap_and_a_snare_get_a_tier_one_file_before_they_are_recorded_absent(soft_kit):
    files, reasons, root = soft_kit
    reasons["a.wav"] = ["name_or_path_conflicts_with_track"]
    reasons["b.wav"] = ["name_or_path_conflicts_with_track"]
    pattern, sound_map = _pattern(), {}
    lane_fallback.mark_absent_backbeat_lanes(pattern, sound_map, {"x": 1}, root)
    assert sound_map["snare"].name != sound_map["clap"].name  # one file never plays two lanes
    for lane in ("snare", "clap"):
        record = pattern["plg_sample_fallback"][lane]
        assert record["tier"] == 1 and record["sample"] and not lane_fallback.lane_is_absent(pattern, lane)
    assert "snare_layer" in pattern["plg_sample_fallback"] and lane_fallback.lane_is_absent(pattern, "snare_layer")  # none left


def test_without_a_catalog_or_a_candidate_the_lanes_are_absent_exactly_as_before(soft_kit):
    pattern, sound_map = _pattern(), {}
    lane_fallback.mark_absent_backbeat_lanes(pattern, sound_map)  # the old two-argument call
    assert all(lane_fallback.lane_is_absent(pattern, lane) for lane in ("snare", "clap", "snare_layer"))
    pattern, sound_map = _pattern(), {}
    lane_fallback.mark_absent_backbeat_lanes(pattern, sound_map, {"x": 1}, soft_kit[2])  # nothing turned down softly
    assert all(lane_fallback.lane_is_absent(pattern, lane) for lane in ("snare", "clap"))


def test_a_lane_with_a_strict_pick_is_left_alone(soft_kit):
    files, reasons, root = soft_kit
    reasons["a.wav"] = ["name_or_path_conflicts_with_track"]
    pattern, sound_map = _pattern(), {"snare": files["b.wav"], "clap": files["Kick 1.wav"]}
    lane_fallback.mark_absent_backbeat_lanes(pattern, sound_map, {"x": 1}, root)
    assert sound_map["snare"] == files["b.wav"] and "snare" not in (pattern.get("plg_sample_fallback") or {})


# --- the log line -----------------------------------------------------------------------------------------------


def test_the_session_log_line_names_every_lane_and_why_one_is_missing():
    import plg_api

    pattern = {
        "plg_sample_origins": {
            "kick": {"origin": "user", "file": "BD #11 @producerc.wav"},
            "808_bass": {"origin": "user_soft", "file": "808 Long.wav"},
            "hi_hats": {"origin": "absent", "file": ""},
        },
        "plg_sample_fallback": {"hi_hats": {"tier": 0, "reasons": ["missing_track_identity"], "user_files": 2}},
    }
    line = plg_api._lane_summary(pattern)
    assert "kick=BD #11 @producerc.wav [user]" in line and "808_bass=808 Long.wav [user_soft]" in line
    assert "hi_hats=ABSENT(missing_track_identity; own files turned down: 2)" in line
    assert plg_api._lane_summary({"plg_sample_origins": 5}).startswith("lanes:")

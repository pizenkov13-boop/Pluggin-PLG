"""tools/show_flp_sounds.py: the dev tool that prints, per channel, the baked file and YOUR source file."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from flp_writer import build_flp

TOOL = Path(__file__).resolve().parent.parent / "tools" / "show_flp_sounds.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("show_flp_sounds", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _note(t=0.0, n="C3", ln=0.5):
    return {"time_step": t, "note": n, "length": ln, "velocity": 100}


def _beat(tmp_path, sample_name="kick__warm_d1.40_hp0_Kick 01.wav"):
    baked = tmp_path / "baked" / sample_name
    baked.parent.mkdir(parents=True, exist_ok=True)
    baked.write_bytes(b"x")
    data = {
        "bpm": 140,
        "plg_midi_melody": True,
        "tracks": {"melody_lead": [_note(0.0, "A4", 4.0)], "kick": [_note(0.0)], "clap": [_note(1.0)]},
        "plg_sound_paths": {"kick": str(baked)},
        "plg_sample_fallback": {"clap": {"tier": 0, "sample": None, "reasons": [], "user_files": 0}},
    }
    flp = tmp_path / "beat.flp"
    flp.write_bytes(build_flp(data))
    return flp, data


def test_the_channels_come_back_with_their_baked_files(tool, tmp_path):
    flp, _data = _beat(tmp_path)
    channels = tool.read_channels(flp)
    names = [c["name"] for c in channels]
    assert names[0] == "PLG MIDI" and "PLG Kick" in names
    assert channels[0]["sample"] is None
    kick = next(c for c in channels if c["name"] == "PLG Kick")
    assert Path(kick["sample"]).name == "kick__warm_d1.40_hp0_Kick 01.wav"


@pytest.mark.parametrize(
    "baked, original",
    [
        ("kick__warm_d1.40_hp0_BLAKK ROCKSTAR P2 Kick @producera.wav", "BLAKK ROCKSTAR P2 Kick @producera"),
        ("kick__phase_inv_kick__warm_d1.40_hp0_OFF THE METER Kick.wav", "OFF THE METER Kick"),
        ("hi_hats__ROOT_OF_ALL_EVIL_Hat_@producerb_pcm24.wav", "ROOT_OF_ALL_EVIL_Hat_@producerb"),
        ("snare_layer__warm_d1.20_hp0_XPOSED Snap @producera.wav", "XPOSED Snap @producera"),
    ],
)
def test_the_original_name_is_read_back_out_of_a_baked_name(tool, baked, original):
    assert tool.strip_bake_marks(baked) == original


def test_a_source_is_found_in_the_library_by_name_whatever_the_spaces(tool, tmp_path):
    lib = tmp_path / "lib"
    (lib / "Hat").mkdir(parents=True)
    wanted = lib / "Hat" / "ROOT OF ALL EVIL Hat @producerb.wav"
    wanted.write_bytes(b"x")
    assert tool.find_in_library(lib, "ROOT_OF_ALL_EVIL_Hat_@producerb") == [wanted]
    assert tool.find_in_library(lib, "nothing like it") == []


def test_the_newest_flp_of_a_folder_is_taken(tool, tmp_path):
    import os
    import time

    older, newer = tmp_path / "a.flp", tmp_path / "b.flp"
    older.write_bytes(b"1")
    newer.write_bytes(b"2")
    os.utime(older, (time.time() - 100, time.time() - 100))
    assert tool.latest_flp(tmp_path) == newer
    assert tool.latest_flp(older) == older


def test_the_report_names_the_users_pick_and_says_when_the_data_is_someone_elses(tool, tmp_path, capsys, monkeypatch):
    flp, data = _beat(tmp_path)
    source = tmp_path / "lib" / "Kick 01.wav"
    source.parent.mkdir()
    source.write_bytes(b"x")
    data["plg_explicit_lanes"] = ["kick"]
    data["plg_sample_origins"] = {
        "kick": {"origin": "user", "file": "Kick 01.wav", "path": str(source)},
        "clap": {"origin": "absent", "file": "", "path": ""},
    }
    (tmp_path / "beat.json").write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["show_flp_sounds", str(flp)])
    assert tool.main() == 0
    report = capsys.readouterr().out
    assert "matches this .flp" in report and str(source) in report and "YOUR PICK" in report
    assert "no channel in the .flp" in report and "PLG Clap" in report

    other = dict(data, plg_sound_paths={"kick": str(tmp_path / "baked" / "kick__warm_d1.40_hp0_Another Kick.wav")})
    (tmp_path / "beat.json").write_text(json.dumps(other), encoding="utf-8")
    assert tool.main() == 0
    assert "does NOT match" in capsys.readouterr().out

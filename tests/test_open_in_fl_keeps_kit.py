"""Open in FL must reopen the beat PLG just made, not re-pick its kit.

Found by running the tester exe end to end: create_beat logged kick=OFF THE METER, clap=NAKED, hats=PSYCHO; after the automatic
Open in FL the .flp loaded BLAKK, 200 KASH, THX Hat. ``fl_launch._has_usable_saved_sound_paths`` demanded a saved sound for EVERY lane
with notes - the melody (no sample by design) and a lane recorded absent never have one - so it said "no" for every beat and the
kit was chosen again.
"""

from __future__ import annotations

import pytest

from fl_launch import _has_usable_saved_sound_paths


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("PLG_MELODY_SAMPLE", raising=False)


def _note():
    return [{"time_step": 0.0, "note": "C3", "length": 0.25, "velocity": 100}]


def _beat(tmp_path, **paths):
    files = {}
    for lane in ("kick", "808_bass", "hi_hats", "clap", "snare", "snare_layer"):
        if lane in paths and paths[lane] is None:
            continue
        f = tmp_path / f"{lane}.wav"
        f.write_bytes(b"x")
        files[lane] = str(f)
    return {
        "plg_midi_melody": True,
        "tracks": {lane: _note() for lane in ("melody_lead", "kick", "808_bass", "hi_hats", "clap", "snare", "snare_layer")},
        "plg_sound_paths": files,
        "plg_sample_fallback": {},
    }


def test_the_melody_has_no_sound_by_design_and_the_kit_is_kept(tmp_path):
    assert _has_usable_saved_sound_paths(_beat(tmp_path)) is True


def test_a_lane_recorded_absent_is_not_missing(tmp_path):
    data = _beat(tmp_path, kick=None)
    data["plg_sample_fallback"]["kick"] = {"tier": 0, "sample": None, "reasons": [], "user_files": 0}
    assert _has_usable_saved_sound_paths(data) is True


def test_a_lane_with_notes_and_no_record_and_no_file_is_still_missing(tmp_path):
    assert _has_usable_saved_sound_paths(_beat(tmp_path, kick=None)) is False


def test_a_saved_file_that_has_gone_is_missing(tmp_path):
    data = _beat(tmp_path)
    (tmp_path / "snare.wav").unlink()
    assert _has_usable_saved_sound_paths(data) is False


def test_a_melody_that_does_carry_a_sample_needs_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PLG_MELODY_SAMPLE", "on")
    assert _has_usable_saved_sound_paths(_beat(tmp_path)) is False


def test_nothing_to_play_is_not_usable(tmp_path):
    data = _beat(tmp_path)
    data["tracks"] = {}
    assert _has_usable_saved_sound_paths(data) is False

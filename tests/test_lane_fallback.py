"""The generation must finish: last-resort samples for kick / 808_bass / hi_hats.

This file is the LEGACY ladder (``PLG_SUBSTITUTE_SOUNDS=on``): the tests that need tier 2 or a
starter sound carry the ``substitutes_on`` fixture. What the default does (a lane without a usable
file of the user's is not in the .flp) is in ``test_empty_lanes.py``.

Strict selection still decides first. Only when a required lane has no sample
that passes the export gate does ``lane_fallback`` settle it (once, recorded in
``plg_sample_fallback``), and export honours that record:

* tier 1 - the user's own closest file failing only SOFT reasons (808 notes
  are shortened to fit it);
* tier 2 - a synthetic sample that passes every gate by construction.

HARD reasons - above all ``overdriven_808_bass``, the one 808 gate tied to the
harsh-noise history - are never overridden.
"""

from __future__ import annotations

import math
import struct
import wave

import pytest

import fallback_samples as fs
from lane_fallback import SOFT_REASONS, ensure_required_lane_samples, resolve_lane_sample
from library_catalog import scan_library
from library_quality import (
    bass_808_max_source_seconds,
    bass_808_tail_rejection_reason,
    fit_808_notes_to_duration,
)
from sample_match import (
    rejection_reason_for_pattern,
    track_candidate_rejection_reason,
    track_candidate_rejection_reasons,
)
from sample_pitch_passport import sample_pitch_passport

RATE = 44100


@pytest.fixture(autouse=True)
def _isolated_user_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))


@pytest.fixture
def substitutes_on(monkeypatch):
    monkeypatch.setenv("PLG_SUBSTITUTE_SOUNDS", "on")


def _write(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, int(s)))) for s in samples))


def _sine(freq, seconds, *, tau=1.2, amp=24000):
    return [
        amp * min(1.0, i / 220.0) * math.exp(-i / RATE / tau) * math.sin(2 * math.pi * freq * i / RATE)
        for i in range(int(RATE * seconds))
    ]


def _notes(lengths=(2.75, 1.45, 0.5), names=("C2", "E2", "G2")):
    return [
        {"time_step": float(i * 4), "note": names[i % len(names)], "length": float(length), "velocity": 116}
        for i, length in enumerate(lengths)
    ]


def _pattern(bpm=140, lengths=(2.75, 1.45, 0.5)):
    return {
        "bpm": bpm,
        "plg_midi_melody": True,
        "tracks": {
            "808_bass": _notes(lengths),
            "kick": [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 124}],
            "hi_hats": [{"time_step": 0.0, "note": "C5", "length": 0.1, "velocity": 90}],
        },
    }


# --- one owner of the rules ---------------------------------------------------


@pytest.mark.parametrize("name", ["808 A.wav", "Sub_01.wav", "Dark_01.wav", "808 harsh.wav"])
def test_first_reason_is_the_head_of_all_reasons(tmp_path, name):
    _write(tmp_path / name, _sine(4000 if "harsh" in name else 55, 1.0))
    path = tmp_path / name
    every = track_candidate_rejection_reasons(path, "808_bass", library_root=tmp_path)
    first = track_candidate_rejection_reason(path, "808_bass", library_root=tmp_path)
    assert first == (every[0] if every else None)


# --- the synthetic samples pass every gate BY CONSTRUCTION ---------------------


def test_synthetic_hat_passes_the_hat_gate():
    hat = fs.fallback_hat()
    assert track_candidate_rejection_reasons(hat, "hi_hats") == []
    assert fs.fallback_hat() == hat  # cached, stable name


@pytest.mark.parametrize("bpm", [60, 68, 100, 140, 180])
def test_synthetic_808_passes_every_gate_and_covers_the_longest_note(bpm):
    notes = _notes((2.75, 2.75, 1.45), ("C2", "G2", "C3"))  # the export range: MIDI 24-36
    path = fs.fallback_808(notes, bpm)
    passport = sample_pitch_passport(path)

    assert track_candidate_rejection_reasons(path, "808_bass") == []
    assert bass_808_tail_rejection_reason(path, notes, bpm, passport) is None
    assert not passport["weak_pitch_classes"]
    assert bass_808_max_source_seconds(notes, bpm, passport) <= (path.stat().st_size / 2 / RATE)


# --- fitting notes to a shorter sample -----------------------------------------


def test_fit_shortens_only_what_overruns_and_never_below_the_floor():
    passport = {"fl_root_note": 28, "pitch_class_map": {}}
    notes = _notes((2.0, 0.6, 0.5), ("E2", "E2", "E2"))  # E2 = MIDI 28 = the root: 1 s/beat at 60 bpm
    assert fit_808_notes_to_duration(notes, 60, passport, 1.0, min_length=0.5, safety=1.0) is True
    assert [n["length"] for n in notes] == [1.0, 0.6, 0.5]

    tiny = _notes((2.0,), ("E2",))
    before = [dict(n) for n in tiny]
    assert fit_808_notes_to_duration(tiny, 60, passport, 0.4, min_length=0.5) is False
    assert tiny == before  # all-or-nothing: nothing touched when it cannot fit


def test_fit_leaves_slides_alone():
    passport = {"fl_root_note": 28, "pitch_class_map": {}}
    notes = [{"time_step": 0.0, "note": "E2", "length": 9.0, "slide": True}]
    assert fit_808_notes_to_duration(notes, 60, passport, 1.0, min_length=0.5) is True
    assert notes[0]["length"] == 9.0


# --- the ladder ------------------------------------------------------------------


def _library(tmp_path, files):
    lib = tmp_path / "lib"
    for name, samples in files.items():
        _write(lib / name, samples)
    return lib, scan_library(lib, quarantine=False)


def test_clean_lanes_are_untouched_and_nothing_is_recorded(tmp_path):
    lib, catalog = _library(tmp_path, {"808 Clean.wav": _sine(41.2034, 3.0)})
    pattern = _pattern(bpm=140)
    sound_map = {
        "808_bass": lib / "808 Clean.wav",
        "kick": fs.fallback_dir().parent.parent / "assets" / "unused.wav",  # replaced below
    }
    from starter_kit import ensure_starter_kit

    starter = ensure_starter_kit()
    sound_map["kick"] = starter["kick"]
    sound_map["hi_hats"] = fs.fallback_hat()

    ensure_required_lane_samples(pattern, sound_map, catalog, lib)

    assert "plg_sample_fallback" not in pattern
    assert sound_map["808_bass"] == lib / "808 Clean.wav"


def test_overdriven_808_is_never_used_the_synthetic_wins(tmp_path, substitutes_on):
    # centroid ~4 kHz: a pre-destroyed file. HARD: tier 2 even though it is the
    # user's only 808 and its name says 808.
    lib, catalog = _library(tmp_path, {"808 harsh.wav": _sine(4000, 1.5)})
    assert "overdriven_808_bass" in track_candidate_rejection_reasons(lib / "808 harsh.wav", "808_bass")
    assert "overdriven_808_bass" not in SOFT_REASONS["808_bass"]

    pattern = _pattern()
    path = resolve_lane_sample("808_bass", pattern, {}, catalog, lib)

    assert path.name.startswith("PLG_fallback_808_")
    assert pattern["plg_sample_fallback"]["808_bass"]["tier"] == 2


def test_soft_failure_uses_the_users_file_and_fits_the_notes(tmp_path):
    # centroid ~700 Hz: only "too_bright" (SOFT) -> tier 1, notes shortened.
    lib, catalog = _library(tmp_path, {"808 bright.wav": _sine(700, 1.4, tau=0.8)})
    assert track_candidate_rejection_reasons(lib / "808 bright.wav", "808_bass") == ["too_bright_for_808_bass"]

    pattern = _pattern(bpm=68, lengths=(2.75, 2.75, 1.45))
    path = resolve_lane_sample("808_bass", pattern, {}, catalog, lib)
    passport = sample_pitch_passport(path)

    record = pattern["plg_sample_fallback"]["808_bass"]
    assert path.name == "808 bright.wav" and record["tier"] == 1
    assert record["reasons"] == ["too_bright_for_808_bass"]
    assert bass_808_tail_rejection_reason(path, pattern["tracks"]["808_bass"], 68, passport) is None
    assert all(n["length"] >= 0.5 for n in pattern["tracks"]["808_bass"])  # never a click


def test_export_honours_the_record_but_never_a_hard_reason(tmp_path):
    lib, _ = _library(
        tmp_path, {"808 bright.wav": _sine(700, 1.4, tau=0.8), "808 harsh.wav": _sine(4000, 1.5)}
    )
    bright, harsh = lib / "808 bright.wav", lib / "808 harsh.wav"
    record = {"808_bass": {"tier": 1, "sample": str(bright), "reasons": ["too_bright_for_808_bass"]}}

    assert rejection_reason_for_pattern(bright, "808_bass", {}) == "too_bright_for_808_bass"
    assert rejection_reason_for_pattern(bright, "808_bass", {"plg_sample_fallback": record}) is None
    # the recorded soft reason must not launder a hard one
    assert (
        rejection_reason_for_pattern(harsh, "808_bass", {"plg_sample_fallback": record})
        == "overdriven_808_bass"
    )


def test_empty_kit_still_gets_working_808_and_hats(tmp_path, substitutes_on):
    pattern = _pattern(bpm=68, lengths=(2.75, 2.75, 1.45))
    sound_map = {}
    ensure_required_lane_samples(pattern, sound_map, None, tmp_path)

    assert set(sound_map) == {"kick", "808_bass", "hi_hats"}
    for lane, path in sound_map.items():
        assert rejection_reason_for_pattern(path, lane, pattern) is None, lane
    passport = sample_pitch_passport(sound_map["808_bass"])
    assert bass_808_tail_rejection_reason(
        sound_map["808_bass"], pattern["tracks"]["808_bass"], 68, passport
    ) is None


def test_export_validator_no_longer_vetoes_the_fallback(tmp_path, substitutes_on):
    from midi_validate import validate_midi_mode

    pattern = _pattern(bpm=68, lengths=(2.75, 1.45, 0.5))
    sound_map = {}
    ensure_required_lane_samples(pattern, sound_map, None, tmp_path)
    pattern["plg_sound_paths"] = {lane: str(path) for lane, path in sound_map.items()}

    sample_errors = [e for e in validate_midi_mode(pattern) if "sample" in e]
    assert sample_errors == []


def test_a_lane_that_is_not_midi_mode_is_left_alone(tmp_path):
    pattern = _pattern()
    pattern["plg_midi_melody"] = False
    sound_map = {}
    ensure_required_lane_samples(pattern, sound_map, None, tmp_path)
    assert sound_map == {} and "plg_sample_fallback" not in pattern


# --- the retired sub_808 lane must not eat the only 808 ------------------------


def test_sub_808_does_not_hold_the_808_bass_files_hostage(tmp_path):
    from starter_kit import _healthy_808_replacement

    lib, catalog = _library(tmp_path, {"808 Only.wav": _sine(41.2034, 4.0)})
    only = (lib / "808 Only.wav").resolve()
    picked = _healthy_808_replacement(
        {"sub_808": only},  # the dead lane grabbed it first
        catalog,
        lib,
        prompt="",
        style="",
        melody_energy=None,
        needed_pcs=set(),
        notes=[],
        bpm=140.0,
    )
    assert picked == only


def test_bundled_closed_hat_pick_never_returns_the_open_roll():
    from build_bundled_sounds import pick_bundled_for_track

    picked = pick_bundled_for_track("hi_hats", prompt="roll machine", style="")
    assert picked is not None and "open" not in picked.name and "roll" not in picked.name


# --- the "first file in the lane's folder" fallback must respect the type --------


def test_a_kit_without_snares_never_gets_an_808_or_kick_as_snare_or_clap(tmp_path, substitutes_on):
    from starter_kit import ensure_starter_kit, resolve_track_samples

    # Two kicks and two 808s: after kick/808 take one each, spare kick/808 files
    # remain in kits/ - exactly what the folder fallback used to hand to snare/clap.
    lib = tmp_path / "lib"
    names = ["OFF THE METER Kick.wav", "BLAKK ROCKSTAR P2 Kick.wav", "GHOUL 808.wav", "THX 808.wav"]
    for name in names:
        _write(lib / "kits" / name, _sine(60, 0.2, tau=0.05) if "Kick" in name else _sine(41.2034, 2.0))
    catalog = scan_library(lib, quarantine=False)

    chosen = resolve_track_samples(catalog, library_root=lib)
    starter = ensure_starter_kit()
    user_files = {(lib / "kits" / name).resolve() for name in names}

    for lane in ("snare", "clap", "snare_layer"):
        assert chosen[lane].resolve() not in user_files, f"{lane} was handed a kick/808"
    assert chosen["snare"] == starter["snare"]
    assert chosen["clap"] == starter["clap"]


def test_the_folder_fallback_still_takes_an_untyped_file_for_a_lane(tmp_path):
    # Only a file that says it is ANOTHER lane is skipped: an unlabelled one in
    # the lane's folder is still better than nothing, exactly as before.
    from starter_kit import resolve_track_samples

    lib = tmp_path / "lib"
    # A snare-like sound (a 3.2 kHz body with a noisy tail): untyped, but not
    # clearly another drum, so it still beats the bundled starter.
    _write(lib / "kits" / "Thing 01.wav", _sine(3200, 0.2, tau=0.05))
    catalog = scan_library(lib, quarantine=False)
    chosen = resolve_track_samples(catalog, library_root=lib)
    assert chosen["snare"].name == "Thing 01.wav"


def test_the_folder_fallback_skips_an_untyped_file_that_sounds_like_another_drum(tmp_path, substitutes_on):
    # The same untyped file, but a low thump: sound_type says it is a kick, so it
    # does not become the snare.
    from starter_kit import ensure_starter_kit, resolve_track_samples

    lib = tmp_path / "lib"
    _write(lib / "kits" / "Thing 01.wav", _sine(80, 0.2, tau=0.05))
    catalog = scan_library(lib, quarantine=False)
    chosen = resolve_track_samples(catalog, library_root=lib)
    assert chosen["snare"] == ensure_starter_kit()["snare"]

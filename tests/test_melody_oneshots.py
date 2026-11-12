"""PLG's own melody one-shots (``melody_oneshots``, ``PLG_MELODY_ONESHOTS``): library, selection, preparation,
the ``starter_kit`` wiring, ``tools/check_oneshots.py`` and the exe bundle list.

The selection scheme is an ESTIMATE (the group thresholds are first guesses): these tests pin the scheme as written,
they do not say it is right. Pavel checks it by ear.
"""

from __future__ import annotations

import hashlib
import math
import shutil
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

import audio_features
import flp_writer
import kit_variety
import library_catalog
import melody_oneshots as mo
import sample_passport
import sound_policy
import starter_kit
from kit_library_fixture import build_library

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import check_oneshots  # noqa: E402
import melody_oneshots_bundle  # noqa: E402
import show_flp_sounds  # noqa: E402

RATE = 44100
SHIPPED_COUNTS = {"piano": 3, "pluck": 3, "pad": 3, "strings": 3, "key": 2, "lead": 2}


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr(audio_features, "CACHE_FILE", tmp_path / "audio_features.json")
    monkeypatch.setattr(audio_features, "_cache", None)
    monkeypatch.setattr(sample_passport, "PASSPORT_FILE", tmp_path / "sample_passports.json")
    sample_passport.reset_memory_cache()
    monkeypatch.setattr(kit_variety, "save_kit_pick", lambda picks: None)
    monkeypatch.setattr(kit_variety, "_load_history", lambda: [])
    monkeypatch.delenv("PLG_MELODY_ONESHOTS", raising=False)
    monkeypatch.delenv("PLG_MELODY_SAMPLE", raising=False)
    yield
    sample_passport.reset_memory_cache()


# --- helpers --------------------------------------------------------------------------------------------------
def _tone(hz: float, seconds: float = 1.0, *, dc: float = 0.0, amp: float = 0.5, partials: dict[int, float] | None = None) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    parts = partials or {1: 1.0}
    wave_ = sum(level * np.sin(2 * math.pi * hz * k * t) for k, level in parts.items())
    wave_ = amp * wave_ / max(1e-9, np.abs(wave_).max())
    return (wave_ + dc)[:, None].repeat(2, axis=1)


def _write(path: Path, hz: float = 261.6256, **kwargs) -> Path:
    mo.write_wav24(path, _tone(hz, **kwargs), RATE)
    return path


def _bank(root: Path, layout: dict[str, int]) -> Path:
    """``{type: number of files}`` -> root/<type>/<type>_<n>_x.wav, plus the things that must never be picked."""
    for kind, count in layout.items():
        for n in range(1, count + 1):
            _write(root / kind / f"{kind}_{n}_x.wav")
    return root


def _notes(spec: list[tuple[float, str, float]]) -> list[dict]:
    return [{"time_step": t, "note": n, "length": ln, "velocity": 100} for t, n, ln in spec]


def _pattern(spec, seed: int | None = 7) -> dict:
    pattern = {"bpm": 140, "plg_midi_melody": True, "tracks": {"melody_lead": _notes(spec)}}
    if seed is not None:
        pattern["plg_variety"] = {"seed": seed}
    return pattern


def _sustained():  # three notes held 8 beats, a new chord every 8 beats
    return [(start, note, 8.0) for start in (0, 8, 16, 24) for note in ("C5", "E5", "G5")]


def _plucked():  # one high voice, eighth notes
    return [(i * 0.5, "A6", 0.25) for i in range(64)]


def _chords():  # three-note chords, one beat long
    return [(float(beat), note, 1.0) for beat in range(32) for note in ("C5", "E5", "G5")]


def _slow_line():  # one voice, a note a beat, around middle C
    return [(float(beat), "C5", 1.0) for beat in range(32)]


# --- the switch -------------------------------------------------------------------------------------------------
def test_the_switch_is_on_unless_it_says_off(monkeypatch):
    assert sound_policy.melody_oneshots_enabled() is True  # the variable is deleted by the fixture: the default
    for raw in ("on", "1", "true", "YES", "", "banana"):
        monkeypatch.setenv("PLG_MELODY_ONESHOTS", raw)
        assert sound_policy.melody_oneshots_enabled() is True
    for raw in ("off", "OFF", "0", "false", "no", " off "):
        monkeypatch.setenv("PLG_MELODY_ONESHOTS", raw)
        assert sound_policy.melody_oneshots_enabled() is False


@pytest.mark.skip(reason="requires third-party assets not included")
def test_the_tester_template_turns_it_on_and_the_release_one_leaves_the_default():
    def values(name: str) -> dict[str, str]:
        text = (REPO / name).read_text(encoding="utf-8")
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))

    assert values(".env.tester")["PLG_MELODY_ONESHOTS"] == "on"
    assert "PLG_MELODY_ONESHOTS" not in values(".env.release")  # the code default (on) applies; nothing to seed


# --- the library ----------------------------------------------------------------------------------------------------
def test_an_empty_or_missing_folder_has_no_sounds(tmp_path):
    assert mo.library(tmp_path / "missing") == {}
    (tmp_path / "empty").mkdir()
    assert mo.library(tmp_path / "empty") == {}
    assert mo.choose(_pattern(_sustained()), root=tmp_path / "empty") is None


def test_one_file_is_the_whole_library(tmp_path):
    _write(tmp_path / "pad" / "pad_1_dark.wav")
    found = mo.library(tmp_path)
    assert list(found) == ["pad"] and [p.name for p in found["pad"]] == ["pad_1_dark.wav"]
    choice = mo.choose(_pattern(_slow_line()), root=tmp_path)  # a piano melody, but pad is all there is
    assert choice["type"] == "pad" and choice["file"] == "pad_1_dark.wav"


def test_several_types_are_listed_sorted_and_the_rest_is_ignored(tmp_path):
    _bank(tmp_path, {"piano": 2, "lead": 1, "strings": 3})
    _write(tmp_path / "_rejected" / "lead" / "lead_1_dark.wav")  # dropped by ear
    _write(tmp_path / "_rejected" / "pad_9_x.wav")
    _write(tmp_path / "drums" / "kick.wav")  # not a type
    _write(tmp_path / "pad" / "_scratch.wav")  # an underscore file is not a candidate
    (tmp_path / "pad" / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sources.txt").write_text("secret", encoding="utf-8")
    found = mo.library(tmp_path)
    assert sorted(found) == ["lead", "piano", "strings"]
    assert [p.name for p in found["strings"]] == ["strings_1_x.wav", "strings_2_x.wav", "strings_3_x.wav"]
    assert all("_rejected" not in str(p) for paths in found.values() for p in paths)


@pytest.mark.skip(reason="requires third-party assets not included")
def test_the_shipped_folder_has_sixteen_sounds_and_no_rejected_one():
    found = mo.library(REPO / "assets" / "melody_oneshots")
    assert {kind: len(paths) for kind, paths in found.items()} == SHIPPED_COUNTS
    names = {p.name for paths in found.values() for p in paths}
    assert "lead_1_dark.wav" not in names and "key_2_rage.wav" not in names
    rejected = REPO / "assets" / "melody_oneshots" / "_rejected"
    assert (rejected / "lead" / "lead_1_dark.wav").is_file() and (rejected / "key" / "key_2_rage.wav").is_file()


def test_a_rejected_file_is_never_chosen(tmp_path):
    _bank(tmp_path, {"lead": 1})
    _write(tmp_path / "_rejected" / "lead" / "lead_9_bad.wav")
    for seed in range(40):
        assert mo.choose(_pattern(_plucked(), seed), root=tmp_path)["file"] == "lead_1_x.wav"


# --- what the melody is like and the group --------------------------------------------------------------------------
def test_the_features_come_from_the_notes():
    features = mo.melody_features(_pattern(_sustained()))
    assert features["mean_len_beats"] == pytest.approx(8.0)
    assert features["polyphony"] > 0.9  # three notes sound nearly all the time
    assert features["median_pitch"] == 64.0 + 0  # E5 in FL naming: 5 * 12 + 4
    assert features["notes_per_bar"] == pytest.approx(12 / 8.0)
    assert mo.melody_features({"tracks": {"melody_lead": []}}) is None


def test_role_lanes_count_as_the_same_melody():
    pattern = {"tracks": {"melody_lead": _notes([(0, "C5", 8.0)]), "chords": _notes([(0, "E5", 8.0), (0, "G5", 8.0)])}}
    assert mo.melody_features(pattern)["polyphony"] > 0.9


@pytest.mark.parametrize(
    ("spec", "group"),
    [
        (_sustained(), mo.GROUP_SUSTAIN),
        (_plucked(), mo.GROUP_PLUCKED),
        (_chords(), mo.GROUP_KEYS),
        (_slow_line(), mo.GROUP_DEFAULT),
    ],
)
def test_the_scheme_picks_the_group(spec, group):
    assert mo.choose_group(mo.melody_features(_pattern(spec))) == group


def test_the_scheme_answers_with_the_types_it_names():
    assert mo.GROUP_TYPES[mo.GROUP_SUSTAIN] == ("pad", "strings")
    assert mo.GROUP_TYPES[mo.GROUP_PLUCKED] == ("lead", "pluck")
    assert mo.GROUP_TYPES[mo.GROUP_KEYS] == ("piano", "key")
    assert mo.GROUP_TYPES[mo.GROUP_DEFAULT] == ("piano",)


# --- choosing ---------------------------------------------------------------------------------------------------------
def test_the_same_seed_gives_the_same_sound(tmp_path):
    _bank(tmp_path, {"pad": 3, "strings": 3})
    first = [mo.choose(_pattern(_sustained(), seed), root=tmp_path) for seed in range(30)]
    again = [mo.choose(_pattern(_sustained(), seed), root=tmp_path) for seed in range(30)]
    assert first == again
    assert {(c["type"], c["file"]) for c in first} != {(first[0]["type"], first[0]["file"])}  # the seed does move it
    assert {c["type"] for c in first} == {"pad", "strings"}
    assert all(c["group"] == mo.GROUP_SUSTAIN == c["group_used"] for c in first)


def test_the_seed_is_the_variety_seed_and_zero_without_one(tmp_path):
    _bank(tmp_path, {"piano": 3})
    assert mo.seed_of(_pattern(_slow_line(), 11)) == 11
    assert mo.seed_of(_pattern(_slow_line(), None)) == 0
    assert mo.choose(_pattern(_slow_line(), None), root=tmp_path)["seed"] == 0
    assert mo.choose(_pattern(_slow_line(), 5), root=tmp_path, seed=99)["seed"] == 99  # an explicit seed wins


def test_a_type_without_files_is_never_chosen(tmp_path):
    _bank(tmp_path, {"strings": 2})  # the sustain group is pad + strings, pad is empty
    assert {mo.choose(_pattern(_sustained(), s), root=tmp_path)["type"] for s in range(30)} == {"strings"}


def test_an_empty_group_falls_back_to_the_nearest_group(tmp_path):
    _bank(tmp_path, {"pad": 1, "piano": 1})  # lead / pluck missing
    choice = mo.choose(_pattern(_plucked()), root=tmp_path)
    assert choice["group"] == mo.GROUP_PLUCKED and choice["group_used"] == mo.GROUP_KEYS and choice["type"] == "piano"
    only_lead = _bank(tmp_path / "b", {"lead": 1})
    choice = mo.choose(_pattern(_sustained()), root=only_lead)  # sustain -> keys (empty) -> lead_pluck
    assert choice["group_used"] == mo.GROUP_PLUCKED and choice["type"] == "lead"


def test_no_melody_notes_no_sound(tmp_path):
    _bank(tmp_path, {"piano": 1})
    assert mo.choose({"tracks": {}}, root=tmp_path) is None


# --- preparing the audio ----------------------------------------------------------------------------------------------
def test_preparation_removes_dc_fades_and_normalises():
    raw = _tone(261.6256, 1.0, dc=0.2, amp=0.4)
    out = mo.prepare_samples(raw, RATE)
    assert abs(raw.mean()) > 0.19 and abs(out.mean()) < 5e-4  # 0.2 before; the fades leave about -70 dBFS
    assert 20 * math.log10(np.abs(out).max()) == pytest.approx(-3.0, abs=0.01)
    assert abs(out[0]).max() < 1e-3 and abs(out[-1]).max() < 1e-3


def test_the_fades_are_five_and_fifty_milliseconds():
    out = mo.prepare_samples(_tone(1000.0, 1.0), RATE)[:, 0]
    full = np.abs(out[RATE // 2 : RATE // 2 + 441]).max()

    def level(start_s: float, length_s: float = 0.0005) -> float:
        a = int(RATE * start_s)
        return float(np.abs(out[a : a + int(RATE * length_s)]).max()) / full

    assert level(0.0) < 0.1  # the first half millisecond of a 5 ms ramp
    assert level(0.0025) == pytest.approx(0.5, abs=0.15)  # halfway up
    assert level(0.0065) > 0.9  # the ramp is over by 5 ms
    assert level(1.0 - 0.0255) == pytest.approx(0.5, abs=0.1)  # halfway down a 50 ms fade-out
    assert level(1.0 - 0.0505) > 0.9 and level(1.0 - 0.0005) < 0.05


def test_the_twenty_hertz_highpass_takes_out_the_rumble():
    rumble = _tone(5.0, 2.0, amp=0.5) + _tone(1000.0, 2.0, amp=0.1)
    out = mo.prepare_samples(rumble, RATE)
    freqs = np.fft.rfftfreq(len(out), 1 / RATE)

    def rumble_to_tone(signal: np.ndarray) -> float:
        spectrum = np.abs(np.fft.rfft(signal[:, 0]))
        return float(spectrum[(freqs > 3) & (freqs < 8)].max() / spectrum[(freqs > 990) & (freqs < 1010)].max())

    # a 2nd-order slope at a quarter of the cutoff: about 24 dB down (the rumble was 5x the tone)
    assert rumble_to_tone(rumble) == pytest.approx(5.0, rel=0.05)
    assert rumble_to_tone(out) < rumble_to_tone(rumble) / 10.0


def test_a_silent_file_is_returned_unchanged():
    silent = np.zeros((1000, 2))
    assert np.array_equal(mo.prepare_samples(silent, RATE), silent)


def test_the_prepared_copy_is_new_24_bit_and_the_source_is_untouched(tmp_path):
    source = _write(tmp_path / "src" / "pad_1_x.wav", dc=0.2, amp=0.3)
    before = hashlib.sha1(source.read_bytes()).hexdigest()
    copy = mo.prepared_copy(source, tmp_path / "cache")
    assert copy != source and copy.parent == tmp_path / "cache" and copy.name == f"pad_1_x__prep{mo.PREP_VERSION}.wav"
    assert hashlib.sha1(source.read_bytes()).hexdigest() == before
    with wave.open(str(copy), "rb") as handle:
        assert (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) == (2, 3, RATE)
    from wav_decode import read_wav

    audio = read_wav(copy)
    assert 20 * math.log10(float(np.abs(audio.samples).max())) == pytest.approx(-3.0, abs=0.05)
    assert abs(float(audio.samples.mean())) < 5e-4
    stamp = copy.stat().st_mtime_ns
    assert mo.prepared_copy(source, tmp_path / "cache") == copy and copy.stat().st_mtime_ns == stamp  # made once


def test_a_broken_file_leaves_the_channel_without_a_sound(tmp_path):
    (tmp_path / "pad").mkdir()
    (tmp_path / "pad" / "pad_1_x.wav").write_bytes(b"not a wav at all")
    pattern = _pattern(_sustained())
    assert mo.pick_for_pattern(pattern, tmp_path / "cache", root=tmp_path) is None
    assert "plg_melody_oneshot" not in pattern


def test_the_choice_is_recorded_for_show_flp_sounds(tmp_path):
    _bank(tmp_path, {"pad": 1})
    pattern = _pattern(_sustained(), 3)
    path = mo.pick_for_pattern(pattern, tmp_path / "cache", root=tmp_path)
    record = pattern["plg_melody_oneshot"]
    assert Path(record["prepared"]) == path and record["seed"] == 3 and record["type"] == "pad"
    assert record["file"] == "pad_1_x.wav" and record["features"]["mean_len_beats"] == 8.0


# --- starter_kit ------------------------------------------------------------------------------------------------------
def _kit_pattern(seed: int = 4) -> dict:
    pattern = _pattern(_sustained(), seed)
    pattern["style"] = "trap"
    pattern["tracks"]["kick"] = [{"time_step": 0.0, "note": "C1", "length": 0.25, "velocity": 110}]
    pattern["tracks"]["808_bass"] = [{"time_step": 0.0, "note": "A1", "length": 1.0, "velocity": 100}]
    pattern["plg_variety"]["genre"] = "trap"
    return pattern


@pytest.fixture()
def user_library(tmp_path):
    return build_library(tmp_path / "lib")


@pytest.fixture()
def no_user_melody(user_library):
    shutil.rmtree(user_library / "Melodies")
    return user_library


@pytest.fixture()
def bank(tmp_path, monkeypatch):
    import plg_paths

    root = _bank(tmp_path / "bank", {"pad": 2, "strings": 2, "piano": 2})
    monkeypatch.setattr(plg_paths, "melody_oneshots_dir", lambda: root)
    monkeypatch.setattr(starter_kit, "_starter_dir", lambda: tmp_path / "starter")
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")
    return root


def test_switched_off_the_melody_channel_stays_empty(user_library, bank, monkeypatch):
    monkeypatch.setenv("PLG_MELODY_ONESHOTS", "off")
    pattern = _kit_pattern()
    sound_map = starter_kit.attach_sounds_to_pattern(pattern, library_catalog.scan_library(user_library), library_root=user_library)
    assert "melody_lead" not in sound_map and "plg_melody_oneshot" not in pattern


def test_switched_off_it_is_the_earlier_behaviour_with_the_melody_sample_switch(user_library, bank, monkeypatch):
    monkeypatch.setenv("PLG_MELODY_ONESHOTS", "off")
    monkeypatch.setenv("PLG_MELODY_SAMPLE", "on")
    pattern = _kit_pattern()
    sound_map = starter_kit.attach_sounds_to_pattern(pattern, library_catalog.scan_library(user_library), library_root=user_library)
    assert Path(sound_map["melody_lead"]).is_file() and "plg_melody_oneshot" not in pattern


def test_switched_off_a_melody_pin_is_ignored_as_before(user_library, bank, monkeypatch):
    monkeypatch.setenv("PLG_MELODY_ONESHOTS", "off")
    _pin_melody(user_library)
    pattern = _kit_pattern()
    sound_map = starter_kit.attach_sounds_to_pattern(pattern, library_catalog.scan_library(user_library), library_root=user_library)
    assert "melody_lead" not in sound_map and "melody_lead" not in (pattern.get("plg_explicit_lanes") or [])


def test_switched_on_a_melody_with_no_user_sound_gets_a_prepared_one_shot(no_user_melody, bank, monkeypatch, tmp_path):
    monkeypatch.setenv("PLG_MELODY_ONESHOTS", "on")
    pattern = _kit_pattern()
    notes_before = [dict(n) for n in pattern["tracks"]["melody_lead"]]
    sound_map = starter_kit.attach_sounds_to_pattern(pattern, library_catalog.scan_library(no_user_melody), library_root=no_user_melody)

    lead = Path(sound_map["melody_lead"])
    record = pattern["plg_melody_oneshot"]
    assert lead.is_file() and lead.name.endswith(f"__prep{mo.PREP_VERSION}.wav") and Path(record["prepared"]) == lead
    assert record["type"] in ("pad", "strings") and record["group"] == mo.GROUP_SUSTAIN
    assert pattern["plg_sound_paths"]["melody_lead"] == str(lead)
    assert pattern["tracks"]["melody_lead"] == notes_before, "the notes are the user's"
    assert "plg_sample_chop" not in pattern, "a one-shot is not chopped"
    for lane in ("kick", "808_bass"):
        assert lane in sound_map
    import flp_writer

    published = flp_writer._sample_paths_from_data(pattern)
    assert "melody_lead" in published and Path(published["melody_lead"]).is_file()


def test_the_prepared_file_is_not_rebaked_by_warmth_or_width(no_user_melody, bank, monkeypatch, tmp_path):
    monkeypatch.setenv("PLG_MELODY_ONESHOTS", "on")
    monkeypatch.delenv("PLG_RAW_SAMPLES", raising=False)  # the warmth / width copies are written (into tmp, see `bank`)
    pattern = _kit_pattern()
    sound_map = starter_kit.attach_sounds_to_pattern(pattern, library_catalog.scan_library(no_user_melody), library_root=no_user_melody)
    assert Path(sound_map["melody_lead"]) == Path(pattern["plg_melody_oneshot"]["prepared"])


def test_the_same_seed_gives_the_same_file_through_starter_kit(no_user_melody, bank, monkeypatch):
    monkeypatch.setenv("PLG_MELODY_ONESHOTS", "on")
    picked = []
    for _ in range(2):
        pattern = _kit_pattern(seed=21)
        starter_kit.attach_sounds_to_pattern(pattern, library_catalog.scan_library(no_user_melody), library_root=no_user_melody)
        picked.append((pattern["plg_melody_oneshot"]["type"], pattern["plg_melody_oneshot"]["file"]))
    assert picked[0] == picked[1]


def _pin_melody(library: Path, name: str = "Bell 00 Dark.wav") -> Path:
    import pinned_kit

    target = next(library.rglob(name))
    assert pinned_kit.pin_by_hand("melody_lead", target, library) is None
    return target.resolve()


def test_no_automatic_pick_from_the_users_folder_sits_on_the_melody(user_library, bank, monkeypatch):
    """The matcher guessed a file of the user's kit for the melody (a file called "Sound" says nothing): not any more."""
    monkeypatch.setenv("PLG_MELODY_ONESHOTS", "off")
    monkeypatch.setenv("PLG_MELODY_SAMPLE", "on")  # the earlier route does put the matcher's pick on the channel ...
    before = starter_kit.attach_sounds_to_pattern(_kit_pattern(), library_catalog.scan_library(user_library), library_root=user_library)
    assert Path(before["melody_lead"]).is_file()
    monkeypatch.delenv("PLG_MELODY_ONESHOTS")  # ... the flag on: only our one-shot, whatever the folder holds
    pattern = _kit_pattern()
    sound_map = starter_kit.attach_sounds_to_pattern(pattern, library_catalog.scan_library(user_library), library_root=user_library)
    lead = Path(sound_map["melody_lead"])
    assert user_library not in lead.parents and lead.name.endswith(f"__prep{mo.PREP_VERSION}.wav")
    assert pattern["plg_melody_oneshot"]["prepared"] == str(lead)
    assert "melody_lead" not in (pattern.get("plg_explicit_lanes") or [])


def test_an_explicit_pin_on_the_melody_wins_over_the_one_shot(user_library, bank):
    pinned = _pin_melody(user_library)
    pattern = _kit_pattern()
    sound_map = starter_kit.attach_sounds_to_pattern(pattern, library_catalog.scan_library(user_library), library_root=user_library)
    assert Path(sound_map["melody_lead"]).resolve() == pinned
    assert "plg_melody_oneshot" not in pattern and "melody_lead" in pattern["plg_explicit_lanes"]
    assert "plg_sample_chop" not in pattern, "the user's pinned file is played as it is"
    assert show_flp_sounds.melody_source(pattern).startswith("explicit user pin")


def test_unpinning_gives_the_melody_back_to_the_one_shot(user_library, bank):
    import pinned_kit

    _pin_melody(user_library)
    pinned_kit.unpin("melody_lead", user_library)
    pattern = _kit_pattern()
    sound_map = starter_kit.attach_sounds_to_pattern(pattern, library_catalog.scan_library(user_library), library_root=user_library)
    assert "plg_melody_oneshot" in pattern and "__prep" in Path(sound_map["melody_lead"]).name


def test_the_dice_never_draw_a_melody_sound_and_a_hand_pin_is_only_by_hand(user_library):
    import pinned_kit

    picks, _without = pinned_kit.pick_random_kit(library_catalog.scan_library(user_library), user_library)
    assert "melody_lead" not in picks and set(picks) <= set(pinned_kit.PIN_LANES)
    pinned = _pin_melody(user_library)
    assert "melody_lead" in pinned_kit.valid_explicit(user_library) and pinned_kit.valid_pins(user_library)["melody_lead"] == pinned
    assert pinned_kit.pin_by_hand("banana", pinned, user_library) == "unknown_lane"


def test_switched_on_with_an_empty_folder_nothing_breaks(no_user_melody, tmp_path, monkeypatch):
    import plg_paths

    monkeypatch.setattr(plg_paths, "melody_oneshots_dir", lambda: tmp_path / "nothing")
    monkeypatch.setattr(starter_kit, "_starter_dir", lambda: tmp_path / "starter")
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")
    monkeypatch.setenv("PLG_MELODY_ONESHOTS", "on")
    pattern = _kit_pattern()
    sound_map = starter_kit.attach_sounds_to_pattern(pattern, library_catalog.scan_library(no_user_melody), library_root=no_user_melody)
    assert "melody_lead" not in sound_map and "plg_melody_oneshot" not in pattern and "kick" in sound_map


def test_a_non_midi_pattern_is_left_alone(no_user_melody, bank, monkeypatch):
    monkeypatch.setenv("PLG_MELODY_ONESHOTS", "on")
    pattern = _kit_pattern()
    pattern["plg_midi_melody"] = False
    sound_map = starter_kit.attach_sounds_to_pattern(pattern, library_catalog.scan_library(no_user_melody), library_root=no_user_melody)
    assert "plg_melody_oneshot" not in pattern
    assert "melody_lead" not in sound_map or "__prep" not in Path(sound_map["melody_lead"]).name


# --- check_oneshots (the pitch detector) --------------------------------------------------------------------------------
@pytest.mark.parametrize(("hz", "cents"), [(261.6256, 0.0), (523.2511, 1200.0), (130.8128, -1200.0), (277.18, 100.0)])
def test_the_pitch_detector_reads_a_plain_tone(hz, cents):
    f0, conf = check_oneshots.detect_pitch(_tone(hz, 1.0)[:, 0], RATE)
    assert check_oneshots.cents_from_root(f0) == pytest.approx(cents, abs=8.0)
    assert conf < 0.15


def test_the_pitch_is_the_fundamental_not_the_loudest_partial():
    """f0 220 Hz with a weak fundamental and strong 2nd / 3rd partials: an FFT peak would say 440 or 660."""
    mono = _tone(220.0, 1.0, partials={1: 0.25, 2: 1.0, 3: 0.9, 4: 0.5})[:, 0]
    peak = np.fft.rfftfreq(len(mono), 1 / RATE)[np.argmax(np.abs(np.fft.rfft(mono)))]
    assert abs(peak - 220.0) > 100
    f0, _ = check_oneshots.detect_pitch(mono, RATE)
    assert f0 == pytest.approx(220.0, rel=0.01)


def test_silence_has_no_pitch():
    assert check_oneshots.detect_pitch(np.zeros(RATE), RATE) == (None, None)


def test_the_table_marks_ok_octave_off_and_rejected(tmp_path):
    _write(tmp_path / "pad" / "ok.wav", 261.6256)
    _write(tmp_path / "pad" / "octave.wav", 130.8128)
    _write(tmp_path / "pad" / "off.wav", 300.0)
    _write(tmp_path / "_rejected" / "lead" / "bad.wav", 261.6256, dc=0.1)
    rows = {r["name"]: r for r in check_oneshots.collect(tmp_path)}
    assert rows["pad/ok.wav"]["status"] == "OK" and rows["pad/octave.wav"]["status"] == "OCT" and rows["pad/off.wav"]["status"] == "OFF"
    assert rows["_rejected/lead/bad.wav"]["rejected"] and rows["pad/ok.wav"]["rejected"] is False
    assert rows["_rejected/lead/bad.wav"]["dc"] > 0.05 > rows["pad/ok.wav"]["dc"]
    table = check_oneshots.format_table(list(rows.values()))
    assert "(rejected)" in table and "3 files (rejected not counted)" in table and "OK 1, OCT 1 (pitch class right" in table


# --- show_flp_sounds --------------------------------------------------------------------------------------------------
def test_show_flp_sounds_prints_the_features_the_group_and_the_choice():
    data = _pattern(_sustained(), 3)
    data["plg_melody_oneshot"] = {"seed": 3, "type": "pad", "file": "pad_2_rage.wav", "group": "sustain", "group_used": "keys"}
    text = "\n".join(show_flp_sounds.melody_report(data))
    assert "mean note length 8.00 beats" in text and "notes per bar" in text and "median pitch" in text
    assert "group by the scheme (an estimate): sustain -> pad, strings" in text
    assert "seed 3 / type pad / file pad_2_rage.wav" in text and "had no file, used keys" in text
    none = "\n".join(show_flp_sounds.melody_report(_pattern(_sustained())))
    assert "no PLG melody one-shot" in none
    assert "no melody notes" in "\n".join(show_flp_sounds.melody_report({"tracks": {}}))


# --- the exe ------------------------------------------------------------------------------------------------------------
@pytest.mark.skip(reason="requires third-party assets not included")
def test_the_exe_gets_the_sixteen_sounds_the_offsets_and_nothing_else():
    files = melody_oneshots_bundle.bundle_files(REPO)
    wavs = [(src, dest) for src, dest in files if src.endswith(".wav")]
    assert len(wavs) == 16 and len(files) == 17
    assert {Path(dest).name for _src, dest in wavs} == set(SHIPPED_COUNTS)
    assert all(dest == f"assets/melody_oneshots/{Path(src).parent.name}" for src, dest in wavs)
    others = [(Path(src).name, dest) for src, dest in files if not src.endswith(".wav")]
    assert others == [("root_offsets.json", "assets/melody_oneshots")]  # data beside the wavs; no sources.txt, nothing rejected
    assert not any("_rejected" in src for src, _dest in files)
    shipped = {Path(src) for src, _dest in wavs}
    assert shipped == {p for paths in mo.library(REPO / "assets" / "melody_oneshots").values() for p in paths}


def test_an_empty_folder_stops_the_build(tmp_path):
    (tmp_path / "assets" / "melody_oneshots" / "_rejected").mkdir(parents=True)
    with pytest.raises(SystemExit):
        melody_oneshots_bundle.bundle_files(tmp_path)


def test_the_spec_ships_the_list_and_the_module():
    spec = (REPO / "PLG.spec").read_text(encoding="utf-8")
    assert "melody_oneshots_bundle.bundle_files(root)" in spec and "+ melody_oneshot_files +" in spec
    assert '"melody_oneshots",' in spec


# --- the channel root offset ------------------------------------------------------------------------------------------
OCTAVE_FILES = {"lead/lead_3_soft.wav", "pad/pad_1_dark.wav", "pad/pad_3_soft.wav", "piano/piano_3_soft.wav", "pluck/pluck_2_rage.wav"}
OFFSETS_JSON = REPO / "assets" / "melody_oneshots" / "root_offsets.json"


@pytest.mark.parametrize(
    ("cents", "offset"),
    [(0, 0), (19, 0), (-50, 0), (-599, 0), (599, 0), (-600, -6), (600, 6), (-1184, -12), (-1202, -12), (1200, 12), (-2400, -24), (None, 0)],
)
def test_the_offset_rule_is_whole_semitones_and_only_for_a_register_deviation(cents, offset):
    assert check_oneshots.root_offset_semitones(cents) == offset


@pytest.mark.skip(reason="requires third-party assets not included")
def test_the_shipped_offsets_are_minus_twelve_for_the_octave_files_and_zero_for_the_rest():
    import json

    offsets = json.loads(OFFSETS_JSON.read_text(encoding="utf-8"))["offsets"]
    library = mo.library(REPO / "assets" / "melody_oneshots")
    assert set(offsets) == {f"{kind}/{p.name}" for kind, paths in library.items() for p in paths}  # none for _rejected
    assert {name for name, value in offsets.items() if value == -12} == OCTAVE_FILES
    assert all(value == 0 for name, value in offsets.items() if name not in OCTAVE_FILES)
    for kind, paths in library.items():
        for path in paths:
            assert mo.root_offset(path) == offsets[f"{kind}/{path.name}"]


@pytest.mark.skip(reason="requires third-party assets not included")
def test_the_offsets_are_what_the_pitch_detector_measures():
    """The data is not a hand edit: re-measuring every shipped file gives the file's number again."""
    import json

    offsets = json.loads(OFFSETS_JSON.read_text(encoding="utf-8"))["offsets"]
    rows = [r for r in check_oneshots.collect(REPO / "assets" / "melody_oneshots") if not r["rejected"]]
    assert len(rows) == 16
    assert {r["name"]: r["root_offset"] for r in rows} == offsets


def test_a_missing_or_broken_offsets_file_means_zero(tmp_path):
    wav = _write(tmp_path / "pad" / "pad_1_x.wav")
    assert mo.root_offset(wav) == 0
    (tmp_path / mo.OFFSETS_FILE).write_text("{not json", encoding="utf-8")
    assert mo.root_offset(wav) == 0
    (tmp_path / mo.OFFSETS_FILE).write_text('{"offsets": {"pad/other.wav": -12}}', encoding="utf-8")
    assert mo.root_offset(wav) == 0
    (tmp_path / mo.OFFSETS_FILE).write_text('{"offsets": {"pad/pad_1_x.wav": -12}}', encoding="utf-8")
    assert mo.root_offset(wav) == -12


def test_the_choice_carries_the_offset_and_repeats(tmp_path):
    _bank(tmp_path, {"pad": 1})
    (tmp_path / mo.OFFSETS_FILE).write_text('{"offsets": {"pad/pad_1_x.wav": -12}}', encoding="utf-8")
    first = mo.choose(_pattern(_sustained(), 9), root=tmp_path)
    assert first["root_offset"] == -12 and first == mo.choose(_pattern(_sustained(), 9), root=tmp_path)


@pytest.mark.skip(reason="requires third-party assets not included")
def test_the_prepared_copy_keeps_the_pitch_of_the_source(tmp_path):
    """The offset is in the .flp, never in the audio: a prepared octave-down file is still an octave down."""
    source = REPO / "assets" / "melody_oneshots" / "pad" / "pad_1_dark.wav"
    copy = mo.prepared_copy(source, tmp_path)
    before = check_oneshots.measure(source)["f0_hz"]
    after = check_oneshots.measure(copy)["f0_hz"]
    assert abs(check_oneshots.cents_from_root(after) - check_oneshots.cents_from_root(before)) < 5
    assert check_oneshots.measure(copy)["root_offset"] == -12


def test_the_channel_root_helper_only_speaks_for_our_one_shot():
    data = {
        "plg_melody_oneshot": {"prepared": "C:/x/pad_1__prep1.wav", "root_offset": -12},
        "plg_sound_paths": {
            "melody_lead": "C:/x/pad_1__prep1.wav",
            "counter_melody": "C:/x/pad_1__prep1.wav",
            "chords": "C:/x/other.wav",
            "kick": "C:/x/pad_1__prep1.wav",
        },
    }
    assert flp_writer._melody_oneshot_root_note(data, "melody_lead") == 48
    assert flp_writer._melody_oneshot_root_note(data, "counter_melody") == 48  # the same sample on the answer lane
    assert flp_writer._melody_oneshot_root_note(data, "chords") is None  # another sound keeps FL's default root
    assert flp_writer._melody_oneshot_root_note({"plg_sound_paths": data["plg_sound_paths"]}, "melody_lead") is None
    data["plg_melody_oneshot"]["root_offset"] = 0
    assert flp_writer._melody_oneshot_root_note(data, "melody_lead") == 60


def _flp_with_one_shot(tmp_path, monkeypatch, root: Path, seed: int = 4):
    import plg_paths

    monkeypatch.setattr(plg_paths, "melody_oneshots_dir", lambda: root)
    monkeypatch.setattr(starter_kit, "_starter_dir", lambda: tmp_path / "starter")
    monkeypatch.setenv("PLG_RAW_SAMPLES", "1")
    monkeypatch.setenv("PLG_MELODY_ONESHOTS", "on")
    lib = build_library(tmp_path / "lib")
    shutil.rmtree(lib / "Melodies")
    pattern = _kit_pattern(seed)
    starter_kit.attach_sounds_to_pattern(pattern, library_catalog.scan_library(lib), library_root=lib)
    flp = flp_writer.write_flp_session(pattern, tmp_path / "Projects" / "t.flp", bundle_sounds=True)
    channels = {c["name"]: c for c in show_flp_sounds.read_channels(Path(flp))}
    return pattern, channels


def test_the_flp_channel_root_follows_the_offset(tmp_path, monkeypatch):
    bank = _bank(tmp_path / "bank", {"pad": 1})
    (bank / mo.OFFSETS_FILE).write_text('{"offsets": {"pad/pad_1_x.wav": -12}}', encoding="utf-8")
    pattern, channels = _flp_with_one_shot(tmp_path, monkeypatch, bank)
    assert pattern["plg_melody_oneshot"]["root_offset"] == -12
    assert channels["PLG MIDI"]["root"] == 48
    assert channels["PLG Kick"]["root"] is None  # nothing else got a root from this


def test_a_file_on_midi_60_gets_root_60_and_no_file_means_the_default_offset(tmp_path, monkeypatch):
    bank = _bank(tmp_path / "bank", {"pad": 1})
    (bank / mo.OFFSETS_FILE).write_text('{"offsets": {"pad/pad_1_x.wav": 0}}', encoding="utf-8")
    _pattern_a, channels = _flp_with_one_shot(tmp_path / "a", monkeypatch, bank)
    assert channels["PLG MIDI"]["root"] == 60
    (bank / mo.OFFSETS_FILE).unlink()
    _pattern_b, channels = _flp_with_one_shot(tmp_path / "b", monkeypatch, bank)
    assert channels["PLG MIDI"]["root"] == 60


@pytest.mark.skip(reason="requires third-party assets not included")
def test_with_the_real_library_an_octave_file_writes_root_48_and_it_repeats(tmp_path, monkeypatch):
    """Real shipped files and data: find a seed that picks an octave-down file, build the .flp twice."""
    real = REPO / "assets" / "melody_oneshots"
    seed = next(s for s in range(200) if mo.choose(_kit_pattern(s), root=real)["root_offset"] == -12)
    results = []
    for run in ("one", "two"):
        pattern, channels = _flp_with_one_shot(tmp_path / run, monkeypatch, real, seed)
        record = pattern["plg_melody_oneshot"]
        assert f"{record['type']}/{record['file']}" in OCTAVE_FILES
        results.append((record["file"], record["root_offset"], channels["PLG MIDI"]["root"]))
    assert results[0] == results[1] and results[0][1:] == (-12, 48)


@pytest.mark.skip(reason="requires third-party assets not included")
def test_the_real_library_file_on_midi_60_writes_root_60(tmp_path, monkeypatch):
    real = REPO / "assets" / "melody_oneshots"
    seed = next(s for s in range(200) if mo.choose(_kit_pattern(s), root=real)["root_offset"] == 0)
    pattern, channels = _flp_with_one_shot(tmp_path, monkeypatch, real, seed)
    assert pattern["plg_melody_oneshot"]["root_offset"] == 0 and channels["PLG MIDI"]["root"] == 60


def test_show_flp_sounds_says_the_offset_and_the_root():
    data = _pattern(_sustained(), 3)
    data["plg_melody_oneshot"] = {
        "seed": 3, "type": "pad", "file": "pad_1_dark.wav", "group": "sustain", "group_used": "sustain", "root_offset": -12,
    }
    text = "\n".join(show_flp_sounds.melody_report(data))
    assert "root offset: -12 semitones -> channel root 48" in text and "the audio is unchanged" in text
    data["plg_melody_oneshot"]["root_offset"] = 0
    assert "root offset: +0 semitones -> channel root 60 (the file is on MIDI 60)" in "\n".join(show_flp_sounds.melody_report(data))


def test_show_flp_sounds_names_who_sounds_on_the_melody():
    one = {"plg_melody_oneshot": {"prepared": "x", "type": "pad", "file": "pad_2_rage.wav"}}
    assert show_flp_sounds.melody_source(one) == "our one-shot (pad/pad_2_rage.wav)"
    pin = {"plg_explicit_lanes": ["kick", "melody_lead"], "plg_pinned_kit": {"pinned": {"melody_lead": "Bell 00 Dark.wav"}}}
    assert show_flp_sounds.melody_source(pin) == "explicit user pin (Bell 00 Dark.wav)"
    assert show_flp_sounds.melody_source({}).startswith("empty")
    assert show_flp_sounds.melody_source({}, "DISMANTLED Sound.wav").startswith("another sound (DISMANTLED Sound.wav;")
    both = {**one, **pin}
    assert show_flp_sounds.melody_source(both).startswith("our one-shot")  # a record of our own is what the channel plays
    text = "\n".join(show_flp_sounds.melody_report({**_pattern(_sustained()), **pin}))
    assert "melody: explicit user pin (Bell 00 Dark.wav)" in text and "no PLG melody one-shot" in text

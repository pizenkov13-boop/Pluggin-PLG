"""Real kits are not named like our keyword list.

Covers the three ways an ordinary drum kit used to lose its 808 (and kick/hats):

* the file NAME had no exact keyword ("Sub 01", "eight-o-eight", "Бас") or the
  keyword lived only in a dedicated FOLDER ("808s/Dark_01.wav");
* the catalog let a kit FOLDER name ("808 Heavy Drum Kit") classify every file
  inside it, so its kicks/hats were filed as 808s, failed the 808 body gate and
  were quarantined;
* the pitch passport called any 808 with a big attack pitch-drop "uncertain"
  and the gate threw it out, although its sustained body has a clear root.

The audio gates are deliberately NOT relaxed here: a wrongly-named file must
still fail on evidence, and an unpitched file must still fail the root check.
"""

from __future__ import annotations

import math
import random
import struct
import wave

import pytest

from library_catalog import scan_library
from sample_match import track_candidate_rejection_reason
from sample_pitch_passport import sample_pitch_passport

RATE = 44100


@pytest.fixture(autouse=True)
def _names_only(monkeypatch):
    """The identity tests here are about NAMES and the 808 audio gates; the
    sound layer (sound_type) has its own tests in test_sound_type.py."""
    monkeypatch.setenv("PLG_SOUND_TYPE", "off")


def _write(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, int(s)))) for s in samples))


def _steady_808(seconds=1.4, freq=55.0):
    return [
        26000 * min(1.0, i / 300.0) * math.exp(-i / RATE / 1.5) * math.sin(2 * math.pi * freq * i / RATE)
        for i in range(int(RATE * seconds))
    ]


def _glide_808(seconds=1.6, start=400.0, body=45.0, drop_seconds=0.05):
    """A classic 808: attack far above the body, settling onto a steady sub."""
    out, phase = [], 0.0
    for i in range(int(RATE * seconds)):
        t = i / RATE
        freq = body + (start - body) * math.exp(-t / drop_seconds)
        phase += 2 * math.pi * freq / RATE
        out.append(26000 * min(1.0, i / 200.0) * math.exp(-t / 1.5) * math.sin(phase))
    return out


def _short_click(seconds=0.06):
    rng = random.Random(3)
    return [20000 * math.exp(-i / RATE / 0.01) * rng.uniform(-1, 1) for i in range(int(RATE * seconds))]


def _tick(seconds=0.05):
    """A dull closed-hat-like tick (passes the hat gates, unlike white noise)."""
    return [
        16000 * math.exp(-i / RATE / 0.012) * math.sin(2 * math.pi * 3000 * i / RATE)
        for i in range(int(RATE * seconds))
    ]


# --- name / folder identity ---------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "Bass_808.wav",          # already worked
        "Sub_01.wav",            # "sub" alone used to be a hard conflict
        "Kit/808s/Sub.wav",
        "eight-o-eight.wav",     # spelled out
        "Eight O Eight.wav",
        "бас.wav",               # Cyrillic
        "Кит/808/Ghost.wav",     # keyword only in a dedicated folder
        "808s/Dark_01.wav",
    ],
)
def test_808_identity_accepts_realistic_kit_names(tmp_path, rel):
    _write(tmp_path / rel, _steady_808())
    assert track_candidate_rejection_reason(tmp_path / rel, "808_bass", library_root=tmp_path) is None


@pytest.mark.parametrize(
    ("rel", "reason"),
    [
        ("Dark_01.wav", "missing_track_identity"),                  # nothing says what it is
        ("Ghost_A#.wav", "missing_track_identity"),
        ("808 Heavy Drum Kit/Dark_01.wav", "missing_track_identity"),  # a whole kit is not "the 808 folder"
        ("808s/Kick_01.wav", "name_or_path_conflicts_with_track"),  # folder must not override a foreign name
        ("Snare Sub.wav", "name_or_path_conflicts_with_track"),     # "sub" alias must not beat a foreign marker
        ("808s/Lead Synth.wav", "name_or_path_conflicts_with_track"),
    ],
)
def test_808_identity_still_rejects_wrongly_named_files(tmp_path, rel, reason):
    _write(tmp_path / rel, _steady_808())
    assert track_candidate_rejection_reason(tmp_path / rel, "808_bass", library_root=tmp_path) == reason


def test_export_time_validation_agrees_without_a_library_root(tmp_path):
    # midi_validate / flp_writer call the gate with no library_root: the parent
    # folder alone must give the same verdict the selection stage gave.
    _write(tmp_path / "808s" / "Dark_01.wav", _steady_808())
    assert track_candidate_rejection_reason(tmp_path / "808s" / "Dark_01.wav", "808_bass") is None


@pytest.mark.parametrize("rel", ["Kicks/01.wav", "Кик 01.wav", "Kick_01.wav"])
def test_kick_identity_accepts_dedicated_folder_and_alias(tmp_path, rel):
    _write(tmp_path / rel, _short_click(0.2))
    assert track_candidate_rejection_reason(tmp_path / rel, "kick", library_root=tmp_path) is None


def test_kick_in_a_generic_folder_is_still_unidentified(tmp_path):
    _write(tmp_path / "Drums" / "01.wav", _short_click(0.2))
    assert (
        track_candidate_rejection_reason(tmp_path / "Drums" / "01.wav", "kick", library_root=tmp_path)
        == "missing_track_identity"
    )


# --- catalog: a kit folder must not classify its files -----------------------


def test_kit_folder_named_808_does_not_hijack_kicks_and_hats(tmp_path):
    lib = tmp_path / "lib"
    _write(lib / "808 Heavy Drum Kit" / "Kick_01.wav", _short_click(0.2))
    _write(lib / "808 Heavy Drum Kit" / "Hat_01.wav", _tick())
    catalog = scan_library(lib, quarantine=True)

    assert catalog["rejected_audio"] == {}
    assert (lib / "808 Heavy Drum Kit" / "Kick_01.wav").is_file()  # not moved to _PLG_UNUSE
    assert (lib / "808 Heavy Drum Kit" / "Hat_01.wav").is_file()
    assert "808 Heavy Drum Kit/Kick_01.wav" in catalog["audio"]["kits"]
    assert "808 Heavy Drum Kit/Hat_01.wav" in catalog["audio"]["hats"]


def test_an_unlabelled_file_still_follows_its_folder(tmp_path):
    lib = tmp_path / "lib"
    _write(lib / "Trap Hats" / "01.wav", _tick())
    assert "Trap Hats/01.wav" in scan_library(lib, quarantine=False)["audio"]["hats"]


# --- pitch passport: root trust ----------------------------------------------


def test_808_with_a_big_attack_pitch_drop_has_a_trusted_root(tmp_path):
    sample = tmp_path / "808_glide.wav"
    _write(sample, _glide_808())
    passport = sample_pitch_passport(sample)

    assert passport["status"] == "uncertain"  # the strict signal is unchanged
    assert passport["pitch_drop_semitones"] > 6.0
    assert passport["root_trusted"] is True
    assert track_candidate_rejection_reason(sample, "808_bass", library_root=tmp_path) is None


def test_unpitched_noise_never_gets_a_trusted_root(tmp_path):
    rng = random.Random(5)
    sample = tmp_path / "808_noise.wav"
    _write(sample, [22000 * math.exp(-i / RATE / 0.4) * rng.uniform(-1, 1) for i in range(int(RATE * 0.9))])
    passport = sample_pitch_passport(sample)

    assert passport["root_trusted"] is False
    assert passport["prominence"] < 50.0


def test_strict_ok_samples_stay_trusted(tmp_path):
    sample = tmp_path / "808_A.wav"
    _write(sample, _steady_808())
    passport = sample_pitch_passport(sample)
    assert passport["status"] == "ok" and passport["root_trusted"] is True


def test_export_writes_the_root_note_for_a_trusted_uncertain_808(tmp_path):
    # The gate lets an "uncertain" 808 in only because its body root is
    # trustworthy; the export must therefore set that root, or FL plays it at
    # its default root and the whole bassline lands at the wrong pitch.
    from flp_writer import _CH_ROOT_NOTE, build_flp

    sample = tmp_path / "808_glide.wav"
    _write(sample, _glide_808())
    passport = sample_pitch_passport(sample)
    assert passport["status"] == "uncertain"

    pattern = {
        "bpm": 140,
        "plg_total_bars": 8,
        "plg_midi_melody": True,
        "plg_sound_paths": {"808_bass": str(sample)},
        "tracks": {
            "melody_lead": [{"time_step": 0.0, "note": "A4", "length": 1.0, "velocity": 90}],
            "kick": [{"time_step": 0.0, "note": "C5", "length": 0.25, "velocity": 124}],
            "808_bass": [{"time_step": 0.0, "note": "F2", "length": 0.5, "velocity": 116}],
        },
    }
    blob = build_flp(pattern)
    assert bytes([_CH_ROOT_NOTE]) + struct.pack("<I", int(passport["fl_root_note"])) in blob
